"""
Tests for few-shot gold example injection in llm_structurer.structure_page.

Mocks anthropic.Anthropic to capture the prompt sent to the API.
No API key needed.

Test cases:
1. Gold examples appear in prompt when gold files exist
2. Type-relevant selection (page with table gets table example)
3. Graceful fallback when no gold available
4. At most 3 examples injected
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from extract.llm_structurer import structure_page, select_fewshot_examples
from extract.pdf_parser import PageExtraction, ExtractedText, ExtractedTable, ExtractedFigure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page(text_blocks=None, tables=None, figures=None, page_number=1):
    return PageExtraction(
        page_number=page_number,
        text_blocks=text_blocks or [],
        tables=tables or [],
        figures=figures or [],
    )


def _gold_element(id_suffix, el_type="provision"):
    """Minimal schema-valid gold element for testing."""
    base = {
        "id": f"ASCE7-22-26.5-{id_suffix}",
        "type": el_type,
        "source": {"standard": "ASCE 7-22", "chapter": 26, "section": "26.5", "page": None},
        "title": f"Gold {el_type}",
        "description": None,
        "cross_references": [],
        "metadata": {"extracted_by": "auto", "qc_status": "passed", "qc_notes": None},
    }
    if el_type == "provision":
        base["data"] = {"rule": "Gold rule", "conditions": [], "then": "do X", "else": None, "exceptions": []}
    elif el_type == "table":
        base["data"] = {"columns": [{"name": "A", "unit": None}], "rows": [{"A": "1"}]}
    elif el_type == "formula":
        base["data"] = {"expression": "V = Kz", "parameters": {"Kz": {"unit": "m/s"}}}
    elif el_type == "reference":
        base["data"] = {"target": "ASCE 7-22 Ch.27"}
    elif el_type == "definition":
        base["data"] = {"term": "Wind Speed", "definition": "Three-second gust speed"}
    return base


def _mock_llm_response():
    """Return a mock anthropic response with an empty JSON array."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock()]
    mock_msg.content[0].text = "[]"
    return mock_msg


def _capture_prompt(MockAnthropic):
    """Extract the prompt text from the mocked messages.create call."""
    call_args = MockAnthropic.return_value.messages.create.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
    # The prompt is in the first content block
    content = messages[0]["content"]
    if isinstance(content, list):
        return content[0]["text"]
    return content


# ---------------------------------------------------------------------------
# 1. Gold examples appear in prompt when gold files exist
# ---------------------------------------------------------------------------

class TestGoldExamplesInPrompt:

    @patch("extract.llm_structurer.anthropic.Anthropic")
    @patch("extract.llm_structurer.load_gold_elements")
    def test_reference_examples_section_present(self, mock_load_gold, MockAnthropic):
        mock_load_gold.return_value = [
            _gold_element("P1", "provision"),
            _gold_element("T1", "table"),
        ]
        MockAnthropic.return_value.messages.create.return_value = _mock_llm_response()

        page = _make_page(text_blocks=[ExtractedText(page=1, text="Some text")])
        structure_page(page, "ASCE 7-22", 26)

        prompt = _capture_prompt(MockAnthropic)
        assert "REFERENCE EXAMPLES" in prompt
        assert "Gold rule" in prompt  # provision gold content


# ---------------------------------------------------------------------------
# 2. Type-relevant selection
# ---------------------------------------------------------------------------

class TestTypeRelevantSelection:

    def test_page_with_table_prefers_table_example(self):
        golds = [
            _gold_element("P1", "provision"),
            _gold_element("T1", "table"),
            _gold_element("E1", "formula"),
        ]
        page = _make_page(
            tables=[ExtractedTable(page=1, bbox=(0, 0, 1, 1), headers=["A"], rows=[["1"]])],
        )
        selected = select_fewshot_examples(golds, page, max_examples=1)
        assert len(selected) == 1
        assert selected[0]["type"] == "table"

    def test_page_with_text_prefers_provision(self):
        golds = [
            _gold_element("T1", "table"),
            _gold_element("P1", "provision"),
            _gold_element("E1", "formula"),
        ]
        page = _make_page(
            text_blocks=[ExtractedText(page=1, text="Some provision text")],
        )
        selected = select_fewshot_examples(golds, page, max_examples=1)
        assert len(selected) == 1
        assert selected[0]["type"] == "provision"

    def test_mixed_page_gets_diverse_types(self):
        golds = [
            _gold_element("P1", "provision"),
            _gold_element("P2", "provision"),
            _gold_element("T1", "table"),
            _gold_element("E1", "formula"),
        ]
        page = _make_page(
            text_blocks=[ExtractedText(page=1, text="text")],
            tables=[ExtractedTable(page=1, bbox=(0, 0, 1, 1), headers=["A"], rows=[["1"]])],
        )
        selected = select_fewshot_examples(golds, page, max_examples=3)
        types = {el["type"] for el in selected}
        assert len(types) >= 2  # at least 2 different types


# ---------------------------------------------------------------------------
# 3. Graceful fallback when no gold available
# ---------------------------------------------------------------------------

class TestNoGoldFallback:

    @patch("extract.llm_structurer.anthropic.Anthropic")
    @patch("extract.llm_structurer.load_gold_elements")
    def test_no_crash_without_gold(self, mock_load_gold, MockAnthropic):
        mock_load_gold.return_value = []
        MockAnthropic.return_value.messages.create.return_value = _mock_llm_response()

        page = _make_page(text_blocks=[ExtractedText(page=1, text="Some text")])
        result = structure_page(page, "ASCE 7-22", 26)

        assert result == []
        prompt = _capture_prompt(MockAnthropic)
        assert "REFERENCE EXAMPLES" not in prompt

    def test_select_empty_gold_returns_empty(self):
        page = _make_page(text_blocks=[ExtractedText(page=1, text="text")])
        assert select_fewshot_examples([], page) == []


# ---------------------------------------------------------------------------
# 4. At most 3 examples injected
# ---------------------------------------------------------------------------

class TestMaxExamplesCap:

    def test_caps_at_3_even_with_many_golds(self):
        golds = [_gold_element(f"P{i}", "provision") for i in range(10)]
        page = _make_page(text_blocks=[ExtractedText(page=1, text="text")])
        selected = select_fewshot_examples(golds, page, max_examples=3)
        assert len(selected) == 3

    @patch("extract.llm_structurer.anthropic.Anthropic")
    @patch("extract.llm_structurer.load_gold_elements")
    def test_prompt_has_at_most_3_examples(self, mock_load_gold, MockAnthropic):
        mock_load_gold.return_value = [_gold_element(f"P{i}", "provision") for i in range(10)]
        MockAnthropic.return_value.messages.create.return_value = _mock_llm_response()

        page = _make_page(text_blocks=[ExtractedText(page=1, text="Some text")])
        structure_page(page, "ASCE 7-22", 26)

        prompt = _capture_prompt(MockAnthropic)
        assert prompt.count("Example ") == 3  # exactly 3 examples
