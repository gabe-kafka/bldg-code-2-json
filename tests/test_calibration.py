"""
Tests for qc/calibration.py — gold standard calibration scoring.

Uses synthetic gold elements. No API calls.

Test cases:
1. Per-element field-level comparison works
2. Type match correctly identifies matches/mismatches
3. Numeric values compared with tolerance
4. Cross-references compared as sets (order-insensitive)
5. Calibration report has correct structure
6. Missing elements reported
7. Calibration is deterministic
"""

import pytest

from qc.calibration import score_against_gold, calibration_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gold_element(id_suffix="P1", el_type="provision", rule="Gold rule", xrefs=None):
    base = {
        "id": f"ASCE7-22-26.5-{id_suffix}",
        "type": el_type,
        "source": {"standard": "ASCE 7-22", "chapter": 26, "section": "26.5", "page": None},
        "title": "Gold Element",
        "description": None,
        "cross_references": xrefs or [],
        "metadata": {"extracted_by": "auto", "qc_status": "passed", "qc_notes": None},
    }
    if el_type == "provision":
        base["data"] = {
            "rule": rule,
            "conditions": [],
            "then": "do X",
            "else": None,
            "exceptions": [],
        }
    elif el_type == "formula":
        base["data"] = {
            "expression": "V = Kz * Kd",
            "parameters": {"Kz": {"unit": "m/s", "range": [0.85, 1.5]}},
        }
    elif el_type == "table":
        base["data"] = {
            "columns": [{"name": "Speed", "unit": "mph"}],
            "rows": [{"Speed": "100"}],
        }
    return base


def _extracted(gold, **overrides):
    """Clone a gold element with overrides to simulate extraction."""
    import copy
    el = copy.deepcopy(gold)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(el.get(k), dict):
            el[k] = {**el[k], **v}
        else:
            el[k] = v
    return el


# ---------------------------------------------------------------------------
# 1. Per-element field-level comparison
# ---------------------------------------------------------------------------

class TestFieldLevelComparison:

    def test_perfect_match_scores_1(self):
        gold = _gold_element("P1")
        ext = _extracted(gold)
        results = score_against_gold([ext], [gold])
        assert len(results) == 1
        assert results[0]["score"] == 1.0
        assert results[0]["data_match"] == 1.0

    def test_partial_data_match(self):
        gold = _gold_element("P1", rule="Exact rule text")
        ext = _extracted(gold, data={
            "rule": "Different rule",
            "conditions": [],
            "then": "do X",
            "else": None,
            "exceptions": [],
        })
        results = score_against_gold([ext], [gold])
        assert results[0]["data_match"] < 1.0
        assert results[0]["score"] < 1.0


# ---------------------------------------------------------------------------
# 2. Type match
# ---------------------------------------------------------------------------

class TestTypeMatch:

    def test_matching_type(self):
        gold = _gold_element("P1", "provision")
        ext = _extracted(gold)
        results = score_against_gold([ext], [gold])
        assert results[0]["type_match"] is True

    def test_mismatched_type(self):
        gold = _gold_element("P1", "provision")
        ext = _extracted(gold, type="definition")
        results = score_against_gold([ext], [gold])
        assert results[0]["type_match"] is False
        assert results[0]["score"] < 1.0


# ---------------------------------------------------------------------------
# 3. Numeric tolerance
# ---------------------------------------------------------------------------

class TestNumericTolerance:

    def test_within_tolerance_passes(self):
        gold = _gold_element("E1", "formula")
        ext = _extracted(gold)
        # Slightly alter a numeric value within tolerance
        ext["data"]["parameters"]["Kz"]["range"] = [0.8504, 1.5]
        results = score_against_gold([ext], [gold], rtol=1e-3)
        assert results[0]["data_match"] == 1.0

    def test_outside_tolerance_fails(self):
        gold = _gold_element("E1", "formula")
        ext = _extracted(gold)
        ext["data"]["parameters"]["Kz"]["range"] = [0.90, 1.5]
        results = score_against_gold([ext], [gold], rtol=1e-3)
        assert results[0]["data_match"] < 1.0


# ---------------------------------------------------------------------------
# 4. Cross-references as sets (order-insensitive)
# ---------------------------------------------------------------------------

class TestXrefComparison:

    def test_same_xrefs_different_order(self):
        gold = _gold_element("P1", xrefs=["REF-A", "REF-B", "REF-C"])
        ext = _extracted(gold, cross_references=["REF-C", "REF-A", "REF-B"])
        results = score_against_gold([ext], [gold])
        assert results[0]["xref_match"] is True

    def test_different_xrefs(self):
        gold = _gold_element("P1", xrefs=["REF-A", "REF-B"])
        ext = _extracted(gold, cross_references=["REF-A", "REF-X"])
        results = score_against_gold([ext], [gold])
        assert results[0]["xref_match"] is False


# ---------------------------------------------------------------------------
# 5. Calibration report structure
# ---------------------------------------------------------------------------

class TestReportStructure:

    def test_has_required_keys(self):
        gold = [_gold_element("P1")]
        ext = [_extracted(gold[0])]
        report = calibration_report(ext, gold)

        assert "per_element" in report
        assert "aggregate" in report
        assert "timestamp" in report

        agg = report["aggregate"]
        assert "accuracy" in agg
        assert "type_match_rate" in agg
        assert "elements_compared" in agg
        assert "elements_missing" in agg

    def test_aggregate_values_correct(self):
        gold = [_gold_element("P1"), _gold_element("P2")]
        ext = [_extracted(gold[0]), _extracted(gold[1])]
        report = calibration_report(ext, gold)

        assert report["aggregate"]["accuracy"] == 1.0
        assert report["aggregate"]["type_match_rate"] == 1.0
        assert report["aggregate"]["elements_compared"] == 2
        assert report["aggregate"]["elements_missing"] == 0


# ---------------------------------------------------------------------------
# 6. Missing elements reported
# ---------------------------------------------------------------------------

class TestMissingElements:

    def test_missing_extracted_element(self):
        gold = [_gold_element("P1"), _gold_element("P2")]
        ext = [_extracted(gold[0])]  # P2 missing
        results = score_against_gold(ext, gold)

        p2 = [r for r in results if r["element_id"] == "ASCE7-22-26.5-P2"][0]
        assert p2["id_match"] is False
        assert p2["score"] == 0.0
        assert "No matching" in p2["details"]

    def test_report_counts_missing(self):
        gold = [_gold_element("P1"), _gold_element("P2")]
        ext = [_extracted(gold[0])]
        report = calibration_report(ext, gold)
        assert report["aggregate"]["elements_missing"] == 1
        assert report["aggregate"]["elements_compared"] == 1


# ---------------------------------------------------------------------------
# 7. Deterministic
# ---------------------------------------------------------------------------

class TestDeterministic:

    def test_same_inputs_same_output(self):
        gold = [_gold_element("P1"), _gold_element("E1", "formula")]
        ext = [_extracted(gold[0]), _extracted(gold[1])]

        r1 = calibration_report(ext, gold)
        r2 = calibration_report(ext, gold)

        # Same except timestamp
        assert r1["per_element"] == r2["per_element"]
        assert r1["aggregate"] == r2["aggregate"]
