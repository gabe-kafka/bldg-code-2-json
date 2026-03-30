"""
Hybrid v2: Docling structure + pdfplumber character patching.

Docling provides: correct reading order, table structure, figure detection,
section header classification.

pdfplumber provides: character-perfect text from the PDF text layer.

This combines both: take Docling's document model, replace every text
element's content with pdfplumber's character-level extraction at the
same bounding box coordinates. Best of both worlds.
"""

import json
import re
import pdfplumber
from pathlib import Path
from collections import defaultdict
from docling.document_converter import DocumentConverter


LIGATURES = {
    '\ufb01': 'fi', '\ufb02': 'fl', '\ufb00': 'ff',
    '\ufb03': 'ffi', '\ufb04': 'ffl',
}


def run_hybrid_v2(pdf_path, standard="ASCE 7-22", chapter=26):
    """Run hybrid v2 pipeline."""
    pdf_path = Path(pdf_path)
    std_slug = standard.replace(" ", "")

    # Step 1: Docling
    print("  [1/3] Docling: document structure...")
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document
    docling = doc.export_to_dict()
    markdown = doc.export_to_markdown()

    # Step 2: pdfplumber characters
    print("  [2/3] pdfplumber: character extraction...")
    page_chars = _extract_chars(pdf_path)

    # Step 3: Patch and build elements
    print("  [3/3] Building elements with patched text...")
    pages_info = docling.get("pages", {})
    elements = []

    # Process texts
    current_section = str(chapter)
    counters = defaultdict(int)
    id_set = set()

    def make_id(section, prefix):
        counters[(section, prefix)] += 1
        eid = f"{std_slug}-{section}-{prefix}{counters[(section, prefix)]}"
        while eid in id_set:
            counters[(section, prefix)] += 1
            eid = f"{std_slug}-{section}-{prefix}{counters[(section, prefix)]}"
        id_set.add(eid)
        return eid

    for item in docling.get("texts", []):
        label = item.get("label", "text")
        if label in ("page_header", "page_footer"):
            continue

        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        bbox = prov.get("bbox", {})

        # Patch text with pdfplumber characters — use Docling text as fallback
        page_h = pages_info.get(str(page_no), {}).get("size", {}).get("height", 792)
        docling_text = (item.get("text", "") or "").strip()
        patched = _get_text_at_bbox(page_chars, page_no, bbox, page_h)

        # Use pdfplumber text only if it's at least 80% as long as Docling's
        # Otherwise Docling's text is more complete (bbox was too tight for pdfplumber)
        if patched and len(patched) > 3:
            if len(patched) >= len(docling_text) * 0.8:
                text = patched
            else:
                # pdfplumber truncated — use Docling but fix its ligatures
                text = docling_text
                for lig, rep in LIGATURES.items():
                    text = text.replace(lig, rep)
        else:
            text = docling_text
            for lig, rep in LIGATURES.items():
                text = text.replace(lig, rep)

        # Fix hyphens and spacing
        text = re.sub(r'(\w)- (\w)', r'\1\2', text)
        text = re.sub(r'  +', ' ', text).strip()

        if not text or len(text) < 3:
            continue

        # Classify
        elem_type, data = _classify_and_build(text, label, current_section, page_no)

        # Track sections
        if label == "section_header" or (elem_type == "heading"):
            sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)*)', text)
            if sec_match:
                current_section = sec_match.group(1)

        section = current_section
        if elem_type == "heading":
            sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)*)', text)
            if sec_match:
                section = sec_match.group(1)

        eid = make_id(section, _prefix_for_type(elem_type))
        elements.append({
            "id": eid,
            "type": elem_type if elem_type != "heading" else "provision",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section,
                       "citation": data.get("citation", f"Section {section}"),
                       "page": page_no},
            "title": text[:200],
            "description": "",
            "data": data["data"],
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending",
                        "qc_notes": data.get("notes", None)}
        })

    # Process tables (from Docling — it's better at table structure)
    for item in docling.get("tables", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        grid = item.get("data", {}).get("table_cells", [])
        if not grid:
            continue

        columns, rows = _parse_table(grid)
        caption = _find_caption(docling, page_no, "Table")
        table_match = re.search(r'Table\s+(\d+\.\d+-\d+)', caption)
        citation = f"Table {table_match.group(1)}" if table_match else ""
        section = table_match.group(1).rsplit("-", 1)[0] if table_match else current_section

        eid = make_id(section, f"T{citation.replace('Table ','').replace('.','-')}" if citation else "T")
        elements.append({
            "id": eid, "type": "table",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": citation, "page": page_no},
            "title": caption[:200] or f"Table in Section {section}",
            "description": "",
            "data": {"columns": columns, "rows": rows},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "docling_table"}
        })

    # Process figures
    for item in docling.get("pictures", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        caption = _find_caption(docling, page_no, "Figure")
        fig_match = re.search(r'Figure\s+(\d+\.\d+-\d+)', caption)
        citation = f"Figure {fig_match.group(1)}" if fig_match else ""
        section = fig_match.group(1).rsplit("-", 1)[0] if fig_match else current_section

        eid = make_id(section, "F")
        elements.append({
            "id": eid, "type": "figure", "classification": "linked",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": citation, "page": page_no},
            "title": caption[:200] or f"Figure on page {page_no}",
            "description": caption or "",
            "data": {"figure_type": "other", "description": caption or "Figure",
                     "source_pdf_page": page_no},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "docling_figure"}
        })

    # Post-process: extract equations from text
    print("  [+] Extracting equations...")
    elements = _extract_equations(elements, std_slug, standard, chapter, id_set)

    # Authoritative figure/table detection from bold labels
    print("  [+] Scanning bold labels for tables and figures...")
    elements = _detect_from_bold_labels(elements, pdf_path, docling, std_slug, standard, chapter, id_set, pages_info)

    print(f"  Done: {len(elements)} elements")
    return elements, markdown


