# Claude Code Prompts — Remaining Features

Run these in order from the project root (`/Users/gabe/projects/bldg-code-2-json`).
Before starting, run: `.venv/bin/python -m pytest tests/ -v --tb=short` to confirm 108 tests pass.

---

## PROMPT 1: Element Retry Module

```
Read these files first to understand the codebase:
- schema/element.schema.json (the JSON Schema)
- extract/post_processor.py (existing post-processor)
- qc/schema_validator.py (validate_element, validate_chapter functions)
- extract/llm_structurer.py (how extraction works)
- tests/test_post_processor.py (test patterns to follow)

Then create extract/element_retry.py with element-level retry functionality.

Core function: retry_elements(elements, qc_results, pages=None, max_retries=3, schema=None) -> tuple[list[dict], dict]

Behavior:
- Takes extracted elements, QC results (from validate_chapter + optionally spot_check), and optional parsed PDF pages for context
- Identifies elements needing retry: (a) schema validation failures from qc_results, (b) elements with spot-check score < 0.7 if spot_check results provided
- For already-valid elements: skip, include unchanged in output, record in report as 'skipped'
- For each failing element:
  - Build a targeted re-prompt containing: the original element JSON, the specific validation error message, the relevant schema constraints (e.g., allowed enum values), and the PDF page text if pages provided
  - Send to Claude via anthropic.Anthropic().messages.create with model "claude-sonnet-4-20250514"
  - Parse the response as JSON
  - Validate the corrected element against the schema
  - If valid: accept, record in report as 'fixed' with retry count
  - If still invalid and retries remaining: retry with updated error
  - If max retries exhausted: keep best attempt, record as 'still_failing'
- Handle API errors gracefully (catch anthropic exceptions, count as failed attempt, continue)
- Return (all_elements, retry_report)
- retry_report structure: {fixed: {id: {retries, original_errors}}, still_failing: [ids], skipped: [ids], summary: {total_attempted, total_fixed, total_still_failing}}

TDD approach — create tests/test_element_retry.py FIRST with failing tests, then implement.
Mock ALL anthropic.Anthropic calls using unittest.mock.patch. Tests must pass without ANTHROPIC_API_KEY.

Test cases needed:
- Successful retry (mock returns valid element)
- Max retries exhausted (mock always returns invalid)
- Already-valid elements skipped (no LLM calls)
- Second-attempt success (first mock returns invalid, second returns valid)
- API error handling (mock raises exception)
- Configurable max_retries (parameterized test)
- Retry report structure validation
- Both schema failures and low spot-check scores targeted

After implementing, verify:
- .venv/bin/python -m pytest tests/test_element_retry.py -v
- .venv/bin/python -c "from extract.element_retry import retry_elements; print('Import OK')"
- .venv/bin/python -m pytest tests/ -v --tb=short (ALL tests pass including existing 108)

Commit when done.
```

---

## PROMPT 2: Fix CLI Command

```
Read these files first:
- cli.py (existing Click CLI with extract, qc, run, refine commands — follow the same patterns)
- extract/element_retry.py (just created — the retry_elements function)
- extract/post_processor.py (the post_process function)
- qc/schema_validator.py (validate_chapter function)
- extract/pdf_parser.py (parse_pdf function)

Add a 'fix' command to cli.py that chains post-processor + element retry.

CLI signature:
  bldg-code-2-json fix --file <raw_json_path> [--pdf <pdf_path>] [--max-retries 3] [--output <output_path>] [--start-page 1] [--end-page N]

Behavior:
1. Load elements from --file
2. Run post_process(elements) to apply deterministic fixes
3. Count how many elements changed (compare before/after for post_processor_fixes count)
4. Run validate_chapter on post-processed elements to find remaining failures
5. If --pdf provided, parse PDF pages for context (using parse_pdf with --start-page/--end-page)
6. Run retry_elements on post-processed elements with QC results and optional pages
7. Write fixed elements to --output (default: output/fixed/{stem}-fixed.json)
8. Write fix report to output/fixed/{stem}-fix-report.json containing: post_processor_fixes count, retry_fixes (from retry report), still_failing, summary
9. Print summary to stdout

Add tests to tests/test_element_retry.py (or create tests/test_fix_cli.py) using Click's CliRunner.
Mock ALL LLM calls. Test cases:
- fix --help exits 0 and shows all options
- Post-processor runs before retry (verify deterministic fixes applied)
- Output JSON file created with correct content
- Fix report JSON created with correct structure
- Works without --pdf
- Works with --pdf (page context in retry prompts)
- --max-retries flag respected

After implementing, verify:
- .venv/bin/python cli.py fix --help
- .venv/bin/python -m pytest tests/ -v --tb=short (ALL tests pass)

Commit when done.
```

