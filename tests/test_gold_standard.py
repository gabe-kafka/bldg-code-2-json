"""
Tests for extract/gold_standard.py — gold standard element management.

Uses tmp_path fixture for all file-based tests. No API calls needed.

Test cases:
1. Load valid gold files
2. Load empty directory (returns [])
3. Load missing directory (returns [])
4. Load with malformed file (skips it, returns valid ones)
5. Draft generation filters to valid elements only
6. Draft generation caps at max_per_type
7. Draft generation covers diverse types
8. Write creates individual JSON files
9. Gold files conform to element schema (validate_element passes)
10. Gold elements have qc_status 'passed'
"""

import json
import pytest

from extract.gold_standard import (
    load_gold_elements,
    generate_draft_gold_set,
    write_gold_files,
)
from qc.schema_validator import load_schema, validate_element


SCHEMA = load_schema()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_element(id_suffix="P1", el_type="provision"):
    """Build a minimal schema-valid element of given type."""
    base = {
        "id": f"ASCE7-22-26.5-{id_suffix}",
        "type": el_type,
        "source": {
            "standard": "ASCE 7-22",
            "chapter": 26,
            "section": "26.5",
            "page": None,
        },
        "title": f"Test {el_type}",
        "description": None,
        "cross_references": [],
        "metadata": {
            "extracted_by": "auto",
            "qc_status": "pending",
            "qc_notes": None,
        },
    }
    if el_type == "provision":
        base["data"] = {
            "rule": "Some rule text",
            "conditions": [],
            "then": "apply method A",
            "else": None,
            "exceptions": [],
        }
    elif el_type == "table":
        base["data"] = {
            "columns": [{"name": "Col A", "unit": None}],
            "rows": [{"Col A": "val"}],
        }
    elif el_type == "formula":
        base["data"] = {
            "expression": "V = K * z",
            "parameters": {"K": {"unit": "m/s"}},
        }
    elif el_type == "reference":
        base["data"] = {"target": "ASCE 7-22 Chapter 27"}
    elif el_type == "definition":
        base["data"] = {
            "term": "Basic Wind Speed",
            "definition": "Three-second gust speed at 33 ft above ground",
        }
    return base


def _write_gold_file(gold_dir, element):
    """Write a single element as a gold file."""
    gold_dir.mkdir(parents=True, exist_ok=True)
    path = gold_dir / f"{element['id']}.json"
    path.write_text(json.dumps(element, indent=2))


# ---------------------------------------------------------------------------
# 1. Load valid gold files
# ---------------------------------------------------------------------------

class TestLoadValidGoldFiles:

    def test_loads_json_files(self, tmp_path):
        gold_dir = tmp_path / "gold"
        el1 = _valid_element("P1")
        el2 = _valid_element("P2")
        _write_gold_file(gold_dir, el1)
        _write_gold_file(gold_dir, el2)

        result = load_gold_elements(str(gold_dir))
        assert len(result) == 2
        ids = {el["id"] for el in result}
        assert "ASCE7-22-26.5-P1" in ids
        assert "ASCE7-22-26.5-P2" in ids


# ---------------------------------------------------------------------------
# 2. Load empty directory
# ---------------------------------------------------------------------------

class TestLoadEmptyDirectory:

    def test_empty_dir_returns_empty(self, tmp_path):
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        result = load_gold_elements(str(gold_dir))
        assert result == []


# ---------------------------------------------------------------------------
# 3. Load missing directory
# ---------------------------------------------------------------------------

class TestLoadMissingDirectory:

    def test_missing_dir_returns_empty(self, tmp_path):
        result = load_gold_elements(str(tmp_path / "nonexistent"))
        assert result == []


# ---------------------------------------------------------------------------
# 4. Load with malformed file (skips it)
# ---------------------------------------------------------------------------

