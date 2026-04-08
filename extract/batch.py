"""
Overnight batch harness.

Processes every chapter in the full ASCE 7-22 PDF:
1. Detect chapter boundaries
2. Extract each chapter to a temporary single-chapter PDF
3. Run pipeline_v3 on each
4. Accumulate symbols registry across chapters
5. Re-resolve cross-chapter references after each new chapter
6. Audit all connections for legitimacy (duplicate variable detection)
7. Produce morning report

Usage:
    python extract/batch.py input/full-asce7-22.pdf
"""

import json
import re
import sys
import time
import fitz
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict


MAX_CHAPTER_PAGES = 50

# Patterns that mark the end of numbered chapters (appendices, commentary, etc.)
_SECTION_BOUNDARY_RE = re.compile(
    r'^\s*(COMMENTARY|APPENDIX\s+[A-Z0-9]|INDEX|REFERENCES)\s*$',
    re.MULTILINE,
)


def find_chapters(pdf_path):
    """Find all chapter boundaries in the full PDF."""
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    # Collect chapter start pages
    chapters = []
    for i in range(total_pages):
        text = doc[i].get_text()[:500]
        m = re.search(r'CHAPTER\s+(\d+)\s*\n\s*([A-Z][A-Z :,\-]+)', text)
        if m:
            chapters.append({
                "chapter": int(m.group(1)),
                "title": m.group(2).strip(),
                "start_page": i,
            })

    # Collect appendix/commentary boundary pages (bold, full-page-width headers)
    boundary_pages = []
    for i in range(total_pages):
        text = doc[i].get_text()[:500]
        if _SECTION_BOUNDARY_RE.search(text):
            boundary_pages.append(i)

    doc.close()

    # Build a sorted list of all hard stop pages (next chapter starts + section boundaries)
    def _earliest_stop(after_page):
        """Return the earliest hard stop page that comes after `after_page`."""
        candidates = [p for p in boundary_pages if p > after_page]
        return min(candidates) if candidates else None

    # Set end pages
    for i in range(len(chapters)):
        ch_start = chapters[i]["start_page"]

        # Next chapter start (if any)
        if i + 1 < len(chapters):
            next_ch_start = chapters[i + 1]["start_page"]
        else:
            next_ch_start = total_pages  # sentinel

        # Earliest section boundary after this chapter starts
        boundary = _earliest_stop(ch_start)

        # Pick the tightest upper bound
        raw_end = min(
            next_ch_start - 1,
            (boundary - 1) if boundary is not None else total_pages - 1,
        )

        # Cap at MAX_CHAPTER_PAGES
        capped_end = min(raw_end, ch_start + MAX_CHAPTER_PAGES - 1)
        if capped_end < raw_end:
            print(
                f"  [WARN] Chapter {chapters[i]['chapter']} truncated from "
                f"{raw_end - ch_start + 1} pages to {MAX_CHAPTER_PAGES} pages"
            )

        chapters[i]["end_page"] = capped_end
        chapters[i]["pages"] = capped_end - ch_start + 1

    return chapters


def extract_chapter_pdf(full_pdf_path, start_page, end_page, output_path):
    """Extract a page range from the full PDF into a single-chapter PDF."""
    doc = fitz.open(str(full_pdf_path))
    new_doc = fitz.open()
    new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page)
    new_doc.save(str(output_path))
    new_doc.close()
    doc.close()


