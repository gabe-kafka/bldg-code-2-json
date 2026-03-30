"""
Unresolved reference tracker.

Scans elements for references to other chapters, categorizes them as
pending (chapter not yet extracted) or broken (chapter extracted but
target doesn't exist).
"""

import json
import re
from pathlib import Path

from extract.manifest import load_manifest, get_all_element_ids


def find_unresolved(elements, manifest_path="output/manifest.json"):
    """Find all cross-references that point outside extracted chapters.

    Returns structured unresolved report.
    """
    manifest = load_manifest(manifest_path)
    extracted_chapters = set(manifest.get("chapters", {}).keys())
    all_ids = get_all_element_ids(manifest)

    # Get this chapter's element IDs
    local_ids = {e["id"] for e in elements}

    # Scan all text for external references
    ref_patterns = [
        # Chapter references: "Chapter 27", "Chapters 27 through 31"
        (re.compile(r'Chapter\s+(\d+)'), "chapter"),
        # Section references: "Section 27.3.1"
        (re.compile(r'Section\s+(\d+\.\d+(?:\.\d+)*)'), "section"),
        # Table references: "Table 27.3-1"
        (re.compile(r'Table\s+(\d+\.\d+-\d+)'), "table"),
        # Figure references: "Figure 27.3-8"
        (re.compile(r'Figure\s+(\d+\.\d+-\d+[A-D]?)'), "figure"),
        # Equation references: "Equation (27.3-1)"
        (re.compile(r'Eq(?:uation)?\.\s*\((\d+\.\d+-\d+[a-z]?)\)'), "equation"),
    ]

    # Determine this chapter number
    this_chapter = None
    for e in elements:
        ch = e.get("source", {}).get("chapter")
        if ch:
            this_chapter = str(ch)
            break

    unresolved = []
    seen = set()

    for e in elements:
        text = (e.get("data", {}).get("rule", "") or "") + " " + \
               (e.get("data", {}).get("definition", "") or "") + " " + \
               (e.get("data", {}).get("target", "") or "")

        for pattern, ref_type in ref_patterns:
            for m in pattern.finditer(text):
                ref_text = m.group(0)
                ref_num = m.group(1)

                # Determine target chapter
                if ref_type == "chapter":
                    target_ch = ref_num
                else:
                    target_ch = ref_num.split(".")[0]

                # Skip self-chapter references
                if target_ch == this_chapter:
                    continue

                # Deduplicate
                key = (e["id"], ref_text)
                if key in seen:
                    continue
                seen.add(key)

                # Check if target chapter is extracted
                if target_ch in extracted_chapters:
                    # Chapter is extracted — check if specific element exists
                    target_id = None
                    if ref_type == "table":
                        # Look for table with this citation
                        for eid, ech in all_ids.items():
                            if f"T{ref_num.replace('.', '-')}" in eid:
                                target_id = eid
                                break
                    elif ref_type == "figure":
                        for eid, ech in all_ids.items():
                            if f"F{ref_num.replace('.', '-')}" in eid:
                                target_id = eid
                                break
                    elif ref_type == "section":
                        for eid, ech in all_ids.items():
                            if ref_num in eid:
                                target_id = eid
                                break

                    if target_id:
                        status = "resolved"
                    else:
                        status = "broken"
                        reason = f"Chapter {target_ch} extracted but target not found"
                else:
                    status = "pending"
                    reason = f"Chapter {target_ch} not yet extracted"

                if status != "resolved":
                    unresolved.append({
                        "from_element": e["id"],
                        "reference_text": ref_text,
                        "target_chapter": int(target_ch),
                        "target_type": ref_type,
                        "status": status,
                        "reason": reason if status != "resolved" else None,
                    })

    # Summary
    pending = sum(1 for u in unresolved if u["status"] == "pending")
    broken = sum(1 for u in unresolved if u["status"] == "broken")

    report = {
        "standard": elements[0]["source"]["standard"] if elements else "",
        "unresolved": unresolved,
        "summary": {
            "total": len(unresolved),
            "pending": pending,
            "broken": broken,
        }
    }

    return report


def save_unresolved(report, path="output/unresolved.json"):
    """Save unresolved report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    return path


def print_unresolved(report):
    """Print unresolved summary."""
    s = report["summary"]
    print(f"  Unresolved references: {s['total']} ({s['pending']} pending, {s['broken']} broken)")

    if s["broken"] > 0:
        print(f"  BROKEN references (target chapter extracted but element missing):")
        for u in report["unresolved"]:
            if u["status"] == "broken":
                print(f"    {u['from_element']}: {u['reference_text']} → {u['reason']}")

    # Group pending by target chapter
    pending_by_ch = {}
    for u in report["unresolved"]:
        if u["status"] == "pending":
            ch = u["target_chapter"]
            pending_by_ch[ch] = pending_by_ch.get(ch, 0) + 1

    if pending_by_ch:
        print(f"  Pending by chapter: {dict(sorted(pending_by_ch.items()))}")
