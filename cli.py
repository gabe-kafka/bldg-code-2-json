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


if __name__ == "__main__":
    cli()
