# Environment

**What belongs here:** Required env vars, external dependencies, setup notes.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

---

## Python Environment

- Python 3.14 via system install
- Virtual env at `.venv/` (created with `python3 -m venv .venv`)
- All commands run via `.venv/bin/python`

## Dependencies

- pdfplumber: PDF text/table extraction
- anthropic: Claude API client
- jsonschema: JSON Schema validation (Draft 2020-12)
- click: CLI framework
- Pillow: Image processing for figure digitization
- pytest: Test framework (to be added)

## API Keys

- `ANTHROPIC_API_KEY`: Required for LLM calls. Stored in `.env` (gitignored).
- Tests MUST NOT require this key — all LLM calls mocked in tests.

## Source Data

- Input PDFs in `input/` (gitignored)
- Current target: ASCE 7-22 Chapter 26 (Wind Loads — General Requirements), 20 pages
- Extraction output in `output/raw/`, QC reports in `output/qc/`
