"""
Building Code PDF -> Structured JSON Scraper
=============================================

This is the entry point for extracting building code PDFs into
machine-readable JSON. Hand this file to a teammate and they
can start extracting any code.

QUICK START (single chapter)
----------------------------
    from extract.scrape import scrape

    result = scrape(
        pdf_path="input/ASCE 7-22.pdf",
        standard="ASCE 7-22",
        chapter=26,
    )
    # result["json_path"]  -> "output/runs/asce722-ch26.json"
    # result["stats"]      -> {"provision": 525, "definition": 33, ...}
    # result["elements"]   -> list of element dicts

QUICK START (full book, all chapters)
-------------------------------------
    from extract.scrape import scrape_book

    report = scrape_book(
        pdf_path="input/ASCE 7-22.pdf",
        standard="ASCE 7-22",
    )
    # Extracts every chapter, builds cross-references, writes a batch report.

CLI
---
    python cli.py extract --pdf input/my-code.pdf --standard "ASCE 7-22" --chapter 26
    python cli.py batch   --pdf input/my-code.pdf --standard "ASCE 7-22"

REQUIREMENTS
------------
The input PDF must have an embedded text layer — the pipeline reads
characters and font metadata directly from the PDF structure. It does
NOT use OCR or vision. Scanned/image-only PDFs will produce nothing.

To check: open the PDF and try to select/copy text. If you can, it
has an embedded text layer and will work. If not, you need to OCR it
first (e.g. with Adobe Acrobat or ocrmypdf) before running this.

ADAPTING TO A NEW CODE
----------------------
The pipeline works on any embedded-text PDF where content blocks are
led by **bold labels**. This covers most US codes: ASCE, IBC, ACI, AISC.

To try a new code:

    1. Run it:
       result = scrape("input/IBC-2021.pdf", standard="IBC-2021", chapter=16)

    2. Validate the output:
       python cli.py validate --file output/runs/ibc2021-ch16.json

    3. Check the element count and type breakdown in result["stats"].
       A good extraction has 80-95% of content classified as provision,
       definition, formula, table, or figure. High text_block % means
       the classifier couldn't determine the type.

If coverage is low, check these (in order of likelihood):

    - SKIP_PATTERNS in pipeline_v3.py: your code's headers/footers may
      differ. Add patterns so page furniture is filtered out.

    - elastic.py _discover_patterns(): the pattern learner looks for bold
      labels like "26.1.1 Scope" or "ALL-CAPS TERM:". If your code uses
      different heading typography, add detection patterns there.

    - _parse_conditions() in pipeline_v3.py: condition extraction is
      domain-specific (Risk Category, Exposure, hurricane-prone). Other
      codes will need their own condition vocabulary added here.

HOW THE PIPELINE WORKS
----------------------
    Phase 1  PyMuPDF bold scan     -> bold label map (which text is bold on each page)
    Phase 2  Docling parse         -> text in reading order + tables + figures
    Phase 3  Elastic learning      -> discover THIS chapter's label patterns
    Phase 4  Classification        -> every text block gets a type from its bold label
    Phase 5  Enrichment            -> tables, figures, equations, cross-references
    Phase 6  Cleanup               -> dedup, merge fragments, associate parents
    Phase 7  Symbols               -> resolve formula parameters from global registry
    Phase 8  Output                -> JSON + markdown files

All extraction logic lives in pipeline_v3.py. This module is a clean
wrapper that handles I/O, null-fixing, and output writing.

DEPENDENCIES
------------
    pip install docling PyMuPDF pdfplumber click jsonschema Pillow
"""

import json
from pathlib import Path


def scrape(
    pdf_path: str,
    standard: str,
    chapter: int,
    output_dir: str = "output/runs",
) -> dict:
    """Extract structured elements from one chapter of a building code PDF.

    Args:
        pdf_path:   Path to the PDF. Can be a full book or a single-chapter
                    extract. The pipeline reads whichever pages are present.
        standard:   Human-readable standard name used in element IDs and
                    filenames. Examples: "ASCE 7-22", "IBC-2021", "ACI 318-19".
        chapter:    Chapter number to extract.
        output_dir: Directory for JSON and markdown output files.

    Returns:
        dict with:
            elements   list[dict]  the extracted elements
            json_path  str         path to the output JSON file
            md_path    str         path to the output markdown file
            stats      dict        element counts by type, e.g. {"provision": 42}
    """
    from extract.pipeline_v3 import run_v3

    pdf_path = str(pdf_path)
    elements, markdown = run_v3(pdf_path, standard=standard, chapter=chapter)

    # Null-fix for schema compliance (pipeline sometimes leaves these None)
    for e in elements:
        if e.get("description") is None:
            e["description"] = ""
        src = e.get("source", {})
        if src.get("citation") is None:
            src["citation"] = f"Section {src.get('section', '')}"

    # Write output
    std_slug = standard.lower().replace(" ", "").replace("-", "")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{std_slug}-ch{chapter}.json"
    json_path.write_text(json.dumps(elements, indent=2))

    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown)

    stats = {}
    for e in elements:
        stats[e["type"]] = stats.get(e["type"], 0) + 1

    return {
        "elements": elements,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "stats": stats,
    }


def scrape_book(
    pdf_path: str,
    standard: str,
    output_dir: str = "output/runs",
    chapters: list[int] | None = None,
    skip_reserved: bool = True,
) -> dict:
    """Extract all chapters from a full building code PDF.

    Detects chapter boundaries automatically by scanning for "CHAPTER N"
    headings, extracts each to a temporary single-chapter PDF, and runs
    the pipeline on each.

    Args:
        pdf_path:       Path to the full book PDF.
        standard:       Standard name, e.g. "ASCE 7-22", "IBC-2021".
        output_dir:     Directory for per-chapter JSON output.
        chapters:       Optional list of specific chapter numbers to process.
                        If None, processes all detected chapters.
        skip_reserved:  Skip chapters whose title contains "RESERVED".

    Returns:
        dict -- batch report with per-chapter results and summary.
        See output/qc/batch-report.json for the full schema.
    """
    from extract.batch import run_batch

    return run_batch(
        pdf_path,
        standard=standard,
        chapters=chapters,
        skip_reserved=skip_reserved,
        output_dir=output_dir,
    )
