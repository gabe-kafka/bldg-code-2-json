"""
Hybrid extraction pipeline: Docling + PyMuPDF + deterministic rules.

1. Docling extracts document structure (text, tables, pictures, sections)
2. PyMuPDF enriches with font flags (bold detection despite obfuscated names)
3. Deterministic rules classify: heading, definition, provision, equation, table, figure
4. Outputs JSON matching the element schema
"""

import json
import re
import fitz
from pathlib import Path
from datetime import datetime, timezone


def run_hybrid(pdf_path, standard="ASCE 7-22", chapter=26, start_page=0):
    """Run the full hybrid pipeline on a PDF.

    Returns list of element dicts matching schema/element.schema.json.
    """
    pdf_path = Path(pdf_path)

    # Step 1: Docling
    print("  [1/3] Docling: extracting document structure...")
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document
    docling_dict = doc.export_to_dict()
    markdown = doc.export_to_markdown()

    # Step 2: PyMuPDF font enrichment
    print("  [2/3] PyMuPDF: extracting font metadata...")
    font_map = _build_font_map(pdf_path)

    # Step 3: Build elements
    print("  [3/3] Building elements...")
    elements = _build_elements(docling_dict, font_map, markdown, standard, chapter, start_page)

    print(f"  Done: {len(elements)} elements")
    return elements, markdown


def _build_font_map(pdf_path):
    """Build a map of (page, y_position) -> font_info using PyMuPDF.

    Returns dict keyed by (page_num, round(y)) -> {bold, size, font}
    """
    doc = fitz.open(str(pdf_path))
    font_map = {}

    for page_num in range(len(doc)):
        page = doc[page_num]
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    y = round(span["bbox"][1])
                    is_bold = bool(span["flags"] & 16)
                    key = (page_num, y)
                    if key not in font_map or is_bold:
                        font_map[key] = {
                            "bold": is_bold,
                            "size": round(span["size"], 1),
                            "font": span["font"],
                        }
    doc.close()
    return font_map


def _is_bold_at(font_map, page_num, y_pdf):
    """Check if text at a given PDF y-coordinate is bold."""
    y = round(y_pdf)
    for dy in range(0, 5):
        for try_y in [y + dy, y - dy]:
            info = font_map.get((page_num, try_y))
            if info:
                return info["bold"]
    return False


def _font_size_at(font_map, page_num, y_pdf):
    """Get font size at a given PDF y-coordinate."""
    y = round(y_pdf)
    for dy in range(0, 5):
        for try_y in [y + dy, y - dy]:
            info = font_map.get((page_num, try_y))
            if info:
                return info["size"]
    return 9.5  # default body size


def _classify_text(text, label, is_bold, font_size, page_num):
    """Classify a text element into our type system."""
    text_stripped = text.strip()

    # Docling already classified some things
    if label in ("section_header", "title"):
        return "heading"
    if label in ("page_header", "page_footer"):
        return None  # skip

    # Definition: BOLD ALL-CAPS TERM followed by colon
    # Pattern: "BASIC WIND SPEED, V: definition text..."
    def_match = re.match(r'^([A-Z][A-Z0-9 ,./()]+)\s*[:]\s*(.+)', text_stripped)
    if def_match and is_bold:
        return "definition"
    # Also catch: "TERM: text" where term is short and all-caps
    if def_match and len(def_match.group(1)) < 60:
        return "definition"

    # Equation: contains mathematical expressions
    # Look for: "= ", common math patterns, equation references
    eq_patterns = [
        r'[A-Za-z_]+\s*[=]\s*\d',       # Kz = 2.01
        r'[A-Za-z_]+\s*[=]\s*[A-Za-z]',  # qz = 0.00256
        r'\d+\.\d+\s*[×*]\s*',           # 0.00256 *
        r'[≤≥<>]\s*\d',                   # >= 0.2
        r'\^\s*\d',                        # ^2
        r'Eq\.\s*\(',                      # Eq. (26.10-1)
    ]
    if any(re.search(p, text_stripped) for p in eq_patterns):
        # But only if it's short (not a provision that mentions an equation)
        if len(text_stripped) < 120:
            return "formula"

    # Provision: contains "shall", "shall not", "is permitted", "must"
    provision_words = ["shall ", "shall not", "is permitted", "must be", "are required"]
    if any(w in text_stripped.lower() for w in provision_words):
        return "provision"

    # Heading: bold + short + section number pattern
    if is_bold and len(text_stripped) < 100:
        if re.match(r'^\d+\.\d+', text_stripped):
            return "heading"
        if text_stripped.isupper() and len(text_stripped) < 60:
            return "heading"

    # Default: text_block (will review)
    return "text_block"


