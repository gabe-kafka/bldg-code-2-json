"""
Unresolved reference tracker.

Scans elements for references to other chapters. Categorizes as:
- resolved: target found in extracted chapters
- pending: target chapter not yet extracted
- broken: target chapter extracted but specific element not found
- external: reference to a different standard entirely (ASTM, IBC, ACI, etc.)

External references are cataloged separately to guide which codes to tackle next.
"""

import json
import re
from pathlib import Path
from collections import defaultdict


# External standard patterns
EXTERNAL_PATTERNS = [
    re.compile(r'\b(ASTM\s+[A-Z]\d+[a-z]?(?:/[A-Z]\d+[a-z]?)?)'),
    re.compile(r'\b(ANSI[/ ][\w.-]+)'),
    re.compile(r'\b(CAN/CSA[\w .-]+)'),
    re.compile(r'\b(ICC\s+\d+)'),
    re.compile(r'\b(ACI\s+\d+[\w.-]*)'),
    re.compile(r'\b(AISC\s+\d+[\w.-]*)'),
    re.compile(r'\b(AWS\s+[\w.]+)'),
    re.compile(r'\b(IBC[\s-]\d+)'),
    re.compile(r'\b(ASCE[\s/]SEI\s+[\d-]+)'),
    re.compile(r'\b(TMS\s+\d+)'),
    re.compile(r'\b(APA\s+[\w-]+)'),
    re.compile(r'\b(FEMA\s+[\w-]+)'),
]


def find_unresolved(elements, manifest_path="output/manifest.json"):
    """Find all references pointing outside the current chapter."""
    manifest = _load_manifest(manifest_path)
    extracted_chapters = set(manifest.get("chapters", {}).keys())

    # Build normalized indexes from ALL extracted chapters
    num_index, sec_index, ch_index = _build_global_index(manifest)

    # Determine this chapter
    this_chapter = None
    for e in elements:
        ch = e.get("source", {}).get("chapter")
        if ch:
            this_chapter = str(ch)
            break

    # Internal ref patterns (same standard, different chapter)
    internal_patterns = [
        (re.compile(r'Chapter\s+(\d+)'), "chapter"),
        (re.compile(r'Section\s+(\d+\.\d+(?:\.\d+)*)'), "section"),
        (re.compile(r'Table\s+(\d+\.\d+-\d+)'), "table"),
        (re.compile(r'Figure\s+(\d+\.\d+-\d+[A-D]?)'), "figure"),
        (re.compile(r'Eq(?:uation)?\.\s*\((\d+\.\d+-\d+[a-z]?)\)'), "equation"),
    ]

    unresolved = []
    external_refs = []
    seen = set()

    for e in elements:
        text = (e.get("data", {}).get("rule", "") or "") + " " + \
               (e.get("data", {}).get("definition", "") or "") + " " + \
               (e.get("data", {}).get("target", "") or "")

        # Scan for internal cross-chapter references
        for pattern, ref_type in internal_patterns:
            for m in pattern.finditer(text):
                ref_text = m.group(0)
                ref_num = m.group(1)

                target_ch = ref_num if ref_type == "chapter" else ref_num.split(".")[0]
                if target_ch == this_chapter:
                    continue

                key = (e["id"], ref_text)
                if key in seen:
                    continue
                seen.add(key)

                # Try to resolve
                resolved = False
                if ref_type == "chapter":
                    resolved = target_ch in ch_index
                elif ref_type == "section":
                    resolved = ref_num in sec_index
                elif ref_type in ("table", "figure", "equation"):
                    resolved = ref_num in num_index

                if resolved:
                    continue  # successfully resolved — not unresolved

                if target_ch in extracted_chapters:
                    status = "broken"
                    reason = f"Chapter {target_ch} extracted but {ref_type} {ref_num} not found"
                else:
                    status = "pending"
                    reason = f"Chapter {target_ch} not yet extracted"

                unresolved.append({
                    "from_element": e["id"],
                    "reference_text": ref_text,
                    "target_chapter": int(target_ch) if target_ch.isdigit() else 0,
                    "target_type": ref_type,
                    "status": status,
                    "reason": reason,
                })

        # Scan for external standard references
        for pat in EXTERNAL_PATTERNS:
            for m in pat.finditer(text):
                ext_ref = m.group(1).strip()
                ext_key = (e["id"], ext_ref)
                if ext_key in seen:
                    continue
                seen.add(ext_key)
                external_refs.append({
                    "from_element": e["id"],
                    "from_chapter": int(this_chapter) if this_chapter and this_chapter.isdigit() else 0,
                    "standard": ext_ref,
                })

    pending = sum(1 for u in unresolved if u["status"] == "pending")
    broken = sum(1 for u in unresolved if u["status"] == "broken")

    # Catalog external refs by standard
    ext_by_standard = defaultdict(lambda: {"count": 0, "chapters": set()})
    for er in external_refs:
        # Normalize standard name
        base = er["standard"].split(",")[0].split(" ")[0:2]
        key = " ".join(base).strip()
        ext_by_standard[key]["count"] += 1
        ext_by_standard[key]["chapters"].add(er["from_chapter"])

    ext_catalog = []
    for std, info in sorted(ext_by_standard.items(), key=lambda x: -x[1]["count"]):
        ext_catalog.append({
            "standard": std,
            "reference_count": info["count"],
            "referenced_from_chapters": sorted(info["chapters"]),
        })

    report = {
        "standard": elements[0]["source"]["standard"] if elements else "",
        "unresolved": unresolved,
        "external_references": ext_catalog,
        "summary": {
            "total_unresolved": len(unresolved),
            "pending": pending,
            "broken": broken,
            "external_standards": len(ext_catalog),
            "total_external_refs": len(external_refs),
        }
    }

    return report


