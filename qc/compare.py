"""
Cross-model comparison — diffs two extraction runs element-by-element.

Designed for comparing outputs from different models (e.g., Claude vs Codex)
reading the same PDF pages. Surfaces disagreements for human review.
"""

from __future__ import annotations

import math


def compare_extractions(
    run_a: list[dict],
    run_b: list[dict],
    label_a: str = "A",
    label_b: str = "B",
    rtol: float = 1e-3,
) -> dict:
    """Compare two extraction runs element-by-element.

    Returns:
        {
            agreed: [{id, type, score}],           # both match
            disagreed: [{id, fields, a, b}],        # both have it, data differs
            only_a: [ids],                          # only in run A
            only_b: [ids],                          # only in run B
            summary: {agreed, disagreed, only_a, only_b, agreement_rate},
        }
    """
    a_by_id = {el.get("id"): el for el in run_a}
    b_by_id = {el.get("id"): el for el in run_b}

    all_ids = sorted(set(a_by_id.keys()) | set(b_by_id.keys()))

    agreed = []
    disagreed = []
    only_a = []
    only_b = []

    for eid in all_ids:
        el_a = a_by_id.get(eid)
        el_b = b_by_id.get(eid)

        if el_a and not el_b:
            only_a.append(eid)
            continue
        if el_b and not el_a:
            only_b.append(eid)
            continue

        # Both exist — compare fields
        diffs = _diff_elements(el_a, el_b, rtol)

        if not diffs:
            agreed.append({"id": eid, "type": el_a.get("type")})
        else:
            disagreed.append({
                "id": eid,
                "type_a": el_a.get("type"),
                "type_b": el_b.get("type"),
                "fields": diffs,
            })

    total_compared = len(agreed) + len(disagreed)
    agreement_rate = len(agreed) / total_compared if total_compared > 0 else 0.0

    return {
        "labels": {label_a: len(run_a), label_b: len(run_b)},
        "agreed": agreed,
        "disagreed": disagreed,
        "only_a": only_a,
        "only_b": only_b,
        "summary": {
            "agreed": len(agreed),
            "disagreed": len(disagreed),
            "only_a": len(only_a),
            "only_b": len(only_b),
            "agreement_rate": round(agreement_rate, 4),
        },
    }


def _diff_elements(a: dict, b: dict, rtol: float) -> list[dict]:
    """Compare two elements, return list of field-level differences."""
    diffs = []

    # Type
    if a.get("type") != b.get("type"):
        diffs.append({"field": "type", "a": a.get("type"), "b": b.get("type")})

    # Title
    if a.get("title") != b.get("title"):
        diffs.append({"field": "title", "a": a.get("title"), "b": b.get("title")})

    # Data — deep compare with numeric tolerance
    data_diffs = _diff_data(a.get("data", {}), b.get("data", {}), rtol)
    if data_diffs:
        diffs.extend(data_diffs)

    # Cross-references (order-insensitive)
    xref_a = set(a.get("cross_references", []))
    xref_b = set(b.get("cross_references", []))
    if xref_a != xref_b:
        diffs.append({
            "field": "cross_references",
            "only_a": sorted(xref_a - xref_b),
            "only_b": sorted(xref_b - xref_a),
        })

    return diffs


def _diff_data(a: dict, b: dict, rtol: float, prefix: str = "data") -> list[dict]:
    """Recursively diff data dicts, returning field-level differences."""
    diffs = []
    all_keys = sorted(set(a.keys()) | set(b.keys()))

    for key in all_keys:
        path = f"{prefix}.{key}"
        val_a = a.get(key)
        val_b = b.get(key)

        if key not in a:
            diffs.append({"field": path, "a": "(missing)", "b": _summarize(val_b)})
            continue
        if key not in b:
            diffs.append({"field": path, "a": _summarize(val_a), "b": "(missing)"})
            continue

        if not _values_equal(val_a, val_b, rtol):
            diffs.append({"field": path, "a": _summarize(val_a), "b": _summarize(val_b)})

    return diffs


def _values_equal(a, b, rtol: float) -> bool:
    """Compare values with numeric tolerance."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == 0 and b == 0:
            return True
        if a == 0:
            return abs(b) <= rtol
        return abs(a - b) / max(abs(a), abs(b)) <= rtol

    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()

    if isinstance(a, bool) and isinstance(b, bool):
        return a == b

    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_values_equal(va, vb, rtol) for va, vb in zip(a, b))

    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_values_equal(a[k], b[k], rtol) for k in a)

    return a == b


def _summarize(val, max_len: int = 80) -> str:
    """Summarize a value for display in diff output."""
    if val is None:
        return "null"
    if isinstance(val, str):
        if len(val) > max_len:
            return val[:max_len] + "..."
        return val
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        return f"[{len(val)} items]"
    if isinstance(val, dict):
        return f"{{{len(val)} keys}}"
    return str(val)[:max_len]
