# User Testing

## Validation Surface

- **CLI only** — no web UI, no API server, no browser testing needed
- All validation through shell commands: Python CLI invocations, file inspection, pytest runs
- Tool: shell commands via Execute

## Validation Concurrency

- Machine: 16 GB RAM, 10 CPUs (macOS)
- All tests are unit tests with mocked LLM — very lightweight
- No services to start, no ports needed
- Max concurrent validators: **5** (tests are CPU-light, memory-light)

## Test Commands

- Install: `.venv/bin/python -m pip install -r requirements.txt`
- Run tests: `.venv/bin/python -m pytest tests/ -v`
- Run specific: `.venv/bin/python -m pytest tests/test_post_processor.py -v`
- CLI check: `.venv/bin/python cli.py --help`

## Known Constraints

- Tests must not make real Anthropic API calls (mock all LLM interactions)
- PDF files in input/ are gitignored — tests use synthetic fixtures
- output/ directory files are gitignored — tests create temp files via tmp_path
