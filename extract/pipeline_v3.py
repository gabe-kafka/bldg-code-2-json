"""
Pipeline v3: Bold-label taxonomy extraction.

Every content block in a building code PDF is led by a bold label that
identifies what it is. This pipeline reads those labels from the PDF's
font metadata and classifies deterministically.

Docling provides: reading order, table structure, figure detection.
PyMuPDF provides: per-span font flags (bold detection).
pdfplumber provides: character-level text verification.
"""

import json
import re
import fitz
from pathlib import Path
from collections import defaultdict
from docling.document_converter import DocumentConverter


LIGATURES = {
    '\ufb01': 'fi', '\ufb02': 'fl', '\ufb00': 'ff',
    '\ufb03': 'ffi', '\ufb04': 'ffl',
}

# Page furniture patterns to skip
SKIP_PATTERNS = [
    re.compile(r'^\d{3}$'),                          # page numbers (261, 262, ...)
    re.compile(r'^STANDARD ASCE'),                    # footer
    re.compile(r'^Minimum Design Loads'),             # footer
    re.compile(r'^Downloaded from'),                  # watermark
]


def run_v3(pdf_path, standard="ASCE 7-22", chapter=26):
    """Run the v3 pipeline."""
    pdf_path = Path(pdf_path)
    std_slug = standard.replace(" ", "")

    # Step 1: Build bold label map from PyMuPDF
    print("  [1/4] PyMuPDF: scanning bold labels...")
    bold_map = _build_bold_map(pdf_path)

    # Step 2: Docling for text in reading order + tables + figures
    print("  [2/4] Docling: document structure...")
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document
    docling = doc.export_to_dict()
    markdown = doc.export_to_markdown()

    # Step 2.5: Build elastic classifier from this chapter's bold patterns
    print("  [2.5] Learning chapter-specific patterns...")
    from extract.elastic import build_chapter_classifier, report_patterns
    elastic_classify, discovered_patterns = build_chapter_classifier(bold_map, [])
    report_patterns(discovered_patterns)

    # Step 3: Build elements using elastic classification
    print("  [3/4] Building elements from elastic classification...")
    elements, id_set, counters, make_id = _build_elements(docling, bold_map, std_slug, standard, chapter, elastic_classify)

    # Step 4: Add tables, figures, references, equations
    print("  [4/4] Enriching with tables, figures, references, equations...")
    _add_tables(elements, docling, std_slug, standard, chapter, id_set, make_id)
    _add_figures(elements, bold_map, docling, std_slug, standard, chapter, id_set, make_id)
    _add_references(elements, bold_map, std_slug, standard, chapter, id_set, make_id)
    _add_equations(elements, std_slug, standard, chapter, id_set, make_id)

    # Tag sequence for stable sort
    for i, e in enumerate(elements):
        e["_seq"] = i

    # Cross-references
    print("  [+] Building cross-references...")
    _add_cross_references(elements)

    # Merge short fragments
    print("  [+] Merging short fragments...")
    elements = _merge_fragments(elements)

    # Page-level equation scan
    print("  [+] Page-level equation scan...")
    _add_equations_page_level(elements, std_slug, standard, chapter, id_set, make_id)

    # Re-sort by page so appended formulas are in natural reading position
    elements.sort(key=lambda e: (e["source"]["page"], e.get("_seq", 0)))

    # Associate body text with parent structural elements — AFTER all elements in order
    print("  [+] Associating body text with parent elements...")
    _associate_text_blocks(elements)

    # Parse provision conditions
    print("  [+] Parsing provision conditions...")
    _parse_conditions(elements)

    # Global symbols registry — replaces local _extract_parameters
    print("  [+] Building global symbols table...")
    from extract.symbols import build_symbols_table, resolve_parameters, save_symbols, load_symbols, merge_symbols
    new_symbols = build_symbols_table(elements)
    existing_symbols = load_symbols()
    all_symbols = merge_symbols(existing_symbols, new_symbols)
    params_filled = resolve_parameters(elements, all_symbols)
    save_symbols(all_symbols)
    print(f"    {len(all_symbols)} symbols, {params_filled}/{ sum(1 for e in elements if e['type']=='formula')} formulas have parameters")

    # Manifest
    print("  [+] Updating manifest...")
    from extract.manifest import build_manifest_entry, update_manifest
    entry = build_manifest_entry(elements, chapter, f"output/runs/final-ch{chapter}.json")
    entry["standard"] = standard
    update_manifest("output/manifest.json", entry)

    # Unresolved references
    print("  [+] Tracking unresolved references...")
    from extract.unresolved import find_unresolved, save_unresolved, print_unresolved
    unresolved = find_unresolved(elements)
    save_unresolved(unresolved)
    print_unresolved(unresolved)

    # Clean up internal fields
    for e in elements:
        e.pop("_seq", None)

    print(f"  Done: {len(elements)} elements")
    return elements, markdown


