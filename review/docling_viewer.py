"""
Docling result viewer — overlays detected elements on page images.

Serves a visual tool showing Docling's bounding boxes color-coded
by element type, with the markdown text in a side panel.
"""

import json
import re
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


class ViewerHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._serve_file(self.server.html_path, "text/html")
        elif path == "/api/data":
            self._json_response(self.server.view_data)
        elif path.startswith("/api/img/"):
            name = path.split("/api/img/", 1)[1]
            img_path = self.server.pages_dir / name
            if img_path.exists():
                self._serve_file(img_path, "image/png")
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def _serve_file(self, filepath, content_type):
        data = Path(filepath).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _json_response(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        if args and str(args[0]).startswith("4"):
            super().log_message(format, *args)


def build_view_data(docling_json_path, pages_dir, dpi=200):
    """Build page-by-page view data from Docling export."""
    d = json.loads(Path(docling_json_path).read_text())

    pages_info = d.get("pages", {})
    # PDF points to pixel scale factor
    # PDF is 72 points/inch, rendered at `dpi`
    scale = dpi / 72.0

    # Collect all elements with provenance
    elements_by_page = {}

    for collection_name in ["texts", "tables", "pictures"]:
        for item in d.get(collection_name, []):
            for prov in item.get("prov", []):
                pg = prov["page_no"]
                bbox = prov["bbox"]
                # Convert from PDF coords (bottom-left origin) to pixel coords (top-left origin)
                page_info = pages_info.get(str(pg), {})
                page_h_pts = page_info.get("size", {}).get("height", 792)

                x0 = bbox["l"] * scale
                x1 = bbox["r"] * scale
                # Flip y: PDF bottom-left -> pixel top-left
                y0 = (page_h_pts - bbox["t"]) * scale
                y1 = (page_h_pts - bbox["b"]) * scale

                label = item.get("label", "unknown")
                text = item.get("text", "")[:200]

                elements_by_page.setdefault(pg, []).append({
                    "x0": round(x0), "y0": round(y0),
                    "x1": round(x1), "y1": round(y1),
                    "label": label,
                    "text": text,
                    "collection": collection_name,
                })

    # Build page list
    page_files = []
    for f in sorted(Path(pages_dir).iterdir()):
        m = re.match(r"page-(\d+)\.png", f.name)
        if m:
            page_files.append({"index": int(m.group(1)), "filename": f.name})

    return {
        "pages": page_files,
        "elements": {str(k): v for k, v in elements_by_page.items()},
        "markdown": d.get("_markdown", ""),
    }


def start_viewer(docling_json, pages_dir, port=8793):
    pages_dir = Path(pages_dir)
    html_path = Path(__file__).parent / "docling_view.html"

    view_data = build_view_data(docling_json, pages_dir)
    total_elements = sum(len(v) for v in view_data["elements"].values())
    print(f"Loaded {len(view_data['pages'])} pages, {total_elements} elements")

    server = HTTPServer(("127.0.0.1", port), ViewerHandler)
    server.pages_dir = pages_dir
    server.html_path = html_path
    server.view_data = view_data

    print(f"Docling viewer: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
