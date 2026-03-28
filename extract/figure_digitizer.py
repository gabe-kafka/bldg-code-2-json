"""
Figure digitizer — classifies figures, extracts computable ones, skips illustrative diagrams.

Extracts:
- xy_chart: x-y plots, multi-curve coefficient charts
- table_image: tables rendered as images that pdfplumber can't parse

Skips (flagged for human review):
- diagram: building cross-sections, topo profiles, pressure zone illustrations
- contour_map: wind speed maps, snow load maps (use external APIs instead)
"""

import base64
import io
import json
import anthropic
from PIL import Image


MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Pass 1: Classify — extract or skip?
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """You are analyzing a figure from a building code document.

Classify this figure into exactly ONE of these types:

1. "xy_chart" — A plot with axes and curves containing numeric data an engineer needs to look up.
   Examples: pressure coefficient vs effective wind area, Kz vs height, velocity profile curves.

2. "table_image" — A table rendered as an image rather than selectable text.

3. "diagram" — An illustrative drawing showing geometry, zones, dimensions, or spatial relationships.
   NOT computable data — exists to help humans understand variable definitions.
   Examples: building cross-sections, topographic hill profiles, pressure zone layouts, roof geometry.

4. "contour_map" — A geographic map with contour lines.
   Examples: wind speed maps, ground snow load maps.

Return ONLY a JSON object:
{
  "figure_type": "<one of the 4 types above>",
  "extractable": true/false,
  "description": "<one sentence describing what the figure shows>",
  "skip_reason": "<why this figure doesn't need extraction, or null if extractable>"
}

"extractable" is true ONLY for xy_chart and table_image. Diagrams and contour maps are false.
"""


def classify_figure(image_bytes: bytes, context: str = "") -> dict:
    """Classify a figure. Returns type and whether to extract or skip."""
    client = anthropic.Anthropic()
    prompt = CLASSIFY_PROMPT
    if context:
        prompt += f"\n\nContext: {context}"

    message = client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                _image_block(image_bytes),
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _parse_json(message.content[0].text)


# ---------------------------------------------------------------------------
# Pass 2: Extract (xy_chart or table_image only)
# ---------------------------------------------------------------------------

XY_CHART_PROMPT = """You are digitizing an engineering chart from a building code into machine-readable data.

This is a chart with axes and curves. Extract ALL data with high precision.

Return a JSON object:
{
  "x_axis": {"name": "<label>", "unit": "<unit>", "scale": "linear or log"},
  "y_axis": {"name": "<label>", "unit": "<unit>", "scale": "linear or log"},
  "curves": [
    {
      "label": "<curve/series name>",
      "points": [[x1, y1], [x2, y2], ...],
      "interpolation": "linear | step | cubic | linear_on_log_x"
    }
  ]
}

Critical rules:
- MINIMUM 10 points per curve. For curves with inflection points or slope changes, sample 15-20+.
- At every gridline intersection, read the value.
- At every labeled tick mark, read the value.
- At curve endpoints, inflection points, and slope changes, add extra sample points.
- For logarithmic axes, sample densely at the low end where the curve changes fastest.
- Read values to the precision allowed by the gridlines.
- Include ALL curves, ALL series, ALL labeled lines — missing one invalidates the extraction.
- Dashed, dotted, and solid lines are all separate curves.
- If curves have positive and negative branches (e.g., +GCp and -GCp), extract both.
- Return ONLY the JSON object.
"""

TABLE_IMAGE_PROMPT = """Extract this table from a building code document into machine-readable JSON.

Return a JSON object:
{
  "columns": [{"name": "<col name>", "unit": "<unit or null>"}],
  "rows": [{"<col1>": value, "<col2>": value, ...}]
}

Critical rules:
- Preserve ALL numeric values exactly as shown.
- For merged cells, repeat the value in each row it spans.
- For cells with superscripts/footnotes, include the footnote marker (e.g., "0.85^a").
- Handle multi-level headers by combining parent + child (e.g., "Exposure_B_alpha" not just "alpha").
- Use null for truly empty cells.
- Include ALL rows and ALL columns — missing data invalidates the extraction.
- Return ONLY the JSON object.
"""