def _build_bold_map(pdf_path):
    """Scan PDF with PyMuPDF and build a map of bold text spans per page.

    Returns: {page_num: [{text, y, is_bold, font, size}]}
    """
    doc = fitz.open(str(pdf_path))
    bold_map = {}

    for page_num in range(len(doc)):
        page = doc[page_num]
        td = page.get_text("dict")
        spans = []

        for block in td.get("blocks", []):
            for line in block.get("lines", []):
                line_spans = line.get("spans", [])
                if not line_spans:
                    continue

                # Build line text and check if it starts with bold
                line_text = "".join(s["text"] for s in line_spans)
                first_bold = ""
                all_bold = True

                for s in line_spans:
                    is_bold = (s["font"].endswith(".B") or
                              s["font"].endswith(".BI") or
                              bool(s["flags"] & 16))
                    if is_bold:
                        first_bold += s["text"]
                    elif first_bold:
                        all_bold = False
                        break
                    else:
                        all_bold = False
                        break

                spans.append({
                    "text": line_text.strip(),
                    "bold_prefix": first_bold.strip(),
                    "all_bold": all_bold and bool(first_bold),
                    "y": round(line_spans[0]["bbox"][1]),
                    "page": page_num + 1,
                })

        bold_map[page_num + 1] = spans

    doc.close()
    return bold_map


def _classify_by_bold(text, bold_prefix, all_bold, docling_label):
    """Classify an element using its bold label pattern.

    Returns (type, notes) tuple.
    """
    # Skip page furniture
    for pat in SKIP_PATTERNS:
        if pat.match(text):
            return None, None

    # Docling section headers
    if docling_label in ("section_header", "title"):
        return "heading", None

    # User Note
    if bold_prefix.startswith("User Note"):
        return "user_note", None

    # Exception
    if bold_prefix.startswith("EXCEPTION"):
        return "exception", None

    # Definition: bold ALL-CAPS TERM followed by colon
    def_match = re.match(r'^([A-Z][A-Z0-9 ,./()]+?)\s*:\s*(.+)', text, re.DOTALL)
    if def_match:
        term = def_match.group(1).strip()
        if 2 < len(term) < 80 and term == term.upper() and any(c.isalpha() for c in term):
            return "definition", None

    # Sub-definition: "Surface Roughness X." or "Exposure X."
    if re.match(r'^(Surface Roughness|Exposure)\s+[A-Z]', bold_prefix):
        return "definition", "sub_definition"

    # Bold section number = heading
    if re.match(r'^\d+\.\d+', bold_prefix):
        return "heading", None

    # Bold ALL-CAPS single word/phrase = topic heading
    if all_bold and bold_prefix == bold_prefix.upper() and len(bold_prefix) < 60 and any(c.isalpha() for c in bold_prefix):
        # But not definitions (those have colons)
        if ":" not in text[:len(bold_prefix) + 5]:
            return "heading", "topic"

    # Reference: bold standard name
    if re.match(r'^(ASTM|ANSI|CAN/CSA|ICC)', bold_prefix):
        return "reference", None

    # Provision: regulatory language
    if any(w in text.lower() for w in ["shall ", "shall not", "is permitted", "must be", "are required"]):
        return "provision", None

    # Default: text_block
    return "text_block", None