def _extract_chars(pdf_path):
    """Extract all characters per page with ligature replacement."""
    page_chars = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            chars = []
            for c in page.chars:
                if c["x0"] < 40:
                    continue
                if c["top"] > 770:
                    continue
                text = LIGATURES.get(c["text"], c["text"])
                chars.append({**c, "text": text})
            page_chars[i + 1] = chars
    return page_chars


def _get_text_at_bbox(page_chars, page_no, bbox, page_h):
    """Get pdfplumber text within a Docling bounding box."""
    chars = page_chars.get(page_no, [])
    if not chars or not bbox:
        return None

    # Convert Docling coords (bottom-left origin) to pdfplumber (top-left)
    # Wide margin to catch characters at bbox edges
    margin = 8
    pdf_left = bbox.get("l", 0) - margin
    pdf_right = bbox.get("r", 0) + margin
    pdf_top = page_h - bbox.get("t", 0) - margin
    pdf_bottom = page_h - bbox.get("b", 0) + margin

    matching = [c for c in chars
                if c["x0"] >= pdf_left and c["x1"] <= pdf_right
                and c["top"] >= pdf_top and c["bottom"] <= pdf_bottom]

    if not matching:
        return None

    matching.sort(key=lambda c: (round(c["top"]), c["x0"]))

    lines = []
    current = [matching[0]]
    for c in matching[1:]:
        if abs(c["top"] - current[0]["top"]) < 2:
            current.append(c)
        else:
            current.sort(key=lambda c: c["x0"])
            lines.append(current)
            current = [c]
    current.sort(key=lambda c: c["x0"])
    lines.append(current)

    result = []
    for line in lines:
        text = ""
        prev_x1 = None
        for c in line:
            if prev_x1 is not None and c["x0"] - prev_x1 > 1.5:
                text += " "
            text += c["text"]
            prev_x1 = c["x1"]
        result.append(text)

    return " ".join(result)


