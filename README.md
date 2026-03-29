# bldg-code-2-json

Extract building code PDFs (ASCE 7-22, IBC, ACI 318, etc.) into structured, machine-readable JSON.

## Quick Start

```bash
# Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set API key (required for LLM-powered steps)
export ANTHROPIC_API_KEY=sk-ant-...

# Run the full pipeline on a chapter
python cli.py pipeline \
  --pdf path/to/asce7-22.pdf \
  --standard "ASCE 7-22" \
  --chapter 26 \
  --start-page 1 \
  --end-page 30
```

## Pipeline Workflow

The `pipeline` command runs all four stages automatically:

```
                         +-------------+
                         |   PDF File  |
                         +------+------+
                                |
                    STEP 1: EXTRACT
                                |
                         +------v------+
                         |  parse_pdf  |  pdfplumber: text, tables, figures
                         +------+------+
                                |
                    +-----------+-----------+
                    |                       |
             +------v------+       +-------v-------+
             |structure_page|       |structure_figures|  Claude API
             | + few-shot   |       |  (classify +   |
             |   gold refs  |       |   digitize)    |
             +------+------+       +-------+-------+
                    |                       |
                    +-----------+-----------+
                                |
                         +------v------+
                         | post_process |  deterministic cleanup
                         | + deduplicate|  (operators, nulls, IDs)
                         +------+------+
                                |
                         +------v------+
                         |  raw JSON   |  output/raw/
                         +------+------+
                                |
                    STEP 2: FIX
                                |
                         +------v------+
                         | post_process |  deterministic (idempotent)
                         +------+------+
                                |
                         +------v------+
                         |  validate   |  schema check
                         +------+------+
                                |
                      +---------+---------+
                      | failures?         | all valid?
                      v                   v
               +------+------+     (skip retry)
               |retry_elements|
               | (Claude API) |  re-prompt with errors + schema
               +------+------+
                      |
                      +--------+----------+
                               |
                         +-----v-------+
                         |  fixed JSON |  output/fixed/
                         +-----+-------+
                               |
                    STEP 3: QC
                               |
              +----------------+----------------+
              |                |                |
       +------v------+ +------v-------+ +------v------+
       |   schema    | | completeness | | spot_check  |
       |  validate   | |(PDF coverage)| |(Claude API) |
       +------+------+ +------+-------+ +------+------+
              |                |                |
              +----------------+----------------+
                               |
                    STEP 4: CALIBRATION
                               |
                         +-----v-------+
                         |  compare vs |  deterministic
                         |  gold set   |  field-level + numeric tolerance
                         +-----+-------+
                               |
                         +-----v-------+
                         |   report    |  output/qc/
                         +-------------+
```

## CLI Commands

### `pipeline` -- Full end-to-end (recommended)

Extract + fix + QC + calibration in one command.

```bash
python cli.py pipeline \
  --pdf chapter.pdf \
  --standard "ASCE 7-22" \
  --chapter 26 \
  --start-page 1 \
  --end-page 30 \
  --max-retries 3 \
  --spot-check-size 10
```

### `extract` -- Extract only

Parse PDF and structure elements via LLM. Outputs raw JSON.

```bash
python cli.py extract \
  --pdf chapter.pdf \
  --standard "ASCE 7-22" \
  --chapter 26 \
  --start-page 1
```

### `fix` -- Fix existing extraction

Run post-processor + LLM retry on previously extracted JSON.

```bash
python cli.py fix \
  --file output/raw/asce722-ch26.json \
  --pdf chapter.pdf \
  --max-retries 3
```

### `qc` -- Quality checks

Schema validation, completeness, spot-check, cross-reference checks.

```bash
python cli.py qc \
  --file output/fixed/asce722-ch26-fixed.json \
  --pdf chapter.pdf \
  --spot-check-size 10
```

### `refine` -- Auto-optimize

Iteratively re-extract and score, maximizing a composite objective.

```bash
python cli.py refine \
  --pdf chapter.pdf \
  --standard "ASCE 7-22" \
  --chapter 26 \
  --max-iterations 5 \
  --target-score 0.90
```

## Output Structure

```
output/
  raw/              # Direct LLM extraction output
  fixed/            # After post-processor + LLM retry
  validated/        # Only if 100% schema valid
  qc/               # QC and pipeline reports
```

## Element Schema

Each element follows `schema/element.schema.json` (JSON Schema Draft 2020-12):

```json
{
  "id": "ASCE7-22-26.5-T1",
  "type": "table|provision|formula|figure|skipped_figure|reference|definition",
  "source": { "standard": "ASCE 7-22", "chapter": 26, "section": "26.5", "page": 1 },
  "title": "Wind Speed Table",
  "data": { ... },
  "cross_references": ["ASCE7-22-26.2-1"],
  "metadata": { "extracted_by": "auto", "qc_status": "pending" }
}
```

## Gold Standard

Reference elements live in `schema/gold/` as individual JSON files. Used for:
- Few-shot examples in extraction prompts
- Calibration scoring (deterministic accuracy measurement)

```bash
# Generate draft gold set from existing extraction
python -m extract.gold_standard

# Load and inspect
python -c "from extract.gold_standard import load_gold_elements; print(len(load_gold_elements()))"
```

## Testing

```bash
# Run all tests (no API key needed -- all LLM calls are mocked)
python -m pytest tests/ -v

# Run specific test suites
python -m pytest tests/test_calibration.py -v
python -m pytest tests/test_element_retry.py -v
python -m pytest tests/test_fewshot.py -v
```

## Architecture

```
cli.py                    # Click CLI entry point
extract/
  pdf_parser.py           # PDF -> text, tables, figures (pdfplumber)
  llm_structurer.py       # Text/tables -> structured JSON (Claude API)
  post_processor.py       # Deterministic cleanup (no API)
  element_retry.py        # LLM retry for schema failures (Claude API)
  figure_digitizer.py     # Figure classification + digitization
  gold_standard.py        # Gold element management
qc/
  schema_validator.py     # JSON Schema validation
  completeness.py         # PDF coverage analysis
  spot_check.py           # LLM accuracy verification (Claude API)
  calibration.py          # Gold standard comparison (no API)
refine/
  objective.py            # Composite scoring function
  optimizer.py            # Iterative refinement loop
schema/
  element.schema.json     # JSON Schema (Draft 2020-12)
  gold/                   # Gold standard reference elements
```
