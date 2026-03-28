"""
bldg-code-2-json CLI — extract building code PDFs into structured JSON.
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
@click.option("--start-page", default=1, type=int, help="First page of chapter (1-indexed)")
@click.option("--end-page", default=None, type=int, help="Last page of chapter (inclusive)")
@click.option("--output", default=None, type=click.Path(), help="Output JSON path (default: output/raw/)")
def extract(pdf, standard, chapter, start_page, end_page, output):
    """Extract a chapter from a building code PDF into raw JSON."""
    from extract.llm_structurer import extract_chapter

    click.echo(f"Extracting {standard} Chapter {chapter} from {pdf}...")
    click.echo(f"Pages {start_page} to {end_page or 'end'}")

    elements = extract_chapter(
        pdf_path=pdf,
        standard=standard,
        chapter=chapter,
        start_page=start_page,
        end_page=end_page,
    )

    if output is None:
        std_slug = standard.lower().replace(" ", "").replace("-", "")
        output = f"output/raw/{std_slug}-ch{chapter}.json"

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(elements, f, indent=2)

    click.echo(f"Extracted {len(elements)} elements → {out_path}")


@cli.command()
@click.option("--file", "input_file", required=True, type=click.Path(exists=True), help="Raw JSON to validate")
@click.option("--pdf", default=None, type=click.Path(exists=True), help="Source PDF for completeness/spot checks")
@click.option("--start-page", default=1, type=int, help="Chapter start page in PDF")
@click.option("--end-page", default=None, type=int, help="Chapter end page in PDF")
@click.option("--spot-check-size", default=10, type=int, help="Number of elements to spot check")
@click.option("--output", default=None, type=click.Path(), help="QC report output path")
def qc(input_file, pdf, start_page, end_page, spot_check_size, output):
    """Run QC checks on extracted JSON."""
    from qc.schema_validator import validate_chapter
    from qc.completeness import check_completeness
    from qc.spot_check import spot_check

    with open(input_file) as f:
        elements = json.load(f)

    click.echo(f"Running QC on {len(elements)} elements...")

    # Schema validation
    click.echo("  Schema validation...")
    schema_results = validate_chapter(elements)
    click.echo(f"    {schema_results['passed']}/{schema_results['total']} passed")

    report = {"schema": schema_results}

    # Completeness check (requires PDF)
    if pdf:
        from extract.pdf_parser import parse_pdf

        click.echo("  Completeness check...")
        pages = parse_pdf(pdf, start_page=start_page, end_page=end_page)
        completeness = check_completeness(elements, pages)
        click.echo(f"    Overall coverage: {completeness['overall_coverage']*100:.1f}%")
        report["completeness"] = completeness

        # Spot check
        if spot_check_size > 0:
            click.echo(f"  Spot checking {spot_check_size} elements...")
            spot_results = spot_check(elements, pages, sample_size=spot_check_size)
            click.echo(f"    Average accuracy: {spot_results['average_score']*100:.1f}%")
            report["spot_check"] = spot_results

    # Cross-reference check
    click.echo("  Cross-reference check...")
    element_ids = {el["id"] for el in elements}
    xref_issues = []
    for el in elements:
        for ref in el.get("cross_references", []):
            if ref not in element_ids:
                xref_issues.append({"element": el["id"], "missing_ref": ref})
    report["cross_references"] = {
        "total_refs": sum(len(el.get("cross_references", [])) for el in elements),
        "unresolved": len(xref_issues),
        "issues": xref_issues,
    }
    click.echo(f"    {len(xref_issues)} unresolved references")

    # Write report
    if output is None:
        base = Path(input_file).stem
        output = f"output/qc/{base}-qc-report.json"

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    click.echo(f"\nQC report → {out_path}")


@cli.command()
@click.option("--pdf", required=True, type=click.Path(exists=True), help="Path to source PDF")
@click.option("--standard", required=True, help='Standard name, e.g. "ASCE 7-22"')
@click.option("--chapter", required=True, type=int, help="Chapter number")
@click.option("--start-page", default=1, type=int, help="First page of chapter (1-indexed)")
@click.option("--end-page", default=None, type=int, help="Last page of chapter (inclusive)")
@click.option("--spot-check-size", default=10, type=int, help="Number of elements to spot check")
def run(pdf, standard, chapter, start_page, end_page, spot_check_size):
    """Full pipeline: extract + QC in one command."""
    from extract.llm_structurer import extract_chapter
    from extract.pdf_parser import parse_pdf
    from qc.schema_validator import validate_chapter
    from qc.completeness import check_completeness
    from qc.spot_check import spot_check

    std_slug = standard.lower().replace(" ", "").replace("-", "")

    # --- Extract ---
    click.echo(f"=== EXTRACT: {standard} Chapter {chapter} ===")
    elements = extract_chapter(pdf, standard, chapter, start_page, end_page)

    raw_path = Path(f"output/raw/{std_slug}-ch{chapter}.json")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w") as f:
        json.dump(elements, f, indent=2)
    click.echo(f"Extracted {len(elements)} elements → {raw_path}")

    # --- QC ---
    click.echo(f"\n=== QC ===")
    schema_results = validate_chapter(elements)
    click.echo(f"Schema: {schema_results['passed']}/{schema_results['total']} passed")

    pages = parse_pdf(pdf, start_page=start_page, end_page=end_page)
    completeness = check_completeness(elements, pages)
    click.echo(f"Completeness: {completeness['overall_coverage']*100:.1f}%")

    spot_results = None
    if spot_check_size > 0:
        spot_results = spot_check(elements, pages, sample_size=spot_check_size)
        click.echo(f"Spot check: {spot_results['average_score']*100:.1f}% accuracy")

    # Cross-refs
    element_ids = {el["id"] for el in elements}
    unresolved = sum(
        1 for el in elements
        for ref in el.get("cross_references", [])
        if ref not in element_ids
    )
    click.echo(f"Cross-refs: {unresolved} unresolved")

    report = {
        "schema": schema_results,
        "completeness": completeness,
        "cross_references": {"unresolved": unresolved},
    }
    if spot_results:
        report["spot_check"] = spot_results

    qc_path = Path(f"output/qc/{std_slug}-ch{chapter}-qc-report.json")
    qc_path.parent.mkdir(parents=True, exist_ok=True)
    with open(qc_path, "w") as f:
        json.dump(report, f, indent=2)

    # --- Validated output ---
    if schema_results["failed"] == 0:
        val_path = Path(f"output/validated/{std_slug}-ch{chapter}.json")
        val_path.parent.mkdir(parents=True, exist_ok=True)
        with open(val_path, "w") as f:
            json.dump(elements, f, indent=2)
        click.echo(f"\nAll elements valid → {val_path}")
    else:
        click.echo(f"\n{schema_results['failed']} elements failed schema validation — skipping validated output")

    click.echo(f"QC report → {qc_path}")


if __name__ == "__main__":
    cli()