class TestLoadMalformedFile:

    def test_skips_malformed_returns_valid(self, tmp_path):
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()

        # Valid file
        el = _valid_element("P1")
        _write_gold_file(gold_dir, el)

        # Malformed JSON file
        (gold_dir / "bad.json").write_text("{not valid json")

        result = load_gold_elements(str(gold_dir))
        assert len(result) == 1
        assert result[0]["id"] == "ASCE7-22-26.5-P1"

    def test_skips_schema_invalid_file(self, tmp_path):
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()

        # Valid file
        el = _valid_element("P1")
        _write_gold_file(gold_dir, el)

        # Schema-invalid file (valid JSON but missing required fields)
        (gold_dir / "invalid.json").write_text(json.dumps({"id": "BAD"}))

        result = load_gold_elements(str(gold_dir))
        assert len(result) == 1
        assert result[0]["id"] == "ASCE7-22-26.5-P1"


# ---------------------------------------------------------------------------
# 5. Draft generation filters to valid elements only
# ---------------------------------------------------------------------------

class TestDraftFiltersValid:

    def test_excludes_invalid_elements(self):
        valid = _valid_element("P1")
        invalid = {
            "id": "BAD-1-1-P1",
            "type": "provision",
            "data": {"rule": "no conditions"},
        }
        result = generate_draft_gold_set([valid, invalid])
        assert len(result) == 1
        assert result[0]["id"] == "ASCE7-22-26.5-P1"


# ---------------------------------------------------------------------------
# 6. Draft generation caps at max_per_type
# ---------------------------------------------------------------------------

class TestDraftCapsPerType:

    def test_max_per_type_respected(self):
        elements = [_valid_element(f"P{i}") for i in range(10)]
        result = generate_draft_gold_set(elements, max_per_type=3)
        assert len(result) == 3

    def test_max_per_type_one(self):
        elements = [_valid_element(f"P{i}") for i in range(5)]
        result = generate_draft_gold_set(elements, max_per_type=1)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 7. Draft generation covers diverse types
# ---------------------------------------------------------------------------

class TestDraftDiverseTypes:

    def test_selects_from_each_type(self):
        elements = [
            _valid_element("P1", "provision"),
            _valid_element("T1", "table"),
            _valid_element("E1", "formula"),
            _valid_element("R1", "reference"),
            _valid_element("D1", "definition"),
        ]
        result = generate_draft_gold_set(elements, max_per_type=2)
        types = {el["type"] for el in result}
        assert types == {"provision", "table", "formula", "reference", "definition"}


# ---------------------------------------------------------------------------
# 8. Write creates individual JSON files
# ---------------------------------------------------------------------------

class TestWriteGoldFiles:

    def test_creates_files(self, tmp_path):
        gold_dir = tmp_path / "gold"
        elements = [_valid_element("P1"), _valid_element("T1", "table")]
        write_gold_files(elements, str(gold_dir))

        files = sorted(gold_dir.glob("*.json"))
        assert len(files) == 2

        for f in files:
            data = json.loads(f.read_text())
            assert "id" in data

    def test_creates_directory_if_needed(self, tmp_path):
        gold_dir = tmp_path / "nested" / "gold"
        write_gold_files([_valid_element("P1")], str(gold_dir))
        assert gold_dir.exists()
        assert len(list(gold_dir.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# 9. Gold files conform to element schema
# ---------------------------------------------------------------------------

class TestGoldConformToSchema:

    def test_generated_gold_validates(self):
        elements = [
            _valid_element("P1", "provision"),
            _valid_element("T1", "table"),
            _valid_element("E1", "formula"),
        ]
        golds = generate_draft_gold_set(elements, max_per_type=3)
        for el in golds:
            vr = validate_element(el, SCHEMA)
            assert vr["valid"], f"{el['id']} failed: {vr['errors']}"


# ---------------------------------------------------------------------------
# 10. Gold elements have qc_status 'passed'
# ---------------------------------------------------------------------------

class TestGoldQcStatus:

    def test_qc_status_set_to_passed(self):
        elements = [_valid_element("P1")]
        assert elements[0]["metadata"]["qc_status"] == "pending"

        golds = generate_draft_gold_set(elements)
        assert golds[0]["metadata"]["qc_status"] == "passed"

    def test_already_passed_stays_passed(self):
        el = _valid_element("P1")
        el["metadata"]["qc_status"] = "passed"
        golds = generate_draft_gold_set([el])
        assert golds[0]["metadata"]["qc_status"] == "passed"
