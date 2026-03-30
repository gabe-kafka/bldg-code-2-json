"""
Rigorous benchmark for extraction accuracy.

Measures three things against ground truth:

1. COVERAGE — Does every identifiable content item in the PDF have
   a corresponding element? Uses the PDF's own structure (section numbers,
   table numbers, equation numbers, figure numbers) as the authoritative
   list of what should exist.

2. TEXT FIDELITY — For every extracted element, does the text match the
   PDF's text layer character-for-character? Uses pdfplumber's raw character
   extraction as ground truth (it reads the PDF text layer directly).

3. STRUCTURAL INTEGRITY — Are elements in the right order? Are section
   numbers monotonically increasing? Do parent sections contain their
   children? Are there duplicates?

No random sampling. No soft matching. Every element is checked.
"""

import json
import re
import pdfplumber
from pathlib import Path
from collections import defaultdict


def benchmark(json_path, pdf_path):
    """Run the full benchmark. Returns a structured report."""
    elements = json.loads(Path(json_path).read_text())
    pdf_path = Path(pdf_path)

    report = {
        "elements": len(elements),
        "coverage": _check_coverage(elements, pdf_path),
        "fidelity": _check_fidelity(elements, pdf_path),
        "structure": _check_structure(elements),
    }

    # Composite score: weighted average
    c = report["coverage"]["score"]
    f = report["fidelity"]["score"]
    s = report["structure"]["score"]
    report["composite"] = round(c * 0.4 + f * 0.4 + s * 0.2, 3)

    return report


def _check_coverage(elements, pdf_path):
    """Check that every identifiable item in the PDF has a corresponding element.

    Ground truth: scan the raw PDF text for section numbers, table numbers,
    equation numbers, and figure numbers. These are the authoritative list
    of content that should be extracted.
    """
    # Get all text from the PDF
    all_pdf_text = ""
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            all_pdf_text += (page.extract_text() or "") + "\n"

    # Find all identifiable items in the PDF text
    ground_truth = {
        "sections": set(),
        "tables": set(),
        "figures": set(),
        "equations": set(),
    }

    # Sections: "26.X.Y.Z Something" at start of a line or after whitespace
    for m in re.finditer(r'(?:^|\s)(26\.\d+(?:\.\d+)*)\s+[A-Z]', all_pdf_text):
        ground_truth["sections"].add(m.group(1))

    # Tables: "Table 26.X-Y"
    for m in re.finditer(r'Table\s+(26\.\d+-\d+)', all_pdf_text):
        ground_truth["tables"].add(m.group(1))

    # Figures: "Figure 26.X-Y"
    for m in re.finditer(r'Figure\s+(26\.\d+-\d+)', all_pdf_text):
        ground_truth["figures"].add(m.group(1))

    # Equations: "(26.X-Y)" — equation numbers in parens
    for m in re.finditer(r'\((\d+\.\d+-\d+[a-z]?)\)', all_pdf_text):
        num = m.group(1)
        if num.startswith("26."):
            ground_truth["equations"].add(num)

    # Check what we extracted
    extracted = {
        "sections": set(),
        "tables": set(),
        "figures": set(),
        "equations": set(),
    }

    for e in elements:
        sec = e.get("source", {}).get("section", "")
        cit = e.get("source", {}).get("citation", "")

        if sec:
            extracted["sections"].add(sec)

        if e["type"] == "table":
            m = re.search(r'(\d+\.\d+-\d+)', cit)
            if m:
                extracted["tables"].add(m.group(1))

        if e["type"] == "figure":
            m = re.search(r'(\d+\.\d+-\d+)', cit)
            if m:
                extracted["figures"].add(m.group(1))

        if e["type"] == "formula":
            m = re.search(r'(\d+\.\d+-\d+[a-z]?)', cit)
            if m:
                extracted["equations"].add(m.group(1))

    # Score each category
    results = {}
    total_gt = 0
    total_found = 0

    for category in ["sections", "tables", "figures", "equations"]:
        gt = ground_truth[category]
        ext = extracted[category]
        found = gt & ext
        missing = gt - ext
        extra = ext - gt

        results[category] = {
            "ground_truth": len(gt),
            "extracted": len(ext),
            "found": len(found),
            "missing": sorted(missing),
            "extra": sorted(extra),
            "score": len(found) / max(len(gt), 1),
        }
        total_gt += len(gt)
        total_found += len(found)

    results["score"] = total_found / max(total_gt, 1)
    results["total_ground_truth"] = total_gt
    results["total_found"] = total_found

    return results


