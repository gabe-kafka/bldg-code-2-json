"""
PDF Parser Arena — compare extraction approaches on the same pages.

Tests each parser on the same PDF pages and scores against
the gold-standard Docling output (which we've visually confirmed).
Evaluates: text fidelity, structure detection, table extraction,
font metadata, and coordinate accuracy.
"""

import json
import time
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber
from PIL import Image


def get_pdf_path():
    return list(Path("input").glob("*.pdf"))[0]


# ═══════════════════════════════════════════════════════════════
# PARSER 1: PyMuPDF (fitz) — raw text blocks + font info
# ═══════════════════════════════════════════════════════════════

def parse_pymupdf(pdf_path, page_num):
    """Extract using PyMuPDF's text block + dict APIs."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    t0 = time.time()

    # Method A: text blocks (fast, grouped)
    blocks = page.get_text("blocks")  # (x0,y0,x1,y1, text, block_no, type)

    # Method B: dict with font info (detailed)
    text_dict = page.get_text("dict")

    elapsed = time.time() - t0

    elements = []
    for block in blocks:
        x0, y0, x1, y1, text, block_no, block_type = block
        if block_type == 1:  # image block
            elements.append({
                "x0": round(x0), "y0": round(y0),
                "x1": round(x1), "y1": round(y1),
                "type": "image",
                "text": "",
            })
        else:
            elements.append({
                "x0": round(x0), "y0": round(y0),
                "x1": round(x1), "y1": round(y1),
                "type": "text",
                "text": text.strip()[:200],
            })

    # Extract font info from dict
    fonts_used = set()
    font_details = []
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                fonts_used.add(span.get("font", ""))
                font_details.append({
                    "text": span["text"][:80],
                    "font": span["font"],
                    "size": round(span["size"], 1),
                    "flags": span["flags"],  # bold=16, italic=2
                    "x0": round(span["bbox"][0]),
                    "y0": round(span["bbox"][1]),
                    "x1": round(span["bbox"][2]),
                    "y1": round(span["bbox"][3]),
                })

    # Images
    images = page.get_images(full=True)

    # Lines/rects (for table detection)
    drawings = page.get_drawings()
    h_lines = [d for d in drawings if d.get("items") and
               any(i[0] == "l" and abs(i[1].y - i[2].y) < 2 for i in d["items"])]
    v_lines = [d for d in drawings if d.get("items") and
               any(i[0] == "l" and abs(i[1].x - i[2].x) < 2 for i in d["items"])]

    page_w, page_h = page.rect.width, page.rect.height
    doc.close()
    return {
        "parser": "pymupdf",
        "time": elapsed,
        "elements": elements,
        "font_details": font_details,
        "fonts_used": sorted(fonts_used),
        "images": len(images),
        "h_lines": len(h_lines),
        "v_lines": len(v_lines),
        "page_size": {"w": round(page_w), "h": round(page_h)},
    }


# ═══════════════════════════════════════════════════════════════
# PARSER 2: pdfplumber — chars, lines, rects, tables
# ═══════════════════════════════════════════════════════════════

def parse_pdfplumber(pdf_path, page_num):
    """Extract using pdfplumber's detailed char/line/table APIs."""
    t0 = time.time()

    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[page_num]

        # Full text
        full_text = page.extract_text() or ""

        # Characters with position + font
        chars = page.chars
        char_sample = [{
            "text": c["text"],
            "font": c.get("fontname", ""),
            "size": round(c.get("size", 0), 1),
            "x0": round(c["x0"]),
            "y0": round(c["top"]),
            "x1": round(c["x1"]),
            "y1": round(c["bottom"]),
            "bold": "Bold" in c.get("fontname", ""),
        } for c in chars[:500]]

        # Words with positions
        words = page.extract_words()

        # Lines and rects (table grid detection)
        lines = page.lines
        rects = page.rects

        # Table detection
        tables = page.find_tables()
        table_data = []
        for t in tables:
            table_data.append({
                "bbox": [round(t.bbox[0]), round(t.bbox[1]),
                         round(t.bbox[2]), round(t.bbox[3])],
                "rows": len(t.extract()),
                "cols": len(t.extract()[0]) if t.extract() else 0,
                "data_sample": t.extract()[:3],
            })

        # Images
        images = page.images

    elapsed = time.time() - t0

    # Detect font-size groups (for heading detection)
    font_sizes = {}
    for c in chars:
        size = round(c.get("size", 0), 1)
        font_sizes[size] = font_sizes.get(size, 0) + 1

    # Detect bold spans
    bold_spans = []
    current_bold = None
    for c in chars:
        is_bold = "Bold" in c.get("fontname", "")
        if is_bold:
            if current_bold is None:
                current_bold = {"text": c["text"], "x0": c["x0"], "y0": c["top"],
                                "size": c.get("size", 0)}
            else:
                current_bold["text"] += c["text"]
        else:
            if current_bold and len(current_bold["text"].strip()) > 2:
                bold_spans.append(current_bold)
            current_bold = None
    if current_bold and len(current_bold["text"].strip()) > 2:
        bold_spans.append(current_bold)

    return {
        "parser": "pdfplumber",
        "time": elapsed,
        "text_length": len(full_text),
        "text_sample": full_text[:500],
        "chars": len(chars),
        "words": len(words),
        "lines": len(lines),
        "rects": len(rects),
        "tables": table_data,
        "images": len(images),
        "font_sizes": dict(sorted(font_sizes.items(), key=lambda x: -x[1])),
        "bold_spans_sample": bold_spans[:20],
        "char_sample": char_sample[:50],
    }


