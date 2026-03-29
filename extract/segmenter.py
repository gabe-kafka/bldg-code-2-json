"""
Page layout segmenter using Claude vision with few-shot examples.

Uses human-labeled gold pages as reference examples to segment new pages.
Falls back to heuristic when no API key is available.
"""

import base64
import json
from pathlib import Path

from anthropic import Anthropic

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


SYSTEM = """\
You are a document layout segmenter for building code PDFs. \
You identify content regions on each page and classify them by type. \
You will be shown example pages with their correct region annotations, \
then asked to segment a new page in the same style."""

REGION_SPEC = """\
For the NEW page image, return a JSON array of region objects:
{
  "x0": <left px>, "y0": <top px>, "x1": <right px>, "y1": <bottom px>,
  "region_type": "heading" | "text_block" | "definition" | "table" | "equation" | "figure",
  "classification": "structured" | "linked" | "skipped",
  "title_index": "<section/table/eq number if visible, else empty>",
  "title_name": "<heading text if visible, else empty>",
  "label": "<brief description>"
}

TYPE RULES:
- heading: Section/chapter headings, bold titles (e.g. "26.1 PROCEDURES", "CHAPTER 26")
- text_block: Body text, provisions, rules, numbered lists, notes
- definition: Term definitions (BOLD TERM: definition text...)
- table: Tabular data with rows and columns
- equation: Standalone math formulas with equation numbers
- figure: Diagrams, charts, flowcharts, maps, illustrations

CLASSIFICATION:
- heading, text_block, definition, table, equation → "structured"
- figure → "linked"
- Page headers, footers, page numbers, watermarks → "skipped"

CALLOUTS: If a text_block contains embedded section headings, add a "callouts" array:
  "callouts": [{"y": <pixel y of heading>, "title_index": "26.1.1", "title_name": "Scope"}]

Match the annotation style of the examples exactly. Return ONLY the JSON array."""


def _encode_image(path):
    return base64.standard_b64encode(Path(path).read_bytes()).decode()


def _build_example_content(pages_dir, classifications, example_pages):
    """Build message content blocks from labeled example pages."""
    content = []
    for pg_key in example_pages:
        pg_data = classifications["pages"].get(str(pg_key))
        if not pg_data:
            continue
        # Find the image file
        img_path = pages_dir / f"page-{int(pg_key):03d}.png"
        if not img_path.exists():
            continue
        content.append({
            "type": "text",
            "text": f"EXAMPLE — Page {pg_key}:",
        })
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": _encode_image(img_path)},
        })
        content.append({
            "type": "text",
            "text": f"Regions for page {pg_key}:\n```json\n{json.dumps(pg_data['regions'], indent=2)}\n```",
        })
    return content


def _select_examples(classifications, max_examples=3):
    """Pick the most informative labeled pages as examples.

    Prefers pages with diverse region types and more regions.
    """
    scored = []
    for pk, pv in classifications.get("pages", {}).items():
        regions = pv.get("regions", [])
        if not regions:
            continue
        types = set(r.get("region_type") for r in regions)
        # Score: number of unique types + total regions
        score = len(types) * 10 + len(regions)
        scored.append((score, pk))
    scored.sort(reverse=True)
    return [pk for _, pk in scored[:max_examples]]


def segment_page_fewshot(
    image_path,
    pages_dir,
    classifications,
    model="claude-sonnet-4-20250514",
    max_tokens=8192,
):
    """Segment a page using few-shot examples from human-labeled data.

    Args:
        image_path: Path to the page PNG to segment.
        pages_dir: Directory containing all page PNGs.
        classifications: Loaded classifications.json dict.
        model: Claude model to use.
        max_tokens: Max response tokens.

    Returns:
        List of region dicts.
    """
    image_path = Path(image_path)
    pages_dir = Path(pages_dir)

    # Get image dimensions
    from PIL import Image
    img = Image.open(image_path)
    width, height = img.size
    img.close()

    # Select best example pages
    example_pages = _select_examples(classifications)
    if not example_pages:
        raise ValueError("No labeled examples available for few-shot segmentation")

    # Build the message
    content = _build_example_content(pages_dir, classifications, example_pages)

    # Add the target page
    content.append({"type": "text", "text": f"\nNEW PAGE to segment ({width}x{height} px):"})
    content.append({
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png",
                   "data": _encode_image(image_path)},
    })
    content.append({"type": "text", "text": REGION_SPEC})

    response = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0].strip()

    raw = json.loads(text)

    # Normalize
    regions = []
    for item in raw:
        bbox = item.get("bbox")
        if bbox:
            item["x0"], item["y0"], item["x1"], item["y1"] = (
                int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            )
        regions.append({
            "x0": int(item.get("x0", 0)),
            "y0": int(item.get("y0", 0)),
            "x1": int(item.get("x1", 0)),
            "y1": int(item.get("y1", 0)),
            "region_type": item.get("region_type", "text_block"),
            "classification": item.get("classification", "structured"),
            "title_index": item.get("title_index", ""),
            "title_name": item.get("title_name", ""),
            "label": item.get("label", ""),
            "callouts": item.get("callouts", []),
        })

    return regions
