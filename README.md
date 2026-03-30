# bldg-code-2-json

Extract building code PDFs (ASCE 7-22, IBC, ACI 318, etc.) into structured, machine-readable JSON.

See [ontology.md](ontology.md) for the full specification of what the JSON represents.

## How It Works

```
PDF → render pages to images → Claude reads images → structured JSON → schema validate
```

Single-pass vision extraction. No text parsing, no retry loops. Claude reads each page as a human would and produces structured elements.

For tables, formulas, equations, provisions, definitions, and references, the target is exact preservation of the code's wording, symbols, numbers, and citations in the authoritative fields. Derived helper fields may normalize structure, and figures are the explicit exception: they are captured as descriptive summaries of what the diagram communicates.

Official source identifiers should also be preserved wherever they exist. Section numbers belong in `source.section`, printed labels like `Eq. (26.10-1)` or `Table 26.10-1` belong in `source.citation`, and element `id` should reuse those identifiers when available instead of relying only on local sequence numbers.

## Quick Start

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Render PDF pages to images
python cli.py render \
  --pdf path/to/asce7-22.pdf \
  --standard "ASCE 7-22" \
  --chapter 26 \
  --start-page 1 --end-page 30

# 2. Extract elements by reading page images in Claude Code
#    (open the rendered images, extract JSON per the schema)

# 3. Validate the extraction
python cli.py validate --file output/asce722-ch26.json
```

## CLI Commands

### `render` — Prepare PDF for extraction
Renders PDF pages to PNG images at configurable DPI.

```bash
python cli.py render --pdf chapter.pdf --standard "ASCE 7-22" --chapter 26
```

### `validate` — Check extraction quality
Schema validation, post-processing cleanup, cross-reference analysis, and calibration against gold standard.

```bash
python cli.py validate --file output/extracted.json
```

## Element Types

Six types based on computational role (see [ontology.md](ontology.md)):

| Type | Role | Data Precision |
|------|------|----------------|
| `table` | Directly queryable | Exact authoritative content |
| `formula` | Directly computable | Exact equation/expression; derived samples allowed |
| `provision` | Evaluable as logic | Exact `rule`; derived logic fields |
| `definition` | Vocabulary reference | Exact `term` and `definition`; derived helpers allowed |
| `reference` | External pointer | Exact `target`; normalized helper metadata allowed |
| `figure` | Illustrative context | Best-effort description |

## Output Structure

```
output/
  pages/            # Rendered PDF page images
  fixed/            # Extracted and validated JSON
  qc/               # Validation reports
```

## Architecture

```
cli.py                    # Click CLI (render + validate)
extract/
  pdf_renderer.py         # PDF → page images (PyMuPDF)
  post_processor.py       # Deterministic cleanup (no API)
  gold_standard.py        # Gold element management
qc/
  schema_validator.py     # JSON Schema validation
  calibration.py          # Gold standard comparison
schema/
  element.schema.json     # JSON Schema (Draft 2020-12)
  gold/                   # Gold standard reference elements
ontology.md               # What the JSON represents
```

## Testing

```bash
python -m pytest tests/ -v  # All tests run without API keys
```
