"""
Tests for qc/compare.py — dual-run comparison behavior.

Focus:
1. Exact matches remain exact
2. Helper-only drift is separated from authoritative disagreement
3. Authoritative mismatches are surfaced explicitly
4. Official source citations can be used as a fallback match key
"""

from qc.compare import compare_extractions


def _base_element(element_id="ASCE7-22-26.10-E26.10-1", el_type="formula"):
    base = {
        "id": element_id,
        "type": el_type,
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.10",
            "citation": "Eq. (26.10-1)",
            "page": 277,
        },
        "title": "Eq. 26.10-1",
        "description": None,
        "cross_references": [],
        "metadata": {"extracted_by": "auto", "qc_status": "pending", "qc_notes": None},
    }
    if el_type == "formula":
        base["data"] = {
            "expression": "qz = 0.00256 * Kz * Kzt * Kd * Ke * V^2",
            "parameters": {
                "Kz": {"unit": "dimensionless"},
                "Kzt": {"unit": "dimensionless"},
            },
            "samples": {"demo": [[1, 2.0]]},
        }
    elif el_type == "provision":
        base["id"] = "ASCE7-22-26.1.1-P1"
        base["source"] = {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.1.1",
            "citation": "Section 26.1.1",
            "page": 261,
        }
        base["title"] = "Scope"
        base["data"] = {
            "rule": "Buildings and other structures shall be designed and constructed to resist wind loads.",
            "conditions": [],
            "then": "Design to resist wind loads",
            "else": None,
            "exceptions": [],
        }
    elif el_type == "figure":
        base["id"] = "ASCE7-22-26.1-F26.1-1"
        base["source"] = {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.1",
            "citation": "Figure 26.1-1",
            "page": 262,
        }
        base["title"] = "Figure 26.1-1"
        base["data"] = {
            "figure_type": "flowchart",
            "description": "Outline of process for determining wind loads.",
            "source_pdf_page": 262,
        }
    return base


def _clone(el):
    import copy
    return copy.deepcopy(el)


class TestCompareExact:
    def test_exact_match_by_id(self):
        a = _base_element()
        b = _clone(a)

        result = compare_extractions([a], [b], "claude", "codex")

        assert result["summary"]["matched_by_id"] == 1
        assert result["summary"]["matched_by_citation"] == 0
        assert result["summary"]["agreed"] == 1
        assert result["summary"]["helper_only"] == 0
        assert result["summary"]["authoritative_disagreed"] == 0
        assert result["summary"]["agreement_rate"] == 1.0
        assert result["summary"]["authoritative_agreement_rate"] == 1.0


class TestCompareHelperOnly:
    def test_helper_only_provision_drift(self):
        a = _base_element(el_type="provision")
        b = _clone(a)
        b["title"] = "General Wind Load Design Requirements"
        b["data"]["then"] = "Design per wind-load provisions"
        b["cross_references"] = ["ASCE7-22-27"]

        result = compare_extractions([a], [b], "claude", "codex")

        assert result["summary"]["agreed"] == 0
        assert result["summary"]["helper_only"] == 1
        assert result["summary"]["authoritative_disagreed"] == 0
        assert result["summary"]["agreement_rate"] == 0.0
        assert result["summary"]["authoritative_agreement_rate"] == 1.0
        severities = {field["severity"] for field in result["helper_only"][0]["fields"]}
        assert severities == {"helper"}

    def test_figure_description_drift_is_not_authoritative(self):
        a = _base_element(el_type="figure")
        b = _clone(a)
        b["data"]["description"] = "Flowchart showing the process for determining wind loads."

        result = compare_extractions([a], [b], "claude", "codex")

        assert result["summary"]["helper_only"] == 1
        assert result["summary"]["authoritative_disagreed"] == 0
        assert result["summary"]["authoritative_agreement_rate"] == 1.0
        assert result["helper_only"][0]["fields"][0]["severity"] == "descriptive"


class TestCompareAuthoritative:
    def test_authoritative_formula_mismatch(self):
        a = _base_element()
        b = _clone(a)
        b["data"]["expression"] = "qz = 0.00256 * Kz * Kzt * Ke * V^2"

        result = compare_extractions([a], [b], "claude", "codex")

        assert result["summary"]["agreed"] == 0
        assert result["summary"]["helper_only"] == 0
        assert result["summary"]["authoritative_disagreed"] == 1
        assert result["summary"]["authoritative_agreement_rate"] == 0.0
        assert result["authoritative_disagreed"][0]["fields"][0]["severity"] == "authoritative"


class TestCompareCitationFallback:
    def test_match_by_official_citation_when_ids_differ(self):
        a = _base_element("ASCE7-22-26.10-E26.10-1", "formula")
        b = _clone(a)
        b["id"] = "ASCE7-22-26.10-E1"

        result = compare_extractions([a], [b], "claude", "codex")

        assert result["summary"]["matched_by_id"] == 0
        assert result["summary"]["matched_by_citation"] == 1
        assert result["summary"]["agreed"] == 1
        assert result["summary"]["only_a"] == 0
        assert result["summary"]["only_b"] == 0
        assert result["agreed"][0]["match_basis"] == "citation"
