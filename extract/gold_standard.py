"""
Gold standard element management — load, generate, and write gold reference elements.

Gold elements are individually stored as JSON files in schema/gold/ and serve
as the ground-truth reference for extraction quality evaluation.
"""

import copy
import json
import logging
from pathlib import Path

from qc.schema_validator import load_schema, validate_element

logger = logging.getLogger(__name__)

GOLD_DIR = Path(__file__).parent.parent / "schema" / "gold"


def load_gold_elements(gold_dir: str = str(GOLD_DIR)) -> list[dict]:
    """Load all gold element JSON files from a directory.

    Validates each against the schema. Skips and warns on malformed or
    invalid files. Returns empty list if directory is missing or empty.
    """
    gold_path = Path(gold_dir)
    if not gold_path.is_dir():
        return []

    schema = load_schema()
    elements = []

    for fp in sorted(gold_path.glob("*.json")):
        try:
            data = json.loads(fp.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping malformed gold file %s: %s", fp.name, e)
            continue

        vr = validate_element(data, schema)
        if not vr["valid"]:
            logger.warning("Skipping invalid gold file %s: %s", fp.name, vr["errors"])
            continue

        elements.append(data)

    return elements


def generate_draft_gold_set(
    elements: list[dict],
    max_per_type: int = 3,
) -> list[dict]:
    """Select a draft gold set from extracted elements.

    Filters to schema-valid elements, selects up to max_per_type per element
    type, preferring those with qc_status == 'passed'. Sets qc_status to
    'passed' on all selected elements.
    """
    schema = load_schema()

    # Filter to valid elements
    valid = []
    for el in elements:
        vr = validate_element(el, schema)
        if vr["valid"]:
            valid.append(el)

    # Group by type, preferring qc_status == 'passed'
    by_type: dict[str, list[dict]] = {}
    for el in valid:
        t = el.get("type", "unknown")
        by_type.setdefault(t, []).append(el)

    selected = []
    for t, group in by_type.items():
        # Sort: 'passed' first, then original order
        group.sort(key=lambda e: (e.get("metadata", {}).get("qc_status") != "passed",))
        for el in group[:max_per_type]:
            gold = copy.deepcopy(el)
            gold["metadata"]["qc_status"] = "passed"
            selected.append(gold)

    return selected


def write_gold_files(elements: list[dict], gold_dir: str = str(GOLD_DIR)) -> None:
    """Write each element to gold_dir/<id>.json as pretty-printed JSON."""
    gold_path = Path(gold_dir)
    gold_path.mkdir(parents=True, exist_ok=True)

    for el in elements:
        fp = gold_path / f"{el['id']}.json"
        fp.write_text(json.dumps(el, indent=2) + "\n")


def generate_initial_gold_set() -> None:
    """Generate the initial draft gold set from real extraction data."""
    from extract.post_processor import post_process

    raw_path = Path(__file__).parent.parent / "output" / "raw" / "asce722-ch26.json"
    if not raw_path.exists():
        logger.warning("Raw extraction data not found at %s", raw_path)
        return

    with open(raw_path) as f:
        elements = json.load(f)

    processed = post_process(elements)
    golds = generate_draft_gold_set(processed)
    write_gold_files(golds)
    print(f"Generated {len(golds)} gold elements → {GOLD_DIR}")


if __name__ == "__main__":
    generate_initial_gold_set()