---

## PROMPT 3: Gold Standard Module

```
Read these files first:
- schema/element.schema.json (element structure)
- extract/post_processor.py (post_process function — used to clean elements before gold selection)
- qc/schema_validator.py (validate_element — used to validate gold files)
- output/raw/asce722-ch26.json (real extraction data — source for draft gold set)

Create extract/gold_standard.py with these functions:

1. load_gold_elements(gold_dir='schema/gold') -> list[dict]
   - Load all .json files from gold_dir, each file is one element
   - Validate each against schema using validate_element
   - Skip/warn on malformed files (log warning, don't crash)
   - Return empty list if dir missing or empty

2. generate_draft_gold_set(elements, max_per_type=3) -> list[dict]
   - Filter to elements that pass schema validation
   - Select up to max_per_type per element type (table, provision, formula, figure, reference, definition)
   - Prefer elements with metadata.qc_status == 'passed'
   - Set metadata.qc_status to 'passed' on selected elements
   - Return selected list

3. write_gold_files(elements, gold_dir='schema/gold')
   - Write each element to gold_dir/<id>.json as pretty-printed JSON (indent=2)
   - Create directory if needed

Also: generate the initial draft gold set by running this at the end of the module (or as a script):
  - Load output/raw/asce722-ch26.json
  - Run post_process on it
  - Run generate_draft_gold_set
  - Run write_gold_files to schema/gold/

Create tests/test_gold_standard.py with TDD. Use tmp_path fixture for file-based tests. No API calls.
Test cases:
- Load valid gold files
- Load empty directory (returns [])
- Load missing directory (returns [])
- Load with malformed file (skips it, returns valid ones)
- Draft generation filters to valid elements only
- Draft generation caps at max_per_type
- Draft generation covers diverse types
- Write creates individual JSON files
- Gold files conform to element schema (validate_element passes)
- Gold elements have qc_status 'passed'

After implementing, verify:
- .venv/bin/python -m pytest tests/test_gold_standard.py -v
- ls schema/gold/*.json (should have files)
- .venv/bin/python -c "from extract.gold_standard import load_gold_elements; golds=load_gold_elements(); print(f'{len(golds)} gold elements loaded')"
- .venv/bin/python -m pytest tests/ -v --tb=short (ALL tests pass)

Commit when done.
```

---

## PROMPT 4: Few-Shot Injection

