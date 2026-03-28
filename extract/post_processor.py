"""
Deterministic post-processor for extracted building code elements.

Applies a sequence of transforms to clean up common LLM output quirks
without any API calls. The post_process function is pure, idempotent,
and preserves all fields not targeted by transforms.

Transforms (applied in order):
1. Operator normalization (Unicode symbols & English words → schema-valid operators)
2. Null-to-empty-string coercion (for required string fields only)
3. Range null removal (remove range keys with null values)
4. ID normalization (strip spaces, uppercase prefix)
5. Definition reclassification (provisions with definition patterns → definition type)
6. Figure shape repair (figure with skipped_figure data → skipped_figure type)
"""

import copy
import re


# ---------------------------------------------------------------------------
# Operator mapping
# ---------------------------------------------------------------------------

OPERATOR_MAP: dict[str, str] = {
    # Unicode symbols
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    # English words (case-insensitive matching handled below)
    "equals": "==",
    "greater than": ">",
    "less than": "<",
    "at least": ">=",
    "at most": "<=",
    "not equal": "!=",
}

VALID_OPERATORS: set[str] = {"==", "!=", ">", ">=", "<", "<=", "in", "not_in"}

# ---------------------------------------------------------------------------
# Definition detection patterns
# ---------------------------------------------------------------------------

# ALL-CAPS term followed by colon, e.g. "BASIC WIND SPEED: ..."
_ALLCAPS_COLON_RE = re.compile(r"^([A-Z][A-Z0-9 ]+[A-Z0-9])\s*:\s*(.+)", re.DOTALL)