def _fix_text(text):
    """Fix ligatures, hyphens, entities."""
    for lig, rep in LIGATURES.items():
        text = text.replace(lig, rep)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r'(\w)- (\w)', r'\1\2', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _build_elements(docling, bold_map, std_slug, standard, chapter, classify_fn=None):
    """Build elements from Docling text items + bold label classification."""
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

    pages_info = docling.get("pages", {})

    for item in docling.get("texts", []):
        label = item.get("label", "text")
        text = _fix_text(item.get("text", "") or "")
        if not text or len(text) < 2:
            continue
        if label in ("page_header", "page_footer"):
            continue

        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        bbox = prov.get("bbox", {})

        # Find matching bold span from PyMuPDF
        page_h = pages_info.get(str(page_no), {}).get("size", {}).get("height", 792)
        y_top = page_h - bbox.get("t", 0) if bbox else 0
        bold_prefix, all_bold = _find_bold_at(bold_map, page_no, y_top)

        # Classify
        # Use elastic classifier if available, fall back to hardcoded
        if classify_fn:
            elem_type, notes = classify_fn(text, bold_prefix, all_bold, label)
        else:
            elem_type, notes = _classify_by_bold(text, bold_prefix, all_bold, label)
        if elem_type is None:
            continue  # skip page furniture

        # Track section
        sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)*)', text)
        if elem_type == "heading" and sec_match:
            current_section = sec_match.group(1)
        elif sec_match and sec_match.group(1).startswith(str(chapter)):
            current_section = sec_match.group(1)

        section = current_section

        # Build type-specific data
        if elem_type == "definition":
            def_match = re.match(r'^([A-Z][A-Z0-9 ,./()]+?)\s*:\s*(.+)', text, re.DOTALL)
            if def_match:
                data = {"term": def_match.group(1).strip(), "definition": def_match.group(2).strip()}
            elif notes == "sub_definition":
                # Surface Roughness B. or Exposure C.
                sd_match = re.match(r'^(.+?\.)\s*(.*)', text, re.DOTALL)
                if sd_match:
                    data = {"term": sd_match.group(1).strip(), "definition": sd_match.group(2).strip()}
                else:
                    data = {"term": text[:50], "definition": text}
            else:
                data = {"term": text[:50], "definition": text}
        elif elem_type == "user_note":
            data = {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []}
        elif elem_type == "exception":
            data = {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []}
        elif elem_type == "reference":
            data = {"target": text}
        elif elem_type == "heading":
            data = {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []}
        elif elem_type == "provision":
            data = {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []}
        else:  # text_block
            data = {"rule": text, "conditions": [], "then": "", "else": None, "exceptions": []}

        prefix_map = {
            "heading": "H", "provision": "P", "definition": "D",
            "user_note": "N", "exception": "X", "reference": "R",
            "text_block": "T",
        }
        prefix = prefix_map.get(elem_type, "T")

        elements.append({
            "id": make_id(section, prefix),
            "type": elem_type,
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": f"Section {section}", "page": page_no},
            "title": text[:200],
            "description": "",
            "data": data,
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": notes}
        })

    return elements, id_set, counters, make_id


def _find_bold_at(bold_map, page_no, y_approx):
    """Find the bold prefix for a text item at a given y position."""
    spans = bold_map.get(page_no, [])
    best = None
    best_dist = float("inf")

    for s in spans:
        dist = abs(s["y"] - y_approx)
        if dist < best_dist:
            best_dist = dist
            best = s

    if best and best_dist < 10:
        return best.get("bold_prefix", ""), best.get("all_bold", False)
    return "", False