def _classify_and_build(text, docling_label, current_section, page_no):
    """Classify text and build element data."""
    # Docling already classified section headers
    if docling_label in ("section_header", "title"):
        return "heading", {
            "data": {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []},
            "notes": "heading",
        }

    # Definition: ALL-CAPS TERM: text
    def_match = re.match(r'^([A-Z][A-Z0-9 ,./()]+?)\s*:\s*(.+)', text, re.DOTALL)
    if def_match:
        term = def_match.group(1).strip()
        if 2 < len(term) < 80 and term == term.upper() and any(c.isalpha() for c in term):
            return "definition", {
                "data": {"term": term, "definition": def_match.group(2).strip()},
            }

    # Provision: contains regulatory language
    if any(w in text.lower() for w in ["shall ", "shall not", "is permitted", "must be", "are required"]):
        return "provision", {
            "data": {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []},
        }

    # Default: text block stored as provision
    return "text_block", {
        "data": {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []},
        "notes": "unclassified",
    }


def _prefix_for_type(t):
    return {"heading": "H", "definition": "D", "formula": "E",
            "provision": "P", "text_block": "T", "table": "T", "figure": "F"}.get(t, "X")


def _find_caption(docling, page_no, prefix):
    """Find a caption like 'Table 26.X-Y...' or 'Figure 26.X-Y...' near a page."""
    for item in docling.get("texts", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        if prov.get("page_no") == page_no:
            text = item.get("text", "")
            if re.search(rf'{prefix}\s+\d+\.\d+-\d+', text):
                return text
    return ""


def _parse_table(table_cells):
    """Parse Docling table cells into columns + rows."""
    if not table_cells:
        return [], []
    max_row = max((c.get("end_row_offset_idx", 1) for c in table_cells), default=1)
    max_col = max((c.get("end_col_offset_idx", 1) for c in table_cells), default=1)
    grid = {}
    for c in table_cells:
        r = c.get("start_row_offset_idx", 0)
        col = c.get("start_col_offset_idx", 0)
        grid[(r, col)] = c.get("text", "")
    columns = [{"name": grid.get((0, c), f"col_{c}"), "unit": None} for c in range(max_col)]
    rows = []
    for r in range(1, max_row):
        row = {}
        for c in range(max_col):
            name = columns[c]["name"] if c < len(columns) else f"col_{c}"
            val = grid.get((r, c), "")
            try:
                val = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                pass
            row[name] = val
        rows.append(row)
    return columns, rows


def _detect_from_bold_labels(elements, pdf_path, docling, std_slug, standard, chapter, id_set, pages_info):
    """Scan PDF for bold 'Table X.Y-Z' and 'Figure X.Y-Z' labels.

    These bold labels are the authoritative markers for what tables and figures
    exist. If a bold label exists but no corresponding element was extracted,
    create one. This catches items that Docling's structural detection missed.
    """
    import fitz

    doc = fitz.open(str(pdf_path))
    bold_tables = {}   # number -> {caption, page}
    bold_figures = {}   # number -> {caption, page}

    for page_num in range(len(doc)):
        page = doc[page_num]
        text_dict = page.get_text("dict")

        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                line_text = ""
                has_bold = False
                for span in line.get("spans", []):
                    line_text += span["text"]
                    if span["flags"] & 16 or span["font"].endswith(".B") or span["font"].endswith(".BI"):
                        has_bold = True

                if not has_bold:
                    continue

                # Bold table label
                tm = re.search(r'Table\s+([\d.]+-\d+)', line_text)
                if tm and tm.group(1) not in bold_tables:
                    bold_tables[tm.group(1)] = {
                        "caption": line_text.strip(),
                        "page": page_num + 1,
                    }

                # Bold figure label
                fm = re.search(r'Figure\s+([\d.]+-\d+[A-D]?)', line_text)
                if fm and fm.group(1) not in bold_figures:
                    bold_figures[fm.group(1)] = {
                        "caption": line_text.strip(),
                        "page": page_num + 1,
                    }

    doc.close()

    # Check which tables/figures we already have
    existing_tables = set()
    existing_figures = set()
    for e in elements:
        cit = e.get("source", {}).get("citation", "")
        if e["type"] == "table":
            m = re.search(r'([\d.]+-\d+)', cit)
            if m:
                existing_tables.add(m.group(1))
        if e["type"] == "figure":
            m = re.search(r'([\d.]+-\d+[A-D]?)', cit)
            if m:
                existing_figures.add(m.group(1))

    # Add missing tables
    counters = defaultdict(int)
    for num, info in sorted(bold_tables.items()):
        if num in existing_tables:
            continue
        section = num.rsplit("-", 1)[0]
        counters[("table", section)] += 1
        eid = f"{std_slug}-{section}-T{num.replace('.', '-')}"
        if eid in id_set:
            eid = f"{eid}-{counters[('table', section)]}"
        id_set.add(eid)

        # Try to get table data from Docling for this page
        columns, rows = [], []
        for item in docling.get("tables", []):
            prov = item.get("prov", [{}])[0] if item.get("prov") else {}
            if prov.get("page_no") == info["page"]:
                grid = item.get("data", {}).get("table_cells", [])
                if grid:
                    columns, rows = _parse_table(grid)
                    break

        elements.append({
            "id": eid, "type": "table",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": f"Table {num}", "page": info["page"]},
            "title": info["caption"][:200],
            "description": "",
            "data": {"columns": columns, "rows": rows},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "bold_label"}
        })

    # Add missing figures
    for num, info in sorted(bold_figures.items()):
        if num in existing_figures:
            continue
        section = num.rsplit("-", 1)[0]
        # Strip letter suffix for section (26.5-1A -> 26.5)
        section = re.sub(r'-\d+[A-D]?$', '', num).rsplit('-', 1)[0] if '-' in num else section
        counters[("figure", section)] += 1
        eid = f"{std_slug}-{section}-F{num.replace('.', '-')}"
        if eid in id_set:
            eid = f"{eid}-{counters[('figure', section)]}"
        id_set.add(eid)

        elements.append({
            "id": eid, "type": "figure",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": f"Figure {num}", "page": info["page"]},
            "title": info["caption"][:200],
            "description": info["caption"],
            "data": {"figure_type": "other", "description": info["caption"],
                     "source_pdf_page": info["page"]},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "bold_label"}
        })

    added_t = len(bold_tables) - len(existing_tables & set(bold_tables.keys()))
    added_f = len(bold_figures) - len(existing_figures & set(bold_figures.keys()))
    if added_t or added_f:
        print(f"    Added {added_t} tables, {added_f} figures from bold labels")

    return elements


