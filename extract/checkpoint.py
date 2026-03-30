"""
Checkpoint report generator.

Loads batch-report.json and all chapter JSONs, computes per-chapter metrics,
and produces:
  - output/qc/checkpoint.html  — color-coded HTML summary table
  - stdout                     — text summary
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
BATCH_REPORT = PROJECT_ROOT / "output" / "qc" / "batch-report.json"
RUNS_DIR = PROJECT_ROOT / "output" / "runs"
CHECKPOINT_HTML = PROJECT_ROOT / "output" / "qc" / "checkpoint.html"
SCHEMA_PATH = PROJECT_ROOT / "schema" / "element.schema.json"

# Element types considered "properly classified" (i.e. not the generic catch-all)
CLASSIFIED_TYPES = {
    "provision", "formula", "table", "figure", "definition",
    "exception", "reference", "user_note",
}
# text_block and heading are partial: heading is structural but not a content
# classification, text_block is the explicit unclassified bucket.
UNCLASSIFIED_TYPES = {"text_block"}

# The primary content fields that should be non-empty per type
FIDELITY_FIELDS: dict[str, list[str]] = {
    "provision":   ["rule"],
    "formula":     ["expression"],
    "table":       ["columns", "rows"],
    "figure":      ["description"],
    "definition":  ["term", "definition"],
    "reference":   ["target"],
    "exception":   ["rule"],
    "user_note":   ["rule"],
    "heading":     ["rule"],
    "text_block":  ["rule"],
}

# ---------------------------------------------------------------------------
# Schema validator (lightweight — no jsonschema dependency required)
# ---------------------------------------------------------------------------

def _load_schema() -> dict | None:
    try:
        return json.loads(SCHEMA_PATH.read_text())
    except Exception:
        return None

def _validate_element(element: dict, schema: dict | None) -> bool:
    """Minimal structural validation against schema required fields."""
    required_top = {"id", "type", "source", "title", "data", "metadata"}
    if not required_top.issubset(element.keys()):
        return False
    valid_types = {
        "table", "provision", "formula", "figure", "reference",
        "definition", "heading", "text_block", "user_note", "exception",
    }
    if element.get("type") not in valid_types:
        return False
    source = element.get("source", {})
    if not all(k in source for k in ("standard", "chapter", "section")):
        return False
    meta = element.get("metadata", {})
    if not all(k in meta for k in ("extracted_by", "qc_status")):
        return False
    # ID pattern: {STANDARD}-{CHAPTER}-{SECTION}-{SUFFIX}
    eid = element.get("id", "")
    if not re.match(r'^[A-Z0-9]+-[0-9]+-[0-9.]+-[A-Za-z0-9.-]+$', eid):
        return False
    return True

# ---------------------------------------------------------------------------
# Cross-reference analysis
# ---------------------------------------------------------------------------

def _chapter_num_from_id(element_id: str) -> int | None:
    """Extract chapter number from an element ID like ASCE7-22-26.1.1-P1."""
    m = re.match(r'^[A-Z0-9]+-\d+-(\d+)', element_id)
    if m:
        return int(m.group(1).split(".")[0])
    return None

def _analyse_xrefs(elements: list[dict], this_chapter: int) -> dict:
    """Count total, intra-chapter, and inter-chapter cross-references."""
    total = 0
    intra = 0
    inter = 0
    for el in elements:
        for ref in el.get("cross_references", []):
            total += 1
            ref_ch = _chapter_num_from_id(ref)
            if ref_ch is None or ref_ch == this_chapter:
                intra += 1
            else:
                inter += 1
    return {"total": total, "intra": intra, "inter": inter}

# ---------------------------------------------------------------------------
# Text fidelity
# ---------------------------------------------------------------------------

def _element_has_content(element: dict) -> bool:
    """Return True when the element's primary data field(s) are non-empty."""
    typ = element.get("type", "")
    data = element.get("data", {})
    fields = FIDELITY_FIELDS.get(typ, [])
    if not fields:
        return bool(data)
    for f in fields:
        val = data.get(f)
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
        if isinstance(val, (list, dict)) and not val:
            return False
    return True

def _fidelity(elements: list[dict]) -> float:
    """Fraction of elements with non-empty primary data fields."""
    if not elements:
        return 0.0
    return sum(1 for e in elements if _element_has_content(e)) / len(elements)

# ---------------------------------------------------------------------------
# Classification breakdown
# ---------------------------------------------------------------------------

