"""
Tests for the 'fix' CLI command.

Uses Click's CliRunner and mocks all LLM calls.

Test cases:
1. fix --help exits 0 and shows all options
2. Post-processor runs before retry (deterministic fixes applied)
3. Output JSON file created with correct content
4. Fix report JSON created with correct structure
5. Works without --pdf
6. Works with --pdf (page context passed to retry)
7. --max-retries flag respected
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from cli import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_element(id_suffix="P1"):
    """A schema-valid provision element."""
    return {
        "id": f"ASCE7-22-26.5-{id_suffix}",
        "type": "provision",
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.5",
            "page": None,
        },
        "title": "Test Provision",
        "description": None,
        "data": {
            "rule": "Some rule text",
            "conditions": [],
            "then": "apply method A",
            "else": None,
            "exceptions": [],
        },
        "cross_references": [],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }


def _invalid_element(id_suffix="P2"):
    """An element that fails schema validation (missing conditions)."""
    return {
        "id": f"ASCE7-22-26.5-{id_suffix}",
        "type": "provision",
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.5",
            "page": None,
        },
        "title": "Bad Provision",
        "description": None,
        "data": {
            "rule": "Some rule",
        },
        "cross_references": [],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }


def _element_needing_pp_fix(id_suffix="P3"):
    """An element that post-processor will fix (Unicode operator)."""
    return {
        "id": f"ASCE7-22-26.5-{id_suffix}",
        "type": "provision",
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.5",
            "page": None,
        },
        "title": "PP Fix Provision",
        "description": None,
        "data": {
            "rule": "Wind speed check",
            "conditions": [{"parameter": "V", "operator": "≥", "value": 100, "unit": "mph"}],
            "then": "apply method B",
            "else": None,
            "exceptions": [],
        },
        "cross_references": [],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }


def _mock_llm_response(element_json):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock()]
    mock_msg.content[0].text = json.dumps(element_json)
    return mock_msg


def _write_input(tmp_path, elements, name="input.json"):
    p = tmp_path / name
    p.write_text(json.dumps(elements, indent=2))
    return str(p)


# ---------------------------------------------------------------------------
# 1. fix --help
# ---------------------------------------------------------------------------

class TestFixHelp:

    def test_help_exits_0(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fix", "--help"])
        assert result.exit_code == 0
        assert "--file" in result.output
        assert "--pdf" in result.output
        assert "--max-retries" in result.output
        assert "--output" in result.output
        assert "--start-page" in result.output
        assert "--end-page" in result.output


# ---------------------------------------------------------------------------
# 2. Post-processor runs before retry
# ---------------------------------------------------------------------------

class TestPostProcessorRunsFirst:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_deterministic_fixes_applied(self, MockAnthropic, tmp_path):
        """Unicode operator should be fixed by post-processor, no LLM needed."""
        elements = [_element_needing_pp_fix()]
        input_path = _write_input(tmp_path, elements)
        output_path = str(tmp_path / "fixed.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["fix", "--file", input_path, "--output", output_path])

        assert result.exit_code == 0

        with open(output_path) as f:
            fixed = json.load(f)

        # Post-processor should have fixed ≥ → >=
        assert fixed[0]["data"]["conditions"][0]["operator"] == ">="

        # No LLM calls needed (element is schema-valid after post-processing)
        MockAnthropic.return_value.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Output JSON file created with correct content
# ---------------------------------------------------------------------------

class TestOutputCreated:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_output_file_written(self, MockAnthropic, tmp_path):
        elements = [_valid_element()]
        input_path = _write_input(tmp_path, elements)
        output_path = str(tmp_path / "out" / "fixed.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["fix", "--file", input_path, "--output", output_path])

        assert result.exit_code == 0
        assert Path(output_path).exists()

        with open(output_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["id"] == "ASCE7-22-26.5-P1"


# ---------------------------------------------------------------------------
# 4. Fix report created with correct structure
# ---------------------------------------------------------------------------

class TestFixReportStructure:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_report_has_required_keys(self, MockAnthropic, tmp_path):
        invalid = _invalid_element("P2")
        valid_fixed = _valid_element("P2")
        elements = [_valid_element("P1"), invalid]
        input_path = _write_input(tmp_path, elements)
        output_path = str(tmp_path / "fixed.json")

        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_llm_response(valid_fixed)

        runner = CliRunner()
        result = runner.invoke(cli, ["fix", "--file", input_path, "--output", output_path])

        assert result.exit_code == 0

        report_path = tmp_path / "input-fix-report.json"
        assert report_path.exists()

        with open(report_path) as f:
            report = json.load(f)

        assert "post_processor_fixes" in report
        assert "retry_report" in report
        assert "fixed" in report["retry_report"]
        assert "still_failing" in report["retry_report"]
        assert "skipped" in report["retry_report"]


# ---------------------------------------------------------------------------
# 5. Works without --pdf
# ---------------------------------------------------------------------------

class TestWithoutPdf:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_no_pdf_flag_works(self, MockAnthropic, tmp_path):
        elements = [_valid_element()]
        input_path = _write_input(tmp_path, elements)
        output_path = str(tmp_path / "fixed.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["fix", "--file", input_path, "--output", output_path])

        assert result.exit_code == 0
        assert "Parsing PDF" not in result.output


# ---------------------------------------------------------------------------
# 6. Works with --pdf (page context passed)
# ---------------------------------------------------------------------------

class TestWithPdf:

    @patch("extract.element_retry.anthropic.Anthropic")
    @patch("extract.pdf_parser.parse_pdf")
    def test_pdf_flag_parses_pages(self, mock_parse_pdf, MockAnthropic, tmp_path):
        mock_parse_pdf.return_value = [MagicMock()]  # one fake page

        elements = [_valid_element()]
        input_path = _write_input(tmp_path, elements)
        output_path = str(tmp_path / "fixed.json")

        # Create a fake PDF file so click.Path(exists=True) passes
        fake_pdf = tmp_path / "fake.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4")

        runner = CliRunner()
        result = runner.invoke(cli, [
            "fix", "--file", input_path,
            "--pdf", str(fake_pdf),
            "--start-page", "5",
            "--end-page", "10",
            "--output", output_path,
        ])

        assert result.exit_code == 0
        assert "Parsing PDF" in result.output
        mock_parse_pdf.assert_called_once_with(str(fake_pdf), start_page=5, end_page=10)


# ---------------------------------------------------------------------------
# 7. --max-retries flag respected
# ---------------------------------------------------------------------------

class TestMaxRetriesFlag:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_max_retries_passed_through(self, MockAnthropic, tmp_path):
        invalid = _invalid_element("P2")
        elements = [invalid]
        input_path = _write_input(tmp_path, elements)
        output_path = str(tmp_path / "fixed.json")

        mock_client = MockAnthropic.return_value
        # Always return invalid so all retries are used
        mock_client.messages.create.return_value = _mock_llm_response(invalid)

        runner = CliRunner()
        result = runner.invoke(cli, [
            "fix", "--file", input_path,
            "--max-retries", "2",
            "--output", output_path,
        ])

        assert result.exit_code == 0
        assert mock_client.messages.create.call_count == 2