def _extract_equations(elements, std_slug, standard, chapter, id_set):
    """Scan element text for equation numbers (26.X-Y) and create formula elements."""
    new_elements = []
    eq_counter = defaultdict(int)

    for e in elements:
        text = e.get("data", {}).get("rule", "")
        eq_matches = list(re.finditer(r'\((\d+\.\d+-\d+[a-z]?(?:\.SI)?)\)', text))

        for m in eq_matches:
            eq_num = m.group(1)
            if not eq_num.startswith("26."):
                continue

            before = text[:m.start()].rstrip()
            boundaries = [before.rfind(". "), before.rfind(";")]
            boundary = max((b for b in boundaries if b >= 0), default=-1) + 1
            expr_start = max(boundary, m.start() - 200)
            expression = text[expr_start:m.end()].strip()

            if len(expression) < 5:
                continue

            sec = eq_num.rsplit("-", 1)[0]
            eq_counter[sec] += 1
            eid = f"{std_slug}-{sec}-E{eq_num.replace('.', '-')}"
            if eid in id_set:
                eid = f"{eid}-{eq_counter[sec]}"
            id_set.add(eid)

            new_elements.append({
                "id": eid, "type": "formula",
                "source": {"standard": standard, "chapter": chapter,
                           "section": sec, "citation": f"Eq. ({eq_num})", "page": e["source"]["page"]},
                "title": f"Equation ({eq_num})",
                "description": "",
                "data": {"expression": expression, "parameters": {}},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "from_text"}
            })

        new_elements.append(e)

    return new_elements
