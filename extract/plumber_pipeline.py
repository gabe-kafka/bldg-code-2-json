"""
pdfplumber-based extraction pipeline.

Uses pdfplumber's character-level PDF text layer for perfect text fidelity,
with font metadata for semantic classification (heading, definition, provision,
equation). Cross-references with Docling for table structure and figure detection.

Key advantage: reads the PDF text layer directly — no OCR, no vision model.
Characters are exactly what the PDF contains.
"""

import json
import re
import pdfplumber
import fitz
from pathlib import Path
from collections import defaultdict


def run_plumber(pdf_path, standard="ASCE 7-22", chapter=26):
    """Run pdfplumber-based extraction pipeline.

    Returns list of element dicts matching the schema.
    """
    pdf_path = Path(pdf_path)
    std_slug = standard.replace(" ", "")

    print("  [1/4] Extracting characters with pdfplumber...")
    pages_data = _extract_all_pages(pdf_path)

    print("  [2/4] Detecting columns and grouping text blocks...")
    blocks = _group_into_blocks(pages_data)

    print("  [3/4] Classifying blocks with font metadata...")
    classified = _classify_blocks(blocks)

    print("  [4/4] Building elements...")
    elements = _build_elements(classified, std_slug, standard, chapter)

    # Get tables from Docling (it's better at table structure)
    print("  [+] Enriching with Docling tables...")
    tables = _get_docling_tables(pdf_path, std_slug, standard, chapter)
    elements.extend(tables)

    # Get figures from Docling
    print("  [+] Enriching with Docling figures...")
    figures = _get_docling_figures(pdf_path, std_slug, standard, chapter)
    elements.extend(figures)

    # Fix ligatures (fi, fl, ff, ffi, ffl broken by PDF encoding)
    print("  [+] Fixing ligatures...")
    _fix_ligatures(elements)

    # Split blocks that contain multiple sections
    print("  [+] Splitting multi-section blocks...")
    elements = _split_multi_section_blocks(elements, std_slug, standard, chapter)

    # Split embedded definitions out of provision blocks
    print("  [+] Splitting embedded definitions...")
    elements = _split_embedded_definitions(elements, std_slug, standard, chapter)

    print(f"  Done: {len(elements)} elements")
    return elements


