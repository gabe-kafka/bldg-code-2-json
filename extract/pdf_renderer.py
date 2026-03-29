"""
PDF renderer — converts PDF pages to images for vision extraction.

Uses PyMuPDF (fitz) to render each page as a PNG at configurable DPI.
This is the only PDF parsing step in the vision pipeline.
"""

from pathlib import Path
import fitz  # PyMuPDF


def render_pages(
    pdf_path: str | Path,
    output_dir: str | Path,
    start_page: int = 1,
    end_page: int | None = None,
    dpi: int = 200,
) -> list[Path]:
    """Render PDF pages to PNG images.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory to write PNG files.
        start_page: First page to render (1-indexed).
        end_page: Last page to render (inclusive). None = all remaining.
        dpi: Resolution for rendering.

    Returns:
        List of paths to rendered PNG images, ordered by page number.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    total = len(doc)

    # Convert to 0-indexed
    start_idx = max(0, start_page - 1)
    end_idx = min(total, end_page) if end_page else total

    image_paths = []
    for i in range(start_idx, end_idx):
        page = doc[i]
        pix = page.get_pixmap(dpi=dpi)
        out_path = output_dir / f"page-{i + 1:03d}.png"
        pix.save(str(out_path))
        image_paths.append(out_path)

    doc.close()
    return image_paths