def _classification_breakdown(elements: list[dict]) -> dict:
    """Return type counts and unclassified percentage."""
    type_counts: Counter = Counter(e.get("type", "unknown") for e in elements)
    total = len(elements)
    unclassified = sum(type_counts[t] for t in UNCLASSIFIED_TYPES if t in type_counts)
    pct_unclassified = unclassified / total if total else 0.0
    return {
        "counts": dict(type_counts),
        "unclassified": unclassified,
        "pct_unclassified": pct_unclassified,
    }

# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------

def _schema_validity(elements: list[dict], schema: dict | None) -> dict:
    """Return pass/fail counts for schema validation."""
    passed = sum(1 for e in elements if _validate_element(e, schema))
    total = len(elements)
    return {"passed": passed, "total": total, "pct": passed / total if total else 0.0}

# ---------------------------------------------------------------------------
# Per-chapter analysis
# ---------------------------------------------------------------------------

def analyse_chapter(ch_meta: dict, schema: dict | None) -> dict:
    """Load chapter JSON and compute all metrics."""
    ch_num = ch_meta["chapter"]
    file_path = PROJECT_ROOT / ch_meta["file"]

    result: dict = {
        "chapter": ch_num,
        "title": ch_meta.get("title", ""),
        "pages": ch_meta.get("pages", 0),
        "elements": ch_meta.get("elements", 0),
        "types": ch_meta.get("types", {}),
        "deploy_pass": ch_meta.get("deploy_pass", False),
        "time": ch_meta.get("time", 0),
        # computed
        "xrefs": {"total": 0, "intra": 0, "inter": 0},
        "fidelity": 0.0,
        "classification": {"counts": {}, "unclassified": 0, "pct_unclassified": 0.0},
        "schema_validity": {"passed": 0, "total": 0, "pct": 0.0},
        "load_error": None,
    }

    try:
        elements = json.loads(file_path.read_text())
    except Exception as exc:
        result["load_error"] = str(exc)
        return result

    result["xrefs"] = _analyse_xrefs(elements, ch_num)
    result["fidelity"] = _fidelity(elements)
    result["classification"] = _classification_breakdown(elements)
    result["schema_validity"] = _schema_validity(elements, schema)
    # Use actual element count from loaded JSON (more accurate than batch report)
    result["elements"] = len(elements)

    return result

# ---------------------------------------------------------------------------
# Overall stats (from batch report)
# ---------------------------------------------------------------------------

def _overall_stats(batch: dict, chapter_results: list[dict]) -> dict:
    summary = batch.get("summary", {})
    unresolved = batch.get("unresolved_summary", {})
    total_xrefs = sum(c["xrefs"]["total"] for c in chapter_results)
    total_inter = sum(c["xrefs"]["inter"] for c in chapter_results)
    total_intra = sum(c["xrefs"]["intra"] for c in chapter_results)
    total_elements = sum(c["elements"] for c in chapter_results)
    total_unclassified = sum(c["classification"]["unclassified"] for c in chapter_results)
    pct_unclassified = total_unclassified / total_elements if total_elements else 0.0
    total_schema_passed = sum(c["schema_validity"]["passed"] for c in chapter_results)
    total_schema_total = sum(c["schema_validity"]["total"] for c in chapter_results)
    avg_fidelity = (
        sum(c["fidelity"] * c["elements"] for c in chapter_results) / total_elements
        if total_elements else 0.0
    )

    return {
        "chapters_processed": summary.get("chapters_processed", len(chapter_results)),
        "total_elements": total_elements,
        "total_xrefs": total_xrefs,
        "total_inter_xrefs": total_inter,
        "total_intra_xrefs": total_intra,
        "broken_refs": unresolved.get("broken", 0),
        "pending_refs": unresolved.get("pending", 0),
        "total_symbols": batch.get("symbols_count", 0),
        "total_unclassified": total_unclassified,
        "pct_unclassified": pct_unclassified,
        "schema_passed": total_schema_passed,
        "schema_total": total_schema_total,
        "schema_pct": total_schema_passed / total_schema_total if total_schema_total else 0.0,
        "avg_fidelity": avg_fidelity,
        "chapters_passed": summary.get("passed", 0),
        "chapters_failed": summary.get("failed", 0),
    }

# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------

