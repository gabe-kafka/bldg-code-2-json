"""
Shared fixtures for bldg-code-2-json tests.
"""

import json
from pathlib import Path

import pytest


SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "element.schema.json"
RAW_OUTPUT_PATH = Path(__file__).parent.parent / "output" / "raw" / "asce722-ch26.json"


@pytest.fixture
def schema():
    """Load the element JSON Schema."""
    with open(SCHEMA_PATH) as f:
        return json.load(f)


@pytest.fixture
def valid_provision():
    """A known-good provision element that should always validate."""
    return {
        "id": "ASCE7-22-26.5-1",
        "type": "provision",
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.5",
            "page": None,
        },
        "title": "Directional Procedure",
        "description": None,
        "data": {
            "rule": "Buildings with mean roof height h > 60 ft shall use exposure defined in Section 26.7.3",
            "conditions": [
                {
                    "parameter": "mean_roof_height",
                    "operator": ">",
                    "value": 60,
                    "unit": "ft",
                }
            ],
            "then": "use Section 26.7.3",
            "else": "use Section 26.7.4",
            "exceptions": [],
        },
        "cross_references": ["ASCE7-22-26.7-3"],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }


@pytest.fixture
def valid_definition():
    """A well-formed definition element."""
    return {
        "id": "ASCE7-22-26.2-1",
        "type": "definition",
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.2",
            "page": None,
        },
        "title": "Basic Wind Speed",
        "description": None,
        "data": {
            "term": "BASIC WIND SPEED",
            "definition": "Three-second gust speed at 33 ft above the ground in Exposure Category C.",
            "conditions": [],
            "exceptions": [],
        },
        "cross_references": [],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }


@pytest.fixture
def valid_table():
    """A known-good table element."""
    return {
        "id": "ASCE7-22-26.6-T1",
        "type": "table",
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.6",
            "page": None,
        },
        "title": "Directional Factor Kd",
        "description": None,
        "data": {
            "columns": [
                {"name": "structure_type", "unit": None},
                {"name": "Kd", "unit": "dimensionless"},
            ],
            "rows": [
                {"structure_type": "Main Wind Force Resisting System", "Kd": 0.85},
            ],
        },
        "cross_references": [],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }
