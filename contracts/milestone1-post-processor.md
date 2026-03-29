# Milestone 1 — Deterministic Post-Processor: Validation Contracts

---

## PP — Post-Processor Functionality

### VAL-PP-001: Module exists at expected path
The file `extract/post_processor.py` must exist and be importable as a Python module.
Tool: shell command
Evidence: `python3 -c "from extract.post_processor import post_process; print('OK')"` exits 0.

### VAL-PP-002: Operator normalization — Unicode symbols
Given an element with `conditions` containing `"operator": "≤"`, `"≥"`, `"≠"`, or `"≈"`, after post-processing the operator values must be `"<="`, `">="`, `"!="`, and `"=="` respectively.
Tool: shell command
Evidence: Run pytest test that feeds these Unicode operators and asserts normalized output matches the schema enum `["==", "!=", ">", ">=", "<", "<=", "in", "not_in"]`.

### VAL-PP-003: Operator normalization — English words
Given an element with `conditions` containing `"operator": "equals"`, `"greater than"`, `"less than"`, `"at least"`, `"at most"`, or `"not equal"`, after post-processing they must be normalized to `"=="`, `">"`, `"<"`, `">="`, `"<="`, and `"!="` respectively.
Tool: shell command
Evidence: Run pytest test asserting each English-word operator maps to the correct symbol.

### VAL-PP-004: Operator normalization — already-valid operators pass through
Given an element with `conditions` containing `"operator": "=="`, after post-processing the operator must remain `"=="` unchanged.
Tool: shell command
Evidence: Run pytest test confirming all schema-valid operators (`==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`) pass through unmodified.

### VAL-PP-005: Operator normalization — empty conditions array
Given an element with `"conditions": []`, post-processing must not raise and the conditions array must remain empty.
Tool: shell command
Evidence: Run pytest test with empty conditions array; no exception; output identical to input for that field.

### VAL-PP-006: Null-to-empty-string coercion — description field
Given an element with `"description": null`, if the schema field type is `["string", "null"]` this is already valid, but given an element where a string-only field is `null`, post-processing must coerce it to `""`.
Tool: shell command
Evidence: Run pytest test confirming string-required fields with null values become `""` after post-processing.

### VAL-PP-007: Null-to-empty-string coercion — nested string fields
Given a provision element with `"data": {"rule": null, ...}`, since `rule` is required as `string` type, post-processing must coerce `null` to `""`.
Tool: shell command
Evidence: Run pytest test with `null` in `data.rule`; output shows `data.rule == ""`.

### VAL-PP-008: Null-to-empty-string coercion — already-valid strings unchanged
Given an element with `"title": "Wind Speed Map"`, post-processing must leave it unchanged.
Tool: shell command
Evidence: Run pytest test confirming non-null strings pass through unmodified.

### VAL-PP-009: Remove range fields with null values
Given a formula element with `"parameters": {"z": {"unit": "ft", "range": null}}`, post-processing must remove the `"range"` key entirely from that parameter object.
Tool: shell command
Evidence: Run pytest test; output parameter dict has no `"range"` key.

### VAL-PP-010: Remove range fields with null values — partial null in range
Given a formula element with `"parameters": {"z": {"unit": "ft", "range": [null, 100]}}`, post-processing must remove the `"range"` key (since the range contains a null, making it invalid per schema which requires `items: {type: number}`).
Tool: shell command
Evidence: Run pytest test; output parameter dict has no `"range"` key when range contains null items.

### VAL-PP-011: Remove range fields — valid range preserved
Given a formula element with `"parameters": {"z": {"unit": "ft", "range": [0, 1500]}}`, post-processing must leave the range intact.
Tool: shell command
Evidence: Run pytest test; `"range": [0, 1500]` still present and unchanged.

### VAL-PP-012: ID normalization — strip spaces
Given an element with `"id": "ASCE7-22 - 26.5 - T1"`, post-processing must strip all internal spaces to produce `"ASCE7-22-26.5-T1"`.
Tool: shell command
Evidence: Run pytest test; output `id` has no spaces and matches pattern `^[A-Z0-9]+-[0-9.]+-[A-Za-z0-9.]+(-[A-Za-z0-9]+)?$`.

