# Architecture

## System Overview

bldg-code-2-json is a Python CLI pipeline that converts building code PDFs into structured JSON. The architecture is a linear pipeline with QC feedback loops.

## Pipeline Flow

```
PDF → pdf_parser → PageExtraction objects → llm_structurer → raw elements
    → post_processor (NEW) → cleaned elements → QC (schema + completeness + spot check)
    → element_retry (NEW) → fixed elements → validated output
```

## Module Map

### extract/ — Extraction Layer
- `pdf_parser.py`: Parses PDF pages into `PageExtraction` dataclasses (text blocks, tables, figure images). Uses pdfplumber. No LLM calls.
- `llm_structurer.py`: Sends page content to Claude to classify and structure into element JSON. One API call per page. Deduplicates by ID.
- `figure_digitizer.py`: 3-pass LLM pipeline for figures: classify → extract (if chart/table image) → verify. Skips diagrams and contour maps.
- `post_processor.py` (NEW): Deterministic transforms on raw elements — operator normalization, null coercion, ID repair, definition reclassification, figure shape repair. Pure function, no API calls.
- `element_retry.py` (NEW): Per-element retry loop. Takes failed elements + errors, re-prompts Claude with targeted context, re-validates, retries up to N times.
- `gold_standard.py` (NEW): Load/validate gold reference elements, generate draft gold sets, inject few-shot examples into prompts.

### qc/ — Quality Control Layer
- `schema_validator.py`: Validates elements against `schema/element.schema.json` using jsonschema.
- `completeness.py`: Compares extracted element IDs/sections against PDF section headings and table/figure labels.
- `spot_check.py`: Sends sample elements + PDF page images back to Claude for accuracy verification.
- `calibration.py` (NEW): Deterministic field-level comparison against gold standard elements. Replaces LLM self-check for accuracy scoring.

### refine/ — Optimization Layer
- `objective.py`: Composite scoring function (schema 0.2, completeness 0.3, accuracy 0.4, xref 0.1). Uses calibration when gold data available.
- `optimizer.py`: Karpathy-style auto-refinement loop. Extracts, scores, asks Claude to propose config changes, applies, repeats.

### schema/ — Data Model
- `element.schema.json`: JSON Schema (Draft 2020-12) defining the element structure. Types: table, provision, formula, figure, skipped_figure, reference, definition (NEW).
- `gold/` (NEW): Human-verified reference elements as individual JSON files.

### cli.py — CLI Interface
Click-based CLI with commands: extract, qc, run, refine, fix (NEW).

## Key Data Structures

- `PageExtraction`: dataclass with page_number, text_blocks, tables, figures
- Element dict: `{id, type, source, title, description, data, cross_references, metadata}`
- `data` field shape varies by type (table_data, provision_data, formula_data, figure_data, skipped_figure_data, reference_data, definition_data)

## Invariants

- All elements must pass schema validation before being written to output/validated/
- Post-processor is idempotent and has no side effects
- Element IDs follow pattern: `^[A-Z0-9]+-[0-9.]+-[A-Za-z0-9.]+(-[A-Za-z0-9]+)?$`
- Gold elements must have qc_status "passed"
- Calibration scoring is deterministic (same inputs → same outputs)

## Configuration

- Model: `claude-sonnet-4-20250514` (used across structurer, digitizer, spot check, retry, optimizer)
- API key: via ANTHROPIC_API_KEY env var (loaded from .env)
- Virtual env: `.venv/` with Python 3.14
