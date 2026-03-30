"""
Elastic heuristics — learn classification patterns from each chapter's own content.

Instead of hardcoded regex, this module:
1. Scans the chapter's bold labels to discover what patterns exist
2. Groups labels into categories by font size, caps, and content
3. Builds a per-chapter classifier from the discovered patterns
4. Applies that classifier to classify every element

This adapts to each chapter's unique formatting without manual rules.
"""

import re
import json
from collections import defaultdict, Counter
from pathlib import Path


def build_chapter_classifier(bold_map, elements):
    """Build a classifier from the chapter's own bold label patterns.

    Scans all bold labels, clusters them by characteristics, and returns
    a function that classifies any text element.
    """
    # Collect all bold labels with their characteristics
    labels = []
    for page_no, spans in bold_map.items():
        for s in spans:
            bp = s.get("bold_prefix", "").strip()
            if not bp or len(bp) < 2:
                continue
            labels.append({
                "text": bp,
                "full": s.get("text", "").strip(),
                "page": page_no,
                "all_bold": s.get("all_bold", False),
            })

    # Discover patterns from the labels themselves
    patterns = _discover_patterns(labels)

    # Build classifier function
    def classify(text, bold_prefix, all_bold, docling_label):
        return _elastic_classify(text, bold_prefix, all_bold, docling_label, patterns)

    return classify, patterns


def _discover_patterns(labels):
    """Discover classification patterns from the chapter's bold labels."""
    patterns = {
        "section_prefixes": set(),      # bold text that starts section headings
        "definition_markers": set(),     # bold ALL-CAPS TERM: patterns
        "note_markers": set(),           # User Note, Commentary, Note, etc.
        "exception_markers": set(),      # EXCEPTION, Exception, etc.
        "reference_markers": set(),      # ASTM, ANSI, ACI, etc.
        "skip_patterns": set(),          # page numbers, footers
        "topic_headings": set(),         # bold ALL-CAPS single words/phrases (DEFINITIONS, PROCEDURES)
        "sub_definition_markers": set(), # Surface Roughness X, Exposure X, etc.
    }

    for lbl in labels:
        bp = lbl["text"]
        full = lbl["full"]

        # Page furniture: pure numbers (page numbers) or standard footers
        if re.match(r'^\d{1,4}$', bp):
            patterns["skip_patterns"].add(bp)
            continue
        if any(footer in bp for footer in ["STANDARD ASCE", "Minimum Design Loads", "Downloaded from"]):
            patterns["skip_patterns"].add(bp[:30])
            continue

        # Section numbers: starts with digits like "26.1" or "12.3.4"
        if re.match(r'^\d+\.\d+', bp):
            patterns["section_prefixes"].add(bp[:20])
            continue

        # Chapter heading
        if bp.startswith("CHAPTER"):
            patterns["section_prefixes"].add("CHAPTER")
            continue

        # Note markers (various formats across chapters)
        note_words = ["User Note", "Commentary", "Note:", "USER NOTE", "COMMENTARY"]
        if any(bp.startswith(nw) for nw in note_words):
            # Extract the exact marker this chapter uses
            for nw in note_words:
                if bp.startswith(nw):
                    patterns["note_markers"].add(nw)
                    break
            continue

        # Exception markers
        if bp.upper().startswith("EXCEPTION"):
            patterns["exception_markers"].add(bp.split(":")[0].split(".")[0].strip())
            continue

        # External standard references
        if re.match(r'^(ASTM|ANSI|CAN/CSA|ICC|ACI|AISC|AWS|IBC|FEMA|TMS|APA)', bp):
            patterns["reference_markers"].add(bp.split(",")[0].split(" ")[0])
            continue

        # ALL-CAPS term with colon = definition
        if bp == bp.upper() and ":" in full[:len(bp)+5] and any(c.isalpha() for c in bp) and len(bp) < 80:
            patterns["definition_markers"].add("ALLCAPS_COLON")
            continue

        # Sub-definitions: "Surface Roughness X." or "Exposure X." or "Seismic Design Category X"
        if re.match(r'^[A-Z][a-z].*[A-Z]\.?\s*$', bp) and len(bp) < 40:
            patterns["sub_definition_markers"].add(bp[:20])
            continue

        # ALL-CAPS topic heading (DEFINITIONS, PROCEDURES, EXPOSURE, etc.)
        if bp == bp.upper() and len(bp) < 60 and any(c.isalpha() for c in bp):
            if ":" not in full[:len(bp)+5]:
                patterns["topic_headings"].add(bp)
                continue

    return patterns


