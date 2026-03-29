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
        """Auto-detect regions on a page image.

        Uses few-shot Claude vision if gold labels + API key exist,
        falls back to pixel heuristic otherwise.
        """
        img_path = self.server.pages_dir / filename
        if not img_path.exists():
            return []

        # Try few-shot segmenter with gold labels
        if self.server.use_vision:
            classifications = self._load_classifications()
            labeled_pages = classifications.get("pages", {})
            # Need at least 1 labeled page as an example
            if any(len(v.get("regions", [])) > 0 for v in labeled_pages.values()):
                try:
                    from extract.segmenter import segment_page_fewshot
                    return segment_page_fewshot(
                        img_path,
                        self.server.pages_dir,
                        classifications,
                    )
                except Exception as e:
                    print(f"Few-shot segmenter failed: {e}")

        # Fallback: pixel heuristic + enrichment
        regions = _segment_page_heuristic(img_path)
        m = re.match(r"page-(\d+)\.png", filename)
        if m and self.server.precomputed_segments:
            page_key = str(int(m.group(1)))
            precomputed = self.server.precomputed_segments.get(page_key, [])
            if precomputed:
                _enrich_regions(regions, precomputed)
        return regions

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
    """Detect content regions using pixel analysis.

    Detects column layout, then finds content bands per column.
    Classifies based on pixel density, horizontal lines, and aspect ratio.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(img_path).convert("L")
    pixels = np.array(img)
    h, w = pixels.shape

    # Page margins
    margin_top = int(h * 0.02)
    margin_bot = int(h * 0.97)
    margin_left = int(w * 0.04)
    margin_right = int(w * 0.96)

    # Detect column layout: look for a vertical gutter
    # Check the middle third of the page for a vertical strip of white
    mid_band = pixels[int(h*0.15):int(h*0.85), :]
    col_means = np.mean(mid_band, axis=0)

    # Find the gutter: a region of high brightness (white) in the middle
    center = w // 2
    search_left = int(w * 0.35)
    search_right = int(w * 0.65)
    gutter_means = col_means[search_left:search_right]

    # Smooth to find a consistent white band
    kernel = np.ones(20) / 20
    smoothed = np.convolve(gutter_means, kernel, mode='same')
    gutter_threshold = np.percentile(smoothed, 90)

    is_two_column = False
    gutter_x = center
    if np.max(smoothed) > 240:  # there's a clear white gutter
        gutter_pos = np.argmax(smoothed)
        gutter_x = search_left + gutter_pos
        # Verify: check that there's content on both sides
        left_content = np.mean(col_means[margin_left:gutter_x-20])
        right_content = np.mean(col_means[gutter_x+20:margin_right])
        if left_content < 245 and right_content < 245:
            is_two_column = True

    # Define column boundaries
    if is_two_column:
        columns = [
            (margin_left, gutter_x - 10),
            (gutter_x + 10, margin_right),
        ]
    else:
        columns = [(margin_left, margin_right)]

    regions = []

    for col_left, col_right in columns:
        col_pixels = pixels[margin_top:margin_bot, col_left:col_right]
        col_w = col_right - col_left

        # Row-by-row variance to find content bands
        row_var = np.var(col_pixels.astype(float), axis=1)
        threshold = max(np.percentile(row_var, 30), 30)
        is_content = row_var > threshold

        # Group consecutive content rows
        bands = []
        in_band = False
        band_start = 0
        min_gap = 8  # merge bands with tiny gaps
        min_height = 15

        for i in range(len(is_content)):
            if is_content[i] and not in_band:
                band_start = i
                in_band = True
            elif not is_content[i] and in_band:
                if i - band_start >= min_height:
                    bands.append((band_start, i))
                in_band = False
        if in_band and len(is_content) - band_start >= min_height:
            bands.append((band_start, len(is_content)))

        # Merge close bands
        merged = []
        for b in bands:
            if merged and b[0] - merged[-1][1] < min_gap:
                merged[-1] = (merged[-1][0], b[1])
            else:
                merged.append(b)

        # Classify each band
        for y0_local, y1_local in merged:
            y0 = y0_local + margin_top
            y1 = y1_local + margin_top
            band_h = y1 - y0
            band = pixels[y0:y1, col_left:col_right]

            # Find tight horizontal extent within the band
            col_var_band = np.var(band.astype(float), axis=0)
            content_cols = np.where(col_var_band > 20)[0]
            if len(content_cols) < 3:
                continue
            x0 = col_left + max(0, int(content_cols[0]) - 5)
            x1 = col_left + min(col_w, int(content_cols[-1]) + 5)

            # Classification heuristics
            band_mean = np.mean(band)
            band_var = np.var(band.astype(float))

            # Detect horizontal lines (table indicator)
            row_means = np.mean(band, axis=1)
            very_dark_rows = np.sum(row_means < 180)
            # Count rows that are mostly uniform (horizontal rules)
            row_stds = np.std(band.astype(float), axis=1)
            rule_rows = np.sum((row_means < 200) & (row_stds < 40))

            # Detect large non-text area (figure)
            # Figures tend to have large dark areas or complex imagery
            dark_pixel_ratio = np.sum(band < 180) / band.size

            if band_h > 250 and dark_pixel_ratio > 0.15 and band_var > 2000:
                region_type = "figure"
                classification = "linked"
                label = "figure region"
            elif rule_rows > 5 and band_h > 60 and (rule_rows / band_h) > 0.03:
                region_type = "table"
                classification = "structured"
                label = "table region"
            elif band_h < 45 and band_var > 800:
                # Short, high-contrast band = likely equation
                region_type = "equation"
                classification = "structured"
                label = "equation region"
            elif band_h < 25:
                # Very short = likely page furniture
                region_type = "text_block"
                classification = "skipped"
                label = "header/footer"
            else:
                region_type = "text_block"
                classification = "structured"
                label = "text block"

            regions.append({
                "x0": int(x0), "y0": int(y0),
                "x1": int(x1), "y1": int(y1),
                "region_type": region_type,
                "classification": classification,
                "label": label,
            })

    return regions


def _overlap(a, b):
    """Compute overlap area between two region dicts."""
    ox = max(0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    oy = max(0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    return ox * oy


def _area(r):
    return max(0, r["x1"] - r["x0"]) * max(0, r["y1"] - r["y0"])


def _enrich_regions(regions, precomputed):
    """Transfer labels, types, and classifications from pre-computed to heuristic regions.

    Matches by maximum overlap. Pre-computed has better type/classification;
    heuristic has better pixel positions.
    """
    for region in regions:
        best_overlap = 0
        best_match = None
        for pc in precomputed:
            ov = _overlap(region, pc)
            if ov > best_overlap:
                best_overlap = ov
                best_match = pc
        if best_match and best_overlap > 0.2 * _area(region):
            region["region_type"] = best_match.get("region_type", region["region_type"])
            region["classification"] = best_match.get("classification", region["classification"])
            if best_match.get("label"):
                region["label"] = best_match["label"]


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
    import os

    pages_dir = Path(pages_dir)
    if output_path is None:
        output_path = pages_dir.parent.parent / "qc" / "classifications.json"
    else:
        output_path = Path(output_path)

    html_path = Path(__file__).parent / "classify.html"
    page_data = _scan_pages(pages_dir)
    use_vision = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # Load pre-computed segmentation if available
    seg_path = pages_dir.parent.parent / "qc" / "segmentation.json"
    precomputed = {}
    if seg_path.exists():
        precomputed = json.loads(seg_path.read_text())
        print(f"Loaded pre-computed segmentation: {len(precomputed)} pages")

    print(f"Found {len(page_data)} pages in {pages_dir}")
    if precomputed:
        print(f"Segmenter: pre-computed")
    elif use_vision:
        print(f"Segmenter: Claude vision")
    else:
        print(f"Segmenter: heuristic (set ANTHROPIC_API_KEY for vision)")

    server = HTTPServer(("127.0.0.1", port), ClassifyHandler)
    server.pages_dir = pages_dir
    server.output_path = output_path
    server.html_path = html_path
    server.page_data = page_data
    server.use_vision = use_vision
    server.precomputed_segments = precomputed

    print(f"Classification tool: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