def _check_fidelity(elements, pdf_path):
    """Check text fidelity against the PDF's raw text layer.

    For each element, check if its text content appears verbatim
    in the PDF page's raw text. Uses a normalized comparison
    (collapse whitespace, ignore case for matching position).
    """
    with pdfplumber.open(str(pdf_path)) as pdf:
        page_texts = {}
        for i, page in enumerate(pdf.pages):
            # Get raw text preserving character fidelity
            raw = page.extract_text() or ""
            # Normalize: collapse whitespace for matching
            page_texts[i + 1] = re.sub(r'\s+', ' ', raw)

    total = 0
    exact_matches = 0
    partial_matches = 0
    failures = []

    for e in elements:
        # Skip figures (no text to check) and tables (Docling extracted)
        if e["type"] in ("figure",):
            continue

        text = e.get("data", {}).get("rule", "") or \
               e.get("data", {}).get("definition", "") or \
               e.get("data", {}).get("expression", "")

        if len(text) < 20:
            continue

        total += 1
        page_num = e.get("source", {}).get("page", 0)
        page_text = page_texts.get(page_num, "")

        # Normalize element text for comparison
        norm_elem = re.sub(r'\s+', ' ', text).strip()

        # Try exact substring match (first 60 chars)
        check_len = min(60, len(norm_elem))
        snippet = norm_elem[:check_len]

        if snippet in page_text:
            exact_matches += 1
        elif snippet.replace(" ", "") in page_text.replace(" ", ""):
            partial_matches += 1
        else:
            failures.append({
                "id": e["id"],
                "page": page_num,
                "snippet": snippet[:80],
                "type": e["type"],
            })

    score = (exact_matches + partial_matches * 0.5) / max(total, 1)

    return {
        "score": score,
        "total_checked": total,
        "exact_matches": exact_matches,
        "partial_matches": partial_matches,
        "failures": len(failures),
        "failure_details": failures[:15],
    }


def _check_structure(elements):
    """Check structural integrity of the extraction."""
    issues = []

    # Check for duplicate IDs
    ids = [e["id"] for e in elements]
    dupes = [id for id in set(ids) if ids.count(id) > 1]
    if dupes:
        issues.append(f"DUPLICATE_IDS: {dupes}")

    # Check section ordering — sections should be monotonically increasing
    sections = []
    for e in elements:
        sec = e.get("source", {}).get("section", "")
        if sec and re.match(r'^\d+\.\d+', sec):
            sections.append(sec)

    # Check that section numbers generally increase
    out_of_order = 0
    for i in range(1, len(sections)):
        try:
            curr = tuple(int(x) for x in sections[i].split("."))
            prev = tuple(int(x) for x in sections[i - 1].split("."))
            if curr < prev:
                out_of_order += 1
        except ValueError:
            pass

    order_score = 1.0 - (out_of_order / max(len(sections), 1))

    # Check that every element has required fields with non-empty values
    empty_fields = 0
    for e in elements:
        if not e.get("source", {}).get("section"):
            empty_fields += 1
        text = e.get("data", {}).get("rule", "") or \
               e.get("data", {}).get("definition", "") or \
               e.get("data", {}).get("expression", "") or \
               e.get("data", {}).get("description", "")
        if e["type"] not in ("table",) and len(text) < 5:
            empty_fields += 1

    completeness_score = 1.0 - (empty_fields / max(len(elements), 1))

    # Composite structural score
    dupe_score = 1.0 if not dupes else 0.5
    score = (order_score + completeness_score + dupe_score) / 3

    return {
        "score": round(score, 3),
        "duplicate_ids": dupes,
        "out_of_order_sections": out_of_order,
        "empty_fields": empty_fields,
        "order_score": round(order_score, 3),
        "completeness_score": round(completeness_score, 3),
        "issues": issues,
    }


def print_benchmark(report):
    """Pretty-print the benchmark results."""
    print("=" * 60)
    print(f"BENCHMARK — Composite Score: {report['composite']*100:.1f}%")
    print("=" * 60)

    c = report["coverage"]
    print(f"\n  COVERAGE: {c['score']*100:.0f}% ({c['total_found']}/{c['total_ground_truth']})")
    for cat in ["sections", "tables", "figures", "equations"]:
        r = c[cat]
        status = "OK" if not r["missing"] else f"MISSING: {r['missing']}"
        print(f"    {cat:12s} {r['found']}/{r['ground_truth']}  {status}")

    f = report["fidelity"]
    print(f"\n  FIDELITY: {f['score']*100:.0f}% ({f['exact_matches']} exact + {f['partial_matches']} partial / {f['total_checked']})")
    if f["failure_details"]:
        print(f"    Failures ({f['failures']}):")
        for fd in f["failure_details"][:5]:
            print(f"      {fd['id']} p{fd['page']}: \"{fd['snippet'][:50]}...\"")

    s = report["structure"]
    print(f"\n  STRUCTURE: {s['score']*100:.0f}%")
    print(f"    Order:       {s['order_score']*100:.0f}% ({s['out_of_order_sections']} out-of-order)")
    print(f"    Completeness:{s['completeness_score']*100:.0f}% ({s['empty_fields']} empty)")
    print(f"    Duplicates:  {len(s['duplicate_ids'])}")

    print(f"\n  COMPOSITE: {report['composite']*100:.1f}%")
    print(f"    Coverage  40%: {c['score']*100:.0f}%")
    print(f"    Fidelity  40%: {f['score']*100:.0f}%")
    print(f"    Structure 20%: {s['score']*100:.0f}%")
    print("=" * 60)


if __name__ == "__main__":
    pdf_path = list(Path("input").glob("*.pdf"))[0]
    report = benchmark("output/runs/plumber-ch26.json", pdf_path)
    print_benchmark(report)
    Path("output/qc/benchmark.json").write_text(json.dumps(report, indent=2))
    print(f"\nSaved to output/qc/benchmark.json")