def _elastic_classify(text, bold_prefix, all_bold, docling_label, patterns):
    """Classify using discovered patterns instead of hardcoded rules."""

    # Skip page furniture
    for skip in patterns["skip_patterns"]:
        if bold_prefix.startswith(skip) or text.startswith(skip):
            return None, None

    # Docling section headers
    if docling_label in ("section_header", "title"):
        return "heading", None

    # Note markers (learned from this chapter)
    for marker in patterns["note_markers"]:
        if bold_prefix.startswith(marker) or text.startswith(marker):
            return "user_note", None

    # Exception markers (learned from this chapter)
    for marker in patterns["exception_markers"]:
        if bold_prefix.upper().startswith(marker.upper()):
            return "exception", None

    # Definition: ALL-CAPS TERM + colon (if this chapter has definitions)
    if "ALLCAPS_COLON" in patterns["definition_markers"]:
        def_match = re.match(r'^([A-Z][A-Z0-9 ,./()]+?)\s*:\s*(.+)', text, re.DOTALL)
        if def_match:
            term = def_match.group(1).strip()
            if 2 < len(term) < 80 and term == term.upper() and any(c.isalpha() for c in term):
                return "definition", None

    # Sub-definitions (learned from this chapter)
    for marker in patterns["sub_definition_markers"]:
        if bold_prefix.startswith(marker[:10]):
            return "definition", "sub_definition"

    # Section number heading
    if re.match(r'^\d+\.\d+', bold_prefix):
        return "heading", None

    # Chapter heading
    if bold_prefix.startswith("CHAPTER"):
        return "heading", None

    # Topic heading (learned from this chapter)
    for topic in patterns["topic_headings"]:
        if bold_prefix == topic:
            return "heading", "topic"

    # Reference markers (learned from this chapter)
    for marker in patterns["reference_markers"]:
        if bold_prefix.startswith(marker):
            return "reference", None

    # Provision: regulatory language (universal across all building codes)
    provision_markers = [
        "shall ", "shall not", "is permitted", "must be", "are required",
        "is required", "are permitted", "need not", "is not required",
    ]
    if any(m in text.lower() for m in provision_markers):
        return "provision", None

    # Default: text_block
    return "text_block", None


def report_patterns(patterns):
    """Print discovered patterns for debugging."""
    print("  Discovered patterns:")
    for key, vals in sorted(patterns.items()):
        if vals:
            display = sorted(vals) if len(vals) <= 10 else sorted(vals)[:10]
            print(f"    {key}: {display}")


def run_elastic_audit(batch_report_path="output/qc/batch-report.json"):
    """Run elastic classification on all chapters and report improvement."""
    import fitz

    batch = json.loads(Path(batch_report_path).read_text())

    print("ELASTIC HEURISTICS AUDIT")
    print("=" * 60)

    results = []
    for ch_info in batch.get("chapters", []):
        ch_num = ch_info["chapter"]
        ch_file = Path(ch_info.get("file", ""))
        if not ch_file.exists():
            continue

        elements = json.loads(ch_file.read_text())
        if not elements:
            continue

        # Count text_blocks before
        tb_before = sum(1 for e in elements if e["type"] == "text_block")

        # Build elastic classifier from bold map
        # Re-scan bold map for this chapter
        ch_pdf = Path(f"input/ch{ch_num}.pdf")
        if not ch_pdf.exists():
            # Try to extract from full book
            full_pdfs = sorted(Path("input").glob("ASCE*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
            if not full_pdfs:
                continue
            # Skip — can't re-extract bold map without the chapter PDF
            results.append({
                "chapter": ch_num,
                "elements": len(elements),
                "tb_before": tb_before,
                "tb_after": tb_before,
                "improvement": 0,
            })
            continue

        doc = fitz.open(str(ch_pdf))
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
                    line_text = "".join(s["text"] for s in line_spans)
                    first_bold = ""
                    a_bold = True
                    for s in line_spans:
                        is_b = s["font"].endswith(".B") or s["font"].endswith(".BI") or bool(s["flags"] & 16)
                        if is_b:
                            first_bold += s["text"]
                        elif first_bold:
                            a_bold = False
                            break
                        else:
                            a_bold = False
                            break
                    spans.append({
                        "text": line_text.strip(),
                        "bold_prefix": first_bold.strip(),
                        "all_bold": a_bold and bool(first_bold),
                    })
            bold_map[page_num + 1] = spans
        doc.close()

        # Build elastic classifier
        classify, patterns = build_chapter_classifier(bold_map, elements)

        # Re-classify text_blocks
        reclassified = 0
        for e in elements:
            if e["type"] != "text_block":
                continue

            text = e.get("data", {}).get("rule", "")
            # Find bold prefix for this element
            page_no = e["source"]["page"]
            bp = ""
            ab = False
            page_spans = bold_map.get(page_no, [])
            # Simple: check if the element text starts with a known bold prefix
            for s in page_spans:
                if s["bold_prefix"] and text.startswith(s["bold_prefix"][:15]):
                    bp = s["bold_prefix"]
                    ab = s["all_bold"]
                    break

            new_type, notes = classify(text, bp, ab, "text")
            if new_type and new_type != "text_block":
                e["type"] = new_type
                if notes:
                    e["metadata"]["qc_notes"] = notes
                reclassified += 1

        tb_after = sum(1 for e in elements if e["type"] == "text_block")

        results.append({
            "chapter": ch_num,
            "elements": len(elements),
            "tb_before": tb_before,
            "tb_after": tb_after,
            "reclassified": reclassified,
            "patterns_found": sum(len(v) for v in patterns.values()),
        })

        print(f"  Ch {ch_num:>2}: {tb_before:>3} → {tb_after:>3} text_blocks "
              f"({reclassified} reclassified, {sum(len(v) for v in patterns.values())} patterns)")

    # Summary
    total_before = sum(r["tb_before"] for r in results)
    total_after = sum(r["tb_after"] for r in results)
    total_reclass = sum(r.get("reclassified", 0) for r in results)

    print(f"\n  TOTAL: {total_before} → {total_after} text_blocks ({total_reclass} reclassified)")
    print(f"  Reduction: {(total_before - total_after) / max(total_before, 1) * 100:.0f}%")

    return results


if __name__ == "__main__":
    run_elastic_audit()
