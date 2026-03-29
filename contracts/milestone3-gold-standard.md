# Milestone 3 — Gold Standard System: Validation Contract

---

## GOLD — Gold Standard Loading, Validation & Draft Generation

### VAL-GOLD-001: Gold directory exists with JSON files
`schema/gold/` directory exists and contains at least one `.json` file. Each file represents a single human-verified reference element.
Tool: `ls`, `Glob`
Evidence: `ls schema/gold/*.json` returns ≥ 1 file.

### VAL-GOLD-002: Gold files conform to element schema
Every JSON file in `schema/gold/` passes validation against `schema/element.schema.json` (via `qc.schema_validator.validate_element`).
Tool: pytest
Evidence: A test calls `validate_element()` on every gold file; all return `{"valid": True}`.

### VAL-GOLD-003: Gold elements have qc_status "passed"
Every gold element has `metadata.qc_status == "passed"` — only validated elements may serve as gold standard.
Tool: pytest
Evidence: Test iterates gold files and asserts `el["metadata"]["qc_status"] == "passed"`.

### VAL-GOLD-004: load_gold_elements returns typed list
`extract.gold_standard.load_gold_elements()` returns a `list[dict]` containing all elements from `schema/gold/`. If the directory is empty or missing, it returns an empty list without raising.
Tool: pytest
Evidence: Test calls `load_gold_elements()` and asserts result is a list; length matches file count in `schema/gold/`.

### VAL-GOLD-005: load_gold_elements rejects invalid files gracefully
If a malformed JSON file is placed in `schema/gold/`, `load_gold_elements()` either skips it with a logged warning or raises a clear `ValueError` — it does not silently include invalid data.
Tool: pytest (with tmp_path fixture)
Evidence: Test creates a temp gold dir with one valid and one malformed file; function either returns only the valid element or raises with a message naming the bad file.

### VAL-GOLD-006: Draft generation selects best candidates
`extract.gold_standard.generate_draft_gold_set(elements)` filters input elements to only those that: (a) pass schema validation, and (b) were marked accurate by spot-check (or have `qc_status == "passed"`). It returns ≤ N candidates per element type (configurable, default ≤ 3 per type).
Tool: pytest
Evidence: Test passes a mixed list (some schema-valid + qc-passed, some invalid, some qc-failed); output contains only the valid+passed elements, with at most 3 per type.

### VAL-GOLD-007: Draft generation covers all extractable types
`generate_draft_gold_set()` attempts to include at least one element for each type in `{table, provision, formula, figure, reference}` when candidates exist, ensuring the gold set has broad type coverage.
Tool: pytest
Evidence: Given input with valid candidates for every type, the output contains ≥ 1 element of each type.

### VAL-GOLD-008: Draft generation writes gold files
`generate_draft_gold_set()` (or a companion `write_draft_gold_set()`) writes each selected element to `schema/gold/<id>.json` as pretty-printed JSON.
Tool: pytest (with tmp_path)
Evidence: After calling the write function, the temp gold dir contains one JSON file per selected element; each file is valid JSON matching its element dict.

---

## FEWSHOT — Few-Shot Injection into Extraction Prompts

### VAL-FEWSHOT-001: Few-shot examples appear in structuring prompt
When gold elements are available, `extract.llm_structurer.structure_page()` (or the prompt builder it calls) injects 2–3 gold examples of the relevant element types into the prompt sent to Claude.
Tool: pytest (mock `anthropic.Anthropic.messages.create`)
Evidence: Capture the prompt text passed to the mocked API call; assert it contains the string `"gold"` or `"example"` header, and includes JSON snippets matching ≥ 2 gold element IDs.

### VAL-FEWSHOT-002: Few-shot selection is type-relevant
The injected examples are filtered to match the types expected on the current page. For a page containing a table, the examples should include a gold table element (not solely provisions).
Tool: pytest (mock API)
Evidence: When page content includes a table, at least one injected example has `"type": "table"`.

### VAL-FEWSHOT-003: Few-shot injection is optional / graceful
If no gold elements are available (empty `schema/gold/` or gold loading returns `[]`), extraction proceeds normally without few-shot examples — no crash, no empty example block in prompt.
Tool: pytest (mock API, empty gold dir)
Evidence: Prompt text does not contain a gold/example section; extraction returns elements as before.

### VAL-FEWSHOT-004: Few-shot count is bounded
At most 3 gold examples are injected regardless of how many gold elements exist, to keep prompt size manageable.
Tool: pytest (mock API, large gold set)
Evidence: Count of gold example blocks in the captured prompt ≤ 3.

---

## CALIB — Calibration Scoring

### VAL-CALIB-001: Calibration produces per-element field-level comparison
`qc.calibration.score_against_gold(extracted_elements, gold_elements)` returns a list of per-element result dicts, each containing field-level match/mismatch details (not LLM-generated scores).
Tool: pytest
Evidence: Result list has one entry per matched gold element; each entry contains keys `element_id`, `fields`, and `score`.

### VAL-CALIB-002: Type match is checked
Each per-element calibration result includes a `type_match: bool` field indicating whether the extracted element's `type` matches the gold element's `type`.
Tool: pytest
Evidence: For a gold element of type `"table"` matched against an extracted element of type `"provision"`, `type_match` is `False`.

### VAL-CALIB-003: Numeric data values compared with precision tolerance
For table rows and formula samples, numeric values are compared with a configurable tolerance (default `rtol=1e-3`). A value of `0.85` in gold matches `0.850` in extraction but not `0.90`.
Tool: pytest
Evidence: Test with gold value `0.85` and extracted value `0.8504` passes; gold `0.85` vs extracted `0.90` fails. Tolerance is configurable via parameter.

