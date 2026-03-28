"""
LLM structurer — sends extracted PDF content to Claude for classification
and structuring into the universal element schema.
"""

import json
import anthropic

from extract.pdf_parser import PageExtraction, ExtractedTable
from extract.figure_digitizer import digitize_figure, digitize_table_image


STRUCTURE_PROMPT = """You are converting building code content into structured JSON elements.

You will receive extracted text and table data from a page of a building code PDF.
The standard is: {standard}
The chapter is: {chapter}

Classify each distinct piece of information into one of these types:
- "provision": A rule, requirement, or definition with conditions
- "table": Tabular lookup data (already extracted — just confirm and structure)
- "formula": A mathematical equation or relationship
- "figure": Reference to a chart/graph (handled separately — just note its ID)
- "reference": Pointer to an external standard, map, or API

For each element, return a JSON object matching this structure:
{{
  "id": "<STANDARD>-<SECTION>-<suffix>",
  "type": "<type>",
  "source": {{
    "standard": "{standard}",
    "chapter": {chapter},
    "section": "<section number>",
    "page": {page}
  }},
  "title": "<descriptive title>",
  "description": "<optional plain-language summary or null>",
  "data": {{ <type-specific data — see below> }},
  "cross_references": ["<element IDs referenced>"],
  "metadata": {{
    "extracted_by": "auto",
    "qc_status": "pending",
    "qc_notes": null
  }}
}}

Data field formats by type:

provision:
{{"rule": "...", "conditions": [{{"parameter": "...", "operator": "...", "value": ..., "unit": "..."}}], "then": "...", "else": null, "exceptions": []}}

formula:
{{"expression": "...", "parameters": {{"name": {{"unit": "...", "range": [min, max], "source": "..."}}}}, "samples": {{}}}}

table:
{{"columns": [{{"name": "...", "unit": "..."}}], "rows": [{{...}}]}}

reference:
{{"target": "...", "url": null, "parameters": []}}

Return a JSON array of all elements found on this page.
Return ONLY the JSON array, no other text.
If no extractable elements exist on this page, return an empty array [].

ID format rules:
- Use the standard abbreviation without spaces: ASCE7-22, ACI318-19, IBC-2021
- Section numbers use dots: 26.5.1
- Suffix: T1, T2 for tables; F1, F2 for figures; P1, P2 for provisions; E1, E2 for formulas
- Example: ASCE7-22-26.5-T1
"""


def structure_page(
    page: PageExtraction,
    standard: str,
    chapter: int,
) -> list[dict]:
    """Send a page's extracted content to Claude for structuring.

    Args:
        page: Extracted page data from pdf_parser.
        standard: Standard name, e.g. "ASCE 7-22".
        chapter: Chapter number.

    Returns:
        List of structured element dicts.
    """
    # Build the content payload
    parts = []

    # Add text
    for block in page.text_blocks:
        parts.append(f"--- TEXT (page {block.page}) ---\n{block.text}")

    # Add tables as formatted text
    for i, table in enumerate(page.tables):
        table_str = _format_table(table)
        parts.append(f"--- TABLE {i+1} (page {table.page}) ---\n{table_str}")

    # Note figures (processed separately)
    for i, fig in enumerate(page.figures):
        parts.append(f"--- FIGURE {i+1} (page {fig.page}) --- [image attached separately]")

    if not parts:
        return []

    content_text = "\n\n".join(parts)

    standard_id = standard.replace(" ", "").replace("-", "")
    prompt = STRUCTURE_PROMPT.format(
        standard=standard,
        chapter=chapter,
        page=page.page_number,
    )

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6-20250514",
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "text", "text": f"Page content:\n\n{content_text}"},
                ],
            }
        ],
    )

    response_text = message.content[0].text
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:-1])

    elements = json.loads(response_text)
    return elements


def structure_figures(page: PageExtraction, standard: str, chapter: int) -> list[dict]:
    """Classify and extract figures. Skips diagrams and contour maps.

    Extractable (xy_chart, table_image) → type "figure" with digitized data.
    Illustrative (diagram, contour_map) → type "skipped_figure" with reason.
    """
    elements = []
    for i, fig in enumerate(page.figures):
        caption = fig.caption or ""
        figure_result = digitize_figure(fig.image_bytes, context=caption, verify=True)

        fig_class = figure_result.get("figure_class", {})
        skipped = figure_result.get("skipped", False)

        if skipped:
            element = {
                "id": f"{standard.replace(' ', '')}-{chapter}-F{i+1}",
                "type": "skipped_figure",
                "source": {
                    "standard": standard,
                    "chapter": chapter,
                    "section": "",
                    "page": fig.page,
                },
                "title": caption or f"Figure on page {fig.page}",
                "description": fig_class.get("description"),
                "data": {
                    "figure_type": fig_class.get("figure_type", "diagram"),
                    "description": fig_class.get("description", ""),
                    "skip_reason": fig_class.get("skip_reason", "Illustrative diagram — not computable data"),
                },
                "cross_references": [],
                "metadata": {
                    "extracted_by": "auto",
                    "qc_status": "pending",
                    "qc_notes": "Skipped: illustrative figure, not computable data. Human review recommended.",
                },
            }
        else:
            element = {
                "id": f"{standard.replace(' ', '')}-{chapter}-F{i+1}",
                "type": "figure",
                "source": {
                    "standard": standard,
                    "chapter": chapter,
                    "section": "",
                    "page": fig.page,
                },
                "title": caption or f"Figure on page {fig.page}",
                "description": fig_class.get("description"),
                "data": figure_result,
                "cross_references": [],
                "metadata": {
                    "extracted_by": "auto",
                    "qc_status": "pending",
                    "qc_notes": None,
                },
            }
        elements.append(element)

    return elements


def extract_chapter(
    pdf_path: str,
    standard: str,
    chapter: int,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[dict]:
    """Full extraction pipeline for a chapter.

    Args:
        pdf_path: Path to the PDF.
        standard: Standard name.
        chapter: Chapter number.
        start_page: First page of the chapter (1-indexed).
        end_page: Last page of the chapter (inclusive).

    Returns:
        List of all structured elements from the chapter.
    """
    from extract.pdf_parser import parse_pdf

    pages = parse_pdf(pdf_path, start_page=start_page, end_page=end_page)
    all_elements = []

    for page in pages:
        # Structure text and tables
        text_elements = structure_page(page, standard, chapter)
        all_elements.extend(text_elements)

        # Digitize figures
        if page.figures:
            figure_elements = structure_figures(page, standard, chapter)
            all_elements.extend(figure_elements)

    # Deduplicate by ID (keep first occurrence)
    seen = set()
    deduped = []
    for el in all_elements:
        if el["id"] not in seen:
            seen.add(el["id"])
            deduped.append(el)

    return deduped


def _format_table(table: ExtractedTable) -> str:
    """Format an extracted table as readable text for the LLM."""
    lines = [" | ".join(table.headers)]
    lines.append(" | ".join(["---"] * len(table.headers)))
    for row in table.rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)
