"""
Spot checker — samples extracted elements and sends them back to Claude
with the original PDF page for accuracy verification.
"""

import json
import random
import base64
import anthropic

from extract.pdf_parser import PageExtraction


SPOT_CHECK_PROMPT = """You are QC-checking a machine-extracted building code element against the original PDF page.

Here is the extracted JSON element:
{element_json}

Compare it against the PDF page image attached. Check:
1. Is the element type correct (table, provision, formula, figure)?
2. Are all numeric values accurate?
3. Are conditions and thresholds correctly captured?
4. Are any provisions, rows, or data points missing?
5. Are cross-references correct?

Return a JSON object:
{{
  "accurate": true/false,
  "score": 0.0 to 1.0,
  "issues": ["list of specific issues found, or empty if accurate"]
}}

Return ONLY the JSON, no other text.
"""


def spot_check(
    elements: list[dict],
    pages: list[PageExtraction],
    sample_size: int = 10,
    seed: int = 42,
) -> dict:
    """Sample elements and verify against original PDF pages.

    Args:
        elements: Extracted elements.
        pages: Original page extractions (with figure images).
        sample_size: Number of elements to check.
        seed: Random seed for reproducibility.

    Returns:
        {
            "sample_size": int,
            "average_score": float,
            "results": [{"id": str, "score": float, "accurate": bool, "issues": list}]
        }
    """
    rng = random.Random(seed)
    sample = rng.sample(elements, min(sample_size, len(elements)))

    # Build page lookup
    page_map = {p.page_number: p for p in pages}

    client = anthropic.Anthropic()
    results = []

    for element in sample:
        page_num = element.get("source", {}).get("page")
        page = page_map.get(page_num)

        content = [
            {
                "type": "text",
                "text": SPOT_CHECK_PROMPT.format(
                    element_json=json.dumps(element, indent=2)
                ),
            }
        ]

        # Attach page image if available
        if page and page.figures:
            b64 = base64.b64encode(page.figures[0].image_bytes).decode("utf-8")
            content.insert(0, {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            })

        message = client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )

        response_text = message.content[0].text
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        try:
            check_result = json.loads(response_text)
        except json.JSONDecodeError:
            check_result = {"accurate": False, "score": 0.0, "issues": ["Failed to parse QC response"]}

        results.append({
            "id": element.get("id", "UNKNOWN"),
            "score": check_result.get("score", 0.0),
            "accurate": check_result.get("accurate", False),
            "issues": check_result.get("issues", []),
        })

    scores = [r["score"] for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    return {
        "sample_size": len(results),
        "average_score": round(avg_score, 3),
        "results": results,
    }