def _extract_all_pages(pdf_path):
    """Extract all characters with position and font data from every page."""
    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            # Ligature map for character-level fix
            _LIG = {'\ufb01':'fi','\ufb02':'fl','\ufb00':'ff','\ufb03':'ffi','\ufb04':'ffl',
                     'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}

            chars = []
            for c in page.chars:
                # Skip watermark/margin text (x0 < 40) and footer area
                if c["x0"] < 40:
                    continue
                if c["top"] > 770:
                    continue
                # Skip ligature-only font glyphs (they duplicate content)
                font = c.get("fontname", "")
                if "+fb" in font or "+fc" in font:
                    continue
                chars.append({
                    "text": _LIG.get(c["text"], c["text"]),
                    "x0": c["x0"], "x1": c["x1"],
                    "top": c["top"], "bottom": c["bottom"],
                    "font": c.get("fontname", ""),
                    "size": c.get("size", 0),
                })
            pages.append({
                "page_num": i + 1,
                "width": float(page.width),
                "height": float(page.height),
                "chars": chars,
            })
    return pages


def _detect_columns(chars, page_width):
    """Detect if a page has two columns by finding a vertical gap."""
    if not chars:
        return [(0, page_width)]

    # Histogram of x-positions
    bins = [0] * int(page_width + 1)
    for c in chars:
        x = int(c["x0"])
        if 0 <= x < len(bins):
            bins[x] += 1

    # Look for a gap in the middle third
    mid_start = int(page_width * 0.35)
    mid_end = int(page_width * 0.65)

    # Smooth and find minimum
    window = 10
    min_density = float("inf")
    min_x = int(page_width / 2)

    for x in range(mid_start, mid_end - window):
        density = sum(bins[x:x + window])
        if density < min_density:
            min_density = density
            min_x = x + window // 2

    # If the gap is significantly empty compared to the sides
    left_density = sum(bins[50:mid_start]) / max(1, mid_start - 50)
    right_density = sum(bins[mid_end:int(page_width) - 50]) / max(1, int(page_width) - 50 - mid_end)
    gap_density = min_density / window

    if left_density > 2 and right_density > 2 and gap_density < max(left_density, right_density) * 0.1:
        return [(0, min_x - 5), (min_x + 5, page_width)]

    return [(0, page_width)]


def _group_into_blocks(pages_data):
    """Group characters into text blocks respecting column layout."""
    all_blocks = []

    for page in pages_data:
        chars = page["chars"]
        if not chars:
            continue

        columns = _detect_columns(chars, page["width"])

        for col_left, col_right in columns:
            # Filter chars in this column
            col_chars = [c for c in chars if c["x0"] >= col_left - 5 and c["x1"] <= col_right + 5]
            if not col_chars:
                continue

            # Group chars into lines by y-position (within 1.5pt tolerance)
            col_chars.sort(key=lambda c: (c["top"], c["x0"]))
            lines = []
            current_line = [col_chars[0]]

            for c in col_chars[1:]:
                if abs(c["top"] - current_line[0]["top"]) < 1.5:
                    current_line.append(c)
                else:
                    current_line.sort(key=lambda c: c["x0"])
                    lines.append(current_line)
                    current_line = [c]
            current_line.sort(key=lambda c: c["x0"])
            lines.append(current_line)

            # Group lines into blocks (separated by vertical gaps > 3pt)
            blocks = []
            current_block_lines = [lines[0]]

            for line in lines[1:]:
                prev_bottom = max(c["bottom"] for c in current_block_lines[-1])
                curr_top = min(c["top"] for c in line)
                gap = curr_top - prev_bottom

                if gap > 3:
                    blocks.append(current_block_lines)
                    current_block_lines = [line]
                else:
                    current_block_lines.append(line)
            blocks.append(current_block_lines)

            # Convert blocks to structured data
            for block_lines in blocks:
                text_lines = []
                font_info = []

                for line in block_lines:
                    line_text = ""
                    prev_x1 = None
                    for c in line:
                        if prev_x1 is not None and c["x0"] - prev_x1 > 1.5:
                            line_text += " "
                        line_text += c["text"]
                        prev_x1 = c["x1"]

                    text_lines.append(line_text)

                    # Collect font info for this line
                    fonts = set(c["font"] for c in line)
                    sizes = [c["size"] for c in line]
                    avg_size = sum(sizes) / len(sizes) if sizes else 0
                    font_info.append({
                        "fonts": fonts,
                        "avg_size": avg_size,
                        "bold": any(c.get("font", "").endswith(".B") or
                                   c.get("font", "").endswith(".BI") or
                                   c.get("font", "").endswith(",Bold") or
                                   "Bold" in c.get("font", "")
                                   for c in line),
                    })

                full_text = " ".join(text_lines)
                x0 = min(c["x0"] for line in block_lines for c in line)
                y0 = min(c["top"] for line in block_lines for c in line)
                x1 = max(c["x1"] for line in block_lines for c in line)
                y1 = max(c["bottom"] for line in block_lines for c in line)

                # Determine if first line is bold
                first_bold = font_info[0]["bold"] if font_info else False
                first_size = font_info[0]["avg_size"] if font_info else 9.5
                all_bold = all(fi["bold"] for fi in font_info)

                all_blocks.append({
                    "page": page["page_num"],
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "text": full_text.strip(),
                    "lines": text_lines,
                    "first_bold": first_bold,
                    "all_bold": all_bold,
                    "first_size": round(first_size, 1),
                    "column": 0 if col_left < page["width"] / 2 else 1,
                })

    return all_blocks


def _classify_blocks(blocks):
    """Classify each block by type using font and content heuristics."""
    classified = []

    for b in blocks:
        text = b["text"]
        if not text or len(text) < 2:
            continue

        # Skip page headers/footers/watermarks
        if b["y0"] < 30 or b["y1"] > 770:
            if len(text) < 150:
                continue
        # Skip rotated watermark text (very narrow x range, tall y range)
        if (b["x1"] - b["x0"]) < 15 and (b["y1"] - b["y0"]) > 100:
            continue
        # Skip "Downloaded from" / "Minimum Design Loads" boilerplate
        if text.startswith("Downloaded from") or text.startswith("Minimum Design"):
            continue
        if "ascelibrary.org" in text:
            continue

        # Heading: starts with section number pattern, or bold + short + all-caps
        if re.match(r'^\d+\.\d+(\.\d+)*\s', text) and len(text) < 150:
            b["type"] = "heading"
            classified.append(b)
            continue
        if b["first_bold"] and len(text) < 120:
            if text.isupper() and len(text) < 80:
                b["type"] = "heading"
                classified.append(b)
                continue
            if text.startswith("CHAPTER"):
                b["type"] = "heading"
                classified.append(b)
                continue

        # Definition: ALL-CAPS TERM followed by colon then definition text
        # Don't require bold — pdfplumber may miss it due to obfuscated font names
        def_match = re.match(r'^([A-Z][A-Z0-9 ,./()]+?)\s*:\s*(.+)', text, re.DOTALL)
        if def_match:
            term_candidate = def_match.group(1).strip()
            if (2 < len(term_candidate) < 80 and
                term_candidate == term_candidate.upper() and
                any(c.isalpha() for c in term_candidate)):
                b["type"] = "definition"
                b["term"] = term_candidate
                b["definition_text"] = def_match.group(2).strip()
                classified.append(b)
                continue

        # Equation: short block with math patterns
        eq_patterns = [
            r'[A-Za-z_]+\s*[=<>≤≥]\s*[\d.]',
            r'\d+\.\d+\s*[×*]\s*',
            r'Eq\.\s*\(',
        ]
        if len(text) < 150 and any(re.search(p, text) for p in eq_patterns):
            # Verify it's not just a provision mentioning a number
            if not any(w in text.lower() for w in ["shall", "permitted", "required"]):
                b["type"] = "formula"
                classified.append(b)
                continue

        # Provision: contains regulatory language
        provision_markers = ["shall ", "shall not", "is permitted", "must be",
                           "are required", "is required", "are permitted"]
        if any(m in text.lower() for m in provision_markers):
            b["type"] = "provision"
            classified.append(b)
            continue

        # Default: text block
        b["type"] = "text_block"
        classified.append(b)

    return classified


def _build_elements(classified, std_slug, standard, chapter):
    """Convert classified blocks to schema elements."""
    elements = []
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

    for b in classified:
        text = b["text"]
        page = b["page"]

        # Update current section from headings
        if b["type"] == "heading":
            sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)*)', text)
            if sec_match:
                current_section = sec_match.group(1)

        section = current_section

        if b["type"] == "heading":
            sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)*)\s*(.*)', text)
            title_text = sec_match.group(2).strip() if sec_match else text
            if sec_match:
                section = sec_match.group(1)
            elements.append({
                "id": make_id(section, "H"),
                "type": "provision",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "citation": f"Section {section}", "page": page},
                "title": text[:200],
                "description": "",
                "data": {"rule": text, "conditions": [], "then": title_text or "", "else": None, "exceptions": []},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "heading"}
            })

        elif b["type"] == "definition":
            elements.append({
                "id": make_id(section, "D"),
                "type": "definition",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "citation": f"Section {section}", "page": page},
                "title": f"Definition: {b['term']}",
                "description": "",
                "data": {"term": b["term"], "definition": b["definition_text"]},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
            })

        elif b["type"] == "formula":
            eq_match = re.search(r'Eq\.\s*\(([^)]+)\)', text)
            citation = eq_match.group(0) if eq_match else f"Section {section}"
            elements.append({
                "id": make_id(section, "E"),
                "type": "formula",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "citation": citation, "page": page},
                "title": f"Equation in Section {section}",
                "description": "",
                "data": {"expression": text, "parameters": {}},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
            })

        elif b["type"] == "provision":
            elements.append({
                "id": make_id(section, "P"),
                "type": "provision",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "citation": f"Section {section}", "page": page},
                "title": f"Provision in Section {section}",
                "description": "",
                "data": {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None}
            })

        else:  # text_block
            elements.append({
                "id": make_id(section, "T"),
                "type": "provision",
                "source": {"standard": standard, "chapter": chapter,
                           "section": section, "citation": f"Section {section}", "page": page},
                "title": f"Text in Section {section}",
                "description": "",
                "data": {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "unclassified"}
            })

    return elements


