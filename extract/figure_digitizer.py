"""
Figure digitizer — multi-pass, type-aware extraction of complex building code figures.

Handles:
- xy_chart: standard x-y plots, multi-curve charts, coefficient graphs
- diagram: annotated engineering drawings (pressure zones, building geometry, topo features)
- multi_panel: figures with multiple sub-figures or sub-charts
- contour_map: geographic contour maps (wind speed, snow load)
- table_image: tables rendered as images that pdfplumber can't parse
"""

import base64
import io
import json
import anthropic
from PIL import Image


MODEL = "claude-sonnet-4-6-20250514"
MODEL_HARD = "claude-opus-4-6-20250514"


# ---------------------------------------------------------------------------
# Pass 1: Classify the figure
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """You are analyzing a figure from a building code document (ASCE 7-22, Wind Loads).

Classify this figure into exactly ONE of these types:

1. "xy_chart" — A standard plot with x-axis, y-axis, and one or more curves/data series.
   Examples: pressure coefficient vs effective wind area, Kz vs height, velocity profile.

2. "diagram" — An annotated engineering drawing showing geometry, zones, dimensions, or spatial relationships.
   Examples: building cross-sections with pressure zones labeled (1,2,3,4,5), topographic feature definitions (hill with H, Lh, x dimensions), roof geometry, wall zones, wind direction arrows.

3. "multi_panel" — A figure containing multiple distinct sub-figures, each of which could be classified independently.
   Examples: a single figure number with parts (a), (b), (c) showing different building types or conditions.

4. "contour_map" — A geographic map with contour lines showing spatially varying values.
   Examples: basic wind speed maps, ground snow load maps.

5. "table_image" — A table rendered as an image rather than selectable text.

Return ONLY a JSON object:
{
  "figure_type": "<one of the 5 types above>",
  "confidence": 0.0 to 1.0,
  "description": "<one sentence describing what the figure shows>",
  "sub_panels": <number of distinct sub-figures if multi_panel, else null>,
  "complexity": "low | medium | high",
  "notes": "<anything unusual about this figure that affects extraction>"
}
"""


def classify_figure(image_bytes: bytes, context: str = "") -> dict:
    """Classify a figure into one of the supported types."""
    client = anthropic.Anthropic()
    prompt = CLASSIFY_PROMPT
    if context:
        prompt += f"\n\nContext: {context}"

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
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
# Pass 2: Type-specific extraction
# ---------------------------------------------------------------------------

XY_CHART_PROMPT = """You are digitizing an engineering chart from ASCE 7-22 (Wind Loads) into machine-readable data.

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
- MINIMUM 10 points per curve. For curves with inflection points or changes in slope, sample 15-20+.
- At every gridline intersection, read the value.
- At every labeled tick mark, read the value.
- At curve endpoints, inflection points, and slope changes, add extra sample points.
- For logarithmic axes, sample densely at the low end where the curve changes fastest.
- Read values to the precision allowed by the gridlines (e.g., if grid spacing is 0.05, report to 0.05).
- Include ALL curves, ALL series, ALL labeled lines — missing one invalidates the extraction.
- Dashed, dotted, and solid lines are all separate curves.
- If curves have positive and negative branches (e.g., +GCp and -GCp), extract both.
- Return ONLY the JSON object.
"""

DIAGRAM_PROMPT = """You are extracting an annotated engineering diagram from ASCE 7-22 (Wind Loads) into structured data.

This is NOT a chart with axes. It is a geometric/spatial diagram showing building features, pressure zones, dimensions, or topographic features.

Return a JSON object:
{
  "diagram_type": "<specific type: pressure_zones | topo_feature | building_geometry | roof_zones | wall_zones | wind_direction | other>",
  "elements": [
    {
      "id": "<label from diagram, e.g. 'Zone 1', 'H', 'Lh'>",
      "type": "zone | dimension | annotation | arrow | boundary | surface | angle",
      "description": "<what this element represents>",
      "value": <numeric value if applicable, else null>,
      "unit": "<unit if applicable, else null>",
      "position": "<spatial description: 'windward wall', 'leeward roof', 'top of hill', etc.>",
      "conditions": "<when this element applies, e.g. 'theta <= 10 deg'>"
    }
  ],
  "relationships": [
    {
      "from": "<element id>",
      "to": "<element id>",
      "type": "defines | bounds | equals | references",
      "description": "<how these elements relate>"
    }
  ],
  "geometry": {
    "description": "<overall geometry description: 'rectangular building cross-section', '2D hill profile', etc.>",
    "key_dimensions": {"<name>": {"value": null, "unit": "<unit>", "description": "<what it measures>"}},
    "coordinate_system": "<description of orientation: 'wind from left', 'plan view from above', etc.>"
  },
  "notes": ["<any text annotations, equations, or conditions shown on the diagram>"]
}

Critical rules:
- Extract EVERY labeled element, dimension, zone, arrow, and annotation.
- Preserve exact labels as shown (e.g., "Zone 1", "4E", "h", "L", "θ").
- For pressure zone diagrams: capture zone boundaries, coefficient labels, and applicable conditions.
- For topographic features: capture all dimension definitions (H, Lh, x, K1, K2, K3).
- For building geometry: capture all surfaces, angles, and dimension relationships.
- Include equations or formulas shown on the diagram in the notes array.
- Return ONLY the JSON object.
"""