### VAL-CALIB-004: Cross-references compared
Calibration checks that `cross_references` arrays match between gold and extracted elements (order-insensitive set comparison).
Tool: pytest
Evidence: Gold xrefs `["A", "B"]` vs extracted `["B", "A"]` → match. Gold `["A", "B"]` vs extracted `["A"]` → mismatch noted in result.

### VAL-CALIB-005: ID format checked
Calibration verifies the extracted element's `id` matches the gold element's `id` exactly (string equality) — ID format drift is caught.
Tool: pytest
Evidence: Gold ID `"ASCE7-22-26.5-T1"` vs extracted `"ASCE7-22-26.5-T1"` → match. Gold vs `"asce7-22-26.5-T1"` → mismatch.

### VAL-CALIB-006: Calibration report structure
`qc.calibration.calibration_report(extracted, gold)` returns a dict with:
- `"per_element"`: list of per-element results (from VAL-CALIB-001)
- `"aggregate"`: `{"accuracy": float, "type_match_rate": float, "elements_compared": int, "elements_missing": int}`
- `"timestamp"`: ISO 8601 string
Tool: pytest
Evidence: Assert all keys present; `accuracy` is a float in [0, 1]; `elements_compared` equals the number of gold elements that had a matching extracted element by ID.

### VAL-CALIB-007: Missing elements are reported
If a gold element has no corresponding extracted element (by ID), the report lists it under `"elements_missing"` count and includes its ID in a `"missing_ids"` list.
Tool: pytest
Evidence: Pass gold set with 3 elements but extraction with only 2 matching IDs; `elements_missing == 1` and the missing ID appears in `missing_ids`.

### VAL-CALIB-008: Calibration is deterministic
Running `calibration_report()` twice with identical inputs produces byte-identical output (except for `timestamp`).
Tool: pytest
Evidence: Two calls with same inputs; assert `report1["per_element"] == report2["per_element"]` and `report1["aggregate"] == report2["aggregate"]`.

---

## SCORE — Objective Function Integration

### VAL-SCORE-001: Objective function uses calibration when gold data is available
When `schema/gold/` contains valid gold elements, `refine.objective.score_run()` incorporates the calibration accuracy score into its composite score (replacing or supplementing the LLM spot-check accuracy component).
Tool: pytest (mock gold data + mock/patch spot_check)
Evidence: With gold data present, `score_run()` returns a result whose `"components"` dict includes a `"calibration"` or updated `"accuracy"` key derived from the deterministic calibration, not from LLM spot-check alone.

### VAL-SCORE-002: Objective function falls back to spot-check without gold
When `schema/gold/` is empty or missing, `score_run()` behaves exactly as before — it uses LLM spot-check for the accuracy component. No regression.
Tool: pytest (empty gold dir, mock spot_check)
Evidence: Result structure matches the pre-milestone-3 format; `"accuracy"` component comes from `spot_check()`.

### VAL-SCORE-003: Calibration score weight is documented and configurable
The weight given to calibration vs. spot-check in the composite score is defined in `WEIGHTS` (or equivalent config) and can be overridden by the caller.
Tool: code review + pytest
Evidence: `WEIGHTS` dict (or function parameter) includes a calibration-related key; passing a custom weights dict changes the composite score proportionally.

### VAL-SCORE-004: Composite score range preserved
The composite score remains in [0, 1] regardless of whether calibration or spot-check is used.
Tool: pytest
Evidence: With adversarial inputs (all-zeros calibration, all-ones calibration, mixed), composite score is always `0.0 ≤ score ≤ 1.0`.

---

## TEST — Test Suite for Milestone 3

### VAL-TEST-M3-001: Gold standard module has pytest coverage
A test file `tests/test_gold_standard.py` (or similar) exists and contains tests for `load_gold_elements`, `generate_draft_gold_set`, and the gold file write function.
Tool: `Glob`, pytest
Evidence: `pytest tests/test_gold_standard.py` runs ≥ 5 test cases and all pass.

### VAL-TEST-M3-002: Calibration module has pytest coverage
A test file `tests/test_calibration.py` (or similar) exists and contains tests for `score_against_gold` and `calibration_report`, covering type match, numeric comparison, xref comparison, missing elements, and determinism.
Tool: `Glob`, pytest
Evidence: `pytest tests/test_calibration.py` runs ≥ 6 test cases and all pass.

### VAL-TEST-M3-003: Few-shot injection tested with mocked LLM
Tests for few-shot injection mock the Anthropic API and verify prompt content — no real LLM calls are made during testing.
Tool: pytest
Evidence: Test file patches `anthropic.Anthropic` and asserts on the `messages.create` call args.

### VAL-TEST-M3-004: Objective function integration tested with and without gold
Tests for `score_run()` exercise both the gold-available and no-gold paths, confirming correct fallback behavior.
Tool: pytest
Evidence: Two separate test cases in `tests/test_objective.py` (or similar); both pass.

### VAL-TEST-M3-005: Test fixtures use synthetic gold data
Tests do not depend on real ASCE 7-22 extraction output. They use synthetic gold elements constructed in fixtures (via `tmp_path` or inline dicts) that conform to `element.schema.json`.
Tool: pytest, code review
Evidence: No test imports from `output/` or reads from `schema/gold/` directly; fixtures create temp dirs with synthetic `.json` files.

### VAL-TEST-M3-006: All milestone 3 tests pass in CI
Running `pytest tests/ -k "gold or calibration or fewshot"` (or equivalent marker) exits 0 with no failures, errors, or warnings.
Tool: pytest
Evidence: Exit code 0; summary line shows all tests passed.
