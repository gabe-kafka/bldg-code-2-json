"""
Local review server for human-in-the-loop disagreement resolution.

Serves a visual tool where the user sees PDF page images alongside
competing extraction values and makes authoritative decisions.
"""

import json
import re
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


class ReviewHandler(SimpleHTTPRequestHandler):
    """Handles API routes and static file serving for the review tool."""

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            self._serve_file(self.server.html_path, "text/html")
        elif path == "/api/disagreements":
            self._json_response(self.server.disagreements)
        elif path == "/api/decisions":
            self._json_response(self._load_decisions())
        elif path.startswith("/api/pages/"):
            name = path.split("/api/pages/", 1)[1]
            img_path = self.server.pages_dir / name
            if img_path.exists():
                self._serve_file(img_path, "image/png")
            else:
                self.send_error(404, f"Page not found: {name}")
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/decisions":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            self._save_decision(body)
            self._json_response({"status": "ok"})
        else:
            self.send_error(404)

    def _serve_file(self, filepath, content_type):
        data = Path(filepath).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _json_response(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _load_decisions(self):
        if self.server.decisions_path.exists():
            return json.loads(self.server.decisions_path.read_text())
        return {"source_compare": str(self.server.compare_path), "decisions": []}

    def _save_decision(self, decision):
        doc = self._load_decisions()
        decision["timestamp"] = datetime.now(timezone.utc).isoformat()
        # Replace existing decision for same element, or append
        existing = {i: d for i, d in enumerate(doc["decisions"])
                    if d["element_id"] == decision["element_id"]}
        if existing:
            idx = next(iter(existing))
            doc["decisions"][idx] = decision
        else:
            doc["decisions"].append(decision)
        self.server.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        self.server.decisions_path.write_text(json.dumps(doc, indent=2))

    def log_message(self, format, *args):
        # Suppress per-request logs except errors
        if args and str(args[0]).startswith("4"):
            super().log_message(format, *args)


def _compute_page_offset(pages_dir, elements_a, elements_b):
    """Map source.page numbers to PNG filenames by computing the offset."""
    # Find PNG indices in directory
    png_indices = []
    for f in pages_dir.iterdir():
        m = re.match(r"page-(\d+)\.png", f.name)
        if m:
            png_indices.append(int(m.group(1)))
    if not png_indices:
        return 0
    min_file_idx = min(png_indices)

    # Find minimum source page across both runs
    source_pages = []
    for el in elements_a + elements_b:
        p = el.get("source", {}).get("page")
        if isinstance(p, int):
            source_pages.append(p)
    if not source_pages:
        return 0

    return min(source_pages) - min_file_idx


def _build_disagreements(compare_path, run_a_path, run_b_path, pages_dir):
    """Load compare report and enrich disagreements with full element data."""
    compare = json.loads(compare_path.read_text())
    elements_a = json.loads(run_a_path.read_text())
    elements_b = json.loads(run_b_path.read_text())

    idx_a = {el["id"]: el for el in elements_a}
    idx_b = {el["id"]: el for el in elements_b}

    offset = _compute_page_offset(pages_dir, elements_a, elements_b)

    enriched = []
    for d in compare.get("authoritative_disagreed", []):
        eid = d["id"]
        id_a = d.get("id_a", eid)
        id_b = d.get("id_b", eid)
        el_a = idx_a.get(id_a)
        el_b = idx_b.get(id_b)
        if not el_a or not el_b:
            continue

        # Determine page image(s)
        page_a = el_a.get("source", {}).get("page")
        page_b = el_b.get("source", {}).get("page")
        pages = set()
        for p in [page_a, page_b]:
            if isinstance(p, int):
                pages.add(f"page-{p - offset:03d}.png")

        enriched.append({
            "id": eid,
            "id_a": id_a,
            "id_b": id_b,
            "type_a": d.get("type_a"),
            "type_b": d.get("type_b"),
            "match_basis": d.get("match_basis", "id"),
            "fields": d.get("fields", []),
            "element_a": el_a,
            "element_b": el_b,
            "page_images": sorted(pages),
        })

    return enriched


def start_server(compare_path, run_a, run_b, pages_dir, port,
                 decisions_path=None):
    """Start the review HTTP server."""
    compare_path = Path(compare_path)
    run_a = Path(run_a)
    run_b = Path(run_b)
    pages_dir = Path(pages_dir)

    if decisions_path is None:
        decisions_path = compare_path.parent / "human-decisions.json"
    else:
        decisions_path = Path(decisions_path)

    html_path = Path(__file__).parent / "index.html"

    disagreements = _build_disagreements(compare_path, run_a, run_b, pages_dir)
    print(f"Loaded {len(disagreements)} authoritative disagreements")

    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    server.compare_path = compare_path
    server.pages_dir = pages_dir
    server.decisions_path = decisions_path
    server.html_path = html_path
    server.disagreements = disagreements

    print(f"Review tool: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