# ═══════════════════════════════════════════════════════════════
# PARSER 3: Docling — full document model
# ═══════════════════════════════════════════════════════════════

def parse_docling(pdf_path, page_num):
    """Extract using Docling's document converter."""
    from docling.document_converter import DocumentConverter

    t0 = time.time()
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document
    d = doc.export_to_dict()
    elapsed = time.time() - t0

    # Get elements for this page
    page_no = page_num + 1  # Docling is 1-indexed
    elements = []

    for collection in ["texts", "tables", "pictures"]:
        for item in d.get(collection, []):
            for prov in item.get("prov", []):
                if prov["page_no"] != page_no:
                    continue
                elements.append({
                    "label": item.get("label", "unknown"),
                    "text": item.get("text", "")[:200],
                    "bbox": prov["bbox"],
                    "collection": collection,
                })

    # Markdown for this page (approximate — Docling doesn't paginate markdown)
    md = doc.export_to_markdown()

    return {
        "parser": "docling",
        "time": elapsed,
        "elements": elements,
        "element_labels": {e["label"]: sum(1 for x in elements if x["label"] == e["label"])
                          for e in elements},
        "markdown_length": len(md),
        "total_texts": len(d.get("texts", [])),
        "total_tables": len(d.get("tables", [])),
        "total_pictures": len(d.get("pictures", [])),
    }


# ═══════════════════════════════════════════════════════════════
# PARSER 4: pymupdf4llm — markdown output
# ═══════════════════════════════════════════════════════════════

def parse_pymupdf4llm(pdf_path, page_num):
    """Extract using pymupdf4llm's markdown converter."""
    import pymupdf4llm

    t0 = time.time()
    md = pymupdf4llm.to_markdown(str(pdf_path), pages=[page_num])
    elapsed = time.time() - t0

    # Count markdown structures
    lines = md.split("\n")
    headings = [l for l in lines if l.startswith("#")]
    tables = [l for l in lines if "|" in l and "---" not in l]
    list_items = [l for l in lines if l.strip().startswith("- ") or l.strip().startswith("1.")]

    return {
        "parser": "pymupdf4llm",
        "time": elapsed,
        "markdown_length": len(md),
        "markdown_sample": md[:1500],
        "headings": headings[:10],
        "table_lines": len(tables),
        "list_items": len(list_items),
        "total_lines": len(lines),
    }


