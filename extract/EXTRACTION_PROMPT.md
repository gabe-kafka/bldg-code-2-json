# Building Code Extraction Prompt

Use this prompt when reading PDF page images to extract structured JSON elements. Give this same prompt to each model (Claude, Codex, etc.) for comparable outputs.

## Instructions

You are extracting building code content from PDF page images into structured JSON.

Read each page image carefully. Extract every distinct piece of content as a JSON element. Do not skip anything — every provision, definition, formula, table, figure, and reference gets its own element.

For all non-figure elements, preserve the building code's wording, symbols, numbers, and citations as exactly as possible. Do not paraphrase code text in authoritative fields. Figures are the exception: they should be described as figures, not reproduced precisely.

Authoritative versus derived fields:
- `table`: `data.columns` and `data.rows` are exact.
- `formula`: `data.expression` and parameter names/units are exact; `data.samples` is derived if included.
- `provision`: `data.rule` is exact; `data.conditions`, `data.then`, `data.else`, and `data.exceptions` are derived structure and must faithfully restate the exact rule text.
- `definition`: `data.term` and `data.definition` are exact; any `conditions` or `exceptions` are derived structure.
- `reference`: `data.target` is exact; `data.url` and `data.parameters` are helper metadata and may be normalized.
- `figure`: `data.description` is descriptive, not exact code wording.

### Element Types

**table** — Tabular lookup data with columns and rows. Extract every row and column exactly.
```json
{"columns": [{"name": "Height (ft)", "unit": "ft"}, {"name": "Exposure B", "unit": null}], "rows": [{"Height (ft)": 15, "Exposure B": 0.57}]}
```

**formula** — Mathematical equation with parameters. Copy the equation or expression, symbols, and parameter names exactly as shown.
```json
{"expression": "qz = 0.00256 * Kz * Kzt * Kd * Ke * V^2", "parameters": {"Kz": {"unit": "dimensionless", "source": "Table 26.10-1"}}}
```

**provision** — Rule, requirement, or conditional logic. Preserve `data.rule` exactly as written in the code. Conditions must use valid operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`.
```json
{"rule": "Full rule text here", "conditions": [{"parameter": "h", "operator": ">", "value": 60, "unit": "ft"}], "then": "use Method X", "else": null, "exceptions": ["Exception text"]}
```

**definition** — Term definition. Preserve both the term and the definition text exactly as written.
```json
{"term": "BASIC WIND SPEED, V", "definition": "Three-second gust speed at 33 ft (10 m) above ground in Exposure C."}
```

**reference** — Pointer to external standard or document. Preserve `data.target` exactly as written. `url` and `parameters` are helper metadata.
```json
{"target": "ASTM E1886", "url": null, "parameters": []}
```

**figure** — Diagram, chart, map, or illustration. Describe what it communicates — do not try to digitize it precisely and do not treat the figure description as exact code wording.
```json
{"figure_type": "flowchart", "description": "Outline of process for determining wind loads. Shows Chapter 26 General Requirements flowing to MWFRS (Chapters 27-29) and C&C (Chapter 30).", "source_pdf_page": 262}
```
Valid figure_type values: `flowchart`, `contour_map`, `geometry_diagram`, `xy_chart`, `table_image`, `detail_drawing`, `photograph`, `other`

### Element Structure

Every element must have this structure:
```json
{
  "id": "ASCE7-22-26.10-T26.10-1",
  "type": "table",
  "source": {"standard": "ASCE 7-22", "chapter": 26, "section": "26.10", "citation": "Table 26.10-1", "page": 277},
  "title": "Table 26.10-1: Velocity Pressure Exposure Coefficients",
  "description": "Optional plain-language summary",
  "data": { ... },
  "cross_references": ["ASCE7-22-26.7.3"],
  "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": null}
}
```

### ID Format

`{STANDARD}-{SECTION}-{SUFFIX}`

- Standard: `ASCE7-22`, `IBC-2021`, `ACI318-19` (no spaces)
- Section: exact printed section or subsection number, e.g. `26.10`, `26.5.1`, `26.2.1`
- Suffix: reuse the official source identifier when one exists, e.g. `T26.10-1`, `E26.10-1`, `F26.1-1`
- Use local sequence suffixes such as `P1`, `P2`, `D1`, `D2`, `R1`, `S1` only when the source does not provide a more specific official identifier for sibling items

### Rules

1. **Be precise with numbers.** Copy values exactly from the image. Do not round or approximate.
2. **Preserve code wording in authoritative fields.** Copy the building code text, equations, symbols, and citations as exactly as possible wherever the field is authoritative.
3. **Derived fields must stay faithful.** `conditions`, `then`, `else`, `exceptions`, formula `samples`, and reference helper metadata may be structured or normalized, but they must not contradict or replace the exact source text.
4. **Preserve official identifiers.** If the source gives an identifier like `Section 26.2.1`, `Eq. (26.10-1)`, `Table 26.10-1`, or `Figure 26.1-1`, capture it exactly in `source.section` or `source.citation` as appropriate and reuse it in the element `id`.
5. **Use valid operators only.** Conditions must use: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`. Do not invent operators like `applies_to` or `requires`.
6. **Cross-reference by element ID.** Link to other elements and sections using IDs.
7. **Figures are the only loose descriptions.** Describe what the figure communicates. Link to the computable elements (tables, formulas, provisions, definitions) that contain the authoritative wording or precise data.
8. **One element per distinct piece of content.** A section with 3 provisions = 3 elements.

### Output Format

Return a JSON array of all elements found on the page(s). No other text.

```json
[
  { ... element 1 ... },
  { ... element 2 ... }
]
```

## Workflow

### Step 1: Render pages
```bash
python cli.py render --pdf input/code.pdf --standard "ASCE 7-22" --chapter 26 --start-page 1 --end-page 20
```

### Step 2: Extract with Model A (e.g., Claude)
Open the page images and give this prompt. Save output as `output/runs/claude-ch26.json`.

### Step 3: Extract with Model B (e.g., Codex)
Same pages, same prompt. Save output as `output/runs/codex-ch26.json`.

### Step 4: Validate both
```bash
python cli.py validate --file output/runs/claude-ch26.json
python cli.py validate --file output/runs/codex-ch26.json
```

### Step 5: Compare
```bash
python cli.py compare \
  --run-a output/runs/claude-ch26.json --label-a claude \
  --run-b output/runs/codex-ch26.json --label-b codex
```

### Step 6: Review disagreements
The comparison report shows exactly which elements and fields disagree. Check those against the original PDF pages. Where both models agree, you have high confidence.
