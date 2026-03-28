"""
Schema validator — validates extracted elements against the JSON Schema.
"""

import json
from pathlib import Path
from jsonschema import validate, ValidationError, Draft202012Validator


SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "element.schema.json"


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def validate_element(element: dict, schema: dict | None = None) -> dict:
    """Validate a single element against the schema.

    Returns:
        {"valid": bool, "errors": list[str]}
    """
    if schema is None:
        schema = load_schema()

    try:
        validate(instance=element, schema=schema)
        return {"valid": True, "errors": []}
    except ValidationError as e:
        return {"valid": False, "errors": [e.message]}


def validate_chapter(elements: list[dict]) -> dict:
    """Validate all elements in a chapter extraction.

    Returns:
        {
            "total": int,
            "passed": int,
            "failed": int,
            "errors": [{"id": str, "errors": list[str]}]
        }
    """
    schema = load_schema()
    results = {
        "total": len(elements),
        "passed": 0,
        "failed": 0,
        "errors": [],
    }

    for element in elements:
        result = validate_element(element, schema)
        if result["valid"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["errors"].append({
                "id": element.get("id", "UNKNOWN"),
                "errors": result["errors"],
            })

    return results