def _fix_ligatures(elements):
    """Fix broken ligatures in all text fields.

    PDFs encode fi/fl/ff/ffi/ffl as single Unicode ligature characters.
    pdfplumber extracts the ligature glyph but downstream text often shows
    them as 'ﬁ', 'ﬂ', etc. or as broken sequences like 'speci ed' (missing 'fi').
    This normalizes all ligatures to their ASCII equivalents.
    """
    LIGATURES = {
        '\ufb01': 'fi',   # ﬁ
        '\ufb02': 'fl',   # ﬂ
        '\ufb00': 'ff',   # ﬀ
        '\ufb03': 'ffi',  # ﬃ
        '\ufb04': 'ffl',  # ﬄ
        'ﬁ': 'fi',
        'ﬂ': 'fl',
        'ﬀ': 'ff',
        'ﬃ': 'ffi',
        'ﬄ': 'ffl',
    }

    # Also fix cases where ligature was extracted but left a gap:
    # "speci ed" -> "specified", "de ned" -> "defined", etc.
    BROKEN_LIGATURE_FIXES = [
        (r'speci\s+ed', 'specified'),
        (r'de\s*ﬁ\s*ned', 'defined'),
        (r'de\s+ned', 'defined'),
        (r'classi\s+ed', 'classified'),
        (r'identi\s+ed', 'identified'),
        (r'modi\s+ed', 'modified'),
        (r'certi\s+ed', 'certified'),
        (r'simpli\s+ed', 'simplified'),
        (r'quali\s+ed', 'qualified'),
        (r'satis\s+ed', 'satisfied'),
        (r'con\s*ﬁ\s*guration', 'configuration'),
        (r'con\s+guration', 'configuration'),
        (r'coef\s*ﬁ\s*cient', 'coefficient'),
        (r'coef\s+cient', 'coefficient'),
        (r'signi\s+cant', 'significant'),
        (r'ef\s+cient', 'efficient'),
    ]

    def fix_text(text):
        if not text:
            return text
        # Replace Unicode ligature characters
        for lig, replacement in LIGATURES.items():
            text = text.replace(lig, replacement)
        # Fix broken ligature gaps
        for pattern, replacement in BROKEN_LIGATURE_FIXES:
            text = re.sub(pattern, replacement, text)
        # Clean orphan "fi " or " fi " fragments from ligature extraction
        text = re.sub(r'\bfi\s+(?=[a-z])', 'fi', text)  # "fi procedures" -> "fiprocedures" — no
        # Better: remove standalone "fi" that appears before a space + lowercase
        text = re.sub(r'\s+fi\s+(?=procedures|ned|gure|eld|lter|nish|nal|rst|re|ll|nd|le|x)', '', text)
        # Fix line-break hyphens: "da- tabase" -> "database"
        text = re.sub(r'(\w)- (\w)', r'\1\2', text)
        # Clean up double spaces
        text = re.sub(r'  +', ' ', text)
        return text

    for e in elements:
        # Fix all text fields
        if 'title' in e:
            e['title'] = fix_text(e['title'])
        if 'description' in e:
            e['description'] = fix_text(e['description'])
        data = e.get('data', {})
        for key in ('rule', 'expression', 'term', 'definition', 'description'):
            if key in data:
                data[key] = fix_text(data[key])
        # Fix table cell values
        if 'rows' in data:
            for row in data['rows']:
                for k, v in row.items():
                    if isinstance(v, str):
                        row[k] = fix_text(v)
        if 'columns' in data:
            for col in data['columns']:
                if 'name' in col:
                    col['name'] = fix_text(col['name'])


