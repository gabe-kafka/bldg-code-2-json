"""
bldg-code-2-json CLI — extract building code PDFs into structured JSON.

Vision-only pipeline: render PDF pages → read with Claude → validate.
See ontology.md for the element specification.
"""

import json
from pathlib import Path
import click


@click.group()
def cli():
    """Convert building code PDFs to machine-readable JSON."""
    pass


@cli.command()
@click.option("--pdf", required=True, type=click.Path(exists=True), help="Path to source PDF")
@click.option("--standard", required=True, help='Standard name, e.g. "ASCE 7-22"')
@click.option("--chapter", required=True, type=int, help="Chapter number")
@click.option("--start-page", default=1, type=int, help="First page (1-indexed)")
@click.option("--end-page", default=None, type=int, help="Last page (inclusive)")
@click.option("--dpi", default=200, type=int, help="Render resolution")
@click.option("--output-dir", default=None, type=click.Path(), help="Image output directory")
def render(pdf, standard, chapter, start_page, end_page, dpi, output_dir):
    """Render PDF pages to images for vision extraction.

    After rendering, open the images in Claude Code to extract elements.
    """
    from extract.pdf_renderer import render_pages

    std_slug = standard.lower().replace(" ", "").replace("-", "")

    if output_dir is None:
        output_dir = f"output/pages/{std_slug}-ch{chapter}"

    click.echo(f"Rendering {standard} Chapter {chapter} pages {start_page}-{end_page or 'end'}...")
    image_paths = render_pages(pdf, output_dir, start_page, end_page, dpi)
    click.echo(f"Rendered {len(image_paths)} pages → {output_dir}/")

    click.echo(f"\nNext: open the images in Claude Code and extract elements.")
    click.echo(f"Save the result as JSON, then validate with:")
    click.echo(f"  python cli.py validate --file <output.json>")


@cli.command()
@click.option("--file", "input_file", required=True, type=click.Path(exists=True), help="Extracted JSON to validate")
@click.option("--output", default=None, type=click.Path(), help="Report output path")
def validate(input_file, output):
    """Validate extracted JSON: schema check + post-process + calibration."""
    from extract.post_processor import post_process
    from qc.schema_validator import validate_chapter, load_schema
    from qc.calibration import calibration_report
    from extract.gold_standard import load_gold_elements

    with open(input_file) as f:
        elements = json.load(f)

    click.echo(f"Loaded {len(elements)} elements from {input_file}")

    # Post-process (deterministic cleanup)
    processed = post_process(elements)
    pp_fixes = sum(1 for a, b in zip(elements, processed) if a != b)
    if pp_fixes:
        click.echo(f"Post-processor: {pp_fixes} elements cleaned up")

    # Schema validation
    schema_results = validate_chapter(processed)
    passed = schema_results["passed"]
    total = schema_results["total"]
    click.echo(f"Schema: {passed}/{total} valid")

    if schema_results["errors"]:
        click.echo(f"\nFailures:")
        for err in schema_results["errors"][:10]:
            click.echo(f"  {err['id']}: {err['errors'][0][:100]}")

    # Cross-references
    element_ids = {el["id"] for el in processed}
    total_refs = 0
    resolved = 0
    for el in processed:
        for ref in el.get("cross_references", []):
            total_refs += 1
            if ref in element_ids:
                resolved += 1
    click.echo(f"Cross-refs: {resolved}/{total_refs} resolved within chapter")

    # Calibration against gold set
    gold = load_gold_elements()
    cal = None
    if gold:
        cal = calibration_report(processed, gold)
        agg = cal["aggregate"]
        click.echo(f"Calibration: {agg['accuracy']:.1%} accuracy ({agg['elements_compared']} compared, {agg['elements_missing']} missing)")
    else:
        click.echo("Calibration: no gold elements found — skipping")

    # Type breakdown
    from collections import Counter
    types = Counter(el["type"] for el in processed)
    click.echo(f"\nTypes: {', '.join(f'{t}={c}' for t, c in types.most_common())}")

    # Write report
    if output is None:
        stem = Path(input_file).stem
        output = f"output/qc/{stem}-report.json"

    report = {
        "source": str(input_file),
        "total_elements": total,
        "post_processor_fixes": pp_fixes,
        "schema": schema_results,
        "cross_references": {"total": total_refs, "resolved": resolved},
        "types": dict(types),
    }
    if cal:
        report["calibration"] = cal

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    click.echo(f"\nReport → {out_path}")

    # Write cleaned output if post-processor made changes
    if pp_fixes > 0:
        clean_path = Path(input_file).parent / f"{Path(input_file).stem}-clean.json"
        with open(clean_path, "w") as f:
            json.dump(processed, f, indent=2)
        click.echo(f"Cleaned JSON → {clean_path}")


