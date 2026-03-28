"""
Tests for extract/post_processor.py — comprehensive TDD tests.

Covers all 6 transforms:
1. Operator normalization (Unicode, English words, pass-through, empty conditions)
2. Null-to-empty-string coercion (required string fields only)
3. Range null removal
4. ID normalization (strip spaces, uppercase prefix)
5. Definition reclassification
6. Figure shape repair

Also covers: idempotency, field preservation, empty input, nested cases.
"""

import copy
import pytest

from extract.post_processor import post_process


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_element(**overrides):
    """Build a minimal valid-ish element dict, merging overrides."""
    base = {
        "id": "ASCE7-22-26.5-P1",
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
        "cross_references": ["ASCE7-22-26.2-1"],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **val}
        else:
            base[key] = val
    return base


# ===========================================================================
# 1. Operator Normalization
# ===========================================================================


class TestOperatorNormalizationUnicode:
    """VAL-PP-002: Unicode operator symbols normalized."""

    def test_less_than_or_equal_unicode(self):
        el = _make_element(data={
            "rule": "x",
            "conditions": [{"parameter": "h", "operator": "≤", "value": 60, "unit": "ft"}],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["conditions"][0]["operator"] == "<="

    def test_greater_than_or_equal_unicode(self):
        el = _make_element(data={
            "rule": "x",
            "conditions": [{"parameter": "h", "operator": "≥", "value": 60, "unit": "ft"}],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["conditions"][0]["operator"] == ">="

    def test_not_equal_unicode(self):
        el = _make_element(data={
            "rule": "x",
            "conditions": [{"parameter": "h", "operator": "≠", "value": 0, "unit": None}],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["conditions"][0]["operator"] == "!="


class TestOperatorNormalizationEnglish:
    """VAL-PP-003: English word operators normalized."""

    @pytest.mark.parametrize("word,expected", [
        ("equals", "=="),
        ("greater than", ">"),
        ("less than", "<"),
        ("at least", ">="),
        ("at most", "<="),
        ("not equal", "!="),
    ])
    def test_english_word_operators(self, word, expected):
        el = _make_element(data={
            "rule": "x",
            "conditions": [{"parameter": "h", "operator": word, "value": 10, "unit": None}],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["conditions"][0]["operator"] == expected


class TestOperatorNormalizationPassthrough:
    """VAL-PP-004: Already-valid operators pass through unchanged."""

    @pytest.mark.parametrize("op", ["==", "!=", ">", ">=", "<", "<=", "in", "not_in"])
    def test_valid_operators_unchanged(self, op):
        el = _make_element(data={
            "rule": "x",
            "conditions": [{"parameter": "h", "operator": op, "value": 10, "unit": None}],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["conditions"][0]["operator"] == op


class TestOperatorNormalizationEmptyConditions:
    """VAL-PP-005: Empty conditions array handled without error."""

    def test_empty_conditions(self):
        el = _make_element(data={
            "rule": "x",
            "conditions": [],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["conditions"] == []


class TestOperatorNormalizationDefinitionConditions:
    """Operator normalization also applies to definition conditions."""

    def test_definition_conditions_normalized(self):
        el = _make_element(
            type="definition",
            data={
                "term": "BASIC WIND SPEED",
                "definition": "Three-second gust speed.",
                "conditions": [
                    {"parameter": "z", "operator": "≤", "value": 33, "unit": "ft"}
                ],
                "exceptions": [],
            },
        )
        result = post_process([el])
        assert result[0]["data"]["conditions"][0]["operator"] == "<="


class TestOperatorNormalizationMultipleConditions:
    """Multiple conditions in one element all get normalized."""

    def test_multiple_conditions(self):
        el = _make_element(data={
            "rule": "x",
            "conditions": [
                {"parameter": "h", "operator": "≤", "value": 60, "unit": "ft"},
                {"parameter": "w", "operator": "greater than", "value": 100, "unit": "ft"},
                {"parameter": "cat", "operator": "==", "value": "B", "unit": None},
            ],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        ops = [c["operator"] for c in result[0]["data"]["conditions"]]
        assert ops == ["<=", ">", "=="]


# ===========================================================================
# 2. Null-to-Empty-String Coercion
# ===========================================================================


class TestNullCoercion:
    """VAL-PP-006, VAL-PP-007: Null coerced to '' for required string fields."""

    def test_provision_rule_null(self):
        """data.rule null → ''."""
        el = _make_element(data={
            "rule": None,
            "conditions": [],
            "then": "a", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["rule"] == ""

    def test_provision_then_null(self):
        """data.then null → ''."""
        el = _make_element(data={
            "rule": "x",
            "conditions": [],
            "then": None, "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["then"] == ""

    def test_nullable_description_unchanged(self):
        """description allows null, so it must NOT be coerced."""
        el = _make_element(description=None)
        result = post_process([el])
        assert result[0]["description"] is None

    def test_nullable_qc_notes_unchanged(self):
        """metadata.qc_notes allows null, must NOT be coerced."""
        el = _make_element()
        el["metadata"]["qc_notes"] = None
        result = post_process([el])
        assert result[0]["metadata"]["qc_notes"] is None

    def test_nullable_page_unchanged(self):
        """source.page allows null, must NOT be coerced."""
        el = _make_element()
        el["source"]["page"] = None
        result = post_process([el])
        assert result[0]["source"]["page"] is None

    def test_non_null_string_unchanged(self):
        """Already-present strings are not altered."""
        el = _make_element(data={
            "rule": "Real rule text",
            "conditions": [],
            "then": "do something", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["data"]["rule"] == "Real rule text"
        assert result[0]["data"]["then"] == "do something"

    def test_formula_parameter_name_null(self):
        """Formula parameter name (key in parameters dict) can't really be null,
        but parameter unit field is a string, so test null unit is handled."""
        el = _make_element(
            type="formula",
            data={
                "expression": "Kz = 2.01 * (z/zg)^(2/alpha)",
                "parameters": {
                    "z": {"unit": None, "range": [0, 1500]},
                },
            },
        )
        # unit in formula_data parameters is typed as "string" (not ["string","null"])
        # so null should be coerced
        result = post_process([el])
        assert result[0]["data"]["parameters"]["z"]["unit"] == ""

    def test_definition_term_null(self):
        """definition_data.term is required string → coerce null."""
        el = _make_element(
            type="definition",
            data={
                "term": None,
                "definition": "Some def",
                "conditions": [],
                "exceptions": [],
            },
        )
        result = post_process([el])
        assert result[0]["data"]["term"] == ""

    def test_definition_definition_null(self):
        """definition_data.definition is required string → coerce null."""
        el = _make_element(
            type="definition",
            data={
                "term": "TERM",
                "definition": None,
                "conditions": [],
                "exceptions": [],
            },
        )
        result = post_process([el])
        assert result[0]["data"]["definition"] == ""

    def test_title_null(self):
        """title is required string → coerce null to ''."""
        el = _make_element(title=None)
        result = post_process([el])
        assert result[0]["title"] == ""


# ===========================================================================
# 3. Range Null Removal
# ===========================================================================


class TestRangeNullRemoval:
    """VAL-PP-008, VAL-PP-009: Remove null/partial-null ranges, preserve valid."""

    def test_range_null_removed(self):
        el = _make_element(
            type="formula",
            data={
                "expression": "Kz = ...",
                "parameters": {
                    "z": {"unit": "ft", "range": None},
                },
            },
        )
        result = post_process([el])
        assert "range" not in result[0]["data"]["parameters"]["z"]

    def test_range_partial_null_removed(self):
        """range: [null, 100] → key removed."""
        el = _make_element(
            type="formula",
            data={
                "expression": "Kz = ...",
                "parameters": {
                    "z": {"unit": "ft", "range": [None, 100]},
                },
            },
        )
        result = post_process([el])
        assert "range" not in result[0]["data"]["parameters"]["z"]

    def test_range_first_element_null_removed(self):
        """range: [null, null] → key removed."""
        el = _make_element(
            type="formula",
            data={
                "expression": "x = ...",
                "parameters": {
                    "z": {"unit": "ft", "range": [None, None]},
                },
            },
        )
        result = post_process([el])
        assert "range" not in result[0]["data"]["parameters"]["z"]

    def test_valid_range_preserved(self):
        """range: [0, 1500] → kept as-is."""
        el = _make_element(
            type="formula",
            data={
                "expression": "Kz = ...",
                "parameters": {
                    "z": {"unit": "ft", "range": [0, 1500]},
                },
            },
        )
        result = post_process([el])
        assert result[0]["data"]["parameters"]["z"]["range"] == [0, 1500]

    def test_no_range_key_no_error(self):
        """Parameter without range key doesn't cause error."""
        el = _make_element(
            type="formula",
            data={
                "expression": "Kz = ...",
                "parameters": {
                    "z": {"unit": "ft", "source": "table_26.11-1"},
                },
            },
        )
        result = post_process([el])
        assert "range" not in result[0]["data"]["parameters"]["z"]
        assert result[0]["data"]["parameters"]["z"]["source"] == "table_26.11-1"

    def test_second_element_null_removed(self):
        """range: [0, null] → key removed."""
        el = _make_element(
            type="formula",
            data={
                "expression": "x",
                "parameters": {
                    "z": {"unit": "ft", "range": [0, None]},
                },
            },
        )
        result = post_process([el])
        assert "range" not in result[0]["data"]["parameters"]["z"]


# ===========================================================================
# 4. ID Normalization
# ===========================================================================


class TestIDNormalization:
    """VAL-PP-010, VAL-PP-011, VAL-PP-012: ID space strip + uppercase prefix."""

    def test_strip_spaces(self):
        """VAL-PP-010: Spaces removed from ID."""
        el = _make_element(id="ASCE7-22 - 26.5 - T1")
        result = post_process([el])
        assert result[0]["id"] == "ASCE7-22-26.5-T1"

    def test_lowercase_prefix_uppercased(self):
        """VAL-PP-011: First segment uppercased."""
        el = _make_element(id="asce7-22-26.5-T1")
        result = post_process([el])
        assert result[0]["id"].startswith("ASCE7")

    def test_already_valid_id_unchanged(self):
        """VAL-PP-012: Valid IDs pass through."""
        el = _make_element(id="ASCE7-22-26.5-T1")
        result = post_process([el])
        assert result[0]["id"] == "ASCE7-22-26.5-T1"

    def test_mixed_spaces_and_lowercase(self):
        """Both fixes applied together."""
        el = _make_element(id="asce7-22 - 26.5 - P1")
        result = post_process([el])
        assert " " not in result[0]["id"]
        assert result[0]["id"].startswith("ASCE7")

    def test_id_with_internal_spaces(self):
        """Internal spaces in segments stripped."""
        el = _make_element(id="ASCE7-22- 26.5 -T1")
        result = post_process([el])
        assert result[0]["id"] == "ASCE7-22-26.5-T1"


# ===========================================================================
# 5. Definition Reclassification
# ===========================================================================


class TestDefinitionReclassification:
    """VAL-PP-013, VAL-PP-014, VAL-PP-015: Reclassify provisions that are definitions."""

    def test_all_caps_term_colon(self):
        """VAL-PP-013: ALL-CAPS term followed by colon → definition."""
        el = _make_element(data={
            "rule": "BASIC WIND SPEED: Three-second gust speed at 33 ft.",
            "conditions": [],
            "then": "", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["type"] == "definition"
        assert result[0]["data"]["term"] == "BASIC WIND SPEED"
        assert "Three-second gust speed" in result[0]["data"]["definition"]

    def test_is_defined_as_pattern(self):
        """'is defined as' keyword triggers reclassification."""
        el = _make_element(data={
            "rule": "Eave height is defined as the distance from ground to roof eave.",
            "conditions": [],
            "then": "", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["type"] == "definition"

    def test_means_pattern(self):
        """'means' keyword triggers reclassification."""
        el = _make_element(data={
            "rule": "Windborne debris region means areas within hurricane-prone zones.",
            "conditions": [],
            "then": "", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["type"] == "definition"

    def test_definition_data_structure(self):
        """Reclassified element gets definition_data format."""
        el = _make_element(data={
            "rule": "EAVE HEIGHT: The distance from ground to roof eave line.",
            "conditions": [{"parameter": "x", "operator": "==", "value": 1, "unit": None}],
            "then": "", "else": None, "exceptions": ["Some exception"],
        })
        result = post_process([el])
        assert result[0]["type"] == "definition"
        data = result[0]["data"]
        assert "term" in data
        assert "definition" in data
        assert "conditions" in data
        assert "exceptions" in data
        assert data["term"] == "EAVE HEIGHT"

    def test_real_provision_unchanged(self):
        """VAL-PP-014: Genuine provision stays as provision."""
        el = _make_element(data={
            "rule": "Buildings with mean roof height h > 60 ft shall use exposure defined in Section 26.7.3",
            "conditions": [{"parameter": "mean_roof_height", "operator": ">", "value": 60, "unit": "ft"}],
            "then": "use Section 26.7.3", "else": None, "exceptions": [],
        })
        result = post_process([el])
        assert result[0]["type"] == "provision"

    def test_non_provision_types_unchanged(self):
        """VAL-PP-015: Non-provision types not affected."""
        for t in ["table", "formula", "figure", "reference", "skipped_figure"]:
            el = _make_element(type=t, data=_type_data(t))
            result = post_process([el])
            assert result[0]["type"] == t

    def test_already_definition_type_unchanged(self):
        """Already type='definition' is not re-processed."""
        el = _make_element(
            type="definition",
            data={
                "term": "BASIC WIND SPEED",
                "definition": "Three-second gust speed.",
                "conditions": [],
                "exceptions": [],
            },
        )
        result = post_process([el])
        assert result[0]["type"] == "definition"
        assert result[0]["data"]["term"] == "BASIC WIND SPEED"


# ===========================================================================
# 6. Figure Shape Repair
# ===========================================================================


class TestFigureShapeRepair:
    """VAL-PP-016, VAL-PP-017: Mistyped figures → skipped_figure."""

    def test_figure_with_skipped_shape(self):
        """VAL-PP-016: figure + skipped data → skipped_figure."""
        el = _make_element(
            type="figure",
            data={
                "figure_type": "diagram",
                "skip_reason": "Illustrative diagram",
                "description": "Some diagram",
            },
        )
        result = post_process([el])
        assert result[0]["type"] == "skipped_figure"

    def test_valid_figure_unchanged(self):
        """VAL-PP-017: Valid figure data keeps type='figure'."""
        el = _make_element(
            type="figure",
            data={
                "figure_class": {
                    "figure_type": "xy_chart",
                    "description": "Wind speed chart",
                },
                "data": {
                    "x_axis": {"name": "height", "unit": "ft", "scale": "linear"},
                    "y_axis": {"name": "Kz", "unit": "dimensionless", "scale": "linear"},
                    "curves": [
                        {
                            "label": "Exposure B",
                            "points": [[0, 0.57], [15, 0.57], [30, 0.70], [60, 0.81], [100, 0.90]],
                            "interpolation": "linear",
                        }
                    ],
                },
            },
        )
        result = post_process([el])
        assert result[0]["type"] == "figure"

    def test_skipped_figure_type_unchanged(self):
        """Already skipped_figure stays skipped_figure."""
        el = _make_element(
            type="skipped_figure",
            data={
                "figure_type": "diagram",
                "skip_reason": "Illustrative",
                "description": "Some diagram",
            },
        )
        result = post_process([el])
        assert result[0]["type"] == "skipped_figure"


# ===========================================================================
# General / Cross-cutting
# ===========================================================================


class TestEmptyInput:
    """VAL-PP-018: Empty list handled."""

    def test_empty_list(self):
        assert post_process([]) == []


class TestIdempotency:
    """VAL-PP-019: Double application equals single application."""

    def test_idempotent_provision(self):
        el = _make_element(
            id="asce7-22 - 26.5 - P1",
            data={
                "rule": None,
                "conditions": [{"parameter": "h", "operator": "≤", "value": 60, "unit": "ft"}],
                "then": None, "else": None, "exceptions": [],
            },
        )
        once = post_process([el])
        twice = post_process(once)
        assert once == twice

    def test_idempotent_definition_reclassification(self):
        el = _make_element(data={
            "rule": "BASIC WIND SPEED: Three-second gust speed at 33 ft.",
            "conditions": [],
            "then": "", "else": None, "exceptions": [],
        })
        once = post_process([el])
        twice = post_process(once)
        assert once == twice

    def test_idempotent_figure_repair(self):
        el = _make_element(
            type="figure",
            data={
                "figure_type": "diagram",
                "skip_reason": "Illustrative",
                "description": "x",
            },
        )
        once = post_process([el])
        twice = post_process(once)
        assert once == twice

    def test_idempotent_formula_range(self):
        el = _make_element(
            type="formula",
            data={
                "expression": "Kz = ...",
                "parameters": {
                    "z": {"unit": "ft", "range": [None, 100]},
                },
            },
        )
        once = post_process([el])
        twice = post_process(once)
        assert once == twice


class TestFieldPreservation:
    """VAL-PP-020: Fields not targeted by transforms are preserved."""

    def test_cross_references_preserved(self):
        el = _make_element(cross_references=["ASCE7-22-26.2-1", "ASCE7-22-26.7-3"])
        result = post_process([el])
        assert result[0]["cross_references"] == ["ASCE7-22-26.2-1", "ASCE7-22-26.7-3"]

    def test_metadata_preserved(self):
        el = _make_element()
        el["metadata"] = {"extracted_by": "manual", "qc_status": "passed", "qc_notes": "Reviewed"}
        result = post_process([el])
        assert result[0]["metadata"]["extracted_by"] == "manual"
        assert result[0]["metadata"]["qc_status"] == "passed"
        assert result[0]["metadata"]["qc_notes"] == "Reviewed"

    def test_source_preserved(self):
        el = _make_element()
        el["source"] = {"standard": "IBC-2021", "chapter": 16, "section": "1609", "page": 42}
        result = post_process([el])
        assert result[0]["source"]["standard"] == "IBC-2021"
        assert result[0]["source"]["page"] == 42

    def test_extra_unknown_fields_preserved(self):
        el = _make_element()
        el["custom_field"] = "should survive"
        result = post_process([el])
        assert result[0]["custom_field"] == "should survive"


class TestPureFunctionNoSideEffects:
    """VAL-PP-021: Post-processor does not modify input."""

    def test_input_not_mutated(self):
        el = _make_element(
            id="asce7-22 - 26.5 - P1",
            data={
                "rule": None,
                "conditions": [{"parameter": "h", "operator": "≤", "value": 60, "unit": "ft"}],
                "then": None, "else": None, "exceptions": [],
            },
        )
        original = copy.deepcopy(el)
        post_process([el])
        assert el == original

    def test_returns_new_list(self):
        elements = [_make_element()]
        result = post_process(elements)
        assert result is not elements


class TestMultipleElements:
    """Multiple elements processed independently."""

    def test_two_elements_both_transformed(self):
        el1 = _make_element(
            id="asce7-22 - 26.5 - P1",
            data={
                "rule": "x",
                "conditions": [{"parameter": "h", "operator": "≤", "value": 60, "unit": "ft"}],
                "then": "a", "else": None, "exceptions": [],
            },
        )
        el2 = _make_element(
            id="ASCE7-22-26.6-T1",
            type="table",
            data={
                "columns": [{"name": "col", "unit": None}],
                "rows": [{"col": "val"}],
            },
        )
        result = post_process([el1, el2])
        assert len(result) == 2
        assert result[0]["data"]["conditions"][0]["operator"] == "<="
        assert " " not in result[0]["id"]
        assert result[1]["type"] == "table"


# ===========================================================================
# Helpers for non-provision types
# ===========================================================================


def _type_data(t):
    """Return minimal data dict for a given type."""
    if t == "table":
        return {"columns": [{"name": "x", "unit": None}], "rows": []}
    elif t == "formula":
        return {"expression": "x = 1", "parameters": {}}
    elif t == "figure":
        return {
            "figure_class": {"figure_type": "xy_chart", "description": "test"},
            "data": {
                "x_axis": {"name": "x", "unit": "ft", "scale": "linear"},
                "y_axis": {"name": "y", "unit": "ft", "scale": "linear"},
                "curves": [{"label": "c", "points": [[0,0],[1,1],[2,2],[3,3],[4,4]], "interpolation": "linear"}],
            },
        }
    elif t == "reference":
        return {"target": "USGS", "url": None, "parameters": []}
    elif t == "skipped_figure":
        return {"figure_type": "diagram", "skip_reason": "Illustrative", "description": "x"}
    elif t == "definition":
        return {"term": "T", "definition": "D", "conditions": [], "exceptions": []}
    return {}