def _add_tables(elements, docling, std_slug, standard, chapter, id_set, make_id):

    for item in docling.get("tables", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        grid = item.get("data", {}).get("table_cells", [])
        if not grid:
            continue

        columns, rows = _parse_table(grid)

        # Find caption
        caption = ""
        for txt in docling.get("texts", []):
            tp = txt.get("prov", [{}])[0] if txt.get("prov") else {}
            if tp.get("page_no") == page_no:
                t = txt.get("text", "")
                if re.search(r'Table\s+\d+\.\d+-\d+', t):
                    caption = t
                    break

        tm = re.search(r'Table\s+(\d+\.\d+-\d+)', caption)
        citation = f"Table {tm.group(1)}" if tm else ""
        section = tm.group(1).rsplit("-", 1)[0] if tm else str(chapter)

        eid = make_id(section, f"T{citation.replace('Table ','').replace('.','-')}" if citation else "T")
        elements.append({
            "id": eid, "type": "table",
            "source": {"standard": standard, "chapter": chapter,
                       "section": section, "citation": citation, "page": page_no},
            "title": _fix_text(caption[:200]) if caption else "Table",
            "description": "",
            "data": {"columns": columns, "rows": rows},
            "cross_references": [],
            "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "docling"}
        })


def _add_figures(elements, bold_map, docling, std_slug, standard, chapter, id_set, make_id):

    # Collect all bold Figure labels
    bold_figures = {}
    for page_no, spans in bold_map.items():
        for s in spans:
            fm = re.search(r'Figure\s+([\d.]+-\d+[A-D]?)', s["text"])
            if fm and (s["bold_prefix"] or s["all_bold"]):
                num = fm.group(1)
                if num not in bold_figures:
                    bold_figures[num] = {"caption": _fix_text(s["text"]), "page": page_no}

    # Also get from Docling pictures
    for item in docling.get("pictures", []):
        prov = item.get("prov", [{}])[0] if item.get("prov") else {}
        page_no = prov.get("page_no", 1)
        # Find caption from nearby text
        for txt in docling.get("texts", []):
            tp = txt.get("prov", [{}])[0] if txt.get("prov") else {}
            if tp.get("page_no") == page_no:
                t = txt.get("text", "")
                fm = re.search(r'Figure\s+([\d.]+-\d+[A-D]?)', t)
                if fm and fm.group(1) not in bold_figures:
                    bold_figures[fm.group(1)] = {"caption": _fix_text(t), "page": page_no}

    for num in sorted(bold_figures.keys()):
        info = bold_figures[num]
        section = re.sub(r'-\d+[A-D]?$', '', num)
        eid = make_id(section, f"F{num.replace('.', '-')}")
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


def _add_references(elements, bold_map, std_slug, standard, chapter, id_set, make_id):

    # Check if references already exist
    existing_refs = {e["data"].get("target", "")[:20] for e in elements if e["type"] == "reference"}

    for page_no, spans in bold_map.items():
        for s in spans:
            bp = s.get("bold_prefix", "")
            if re.match(r'^(ASTM|ANSI|CAN/CSA|ICC)', bp):
                target = _fix_text(s["text"])
                if target[:20] not in existing_refs:
                    existing_refs.add(target[:20])
                    eid = make_id(str(chapter), "R")
                    elements.append({
                        "id": eid, "type": "reference",
                        "source": {"standard": standard, "chapter": chapter,
                                   "section": str(chapter), "citation": bp.split(",")[0].strip(),
                                   "page": page_no},
                        "title": bp.split(",")[0].strip(),
                        "description": "",
                        "data": {"target": target},
                        "cross_references": [],
                        "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "bold_label"}
                    })


def _add_equations(elements, std_slug, standard, chapter, id_set, make_id):
    found_eqs = set()

    new_elements = []
    for e in elements:
        text = e.get("data", {}).get("rule", "") or e.get("data", {}).get("definition", "")
        for m in re.finditer(r'\(((\d+)\.\d+-\d+[a-z]?(?:\.SI)?)\)', text):
            eq_num = m.group(1)
            ch = m.group(2)
            if ch != str(chapter):
                continue
            if eq_num in found_eqs:
                continue
            found_eqs.add(eq_num)

            before = text[:m.start()].rstrip()
            boundaries = [before.rfind(". "), before.rfind(";")]
            boundary = max((b for b in boundaries if b >= 0), default=-1) + 1
            expr_start = max(boundary, m.start() - 200)
            expression = text[expr_start:m.end()].strip()

            if len(expression) < 5:
                continue

            sec = eq_num.rsplit("-", 1)[0]
            eid = make_id(sec, f"E{eq_num.replace('.', '-')}")
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

    elements.extend(new_elements)


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
        grid[(r, col)] = _fix_text(c.get("text", ""))
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


# ═══════════════════════════════════════════════════════════════
# BLOCKER 5: Cross-references
# ═══════════════════════════════════════════════════════════════

def _associate_text_blocks(elements):
    """Associate body text with parent structural elements.

    Uses section-number hierarchy as backbone + content-affinity rules:
    1. Section-number match: text_block's section → nearest heading at that section
    2. "where" clauses → nearest preceding formula
    3. Figure references → corresponding figure element
    4. List items → nearest preceding provision
    5. Default → deepest matching section heading
    """
    # Build heading index by section number
    heading_index = {}
    for e in elements:
        if e["type"] == "heading":
            sec = e["source"]["section"]
            heading_index[sec] = e["id"]

    # Build element lookup
    id_lookup = {e["id"]: e for e in elements}

    # Build ordered list for sequential lookups
    elem_ids = [e["id"] for e in elements]

    associated = 0
    for i, e in enumerate(elements):
        if e["type"] != "text_block":
            e["parent_id"] = None
            continue

        text = e.get("data", {}).get("rule", "")
        section = e["source"]["section"]

        # Rule 1: "where" clause → nearest preceding formula, provision, or equation-bearing text
        if text.lower().startswith("where ") or text.lower().startswith("in which"):
            found = False
            for j in range(i - 1, max(i - 10, -1), -1):
                prev = elements[j]
                prev_text = prev.get("data", {}).get("rule", "") or prev.get("data", {}).get("expression", "")
                # Match: formula elements, provisions, or text_blocks with math
                if prev["type"] == "formula":
                    e["parent_id"] = prev["id"]
                    found = True
                    break
                if prev["type"] in ("provision", "text_block") and re.search(r'[=<>≤≥]', prev_text):
                    e["parent_id"] = prev["id"]
                    found = True
                    break
                if prev["type"] == "heading":
                    break  # don't cross section boundaries
            if not found:
                e["parent_id"] = _find_section_heading(section, heading_index)
            if e.get("parent_id"):
                associated += 1
            continue

        # Rule 2: Figure reference text → figure element
        fig_match = re.match(r'^Figure\s+(\d+\.\d+-\d+[A-D]?)', text)
        if fig_match:
            fig_num = fig_match.group(1)
            for el in elements:
                if el["type"] == "figure" and fig_num in el.get("source", {}).get("citation", ""):
                    e["parent_id"] = el["id"]
                    associated += 1
                    break
            else:
                e["parent_id"] = _find_section_heading(section, heading_index)
                if e["parent_id"]:
                    associated += 1
            continue

        # Rule 3: Numbered list item → nearest preceding provision or heading
        if re.match(r'^\d+\.\s', text):
            for j in range(i - 1, max(i - 15, -1), -1):
                if elements[j]["type"] in ("provision", "heading"):
                    e["parent_id"] = elements[j]["id"]
                    associated += 1
                    break
            else:
                e["parent_id"] = _find_section_heading(section, heading_index)
                if e["parent_id"]:
                    associated += 1
            continue

        # Rule 4: Default — section-number hierarchy lookup
        parent_id = _find_section_heading(section, heading_index)
        if parent_id:
            e["parent_id"] = parent_id
            associated += 1
        else:
            # Fallback: nearest preceding heading
            for j in range(i - 1, -1, -1):
                if elements[j]["type"] == "heading":
                    e["parent_id"] = elements[j]["id"]
                    associated += 1
                    break
            else:
                e["parent_id"] = None

    tb_count = sum(1 for e in elements if e["type"] == "text_block")
    print(f"    {associated}/{tb_count} text_blocks associated with parents")


def _find_section_heading(section, heading_index):
    """Find the deepest heading matching this section number via ancestor walk."""
    parts = section.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in heading_index:
            return heading_index[candidate]
        parts.pop()
    return None


def _add_cross_references(elements):
    """Scan text for Table/Figure/Section/Eq references and link to element IDs.

    Uses normalized number matching (26.6-1) instead of exact citation strings.
    Also loads cross-chapter index from manifest for inter-chapter resolution.
    """
    # Build normalized index: "26.6-1" → element_id
    num_index = {}
    sec_index = {}

    # Local elements
    for e in elements:
        cit = e.get("source", {}).get("citation", "")
        sec = e.get("source", {}).get("section", "")
        # Extract number from citation
        m = re.search(r'(\d+\.\d+-\d+[A-Da-d]?)', cit)
        if m:
            num_index[m.group(1)] = e["id"]
        if sec and sec not in sec_index:
            sec_index[sec] = e["id"]

    # Cross-chapter: load all other chapter files from manifest
    from extract.manifest import load_manifest
    manifest = load_manifest()
    from pathlib import Path
    for ch, info in manifest.get("chapters", {}).items():
        filepath = info.get("file", "")
        if filepath and Path(filepath).exists():
            try:
                ch_elements = json.loads(Path(filepath).read_text())
                for ce in ch_elements:
                    cit = ce.get("source", {}).get("citation", "")
                    sec = ce.get("source", {}).get("section", "")
                    m = re.search(r'(\d+\.\d+-\d+[A-Da-d]?)', cit)
                    if m and m.group(1) not in num_index:
                        num_index[m.group(1)] = ce["id"]
                    if sec and sec not in sec_index:
                        sec_index[sec] = ce["id"]
            except Exception:
                pass

    # Scan text for references
    ref_patterns = [
        re.compile(r'Table\s+(\d+\.\d+-\d+)'),
        re.compile(r'Figure\s+(\d+\.\d+-\d+[A-D]?)'),
        re.compile(r'Eq(?:uation)?\.\s*\((\d+\.\d+-\d+[a-z]?)\)'),
    ]
    sec_pattern = re.compile(r'Section\s+(\d+\.\d+(?:\.\d+)*)')

    for e in elements:
        text = (e.get("data", {}).get("rule", "") or "") + " " + \
               (e.get("data", {}).get("definition", "") or "") + " " + \
               (e.get("data", {}).get("target", "") or "")
        if not text.strip():
            continue

        refs = set()

        # Number-based refs (tables, figures, equations)
        for pattern in ref_patterns:
            for m in pattern.finditer(text):
                num = m.group(1)
                target_id = num_index.get(num)
                if target_id and target_id != e["id"]:
                    refs.add(target_id)

        # Section refs
        for m in sec_pattern.finditer(text):
            sec = m.group(1)
            target_id = sec_index.get(sec)
            if target_id and target_id != e["id"]:
                refs.add(target_id)

        if refs:
            e["cross_references"] = sorted(refs)


# ═══════════════════════════════════════════════════════════════
# BLOCKER 1: Merge short fragments
# ═══════════════════════════════════════════════════════════════

def _merge_fragments(elements):
    """Merge short text fragments (<30 chars) into preceding element."""
    merged = []
    for i, e in enumerate(elements):
        text = e.get("data", {}).get("rule", "")
        if e["type"] == "text_block" and len(text) < 30 and merged:
            # Append to previous element's text
            prev = merged[-1]
            prev_text = prev.get("data", {}).get("rule", "")
            if prev_text:
                prev["data"]["rule"] = prev_text + " " + text
            continue
        merged.append(e)
    return merged


# ═══════════════════════════════════════════════════════════════
# BLOCKER 2: Page-level equation scan
# ═══════════════════════════════════════════════════════════════

def _add_equations_page_level(elements, std_slug, standard, chapter, id_set, make_id):
    """Find equations by scanning raw PDF text via pdfplumber (not Docling)."""
    import pdfplumber

    existing_eqs = set()
    for e in elements:
        if e["type"] == "formula":
            m = re.search(r'(\d+\.\d+-\d+[a-z]?)', e["source"].get("citation", ""))
            if m:
                existing_eqs.add(m.group(1))

    # Get PDF path from first element
    pdf_path = None
    for e in elements:
        if e.get("source", {}).get("standard"):
            # Reconstruct — we need to find the PDF
            from pathlib import Path
            pdfs = list(Path("input").glob("*.pdf"))
            if pdfs:
                pdf_path = pdfs[0]
            break

    if not pdf_path:
        return

    new = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            raw = page.extract_text() or ""
            page_no = i + 1

            for m in re.finditer(r'\((\d+\.\d+-\d+[a-z]?)\)', raw):
                eq_num = m.group(1)
                if not eq_num.startswith(f"{chapter}."):
                    continue
                if eq_num in existing_eqs:
                    continue
                existing_eqs.add(eq_num)

                # Get expression context
                before = raw[:m.start()].rstrip()
                boundary = max(before.rfind(". "), before.rfind(";"), before.rfind("\n"), -1) + 1
                expr_start = max(boundary, m.start() - 200)
                expression = raw[expr_start:m.end()].strip()
                expression = re.sub(r'\s+', ' ', expression)
                if len(expression) < 5:
                    continue

                # Fix ligatures
                for lig, rep in LIGATURES.items():
                    expression = expression.replace(lig, rep)

                sec = eq_num.rsplit("-", 1)[0]
                eid = make_id(sec, f"E{eq_num.replace('.', '-')}")
                new.append({
                    "id": eid, "type": "formula",
                    "source": {"standard": standard, "chapter": chapter,
                               "section": sec, "citation": f"Eq. ({eq_num})", "page": page_no},
                    "title": f"Equation ({eq_num})",
                    "description": "",
                    "data": {"expression": expression, "parameters": {}},
                    "cross_references": [],
                    "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": "pdfplumber_scan"}
                })

    if new:
        print(f"    Found {len(new)} additional equations from raw PDF text")
    elements.extend(new)


