"""
Recursive tuning harness.

Runs the pipeline, measures accuracy, diagnoses failures,
and reports what needs fixing. The outer loop (Claude or human)
reads the diagnosis and modifies the pipeline code.
"""

import json
import re
import random
from pathlib import Path


def measure(json_path, pdf_path):
    """Run all accuracy checks and return a structured report."""
    elements = json.loads(Path(json_path).read_text())

    report = {
        "total_elements": len(elements),
        "schema_valid": 0,
        "completeness": {},
        "spot_check": {},
        "classification": {},
        "failures": [],  # specific, actionable failure descriptions
    }

    # Schema validation
    from qc.schema_validator import validate_chapter
    schema_result = validate_chapter(elements)
    report["schema_valid"] = schema_result["passed"]

    # Type counts
    types = {}
    for e in elements:
        types[e["type"]] = types.get(e["type"], 0) + 1
    report["types"] = types

    # === COMPLETENESS: cross-reference check ===
    all_text = " ".join(
        e.get("data", {}).get("rule", "") + " " +
        e.get("data", {}).get("definition", "") + " " +
        e.get("data", {}).get("expression", "") + " " +
        e.get("data", {}).get("description", "")
        for e in elements
    )

    table_refs = set(re.findall(r'Table\s+(26\.\d+-\d+)', all_text))
    figure_refs = set(re.findall(r'Figure\s+(26\.\d+-\d+)', all_text))
    section_refs = {s for s in re.findall(r'Section\s+(26\.\d+(?:\.\d+)*)', all_text)}

    extracted_tables = set()
    extracted_figures = set()
    extracted_sections = set()

    for e in elements:
        cit = e.get("source", {}).get("citation", "")
        sec = e.get("source", {}).get("section", "")
        m = re.search(r'(\d+\.\d+-\d+)', cit)
        if e["type"] == "table" and m:
            extracted_tables.add(m.group(1))
        if e["type"] == "figure" and m:
            extracted_figures.add(m.group(1))
        if sec:
            extracted_sections.add(sec)

    missing_tables = table_refs - extracted_tables
    missing_figures = figure_refs - extracted_figures
    missing_sections = {s for s in section_refs if s.startswith("26.")} - extracted_sections

    total_refs = len(table_refs) + len(figure_refs) + len({s for s in section_refs if s.startswith("26.")})
    found_refs = (len(table_refs) - len(missing_tables)) + \
                 (len(figure_refs) - len(missing_figures)) + \
                 len({s for s in section_refs if s.startswith("26.")} - missing_sections)

    report["completeness"] = {
        "score": found_refs / max(total_refs, 1),
        "total": total_refs,
        "found": found_refs,
        "missing_tables": sorted(missing_tables),
        "missing_figures": sorted(missing_figures),
        "missing_sections": sorted(missing_sections),
    }

    for s in sorted(missing_sections):
        report["failures"].append(f"MISSING_SECTION: {s} — referenced in text but no element has this section number")

    # === SPOT-CHECK: text fidelity ===
    import pdfplumber

    checkable = [e for e in elements
                 if e["type"] in ("provision", "definition")
                 and len(e.get("data", {}).get("rule", e.get("data", {}).get("definition", ""))) > 50
                 and "heading" not in (e.get("metadata", {}).get("qc_notes") or "")]

    random.seed(42)
    sample = random.sample(checkable, min(30, len(checkable)))

    with pdfplumber.open(str(pdf_path)) as pdf:
        matches = 0
        mismatches = []
        for e in sample:
            page_num = e["source"]["page"] - 1
            if page_num < 0 or page_num >= len(pdf.pages):
                continue
            page = pdf.pages[page_num]
            raw_text = (page.extract_text() or "").replace("\n", " ")
            elem_text = e["data"].get("rule", e["data"].get("definition", ""))
            snippet = elem_text[:50].replace("  ", " ").strip()
            snippet_nospace = snippet.replace(" ", "")

            if snippet in raw_text or snippet_nospace in raw_text.replace(" ", ""):
                matches += 1
            else:
                mismatches.append({
                    "id": e["id"],
                    "page": e["source"]["page"],
                    "expected": snippet,
                    "section": e["source"]["section"],
                })

    report["spot_check"] = {
        "score": matches / max(len(sample), 1),
        "checked": len(sample),
        "matched": matches,
        "mismatches": mismatches[:10],
    }

    for mm in mismatches[:5]:
        report["failures"].append(
            f"TEXT_MISMATCH: {mm['id']} p{mm['page']} sec {mm['section']} — "
            f"extracted text doesn't match PDF: \"{mm['expected'][:60]}...\""
        )

    # === CLASSIFICATION quality ===
    # Check: provisions should contain "shall" or similar
    provs = [e for e in elements if e["type"] == "provision"
             and "heading" not in (e.get("metadata", {}).get("qc_notes") or "")
             and "unclassified" not in (e.get("metadata", {}).get("qc_notes") or "")]
    shall_count = sum(1 for p in provs if "shall" in p["data"].get("rule", "").lower())

    unclassified = [e for e in elements
                    if e.get("metadata", {}).get("qc_notes") == "unclassified"]

    report["classification"] = {
        "provisions_with_shall": f"{shall_count}/{len(provs)}",
        "unclassified_text_blocks": len(unclassified),
    }

    if len(unclassified) > 20:
        report["failures"].append(
            f"UNCLASSIFIED: {len(unclassified)} text blocks not classified as provision/definition/formula"
        )

    # Overall score
    completeness = report["completeness"]["score"]
    spot_check = report["spot_check"]["score"]
    schema_pct = report["schema_valid"] / max(report["total_elements"], 1)
    report["overall_score"] = round((completeness + spot_check + schema_pct) / 3, 3)

    return report


def print_report(report):
    """Pretty-print the accuracy report."""
    print("=" * 60)
    print(f"ACCURACY REPORT — Score: {report['overall_score']*100:.0f}%")
    print("=" * 60)
    print(f"  Elements:     {report['total_elements']}")
    print(f"  Schema valid: {report['schema_valid']}/{report['total_elements']}")
    print(f"  Types:        {report['types']}")
    print(f"\n  Completeness: {report['completeness']['score']*100:.0f}% ({report['completeness']['found']}/{report['completeness']['total']})")
    if report["completeness"]["missing_sections"]:
        print(f"    Missing sections: {report['completeness']['missing_sections']}")
    print(f"\n  Spot-check:   {report['spot_check']['score']*100:.0f}% ({report['spot_check']['matched']}/{report['spot_check']['checked']})")
    if report["spot_check"]["mismatches"]:
        print(f"    Mismatches:")
        for mm in report["spot_check"]["mismatches"][:5]:
            print(f"      {mm['id']} p{mm['page']}: \"{mm['expected'][:50]}...\"")
    print(f"\n  Classification: {report['classification']}")
    print(f"\n  FAILURES ({len(report['failures'])}):")
    for f in report["failures"]:
        print(f"    {f}")
    print("=" * 60)


if __name__ == "__main__":
    from pathlib import Path
    pdf_path = list(Path("input").glob("*.pdf"))[0]
    report = measure("output/runs/plumber-ch26.json", pdf_path)
    print_report(report)
    Path("output/qc/tune-report.json").write_text(json.dumps(report, indent=2))
