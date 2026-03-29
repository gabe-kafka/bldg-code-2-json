"""
LLM structurer — sends extracted PDF content to Claude for classification
and structuring into the universal element schema.
"""

import json
import anthropic

from extract.pdf_parser import PageExtraction, ExtractedTable
from extract.figure_digitizer import digitize_figure, digitize_table_image
from extract.post_processor import post_process
from extract.gold_standard import load_gold_elements

STRUCTURER_MODEL = "claude-sonnet-4-20250514"

MAX_FEWSHOT_EXAMPLES = 3


STRUCTURE_PROMPT = """You are converting building code content into structured JSON elements.

You will receive extracted text and table data from a page of a building code PDF.
The standard is: {standard}
The chapter is: {chapter}

Classify each distinct piece of information into one of these types:
- "provision": A rule, requirement, or conditional statement
- "definition": A term definition (e.g. "BASIC WIND SPEED: ...", "X is defined as ...", "Y means ...")
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

definition:
{{"term": "...", "definition": "...", "conditions": [], "exceptions": []}}

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


def select_fewshot_examples(
    gold_elements: list[dict],
    page: PageExtraction,
    max_examples: int = MAX_FEWSHOT_EXAMPLES,
) -> list[dict]:
    """Select type-relevant gold examples for a page.

    Prefers examples matching the content types detected on the page:
    - Tables present → prefer table examples
    - Text-heavy → prefer provision examples
    - Mixed → pick diverse types
    """
    if not gold_elements:
        return []

    # Detect what's on the page
    has_tables = bool(page.tables)
    has_figures = bool(page.figures)

    # Determine preferred types based on page content
    preferred_types = []
    if has_tables:
        preferred_types.append("table")
    if has_figures:
        preferred_types.extend(["figure", "skipped_figure"])
    # Text blocks usually contain provisions/definitions
    if page.text_blocks:
        preferred_types.append("provision")

    # Score each gold element: preferred type gets priority
    preferred_set = set(preferred_types)

    def sort_key(el: dict) -> tuple:
        is_preferred = el.get("type") not in preferred_set
        return (is_preferred, el.get("type", ""))

    sorted_golds = sorted(gold_elements, key=sort_key)

    # Pick up to max_examples, preferring type diversity
    selected = []
    seen_types: set[str] = set()
    # First pass: one per type
    for el in sorted_golds:
        if len(selected) >= max_examples:
            break
        t = el.get("type", "")
        if t not in seen_types:
            selected.append(el)
            seen_types.add(t)
    # Second pass: fill remaining slots
    for el in sorted_golds:
        if len(selected) >= max_examples:
            break
        if el not in selected:
            selected.append(el)

    return selected


def _build_fewshot_section(examples: list[dict]) -> str:
    """Format gold examples as a REFERENCE EXAMPLES prompt section."""
    lines = ["\n\nREFERENCE EXAMPLES — use these as models for your output format:\n"]
    for i, ex in enumerate(examples, 1):
        lines.append(f"Example {i} ({ex.get('type', 'unknown')}):")
        lines.append(json.dumps(ex, indent=2))
        lines.append("")
    return "\n".join(lines)


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

    # Inject few-shot gold examples if available
    gold_elements = load_gold_elements()
    examples = select_fewshot_examples(gold_elements, page)
    if examples:
        prompt += _build_fewshot_section(examples)

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=STRUCTURER_MODEL,
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

    try:
        elements = json.loads(response_text)
    except json.JSONDecodeError:
        print(f"  Warning: failed to parse LLM response for page {page.page_number}, skipping")
        return []
    return elements


def structure_figures(page: PageExtraction, standard: str, chapter: int, figure_counter: list[int]) -> list[dict]:
    """Classify and extract figures. Skips diagrams and contour maps.

    Extractable (xy_chart, table_image) → type "figure" with digitized data.
    Illustrative (diagram, contour_map) → type "skipped_figure" with reason.

    Args:
        figure_counter: Single-element list [n] used as a mutable counter
                        across pages to avoid ID collisions.
    """
    elements = []
    for fig in page.figures:
        figure_counter[0] += 1
        fig_num = figure_counter[0]
        caption = fig.caption or ""
        figure_result = digitize_figure(fig.image_bytes, context=caption, verify=True)

        fig_class = figure_result.get("figure_class", {})
        skipped = figure_result.get("skipped", False)
        std_id = standard.replace(" ", "")

        if skipped:
            element = {
                "id": f"{std_id}-{chapter}-F{fig_num}",
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
                "id": f"{std_id}-{chapter}-F{fig_num}",
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
    render_dpi: int = 300,
    pages_per_chunk: int = 1,
) -> list[dict]:
    """Full extraction pipeline for a chapter. Parses PDF then extracts."""
    from extract.pdf_parser import parse_pdf

    pages = parse_pdf(pdf_path, start_page=start_page, end_page=end_page, render_dpi=render_dpi)
    return extract_chapter_from_pages(pages, standard, chapter, pages_per_chunk)


def extract_chapter_from_pages(
    pages: list,
    standard: str,
    chapter: int,
    pages_per_chunk: int = 1,
    progress_callback: callable | None = None,
) -> list[dict]:
    """Extract from pre-parsed pages. Avoids re-parsing the PDF.

    Args:
        progress_callback: Optional callable(page_index, total_pages) for progress.
    """
    all_elements = []
    figure_counter = [0]  # mutable counter shared across pages
    total_pages = len(pages)

    for chunk_start in range(0, len(pages), pages_per_chunk):
        chunk = pages[chunk_start:chunk_start + pages_per_chunk]

        for page in chunk:
            text_elements = structure_page(page, standard, chapter)
            all_elements.extend(text_elements)

            if page.figures:
                figure_elements = structure_figures(page, standard, chapter, figure_counter)
                all_elements.extend(figure_elements)

            if progress_callback:
                progress_callback(chunk_start + 1, total_pages)

    # Apply deterministic post-processing BEFORE deduplication so that
    # ID normalization (space stripping) runs first — otherwise two elements
    # whose IDs differ only by whitespace both survive dedupe but become
    # duplicates after normalization.
    processed = post_process(all_elements)

    # Deduplicate by ID (keep first occurrence)
    seen = set()
    deduped = []
    for el in processed:
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