# ═══════════════════════════════════════════════════════════════
# BLOCKER 4: Parse provision conditions
# ═══════════════════════════════════════════════════════════════

def _parse_conditions(elements):
    """Extract structured conditions from provision rule text."""
    condition_patterns = [
        # "h > 60 ft" or "height greater than 60 ft"
        (re.compile(r'\b([a-zA-Z_]+)\s*(>|>=|<|<=|=)\s*(\d+\.?\d*)\s*(ft|m|mi/h|m/s|mph|Hz|%)?'),
         lambda m: {"parameter": m.group(1), "operator": m.group(2).replace("=", "==") if m.group(2) == "=" else m.group(2),
                     "value": float(m.group(3)), "unit": m.group(4)}),
        # "greater than or equal to X"
        (re.compile(r'(?:greater than or equal to|equal to or greater than)\s+(\d+\.?\d*)\s*(ft|m|mi/h|m/s|mph)?'),
         lambda m: {"parameter": "", "operator": ">=", "value": float(m.group(1)), "unit": m.group(2)}),
        # "less than or equal to X"
        (re.compile(r'(?:less than or equal to|equal to or less than)\s+(\d+\.?\d*)\s*(ft|m|mi/h|m/s|mph)?'),
         lambda m: {"parameter": "", "operator": "<=", "value": float(m.group(1)), "unit": m.group(2)}),
        # "greater than X"
        (re.compile(r'greater than\s+(\d+\.?\d*)\s*(ft|m|mi/h|m/s|mph)?'),
         lambda m: {"parameter": "", "operator": ">", "value": float(m.group(1)), "unit": m.group(2)}),
        # "less than X"
        (re.compile(r'less than\s+(\d+\.?\d*)\s*(ft|m|mi/h|m/s|mph)?'),
         lambda m: {"parameter": "", "operator": "<", "value": float(m.group(1)), "unit": m.group(2)}),
        # "Risk Category II" or "Risk Category III"
        (re.compile(r'Risk\s+Category\s+(I{1,3}V?|IV)'),
         lambda m: {"parameter": "risk_category", "operator": "==", "value": m.group(1), "unit": None}),
        # "Exposure B" or "Exposure C"
        (re.compile(r'Exposure\s+(B|C|D)\b'),
         lambda m: {"parameter": "exposure_category", "operator": "==", "value": m.group(1), "unit": None}),
        # "low-rise buildings"
        (re.compile(r'low-rise\s+building'),
         lambda m: {"parameter": "building_type", "operator": "==", "value": "low-rise", "unit": None}),
        # "enclosed" / "partially enclosed" / "open"
        (re.compile(r'(enclosed|partially enclosed|partially open|open)\s+building'),
         lambda m: {"parameter": "enclosure", "operator": "==", "value": m.group(1), "unit": None}),
        # "flexible" / "rigid"
        (re.compile(r'(flexible|rigid)\s+(?:building|structure)'),
         lambda m: {"parameter": "flexibility", "operator": "==", "value": m.group(1), "unit": None}),
        # "hurricane-prone regions"
        (re.compile(r'hurricane-prone\s+region'),
         lambda m: {"parameter": "location", "operator": "in", "value": "hurricane-prone regions", "unit": None}),
        # "wind-borne debris region"
        (re.compile(r'wind-borne\s+debris\s+region'),
         lambda m: {"parameter": "location", "operator": "in", "value": "wind-borne debris regions", "unit": None}),
    ]

    for e in elements:
        if e["type"] not in ("provision", "exception"):
            continue
        text = e.get("data", {}).get("rule", "")
        if not text:
            continue

        conditions = []
        for pattern, builder in condition_patterns:
            for m in pattern.finditer(text):
                cond = builder(m)
                if cond and cond not in conditions:
                    conditions.append(cond)

        if conditions:
            e["data"]["conditions"] = conditions