# ═══════════════════════════════════════════════════════════════
# PARSER 5: pdfplumber font-semantic (heading/definition detection)
# ═══════════════════════════════════════════════════════════════

def parse_font_semantic(pdf_path, page_num):
    """Use pdfplumber char-level font data for semantic classification."""
    t0 = time.time()

    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[page_num]
        chars = page.chars

    elapsed = time.time() - t0

    if not chars:
        return {"parser": "font_semantic", "time": elapsed, "elements": []}

    # Group chars into lines by y-position (within 2pt tolerance)
    lines = []
    current_line = [chars[0]]
    for c in chars[1:]:
        if abs(c["top"] - current_line[-1]["top"]) < 2:
            current_line.append(c)
        else:
            lines.append(current_line)
            current_line = [c]
    lines.append(current_line)

    # Classify each line
    elements = []
    for line_chars in lines:
        if not line_chars:
            continue

        text = "".join(c["text"] for c in line_chars).strip()
        if not text:
            continue

        # Font analysis
        fonts = set(c.get("fontname", "") for c in line_chars)
        sizes = [c.get("size", 0) for c in line_chars]
        avg_size = sum(sizes) / len(sizes) if sizes else 0
        is_bold = any("Bold" in f for f in fonts)
        is_all_caps = text == text.upper() and any(c.isalpha() for c in text)

        x0 = min(c["x0"] for c in line_chars)
        y0 = min(c["top"] for c in line_chars)
        x1 = max(c["x1"] for c in line_chars)
        y1 = max(c["bottom"] for c in line_chars)

        # Classification rules
        if avg_size > 11 and is_bold:
            label = "heading"
        elif is_bold and ":" in text and is_all_caps:
            label = "definition"
        elif is_bold and is_all_caps and len(text) < 80:
            label = "heading"
        elif text.startswith(("26.", "CHAPTER")):
            label = "heading"
        elif is_bold and ":" in text:
            label = "definition"
        else:
            label = "text"

        elements.append({
            "label": label,
            "text": text[:150],
            "x0": round(x0), "y0": round(y0),
            "x1": round(x1), "y1": round(y1),
            "font_size": round(avg_size, 1),
            "bold": is_bold,
            "all_caps": is_all_caps,
        })

    return {
        "parser": "font_semantic",
        "time": elapsed,
        "elements": elements,
        "element_labels": {},
    }


# ═══════════════════════════════════════════════════════════════
# ARENA RUNNER
# ═══════════════════════════════════════════════════════════════