def _split_multi_section_blocks(elements, std_slug, standard, chapter):
    """Split text blocks that contain multiple section headings.

    e.g. a block with "26.5.1 Basic Wind Speed ... 26.5.2 Special Wind Regions ..."
    becomes two separate elements.
    """
    new_elements = []
    id_set = {e["id"] for e in elements}
    counters = defaultdict(int)

    section_pattern = re.compile(r'(26\.\d+(?:\.\d+)*)\s+([A-Z][A-Za-z, -]+)')

    for e in elements:
        text = e.get("data", {}).get("rule", "")
        if e["type"] not in ("provision",) or len(text) < 80:
            new_elements.append(e)
            continue

        # Find all section number patterns in the text
        matches = list(section_pattern.finditer(text))
        if len(matches) < 2:
            new_elements.append(e)
            continue

        # Split at each section boundary
        for i, m in enumerate(matches):
            sec_num = m.group(1)
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk = text[start:end].strip()

            if len(chunk) < 10:
                continue

            counters[sec_num] += 1
            eid = f"{std_slug}-{sec_num}-S{counters[sec_num]}"
            while eid in id_set:
                counters[sec_num] += 1
                eid = f"{std_slug}-{sec_num}-S{counters[sec_num]}"
            id_set.add(eid)

            # Classify: if it starts with the section number, it's a heading+provision
            is_provision = any(w in chunk.lower() for w in ["shall ", "shall not", "is permitted", "must be"])

            new_elements.append({
                "id": eid,
                "type": "provision",
                "source": {"standard": e["source"]["standard"], "chapter": chapter,
                           "section": sec_num, "citation": f"Section {sec_num}",
                           "page": e["source"]["page"]},
                "title": chunk[:120],
                "description": "",
                "data": {"rule": chunk, "conditions": [], "then": "", "else": None, "exceptions": []},
                "cross_references": [],
                "metadata": {"extracted_by": "auto", "qc_status": "pending",
                            "qc_notes": "split_section"}
            })

    return new_elements


