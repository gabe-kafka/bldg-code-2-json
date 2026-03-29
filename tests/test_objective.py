"""
Tests for refine/objective.py — score_run with calibration integration.

Mocks gold elements and spot_check. No API calls.

Test cases:
1. score_run uses calibration when gold available
2. score_run falls back to spot-check without gold
3. Composite score stays in [0, 1] with adversarial inputs
"""

import pytest
from unittest.mock import patch, MagicMock

from refine.objective import score_run
from extract.pdf_parser import PageExtraction, ExtractedText


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_element(id_suffix="P1"):
    return {
        "id": f"ASCE7-22-26.5-{id_suffix}",
        "type": "provision",
        "source": {"standard": "ASCE 7-22", "chapter": 26, "section": "26.5", "page": 1},
        "title": "Test",
        "description": None,
        "data": {
            "rule": "Some rule",
            "conditions": [],
            "then": "do X",
            "else": None,
            "exceptions": [],
        },
        "cross_references": [],
        "metadata": {"extracted_by": "auto", "qc_status": "passed", "qc_notes": None},
    }


def _gold_element(id_suffix="P1"):
    el = _valid_element(id_suffix)
    el["metadata"]["qc_status"] = "passed"
    return el


def _make_pages():
    return [PageExtraction(
        page_number=1,
        text_blocks=[ExtractedText(page=1, text="Some text")],
    )]


# ---------------------------------------------------------------------------
# 1. Uses calibration when gold available
# ---------------------------------------------------------------------------

class TestCalibrationUsed:

    @patch("refine.objective.spot_check")
    @patch("refine.objective.check_completeness")
    @patch("refine.objective.load_gold_elements")
    def test_calibration_replaces_spot_check(self, mock_load_gold, mock_completeness, mock_spot):
        gold = [_gold_element("P1")]
        mock_load_gold.return_value = gold
        mock_completeness.return_value = {"overall_coverage": 0.9, "sections": {}, "tables": {}}

        elements = [_valid_element("P1")]
        pages = _make_pages()

        result = score_run(elements, pages)

        # spot_check should NOT have been called
        mock_spot.assert_not_called()
        # accuracy should come from calibration (perfect match = 1.0)
        assert result["components"]["accuracy"] == 1.0

    @patch("refine.objective.spot_check")
    @patch("refine.objective.check_completeness")
    @patch("refine.objective.load_gold_elements")
    def test_calibration_details_in_result(self, mock_load_gold, mock_completeness, mock_spot):
        mock_load_gold.return_value = [_gold_element("P1")]
        mock_completeness.return_value = {"overall_coverage": 1.0, "sections": {}, "tables": {}}

        result = score_run([_valid_element("P1")], _make_pages())
        assert "calibration" in result["details"]


# ---------------------------------------------------------------------------
# 2. Falls back to spot-check without gold
# ---------------------------------------------------------------------------

class TestSpotCheckFallback:

    @patch("refine.objective.spot_check")
    @patch("refine.objective.check_completeness")
    @patch("refine.objective.load_gold_elements")
    def test_spot_check_used_when_no_gold(self, mock_load_gold, mock_completeness, mock_spot):
        mock_load_gold.return_value = []
        mock_completeness.return_value = {"overall_coverage": 0.8, "sections": {}, "tables": {}}
        mock_spot.return_value = {
            "sample_size": 1,
            "average_score": 0.75,
            "results": [{"id": "P1", "score": 0.75, "accurate": True, "issues": []}],
        }

        elements = [_valid_element("P1")]
        pages = _make_pages()

        result = score_run(elements, pages)

        mock_spot.assert_called_once()
        assert result["components"]["accuracy"] == 0.75


# ---------------------------------------------------------------------------
# 3. Composite score stays in [0, 1]
# ---------------------------------------------------------------------------

class TestCompositeScoreBounds:

    @patch("refine.objective.spot_check")
    @patch("refine.objective.check_completeness")
    @patch("refine.objective.load_gold_elements")
    def test_perfect_scores(self, mock_load_gold, mock_completeness, mock_spot):
        mock_load_gold.return_value = [_gold_element("P1")]
        mock_completeness.return_value = {"overall_coverage": 1.0, "sections": {}, "tables": {}}

        result = score_run([_valid_element("P1")], _make_pages())
        assert 0.0 <= result["composite_score"] <= 1.0

    @patch("refine.objective.spot_check")
    @patch("refine.objective.check_completeness")
    @patch("refine.objective.load_gold_elements")
    def test_zero_scores(self, mock_load_gold, mock_completeness, mock_spot):
        mock_load_gold.return_value = [_gold_element("P1")]
        mock_completeness.return_value = {"overall_coverage": 0.0, "sections": {}, "tables": {}}

        # Element with different ID than gold — 0 calibration accuracy
        result = score_run([_valid_element("X1")], _make_pages())
        assert 0.0 <= result["composite_score"] <= 1.0

    @patch("refine.objective.spot_check")
    @patch("refine.objective.check_completeness")
    @patch("refine.objective.load_gold_elements")
    def test_empty_elements(self, mock_load_gold, mock_completeness, mock_spot):
        mock_load_gold.return_value = []
        mock_completeness.return_value = {"overall_coverage": 0.0, "sections": {}, "tables": {}}
        mock_spot.return_value = {"sample_size": 0, "average_score": 0.0, "results": []}

        result = score_run([], _make_pages())
        assert 0.0 <= result["composite_score"] <= 1.0