def _load_manifest(path):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text())
    return {"chapters": {}}


def _build_global_index(manifest):
    """Build normalized lookup indexes from all extracted chapters."""
    num_index = {}   # "26.6-1" → True
    sec_index = {}   # "26.6" → True
    ch_index = {}    # "26" → True

    for ch, info in manifest.get("chapters", {}).items():
        ch_index[ch] = True
        filepath = info.get("file", "")
        if filepath and Path(filepath).exists():
            try:
                elements = json.loads(Path(filepath).read_text())
                for e in elements:
                    cit = e.get("source", {}).get("citation", "")
                    sec = e.get("source", {}).get("section", "")
                    m = re.search(r'(\d+\.\d+-\d+[A-Da-d]?)', cit)
                    if m:
                        num_index[m.group(1)] = True
                    if sec:
                        sec_index[sec] = True
            except Exception:
                pass

    return num_index, sec_index, ch_index


def save_unresolved(report, path="output/unresolved.json"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=_serialize))
    return path


def _serialize(obj):
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def print_unresolved(report):
    s = report["summary"]
    print(f"  Internal refs: {s['total_unresolved']} ({s['pending']} pending, {s['broken']} broken)")

    if s["broken"] > 0:
        # Group broken by target chapter
        broken_by_ch = defaultdict(int)
        for u in report["unresolved"]:
            if u["status"] == "broken":
                broken_by_ch[u["target_chapter"]] += 1
        print(f"  Broken by chapter: {dict(sorted(broken_by_ch.items()))}")

    # External references
    if report.get("external_references"):
        print(f"\n  External standards referenced: {s['external_standards']}")
        print(f"  Total external refs: {s['total_external_refs']}")
        print(f"\n  TOP EXTERNAL DEPENDENCIES:")
        for ext in report["external_references"][:15]:
            chs = ext["referenced_from_chapters"]
            print(f"    {ext['reference_count']:>3}x  {ext['standard']:30s}  from chapters {chs}")
