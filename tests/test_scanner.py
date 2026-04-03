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

import textwrap
from pathlib import Path
from typing import List

import pytest

from pr_clean.config import load_config_from_dict
from pr_clean.patterns import InjectionPattern, Severity, get_pattern_by_name
from pr_clean.scanner import ScanMatch, Scanner, _char_offset_to_line_number, _adjust_confidence


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
    def test_offset_zero_is_line_one(self) -> None:
        assert _char_offset_to_line_number("hello\nworld", 0) == 1

    def test_offset_after_first_newline(self) -> None:
        text = "hello\nworld"
        # 'w' is at index 6
        assert _char_offset_to_line_number(text, 6) == 2

    def test_offset_at_newline_character(self) -> None:
        text = "hello\nworld"
        # '\n' is at index 5
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


# ---------------------------------------------------------------------------
# _adjust_confidence helper
# ---------------------------------------------------------------------------


class TestAdjustConfidence:
    def _make_pattern(self, confidence: float = 0.80) -> InjectionPattern:
        return get_pattern_by_name("copilot_agent_tips_block")  # type: ignore[return-value]

    def test_short_text_reduces_confidence(self) -> None:
        pattern = self._make_pattern()
        base = 0.80
        adjusted = _adjust_confidence(base, "short", pattern)
        assert adjusted < base

    def test_long_text_increases_confidence(self) -> None:
        pattern = self._make_pattern()
        base = 0.80
        long_text = "x" * 250
        adjusted = _adjust_confidence(base, long_text, pattern)
        assert adjusted > base

    def test_multiline_text_increases_confidence(self) -> None:
        pattern = self._make_pattern()
        base = 0.80
        adjusted = _adjust_confidence(base, "line one\nline two", pattern)
        assert adjusted > base

    def test_result_clamped_to_one(self) -> None:
        pattern = self._make_pattern()
        adjusted = _adjust_confidence(1.0, "x" * 300, pattern)
        assert adjusted <= 1.0

    def test_result_clamped_to_zero(self) -> None:
        pattern = self._make_pattern()
        adjusted = _adjust_confidence(0.0, "x", pattern)
        assert adjusted >= 0.0


# ---------------------------------------------------------------------------
# ScanMatch dataclass
# ---------------------------------------------------------------------------


class TestScanMatch:
    def _make_match(self, line_start: int = 5, line_end: int = 5) -> ScanMatch:
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
            severity=Severity.CRITICAL,
            confidence=0.99,
            source="body",
        )

    def test_is_multiline_true(self) -> None:
        m = self._make_match(line_start=5, line_end=8)
        assert m.is_multiline is True

    def test_is_multiline_false(self) -> None:
        m = self._make_match(line_start=5, line_end=5)
        assert m.is_multiline is False

    def test_severity_label(self) -> None:
        m = self._make_match()
        assert m.severity_label == "critical"

    def test_line_range_single_line(self) -> None:
        m = self._make_match(line_start=7, line_end=7)
        assert m.line_range == "7"

    def test_line_range_multiline(self) -> None:
        m = self._make_match(line_start=7, line_end=10)
        assert m.line_range == "7-10"

    def test_to_dict_keys(self) -> None:
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

    def test_to_dict_values(self) -> None:
        m = self._make_match()
        d = m.to_dict()
        assert d["pattern_name"] == "copilot_agent_tips_block"
        assert d["severity"] == "critical"
        assert d["source"] == "body"
        assert d["category"] == "copilot"
        assert isinstance(d["confidence"], float)


# ---------------------------------------------------------------------------
# Scanner construction
# ---------------------------------------------------------------------------


class TestScannerConstruction:
    def test_default_scanner_has_all_builtin_patterns(self) -> None:
        from pr_clean.patterns import BUILTIN_PATTERNS

        scanner = Scanner.from_defaults()
        assert len(scanner.active_patterns) == len(BUILTIN_PATTERNS)

    def test_scanner_with_none_config_uses_defaults(self) -> None:
        scanner = Scanner(config=None)
        assert len(scanner.active_patterns) > 0

    def test_scanner_from_config(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "high"})
        scanner = Scanner.from_config(cfg)
        assert scanner.config.severity_threshold == Severity.HIGH

    def test_scanner_with_disabled_pattern(self) -> None:
        cfg = load_config_from_dict(
            {"disable_patterns": ["copilot_agent_tips_block"]}
        )
        scanner = Scanner.from_config(cfg)
        names = [p.name for p in scanner.active_patterns]
        assert "copilot_agent_tips_block" not in names


# ---------------------------------------------------------------------------
# Empty / trivial input
# ---------------------------------------------------------------------------


class TestScanEmptyInput:
    def test_empty_string_returns_empty_list(self, default_scanner: Scanner) -> None:
        assert default_scanner.scan("") == []

    def test_whitespace_only_returns_empty_list(self, default_scanner: Scanner) -> None:
        # Whitespace-only text won't match any patterns.
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


# ---------------------------------------------------------------------------
# Copilot tips block detection
# ---------------------------------------------------------------------------