def _build_elements(docling_dict, font_map, markdown, standard, chapter, start_page):
    """Convert Docling output + font data into schema elements."""
    elements = []
    pages_info = docling_dict.get("pages", {})

    std_slug = standard.replace(" ", "")

    # Track section context
    current_section = str(chapter)
    section_counter = {}
    element_id_set = set()

    def make_id(section, suffix):
        eid = f"{std_slug}-{section}-{suffix}"
        # Deduplicate
        if eid in element_id_set:
            n = 2
            while f"{eid}-{n}" in element_id_set:
                n += 1
            eid = f"{eid}-{n}"
        element_id_set.add(eid)
        return eid

    def next_suffix(section, prefix):
        key = f"{section}-{prefix}"
        section_counter[key] = section_counter.get(key, 0) + 1
        return f"{prefix}{section_counter[key]}"

    # Process texts
    for item in docling_dict.get("texts", []):
        text = item.get("text", "").strip()
        if not text:
            continue

        label = item.get("label", "text")
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        bbox = prov.get("bbox", {})
        y_top = bbox.get("t", 0)

        # Get font info from PyMuPDF
        pymupdf_page = page_no - 1 + start_page
        page_h = pages_info.get(str(page_no), {}).get("size", {}).get("height", 792)
        # Convert Docling's bottom-left y to PyMuPDF's top-left y
        y_for_font = page_h - y_top
        is_bold = _is_bold_at(font_map, pymupdf_page, y_for_font)
        font_size = _font_size_at(font_map, pymupdf_page, y_for_font)

        # Classify
        elem_type = _classify_text(text, label, is_bold, font_size, page_no)
        if elem_type is None:
            continue  # skip page headers/footers

        # Track current section from headings
        if elem_type == "heading":
            sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)*)', text)
            if sec_match:
                current_section = sec_match.group(1)

        # Build element
        section = current_section

        if elem_type == "heading":
            sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)*)\s*(.*)', text)
            if sec_match:
                section = sec_match.group(1)
                title = sec_match.group(2).strip()
            else:
                title = text
            suffix = next_suffix(section, "H")
            element = {
                "id": make_id(section, suffix),
                "type": "provision",  # headings become provision containers
                "classification": "structured",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "citation": f"Section {section}", "page": page_no + start_page},
                "title": text[:200],
                "description": None,
                "data": {"rule": text, "conditions": [], "then": title, "else": None, "exceptions": []},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "heading"}
            }

        elif elem_type == "definition":
            def_match = re.match(r'^([A-Z][A-Z0-9 ,./()]+)\s*[:]\s*(.*)', text, re.DOTALL)
            if def_match:
                term = def_match.group(1).strip()
                definition = def_match.group(2).strip()
            else:
                term = text[:50]
                definition = text
            suffix = next_suffix(section, "D")
            element = {
                "id": make_id(section, suffix),
                "type": "definition",
                "classification": "structured",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "page": page_no + start_page},
                "title": f"Definition: {term}",
                "description": None,
                "data": {"term": term, "definition": definition},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
            }

        elif elem_type == "formula":
            suffix = next_suffix(section, "E")
            # Try to extract equation number
            eq_match = re.search(r'Eq\.\s*\(([^)]+)\)', text)
            citation = eq_match.group(0) if eq_match else None
            element = {
                "id": make_id(section, suffix),
                "type": "formula",
                "classification": "structured",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "citation": citation, "page": page_no + start_page},
                "title": f"Equation in Section {section}",
                "description": None,
                "data": {"expression": text, "parameters": {}},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
            }

        elif elem_type == "provision":
            suffix = next_suffix(section, "P")
            element = {
                "id": make_id(section, suffix),
                "type": "provision",
                "classification": "structured",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "page": page_no + start_page},
                "title": f"Provision in Section {section}",
                "description": None,
                "data": {"rule": text, "conditions": [], "then": None, "else": None, "exceptions": []},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
            }

        else:  # text_block
            suffix = next_suffix(section, "T")
            element = {
                "id": make_id(section, suffix),
                "type": "provision",
                "classification": "structured",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "page": page_no + start_page},
                "title": f"Text in Section {section}",
                "description": None,
                "data": {"rule": text, "conditions": [], "then": None, "else": None, "exceptions": []},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "unclassified_text"}
            }

        elements.append(element)

    # Process tables
    for item in docling_dict.get("tables", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)

        # Extract table grid from Docling's table_cells
        grid = item.get("data", {}).get("table_cells", [])
        if not grid:
            continue

        # Build columns and rows from grid
        columns, rows = _parse_table_grid(grid)

        # Try to get table caption
        caption = item.get("text", "")
        table_match = re.search(r'Table\s+(\d+\.\d+-\d+)', caption)
        citation = table_match.group(0) if table_match else None
        table_section = table_match.group(1).rsplit("-", 1)[0] if table_match else current_section

        suffix = next_suffix(table_section, "T")
        element = {
            "id": make_id(table_section, f"T{citation.replace('Table ', '').replace('.', '-') if citation else suffix}"),
            "type": "table",
            "classification": "structured",
            "source": {"standard": standard, "chapter": chapter,
                       "section": table_section, "citation": citation, "page": page_no + start_page},
            "title": caption[:200] if caption else f"Table in Section {table_section}",
            "description": None,
            "data": {"columns": columns, "rows": rows},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
        }
        elements.append(element)

    # Process pictures (linked figures)
    for item in docling_dict.get("pictures", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        caption = item.get("text", "")

        fig_match = re.search(r'Figure\s+(\d+\.\d+-\d+)', caption)
        citation = fig_match.group(0) if fig_match else None
        fig_section = fig_match.group(1).rsplit("-", 1)[0] if fig_match else current_section

        suffix = next_suffix(fig_section, "F")
        element = {
            "id": make_id(fig_section, f"F{citation.replace('Figure ', '').replace('.', '-') if citation else suffix}"),
            "type": "figure",
            "classification": "linked",
            "source": {"standard": standard, "chapter": chapter,
                       "section": fig_section, "citation": citation, "page": page_no + start_page},
            "title": caption[:200] if caption else f"Figure in Section {fig_section}",
            "description": caption[:500] if caption else None,
            "data": {"figure_type": "other", "description": caption or "Figure", "source_pdf_page": page_no + start_page},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
        }
        elements.append(element)

    return elements


def _parse_table_grid(table_cells):
    """Convert Docling table_cells to columns + rows."""
    if not table_cells:
        return [], []

    # Find dimensions
    max_row = max(c.get("end_row_offset_idx", c.get("row", 0)) for c in table_cells)
    max_col = max(c.get("end_col_offset_idx", c.get("col", 0)) for c in table_cells)

    # Build grid
    grid = {}
    for c in table_cells:
        r = c.get("start_row_offset_idx", c.get("row_span", [0])[0] if isinstance(c.get("row_span"), list) else 0)
        col = c.get("start_col_offset_idx", c.get("col_span", [0])[0] if isinstance(c.get("col_span"), list) else 0)
        text = c.get("text", "")
        grid[(r, col)] = text

    if not grid:
        return [], []

    # First row = headers
    n_cols = max_col
    columns = []
    for c in range(n_cols):
        name = grid.get((0, c), f"col_{c}")
        columns.append({"name": name, "unit": None})

    # Remaining rows
    rows = []
    for r in range(1, max_row):
        row = {}
        for c in range(n_cols):
            col_name = columns[c]["name"] if c < len(columns) else f"col_{c}"
            val = grid.get((r, c), "")
            # Try to parse numbers
            try:
                val = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                pass
            row[col_name] = val
        rows.append(row)

    return columns, rows
