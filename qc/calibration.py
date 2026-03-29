"""
Calibration scoring — compares extracted elements against gold standard references.

Deterministic, no API calls. Uses field-level comparison with numeric tolerance
to produce per-element and aggregate accuracy scores.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


def score_against_gold(
    extracted: list[dict],
    gold: list[dict],
    rtol: float = 1e-3,
) -> list[dict]:
    """Compare extracted elements against gold standard elements.

    For each gold element, finds matching extracted element by ID and compares
    fields with numeric tolerance.

    Returns:
        List of per-element result dicts with keys:
        element_id, type_match, id_match, data_match, xref_match, score, details
    """
    extracted_by_id = {el.get("id"): el for el in extracted}
    results = []

    for gold_el in gold:
        gid = gold_el.get("id", "UNKNOWN")
        ext_el = extracted_by_id.get(gid)

        if ext_el is None:
            results.append({
                "element_id": gid,
                "id_match": False,
                "type_match": False,
                "data_match": 0.0,
                "xref_match": False,
                "score": 0.0,
                "details": "No matching extracted element found",
            })
            continue

        id_match = ext_el.get("id") == gold_el.get("id")
        type_match = ext_el.get("type") == gold_el.get("type")
        data_match = _compare_data(ext_el.get("data", {}), gold_el.get("data", {}), rtol)
        xref_match = _compare_xrefs(ext_el, gold_el)

        # Aggregate score: weighted average of field matches
        score = (
            0.1 * float(id_match)
            + 0.2 * float(type_match)
            + 0.5 * data_match
            + 0.2 * float(xref_match)
        )

        details = []
        if not type_match:
            details.append(f"type: expected {gold_el.get('type')}, got {ext_el.get('type')}")
        if data_match < 1.0:
            details.append(f"data_match: {data_match:.2f}")
        if not xref_match:
            details.append("cross_references differ")

        results.append({
            "element_id": gid,
            "id_match": id_match,
            "type_match": type_match,
            "data_match": round(data_match, 4),
            "xref_match": xref_match,
            "score": round(score, 4),
            "details": "; ".join(details) if details else "perfect match",
        })

    return results


def calibration_report(
    extracted: list[dict],
    gold: list[dict],
    rtol: float = 1e-3,
) -> dict:
    """Compute aggregate calibration stats.

    Returns:
        {
            per_element: [...results from score_against_gold...],
            aggregate: {accuracy, type_match_rate, elements_compared, elements_missing},
            timestamp: ISO 8601 string,
        }
    """
    per_element = score_against_gold(extracted, gold, rtol)

    compared = [r for r in per_element if r["id_match"]]
    missing = [r for r in per_element if not r["id_match"]]

    if compared:
        accuracy = sum(r["score"] for r in compared) / len(compared)
        type_match_rate = sum(1 for r in compared if r["type_match"]) / len(compared)
    else:
        accuracy = 0.0
        type_match_rate = 0.0

    return {
        "per_element": per_element,
        "aggregate": {
            "accuracy": round(accuracy, 4),
            "type_match_rate": round(type_match_rate, 4),
            "elements_compared": len(compared),
            "elements_missing": len(missing),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Internal comparison helpers
# ---------------------------------------------------------------------------


def _compare_data(ext_data: dict, gold_data: dict, rtol: float) -> float:
    """Field-level data comparison returning a 0-1 match score."""
    if not gold_data:
        return 1.0 if not ext_data else 0.0

    gold_keys = set(gold_data.keys())
    ext_keys = set(ext_data.keys())

    if not gold_keys:
        return 1.0

    matches = 0
    total = len(gold_keys)

    for key in gold_keys:
        if key not in ext_data:
            continue
        if _values_match(ext_data[key], gold_data[key], rtol):
            matches += 1

    return matches / total if total > 0 else 1.0


def _values_match(ext_val, gold_val, rtol: float) -> bool:
    """Recursively compare values with numeric tolerance."""
    if gold_val is None:
        return ext_val is None

    if isinstance(gold_val, (int, float)) and isinstance(ext_val, (int, float)):
        if gold_val == 0:
            return abs(ext_val) <= rtol
        return abs(ext_val - gold_val) / abs(gold_val) <= rtol

    if isinstance(gold_val, str):
        return isinstance(ext_val, str) and ext_val == gold_val

    if isinstance(gold_val, bool):
        return ext_val == gold_val

    if isinstance(gold_val, list) and isinstance(ext_val, list):
        if len(gold_val) != len(ext_val):
            return False
        return all(_values_match(e, g, rtol) for e, g in zip(ext_val, gold_val))

    if isinstance(gold_val, dict) and isinstance(ext_val, dict):
        if set(gold_val.keys()) != set(ext_val.keys()):
            return False
        return all(
            _values_match(ext_val.get(k), v, rtol)
            for k, v in gold_val.items()
        )

    return ext_val == gold_val


def _compare_xrefs(ext_el: dict, gold_el: dict) -> bool:
    """Order-insensitive set comparison of cross_references."""
    ext_refs = set(ext_el.get("cross_references", []))
    gold_refs = set(gold_el.get("cross_references", []))
    return ext_refs == gold_refs