def print_text_summary(overall: dict, chapter_results: list[dict]) -> None:
    W = 72
    print("=" * W)
    print("CHECKPOINT REPORT".center(W))
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ").center(W))
    print("=" * W)

    print(f"\nOVERALL")
    print(f"  Chapters processed : {overall['chapters_processed']}")
    print(f"  Total elements     : {overall['total_elements']:,}")
    print(f"  Total symbols      : {overall['total_symbols']:,}")
    print(f"  Cross-references   : {overall['total_xrefs']:,} total "
          f"({overall['total_intra_xrefs']:,} intra / {overall['total_inter_xrefs']:,} inter)")
    print(f"  Broken refs        : {overall['broken_refs']:,}  "
          f"  Pending refs : {overall['pending_refs']:,}")
    print(f"  Unclassified (TB%) : {overall['pct_unclassified']*100:.1f}%  "
          f"({overall['total_unclassified']:,} text_block elements)")
    print(f"  Avg text fidelity  : {overall['avg_fidelity']*100:.1f}%")
    print(f"  Schema validity    : {overall['schema_passed']:,}/{overall['schema_total']:,} "
          f"({overall['schema_pct']*100:.1f}%)")
    print(f"  Deploy pass        : {overall['chapters_passed']} / {overall['chapters_processed']}")

    print(f"\nPER-CHAPTER")
    hdr = f"  {'Ch':>3}  {'Title':<32}  {'Elems':>5}  {'TB%':>5}  {'Fid%':>5}  {'XRef':>5}  {'Schm%':>6}  {'Status'}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for c in chapter_results:
        tb_pct = c["classification"]["pct_unclassified"] * 100
        fid_pct = c["fidelity"] * 100
        xref = c["xrefs"]["total"]
        schema_pct = c["schema_validity"]["pct"] * 100
        status = "PASS" if c["deploy_pass"] else "FAIL"
        if c["load_error"]:
            status = "ERR"
        title = c["title"][:32]
        print(f"  {c['chapter']:>3}  {title:<32}  {c['elements']:>5}  "
              f"{tb_pct:>4.0f}%  {fid_pct:>4.0f}%  {xref:>5}  {schema_pct:>5.0f}%  {status}")

    print()
    print("TB%  = % elements classified as text_block (lower is better)")
    print("Fid% = % elements with non-empty primary data fields")
    print("=" * W)

# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_CSS = """\
* { margin:0; padding:0; box-sizing:border-box; }
:root {
  --bg:#FFF; --surface:#F5F5F5; --text:#1A1A1A; --muted:#6B6B6B;
  --dim:#505050; --border:#D4D4D4; --subtle:#E8E8E8;
  --accent:#0057FF; --red:#DC2626; --amber:#D97706; --green:#16A34A; --cyan:#06B6D4;
  --pass-bg:rgba(22,163,74,0.08); --fail-bg:rgba(220,38,38,0.07);
  --partial-bg:rgba(217,119,6,0.08);
}
@media(prefers-color-scheme:dark){:root{
  --bg:#0A0A0A; --surface:#141414; --text:#E5E5E5; --muted:#808080;
  --dim:#606060; --border:#2A2A2A; --subtle:#1A1A1A;
  --accent:#3B82F6; --red:#EF4444; --amber:#F59E0B; --green:#22C55E; --cyan:#22D3EE;
  --pass-bg:rgba(22,163,74,0.08); --fail-bg:rgba(239,68,68,0.08);
  --partial-bg:rgba(245,158,11,0.08);
}}
html,body { background:var(--bg); color:var(--text);
  font-family:'JetBrains Mono','SF Mono','Cascadia Mono','IBM Plex Mono',monospace;
  font-size:12px; line-height:1.5; }
.page { max-width:1100px; margin:0 auto; padding:24px 16px; }
.hdr { border-bottom:1px solid var(--border); padding-bottom:10px; margin-bottom:20px; }
.hdr h1 { font-size:14px; font-weight:600; text-transform:uppercase; letter-spacing:.1em; }
.hdr .sub { font-size:10px; color:var(--muted); margin-top:2px; }
.section { margin-bottom:28px; }
.section-title { font-size:9px; text-transform:uppercase; letter-spacing:.12em;
  color:var(--muted); border-bottom:1px solid var(--border); padding-bottom:4px; margin-bottom:10px; }
