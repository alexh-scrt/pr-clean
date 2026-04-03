"""Unit tests for the core scanner logic (pr_clean/scanner.py).

These tests cover:

* :class:`~pr_clean.scanner.ScanMatch` dataclass properties.
* :class:`~pr_clean.scanner.Scanner` construction variants.
* Detection of known injection patterns against the sample fixture.
* Severity threshold filtering.
* Source label propagation.
* Deduplication of overlapping matches.
* Edge cases: empty text, whitespace-only text, no matches.
* ``scan_multiple`` for scanning several texts at once.
* ``to_dict`` serialisation.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import List

import pytest

from pr_clean.config import load_config_from_dict
from pr_clean.patterns import InjectionPattern, Severity, get_pattern_by_name, BUILTIN_PATTERNS
from pr_clean.scanner import (
    ScanMatch,
    Scanner,
    _char_offset_to_line_number,
    _adjust_confidence,
    _meets_severity_threshold,
    _extract_context_lines,
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
def copilot_tips_block() -> str:
    """Minimal text containing the Copilot tips block."""
    return (
        "Some intro text.\n"
        "START COPILOT CODING AGENT TIPS\n"
        "- Tip one\n"
        "- Tip two\n"
        "END COPILOT CODING AGENT TIPS\n"
        "Some outro text.\n"
    )


# ---------------------------------------------------------------------------
# _char_offset_to_line_number helper
# ---------------------------------------------------------------------------


class TestCharOffsetToLineNumber:
    """Tests for the _char_offset_to_line_number internal helper."""

    def test_offset_zero_is_line_one(self) -> None:
        assert _char_offset_to_line_number("hello\nworld", 0) == 1

    def test_offset_after_first_newline(self) -> None:
        text = "hello\nworld"
        # 'w' is at index 6
        assert _char_offset_to_line_number(text, 6) == 2

    def test_offset_at_newline_character(self) -> None:
        text = "hello\nworld"
        # '\n' is at index 5 — still on line 1
        assert _char_offset_to_line_number(text, 5) == 1

    def test_single_line_text(self) -> None:
        assert _char_offset_to_line_number("hello", 3) == 1

    def test_multiline_text_third_line(self) -> None:
        text = "a\nb\nc\nd"
        # 'c' is at index 4
        assert _char_offset_to_line_number(text, 4) == 3

    def test_offset_clipped_to_zero(self) -> None:
        assert _char_offset_to_line_number("hello", -5) == 1

    def test_offset_clipped_to_len(self) -> None:
        text = "hello\nworld"
        # Beyond end should not crash.
        result = _char_offset_to_line_number(text, 9999)
        assert result >= 1

    def test_empty_string_returns_one(self) -> None:
        assert _char_offset_to_line_number("", 0) == 1

    def test_multiple_newlines(self) -> None:
        text = "line1\nline2\nline3\nline4"
        # 'line4' starts at index 18
        idx = text.index("line4")
        assert _char_offset_to_line_number(text, idx) == 4


# ---------------------------------------------------------------------------
# _adjust_confidence helper
# ---------------------------------------------------------------------------


class TestAdjustConfidence:
    """Tests for the _adjust_confidence heuristic helper."""

    def _get_pattern(self) -> InjectionPattern:
        p = get_pattern_by_name("copilot_agent_tips_block")
        assert p is not None
        return p

    def test_short_text_reduces_confidence(self) -> None:
        pattern = self._get_pattern()
        base = 0.80
        adjusted = _adjust_confidence(base, "short", pattern)
        assert adjusted < base

    def test_long_text_over_200_increases_confidence(self) -> None:
        pattern = self._get_pattern()
        base = 0.80
        long_text = "x" * 250
        adjusted = _adjust_confidence(base, long_text, pattern)
        assert adjusted > base

    def test_medium_long_text_over_100_increases_confidence(self) -> None:
        pattern = self._get_pattern()
        base = 0.80
        medium_text = "x" * 150
        adjusted = _adjust_confidence(base, medium_text, pattern)
        assert adjusted > base

    def test_multiline_text_increases_confidence(self) -> None:
        pattern = self._get_pattern()
        base = 0.80
        # Use a text > 20 chars so the length penalty doesn't interfere.
        adjusted = _adjust_confidence(base, "line one is long\nline two is also long", pattern)
        assert adjusted > base

    def test_result_clamped_to_one(self) -> None:
        pattern = self._get_pattern()
        adjusted = _adjust_confidence(1.0, "x" * 300, pattern)
        assert adjusted <= 1.0

    def test_result_clamped_to_zero(self) -> None:
        pattern = self._get_pattern()
        # Very low base + very short text; should not go below 0.
        adjusted = _adjust_confidence(0.0, "x", pattern)
        assert adjusted >= 0.0

    def test_moderate_text_length_no_penalty(self) -> None:
        pattern = self._get_pattern()
        # Text between 21 and 100 chars: no boost, no penalty.
        base = 0.80
        text = "x" * 50
        adjusted = _adjust_confidence(base, text, pattern)
        # No change expected for this range.
        assert adjusted == base


# ---------------------------------------------------------------------------
# _meets_severity_threshold helper
# ---------------------------------------------------------------------------


class TestMeetsSeverityThreshold:
    """Tests for the severity threshold comparison helper."""

    def test_same_level_passes(self) -> None:
        assert _meets_severity_threshold(Severity.MEDIUM, Severity.MEDIUM) is True

    def test_higher_severity_passes(self) -> None:
        assert _meets_severity_threshold(Severity.CRITICAL, Severity.LOW) is True

    def test_lower_severity_fails(self) -> None:
        assert _meets_severity_threshold(Severity.LOW, Severity.CRITICAL) is False

    def test_low_threshold_passes_all(self) -> None:
        for sev in Severity:
            assert _meets_severity_threshold(sev, Severity.LOW) is True

    def test_critical_threshold_only_critical_passes(self) -> None:
        assert _meets_severity_threshold(Severity.CRITICAL, Severity.CRITICAL) is True
        assert _meets_severity_threshold(Severity.HIGH, Severity.CRITICAL) is False
        assert _meets_severity_threshold(Severity.MEDIUM, Severity.CRITICAL) is False
        assert _meets_severity_threshold(Severity.LOW, Severity.CRITICAL) is False


# ---------------------------------------------------------------------------
# _extract_context_lines helper
# ---------------------------------------------------------------------------


class TestExtractContextLines:
    """Tests for the context line extraction helper."""

    def test_returns_surrounding_lines(self) -> None:
        lines = ["a", "b", "c", "d", "e"]
        ctx = _extract_context_lines(lines, line_start=3, line_end=3, context=1)
        # line 3 is index 2 ('c'); context=1 means lines 2-4 (indices 1-3)
        assert "b" in ctx
        assert "c" in ctx
        assert "d" in ctx

    def test_does_not_exceed_bounds(self) -> None:
        lines = ["only_line"]
        ctx = _extract_context_lines(lines, line_start=1, line_end=1, context=5)
        assert ctx == ["only_line"]

    def test_empty_lines_returns_empty(self) -> None:
        ctx = _extract_context_lines([], line_start=1, line_end=1)
        assert ctx == []

    def test_multiline_match_context(self) -> None:
        lines = ["a", "b", "c", "d", "e", "f", "g"]
        ctx = _extract_context_lines(lines, line_start=3, line_end=5, context=1)
        # Lines 3-5 are 'c','d','e'; with context=1: lines 2-6 ('b'..'f')
        assert "b" in ctx
        assert "f" in ctx


# ---------------------------------------------------------------------------
# ScanMatch dataclass
# ---------------------------------------------------------------------------


class TestScanMatch:
    """Tests for the ScanMatch dataclass and its properties."""

    def _make_match(
        self,
        line_start: int = 5,
        line_end: int = 5,
        severity: Severity = Severity.CRITICAL,
        source: str = "body",
        confidence: float = 0.99,
    ) -> ScanMatch:
        pattern = get_pattern_by_name("copilot_agent_tips_block")
        assert pattern is not None
        return ScanMatch(
            pattern_name=pattern.name,
            pattern=pattern,
            matched_text="START COPILOT CODING AGENT TIPS\nEND COPILOT CODING AGENT TIPS",
            line_start=line_start,
            line_end=line_end,
            char_start=10,
            char_end=70,
            severity=severity,
            confidence=confidence,
            source=source,
        )

    def test_is_multiline_true(self) -> None:
        m = self._make_match(line_start=5, line_end=8)
        assert m.is_multiline is True

    def test_is_multiline_false_same_line(self) -> None:
        m = self._make_match(line_start=5, line_end=5)
        assert m.is_multiline is False

    def test_severity_label_critical(self) -> None:
        m = self._make_match(severity=Severity.CRITICAL)
        assert m.severity_label == "critical"

    def test_severity_label_high(self) -> None:
        m = self._make_match(severity=Severity.HIGH)
        assert m.severity_label == "high"

    def test_line_range_single_line(self) -> None:
        m = self._make_match(line_start=7, line_end=7)
        assert m.line_range == "7"

    def test_line_range_multiline(self) -> None:
        m = self._make_match(line_start=7, line_end=10)
        assert m.line_range == "7-10"

    def test_source_stored_correctly(self) -> None:
        m = self._make_match(source="comment")
        assert m.source == "comment"

    def test_to_dict_has_all_required_keys(self) -> None:
        m = self._make_match()
        d = m.to_dict()
        expected_keys = {
            "pattern_name",
            "matched_text",
            "line_start",
            "line_end",
            "char_start",
            "char_end",
            "severity",
            "confidence",
            "source",
            "category",
            "description",
            "context_lines",
        }
        assert expected_keys == set(d.keys())

    def test_to_dict_values_correct(self) -> None:
        m = self._make_match()
        d = m.to_dict()
        assert d["pattern_name"] == "copilot_agent_tips_block"
        assert d["severity"] == "critical"
        assert d["source"] == "body"
        assert d["category"] == "copilot"
        assert isinstance(d["confidence"], float)
        assert isinstance(d["context_lines"], list)

    def test_to_dict_confidence_rounded_to_4dp(self) -> None:
        m = self._make_match(confidence=0.123456789)
        d = m.to_dict()
        # Should be rounded to 4 decimal places.
        assert d["confidence"] == round(0.123456789, 4)

    def test_context_lines_default_empty(self) -> None:
        m = self._make_match()
        assert m.context_lines == []

    def test_to_dict_is_json_serialisable(self) -> None:
        m = self._make_match()
        # Should not raise.
        serialised = json.dumps(m.to_dict())
        parsed = json.loads(serialised)
        assert parsed["pattern_name"] == "copilot_agent_tips_block"


# ---------------------------------------------------------------------------
# Scanner construction
# ---------------------------------------------------------------------------


class TestScannerConstruction:
    """Tests for the various Scanner construction methods."""

    def test_default_scanner_has_all_builtin_patterns(self) -> None:
        scanner = Scanner.from_defaults()
        assert len(scanner.active_patterns) == len(BUILTIN_PATTERNS)

    def test_scanner_with_none_config_uses_defaults(self) -> None:
        scanner = Scanner(config=None)
        assert len(scanner.active_patterns) > 0

    def test_scanner_config_property(self) -> None:
        scanner = Scanner.from_defaults()
        from pr_clean.config import Config
        assert isinstance(scanner.config, Config)

    def test_scanner_from_config_sets_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "high"})
        scanner = Scanner.from_config(cfg)
        assert scanner.config.severity_threshold == Severity.HIGH

    def test_scanner_with_disabled_pattern_excludes_it(self) -> None:
        cfg = load_config_from_dict(
            {"disable_patterns": ["copilot_agent_tips_block"]}
        )
        scanner = Scanner.from_config(cfg)
        names = [p.name for p in scanner.active_patterns]
        assert "copilot_agent_tips_block" not in names

    def test_scanner_active_patterns_property(self) -> None:
        scanner = Scanner.from_defaults()
        patterns = scanner.active_patterns
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_scanner_from_config_class_method(self) -> None:
        cfg = load_config_from_dict({})
        scanner = Scanner.from_config(cfg)
        assert scanner.config is cfg


# ---------------------------------------------------------------------------
# Empty / trivial input
# ---------------------------------------------------------------------------


class TestScanEmptyInput:
    """Edge cases for empty or clean inputs."""

    def test_empty_string_returns_empty_list(self, default_scanner: Scanner) -> None:
        assert default_scanner.scan("") == []

    def test_whitespace_only_returns_empty_list(self, default_scanner: Scanner) -> None:
        matches = default_scanner.scan("   \n\n\t  ")
        assert matches == []

    def test_plain_human_text_returns_empty_list(self, default_scanner: Scanner) -> None:
        text = textwrap.dedent("""\
            # Fix login bug

            Changed the session timeout.

            ## Testing

            Run `pytest` to verify.
        """)
        matches = default_scanner.scan(text)
        assert matches == []

    def test_none_like_empty_string_returns_empty(self, default_scanner: Scanner) -> None:
        assert default_scanner.scan("") == []

    def test_newlines_only_returns_empty(self, default_scanner: Scanner) -> None:
        assert default_scanner.scan("\n\n\n") == []


# ---------------------------------------------------------------------------
# Copilot tips block detection
# ---------------------------------------------------------------------------


class TestCopilotTipsBlockDetection:
    """Tests for detection of the canonical Copilot tips block."""

    def test_detects_copilot_tips_block(self, default_scanner: Scanner, copilot_tips_block: str) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        pattern_names = [m.pattern_name for m in matches]
        assert "copilot_agent_tips_block" in pattern_names

    def test_match_severity_is_critical(self, default_scanner: Scanner, copilot_tips_block: str) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [m for m in matches if m.pattern_name == "copilot_agent_tips_block"]
        assert len(tips_matches) >= 1
        assert tips_matches[0].severity == Severity.CRITICAL

    def test_match_is_multiline(self, default_scanner: Scanner, copilot_tips_block: str) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [m for m in matches if m.pattern_name == "copilot_agent_tips_block"]
        assert tips_matches[0].is_multiline is True

    def test_matched_text_contains_start_marker(self, default_scanner: Scanner, copilot_tips_block: str) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [m for m in matches if m.pattern_name == "copilot_agent_tips_block"]
        assert "START COPILOT CODING AGENT TIPS" in tips_matches[0].matched_text.upper()

    def test_matched_text_contains_end_marker(self, default_scanner: Scanner, copilot_tips_block: str) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [m for m in matches if m.pattern_name == "copilot_agent_tips_block"]
        assert "END COPILOT CODING AGENT TIPS" in tips_matches[0].matched_text.upper()

    def test_case_insensitive_detection(self, default_scanner: Scanner) -> None:
        text = "start copilot coding agent tips\nsome tip\nend copilot coding agent tips"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_agent_tips_block" in names

    def test_line_numbers_are_correct(self, default_scanner: Scanner) -> None:
        text = "Line 1\nLine 2\nSTART COPILOT CODING AGENT TIPS\nTip\nEND COPILOT CODING AGENT TIPS\nLine 6"
        matches = default_scanner.scan(text)
        tips_matches = [m for m in matches if m.pattern_name == "copilot_agent_tips_block"]
        assert len(tips_matches) >= 1
        assert tips_matches[0].line_start == 3
        assert tips_matches[0].line_end == 5

    def test_confidence_is_high(self, default_scanner: Scanner, copilot_tips_block: str) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [m for m in matches if m.pattern_name == "copilot_agent_tips_block"]
        assert tips_matches[0].confidence >= 0.95

    def test_char_offsets_are_valid(self, default_scanner: Scanner, copilot_tips_block: str) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [m for m in matches if m.pattern_name == "copilot_agent_tips_block"]
        m = tips_matches[0]
        assert 0 <= m.char_start < len(copilot_tips_block)
        assert m.char_start < m.char_end
        assert m.char_end <= len(copilot_tips_block)


# ---------------------------------------------------------------------------
# Other built-in pattern detection
# ---------------------------------------------------------------------------


class TestBuiltinPatternDetection:
    """Tests that verify individual built-in patterns fire on known text."""

    def test_detects_copilot_generated_marker(self, default_scanner: Scanner) -> None:
        text = "Generated by GitHub Copilot\nSome other text."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_generated_marker" in names

    def test_detects_copilot_generated_marker_lowercase(self, default_scanner: Scanner) -> None:
        text = "generated by github copilot"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_generated_marker" in names

    def test_detects_ai_generated_disclaimer(self, default_scanner: Scanner) -> None:
        text = "This PR description was AI-generated. Please review."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "ai_generated_disclaimer" in names

    def test_detects_auto_generated_disclaimer(self, default_scanner: Scanner) -> None:
        text = "auto-generated content below"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "ai_generated_disclaimer" in names

    def test_detects_promotional_powered_by(self, default_scanner: Scanner) -> None:
        text = "Powered by GitHub Copilot"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "promotional_powered_by" in names

    def test_detects_promotional_upgrade_cta(self, default_scanner: Scanner) -> None:
        text = "Upgrade to Pro today!"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "promotional_upgrade_cta" in names

    def test_detects_promotional_learn_more(self, default_scanner: Scanner) -> None:
        text = "Learn more about GitHub Copilot features"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "promotional_learn_more_link" in names

    def test_detects_chatgpt_response_marker(self, default_scanner: Scanner) -> None:
        text = "As an AI language model, I cannot guarantee this is correct."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "chatgpt_response_marker" in names

    def test_detects_chatgpt_i_am_an_ai(self, default_scanner: Scanner) -> None:
        text = "I am an AI assistant and cannot do that."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "chatgpt_response_marker" in names

    def test_detects_copilot_summary_header(self, default_scanner: Scanner) -> None:
        text = "## Copilot Summary\n\nSome summary text here."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_summary_header" in names

    def test_detects_copilot_analysis_header(self, default_scanner: Scanner) -> None:
        text = "## Copilot Analysis\n\nSome analysis."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_summary_header" in names

    def test_detects_github_actions_bot_footer(self, default_scanner: Scanner) -> None:
        text = "<sub>posted by GitHub Actions</sub>"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "github_actions_bot_footer" in names

    def test_detects_do_not_edit_banner(self, default_scanner: Scanner) -> None:
        text = "DO NOT EDIT: auto-generated section"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "bot_do_not_edit_comment" in names

    def test_detects_do_not_edit_automatically(self, default_scanner: Scanner) -> None:
        text = "do not edit - automatically generated"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "bot_do_not_edit_comment" in names

    def test_detects_coderabbit_block(self, default_scanner: Scanner) -> None:
        text = "<!-- coderabbit ai review summary -->\nLooks good!"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "coderabbit_review_block" in names

    def test_detects_copilot_inline_suggestion(self, default_scanner: Scanner) -> None:
        text = "<!-- copilot_suggestion: consider splitting this -->"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_inline_suggestion_marker" in names

    def test_detects_unsolicited_tips_header(self, default_scanner: Scanner) -> None:
        text = "### Tips and Tricks\n\n- Squash before merging."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "unsolicited_tips_header" in names

    def test_detects_auto_summary_section(self, default_scanner: Scanner) -> None:
        text = "## Auto-generated Summary\n\nSome content."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "auto_summary_section" in names

    def test_detects_llm_agent_start_marker(self, default_scanner: Scanner) -> None:
        text = "START AGENT OUTPUT\nsome content\nEND AGENT OUTPUT"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "llm_agent_start_marker" in names

    def test_detects_copilot_workspace_block(self, default_scanner: Scanner) -> None:
        text = "copilot_workspace_session: active"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_workspace_block" in names


# ---------------------------------------------------------------------------
# Sample fixture
# ---------------------------------------------------------------------------


class TestSampleFixture:
    """Scan the full sample fixture and verify expected patterns are found."""

    def test_fixture_file_exists(self) -> None:
        assert FIXTURE_PATH.exists(), f"Fixture not found at {FIXTURE_PATH}"

    def test_scan_fixture_returns_multiple_matches(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        assert len(matches) >= 5, (
            f"Expected at least 5 matches in the sample fixture, got {len(matches)}"
        )

    def test_copilot_tips_block_found_in_fixture(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        names = [m.pattern_name for m in matches]
        assert "copilot_agent_tips_block" in names

    def test_all_matches_have_valid_line_numbers(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        total_lines = sample_text.count("\n") + 1
        for m in matches:
            assert 1 <= m.line_start <= total_lines, (
                f"line_start {m.line_start} out of range for pattern {m.pattern_name}"
            )
            assert m.line_start <= m.line_end, (
                f"line_start > line_end for pattern {m.pattern_name}"
            )

    def test_all_matches_have_valid_char_offsets(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        for m in matches:
            assert 0 <= m.char_start < len(sample_text), (
                f"char_start {m.char_start} out of range"
            )
            assert m.char_start < m.char_end, (
                f"char_start >= char_end for pattern {m.pattern_name}"
            )

    def test_matches_sorted_by_char_start(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        starts = [m.char_start for m in matches]
        assert starts == sorted(starts), "Matches are not sorted by char_start"

    def test_all_matches_have_confidence_in_range(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        for m in matches:
            assert 0.0 <= m.confidence <= 1.0, (
                f"Confidence {m.confidence} out of [0, 1] for {m.pattern_name}"
            )

    def test_source_label_propagated(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text, source="body")
        for m in matches:
            assert m.source == "body"

    def test_all_matches_have_non_empty_pattern_name(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        for m in matches:
            assert m.pattern_name, f"Empty pattern_name found: {m}"

    def test_all_matches_have_non_empty_matched_text(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        for m in matches:
            assert m.matched_text, f"Empty matched_text found for {m.pattern_name}"

    def test_multiple_patterns_fire_in_fixture(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        matches = default_scanner.scan(sample_text)
        unique_patterns = {m.pattern_name for m in matches}
        assert len(unique_patterns) >= 3, (
            f"Expected at least 3 unique patterns, got: {unique_patterns}"
        )


# ---------------------------------------------------------------------------
# Severity threshold filtering
# ---------------------------------------------------------------------------


class TestSeverityFiltering:
    """Tests that severity threshold filtering works correctly."""

    def test_critical_threshold_returns_only_critical(
        self, sample_text: str
    ) -> None:
        cfg = load_config_from_dict({"severity_threshold": "critical"})
        scanner = Scanner.from_config(cfg)
        matches = scanner.scan(sample_text)
        for m in matches:
            assert m.severity == Severity.CRITICAL

    def test_high_threshold_returns_high_and_critical(
        self, sample_text: str
    ) -> None:
        cfg = load_config_from_dict({"severity_threshold": "high"})
        scanner = Scanner.from_config(cfg)
        matches = scanner.scan(sample_text)
        for m in matches:
            assert m.severity in {Severity.HIGH, Severity.CRITICAL}

    def test_medium_threshold_excludes_low(
        self, sample_text: str
    ) -> None:
        cfg = load_config_from_dict({"severity_threshold": "medium"})
        scanner = Scanner.from_config(cfg)
        matches = scanner.scan(sample_text)
        for m in matches:
            assert m.severity != Severity.LOW

    def test_low_threshold_returns_more_than_high(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        cfg_high = load_config_from_dict({"severity_threshold": "high"})
        scanner_high = Scanner.from_config(cfg_high)
        all_matches = default_scanner.scan(sample_text)
        high_matches = scanner_high.scan(sample_text)
        assert len(all_matches) >= len(high_matches)

    def test_critical_threshold_text_with_no_critical(
        self, default_scanner: Scanner
    ) -> None:
        # Text with only medium-severity matches.
        text = "Powered by GitHub Copilot"  # promotional_powered_by = MEDIUM
        cfg = load_config_from_dict({"severity_threshold": "critical"})
        scanner = Scanner.from_config(cfg)
        matches = scanner.scan(text)
        # Should be empty since there's no CRITICAL match.
        for m in matches:
            assert m.severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# scan_multiple
# ---------------------------------------------------------------------------


class TestScanMultiple:
    """Tests for the scan_multiple batch scanning method."""

    def test_scan_multiple_empty_list(self, default_scanner: Scanner) -> None:
        assert default_scanner.scan_multiple([]) == []

    def test_scan_multiple_returns_flat_list(
        self, default_scanner: Scanner, copilot_tips_block: str
    ) -> None:
        comments = [
            copilot_tips_block,
            "Powered by GitHub Copilot",
            "This is a clean comment.",
        ]
        matches = default_scanner.scan_multiple(comments, source="comment")
        assert len(matches) >= 2

    def test_scan_multiple_source_label(
        self, default_scanner: Scanner, copilot_tips_block: str
    ) -> None:
        matches = default_scanner.scan_multiple(
            [copilot_tips_block], source="comment"
        )
        for m in matches:
            assert m.source == "comment"

    def test_scan_multiple_with_all_clean_texts(
        self, default_scanner: Scanner
    ) -> None:
        texts = [
            "Fixed the login bug.",
            "Added unit tests.",
            "Updated documentation.",
        ]
        matches = default_scanner.scan_multiple(texts)
        assert matches == []

    def test_scan_multiple_default_source_is_comment(
        self, default_scanner: Scanner, copilot_tips_block: str
    ) -> None:
        matches = default_scanner.scan_multiple([copilot_tips_block])
        for m in matches:
            assert m.source == "comment"

    def test_scan_multiple_multiple_injected_texts(
        self, default_scanner: Scanner
    ) -> None:
        texts = [
            "Generated by GitHub Copilot",
            "Powered by GitHub Copilot",
            "As an AI language model, I cannot help with that.",
        ]
        matches = default_scanner.scan_multiple(texts, source="comment")
        assert len(matches) >= 3

    def test_scan_multiple_with_empty_strings_in_list(
        self, default_scanner: Scanner
    ) -> None:
        texts = ["", "", "Generated by GitHub Copilot", ""]
        matches = default_scanner.scan_multiple(texts, source="comment")
        assert len(matches) >= 1


# ---------------------------------------------------------------------------
# Custom patterns via config
# ---------------------------------------------------------------------------


class TestCustomPatternScanning:
    """Tests that user-defined custom patterns work correctly with the scanner."""

    def test_custom_pattern_fires(self) -> None:
        cfg = load_config_from_dict(
            {
                "patterns": [
                    {
                        "name": "my_secret_block",
                        "regex": r"SECRET_INJECTION_MARKER",
                        "severity": "high",
                        "confidence": 0.95,
                    }
                ]
            }
        )
        scanner = Scanner.from_config(cfg)
        text = "Some intro\nSECRET_INJECTION_MARKER\nSome outro"
        matches = scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "my_secret_block" in names

    def test_custom_pattern_does_not_fire_on_clean_text(self) -> None:
        cfg = load_config_from_dict(
            {
                "patterns": [
                    {
                        "name": "my_secret_block",
                        "regex": r"SECRET_INJECTION_MARKER",
                        "severity": "high",
                        "confidence": 0.95,
                    }
                ]
            }
        )
        scanner = Scanner.from_config(cfg)
        text = "Nothing suspicious here."
        matches = scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "my_secret_block" not in names

    def test_custom_pattern_severity_stored_correctly(self) -> None:
        cfg = load_config_from_dict(
            {
                "patterns": [
                    {
                        "name": "low_noise",
                        "regex": r"MAYBE_NOISE",
                        "severity": "low",
                        "confidence": 0.60,
                    }
                ]
            }
        )
        scanner = Scanner.from_config(cfg)
        text = "MAYBE_NOISE detected here"
        matches = scanner.scan(text)
        noise_matches = [m for m in matches if m.pattern_name == "low_noise"]
        assert len(noise_matches) >= 1
        assert noise_matches[0].severity == Severity.LOW

    def test_custom_pattern_confidence_stored(self) -> None:
        cfg = load_config_from_dict(
            {
                "patterns": [
                    {
                        "name": "my_pattern",
                        "regex": r"CUSTOM_MARKER",
                        "severity": "medium",
                        "confidence": 0.70,
                    }
                ]
            }
        )
        scanner = Scanner.from_config(cfg)
        text = "CUSTOM_MARKER found here"
        matches = scanner.scan(text)
        custom_matches = [m for m in matches if m.pattern_name == "my_pattern"]
        assert len(custom_matches) >= 1
        # Confidence may be adjusted slightly by heuristics.
        assert 0.0 <= custom_matches[0].confidence <= 1.0

    def test_custom_pattern_filtered_by_severity_threshold(self) -> None:
        cfg = load_config_from_dict(
            {
                "severity_threshold": "high",
                "patterns": [
                    {
                        "name": "low_custom",
                        "regex": r"LOW_NOISE_MARKER",
                        "severity": "low",
                        "confidence": 0.60,
                    }
                ],
            }
        )
        scanner = Scanner.from_config(cfg)
        text = "LOW_NOISE_MARKER here"
        matches = scanner.scan(text)
        names = [m.pattern_name for m in matches]
        # Low-severity pattern should be filtered out by the HIGH threshold.
        assert "low_custom" not in names

    def test_custom_pattern_with_multiline_regex(self) -> None:
        cfg = load_config_from_dict(
            {
                "patterns": [
                    {
                        "name": "multiline_block",
                        "regex": r"BEGIN BLOCK.*?END BLOCK",
                        "severity": "high",
                        "confidence": 0.90,
                    }
                ]
            }
        )
        scanner = Scanner.from_config(cfg)
        text = "Intro.\nBEGIN BLOCK\ncontent\nEND BLOCK\nOutro."
        matches = scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "multiline_block" in names


# ---------------------------------------------------------------------------
# Context lines
# ---------------------------------------------------------------------------


class TestContextLines:
    """Tests that context lines are correctly extracted for matches."""

    def test_context_lines_populated(self, default_scanner: Scanner) -> None:
        text = (
            "Line 1\n"
            "Line 2\n"
            "Generated by GitHub Copilot\n"
            "Line 4\n"
            "Line 5\n"
        )
        matches = default_scanner.scan(text)
        gen_matches = [
            m for m in matches if m.pattern_name == "copilot_generated_marker"
        ]
        assert len(gen_matches) >= 1
        assert len(gen_matches[0].context_lines) > 0

    def test_context_lines_include_match_line(self, default_scanner: Scanner) -> None:
        text = "A\nB\nGenerated by GitHub Copilot\nD\nE"
        matches = default_scanner.scan(text)
        gen_matches = [
            m for m in matches if m.pattern_name == "copilot_generated_marker"
        ]
        assert len(gen_matches) >= 1
        all_context = "\n".join(gen_matches[0].context_lines)
        assert "copilot" in all_context.lower()

    def test_context_lines_contain_adjacent_lines(self, default_scanner: Scanner) -> None:
        text = (
            "line before\n"
            "Generated by GitHub Copilot\n"
            "line after\n"
        )
        matches = default_scanner.scan(text)
        gen_matches = [m for m in matches if m.pattern_name == "copilot_generated_marker"]
        assert len(gen_matches) >= 1
        all_context = "\n".join(gen_matches[0].context_lines)
        # Should include the line before and/or after.
        assert "line before" in all_context or "line after" in all_context


# ---------------------------------------------------------------------------
# to_dict serialisation
# ---------------------------------------------------------------------------


class TestToDictSerialisation:
    """Tests for ScanMatch.to_dict() and JSON serialisation."""

    def test_to_dict_is_json_serialisable(self, default_scanner: Scanner) -> None:
        text = "Generated by GitHub Copilot"
        matches = default_scanner.scan(text)
        assert len(matches) >= 1
        # Should not raise.
        serialised = json.dumps([m.to_dict() for m in matches])
        parsed = json.loads(serialised)
        assert len(parsed) >= 1

    def test_to_dict_confidence_rounded(self, default_scanner: Scanner) -> None:
        text = "Generated by GitHub Copilot"
        matches = default_scanner.scan(text)
        d = matches[0].to_dict()
        # Confidence should be rounded to 4 decimal places.
        conf_str = str(d["confidence"])
        if "." in conf_str:
            decimal_places = len(conf_str.split(".")[1])
            assert decimal_places <= 4

    def test_scan_results_all_serialisable(self, default_scanner: Scanner, sample_text: str) -> None:
        matches = default_scanner.scan(sample_text)
        # All matches from the fixture should serialise without errors.
        payload = [m.to_dict() for m in matches]
        serialised = json.dumps(payload)
        parsed = json.loads(serialised)
        assert len(parsed) == len(matches)

    def test_to_dict_source_field(self, default_scanner: Scanner) -> None:
        text = "Generated by GitHub Copilot"
        matches = default_scanner.scan(text, source="pr_body")
        d = matches[0].to_dict()
        assert d["source"] == "pr_body"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Tests that overlapping matches from different patterns are deduplicated."""

    def test_no_duplicate_spans_in_output(self, default_scanner: Scanner, sample_text: str) -> None:
        matches = default_scanner.scan(sample_text)
        # No two matches should have the same (char_start, char_end).
        spans = [(m.char_start, m.char_end) for m in matches]
        assert len(spans) == len(set(spans)), "Duplicate character spans found in matches"

    def test_single_pattern_no_duplicates(self, default_scanner: Scanner) -> None:
        # A pattern that could theoretically match multiple times in the same text.
        text = "Generated by GitHub Copilot\nGenerated by GitHub Copilot"
        matches = default_scanner.scan(text)
        gen_matches = [m for m in matches if m.pattern_name == "copilot_generated_marker"]
        # Two separate occurrences should both appear (different spans).
        assert len(gen_matches) == 2
        spans = [(m.char_start, m.char_end) for m in gen_matches]
        assert len(spans) == len(set(spans))
