"""
Cross-model comparison — diffs two extraction runs element-by-element.

Designed for comparing outputs from different models (e.g., Claude vs Codex)
reading the same PDF pages. Surfaces disagreements for human review.
"""

from __future__ import annotations

from collections import Counter


def compare_extractions(
    run_a: list[dict],
    run_b: list[dict],
    label_a: str = "A",
    label_b: str = "B",
    rtol: float = 1e-3,
) -> dict:
    """Compare two extraction runs element-by-element.

    Comparison policy:
    - Match by exact element ID first.
    - For unmatched elements, match by exact official source identifier when
      uniquely available: (type, standard, chapter, section, citation).
    - Preserve exact comparison for authoritative fields.
    - Separate helper/descriptive drift from authoritative disagreements.
    """
    pairs, only_a, only_b = _match_elements(run_a, run_b)

    agreed = []
    helper_only = []
    authoritative_disagreed = []

    for pair in pairs:
        diffs = _diff_elements(pair["a"], pair["b"], rtol)

        if not diffs:
            agreed.append({
                "id": pair["display_id"],
                "id_a": pair["a"].get("id"),
                "id_b": pair["b"].get("id"),
                "type": pair["a"].get("type"),
                "match_basis": pair["match_basis"],
            })
            continue

        authoritative = [d for d in diffs if d["severity"] == "authoritative"]
        entry = {
            "id": pair["display_id"],
            "id_a": pair["a"].get("id"),
            "id_b": pair["b"].get("id"),
            "type_a": pair["a"].get("type"),
            "type_b": pair["b"].get("type"),
            "match_basis": pair["match_basis"],
            "fields": diffs,
        }

        if authoritative:
            authoritative_disagreed.append(entry)
        else:
            helper_only.append(entry)

    disagreed = authoritative_disagreed + helper_only
    matched_total = len(agreed) + len(disagreed)
    exact_agreement_rate = len(agreed) / matched_total if matched_total > 0 else 0.0
    authoritative_agreement_rate = (len(agreed) + len(helper_only)) / matched_total if matched_total > 0 else 0.0

    match_basis_counts = Counter(pair["match_basis"] for pair in pairs)

    return {
        "labels": {label_a: len(run_a), label_b: len(run_b)},
        "agreed": agreed,
        "helper_only": helper_only,
        "authoritative_disagreed": authoritative_disagreed,
        "disagreed": disagreed,
        "only_a": sorted(only_a),
        "only_b": sorted(only_b),
        "summary": {
            "matched_total": matched_total,
            "matched_by_id": match_basis_counts.get("id", 0),
            "matched_by_citation": match_basis_counts.get("citation", 0),
            "agreed": len(agreed),
            "helper_only": len(helper_only),
            "authoritative_disagreed": len(authoritative_disagreed),
            "disagreed": len(disagreed),
            "only_a": len(only_a),
            "only_b": len(only_b),
            "agreement_rate": round(exact_agreement_rate, 4),
            "authoritative_agreement_rate": round(authoritative_agreement_rate, 4),
        },
    }


def _match_elements(run_a: list[dict], run_b: list[dict]) -> tuple[list[dict], set[str], set[str]]:
    """Match elements by exact ID first, then by exact official citation."""
    a_by_id = {el.get("id"): el for el in run_a}
    b_by_id = {el.get("id"): el for el in run_b}

    pairs = []
    matched_a: set[str] = set()
    matched_b: set[str] = set()

    shared_ids = sorted(set(a_by_id.keys()) & set(b_by_id.keys()))
    for eid in shared_ids:
        pairs.append({
            "a": a_by_id[eid],
            "b": b_by_id[eid],
            "match_basis": "id",
            "display_id": eid,
        })
        matched_a.add(eid)
        matched_b.add(eid)

    # Citation fallback for remaining elements.
    a_remaining = [el for el in run_a if el.get("id") not in matched_a]
    b_remaining = [el for el in run_b if el.get("id") not in matched_b]

    a_by_key = _index_unique_by_citation(a_remaining)
    b_by_key = _index_unique_by_citation(b_remaining)

    for key in sorted(set(a_by_key.keys()) & set(b_by_key.keys())):
        el_a = a_by_key[key]
        el_b = b_by_key[key]
        pairs.append({
            "a": el_a,
            "b": el_b,
            "match_basis": "citation",
            "display_id": el_a.get("source", {}).get("citation") or f"{el_a.get('id')} <> {el_b.get('id')}",
        })
        matched_a.add(el_a.get("id"))
        matched_b.add(el_b.get("id"))

    only_a = set(a_by_id.keys()) - matched_a
    only_b = set(b_by_id.keys()) - matched_b
    return pairs, only_a, only_b


def _index_unique_by_citation(elements: list[dict]) -> dict[tuple, dict]:
    """Index elements by unique exact official citation key."""
    buckets: dict[tuple, list[dict]] = {}
    for el in elements:
        key = _citation_key(el)
        if key is None:
            continue
        buckets.setdefault(key, []).append(el)

    return {key: vals[0] for key, vals in buckets.items() if len(vals) == 1}