class TestCopilotTipsBlock:
    def test_detects_copilot_tips_block(
        self, default_scanner: Scanner, copilot_tips_block: str
    ) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        pattern_names = [m.pattern_name for m in matches]
        assert "copilot_agent_tips_block" in pattern_names

    def test_match_severity_is_critical(
        self, default_scanner: Scanner, copilot_tips_block: str
    ) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [
            m for m in matches if m.pattern_name == "copilot_agent_tips_block"
        ]
        assert len(tips_matches) >= 1
        assert tips_matches[0].severity == Severity.CRITICAL

    def test_match_is_multiline(
        self, default_scanner: Scanner, copilot_tips_block: str
    ) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [
            m for m in matches if m.pattern_name == "copilot_agent_tips_block"
        ]
        assert tips_matches[0].is_multiline is True

    def test_matched_text_contains_start_and_end(
        self, default_scanner: Scanner, copilot_tips_block: str
    ) -> None:
        matches = default_scanner.scan(copilot_tips_block)
        tips_matches = [
            m for m in matches if m.pattern_name == "copilot_agent_tips_block"
        ]
        text = tips_matches[0].matched_text
        assert "START COPILOT CODING AGENT TIPS" in text.upper()
        assert "END COPILOT CODING AGENT TIPS" in text.upper()

    def test_case_insensitive_detection(self, default_scanner: Scanner) -> None:
        text = "start copilot coding agent tips\nsome tip\nend copilot coding agent tips"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_agent_tips_block" in names

    def test_line_numbers_are_correct(self, default_scanner: Scanner) -> None:
        text = "Line 1\nLine 2\nSTART COPILOT CODING AGENT TIPS\nTip\nEND COPILOT CODING AGENT TIPS\nLine 6"
        matches = default_scanner.scan(text)
        tips_matches = [
            m for m in matches if m.pattern_name == "copilot_agent_tips_block"
        ]
        assert tips_matches[0].line_start == 3
        assert tips_matches[0].line_end == 5


# ---------------------------------------------------------------------------
# Other built-in pattern detection
# ---------------------------------------------------------------------------


class TestBuiltinPatternDetection:
    def test_detects_copilot_generated_marker(self, default_scanner: Scanner) -> None:
        text = "Generated by GitHub Copilot\nSome other text."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "copilot_generated_marker" in names

    def test_detects_ai_generated_disclaimer(self, default_scanner: Scanner) -> None:
        text = "This PR description was AI-generated. Please review."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "ai_generated_disclaimer" in names

    def test_detects_promotional_powered_by(self, default_scanner: Scanner) -> None:
        text = "Powered by GitHub Copilot"
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "promotional_powered_by" in names

    def test_detects_chatgpt_response_marker(self, default_scanner: Scanner) -> None:
        text = "As an AI language model, I cannot guarantee this is correct."
        matches = default_scanner.scan(text)
        names = [m.pattern_name for m in matches]
        assert "chatgpt_response_marker" in names

    def test_detects_copilot_summary_header(self, default_scanner: Scanner) -> None:
        text = "## Copilot Summary\n\nSome summary text here."
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


# ---------------------------------------------------------------------------
# Severity threshold filtering
# ---------------------------------------------------------------------------


class TestSeverityFiltering:
    def test_critical_threshold_hides_low_medium_high(
        self, sample_text: str
    ) -> None:
        cfg = load_config_from_dict({"severity_threshold": "critical"})
        scanner = Scanner.from_config(cfg)
        matches = scanner.scan(sample_text)
        for m in matches:
            assert m.severity == Severity.CRITICAL

    def test_high_threshold_hides_low_and_medium(
        self, sample_text: str
    ) -> None:
        cfg = load_config_from_dict({"severity_threshold": "high"})
        scanner = Scanner.from_config(cfg)
        matches = scanner.scan(sample_text)
        for m in matches:
            assert m.severity in {Severity.HIGH, Severity.CRITICAL}

    def test_low_threshold_returns_all_matches(
        self, default_scanner: Scanner, sample_text: str
    ) -> None:
        # LOW threshold (default) should return more matches than HIGH threshold.
        cfg_high = load_config_from_dict({"severity_threshold": "high"})
        scanner_high = Scanner.from_config(cfg_high)
        all_matches = default_scanner.scan(sample_text)
        high_matches = scanner_high.scan(sample_text)
        assert len(all_matches) >= len(high_matches)

    def test_medium_threshold_excludes_low_severity(self, sample_text: str) -> None:
        cfg = load_config_from_dict({"severity_threshold": "medium"})
        scanner = Scanner.from_config(cfg)
        matches = scanner.scan(sample_text)
        for m in matches:
            assert m.severity != Severity.LOW


# ---------------------------------------------------------------------------
# scan_multiple
# ---------------------------------------------------------------------------


class TestScanMultiple:
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


# ---------------------------------------------------------------------------
# Custom patterns via config
# ---------------------------------------------------------------------------


class TestCustomPatternScanning:
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
        assert noise_matches[0].severity == Severity.LOW


# ---------------------------------------------------------------------------
# Context lines
# ---------------------------------------------------------------------------


class TestContextLines:
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
        # Context lines should contain surrounding content.
        assert len(gen_matches[0].context_lines) > 0

    def test_context_lines_include_match_line(self, default_scanner: Scanner) -> None:
        text = "A\nB\nGenerated by GitHub Copilot\nD\nE"
        matches = default_scanner.scan(text)
        gen_matches = [
            m for m in matches if m.pattern_name == "copilot_generated_marker"
        ]
        # At least one context line should contain the matched text.
        all_context = "\n".join(gen_matches[0].context_lines)
        assert "Copilot" in all_context or "copilot" in all_context.lower()


# ---------------------------------------------------------------------------
# to_dict serialisation
# ---------------------------------------------------------------------------


class TestToDictSerialisation:
    def test_to_dict_is_json_serialisable(self, default_scanner: Scanner) -> None:
        import json

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
        assert len(str(d["confidence"]).split(".")[-1]) <= 4
