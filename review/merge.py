"""
Merge human decisions into a base extraction to produce a resolved output.

Reads human-decisions.json and patches the base extraction JSON
with the chosen values for each disagreement.
"""

import json
from copy import deepcopy
from pathlib import Path


def _set_nested(obj, dotpath, value):
    """Set a value at a dot-separated path like 'data.expression'."""
    keys = dotpath.split(".")
    for key in keys[:-1]:
        obj = obj[key]
    obj[keys[-1]] = value


def _get_nested(obj, dotpath):
    """Get a value at a dot-separated path."""
    for key in dotpath.split("."):
        obj = obj[key]
    return obj


def merge_decisions(base_path, alt_path, decisions_path, output_path):
    """Apply human decisions to produce a merged extraction.

    Args:
        base_path: Path to the base extraction JSON (e.g., codex run).
        alt_path: Path to the alternative extraction JSON (e.g., claude run).
        decisions_path: Path to human-decisions.json.
        output_path: Where to write the merged result.
    """
    base = json.loads(Path(base_path).read_text())
    alt = json.loads(Path(alt_path).read_text())
    decisions = json.loads(Path(decisions_path).read_text())

    base_idx = {el["id"]: el for el in base}
    alt_idx = {el["id"]: el for el in alt}

    merged = deepcopy(base)
    merged_idx = {el["id"]: el for el in merged}

    applied = 0
    for dec in decisions.get("decisions", []):
        eid = dec["element_id"]
        target = merged_idx.get(eid)
        if not target:
            # Element only in alt run — add it if decision references it
            alt_el = alt_idx.get(eid)
            if alt_el:
                target = deepcopy(alt_el)
                merged.append(target)
                merged_idx[eid] = target

        if not target:
            continue

        for field, choice_info in dec.get("fields", {}).items():
            choice = choice_info.get("choice")
            manual_value = choice_info.get("value")

            if choice == "manual" and manual_value is not None:
                _set_nested(target, field, manual_value)
                applied += 1
            elif choice == "a":
                alt_el = alt_idx.get(dec.get("id_a", eid))
                if alt_el:
                    try:
                        _set_nested(target, field, _get_nested(alt_el, field))
                        applied += 1
                    except (KeyError, TypeError):
                        pass
            elif choice == "b":
                base_el = base_idx.get(dec.get("id_b", eid))
                if base_el:
                    try:
                        _set_nested(target, field, _get_nested(base_el, field))
                        applied += 1
                    except (KeyError, TypeError):
                        pass

        # Update metadata
        target["metadata"]["qc_status"] = "passed"
        target["metadata"]["qc_notes"] = f"Human-reviewed {dec.get('timestamp', '')}"

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2))
    return len(merged), applied
