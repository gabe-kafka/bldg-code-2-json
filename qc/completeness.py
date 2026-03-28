"""
Completeness checker — verifies that all sections, tables, and figures
found in the PDF are represented in the extracted JSON.
"""

from extract.pdf_parser import PageExtraction, extract_section_headings, extract_table_figure_labels


def check_completeness(
    elements: list[dict],
    pages: list[PageExtraction],
) -> dict:
    """Compare extracted elements against what's in the PDF.

    Returns:
        {
            "sections": {"found": [...], "missing": [...], "coverage": float},
            "tables": {"found": [...], "missing": [...], "coverage": float},
            "figures": {"found": [...], "missing": [...], "coverage": float},
            "overall_coverage": float
        }
    """
    # Get expected items from PDF
    headings = extract_section_headings(pages)
    labels = extract_table_figure_labels(pages)

    expected_sections = [h["section"] for h in headings]
    expected_tables = labels["tables"]
    expected_figures = labels["figures"]

    # Get what we extracted
    extracted_sections = set()
    extracted_tables = set()
    extracted_figures = set()

    for el in elements:
        section = el.get("source", {}).get("section", "")
        if section:
            extracted_sections.add(section)

        el_type = el.get("type", "")
        title = el.get("title", "")
        el_id = el.get("id", "")

        if el_type == "table":
            # Try to match table labels
            for label in expected_tables:
                if label in title or label in el_id:
                    extracted_tables.add(label)
        elif el_type == "figure":
            for label in expected_figures:
                if label in title or label in el_id:
                    extracted_figures.add(label)

    # Compute coverage
    sections_result = _coverage(expected_sections, extracted_sections)
    tables_result = _coverage(expected_tables, extracted_tables)
    figures_result = _coverage(expected_figures, extracted_figures)

    total_expected = len(expected_sections) + len(expected_tables) + len(expected_figures)
    total_found = len(sections_result["found"]) + len(tables_result["found"]) + len(figures_result["found"])
    overall = total_found / total_expected if total_expected > 0 else 1.0

    return {
        "sections": sections_result,
        "tables": tables_result,
        "figures": figures_result,
        "overall_coverage": round(overall, 3),
    }


def _coverage(expected: list[str], found: set[str]) -> dict:
    matched = [s for s in expected if s in found]
    missing = [s for s in expected if s not in found]
    pct = len(matched) / len(expected) if expected else 1.0
    return {
        "found": matched,
        "missing": missing,
        "coverage": round(pct, 3),
    }