def _split_embedded_definitions(elements, std_slug, standard, chapter):
    """Find ALL-CAPS TERM: definition patterns inside provision/text blocks and split them out."""
    new_elements = []
    id_set = {e["id"] for e in elements}
    def_counter = defaultdict(int)

    # Pattern: ALL-CAPS TERM (possibly with comma, parens) followed by colon
    def_pattern = re.compile(
        r'([A-Z][A-Z0-9 ,/()]+?)\s*:\s*'
    )

    for e in elements:
        if e["type"] not in ("provision",) or "heading" in (e["metadata"].get("qc_notes") or ""):
            new_elements.append(e)
            continue

        text = e["data"].get("rule", "")
        # Find all definition-like patterns in the text
        matches = list(def_pattern.finditer(text))
        found_defs = []

        for m in matches:
            term = m.group(1).strip()
            # Must be all-caps, >2 chars, <80 chars, has letters
            if (term != term.upper() or len(term) < 3 or len(term) > 80
                    or not any(c.isalpha() for c in term)):
                continue
            # Skip common false positives
            if term in ("EXCEPTION", "NOTE", "USER NOTE", "WIND", "MWFRS", "C&C"):
                continue
            # Get the definition text (from colon to next all-caps term or end)
            start = m.end()
            # Find next definition or end of text
            next_match = None
            for nm in matches:
                if nm.start() > start:
                    nt = nm.group(1).strip()
                    if nt == nt.upper() and len(nt) > 2 and any(c.isalpha() for c in nt):
                        next_match = nm
                        break
            end = next_match.start() if next_match else len(text)
            def_text = text[start:end].strip()

            if len(def_text) > 10:  # meaningful definition
                found_defs.append({"term": term, "definition": def_text})

        if found_defs:
            section = e["source"]["section"]
            for fd in found_defs:
                def_counter[section] += 1
                eid = f"{std_slug}-{section}-D{def_counter[section]}"
                while eid in id_set:
                    def_counter[section] += 1
                    eid = f"{std_slug}-{section}-D{def_counter[section]}"
                id_set.add(eid)

                new_elements.append({
                    "id": eid,
                    "type": "definition",
                    "source": {**e["source"]},
                    "title": f"Definition: {fd['term']}",
                    "description": "",
                    "data": {"term": fd["term"], "definition": fd["definition"]},
                    "cross_references": [],
                    "metadata": {"extracted_by": "auto", "qc_status": "pending",
                                "qc_notes": "split_from_text"}
                })
            # Keep the original provision too (it may contain non-definition content)
            new_elements.append(e)
        else:
            new_elements.append(e)

    return new_elements


