"""
Objective function — scores an extraction run on [0, 1].

Composite of four signals, weighted by importance:
  - schema_validity (0.2): do elements conform to the JSON schema?
  - completeness   (0.3): are all sections/tables/figures accounted for?
  - accuracy       (0.4): are extracted values correct? (spot check)
  - xref_resolve   (0.1): do cross-references point to real element IDs?

A perfect run scores 1.0. The optimizer tries to maximize this.
"""

from __future__ import annotations
import json
from pathlib import Path

from qc.schema_validator import validate_chapter
from qc.completeness import check_completeness
from qc.spot_check import spot_check
from extract.pdf_parser import PageExtraction


WEIGHTS = {
    "schema_validity": 0.2,
    "completeness": 0.3,
    "accuracy": 0.4,
    "xref_resolve": 0.1,
}


def score_run(
    elements: list[dict],
    pages: list[PageExtraction],
    spot_check_size: int = 10,
    seed: int = 42,
) -> dict:
    """Score an extraction run.

    Args:
        elements: Extracted elements from one pipeline run.
        pages: Parsed PDF pages (for completeness + spot check).
        spot_check_size: How many elements to sample for accuracy.
        seed: Random seed for reproducible spot checks.

    Returns:
        {
            "composite_score": float,  # 0-1, the objective
            "components": {
                "schema_validity": float,
                "completeness": float,
                "accuracy": float,
                "xref_resolve": float,
            },
            "details": {
                "schema": {...},
                "completeness": {...},
                "spot_check": {...},
                "xref": {...},
            },
            "failure_analysis": [
                {"category": str, "description": str, "element_ids": list}
            ]
        }
    """
    # --- Schema validity ---
    schema_result = validate_chapter(elements)
    schema_score = schema_result["passed"] / schema_result["total"] if schema_result["total"] > 0 else 0.0

    # --- Completeness ---
    completeness_result = check_completeness(elements, pages)
    completeness_score = completeness_result["overall_coverage"]

    # --- Accuracy (spot check) ---
    extractable = [el for el in elements if el.get("type") != "skipped_figure"]
    if extractable and spot_check_size > 0:
        spot_result = spot_check(extractable, pages, sample_size=spot_check_size, seed=seed)
        accuracy_score = spot_result["average_score"]
    else:
        spot_result = {"sample_size": 0, "average_score": 0.0, "results": []}
        accuracy_score = 0.0

    # --- Cross-reference resolution ---
    element_ids = {el["id"] for el in elements}
    total_refs = 0
    resolved_refs = 0
    for el in elements:
        for ref in el.get("cross_references", []):
            total_refs += 1
            if ref in element_ids:
                resolved_refs += 1
    xref_score = resolved_refs / total_refs if total_refs > 0 else 1.0

    # --- Composite ---
    composite = (
        WEIGHTS["schema_validity"] * schema_score
        + WEIGHTS["completeness"] * completeness_score
        + WEIGHTS["accuracy"] * accuracy_score
        + WEIGHTS["xref_resolve"] * xref_score
    )

    # --- Failure analysis ---
    failures = _analyze_failures(schema_result, completeness_result, spot_result, element_ids, elements)

    return {
        "composite_score": round(composite, 4),
        "components": {
            "schema_validity": round(schema_score, 4),
            "completeness": round(completeness_score, 4),
            "accuracy": round(accuracy_score, 4),
            "xref_resolve": round(xref_score, 4),
        },
        "details": {
            "schema": schema_result,
            "completeness": completeness_result,
            "spot_check": spot_result,
            "xref": {"total": total_refs, "resolved": resolved_refs},
        },
        "failure_analysis": failures,
    }


def _analyze_failures(schema_result, completeness_result, spot_result, element_ids, elements) -> list[dict]:
    """Categorize failures for the optimizer to act on."""
    failures = []

    # Schema failures
    if schema_result["errors"]:
        failures.append({
            "category": "schema_violation",
            "description": f"{len(schema_result['errors'])} elements failed schema validation",
            "element_ids": [e["id"] for e in schema_result["errors"]],
            "details": schema_result["errors"][:5],  # cap for prompt size
        })

    # Missing sections
    missing_sections = completeness_result.get("sections", {}).get("missing", [])
    if missing_sections:
        failures.append({
            "category": "missing_sections",
            "description": f"{len(missing_sections)} sections not extracted",
            "element_ids": [],
            "details": missing_sections[:10],
        })

    # Missing tables
    missing_tables = completeness_result.get("tables", {}).get("missing", [])
    if missing_tables:
        failures.append({
            "category": "missing_tables",
            "description": f"{len(missing_tables)} tables not extracted",
            "element_ids": [],
            "details": missing_tables,
        })

    # Accuracy failures
    inaccurate = [r for r in spot_result.get("results", []) if not r.get("accurate", True)]
    if inaccurate:
        failures.append({
            "category": "inaccurate_extraction",
            "description": f"{len(inaccurate)} spot-checked elements had accuracy issues",
            "element_ids": [r["id"] for r in inaccurate],
            "details": [{"id": r["id"], "issues": r["issues"]} for r in inaccurate[:5]],
        })

    # Unresolved xrefs
    unresolved = []
    for el in elements:
        for ref in el.get("cross_references", []):
            if ref not in element_ids:
                unresolved.append({"element": el["id"], "missing_ref": ref})
    if unresolved:
        failures.append({
            "category": "unresolved_xrefs",
            "description": f"{len(unresolved)} cross-references point to non-existent elements",
            "element_ids": list({u["element"] for u in unresolved}),
            "details": unresolved[:10],
        })

    return failures