# Contains "is defined as" or "means"
_DEFINED_AS_RE = re.compile(r"\bis defined as\b", re.IGNORECASE)
_MEANS_RE = re.compile(r"\bmeans\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def post_process(elements: list[dict]) -> list[dict]:
    """Apply deterministic transforms to a list of extracted elements.

    This function is pure (no side effects, no API calls, no file I/O),
    idempotent (double application equals single), and preserves all
    fields not targeted by the transforms.

    Args:
        elements: List of element dicts from extraction.

    Returns:
        New list of transformed element dicts. Input is not mutated.
    """
    return [_process_element(copy.deepcopy(el)) for el in elements]


# ---------------------------------------------------------------------------
# Per-element pipeline
# ---------------------------------------------------------------------------


def _process_element(el: dict) -> dict:
    """Apply all transforms to a single element (mutates in place)."""
    _normalize_operators(el)
    _coerce_null_strings(el)
    _remove_null_ranges(el)
    _normalize_id(el)
    _reclassify_definition(el)
    _repair_figure_shape(el)
    return el


# ---------------------------------------------------------------------------
# 1. Operator normalization
# ---------------------------------------------------------------------------


def _normalize_operators(el: dict) -> None:
    """Normalize operators in conditions arrays (provision_data, definition_data)."""
    data = el.get("data")
    if not isinstance(data, dict):
        return

    conditions = data.get("conditions")
    if not isinstance(conditions, list):
        return

    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        op = cond.get("operator")
        if op is None:
            continue
        # Guard against non-string operator values (int, bool, etc.)
        if not isinstance(op, str):
            continue
        # Check the map (case-insensitive for English words)
        op_lower = op.lower().strip()
        if op in OPERATOR_MAP:
            cond["operator"] = OPERATOR_MAP[op]
        elif op_lower in OPERATOR_MAP:
            cond["operator"] = OPERATOR_MAP[op_lower]
        # Already-valid operators pass through


# ---------------------------------------------------------------------------
# 2. Null-to-empty-string coercion
# ---------------------------------------------------------------------------

# Fields where the schema requires type "string" (not ["string", "null"]).
# We coerce null → "" for these. Fields allowing null are NOT touched.

def _coerce_null_strings(el: dict) -> None:
    """Coerce null → '' for fields where the schema requires a plain string.

    Covers every field with "type": "string" (not ["string", "null"]) in the
    JSON schema that could plausibly be null in LLM output.
    """
    # Top-level required strings
    if el.get("title") is None:
        el["title"] = ""

    # source object — standard and section are required strings
    source = el.get("source")
    if isinstance(source, dict):
        if source.get("standard") is None:
            source["standard"] = ""
        if source.get("section") is None:
            source["section"] = ""

    # metadata object — extracted_by and qc_status are required strings (enums)
    metadata = el.get("metadata")
    if isinstance(metadata, dict):
        if metadata.get("extracted_by") is None:
            metadata["extracted_by"] = ""
        if metadata.get("qc_status") is None:
            metadata["qc_status"] = ""

    # Provision data
    data = el.get("data")
    if not isinstance(data, dict):
        return

    el_type = el.get("type", "")

    if el_type == "provision" or (el_type != "definition" and "rule" in data):
        if data.get("rule") is None:
            data["rule"] = ""
        # provision_data.then is "type": "string" (not nullable)
        if "then" in data and data.get("then") is None:
            data["then"] = ""
        # Note: provision_data.else is ["string", "null"] — skip

    # Definition data
    if el_type == "definition":
        if data.get("term") is None:
            data["term"] = ""
        if data.get("definition") is None:
            data["definition"] = ""

    # Formula data — expression is required string
    if "expression" in data and data.get("expression") is None:
        data["expression"] = ""

    # Formula parameters — unit is required as string
    if "parameters" in data and isinstance(data["parameters"], dict):
        for param_name, param_val in data["parameters"].items():
            if isinstance(param_val, dict):
                if "unit" in param_val and param_val["unit"] is None:
                    param_val["unit"] = ""

    # Reference data — target is required string
    if "target" in data and data.get("target") is None:
        data["target"] = ""

    # Table column names — always string
    if "columns" in data and isinstance(data["columns"], list):
        for col in data["columns"]:
            if isinstance(col, dict) and col.get("name") is None:
                col["name"] = ""

    # Condition parameters in data.conditions
    conditions = data.get("conditions")
    if isinstance(conditions, list):
        for cond in conditions:
            if isinstance(cond, dict) and cond.get("parameter") is None:
                cond["parameter"] = ""

    # Figure data — axis fields (name, unit, scale) and curve fields (label, interpolation)
    # These are nested inside figure_data.data (xy_chart_data)
    fig_data = data.get("data") if isinstance(data.get("data"), dict) else None
    if fig_data is None and el_type == "figure":
        # Also check top-level data directly for xy_chart structure
        fig_data = data if "x_axis" in data else None

    if isinstance(fig_data, dict):
        # Axis fields: name, unit, scale are all required strings in axis schema
        for axis_key in ("x_axis", "y_axis"):
            axis = fig_data.get(axis_key)
            if isinstance(axis, dict):
                if axis.get("name") is None:
                    axis["name"] = ""
                if axis.get("unit") is None:
                    axis["unit"] = ""
                if axis.get("scale") is None:
                    axis["scale"] = ""

        # Curve fields: label and interpolation are required strings
        curves = fig_data.get("curves")
        if isinstance(curves, list):
            for curve in curves:
                if isinstance(curve, dict):
                    if curve.get("label") is None:
                        curve["label"] = ""
                    if curve.get("interpolation") is None:
                        curve["interpolation"] = ""

    # Skipped figure data — figure_type and skip_reason are required strings
    if el_type == "skipped_figure" or ("figure_type" in data and "skip_reason" in data):
        if data.get("figure_type") is None:
            data["figure_type"] = ""
        if data.get("skip_reason") is None:
            data["skip_reason"] = ""


# ---------------------------------------------------------------------------
# 3. Range null removal
# ---------------------------------------------------------------------------


def _remove_null_ranges(el: dict) -> None:
    """Remove range keys that are null or contain null values."""
    data = el.get("data")
    if not isinstance(data, dict):
        return

    parameters = data.get("parameters")
    if not isinstance(parameters, dict):
        return

    for param_val in parameters.values():
        if not isinstance(param_val, dict):
            continue
        if "range" not in param_val:
            continue
        rng = param_val["range"]
        if rng is None:
            del param_val["range"]
        elif isinstance(rng, list) and any(v is None for v in rng):
            del param_val["range"]


# ---------------------------------------------------------------------------
# 4. ID normalization
# ---------------------------------------------------------------------------


def _normalize_id(el: dict) -> None:
    """Strip spaces from ID and uppercase the first segment (standard prefix)."""
    raw_id = el.get("id")
    if not isinstance(raw_id, str):
        return

    # Strip all spaces
    cleaned = raw_id.replace(" ", "")

    # Uppercase the first segment (before the first '-')
    parts = cleaned.split("-", 1)
    if parts:
        parts[0] = parts[0].upper()
    cleaned = "-".join(parts)

    el["id"] = cleaned


# ---------------------------------------------------------------------------
# 5. Definition reclassification
# ---------------------------------------------------------------------------


def _reclassify_definition(el: dict) -> None:
    """Reclassify provisions that are actually definitions.

    Detection patterns:
    - ALL-CAPS term followed by colon (e.g. "BASIC WIND SPEED: ...")
    - Contains "is defined as"
    - Contains "means"
    """
    if el.get("type") != "provision":
        return

    data = el.get("data")
    if not isinstance(data, dict):
        return

    rule = data.get("rule")
    if not isinstance(rule, str) or not rule.strip():
        return

    # Try ALL-CAPS colon pattern
    m = _ALLCAPS_COLON_RE.match(rule.strip())
    if m:
        term = m.group(1).strip()
        definition_text = m.group(2).strip()
        _convert_to_definition(el, term, definition_text, data)
        return

    # Try "is defined as" pattern
    if _DEFINED_AS_RE.search(rule):
        parts = re.split(r"\bis defined as\b", rule, maxsplit=1, flags=re.IGNORECASE)
        term = parts[0].strip().rstrip(".,;")
        definition_text = parts[1].strip() if len(parts) > 1 else rule
        _convert_to_definition(el, term, definition_text, data)
        return

    # Try "means" pattern
    if _MEANS_RE.search(rule):
        parts = re.split(r"\bmeans\b", rule, maxsplit=1, flags=re.IGNORECASE)
        term = parts[0].strip().rstrip(".,;")
        definition_text = parts[1].strip() if len(parts) > 1 else rule
        _convert_to_definition(el, term, definition_text, data)
        return


def _convert_to_definition(
    el: dict, term: str, definition_text: str, old_data: dict
) -> None:
    """Convert a provision element to definition type with definition_data structure."""
    el["type"] = "definition"
    el["data"] = {
        "term": term,
        "definition": definition_text,
        "conditions": old_data.get("conditions", []),
        "exceptions": old_data.get("exceptions", []),
    }


# ---------------------------------------------------------------------------
# 6. Figure shape repair
# ---------------------------------------------------------------------------


def _repair_figure_shape(el: dict) -> None:
    """Retype figure → skipped_figure if data has skipped_figure shape."""
    if el.get("type") != "figure":
        return

    data = el.get("data")
    if not isinstance(data, dict):
        return

    # skipped_figure_data shape: has "figure_type" and "skip_reason" at top level
    if "figure_type" in data and "skip_reason" in data:
        el["type"] = "skipped_figure"