def _citation_key(el: dict) -> tuple | None:
    """Return a citation-based identity key when exact official identifiers exist."""
    source = el.get("source", {})
    citation = _norm_text(source.get("citation"))
    section = _norm_text(source.get("section"))
    standard = _norm_text(source.get("standard"))
    chapter = source.get("chapter")
    el_type = el.get("type")

    if not citation or not section or not standard or chapter is None or not el_type:
        return None
    return (el_type, standard, chapter, section, citation)


def _diff_elements(a: dict, b: dict, rtol: float) -> list[dict]:
    """Compare two elements, return list of field-level differences."""
    diffs = []
    el_type = a.get("type") or b.get("type")

    # Type
    if a.get("type") != b.get("type"):
        diffs.append({
            "field": "type",
            "a": a.get("type"),
            "b": b.get("type"),
            "severity": "authoritative",
        })

    diffs.extend(_diff_source(a.get("source", {}), b.get("source", {}), rtol))

    # Title
    if a.get("title") != b.get("title"):
        diffs.append({
            "field": "title",
            "a": a.get("title"),
            "b": b.get("title"),
            "severity": "helper",
        })

    if a.get("description") != b.get("description"):
        diffs.append({
            "field": "description",
            "a": _summarize(a.get("description")),
            "b": _summarize(b.get("description")),
            "severity": "descriptive",
        })

    # Data — deep compare with numeric tolerance
    data_diffs = _diff_data(a.get("data", {}), b.get("data", {}), el_type, rtol)
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
            "severity": "helper",
        })

    return sorted(diffs, key=_diff_sort_key)


def _diff_source(a: dict, b: dict, rtol: float, prefix: str = "source") -> list[dict]:
    """Diff source metadata, preserving authoritative citation drift."""
    diffs = []
    all_keys = sorted(set(a.keys()) | set(b.keys()))

    for key in all_keys:
        path = f"{prefix}.{key}"
        val_a = a.get(key)
        val_b = b.get(key)

        if key not in a:
            diffs.append({
                "field": path,
                "a": "(missing)",
                "b": _summarize(val_b),
                "severity": _source_field_severity(key),
            })
            continue
        if key not in b:
            diffs.append({
                "field": path,
                "a": _summarize(val_a),
                "b": "(missing)",
                "severity": _source_field_severity(key),
            })
            continue

        if not _values_equal(val_a, val_b, rtol):
            diffs.append({
                "field": path,
                "a": _summarize(val_a),
                "b": _summarize(val_b),
                "severity": _source_field_severity(key),
            })

    return diffs


def _diff_data(a: dict, b: dict, el_type: str | None, rtol: float, prefix: str = "data") -> list[dict]:
    """Recursively diff data dicts, returning field-level differences."""
    diffs = []
    all_keys = sorted(set(a.keys()) | set(b.keys()))

    for key in all_keys:
        path = f"{prefix}.{key}"
        val_a = a.get(key)
        val_b = b.get(key)

        if key not in a:
            diffs.append({
                "field": path,
                "a": "(missing)",
                "b": _summarize(val_b),
                "severity": _data_field_severity(el_type, key),
            })
            continue
        if key not in b:
            diffs.append({
                "field": path,
                "a": _summarize(val_a),
                "b": "(missing)",
                "severity": _data_field_severity(el_type, key),
            })
            continue

        if not _values_equal(val_a, val_b, rtol):
            diffs.append({
                "field": path,
                "a": _summarize(val_a),
                "b": _summarize(val_b),
                "severity": _data_field_severity(el_type, key),
            })

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


def _data_field_severity(el_type: str | None, key: str) -> str:
    """Classify field importance using the ontology's exactness policy."""
    if el_type == "table":
        return "authoritative"
    if el_type == "formula":
        if key in {"expression", "parameters"}:
            return "authoritative"
        if key == "samples":
            return "helper"
    if el_type == "provision":
        if key == "rule":
            return "authoritative"
        if key in {"conditions", "then", "else", "exceptions"}:
            return "helper"
    if el_type == "definition":
        if key in {"term", "definition"}:
            return "authoritative"
        if key in {"conditions", "exceptions"}:
            return "helper"
    if el_type == "reference":
        if key == "target":
            return "authoritative"
        if key in {"url", "parameters"}:
            return "helper"
    if el_type == "figure":
        if key == "description":
            return "descriptive"
        return "helper"
    return "helper"


def _source_field_severity(key: str) -> str:
    """Classify source metadata importance."""
    if key in {"standard", "chapter", "section", "citation"}:
        return "authoritative"
    if key == "page":
        return "helper"
    return "helper"


def _norm_text(val) -> str:
    """Normalize free text for exact identity keys."""
    if not isinstance(val, str):
        return ""
    return " ".join(val.split()).strip()


def _diff_sort_key(diff: dict) -> tuple[int, str]:
    """Sort authoritative diffs ahead of helper/descriptive drift."""
    order = {"authoritative": 0, "helper": 1, "descriptive": 2}
    return (order.get(diff.get("severity", "helper"), 9), diff.get("field", ""))


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