### VAL-PP-013: ID normalization — ensure pattern compliance
Given an element with `"id": "asce7-22-26.5-T1"` (lowercase), post-processing must uppercase the standard prefix to produce `"ASCE7-22-26.5-T1"` matching the schema pattern.
Tool: shell command
Evidence: Run pytest test; output `id` matches the regex pattern from the JSON schema.

### VAL-PP-014: ID normalization — already-valid IDs unchanged
Given an element with `"id": "ASCE7-22-26.5-T1"`, post-processing must leave it unchanged.
Tool: shell command
Evidence: Run pytest test; input equals output for the `id` field.

### VAL-PP-015: Reclassify definitions — provision with definition keywords
Given an element with `"type": "provision"` and `data.rule` containing text like `"BUILDING, ENCLOSED: A building that does not comply with..."` (a definition pattern), post-processing must change `"type"` to `"definition"`.
Tool: shell command
Evidence: Run pytest test; output `type` is `"definition"`.

### VAL-PP-016: Reclassify definitions — non-definition provisions unchanged
Given an element with `"type": "provision"` and `data.rule` containing `"Buildings with mean roof height h > 60 ft shall use exposure defined in Section 26.7.3"` (a real provision), post-processing must leave `"type"` as `"provision"`.
Tool: shell command
Evidence: Run pytest test; output `type` remains `"provision"`.

### VAL-PP-017: Reclassify definitions — already correct types unchanged
Given elements with `"type": "table"`, `"formula"`, `"figure"`, `"reference"`, or `"skipped_figure"`, post-processing must not change their type.
Tool: shell command
Evidence: Run pytest test with each non-provision type; all remain unchanged.

### VAL-PP-018: Repair figure data shapes — diagram data in figure type
Given an element with `"type": "figure"` but `data` that contains `{"figure_type": "diagram", "description": "...", "skip_reason": "..."}` (a skipped_figure shape), post-processing must change `"type"` to `"skipped_figure"`.
Tool: shell command
Evidence: Run pytest test; output `type` is `"skipped_figure"` and `data` conforms to the skipped_figure_data schema.

### VAL-PP-019: Repair figure data shapes — valid figure data unchanged
Given an element with `"type": "figure"` and valid `figure_data` structure (`{"figure_class": {...}, "data": {...}}`), post-processing must leave `type` as `"figure"`.
Tool: shell command
Evidence: Run pytest test; output `type` remains `"figure"` and data is unchanged.

### VAL-PP-020: Repair figure data shapes — skipped_figure already correct
Given an element with `"type": "skipped_figure"` and valid `skipped_figure_data`, post-processing must not modify it.
Tool: shell command
Evidence: Run pytest test; element passes through unchanged.

### VAL-PP-021: Post-processor handles empty elements array
Given an empty list `[]`, `post_process([])` must return `[]` without raising.
Tool: shell command
Evidence: Run pytest test; result is empty list.

### VAL-PP-022: Post-processor is idempotent
Running `post_process(post_process(elements))` must produce the same result as `post_process(elements)`.
Tool: shell command
Evidence: Run pytest test with mixed element types; double-application equals single application.

### VAL-PP-023: Post-processor preserves all other fields
Given an element with `cross_references`, `metadata`, `source`, etc., post-processing must not drop, add, or modify any fields that are not part of the transform rules.
Tool: shell command
Evidence: Run pytest test; non-transformed fields are identical before and after.

### VAL-PP-024: Post-processor handles nested nulls in conditions
Given a provision with `"conditions": [{"parameter": null, "operator": "==", "value": null, "unit": null}]`, post-processing must coerce string-required fields (`parameter`) from null to `""` but leave `value` (schema type `{}` — any) and `unit` (type `["string", "null"]`) as-is.
Tool: shell command
Evidence: Run pytest test confirming selective null coercion based on schema types.

### VAL-PP-025: Post-processor function signature
`post_process` must accept a `list[dict]` and return a `list[dict]`. It must be a pure function (no side effects, no API calls, no file I/O).
Tool: shell command
Evidence: `python3 -c "import inspect; from extract.post_processor import post_process; sig = inspect.signature(post_process); print(sig)"` shows expected parameter.

---

## SCHEMA — Schema Changes for Definition Type