# ═══════════════════════════════════════════════════════════════
# BLOCKER 3: Extract formula parameters
# ═══════════════════════════════════════════════════════════════

def _extract_parameters(elements):
    """Extract parameter names and definitions from formula context."""
    # Build symbols table from Section 26.3 text blocks
    symbols = {}
    for e in elements:
        if e["source"]["section"] == "26.3" and e["type"] == "text_block":
            text = e.get("data", {}).get("rule", "")
            # Pattern: "X = description, unit"
            for m in re.finditer(r'([A-Za-z_αβεγθηλ]+(?:_[a-zA-Z0-9]+)?)\s*=\s*([^=]+?)(?=\s+[A-Za-z_αβεγθηλ]+\s*=|\s*$)', text):
                sym = m.group(1).strip()
                desc = m.group(2).strip().rstrip(",")
                if len(sym) < 5 and len(desc) > 5:
                    symbols[sym] = desc

    # Also check "where" clauses near formulas
    for e in elements:
        if e["type"] != "formula":
            continue

        expression = e["data"].get("expression", "")
        params = {}

        # Extract variable names from expression (single letters or subscripted)
        var_pattern = re.compile(r'\b([A-Za-z](?:_[a-zA-Z0-9]+)?)\b')
        expr_vars = set(var_pattern.findall(expression))
        # Filter out common non-variables
        expr_vars -= {"and", "or", "the", "in", "for", "ft", "mi", "lb", "SI", "where", "from"}

        for var in expr_vars:
            if var in symbols:
                params[var] = {"description": symbols[var]}

        # Check surrounding elements for "where" clauses
        idx = elements.index(e)
        for nearby in elements[max(0, idx-2):idx+3]:
            if nearby is e:
                continue
            nearby_text = nearby.get("data", {}).get("rule", "")
            if "where" in nearby_text.lower()[:20]:
                for m in re.finditer(r'([A-Za-z_]+)\s*=\s*([^,;]+)', nearby_text):
                    sym = m.group(1).strip()
                    desc = m.group(2).strip()
                    if sym in expr_vars and len(desc) > 3:
                        params[sym] = {"description": desc}

        if params:
            e["data"]["parameters"] = params
