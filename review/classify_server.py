"""
Classification server — Phase 1 human-in-the-loop tool.

The user sees page images with detected regions overlaid, and classifies
each region as structured, linked, or skipped before extraction runs.
"""

import json
import re
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


class ClassifyHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            self._serve_file(self.server.html_path, "text/html")
        elif path == "/api/pages":
            self._json_response(self.server.page_data)
        elif path == "/api/classifications":
            self._json_response(self._load_classifications())
        elif path.startswith("/api/segment/"):
            filename = path.split("/api/segment/", 1)[1]
            regions = self._auto_segment(filename)
            self._json_response(regions)
        elif path.startswith("/api/img/"):
            name = path.split("/api/img/", 1)[1]
            img_path = self.server.pages_dir / name
            if img_path.exists():
                self._serve_file(img_path, "image/png")
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def _auto_segment(self, filename):
        """Auto-detect regions on a page image using simple heuristics.

        This is a placeholder. When extract/segmenter.py exists (Claude vision),
        swap this out. For now, uses PIL to find content bands via row variance.
        """
        img_path = self.server.pages_dir / filename
        if not img_path.exists():
            return []
        return _segment_page_heuristic(img_path)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/classifications":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            self._save_classifications(body)
            self._json_response({"status": "ok"})
        else:
            self.send_error(404)

    def _serve_file(self, filepath, content_type):
        data = Path(filepath).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _json_response(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _load_classifications(self):
        if self.server.output_path.exists():
            return json.loads(self.server.output_path.read_text())
        return {"pages": {}}

    def _save_classifications(self, data):
        doc = self._load_classifications()
        page_key = str(data.get("page"))
        doc["pages"][page_key] = {
            "regions": data.get("regions", []),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.server.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.server.output_path.write_text(json.dumps(doc, indent=2))

    def log_message(self, format, *args):
        if args and str(args[0]).startswith("4"):
            super().log_message(format, *args)


def _segment_page_heuristic(img_path):
    """Detect content regions using row-variance bands.

    Scans the grayscale image row by row. Rows with high variance (content)
    are grouped into vertical bands. Each band becomes a region proposal.
    Wide bands with low internal variance are classified as figures;
    narrow bands as text or equations; bands with grid-like structure as tables.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(img_path).convert("L")
    pixels = np.array(img)
    h, w = pixels.shape

    # margins: skip top/bottom 3%, left/right 5%
    margin_top = int(h * 0.03)
    margin_bot = int(h * 0.97)
    margin_left = int(w * 0.05)
    margin_right = int(w * 0.95)

    # row variance across the content area
    content = pixels[margin_top:margin_bot, margin_left:margin_right]
    row_var = np.var(content.astype(float), axis=1)

    # threshold: rows with variance > median are "content"
    threshold = max(np.median(row_var) * 0.5, 50)
    is_content = row_var > threshold

    # group consecutive content rows into bands
    bands = []
    in_band = False
    band_start = 0
    min_band_height = 20

    for i in range(len(is_content)):
        if is_content[i] and not in_band:
            band_start = i
            in_band = True
        elif not is_content[i] and in_band:
            if i - band_start >= min_band_height:
                bands.append((band_start + margin_top, i + margin_top))
            in_band = False
    if in_band and len(is_content) - band_start >= min_band_height:
        bands.append((band_start + margin_top, len(is_content) + margin_top))

    # merge bands with small gaps (< 15px)
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < 15:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)

    # classify each band
    regions = []
    for y0, y1 in merged:
        band_h = y1 - y0
        band_pixels = pixels[y0:y1, margin_left:margin_right]
        band_mean = np.mean(band_pixels)
        band_var = np.var(band_pixels.astype(float))

        # find horizontal content extent
        col_var = np.var(band_pixels.astype(float), axis=0)
        col_threshold = max(np.median(col_var) * 0.3, 30)
        content_cols = np.where(col_var > col_threshold)[0]
        if len(content_cols) < 5:
            x0, x1_local = margin_left, margin_right
        else:
            x0 = max(margin_left, int(content_cols[0]) + margin_left - 10)
            x1_local = min(margin_right, int(content_cols[-1]) + margin_left + 10)

        # heuristic type classification
        aspect = (x1_local - x0) / max(band_h, 1)

        # check for horizontal lines (table indicator)
        row_means = np.mean(band_pixels, axis=1)
        dark_rows = np.sum(row_means < 200) / len(row_means)

        if band_mean < 220 and band_h > 200 and band_var > 3000:
            # large dark area with high variance = figure
            region_type = "figure"
            classification = "linked"
        elif dark_rows > 0.08 and band_h > 80 and aspect > 2:
            # many horizontal dark lines + wide = table
            region_type = "table"
            classification = "structured"
        elif band_h < 60 and band_var > 1500:
            # short high-variance band = equation
            region_type = "equation"
            classification = "structured"
        else:
            # default = text block
            region_type = "text_block"
            classification = "structured"

        regions.append({
            "x0": x0,
            "y0": y0,
            "x1": x1_local,
            "y1": y1,
            "region_type": region_type,
            "classification": classification,
        })

    return regions


def _scan_pages(pages_dir):
    """Build page list from directory of PNGs."""
    pages = []
    for f in sorted(pages_dir.iterdir()):
        m = re.match(r"page-(\d+)\.png", f.name)
        if m:
            pages.append({
                "index": int(m.group(1)),
                "filename": f.name,
            })
    return pages


def start_classify_server(pages_dir, port=8788, output_path=None):
    pages_dir = Path(pages_dir)
    if output_path is None:
        output_path = pages_dir.parent.parent / "qc" / "classifications.json"
    else:
        output_path = Path(output_path)

    html_path = Path(__file__).parent / "classify.html"
    page_data = _scan_pages(pages_dir)

    print(f"Found {len(page_data)} pages in {pages_dir}")

    server = HTTPServer(("127.0.0.1", port), ClassifyHandler)
    server.pages_dir = pages_dir
    server.output_path = output_path
    server.html_path = html_path
    server.page_data = page_data

    print(f"Classification tool: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
