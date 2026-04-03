"""Unit tests for the pr_clean stripper (pr_clean/stripper.py).

Covers:

* :class:`~pr_clean.stripper.StripResult` dataclass properties.
* :func:`~pr_clean.stripper.strip_matches` convenience function.
* :func:`~pr_clean.stripper.strip_result` convenience function.
* :class:`~pr_clean.stripper.Stripper` for various block types.
* Blank-line collapsing and trailing-whitespace stripping.
* Overlapping span deduplication via :func:`_build_non_overlapping_spans`.
* Line expansion via :func:`_expand_span_to_full_lines`.
* Edge cases: empty text, no matches, matches-only text.
* Integration: scan then strip the full sample fixture.
"""

from __future__ import annotations

import json
textwrap_module_imported = True
import textwrap
from pathlib import Path
from typing import List

import pytest

from pr_clean.config import load_config_from_dict
from pr_clean.patterns import Severity, get_pattern_by_name
from pr_clean.scanner import ScanMatch, Scanner
from pr_clean.stripper import (
    Stripper,
    StripResult,
    _build_non_overlapping_spans,
    _collapse_excess_blank_lines,
    _expand_span_to_full_lines,
    _strip_trailing_whitespace,
    strip_matches,
    strip_result,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_pr_body.md"


@pytest.fixture()
def sample_text() -> str:
    """Return the contents of the sample PR body fixture."""
    return FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture()
def default_scanner() -> Scanner:
    """Return a Scanner with all-default settings."""
    return Scanner.from_defaults()


@pytest.fixture()
def copilot_block_text() -> str:
    """Minimal text containing a Copilot tips block."""
    return (
        "Intro line.\n"
        "START COPILOT CODING AGENT TIPS\n"
        "- Tip one\n"
        "- Tip two\n"
        "END COPILOT CODING AGENT TIPS\n"
        "Outro line.\n"
    )


@pytest.fixture()
def copilot_block_matches(
    copilot_block_text: str, default_scanner: Scanner
) -> List[ScanMatch]:
    """Pre-scanned matches for the copilot_block_text fixture."""
    return default_scanner.scan(copilot_block_text)


def _make_minimal_match(
    char_start: int,
    char_end: int,
    pattern_name: str = "copilot_agent_tips_block",
    severity: Severity = Severity.HIGH,
) -> ScanMatch:
    """Build a minimal ScanMatch suitable for span/overlap tests."""
    pattern = get_pattern_by_name("copilot_agent_tips_block")
    assert pattern is not None
    return ScanMatch(
        pattern_name=pattern_name,
        pattern=pattern,
        matched_text="x" * max(1, char_end - char_start),
        line_start=1,
        line_end=1,
        char_start=char_start,
        char_end=char_end,
        severity=severity,
        confidence=0.90,
    )


# ---------------------------------------------------------------------------
# _collapse_excess_blank_lines helper
# ---------------------------------------------------------------------------


class TestCollapseExcessBlankLines:
    """Tests for the _collapse_excess_blank_lines post-processing helper."""

    def test_three_blank_lines_collapsed_to_two(self) -> None:
        text = "a\n\n\n\nb"
        assert _collapse_excess_blank_lines(text) == "a\n\nb"

    def test_four_blank_lines_collapsed(self) -> None:
        text = "a\n\n\n\n\nb"
        result = _collapse_excess_blank_lines(text)
        assert "\n\n\n" not in result

    def test_two_blank_lines_preserved(self) -> None:
        text = "a\n\n\nb"
        assert _collapse_excess_blank_lines(text) == "a\n\nb"

    def test_single_blank_line_preserved(self) -> None:
        text = "a\n\nb"
        assert _collapse_excess_blank_lines(text) == "a\n\nb"

    def test_no_blank_lines_unchanged(self) -> None:
        text = "a\nb\nc"
        assert _collapse_excess_blank_lines(text) == text

    def test_ten_consecutive_blanks(self) -> None:
        text = "a" + "\n" * 10 + "b"
        result = _collapse_excess_blank_lines(text)
        assert "\n" * 3 not in result
        assert "a" in result
        assert "b" in result

    def test_empty_string_unchanged(self) -> None:
        assert _collapse_excess_blank_lines("") == ""

    def test_no_change_to_single_newline(self) -> None:
        text = "hello\nworld"
        assert _collapse_excess_blank_lines(text) == text


# ---------------------------------------------------------------------------
# _strip_trailing_whitespace helper
# ---------------------------------------------------------------------------


class TestStripTrailingWhitespace:
    """Tests for the _strip_trailing_whitespace helper."""

    def test_trailing_spaces_removed(self) -> None:
        text = "line one   \nline two\n"
        result = _strip_trailing_whitespace(text)
        assert "line one   " not in result
        assert "line one" in result

    def test_trailing_tabs_removed(self) -> None:
        text = "line one\t\t\nline two\n"
        result = _strip_trailing_whitespace(text)
        for line in result.splitlines():
            assert line == line.rstrip()

    def test_no_trailing_whitespace_unchanged(self) -> None:
        text = "line one\nline two\n"
        assert _strip_trailing_whitespace(text) == text

    def test_empty_string_unchanged(self) -> None:
        assert _strip_trailing_whitespace("") == ""

    def test_each_line_cleaned(self) -> None:
        text = "a   \nb   \nc   \n"
        result = _strip_trailing_whitespace(text)
        for line in result.splitlines():
            assert not line.endswith(" ")


# ---------------------------------------------------------------------------
# _expand_span_to_full_lines helper
# ---------------------------------------------------------------------------


class TestExpandSpanToFullLines:
    """Tests for the _expand_span_to_full_lines character-span expansion helper."""

    def test_single_line_match_expanded_to_line_boundaries(self) -> None:
        text = "line one\nGENERATED BY COPILOT\nline three\n"
        start = text.index("GENERATED")
        end = start + len("GENERATED BY COPILOT")
        exp_start, exp_end = _expand_span_to_full_lines(text, start, end, strip_full_block=True)
        # Expanded start should be at the beginning of the matched line.
        assert exp_start == text.index("GENERATED")
        # Expanded end should go past the trailing newline.
        assert exp_end > end

    def test_no_expansion_when_strip_full_block_false(self) -> None:
        text = "line one\nGENERATED\nline three\n"
        start = text.index("GENERATED")
        end = start + len("GENERATED")
        exp_start, exp_end = _expand_span_to_full_lines(
            text, start, end, strip_full_block=False
        )
        assert exp_start == start
        assert exp_end == end

    def test_expansion_at_start_of_text(self) -> None:
        text = "MATCH\nmore text\n"
        start, end = 0, len("MATCH")
        exp_start, exp_end = _expand_span_to_full_lines(text, start, end, strip_full_block=True)
        assert exp_start == 0

    def test_expansion_at_end_of_text_no_crash(self) -> None:
        text = "some text\nMATCH"
        start = text.index("MATCH")
        end = len(text)
        exp_start, exp_end = _expand_span_to_full_lines(text, start, end, strip_full_block=True)
        assert exp_end <= len(text) + 1

    def test_multiline_span_expanded(self) -> None:
        text = "before\nSTART\nmiddle\nEND\nafter\n"
        start = text.index("START")
        end = text.index("END") + len("END")
        exp_start, exp_end = _expand_span_to_full_lines(text, start, end, strip_full_block=True)
        assert exp_start <= start
        assert exp_end >= end

    def test_expansion_of_span_in_middle_of_line_goes_to_start(self) -> None:
        # Span starts mid-line; expansion should walk back to line start.
        text = "prefix MATCH suffix\nnext line\n"
        start = text.index("MATCH")
        end = start + len("MATCH")
        exp_start, exp_end = _expand_span_to_full_lines(text, start, end, strip_full_block=True)
        # Should have walked back to index 0 (start of the line).
        assert exp_start == 0


# ---------------------------------------------------------------------------
# _build_non_overlapping_spans helper
# ---------------------------------------------------------------------------


class TestBuildNonOverlappingSpans:
    """Tests for the overlap-resolution helper that merges scan match spans."""

    def test_non_overlapping_spans_preserved_count(self) -> None:
        matches = [
            _make_minimal_match(0, 10, "a"),
            _make_minimal_match(20, 30, "b"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert len(spans) == 2

    def test_non_overlapping_spans_correct_positions(self) -> None:
        matches = [
            _make_minimal_match(0, 10, "a"),
            _make_minimal_match(20, 30, "b"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert spans[0][0] == 0 and spans[0][1] == 10
        assert spans[1][0] == 20 and spans[1][1] == 30

    def test_overlapping_spans_merged_into_one(self) -> None:
        matches = [
            _make_minimal_match(0, 15, "a"),
            _make_minimal_match(10, 25, "b"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert len(spans) == 1

    def test_overlapping_spans_take_larger_end(self) -> None:
        matches = [
            _make_minimal_match(0, 15, "a"),
            _make_minimal_match(10, 25, "b"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert spans[0][0] == 0
        assert spans[0][1] == 25

    def test_identical_spans_deduplicated(self) -> None:
        matches = [
            _make_minimal_match(5, 15, "a"),
            _make_minimal_match(5, 15, "b"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert len(spans) == 1

    def test_empty_matches_returns_empty(self) -> None:
        assert _build_non_overlapping_spans([]) == []

    def test_adjacent_spans_not_merged(self) -> None:
        # Span [0,10) and [10,20) share a boundary but don't overlap.
        matches = [
            _make_minimal_match(0, 10, "a"),
            _make_minimal_match(10, 20, "b"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert len(spans) == 2

    def test_spans_sorted_by_start(self) -> None:
        matches = [
            _make_minimal_match(50, 60, "b"),
            _make_minimal_match(10, 20, "a"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert spans[0][0] < spans[1][0]

    def test_single_match_returned_as_single_span(self) -> None:
        matches = [_make_minimal_match(5, 15, "a")]
        spans = _build_non_overlapping_spans(matches)
        assert len(spans) == 1
        assert spans[0] == (5, 15, "a")

    def test_contained_span_does_not_split_outer(self) -> None:
        # [0, 30) contains [5, 15) completely.
        matches = [
            _make_minimal_match(0, 30, "outer"),
            _make_minimal_match(5, 15, "inner"),
        ]
        spans = _build_non_overlapping_spans(matches)
        assert len(spans) == 1
        assert spans[0][0] == 0
        assert spans[0][1] == 30


# ---------------------------------------------------------------------------
# StripResult dataclass
# ---------------------------------------------------------------------------


class TestStripResult:
    """Tests for the StripResult dataclass and its properties."""

    def test_changed_true_when_text_differs(self) -> None:
        r = StripResult(
            original_text="original",
            clean_text="clean",
            removed_spans=[(0, 4, "p")],
        )
        assert r.changed is True

    def test_changed_false_when_text_identical(self) -> None:
        r = StripResult(
            original_text="same",
            clean_text="same",
            removed_spans=[],
        )
        assert r.changed is False

    def test_match_count_equals_removed_spans(self) -> None:
        r = StripResult(
            original_text="x",
            clean_text="",
            removed_spans=[(0, 1, "p1"), (2, 3, "p2")],
        )
        assert r.match_count == 2

    def test_match_count_zero_with_no_spans(self) -> None:
        r = StripResult(original_text="a", clean_text="a", removed_spans=[])
        assert r.match_count == 0

    def test_to_dict_has_required_keys(self) -> None:
        r = StripResult(original_text="a", clean_text="b", removed_spans=[])
        d = r.to_dict()
        assert set(d.keys()) == {"changed", "match_count", "removed_spans", "clean_text"}

    def test_to_dict_changed_value(self) -> None:
        r = StripResult(original_text="a", clean_text="b", removed_spans=[])
        assert r.to_dict()["changed"] is True

    def test_to_dict_removed_spans_structure(self) -> None:
        r = StripResult(
            original_text="abcde",
            clean_text="",
            removed_spans=[(0, 5, "my_pattern")],
        )
        d = r.to_dict()
        assert len(d["removed_spans"]) == 1
        span = d["removed_spans"][0]
        assert span["char_start"] == 0
        assert span["char_end"] == 5
        assert span["pattern_name"] == "my_pattern"

    def test_to_dict_is_json_serialisable(self) -> None:
        r = StripResult(
            original_text="original",
            clean_text="clean",
            removed_spans=[(0, 4, "some_pattern")],
        )
        serialised = json.dumps(r.to_dict())
        parsed = json.loads(serialised)
        assert parsed["changed"] is True
        assert parsed["match_count"] == 1

    def test_original_text_preserved_on_result(self) -> None:
        r = StripResult(original_text="original", clean_text="clean", removed_spans=[])
        assert r.original_text == "original"


# ---------------------------------------------------------------------------
# Stripper.strip — core behaviour
# ---------------------------------------------------------------------------


class TestStripperCoreStripping:
    """Tests for Stripper.strip() core removal behaviour."""

    def test_empty_text_returns_unchanged(self) -> None:
        stripper = Stripper()
        result = stripper.strip("", [])
        assert result.clean_text == ""
        assert result.changed is False

    def test_no_matches_returns_original_text(
        self, copilot_block_text: str
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(copilot_block_text, [])
        assert result.clean_text == copilot_block_text
        assert result.changed is False

    def test_copilot_tips_block_removed(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(copilot_block_text, copilot_block_matches)
        assert "START COPILOT CODING AGENT TIPS" not in result.clean_text
        assert "END COPILOT CODING AGENT TIPS" not in result.clean_text

    def test_surrounding_content_preserved(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(copilot_block_text, copilot_block_matches)
        assert "Intro line." in result.clean_text
        assert "Outro line." in result.clean_text

    def test_result_changed_true(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(copilot_block_text, copilot_block_matches)
        assert result.changed is True

    def test_removed_spans_recorded(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(copilot_block_text, copilot_block_matches)
        assert result.match_count > 0

    def test_clean_text_shorter_than_original(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(copilot_block_text, copilot_block_matches)
        assert len(result.clean_text) < len(result.original_text)

    def test_original_text_unchanged_on_result(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(copilot_block_text, copilot_block_matches)
        assert result.original_text == copilot_block_text

    def test_strip_text_convenience_method(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        clean = stripper.strip_text(copilot_block_text, copilot_block_matches)
        assert isinstance(clean, str)
        assert "START COPILOT CODING AGENT TIPS" not in clean

    def test_strip_text_preserves_surrounding(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        clean = stripper.strip_text(copilot_block_text, copilot_block_matches)
        assert "Intro line." in clean
        assert "Outro line." in clean


# ---------------------------------------------------------------------------
# Stripper.strip — blank line collapsing
# ---------------------------------------------------------------------------


class TestBlankLineCollapsing:
    """Tests that blank-line collapsing post-processing works correctly."""

    def test_excess_blank_lines_collapsed_with_no_matches(self) -> None:
        text = "Before\n\n\n\n\nAfter\n"
        stripper = Stripper()
        result = stripper.strip(text, [], collapse_blank_lines=True)
        assert "\n\n\n" not in result.clean_text

    def test_blank_line_collapse_disabled(self) -> None:
        text = "Before\n\n\n\n\nAfter\n"
        stripper = Stripper()
        result = stripper.strip(text, [], collapse_blank_lines=False)
        # Excess blanks should remain since we disabled collapsing.
        assert "\n\n\n" in result.clean_text

    def test_collapse_blank_lines_after_block_removal(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        stripper = Stripper()
        result = stripper.strip(
            copilot_block_text, copilot_block_matches, collapse_blank_lines=True
        )
        assert "\n\n\n" not in result.clean_text


# ---------------------------------------------------------------------------
# Stripper.strip — trailing whitespace
# ---------------------------------------------------------------------------


class TestTrailingWhitespaceStripping:
    """Tests for the trailing-whitespace cleanup in Stripper."""

    def test_trailing_whitespace_removed(self) -> None:
        text = "line one   \nline two\t\nline three\n"
        stripper = Stripper()
        result = stripper.strip(text, [], strip_trailing_whitespace=True)
        for line in result.clean_text.splitlines():
            assert line == line.rstrip(), f"Trailing whitespace in: {line!r}"

    def test_trailing_whitespace_preserved_when_disabled(self) -> None:
        text = "line one   \nline two\n"
        stripper = Stripper()
        result = stripper.strip(
            text, [], strip_trailing_whitespace=False, collapse_blank_lines=False
        )
        assert "line one   " in result.clean_text


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


class TestStripMatchesFunction:
    """Tests for the strip_matches module-level convenience function."""

    def test_returns_string(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        clean = strip_matches(copilot_block_text, copilot_block_matches)
        assert isinstance(clean, str)

    def test_injection_removed(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        clean = strip_matches(copilot_block_text, copilot_block_matches)
        assert "START COPILOT CODING AGENT TIPS" not in clean

    def test_empty_matches_returns_original(self) -> None:
        text = "No injections here."
        assert strip_matches(text, []) == text

    def test_empty_text_returns_empty(self) -> None:
        assert strip_matches("", []) == ""

    def test_surrounding_content_preserved(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        clean = strip_matches(copilot_block_text, copilot_block_matches)
        assert "Intro line." in clean
        assert "Outro line." in clean


class TestStripResultFunction:
    """Tests for the strip_result module-level convenience function."""

    def test_returns_strip_result(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        r = strip_result(copilot_block_text, copilot_block_matches)
        assert isinstance(r, StripResult)

    def test_result_changed(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        r = strip_result(copilot_block_text, copilot_block_matches)
        assert r.changed is True

    def test_result_original_text_preserved(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        r = strip_result(copilot_block_text, copilot_block_matches)
        assert r.original_text == copilot_block_text

    def test_result_match_count_positive(
        self,
        copilot_block_text: str,
        copilot_block_matches: List[ScanMatch],
    ) -> None:
        r = strip_result(copilot_block_text, copilot_block_matches)
        assert r.match_count > 0

    def test_result_no_matches_not_changed(self) -> None:
        text = "Clean text with no injections."
        r = strip_result(text, [])
        assert r.changed is False
        assert r.clean_text == text


# ---------------------------------------------------------------------------
# Integration: scan then strip the full sample fixture
# ---------------------------------------------------------------------------


class TestScanThenStrip:
    """End-to-end integration tests that scan and then strip the fixture."""

    def test_fixture_has_matches(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        assert len(matches) > 0

    def test_strip_removes_copilot_block_from_fixture(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        clean = strip_matches(sample_text, matches)
        assert "START COPILOT CODING AGENT TIPS" not in clean
        assert "END COPILOT CODING AGENT TIPS" not in clean

    def test_human_authored_title_preserved(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        clean = strip_matches(sample_text, matches)
        assert "Fix authentication timeout bug" in clean

    def test_human_authored_notes_preserved(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        clean = strip_matches(sample_text, matches)
        assert "Human-authored notes" in clean

    def test_clean_text_shorter_than_original(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        clean = strip_matches(sample_text, matches)
        assert len(clean) < len(sample_text)

    def test_clean_text_has_no_excess_blank_lines(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        clean = strip_matches(sample_text, matches)
        assert "\n\n\n" not in clean, "Excess blank lines found after stripping"

    def test_strip_result_records_removed_spans(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        r = strip_result(sample_text, matches)
        assert r.match_count > 0

    def test_strip_result_to_dict_is_json_serialisable(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        r = strip_result(sample_text, matches)
        serialised = json.dumps(r.to_dict())
        parsed = json.loads(serialised)
        assert parsed["changed"] is True

    def test_removed_spans_char_ranges_are_valid(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        r = strip_result(sample_text, matches)
        for start, end, name in r.removed_spans:
            assert 0 <= start <= end <= len(sample_text), (
                f"Invalid span ({start}, {end}) for pattern {name}"
            )

    def test_strip_result_changed_is_true(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        r = strip_result(sample_text, matches)
        assert r.changed is True

    def test_changes_section_still_in_clean_text(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        clean = strip_matches(sample_text, matches)
        # The '## Changes' section is human-authored and should survive.
        assert "## Changes" in clean


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestStripperEdgeCases:
    """Edge cases for the Stripper and convenience functions."""

    def test_match_at_very_start_of_text(
        self, default_scanner: Scanner
    ) -> None:
        text = "Generated by GitHub Copilot\nThis is fine.\n"
        matches = default_scanner.scan(text)
        clean = strip_matches(text, matches)
        assert "Generated by GitHub Copilot" not in clean
        assert "This is fine." in clean

    def test_match_at_very_end_of_text(
        self, default_scanner: Scanner
    ) -> None:
        text = "This is fine.\nGenerated by GitHub Copilot"
        matches = default_scanner.scan(text)
        clean = strip_matches(text, matches)
        assert "This is fine." in clean
        assert "Generated by GitHub Copilot" not in clean

    def test_multiple_non_overlapping_matches_all_removed(
        self, default_scanner: Scanner
    ) -> None:
        text = (
            "Intro.\n"
            "Generated by GitHub Copilot\n"
            "Middle.\n"
            "Powered by GitHub Copilot\n"
            "Outro.\n"
        )
        matches = default_scanner.scan(text)
        clean = strip_matches(text, matches)
        assert "Generated by GitHub Copilot" not in clean
        assert "Powered by GitHub Copilot" not in clean
        assert "Intro." in clean
        assert "Middle." in clean
        assert "Outro." in clean

    def test_text_with_only_injection_mostly_empty(
        self, default_scanner: Scanner
    ) -> None:
        text = "Generated by GitHub Copilot\n"
        matches = default_scanner.scan(text)
        clean = strip_matches(text, matches)
        assert "Generated by GitHub Copilot" not in clean

    def test_custom_pattern_stripped(self) -> None:
        cfg = load_config_from_dict(
            {
                "patterns": [
                    {
                        "name": "internal_bot",
                        "regex": r"INTERNAL BOT OUTPUT",
                        "severity": "high",
                        "confidence": 0.95,
                        "strip_full_block": True,
                    }
                ]
            }
        )
        scanner = Scanner.from_config(cfg)
        text = "Good content.\nINTERNAL BOT OUTPUT\nMore good content.\n"
        matches = scanner.scan(text)
        clean = strip_matches(text, matches)
        assert "INTERNAL BOT OUTPUT" not in clean
        assert "Good content." in clean
        assert "More good content." in clean

    def test_strip_preserves_exact_human_lines(self) -> None:
        """Verify that precise human-authored lines are preserved byte-for-byte."""
        human_line = "This is an important human comment that must survive."
        text = (
            f"{human_line}\n"
            "Generated by GitHub Copilot\n"
            f"{human_line}\n"
        )
        scanner = Scanner.from_defaults()
        matches = scanner.scan(text)
        clean = strip_matches(text, matches)
        assert human_line in clean

    def test_full_copilot_block_with_surrounding_content(
        self, default_scanner: Scanner
    ) -> None:
        text = (
            "# PR Title\n"
            "\n"
            "## Description\n"
            "\n"
            "Some human description.\n"
            "\n"
            "START COPILOT CODING AGENT TIPS\n"
            "- Tip A\n"
            "- Tip B\n"
            "END COPILOT CODING AGENT TIPS\n"
            "\n"
            "## Testing\n"
            "\n"
            "Run the tests.\n"
        )
        matches = default_scanner.scan(text)
        clean = strip_matches(text, matches)
        assert "START COPILOT CODING AGENT TIPS" not in clean
        assert "END COPILOT CODING AGENT TIPS" not in clean
        assert "# PR Title" in clean
        assert "Some human description." in clean
        assert "## Testing" in clean
        assert "Run the tests." in clean