@cli.command()
@click.option("--run-a", required=True, type=click.Path(exists=True), help="First extraction JSON (e.g., Claude)")
@click.option("--run-b", required=True, type=click.Path(exists=True), help="Second extraction JSON (e.g., Codex)")
@click.option("--label-a", default="claude", help="Label for first run")
@click.option("--label-b", default="codex", help="Label for second run")
@click.option("--output", default=None, type=click.Path(), help="Comparison report path")
def compare(run_a, run_b, label_a, label_b, output):
    """Compare two extraction runs and surface disagreements."""
    from qc.compare import compare_extractions

    with open(run_a) as f:
        elements_a = json.load(f)
    with open(run_b) as f:
        elements_b = json.load(f)

    click.echo(f"Comparing {label_a} ({len(elements_a)} elements) vs {label_b} ({len(elements_b)} elements)")

    result = compare_extractions(elements_a, elements_b, label_a, label_b)
    s = result["summary"]

    click.echo(f"\n  Matched by id:       {s['matched_by_id']}")
    click.echo(f"  Matched by citation: {s['matched_by_citation']}")
    click.echo(f"  Exact agreed:        {s['agreed']} elements")
    click.echo(f"  Helper-only diffs:   {s['helper_only']} elements")
    click.echo(f"  Auth disagreements:  {s['authoritative_disagreed']} elements")
    click.echo(f"  Only {label_a}: {s['only_a']}")
    click.echo(f"  Only {label_b}: {s['only_b']}")
    click.echo(f"  Exact agreement:     {s['agreement_rate']:.1%}")
    click.echo(f"  Auth agreement:      {s['authoritative_agreement_rate']:.1%}")

    if result["authoritative_disagreed"]:
        click.echo(f"\nTop authoritative disagreements:")
        for d in result["authoritative_disagreed"][:15]:
            type_info = ""
            if d["type_a"] != d["type_b"]:
                type_info = f" (type: {d['type_a']} vs {d['type_b']})"
            basis_info = ""
            if d.get("match_basis") == "citation":
                basis_info = f" [matched by citation: {d['id_a']} <> {d['id_b']}]"
            click.echo(f"  {d['id']}{type_info}{basis_info}")
            for f in d["fields"][:3]:
                field = f["field"]
                if "only_a" in f:
                    click.echo(f"    {field}: {label_a}={f['only_a']}, {label_b}={f['only_b']}")
                else:
                    click.echo(f"    {field}: {label_a}={f['a']}, {label_b}={f['b']}")
    elif result["helper_only"]:
        click.echo(f"\nNo authoritative disagreements. Top helper-only differences:")
        for d in result["helper_only"][:10]:
            basis_info = ""
            if d.get("match_basis") == "citation":
                basis_info = f" [matched by citation: {d['id_a']} <> {d['id_b']}]"
            click.echo(f"  {d['id']}{basis_info}")
            for f in d["fields"][:3]:
                field = f["field"]
                if "only_a" in f:
                    click.echo(f"    {field}: {label_a}={f['only_a']}, {label_b}={f['only_b']}")
                else:
                    click.echo(f"    {field}: {label_a}={f['a']}, {label_b}={f['b']}")

    if output is None:
        output = f"output/qc/compare-{label_a}-vs-{label_b}.json"

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    click.echo(f"\nFull report → {out_path}")


