"""
Figure digitizer — sends chart/graph images to Claude vision API
and gets back digitized (x, y) point arrays.
"""

import base64
import json
import anthropic


DIGITIZE_PROMPT = """You are digitizing a chart/graph from a building code document into machine-readable data.

Analyze this image and extract ALL curves, data series, or relationships shown.

Return a JSON object with this exact structure:
{
  "x_axis": {"name": "<axis label>", "unit": "<unit>", "scale": "linear or log"},
  "y_axis": {"name": "<axis label>", "unit": "<unit>", "scale": "linear or log"},
  "curves": [
    {
      "label": "<series/curve name>",
      "points": [[x1, y1], [x2, y2], ...],
      "interpolation": "linear"
    }
  ]
}

Rules:
- Sample enough points to capture the shape accurately (minimum 5 per curve, more for complex shapes)
- Read values as precisely as possible from the gridlines
- Use "linear" interpolation unless the curve is clearly stepped ("step")
- If the x-axis is logarithmic, report actual x values (not log-transformed)
- Include ALL curves/series visible in the chart
- Return ONLY the JSON object, no other text
"""


def digitize_figure(image_bytes: bytes, context: str = "") -> dict:
    """Send a figure image to Claude and get digitized point data.

    Args:
        image_bytes: PNG image bytes of the figure.
        context: Optional text context (e.g., figure caption, surrounding text).

    Returns:
        Parsed figure data dict matching the figure_data schema.
    """
    client = anthropic.Anthropic()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    prompt = DIGITIZE_PROMPT
    if context:
        prompt += f"\n\nContext from the document: {context}"

    message = client.messages.create(
        model="claude-sonnet-4-6-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )

    response_text = message.content[0].text
    # Strip markdown fencing if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:-1])

    return json.loads(response_text)


def digitize_table_image(image_bytes: bytes, context: str = "") -> dict:
    """Send a table image to Claude when pdfplumber can't parse it.

    Some PDF tables are rendered as images or have complex merged cells.
    Falls back to vision-based extraction.

    Args:
        image_bytes: PNG image bytes containing a table.
        context: Optional text context.

    Returns:
        Parsed table data dict matching the table_data schema.
    """
    client = anthropic.Anthropic()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """Extract this table from a building code document into machine-readable JSON.

Return a JSON object with this exact structure:
{
  "columns": [{"name": "<col name>", "unit": "<unit or null>"}],
  "rows": [{"<col1>": value, "<col2>": value, ...}]
}

Rules:
- Preserve all numeric values exactly as shown
- Use null for empty cells
- Include all rows and columns
- Return ONLY the JSON object, no other text
"""
    if context:
        prompt += f"\n\nContext from the document: {context}"

    message = client.messages.create(
        model="claude-sonnet-4-6-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64_image,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    response_text = message.content[0].text
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:-1])

    return json.loads(response_text)
