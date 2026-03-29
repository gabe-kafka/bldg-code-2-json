"""
Element-level retry — re-prompts Claude for elements that failed QC.

Targets two categories:
1. Schema validation failures (from validate_chapter QC results)
2. Low spot-check scores (below configurable threshold)

Already-valid elements pass through unchanged. Failing elements get
a targeted re-prompt with the original JSON, specific errors, and
the schema, then validated against the schema before acceptance.
"""

import json
import copy
import logging

import anthropic

from qc.schema_validator import load_schema, validate_element

logger = logging.getLogger(__name__)


RETRY_MODEL = "claude-sonnet-4-20250514"

RETRY_PROMPT = """You are fixing a building code element that failed JSON Schema validation.

Here is the original element JSON:
{element_json}

It failed validation with these errors:
{errors}

Here is the JSON Schema it must conform to:
{schema_json}

Fix the element so it passes validation. Return ONLY the corrected JSON object, no other text.
"""


def retry_elements(
    elements: list[dict],
    qc_results: dict,
    pages: list | None = None,
    max_retries: int = 3,
    schema: dict | None = None,
    spot_check_threshold: float = 0.5,
) -> tuple[list[dict], dict]:
    """Retry elements that failed QC validation.

    Args:
        elements: Extracted elements list.
        qc_results: Output from validate_chapter, optionally with spot_check scores.
        pages: Original PDF pages (reserved for future use).
        max_retries: Maximum retry attempts per element.
        schema: JSON Schema dict. Loaded from disk if None.
        spot_check_threshold: Elements with spot-check scores below this are retried.

    Returns:
        (all_elements, retry_report) where retry_report has keys:
            fixed: {id: {retries, original_errors}}
            still_failing: [ids]
            skipped: [ids]
    """
    if schema is None:
        schema = load_schema()

    # Build set of failing element IDs and their errors
    failing = {}
    for err_entry in qc_results.get("errors", []):
        eid = err_entry["id"]
        failing[eid] = err_entry["errors"]

    # Add low spot-check scores
    spot_checks = qc_results.get("spot_check", {})
    for eid, score in spot_checks.items():
        if score < spot_check_threshold and eid not in failing:
            failing[eid] = [f"Low spot-check score: {score}"]

    report = {"fixed": {}, "still_failing": [], "skipped": []}
    result_elements = []
    client = None

    for el in elements:
        eid = el.get("id", "UNKNOWN")

        if eid not in failing:
            report["skipped"].append(eid)
            result_elements.append(copy.deepcopy(el))
            continue

        # Lazy-init client only when needed
        if client is None:
            client = anthropic.Anthropic()

        original_errors = failing[eid]
        current_el = copy.deepcopy(el)
        current_errors = original_errors
        fixed = False

        for attempt in range(1, max_retries + 1):
            try:
                prompt = RETRY_PROMPT.format(
                    element_json=json.dumps(current_el, indent=2),
                    errors="\n".join(f"- {e}" for e in current_errors),
                    schema_json=json.dumps(schema, indent=2),
                )

                message = client.messages.create(
                    model=RETRY_MODEL,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )

                response_text = message.content[0].text
                if response_text.startswith("```"):
                    lines = response_text.split("\n")
                    response_text = "\n".join(lines[1:-1])

                candidate = json.loads(response_text)
                vr = validate_element(candidate, schema)

                if vr["valid"]:
                    result_elements.append(candidate)
                    report["fixed"][eid] = {
                        "retries": attempt,
                        "original_errors": original_errors,
                    }
                    fixed = True
                    break
                else:
                    current_el = candidate
                    current_errors = vr["errors"]

            except Exception as exc:
                logger.warning(
                    "Retry attempt %d/%d for %s failed: %s",
                    attempt, max_retries, eid, exc,
                )
                continue

        if not fixed:
            report["still_failing"].append(eid)
            result_elements.append(copy.deepcopy(el))

    return result_elements, report