@cli.command()
@click.option("--compare", default="output/qc/compare-claude-vs-codex.json", type=click.Path(exists=True), help="Comparison report JSON")
@click.option("--run-a", required=True, type=click.Path(exists=True), help="First extraction JSON")
@click.option("--run-b", required=True, type=click.Path(exists=True), help="Second extraction JSON")
@click.option("--pages-dir", required=True, type=click.Path(exists=True), help="Rendered page images directory")
@click.option("--port", default=8787, type=int, help="Server port")
def review(compare, run_a, run_b, pages_dir, port):
    """Launch visual review tool for resolving extraction disagreements."""
    from review.server import start_server

    start_server(compare, run_a, run_b, pages_dir, port)


@cli.command()
@click.option("--base", required=True, type=click.Path(exists=True), help="Base extraction JSON")
@click.option("--alt", required=True, type=click.Path(exists=True), help="Alternative extraction JSON")
@click.option("--decisions", default="output/qc/human-decisions.json", type=click.Path(exists=True), help="Human decisions JSON")
@click.option("--output", default=None, type=click.Path(), help="Merged output path")
def merge(base, alt, decisions, output):
    """Merge human review decisions into a resolved extraction."""
    from review.merge import merge_decisions

    if output is None:
        output = "output/runs/merged-ch26.json"

    total, applied = merge_decisions(base, alt, decisions, output)
    click.echo(f"Merged {applied} field decisions across {total} elements → {output}")


@cli.command()
@click.option("--pages-dir", required=True, type=click.Path(exists=True), help="Rendered page images directory")
@click.option("--port", default=8788, type=int, help="Server port")
@click.option("--output", default=None, type=click.Path(), help="Classifications output path")
def classify(pages_dir, port, output):
    """Launch region classification tool (Phase 1: human selects structured/linked/skipped)."""
    from review.classify_server import start_classify_server

    start_classify_server(pages_dir, port, output)


@cli.command(name="extract")
@click.option("--pdf", required=True, type=click.Path(exists=True), help="Path to source PDF")
@click.option("--standard", required=True, help='Standard name, e.g. "ASCE 7-22"')
@click.option("--chapter", required=True, type=int, help="Chapter number")
@click.option("--output", default=None, type=click.Path(), help="Output JSON path")
def extract_cmd(pdf, standard, chapter, output):
    """Extract building code elements using hybrid pipeline (Docling + PyMuPDF)."""
    from extract.pipeline_v3 import run_v3 as run_hybrid

    click.echo(f"Extracting {standard} Chapter {chapter}...")
    elements, markdown = run_hybrid(pdf, standard=standard, chapter=chapter)

    # Fix null fields for schema compliance
    for e in elements:
        if e.get("description") is None:
            e["description"] = ""
        src = e.get("source", {})
        if src.get("citation") is None:
            src["citation"] = f"Section {src.get('section', '')}"

    if output is None:
        std_slug = standard.lower().replace(" ", "").replace("-", "")
        output = f"output/runs/{std_slug}-ch{chapter}-hybrid.json"

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(elements, f, indent=2)

    # Save markdown too
    md_path = out_path.with_suffix(".md")
    md_path.write_text(markdown)

    # Type breakdown
    types = {}
    for e in elements:
        types[e["type"]] = types.get(e["type"], 0) + 1

    click.echo(f"\n{len(elements)} elements → {out_path}")
    click.echo(f"Markdown → {md_path}")
    click.echo(f"Types: {', '.join(f'{t}={c}' for t, c in sorted(types.items(), key=lambda x: -x[1]))}")
    click.echo(f"\nValidate: python cli.py validate --file {out_path}")


if __name__ == "__main__":
    cli()