def run_arena(page_num=0):
    """Run all parsers on a single page and display results."""
    pdf_path = get_pdf_path()
    print(f"PDF: {pdf_path.name}")
    print(f"Page: {page_num} (0-indexed)\n")

    results = {}

    # 1. PyMuPDF
    print("=" * 60)
    print("1. PYMUPDF — raw blocks + font metadata")
    print("=" * 60)
    r = parse_pymupdf(pdf_path, page_num)
    results["pymupdf"] = r
    print(f"Time: {r['time']:.3f}s")
    print(f"Elements: {len(r['elements'])}")
    print(f"Fonts: {r['fonts_used']}")
    print(f"Images: {r['images']}, H-lines: {r['h_lines']}, V-lines: {r['v_lines']}")
    print(f"Page size: {r['page_size']}")
    print(f"\nFont details (first 10):")
    for fd in r["font_details"][:10]:
        bold = " BOLD" if fd["flags"] & 16 else ""
        print(f"  [{fd['size']}pt{bold}] {fd['font']}: {fd['text'][:60]}")

    # 2. pdfplumber
    print("\n" + "=" * 60)
    print("2. PDFPLUMBER — chars + lines + tables + font data")
    print("=" * 60)
    r = parse_pdfplumber(pdf_path, page_num)
    results["pdfplumber"] = r
    print(f"Time: {r['time']:.3f}s")
    print(f"Chars: {r['chars']}, Words: {r['words']}")
    print(f"Lines: {r['lines']}, Rects: {r['rects']}")
    print(f"Tables: {len(r['tables'])}")
    for t in r["tables"]:
        print(f"  Table at {t['bbox']}: {t['rows']}x{t['cols']}")
        for row in t["data_sample"][:2]:
            print(f"    {row}")
    print(f"Font sizes: {r['font_sizes']}")
    print(f"\nBold spans (first 10):")
    for bs in r["bold_spans_sample"][:10]:
        print(f"  [{bs['size']:.0f}pt] {bs['text'][:60]}")

    # 3. pymupdf4llm
    print("\n" + "=" * 60)
    print("3. PYMUPDF4LLM — direct to markdown")
    print("=" * 60)
    r = parse_pymupdf4llm(pdf_path, page_num)
    results["pymupdf4llm"] = r
    print(f"Time: {r['time']:.3f}s")
    print(f"Markdown: {r['markdown_length']} chars, {r['total_lines']} lines")
    print(f"Headings: {r['headings']}")
    print(f"Table lines: {r['table_lines']}, List items: {r['list_items']}")
    print(f"\nMarkdown sample:\n{r['markdown_sample'][:800]}")

    # 4. Font-semantic
    print("\n" + "=" * 60)
    print("4. FONT-SEMANTIC — pdfplumber chars + classification rules")
    print("=" * 60)
    r = parse_font_semantic(pdf_path, page_num)
    results["font_semantic"] = r
    print(f"Time: {r['time']:.3f}s")
    print(f"Elements: {len(r['elements'])}")
    labels = {}
    for e in r["elements"]:
        labels[e["label"]] = labels.get(e["label"], 0) + 1
    print(f"Labels: {labels}")
    print(f"\nClassified lines (first 15):")
    for e in r["elements"][:15]:
        bold = "B" if e["bold"] else " "
        caps = "C" if e["all_caps"] else " "
        print(f"  [{e['font_size']:4.1f}pt {bold}{caps}] {e['label']:12s} {e['text'][:60]}")

    # 5. Docling (slow, run last)
    print("\n" + "=" * 60)
    print("5. DOCLING — full document model")
    print("=" * 60)
    r = parse_docling(pdf_path, page_num)
    results["docling"] = r
    print(f"Time: {r['time']:.2f}s (full document)")
    print(f"Page elements: {len(r['elements'])}")
    print(f"Labels: {r['element_labels']}")
    print(f"Total: {r['total_texts']} texts, {r['total_tables']} tables, {r['total_pictures']} pictures")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Parser':<16} {'Time':>8} {'Elements':>10} {'Tables':>8} {'Fonts':>8}")
    print("-" * 56)

    pymupdf = results["pymupdf"]
    print(f"{'pymupdf':<16} {pymupdf['time']:>7.3f}s {len(pymupdf['elements']):>10} {'-':>8} {len(pymupdf['fonts_used']):>8}")

    plumber = results["pdfplumber"]
    print(f"{'pdfplumber':<16} {plumber['time']:>7.3f}s {plumber['words']:>10} {len(plumber['tables']):>8} {len(plumber['font_sizes']):>8}")

    m4l = results["pymupdf4llm"]
    print(f"{'pymupdf4llm':<16} {m4l['time']:>7.3f}s {m4l['total_lines']:>10} {m4l['table_lines']:>8} {'-':>8}")

    fsem = results["font_semantic"]
    print(f"{'font_semantic':<16} {fsem['time']:>7.3f}s {len(fsem['elements']):>10} {'-':>8} {'yes':>8}")

    docl = results["docling"]
    print(f"{'docling':<16} {docl['time']:>7.2f}s {len(docl['elements']):>10} {docl['total_tables']:>8} {'-':>8}")

    return results


if __name__ == "__main__":
    import sys
    page = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    run_arena(page)