### VAL-SCHEMA-001: "definition" added to type enum
The JSON schema file `schema/element.schema.json` must include `"definition"` in the `type` property enum: `["table", "provision", "formula", "figure", "skipped_figure", "reference", "definition"]`.
Tool: shell command
Evidence: `python3 -c "import json; s=json.load(open('schema/element.schema.json')); assert 'definition' in s['properties']['type']['enum']; print('OK')"` exits 0.

### VAL-SCHEMA-002: definition_data schema definition exists
`schema/element.schema.json` must contain a `$defs/definition_data` entry defining the data shape for definitions.
Tool: shell command
Evidence: `python3 -c "import json; s=json.load(open('schema/element.schema.json')); assert 'definition_data' in s['\\$defs']; print('OK')"` exits 0.

### VAL-SCHEMA-003: definition_data included in data oneOf
The `data` property's `oneOf` array must include a `$ref` to `#/$defs/definition_data`.
Tool: shell command
Evidence: `python3 -c "import json; s=json.load(open('schema/element.schema.json')); refs=[x.get('\\$ref','') for x in s['properties']['data']['oneOf']]; assert '#/\\$defs/definition_data' in refs; print('OK')"` exits 0.

### VAL-SCHEMA-004: definition_data has required fields
The `definition_data` schema must require at minimum a `term` (string) and `definition` (string) field.
Tool: shell command
Evidence: `python3 -c "import json; s=json.load(open('schema/element.schema.json')); d=s['\\$defs']['definition_data']; assert 'term' in d.get('required',[]); assert 'definition' in d.get('required',[]); print('OK')"` exits 0.

### VAL-SCHEMA-005: Existing element types still validate after schema change
A previously valid `provision` element must still pass schema validation after the schema is updated with the definition type.
Tool: shell command
Evidence: Run pytest test that validates a known-good provision element against the updated schema; passes.

### VAL-SCHEMA-006: A definition element validates against updated schema
A well-formed element with `"type": "definition"` and appropriate `data` must pass JSON Schema validation.
Tool: shell command
Evidence: Run pytest test that validates a definition element; `validate_element()` returns `{"valid": True, "errors": []}`.

### VAL-SCHEMA-007: Schema file is valid JSON Schema Draft 2020-12
The updated `schema/element.schema.json` must itself be valid according to the JSON Schema meta-schema.
Tool: shell command
Evidence: `python3 -c "import json; from jsonschema import Draft202012Validator; s=json.load(open('schema/element.schema.json')); Draft202012Validator.check_schema(s); print('OK')"` exits 0.

---

## INTEG — Integration into Pipeline

### VAL-INTEG-001: Post-processor runs after LLM extraction
In `extract/llm_structurer.py` (or wherever `extract_chapter_from_pages` assembles results), the post-processor must be called on the list of elements after LLM extraction and before the function returns.
Tool: shell command
Evidence: `rg "post_process" extract/llm_structurer.py` shows an import and call to `post_process`. Alternatively, `rg "from extract.post_processor" extract/` confirms the import.

### VAL-INTEG-002: Post-processor runs before QC validation
In the pipeline (e.g., `cli.py` `run` command or `extract_chapter_from_pages`), the post-processor call must occur before `validate_chapter` is called on the elements.
Tool: shell command
Evidence: Inspect code flow — `post_process` is invoked on elements before they are passed to QC functions. `rg -n "post_process|validate_chapter" cli.py extract/llm_structurer.py` shows post_process line number is earlier than validate_chapter.

### VAL-INTEG-003: Post-processor integration does not break extract command
Running `python3 cli.py extract --help` must still succeed (no import errors from the new module).
Tool: shell command
Evidence: `cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python cli.py extract --help` exits 0 and shows usage.

### VAL-INTEG-004: Post-processor integration does not break qc command
Running `python3 cli.py qc --help` must still succeed.
Tool: shell command
Evidence: `cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python cli.py qc --help` exits 0 and shows usage.

### VAL-INTEG-005: Post-processor integration does not break run command
Running `python3 cli.py run --help` must still succeed.
Tool: shell command
Evidence: `cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python cli.py run --help` exits 0 and shows usage.

### VAL-INTEG-006: Post-processor __init__.py export
If `extract/__init__.py` exists, the post_process function should be importable via `from extract.post_processor import post_process` without path manipulation.
Tool: shell command
Evidence: `cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python -c "from extract.post_processor import post_process; print('OK')"` exits 0.

