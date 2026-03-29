"""
Tests for extract/element_retry.py — element-level retry with LLM.

All anthropic.Anthropic calls are mocked. No API key needed.

Test cases:
1. Successful retry (mock returns valid element)
2. Max retries exhausted (mock always returns invalid)
3. Already-valid elements skipped (no LLM calls)
4. Second-attempt success (first mock invalid, second valid)
5. API error handling (mock raises exception)
6. Configurable max_retries (parameterized)
7. Retry report structure validation
8. Both schema failures and low spot-check scores targeted
"""

import json
import copy
import pytest
from unittest.mock import patch, MagicMock

from extract.element_retry import retry_elements
from qc.schema_validator import load_schema, validate_element


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCHEMA = load_schema()


def _valid_element(id_suffix="P1", **overrides):
    """Build a minimal schema-valid provision element."""
    el = {
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
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(el.get(key), dict):
            el[key] = {**el[key], **val}
        else:
            el[key] = val
    return el


def _invalid_element(id_suffix="P2"):
    """Build an element that fails schema validation (missing required 'rule')."""
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
            # provision_data requires "rule" and "conditions" — missing "conditions"
            "rule": "Some rule",
        },
        "cross_references": [],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }


def _make_qc_results(elements, extra_failures=None, spot_check_scores=None):
    """Build QC results dict similar to validate_chapter output.

    extra_failures: list of (id, error_msg) to inject
    spot_check_scores: dict of {id: score} for spot-check results
    """
    schema = SCHEMA
    results = {
        "total": len(elements),
        "passed": 0,
        "failed": 0,
        "errors": [],
    }
    for el in elements:
        vr = validate_element(el, schema)
        if vr["valid"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
            results["errors"].append({
                "id": el.get("id", "UNKNOWN"),
                "errors": vr["errors"],
            })

    if extra_failures:
        for eid, msg in extra_failures:
            results["errors"].append({"id": eid, "errors": [msg]})
            results["failed"] += 1

    if spot_check_scores:
        results["spot_check"] = spot_check_scores

    return results


def _mock_llm_response(element_json):
    """Create a mock anthropic message response containing element JSON."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock()]
    mock_msg.content[0].text = json.dumps(element_json)
    return mock_msg


# ---------------------------------------------------------------------------
# 1. Successful retry — mock returns valid element
# ---------------------------------------------------------------------------

class TestSuccessfulRetry:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_retry_fixes_invalid_element(self, MockAnthropic):
        invalid = _invalid_element("P2")
        valid = _valid_element("P2")
        elements = [_valid_element("P1"), invalid]
        qc = _make_qc_results(elements)

        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_llm_response(valid)

        result_elements, report = retry_elements(elements, qc, schema=SCHEMA)

        assert len(result_elements) == 2
        # The fixed element should now be valid
        vr = validate_element(result_elements[1], SCHEMA)
        assert vr["valid"]
        assert "ASCE7-22-26.5-P2" in report["fixed"]
        mock_client.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Max retries exhausted
# ---------------------------------------------------------------------------

class TestMaxRetriesExhausted:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_still_failing_after_max_retries(self, MockAnthropic):
        invalid = _invalid_element("P2")
        elements = [invalid]
        qc = _make_qc_results(elements)

        # Always return the same invalid element
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_llm_response(invalid)

        result_elements, report = retry_elements(elements, qc, max_retries=3, schema=SCHEMA)

        assert len(result_elements) == 1
        assert "ASCE7-22-26.5-P2" in report["still_failing"]
        assert mock_client.messages.create.call_count == 3


# ---------------------------------------------------------------------------
# 3. Already-valid elements skipped
# ---------------------------------------------------------------------------

class TestSkipValidElements:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_no_llm_calls_for_valid_elements(self, MockAnthropic):
        elements = [_valid_element("P1"), _valid_element("P2")]
        qc = _make_qc_results(elements)

        mock_client = MockAnthropic.return_value

        result_elements, report = retry_elements(elements, qc, schema=SCHEMA)

        assert len(result_elements) == 2
        mock_client.messages.create.assert_not_called()
        assert len(report["skipped"]) == 2


# ---------------------------------------------------------------------------
# 4. Second-attempt success
# ---------------------------------------------------------------------------

class TestSecondAttemptSuccess:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_fixes_on_second_try(self, MockAnthropic):
        invalid = _invalid_element("P2")
        valid = _valid_element("P2")
        elements = [invalid]
        qc = _make_qc_results(elements)

        mock_client = MockAnthropic.return_value
        # First call returns still-invalid, second returns valid
        mock_client.messages.create.side_effect = [
            _mock_llm_response(invalid),
            _mock_llm_response(valid),
        ]

        result_elements, report = retry_elements(elements, qc, schema=SCHEMA)

        assert "ASCE7-22-26.5-P2" in report["fixed"]
        assert report["fixed"]["ASCE7-22-26.5-P2"]["retries"] == 2
        assert mock_client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# 5. API error handling
# ---------------------------------------------------------------------------

class TestAPIErrorHandling:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_api_error_counts_as_failed_attempt(self, MockAnthropic):
        invalid = _invalid_element("P2")
        valid = _valid_element("P2")
        elements = [invalid]
        qc = _make_qc_results(elements)

        mock_client = MockAnthropic.return_value
        # First call raises, second returns valid
        mock_client.messages.create.side_effect = [
            Exception("API rate limit"),
            _mock_llm_response(valid),
        ]

        result_elements, report = retry_elements(elements, qc, max_retries=3, schema=SCHEMA)

        assert "ASCE7-22-26.5-P2" in report["fixed"]
        assert mock_client.messages.create.call_count == 2

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_all_api_errors_keeps_original(self, MockAnthropic):
        invalid = _invalid_element("P2")
        elements = [invalid]
        qc = _make_qc_results(elements)

        mock_client = MockAnthropic.return_value
        mock_client.messages.create.side_effect = Exception("API down")

        result_elements, report = retry_elements(elements, qc, max_retries=2, schema=SCHEMA)

        assert len(result_elements) == 1
        assert "ASCE7-22-26.5-P2" in report["still_failing"]


# ---------------------------------------------------------------------------
# 6. Configurable max_retries (parameterized)
# ---------------------------------------------------------------------------

class TestConfigurableMaxRetries:

    @pytest.mark.parametrize("max_retries", [1, 2, 5])
    @patch("extract.element_retry.anthropic.Anthropic")
    def test_respects_max_retries(self, MockAnthropic, max_retries):
        invalid = _invalid_element("P2")
        elements = [invalid]
        qc = _make_qc_results(elements)

        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_llm_response(invalid)

        result_elements, report = retry_elements(
            elements, qc, max_retries=max_retries, schema=SCHEMA
        )

        assert mock_client.messages.create.call_count == max_retries


# ---------------------------------------------------------------------------
# 7. Retry report structure validation
# ---------------------------------------------------------------------------

class TestRetryReportStructure:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_report_has_required_keys(self, MockAnthropic):
        valid = _valid_element("P1")
        invalid = _invalid_element("P2")
        fixed = _valid_element("P2")
        elements = [valid, invalid]
        qc = _make_qc_results(elements)

        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_llm_response(fixed)

        _, report = retry_elements(elements, qc, schema=SCHEMA)

        # Required top-level keys
        assert "fixed" in report
        assert "still_failing" in report
        assert "skipped" in report

        # fixed entry structure
        assert isinstance(report["fixed"], dict)
        entry = report["fixed"]["ASCE7-22-26.5-P2"]
        assert "retries" in entry
        assert "original_errors" in entry

        # skipped and still_failing are lists
        assert isinstance(report["still_failing"], list)
        assert isinstance(report["skipped"], list)
        assert "ASCE7-22-26.5-P1" in report["skipped"]


# ---------------------------------------------------------------------------
# 8. Both schema failures and low spot-check scores targeted
# ---------------------------------------------------------------------------

class TestSpotCheckScoresTargeted:

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_low_spot_check_score_triggers_retry(self, MockAnthropic):
        """Elements with low spot-check scores should be retried even if schema-valid."""
        el1 = _valid_element("P1")
        el2 = _valid_element("P2")
        elements = [el1, el2]

        # Both pass schema, but P2 has low spot-check score
        qc = _make_qc_results(elements, spot_check_scores={
            "ASCE7-22-26.5-P1": 0.95,
            "ASCE7-22-26.5-P2": 0.3,
        })

        improved = _valid_element("P2", title="Improved Provision")
        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_llm_response(improved)

        result_elements, report = retry_elements(
            elements, qc, schema=SCHEMA, spot_check_threshold=0.5
        )

        # P2 should have been retried
        mock_client.messages.create.assert_called_once()
        assert "ASCE7-22-26.5-P2" in report["fixed"]
        # P1 should be skipped (high score)
        assert "ASCE7-22-26.5-P1" in report["skipped"]

    @patch("extract.element_retry.anthropic.Anthropic")
    def test_schema_failure_and_spot_check_both_targeted(self, MockAnthropic):
        """Both schema-invalid and low-score elements get retried."""
        valid_low_score = _valid_element("P1")
        schema_invalid = _invalid_element("P2")
        elements = [valid_low_score, schema_invalid]

        qc = _make_qc_results(elements, spot_check_scores={
            "ASCE7-22-26.5-P1": 0.2,
        })

        mock_client = MockAnthropic.return_value
        mock_client.messages.create.return_value = _mock_llm_response(
            _valid_element("P1")  # return valid for both
        )

        result_elements, report = retry_elements(
            elements, qc, schema=SCHEMA, spot_check_threshold=0.5
        )

        # Both should be retried
        assert mock_client.messages.create.call_count == 2
