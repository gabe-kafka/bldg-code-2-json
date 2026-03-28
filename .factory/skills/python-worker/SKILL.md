---
name: python-worker
description: General-purpose Python implementation worker for the bldg-code-2-json pipeline
---

# Python Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Any feature that involves writing Python modules, updating the JSON schema, creating tests, or modifying the CLI. This covers all implementation work in this mission.

## Required Skills

None — this is a CLI-only Python project. No browser, no TUI.

## Work Procedure

1. **Read the feature description carefully.** Understand preconditions, expected behavior, and verification steps. Read AGENTS.md for conventions and boundaries.

2. **Read existing code.** Before writing anything, read the modules you'll be modifying or depending on. Understand the data structures, function signatures, and patterns in use. Key files:
   - `schema/element.schema.json` — the JSON Schema (understand data shapes)
   - `extract/llm_structurer.py` — how extraction works (PageExtraction → elements)
   - `qc/schema_validator.py` — how validation works (validate_element, validate_chapter)
   - `cli.py` — how CLI commands are structured (Click group + commands)
   - Existing tests in `tests/` if any exist

3. **Write tests first (TDD).** Create the test file BEFORE the implementation:
   - Write failing tests that cover the expected behavior from the feature description
   - Use `unittest.mock.patch` to mock ALL Anthropic API calls — tests must never hit the real API
   - Use synthetic element dicts constructed inline for unit tests
   - Use `tmp_path` fixture for any file-based tests
   - Run tests to confirm they fail: `.venv/bin/python -m pytest tests/test_<module>.py -v`

4. **Implement.** Write the module/changes to make tests pass:
   - Follow existing code patterns (docstrings, imports, Path usage)
   - Keep functions focused — one responsibility per function
   - For schema changes, ensure backward compatibility (existing valid elements must still validate)

5. **Run tests green.** Confirm all new tests pass:
   - `.venv/bin/python -m pytest tests/test_<module>.py -v`
   - Fix any failures before proceeding

6. **Run full test suite.** Ensure no regressions:
   - `.venv/bin/python -m pytest tests/ -v --tb=short`

7. **Verify CLI still works.** Quick smoke test:
   - `.venv/bin/python cli.py --help` (all commands listed, no import errors)
   - If you modified a specific command, run its `--help` too

8. **Manual verification.** For modules that process real data:
   - If the feature involves post-processing, run it on `output/raw/asce722-ch26.json` and verify the output makes sense
   - If the feature involves schema changes, validate a sample element against the updated schema

9. **Commit.** Stage only the files you created/modified. Write a clear commit message.

## Example Handoff

```json
{
  "salientSummary": "Implemented extract/post_processor.py with 6 transform functions (operator normalization, null coercion, range removal, ID repair, definition reclassification, figure shape repair). TDD: wrote 24 tests in tests/test_post_processor.py first, all failing, then implemented to green. Full suite passes (24/24). Verified on real data: post_process on asce722-ch26.json reduced schema failures from 61 to 12.",
  "whatWasImplemented": "extract/post_processor.py: post_process() pure function with operator_map for 15 input patterns, null string coercion for required fields, null range removal, ID space stripping + uppercase prefix, definition keyword detection for reclassification, figure data shape detection for skipped_figure retyping. Updated schema/element.schema.json with definition type + definition_data $def. Updated extract/llm_structurer.py to call post_process after extraction. Added pytest to requirements.txt. Created tests/test_post_processor.py with 24 test cases and tests/test_schema_validator.py with 8 test cases.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": ".venv/bin/python -m pytest tests/test_post_processor.py -v", "exitCode": 0, "observation": "24 passed in 0.3s"},
      {"command": ".venv/bin/python -m pytest tests/test_schema_validator.py -v", "exitCode": 0, "observation": "8 passed in 0.2s"},
      {"command": ".venv/bin/python -m pytest tests/ -v --tb=short", "exitCode": 0, "observation": "32 passed in 0.5s"},
      {"command": ".venv/bin/python cli.py --help", "exitCode": 0, "observation": "All 5 commands listed: extract, qc, run, refine, fix"},
      {"command": ".venv/bin/python -c \"from extract.post_processor import post_process; import json; els=json.load(open('output/raw/asce722-ch26.json')); fixed=post_process(els); print(f'Fixed {len(els)-len([e for e in fixed if True])} elements')\"", "exitCode": 0, "observation": "Post-processor ran successfully on 92 elements"}
    ],
    "interactiveChecks": []
  },
  "tests": {
    "added": [
      {"file": "tests/test_post_processor.py", "cases": [
        {"name": "test_operator_unicode_less_equal", "verifies": "≤ → <="},
        {"name": "test_operator_english_equals", "verifies": "equals → =="},
        {"name": "test_null_string_coercion", "verifies": "null → empty string for required fields"},
        {"name": "test_idempotent", "verifies": "double application equals single"}
      ]},
      {"file": "tests/test_schema_validator.py", "cases": [
        {"name": "test_valid_provision", "verifies": "known-good provision passes"},
        {"name": "test_valid_definition", "verifies": "new definition type passes"}
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Feature depends on a module that doesn't exist yet (e.g., post_processor needed before retry can be built)
- Schema change would break existing validated output files in a way the feature description doesn't address
- Real data in output/raw/ doesn't match expected structure
- Tests require network access that can't be mocked
- Existing code has bugs that block the feature (not introduced by you)