---

## TEST — Test Suite

### VAL-TEST-001: pytest is in requirements.txt
The file `requirements.txt` must include `pytest` as a dependency.
Tool: shell command
Evidence: `grep -i pytest requirements.txt` returns a match (e.g. `pytest>=7.0.0` or similar).

### VAL-TEST-002: Test files exist
At least one test file for the post-processor must exist, named following pytest convention (e.g., `tests/test_post_processor.py` or `test_post_processor.py`).
Tool: shell command
Evidence: `find . -name "test_post_processor*" -o -name "*post_processor*test*" | head` returns at least one file.

### VAL-TEST-003: Test file for schema validator exists
A test file for schema validation must exist (e.g., `tests/test_schema_validator.py`).
Tool: shell command
Evidence: `find . -name "test_schema*" | head` returns at least one file.

### VAL-TEST-004: All post-processor tests pass
Running the post-processor test suite must produce zero failures.
Tool: shell command
Evidence: `cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python -m pytest tests/test_post_processor.py -v` exits 0 with all tests passing.

### VAL-TEST-005: All schema validator tests pass
Running the schema validator test suite must produce zero failures.
Tool: shell command
Evidence: `cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python -m pytest tests/test_schema_validator.py -v` exits 0 with all tests passing.

### VAL-TEST-006: Full test suite passes
Running the entire test suite must produce zero failures.
Tool: shell command
Evidence: `cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python -m pytest -v` exits 0 with all tests passing.

### VAL-TEST-007: Test coverage — operator normalization
The test suite must contain at least one test for each operator mapping: Unicode symbols (≤, ≥, ≠), English words (equals, greater than, etc.), and already-valid operators.
Tool: shell command
Evidence: `rg "≤|≥|≠|equals|greater.than|less.than|already.valid|pass.through" tests/test_post_processor.py` shows matching test cases.

### VAL-TEST-008: Test coverage — null coercion
The test suite must contain at least one test for null-to-empty-string coercion on required string fields.
Tool: shell command
Evidence: `rg "null|None.*coer|coer.*null|empty.*string" tests/test_post_processor.py` shows matching test cases.

### VAL-TEST-009: Test coverage — range removal
The test suite must contain tests for null range removal and valid range preservation.
Tool: shell command
Evidence: `rg "range.*null|null.*range|range.*preserve|valid.*range" tests/test_post_processor.py` shows matching test cases.

### VAL-TEST-010: Test coverage — ID normalization
The test suite must contain tests for ID space stripping and pattern compliance.
Tool: shell command
Evidence: `rg "id.*space|strip.*space|id.*normal|pattern" tests/test_post_processor.py` shows matching test cases.

### VAL-TEST-011: Test coverage — definition reclassification
The test suite must contain tests for reclassifying provisions to definitions and leaving non-definitions unchanged.
Tool: shell command
Evidence: `rg "definition|reclassif|DEFINITION" tests/test_post_processor.py` shows matching test cases.

### VAL-TEST-012: Test coverage — figure repair
The test suite must contain tests for repairing figure data shapes (diagram data in figure → skipped_figure).
Tool: shell command
Evidence: `rg "figure.*repair|skipped_figure|diagram.*figure|figure.*shape" tests/test_post_processor.py` shows matching test cases.

### VAL-TEST-013: Test coverage — edge cases
The test suite must contain tests for: empty element list, idempotency, and preservation of unrelated fields.
Tool: shell command
Evidence: `rg "empty|idempoten|preserv|unchanged" tests/test_post_processor.py` shows matching test cases.

### VAL-TEST-014: Test coverage — definition type validates against schema
The test suite must contain a test that creates a `"type": "definition"` element and validates it against the updated JSON schema using `validate_element()`.
Tool: shell command
Evidence: `rg "definition.*valid|validate.*definition" tests/test_schema_validator.py` shows matching test cases.

### VAL-TEST-015: Tests do not require external API calls
All tests must run without requiring an Anthropic API key or any network access (pure unit tests).
Tool: shell command
Evidence: `unset ANTHROPIC_API_KEY && cd /Users/gabe/projects/bldg-code-2-json && .venv/bin/python -m pytest tests/ -v` passes without network errors.