/* stat grid */
.stats { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:8px; margin-bottom:20px; }
.stat { background:var(--surface); border:1px solid var(--border); padding:8px 10px; }
.stat .lbl { font-size:9px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.stat .val { font-size:18px; font-variant-numeric:tabular-nums; color:var(--text); margin-top:2px; }
.stat .sub { font-size:9px; color:var(--dim); margin-top:1px; }
.stat.s-pass { border-color:var(--green); }
.stat.s-fail { border-color:var(--red); }
.stat.s-warn { border-color:var(--amber); }
/* table */
table { width:100%; border-collapse:collapse; font-size:11px; }
th { font-size:9px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
  text-align:left; padding:4px 8px; border-bottom:2px solid var(--border); white-space:nowrap; }
td { padding:4px 8px; border-bottom:1px solid var(--border); font-variant-numeric:tabular-nums;
  white-space:nowrap; }
tr.pass td { background:var(--pass-bg); }
tr.fail td { background:var(--fail-bg); }
tr.partial td { background:var(--partial-bg); }
tr:hover td { filter:brightness(0.96); }
.badge { display:inline-block; font-size:9px; padding:1px 5px; border:1px solid;
  text-transform:uppercase; letter-spacing:.05em; }
.badge-pass { border-color:var(--green); color:var(--green); }
.badge-fail { border-color:var(--red); color:var(--red); }
.badge-partial { border-color:var(--amber); color:var(--amber); }
.badge-err { border-color:var(--muted); color:var(--muted); }
.bar-wrap { display:flex; align-items:center; gap:5px; }
.bar { height:6px; flex:1; background:var(--subtle); border-radius:1px; overflow:hidden; }
.bar-fill { height:100%; border-radius:1px; }
.bar-g { background:var(--green); }
.bar-a { background:var(--amber); }
.bar-r { background:var(--red); }
.types-list { font-size:10px; color:var(--dim); }
.note { font-size:9px; color:var(--muted); margin-top:12px; }
"""

def _bar(pct: float, thresholds=(0.8, 0.5)) -> str:
    """Return a small SVG-free percentage bar HTML snippet."""
    hi, lo = thresholds
    cls = "bar-g" if pct >= hi else ("bar-a" if pct >= lo else "bar-r")
    return (
        f'<div class="bar-wrap">'
        f'<div class="bar"><div class="bar-fill {cls}" style="width:{pct*100:.0f}%"></div></div>'
        f'<span>{pct*100:.0f}%</span>'
        f'</div>'
    )

def _badge(deploy_pass: bool, load_error: str | None) -> str:
    if load_error:
        return '<span class="badge badge-err">ERR</span>'
    if deploy_pass:
        return '<span class="badge badge-pass">PASS</span>'
    return '<span class="badge badge-fail">FAIL</span>'

def _row_class(c: dict) -> str:
    if c["load_error"]:
        return ""
    if c["deploy_pass"]:
        return "pass"
    # partial: fidelity decent but not pass
    if c["fidelity"] >= 0.7 and c["classification"]["pct_unclassified"] < 0.5:
        return "partial"
    return "fail"

def _types_summary(types: dict) -> str:
    parts = [f"{t}:{n}" for t, n in sorted(types.items(), key=lambda x: -x[1])]
    return "  ".join(parts[:6])

def _stat(label: str, value: str, sub: str = "", cls: str = "") -> str:
    return (
        f'<div class="stat {cls}">'
        f'<div class="lbl">{label}</div>'
        f'<div class="val">{value}</div>'
        f'{"<div class=sub>" + sub + "</div>" if sub else ""}'
        f'</div>'
    )

def generate_html(overall: dict, chapter_results: list[dict], batch_ts: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Overall stat cards
    broken_cls = "s-fail" if overall["broken_refs"] > 0 else "s-pass"
    unclass_cls = "s-warn" if overall["pct_unclassified"] > 0.3 else "s-pass"
    schema_cls = "s-pass" if overall["schema_pct"] >= 0.95 else ("s-warn" if overall["schema_pct"] >= 0.8 else "s-fail")

    stats_html = (
        _stat("Chapters", str(overall["chapters_processed"]),
              f"{overall['chapters_passed']} pass / {overall['chapters_failed']} fail",
              "s-fail" if overall["chapters_passed"] == 0 else "s-warn") +
        _stat("Elements", f"{overall['total_elements']:,}", "") +
        _stat("Symbols", f"{overall['total_symbols']:,}", "") +
        _stat("Cross-refs", f"{overall['total_xrefs']:,}",
              f"{overall['total_intra_xrefs']:,} intra / {overall['total_inter_xrefs']:,} inter") +
        _stat("Broken refs", f"{overall['broken_refs']:,}",
              f"{overall['pending_refs']:,} pending", broken_cls) +
        _stat("Unclassified", f"{overall['pct_unclassified']*100:.1f}%",
              f"{overall['total_unclassified']:,} text_blocks", unclass_cls) +
        _stat("Avg fidelity", f"{overall['avg_fidelity']*100:.1f}%", "non-empty primary fields") +
        _stat("Schema valid", f"{overall['schema_pct']*100:.1f}%",
              f"{overall['schema_passed']:,}/{overall['schema_total']:,}", schema_cls)
    )

    # Chapter table rows
    rows = []
    for c in chapter_results:
        rc = _row_class(c)
        tb_pct = c["classification"]["pct_unclassified"]
        fid = c["fidelity"]
        schema_pct = c["schema_validity"]["pct"]
        xrefs = c["xrefs"]
        types_str = _types_summary(c["types"])
        load_err = c["load_error"] or ""
        err_note = f' <span style="color:var(--red)" title="{load_err}">!</span>' if c["load_error"] else ""

        rows.append(
            f'<tr class="{rc}">'
            f'<td>{c["chapter"]}</td>'
            f'<td>{c["title"][:40]}{err_note}</td>'
            f'<td style="text-align:right">{c["pages"]}</td>'
            f'<td style="text-align:right">{c["elements"]:,}</td>'
            f'<td class="types-list">{types_str}</td>'
            f'<td>{_bar(1.0 - tb_pct, (0.7, 0.5))}</td>'
            f'<td>{_bar(fid, (0.85, 0.6))}</td>'
            f'<td style="text-align:right">{xrefs["total"]}</td>'
            f'<td style="text-align:right">{xrefs["intra"]}/{xrefs["inter"]}</td>'
            f'<td>{_bar(schema_pct, (0.95, 0.8))}</td>'
            f'<td>{_badge(c["deploy_pass"], c["load_error"])}</td>'
            f'</tr>'
        )

    rows_html = "\n".join(rows)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CHECKPOINT — Batch Quality Report</title>
<style>
{_CSS}
</style>
</head>
<body>
<div class="page">
  <div class="hdr">
    <h1>CHECKPOINT — Batch Quality Report</h1>
    <div class="sub">Generated {now} &nbsp;·&nbsp; Batch timestamp: {batch_ts}</div>
  </div>

  <div class="section">
    <div class="section-title">Overall Stats</div>
    <div class="stats">{stats_html}</div>
  </div>

  <div class="section">
    <div class="section-title">Per-Chapter Breakdown</div>
    <table>
      <thead>
        <tr>
          <th>Ch</th>
          <th>Title</th>
          <th style="text-align:right">Pages</th>
          <th style="text-align:right">Elems</th>
          <th>Type breakdown</th>
          <th style="min-width:120px">Classified %</th>
          <th style="min-width:120px">Fidelity %</th>
          <th style="text-align:right">XRefs</th>
          <th>Intra/Inter</th>
          <th style="min-width:120px">Schema %</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
{rows_html}
      </tbody>
    </table>
    <div class="note">
      Classified % = 100% &minus; text_block share (higher is better) &nbsp;·&nbsp;
      Fidelity % = elements with non-empty primary data field &nbsp;·&nbsp;
      Green &ge;80%&nbsp; Amber &ge;50%&nbsp; Red &lt;50% &nbsp;&nbsp;
      Status = deploy gate (schema + fidelity + xrefs + no SHALL in text_block)
    </div>
  </div>
</div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(batch_report_path: Path = BATCH_REPORT) -> None:
    if not batch_report_path.exists():
        print(f"ERROR: batch report not found: {batch_report_path}", file=sys.stderr)
        sys.exit(1)

    batch = json.loads(batch_report_path.read_text())
    schema = _load_schema()

    chapters_meta = batch.get("chapters", [])
    if not chapters_meta:
        print("ERROR: no chapters in batch report", file=sys.stderr)
        sys.exit(1)

    print(f"Analysing {len(chapters_meta)} chapters …", file=sys.stderr)

    chapter_results = []
    for ch_meta in chapters_meta:
        r = analyse_chapter(ch_meta, schema)
        chapter_results.append(r)
        status_sym = "." if not r["load_error"] else "E"
        print(status_sym, end="", flush=True, file=sys.stderr)

    print(file=sys.stderr)

    overall = _overall_stats(batch, chapter_results)

    # Text summary
    print_text_summary(overall, chapter_results)

    # HTML report
    CHECKPOINT_HTML.parent.mkdir(parents=True, exist_ok=True)
    batch_ts = batch.get("timestamp", "unknown")
    html = generate_html(overall, chapter_results, batch_ts)
    CHECKPOINT_HTML.write_text(html, encoding="utf-8")
    print(f"\nHTML report written to: {CHECKPOINT_HTML}", file=sys.stderr)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate checkpoint quality report")
    parser.add_argument(
        "--batch-report",
        default=str(BATCH_REPORT),
        help="Path to batch-report.json (default: output/qc/batch-report.json)",
    )
    args = parser.parse_args()
    run(Path(args.batch_report))
