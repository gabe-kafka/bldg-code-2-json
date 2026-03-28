"""
PDF parser — extracts text blocks, tables, and figure images from a building code PDF.
Uses pdfplumber for text/tables and Pillow for image export.
"""

import io
import pdfplumber
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ExtractedTable:
    page: int
    bbox: tuple
    headers: list[str]
    rows: list[list[str]]


@dataclass
class ExtractedFigure:
    page: int
    bbox: tuple
    image_bytes: bytes
    caption: str | None = None


@dataclass
class ExtractedText:
    page: int
    text: str
    section: str | None = None


@dataclass
class PageExtraction:
    page_number: int
    text_blocks: list[ExtractedText] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    figures: list[ExtractedFigure] = field(default_factory=list)


def parse_pdf(pdf_path: str | Path, start_page: int = 1, end_page: int | None = None, render_dpi: int = 300) -> list[PageExtraction]:
    """Extract text, tables, and figures from a PDF page range.

    Args:
        pdf_path: Path to the PDF file.
        start_page: First page to extract (1-indexed).
        end_page: Last page to extract (inclusive). None = all remaining pages.

    Returns:
        List of PageExtraction objects, one per page.
    """
    pdf_path = Path(pdf_path)
    results = []

    with pdfplumber.open(pdf_path) as pdf:
        if end_page is None:
            end_page = len(pdf.pages)

        for page_num in range(start_page - 1, min(end_page, len(pdf.pages))):
            page = pdf.pages[page_num]
            extraction = PageExtraction(page_number=page_num + 1)

            # --- Text ---
            text = page.extract_text()
            if text:
                extraction.text_blocks.append(
                    ExtractedText(page=page_num + 1, text=text)
                )

            # --- Tables ---
            for table in page.extract_tables():
                if not table or len(table) < 2:
                    continue
                headers = [str(cell or "").strip() for cell in table[0]]
                rows = []
                for row in table[1:]:
                    rows.append([str(cell or "").strip() for cell in row])
                extraction.tables.append(
                    ExtractedTable(
                        page=page_num + 1,
                        bbox=(),
                        headers=headers,
                        rows=rows,
                    )
                )

            # --- Figures (rasterize at high resolution for complex diagrams) ---
            images = page.images
            if images:
                page_image = page.to_image(resolution=render_dpi)
                img_bytes = io.BytesIO()
                page_image.original.save(img_bytes, format="PNG")
                extraction.figures.append(
                    ExtractedFigure(
                        page=page_num + 1,
                        bbox=(),
                        image_bytes=img_bytes.getvalue(),
                    )
                )

            results.append(extraction)

    return results


def extract_section_headings(pages: list[PageExtraction]) -> list[dict]:
    """Pull section headings from extracted text for completeness checking."""
    import re
    headings = []
    pattern = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\s+(.+)$", re.MULTILINE)

    for page in pages:
        for block in page.text_blocks:
            for match in pattern.finditer(block.text):
                headings.append({
                    "section": match.group(1),
                    "title": match.group(2).strip(),
                    "page": page.page_number,
                })
    return headings


def extract_table_figure_labels(pages: list[PageExtraction]) -> dict[str, list[str]]:
    """Find Table X.X and Figure X.X labels in the text."""
    import re
    labels = {"tables": [], "figures": []}
    table_pat = re.compile(r"Table\s+(\d+[\d.]*-\d+)", re.IGNORECASE)
    fig_pat = re.compile(r"Figure\s+(\d+[\d.]*-\d+)", re.IGNORECASE)

    for page in pages:
        for block in page.text_blocks:
            for m in table_pat.finditer(block.text):
                label = m.group(1)
                if label not in labels["tables"]:
                    labels["tables"].append(label)
            for m in fig_pat.finditer(block.text):
                label = m.group(1)
                if label not in labels["figures"]:
                    labels["figures"].append(label)
    return labels