```
Read these files first:
- extract/llm_structurer.py (structure_page function and STRUCTURE_PROMPT — you'll modify this)
- extract/gold_standard.py (load_gold_elements — just created)
- schema/gold/ directory (the gold files just generated)

Update extract/llm_structurer.py to inject gold standard examples as few-shot examples.

Changes to structure_page():
1. At the start of the function, call load_gold_elements() to get available gold examples
2. If gold examples available, select up to 3 that are type-relevant:
   - If the page has tables in its content, prefer gold table examples
   - If provisions, prefer provision examples
   - If mixed content, pick diverse types
3. Append a 'REFERENCE EXAMPLES' section to the prompt with selected gold elements formatted as JSON
4. If no gold elements available, proceed without examples — no crash, no empty section in prompt

Create tests in tests/test_fewshot.py (or add to tests/test_gold_standard.py).
Mock anthropic.Anthropic to capture the prompt text sent to the API.
Test cases:
- Gold examples appear in the prompt when gold files exist
- Type-relevant selection works (page with table gets table example)
- Graceful fallback when no gold available (no crash, no gold section in prompt)
- At most 3 examples injected (even with many gold elements)

After implementing, verify:
- .venv/bin/python -m pytest tests/ -k fewshot -v
- .venv/bin/python -m pytest tests/ -v --tb=short (ALL tests pass)

Commit when done.
```

---

## PROMPT 5: Calibration Scoring + Objective Function Update

```
Read these files first:
- extract/gold_standard.py (load_gold_elements)
- qc/schema_validator.py (for reference on validation patterns)
- refine/objective.py (score_run function and WEIGHTS dict — you'll modify this)
- qc/spot_check.py (current accuracy scoring via LLM — calibration replaces this when gold available)

Create qc/calibration.py with two functions:

1. score_against_gold(extracted, gold, rtol=1e-3) -> list[dict]
   For each gold element, find matching extracted element by ID. Compare:
   - type_match: bool (type string equality)
   - id_match: bool (exact string equality)
   - data_match: field-level comparison with numeric tolerance (rtol parameter).
     For tables: compare column names, row values
     For provisions: compare rule text, conditions
     For formulas: compare expression, parameter names, sample values
   - xref_match: order-insensitive set comparison of cross_references
   - score: float 0-1 aggregate of field matches
   Return list of {element_id, type_match, id_match, data_match, xref_match, score, details}

2. calibration_report(extracted, gold, rtol=1e-3) -> dict
   Calls score_against_gold, computes aggregate stats. Returns:
   {
     per_element: [...results from score_against_gold...],
     aggregate: {accuracy: float, type_match_rate: float, elements_compared: int, elements_missing: int, missing_ids: [str]},
     timestamp: ISO 8601 string
   }
   Must be deterministic (same inputs -> same output except timestamp).

Update refine/objective.py:
- Import load_gold_elements from extract.gold_standard and calibration_report from qc.calibration
- In score_run(), if gold elements exist (load_gold_elements() returns non-empty):
  - Compute calibration score using calibration_report
  - Use calibration accuracy for the "accuracy" component (weight 0.4) instead of LLM spot-check
- If no gold elements, fall back to spot-check exactly as before (no regression)
- Ensure composite score stays in [0, 1]

Create tests/test_calibration.py with TDD:
- Per-element field-level comparison works
- Type match correctly identifies matches/mismatches
- Numeric values compared with tolerance (0.85 vs 0.8504 passes, 0.85 vs 0.90 fails)
- Cross-references compared as sets (order-insensitive)
- Calibration report has correct structure
- Missing elements reported (gold element with no extracted match)
- Calibration is deterministic (two calls = same output)

Create tests/test_objective.py:
- score_run uses calibration when gold available (mock gold + mock spot_check)
- score_run falls back to spot-check without gold (empty gold, mock spot_check)
- Composite score stays in [0, 1] with adversarial inputs

Use synthetic gold elements in fixtures (no real data needed). No real API calls.

After implementing, verify:
- .venv/bin/python -m pytest tests/test_calibration.py -v
- .venv/bin/python -m pytest tests/test_objective.py -v
- .venv/bin/python -c "from qc.calibration import calibration_report; print('Import OK')"
- .venv/bin/python -m pytest tests/ -v --tb=short (ALL tests pass)

Commit when done.
```

---

## Final Verification

After all 5 prompts, run:
```bash
.venv/bin/python -m pytest tests/ -v --tb=short
.venv/bin/python cli.py --help
.venv/bin/python cli.py fix --help
git log --oneline -10
git push
```