# ---------------------------------------------------------------------------
# Pass 3: Verification
# ---------------------------------------------------------------------------

VERIFY_PROMPT = """You are QC-checking a machine extraction of a building code figure.

Here is the original figure image and the extracted JSON data.

Extracted data:
{extracted_json}

Check THOROUGHLY:
1. Are all curves / series / rows accounted for?
2. Are numeric values accurate to the precision visible in the figure?
3. Are labels correct?
4. Is anything missing or fabricated?

Return a JSON object:
{{
  "verified": true/false,
  "issues": [
    {{"severity": "critical | warning", "description": "<what's wrong>", "fix": "<suggested correction>"}}
  ],
  "corrections": {{<corrected fields as partial JSON, or empty object if no corrections needed>}}
}}

Return ONLY the JSON object.
"""


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def digitize_figure(image_bytes: bytes, context: str = "", verify: bool = True) -> dict:
    """Classify, extract (if computable), and verify a figure.

    Diagrams and contour maps are skipped with a flag — they're illustrative,
    not computable data. Only xy_charts and table_images are extracted.

    Returns:
        {
            "figure_class": {...classification...},
            "skipped": bool,
            "data": {...extracted data or null...},
            "verification": {...or null...}
        }
    """
    image_bytes = _ensure_resolution(image_bytes, min_width=1200)

    # Pass 1: Classify
    classification = classify_figure(image_bytes, context)
    fig_type = classification.get("figure_type", "xy_chart")
    extractable = classification.get("extractable", False)

    # Skip non-computable figures
    if not extractable:
        return {
            "figure_class": classification,
            "skipped": True,
            "data": None,
            "verification": None,
        }

    # Pass 2: Extract
    prompt = XY_CHART_PROMPT if fig_type == "xy_chart" else TABLE_IMAGE_PROMPT
    if context:
        prompt += f"\n\nContext from the document: {context}"

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=16384,
        messages=[{
            "role": "user",
            "content": [
                _image_block(image_bytes),
                {"type": "text", "text": prompt},
            ],
        }],
    )
    data = _parse_json(message.content[0].text)

    result = {
        "figure_class": classification,
        "skipped": False,
        "data": data,
        "verification": None,
    }

    # Pass 3: Verify
    if verify:
        verification = _verify_extraction(image_bytes, data)
        result["verification"] = verification

        corrections = verification.get("corrections", {})
        if corrections:
            result["data"] = _apply_corrections(data, corrections)

    return result


def digitize_table_image(image_bytes: bytes, context: str = "") -> dict:
    """Extract a table rendered as an image."""
    image_bytes = _ensure_resolution(image_bytes, min_width=1200)
    client = anthropic.Anthropic()
    prompt = TABLE_IMAGE_PROMPT
    if context:
        prompt += f"\n\nContext: {context}"

    message = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": [
                _image_block(image_bytes),
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _parse_json(message.content[0].text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _verify_extraction(image_bytes: bytes, extracted_data: dict) -> dict:
    """Send extraction back to Claude with the original image for verification."""
    client = anthropic.Anthropic()
    prompt = VERIFY_PROMPT.format(extracted_json=json.dumps(extracted_data, indent=2))

    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                _image_block(image_bytes),
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _parse_json(message.content[0].text)


def _apply_corrections(data: dict, corrections: dict) -> dict:
    """Merge corrections into extracted data."""
    def _deep_merge(base, patch):
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                _deep_merge(base[k], v)
            else:
                base[k] = v
        return base
    return _deep_merge(data, corrections)


def _ensure_resolution(image_bytes: bytes, min_width: int = 1200) -> bytes:
    """Upscale image if too small for accurate extraction."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.width >= min_width:
        return image_bytes

    scale = min_width / img.width
    new_size = (int(img.width * scale), int(img.height * scale))
    img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _image_block(image_bytes: bytes) -> dict:
    """Build a Claude API image content block."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(image_bytes).decode("utf-8"),
        },
    }


def _parse_json(text: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fencing."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        text = "\n".join(lines[1:end])
    return json.loads(text)