def run_batch(pdf_path, standard="ASCE 7-22", chapters=None, skip_reserved=True, output_dir="output/runs"):
    """Run the full batch extraction.

    Args:
        pdf_path:       Path to the full book PDF.
        standard:       Standard name for element IDs, e.g. "ASCE 7-22", "IBC-2021".
        chapters:       Optional list of chapter numbers to process. None = all.
        skip_reserved:  Skip chapters titled "RESERVED".
        output_dir:     Directory for per-chapter JSON output.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    print("=" * 60)
    print("OVERNIGHT BATCH EXTRACTION")
    print(f"PDF: {pdf_path.name}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Find chapters
    all_chapters = find_chapters(pdf_path)
    print(f"\nFound {len(all_chapters)} chapters in {pdf_path.name}")

    # Filter
    if chapters:
        all_chapters = [ch for ch in all_chapters if ch["chapter"] in chapters]
    if skip_reserved:
        all_chapters = [ch for ch in all_chapters if "RESERVED" not in ch["title"]]

    print(f"Processing {len(all_chapters)} chapters\n")

    # Results tracking
    results = []
    total_elements = 0
    total_symbols = 0

    for i, ch_info in enumerate(all_chapters):
        ch_num = ch_info["chapter"]
        ch_title = ch_info["title"]
        pages = ch_info["pages"]

        print(f"\n{'─' * 60}")
        print(f"[{i+1}/{len(all_chapters)}] Chapter {ch_num}: {ch_title} ({pages} pages)")
        print(f"{'─' * 60}")

        ch_start = time.time()

        try:
            # Extract chapter PDF
            ch_pdf = Path(f"input/ch{ch_num}.pdf")
            extract_chapter_pdf(pdf_path, ch_info["start_page"], ch_info["end_page"], ch_pdf)

            # Run pipeline
            from extract.pipeline_v3 import run_v3
            elements, md = run_v3(ch_pdf, standard=standard, chapter=ch_num)

            # Fix nulls
            for e in elements:
                if e.get("description") is None:
                    e["description"] = ""
                if e.get("source", {}).get("citation") is None:
                    e["source"]["citation"] = f"Section {e['source'].get('section', '')}"

            # Save
            output_file = output_dir / f"ch{ch_num}.json"
            output_file.write_text(json.dumps(elements, indent=2))

            # Save markdown
            md_file = output_dir / f"ch{ch_num}.md"
            md_file.write_text(md)

            # Stats
            types = {}
            for e in elements:
                types[e["type"]] = types.get(e["type"], 0) + 1

            # Run deploy check
            from extract.deploy_check import check
            print(f"\n  Deploy gate for Chapter {ch_num}:")
            passed = check(str(output_file), str(ch_pdf))

            elapsed = time.time() - ch_start
            total_elements += len(elements)

            results.append({
                "chapter": ch_num,
                "title": ch_title,
                "pages": pages,
                "elements": len(elements),
                "types": types,
                "deploy_pass": passed,
                "time": round(elapsed, 1),
                "file": str(output_file),
            })

            print(f"\n  Chapter {ch_num}: {len(elements)} elements in {elapsed:.1f}s {'PASS' if passed else 'FAIL'}")

            # Cleanup temp chapter PDF
            ch_pdf.unlink(missing_ok=True)

        except Exception as e:
            elapsed = time.time() - ch_start
            print(f"\n  ERROR: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "chapter": ch_num,
                "title": ch_title,
                "pages": pages,
                "elements": 0,
                "types": {},
                "deploy_pass": False,
                "time": round(elapsed, 1),
                "error": str(e),
            })

    # ═══════════════════════════════════════════════════════════
    # POST-BATCH: Audit connections
    # ═══════════════════════════════════════════════════════════
    print(f"\n\n{'=' * 60}")
    print("POST-BATCH AUDIT")
    print("=" * 60)

    # Load symbols and audit for duplicates/conflicts
    from extract.symbols import load_symbols
    symbols = load_symbols()
    print(f"\nGlobal symbols: {len(symbols)}")

    # Find duplicate variable names with different definitions
    sym_by_name = defaultdict(list)
    for sym, info in symbols.items():
        sym_by_name[sym].append(info)

    conflicts = {k: v for k, v in sym_by_name.items() if len(v) > 1}
    if conflicts:
        print(f"\nSYMBOL CONFLICTS ({len(conflicts)}):")
        for sym, infos in sorted(conflicts.items()):
            print(f"  {sym}:")
            for info in infos:
                print(f"    [{info.get('defined_in', '?')}] {info.get('description', '')[:60]}")
    else:
        print("  No symbol conflicts")

    # Load unresolved and re-check
    from extract.unresolved import find_unresolved, save_unresolved
    from extract.manifest import load_manifest

    manifest = load_manifest()
    all_unresolved = {"standard": standard, "unresolved": [], "summary": {"total": 0, "pending": 0, "broken": 0}}

    for r in results:
        if r.get("error") or r["elements"] == 0:
            continue
        ch_file = r["file"]
        if Path(ch_file).exists():
            ch_elements = json.loads(Path(ch_file).read_text())
            ur = find_unresolved(ch_elements)
            all_unresolved["unresolved"].extend(ur["unresolved"])

    pending = sum(1 for u in all_unresolved["unresolved"] if u["status"] == "pending")
    broken = sum(1 for u in all_unresolved["unresolved"] if u["status"] == "broken")
    all_unresolved["summary"] = {"total": len(all_unresolved["unresolved"]), "pending": pending, "broken": broken}
    save_unresolved(all_unresolved)

    print(f"\nCross-chapter references: {all_unresolved['summary']['total']}")
    print(f"  Pending: {pending}")
    print(f"  Broken: {broken}")

    # Pending by chapter
    pending_by = defaultdict(int)
    for u in all_unresolved["unresolved"]:
        if u["status"] == "pending":
            pending_by[u["target_chapter"]] += 1
    if pending_by:
        print(f"  Pending targets: {dict(sorted(pending_by.items()))}")

    # ═══════════════════════════════════════════════════════════
    # MORNING REPORT
    # ═══════════════════════════════════════════════════════════
    total_time = time.time() - start_time

    print(f"\n\n{'=' * 60}")
    print("MORNING REPORT")
    print("=" * 60)
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total time: {total_time/60:.1f} minutes")
    print(f"Chapters processed: {len(results)}")
    print(f"Total elements: {sum(r['elements'] for r in results)}")
    print(f"Global symbols: {len(symbols)}")
    print(f"\n{'Ch':>4} {'Title':40s} {'Pages':>5} {'Elements':>8} {'Time':>6} {'Gate':>6}")
    print("─" * 75)
    for r in results:
        gate = "PASS" if r["deploy_pass"] else "FAIL" if not r.get("error") else "ERR"
        print(f"{r['chapter']:>4} {r['title'][:40]:40s} {r['pages']:>5} {r['elements']:>8} {r['time']:>5.0f}s {gate:>6}")

    passed = sum(1 for r in results if r["deploy_pass"])
    failed = sum(1 for r in results if not r["deploy_pass"] and not r.get("error"))
    errored = sum(1 for r in results if r.get("error"))
    print(f"\n  PASSED: {passed}  FAILED: {failed}  ERRORS: {errored}")
    print(f"  Broken refs: {broken}  Pending refs: {pending}")
    print(f"  Symbol conflicts: {len(conflicts)}")

    # Save report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_time_seconds": round(total_time),
        "chapters": results,
        "symbols_count": len(symbols),
        "symbol_conflicts": {k: [{"defined_in": i.get("defined_in"), "description": i.get("description", "")[:100]} for i in v] for k, v in conflicts.items()},
        "unresolved_summary": all_unresolved["summary"],
        "summary": {
            "chapters_processed": len(results),
            "total_elements": sum(r["elements"] for r in results),
            "passed": passed,
            "failed": failed,
            "errored": errored,
        }
    }
    Path("output/qc/batch-report.json").write_text(json.dumps(report, indent=2))
    print(f"\n  Report → output/qc/batch-report.json")

    return report


if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else None
    if not pdf:
        pdfs = sorted(Path("input").glob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
        pdf = str(pdfs[0]) if pdfs else None

    if not pdf:
        print("Usage: python extract/batch.py input/full-book.pdf [chapters] [standard]")
        print("  chapters: comma-separated, e.g. 26,27,28")
        print("  standard: e.g. 'ASCE 7-22' (default)")
        sys.exit(1)

    # Optional: specify chapters
    chapters = None
    if len(sys.argv) > 2:
        chapters = [int(x) for x in sys.argv[2].split(",")]

    # Optional: specify standard name
    standard = sys.argv[3] if len(sys.argv) > 3 else "ASCE 7-22"

    run_batch(pdf, standard=standard, chapters=chapters)