MULTI_PANEL_PROMPT = """You are extracting a multi-panel figure from ASCE 7-22 (Wind Loads).

This figure contains {n_panels} distinct sub-figures. Extract each one separately.

Return a JSON object:
{{
  "panels": [
    {{
      "panel_id": "<label: (a), (b), Part 1, Case A, etc.>",
      "title": "<panel-specific title if shown>",
      "panel_type": "xy_chart | diagram | table_image",
      "data": <the full extraction for this panel — use the appropriate format for its type>
    }}
  ],
  "shared_context": "<anything that applies to all panels: shared axis labels, common conditions, etc.>"
}}

For each panel:
- If it's an xy_chart: use x_axis/y_axis/curves format with MINIMUM 10 points per curve.
- If it's a diagram: use diagram_type/elements/relationships/geometry format.
- If it's a table: use columns/rows format.

Critical rules:
- Extract ALL panels. Missing a panel invalidates the extraction.
- Some panels share axis labels or legends — note these in shared_context.
- Return ONLY the JSON object.
"""

CONTOUR_MAP_PROMPT = """You are extracting a contour map from ASCE 7-22 (Wind Loads).

This is a geographic map showing spatially varying values (likely wind speeds).

Return a JSON object:
{
  "map_type": "wind_speed | snow_load | rain | other",
  "value_name": "<what the contours represent>",
  "value_unit": "<unit, e.g. mph, psf>",
  "risk_category": "<if labeled, e.g. II, III, IV>",
  "return_period": "<if labeled, e.g. 700-year, 1700-year>",
  "contours": [
    {"value": <numeric>, "description": "<where this contour runs, referencing states/regions>"}
  ],
  "special_regions": [
    {"name": "<region name>", "value": "<value or range>", "description": "<details>"}
  ],
  "notes": ["<footnotes, special wind regions, hurricane-prone regions, etc.>"]
}

Critical rules:
- Extract EVERY labeled contour line with its value.
- Note special wind regions, hurricane-prone coastlines, and any region-specific notes.
- For maps with insets (Alaska, Hawaii, territories), extract those too.
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
1. Are all curves / zones / elements / panels accounted for?
2. Are numeric values accurate to the precision visible in the figure?
3. Are labels and identifiers correct?
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
    """Full multi-pass figure extraction pipeline.

    Pass 1: Classify the figure type.
    Pass 2: Extract with type-specific prompt.
    Pass 3: Verify extraction against original image (optional).

    Args:
        image_bytes: PNG image bytes.
        context: Text context (caption, surrounding text).
        verify: Whether to run verification pass.

    Returns:
        Dict with keys: figure_class, data, verification (if verify=True).
    """
    # Upscale if image is small
    image_bytes = _ensure_resolution(image_bytes, min_width=1200)

    # Pass 1: Classify
    classification = classify_figure(image_bytes, context)
    fig_type = classification.get("figure_type", "xy_chart")
    complexity = classification.get("complexity", "medium")
    n_panels = classification.get("sub_panels")

    # Choose model based on complexity
    model = MODEL_HARD if complexity == "high" else MODEL

    # Pass 2: Extract
    data = _extract_by_type(image_bytes, fig_type, context, model, n_panels)

    result = {
        "figure_class": classification,
        "data": data,
    }

    # Pass 3: Verify
    if verify:
        verification = _verify_extraction(image_bytes, data, model)
        result["verification"] = verification

        # Auto-apply corrections if any
        corrections = verification.get("corrections", {})
        if corrections:
            data = _apply_corrections(data, corrections)
            result["data"] = data

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


def digitize_region(image_bytes: bytes, bbox: tuple[int, int, int, int], context: str = "") -> dict:
    """Crop a region of a page image and digitize it.

    Use this to isolate a single figure from a page that contains
    multiple figures or surrounding text.

    Args:
        image_bytes: Full page PNG bytes.
        bbox: (left, top, right, bottom) pixel coordinates.
        context: Text context.

    Returns:
        Digitized data for the cropped region.
    """
    img = Image.open(io.BytesIO(image_bytes))
    cropped = img.crop(bbox)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return digitize_figure(buf.getvalue(), context=context)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_by_type(image_bytes: bytes, fig_type: str, context: str, model: str, n_panels: int | None) -> dict:
    """Route to type-specific extraction prompt."""
    prompts = {
        "xy_chart": XY_CHART_PROMPT,
        "diagram": DIAGRAM_PROMPT,
        "contour_map": CONTOUR_MAP_PROMPT,
        "table_image": TABLE_IMAGE_PROMPT,
    }

    if fig_type == "multi_panel":
        prompt = MULTI_PANEL_PROMPT.format(n_panels=n_panels or "multiple")
    else:
        prompt = prompts.get(fig_type, XY_CHART_PROMPT)

    if context:
        prompt += f"\n\nContext from the document: {context}"

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=16384,
        messages=[{
            "role": "user",
            "content": [
                _image_block(image_bytes),
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _parse_json(message.content[0].text)


def _verify_extraction(image_bytes: bytes, extracted_data: dict, model: str) -> dict:
    """Send extraction back to Claude with the original image for verification."""
    client = anthropic.Anthropic()
    prompt = VERIFY_PROMPT.format(extracted_json=json.dumps(extracted_data, indent=2))

    message = client.messages.create(
        model=model,
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
    """Upscale image if it's too small for accurate extraction."""
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
        # Remove first line (```json) and last line (```)
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        text = "\n".join(lines[1:end])
    return json.loads(text)