def _get_docling_tables(pdf_path, std_slug, standard, chapter):
    """Get table elements from Docling (better at table structure)."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    d = result.document.export_to_dict()

    elements = []
    counters = defaultdict(int)

    for item in d.get("tables", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)

        grid = item.get("data", {}).get("table_cells", [])
        if not grid:
            continue

        # Parse table
        columns, rows = _parse_docling_table(grid)
        if not columns:
            continue

        # Get caption from the markdown — Docling's item text is often empty for tables
        caption = item.get("text", "")

        # Also search nearby text elements for "Table 26.X-Y" pattern
        if not re.search(r'Table\s+\d+\.\d+-\d+', caption):
            # Check all text items for table references near this page
            for txt_item in d.get("texts", []):
                txt_prov = txt_item.get("prov", [{}])[0] if txt_item.get("prov") else {}
                if txt_prov.get("page_no") == page_no:
                    txt = txt_item.get("text", "")
                    tm = re.search(r'(Table\s+\d+\.\d+-\d+[^.]*)', txt)
                    if tm:
                        caption = tm.group(1)
                        break

        table_match = re.search(r'Table\s+(\d+\.\d+-\d+)', caption)
        citation = f"Table {table_match.group(1)}" if table_match else ""
        table_num = table_match.group(1) if table_match else ""
        section = table_num.rsplit("-", 1)[0] if table_num else str(chapter)

        counters[section] += 1
        if table_num:
            eid = f"{std_slug}-{section}-T{table_num.replace('.', '-')}"
        else:
            eid = f"{std_slug}-{section}-T{counters[section]}"

        elements.append({
            "id": eid,
            "type": "table",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": citation, "page": page_no},
            "title": caption[:200] if caption else f"Table in Section {section}",
            "description": "",
            "data": {"columns": columns, "rows": rows},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "from_docling"}
        })

    return elements


def _parse_docling_table(table_cells):
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


def _get_docling_figures(pdf_path, std_slug, standard, chapter):
    """Get figure elements from Docling."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    d = result.document.export_to_dict()

    elements = []
    counter = 0

    for item in d.get("pictures", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        caption = item.get("text", "")

        # Search nearby text for figure caption if not in item
        if not re.search(r'Figure\s+\d+\.\d+-\d+', caption):
            for txt_item in d.get("texts", []):
                txt_prov = txt_item.get("prov", [{}])[0] if txt_item.get("prov") else {}
                if txt_prov.get("page_no") == page_no:
                    txt = txt_item.get("text", "")
                    fm = re.search(r'(Figure\s+\d+\.\d+-\d+[^.]*)', txt)
                    if fm:
                        caption = fm.group(1)
                        break

        fig_match = re.search(r'Figure\s+(\d+\.\d+-\d+)', caption)
        citation = f"Figure {fig_match.group(1)}" if fig_match else ""
        fig_num = fig_match.group(1) if fig_match else ""
        section = fig_num.rsplit("-", 1)[0] if fig_num else str(chapter)

        counter += 1
        eid = f"{std_slug}-{section}-F{counter}"

        elements.append({
            "id": eid,
            "type": "figure",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": citation, "page": page_no},
            "title": caption[:200] if caption else f"Figure on page {page_no}",
            "description": caption or f"Figure on page {page_no}",
            "data": {"figure_type": "other", "description": caption or "Figure", "source_pdf_page": page_no},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "from_docling"}
        })

    return elements
