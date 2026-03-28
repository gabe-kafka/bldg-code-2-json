"""
Tests for schema validation — covers the element JSON Schema and the
qc/schema_validator.py module.

Tests cover:
- Valid provision elements still pass after schema update
- Valid definition elements pass
- Invalid elements fail with appropriate errors
- Schema meta-validation (Draft 2020-12)
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from qc.schema_validator import load_schema, validate_element, validate_chapter


# ---------------------------------------------------------------------------
# Schema meta-validation
# ---------------------------------------------------------------------------


class TestSchemaMeta:
    """Ensure the schema itself is valid Draft 2020-12."""

    def test_schema_is_valid_draft_2020_12(self, schema):
        """VAL-SCHEMA-006: Schema passes Draft202012Validator.check_schema."""
        # Should not raise
        Draft202012Validator.check_schema(schema)

    def test_schema_has_definition_in_type_enum(self, schema):
        """VAL-SCHEMA-001: type enum includes 'definition'."""
        type_enum = schema["properties"]["type"]["enum"]
        assert "definition" in type_enum

    def test_schema_has_definition_data_def(self, schema):
        """VAL-SCHEMA-002: $defs/definition_data exists with required fields."""
        assert "definition_data" in schema["$defs"]
        dd = schema["$defs"]["definition_data"]
        assert dd["type"] == "object"
        assert "term" in dd["required"]
        assert "definition" in dd["required"]
        assert dd["properties"]["term"]["type"] == "string"
        assert dd["properties"]["definition"]["type"] == "string"

    def test_schema_definition_data_has_conditions(self, schema):
        """definition_data has optional conditions array with provision-style items."""
        dd = schema["$defs"]["definition_data"]
        conditions = dd["properties"]["conditions"]
        assert conditions["type"] == "array"
        item_props = conditions["items"]["properties"]
        assert "parameter" in item_props
        assert "operator" in item_props
        assert "value" in item_props

    def test_schema_definition_data_has_exceptions(self, schema):
        """definition_data has optional exceptions array of strings."""
        dd = schema["$defs"]["definition_data"]
        exceptions = dd["properties"]["exceptions"]
        assert exceptions["type"] == "array"
        assert exceptions["items"]["type"] == "string"

    def test_schema_data_oneof_includes_definition_ref(self, schema):
        """VAL-SCHEMA-003: data oneOf includes $ref to definition_data."""
        refs = [entry.get("$ref", "") for entry in schema["properties"]["data"]["oneOf"]]
        assert "#/$defs/definition_data" in refs


# ---------------------------------------------------------------------------
# Valid elements pass validation
# ---------------------------------------------------------------------------


class TestValidElements:
    """Ensure known-good elements validate successfully."""

    def test_valid_provision_passes(self, valid_provision):
        """VAL-SCHEMA-004: A previously valid provision element still passes."""
        result = validate_element(valid_provision)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_valid_definition_passes(self, valid_definition):
        """VAL-SCHEMA-005: A well-formed definition element passes."""
        result = validate_element(valid_definition)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_valid_table_passes(self, valid_table):
        """A valid table element still passes after schema update."""
        result = validate_element(valid_table)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_definition_with_conditions_passes(self):
        """A definition element with non-empty conditions validates."""
        element = {
            "id": "ASCE7-22-26.2-2",
            "type": "definition",
            "source": {
                "standard": "ASCE 7-22",
                "chapter": 26,
                "section": "26.2",
                "page": None,
            },
            "title": "Enclosed Building",
            "description": None,
            "data": {
                "term": "ENCLOSED BUILDING",
                "definition": "A building that does not comply with requirements for open or partially enclosed buildings.",
                "conditions": [
                    {
                        "parameter": "Ao",
                        "operator": "<=",
                        "value": 0.01,
                        "unit": None,
                    }
                ],
                "exceptions": ["Exception applies to buildings in hurricane-prone regions."],
            },
            "cross_references": [],
            "metadata": {
                "extracted_by": "auto",
                "qc_status": "pending",
                "qc_notes": None,
            },
        }
        result = validate_element(element)
        assert result["valid"] is True

    def test_definition_minimal_passes(self):
        """A definition with only required fields (term, definition) in data validates."""
        element = {
            "id": "ASCE7-22-26.2-3",
            "type": "definition",
            "source": {
                "standard": "ASCE 7-22",
                "chapter": 26,
                "section": "26.2",
                "page": None,
            },
            "title": "Eave Height",
            "description": None,
            "data": {
                "term": "EAVE HEIGHT",
                "definition": "The distance from the ground surface adjacent to the building to the roof eave line.",
            },
            "cross_references": [],
            "metadata": {
                "extracted_by": "auto",
                "qc_status": "pending",
                "qc_notes": None,
            },
        }
        result = validate_element(element)
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# Invalid elements fail validation
# ---------------------------------------------------------------------------


class TestInvalidElements:
    """Ensure invalid elements are properly rejected."""

    def test_missing_required_field_fails(self):
        """An element missing a required top-level field fails."""
        element = {
            "id": "ASCE7-22-26.5-1",
            "type": "provision",
            # missing source, title, data, metadata
        }
        result = validate_element(element)
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_invalid_type_fails(self):
        """An element with an unknown type fails."""
        element = {
            "id": "ASCE7-22-26.5-1",
            "type": "unknown_type",
            "source": {
                "standard": "ASCE 7-22",
                "chapter": 26,
                "section": "26.5",
                "page": None,
            },
            "title": "Test",
            "description": None,
            "data": {"rule": "test", "conditions": []},
            "cross_references": [],
            "metadata": {
                "extracted_by": "auto",
                "qc_status": "pending",
                "qc_notes": None,
            },
        }
        result = validate_element(element)
        assert result["valid"] is False

    def test_definition_missing_term_fails(self):
        """A definition element with missing 'term' in data fails."""
        element = {
            "id": "ASCE7-22-26.2-1",
            "type": "definition",
            "source": {
                "standard": "ASCE 7-22",
                "chapter": 26,
                "section": "26.2",
                "page": None,
            },
            "title": "Bad Definition",
            "description": None,
            "data": {
                # missing 'term'
                "definition": "Some definition text.",
            },
            "cross_references": [],
            "metadata": {
                "extracted_by": "auto",
                "qc_status": "pending",
                "qc_notes": None,
            },
        }
        result = validate_element(element)
        assert result["valid"] is False

    def test_definition_missing_definition_field_fails(self):
        """A definition element with missing 'definition' in data fails."""
        element = {
            "id": "ASCE7-22-26.2-1",
            "type": "definition",
            "source": {
                "standard": "ASCE 7-22",
                "chapter": 26,
                "section": "26.2",
                "page": None,
            },
            "title": "Bad Definition",
            "description": None,
            "data": {
                "term": "SOME TERM",
                # missing 'definition'
            },
            "cross_references": [],
            "metadata": {
                "extracted_by": "auto",
                "qc_status": "pending",
                "qc_notes": None,
            },
        }
        result = validate_element(element)
        assert result["valid"] is False

    def test_definition_with_bad_condition_operator_fails(self):
        """A definition with an invalid operator in conditions fails."""
        element = {
            "id": "ASCE7-22-26.2-1",
            "type": "definition",
            "source": {
                "standard": "ASCE 7-22",
                "chapter": 26,
                "section": "26.2",
                "page": None,
            },
            "title": "Bad Definition",
            "description": None,
            "data": {
                "term": "SOME TERM",
                "definition": "Some text",
                "conditions": [
                    {
                        "parameter": "x",
                        "operator": "bad_op",
                        "value": 1,
                        "unit": None,
                    }
                ],
            },
            "cross_references": [],
            "metadata": {
                "extracted_by": "auto",
                "qc_status": "pending",
                "qc_notes": None,
            },
        }
        result = validate_element(element)
        assert result["valid"] is False

    def test_empty_element_fails(self):
        """An empty dict is not a valid element."""
        result = validate_element({})
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# validate_chapter
# ---------------------------------------------------------------------------


class TestValidateChapter:
    """Tests for the validate_chapter function."""

    def test_all_valid(self, valid_provision, valid_definition, valid_table):
        """validate_chapter reports correct counts when all pass."""
        elements = [valid_provision, valid_definition, valid_table]
        result = validate_chapter(elements)
        assert result["total"] == 3
        assert result["passed"] == 3
        assert result["failed"] == 0
        assert result["errors"] == []

    def test_mixed_valid_invalid(self, valid_provision):
        """validate_chapter correctly counts mixed results."""
        invalid = {
            "id": "BAD",
            "type": "provision",
        }
        result = validate_chapter([valid_provision, invalid])
        assert result["total"] == 2
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1

    def test_empty_list(self):
        """validate_chapter handles empty element list."""
        result = validate_chapter([])
        assert result["total"] == 0
        assert result["passed"] == 0
        assert result["failed"] == 0


# ---------------------------------------------------------------------------
# load_schema
# ---------------------------------------------------------------------------


class TestLoadSchema:
    """Tests for the load_schema utility."""

    def test_load_schema_returns_dict(self):
        """load_schema returns a dict with expected top-level keys."""
        schema = load_schema()
        assert isinstance(schema, dict)
        assert "$schema" in schema
        assert "$defs" in schema
        assert "properties" in schema
