"""
Multi-chapter manifest.

Tracks all extracted chapters and their contents for cross-chapter
reference resolution.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


def build_manifest_entry(elements, chapter, output_file):
    """Summarize a chapter's contents for the manifest."""
    sections = sorted(set(e["source"]["section"] for e in elements))

    tables = set()
    figures = set()
    equations = set()
    for e in elements:
        cit = e.get("source", {}).get("citation", "")
        if e["type"] == "table":
            m = re.search(r'(\d+\.\d+-\d+)', cit)
            if m:
                tables.add(m.group(1))
        elif e["type"] == "figure":
            m = re.search(r'(\d+\.\d+-\d+[A-D]?)', cit)
            if m:
                figures.add(m.group(1))
        elif e["type"] == "formula":
            m = re.search(r'(\d+\.\d+-\d+[a-z]?)', cit)
            if m:
                equations.add(m.group(1))

    types = {}
    for e in elements:
        types[e["type"]] = types.get(e["type"], 0) + 1

    return {
        "chapter": chapter,
        "file": str(output_file),
        "elements": len(elements),
        "types": types,
        "extracted": datetime.now(timezone.utc).isoformat(),
        "sections": sorted(sections),
        "tables": sorted(tables),
        "figures": sorted(figures),
        "equations": sorted(equations),
    }


def update_manifest(manifest_path, entry):
    """Add or update a chapter in the manifest."""
    manifest_path = Path(manifest_path)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {"standard": "", "chapters": {}}

    ch = str(entry["chapter"])
    manifest["chapters"][ch] = entry
    manifest["standard"] = entry.get("standard", manifest.get("standard", ""))

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def load_manifest(manifest_path="output/manifest.json"):
    """Load the manifest."""
    path = Path(manifest_path)
    if path.exists():
        return json.loads(path.read_text())
    return {"standard": "", "chapters": {}}


def get_all_element_ids(manifest):
    """Load all element IDs across all chapters in the manifest."""
    all_ids = {}
    for ch, info in manifest.get("chapters", {}).items():
        filepath = info.get("file")
        if filepath and Path(filepath).exists():
            elements = json.loads(Path(filepath).read_text())
            for e in elements:
                all_ids[e["id"]] = ch
    return all_ids
