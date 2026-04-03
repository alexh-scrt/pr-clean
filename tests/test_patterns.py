"""Unit tests for the pr_clean pattern registry (pr_clean/patterns.py)."""

from __future__ import annotations

import re
from typing import List

import pytest

from pr_clean.patterns import (
    BUILTIN_PATTERNS,
    InjectionPattern,
    Severity,
    build_custom_pattern,
    get_pattern_by_name,
    get_patterns_by_category,
    get_patterns_by_severity,
)


# ---------------------------------------------------------------------------
# InjectionPattern dataclass
# ---------------------------------------------------------------------------


class TestInjectionPatternValidation:
    """Tests for InjectionPattern post-init validation."""

    def test_valid_pattern_creates_successfully(self) -> None:
        pattern = InjectionPattern(
            name="test_pattern",
            description="A test pattern.",
            regex=re.compile(r"test"),
            severity=Severity.MEDIUM,
            confidence=0.80,
            category="test",
        )
        assert pattern.name == "test_pattern"
        assert pattern.confidence == 0.80

    def test_empty_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="name must not be empty"):
            InjectionPattern(
                name="",
                description="bad",
                regex=re.compile(r"x"),
                severity=Severity.LOW,
                confidence=0.5,
                category="test",
            )

    def test_confidence_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            InjectionPattern(
                name="p",
                description="d",
                regex=re.compile(r"x"),
                severity=Severity.LOW,
                confidence=1.1,
                category="test",
            )

    def test_confidence_below_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            InjectionPattern(
                name="p",
                description="d",
                regex=re.compile(r"x"),
                severity=Severity.LOW,
                confidence=-0.01,
                category="test",
            )

    def test_confidence_at_boundaries_is_valid(self) -> None:
        for c in (0.0, 1.0):
            p = InjectionPattern(
                name="p",
                description="d",
                regex=re.compile(r"x"),
                severity=Severity.LOW,
                confidence=c,
                category="test",
            )
            assert p.confidence == c

    def test_default_tags_is_empty_list(self) -> None:
        p = InjectionPattern(
            name="p",
            description="d",
            regex=re.compile(r"x"),
            severity=Severity.LOW,
            confidence=0.5,
            category="test",
        )
        assert p.tags == []

    def test_strip_full_block_defaults_to_true(self) -> None:
        p = InjectionPattern(
            name="p",
            description="d",
            regex=re.compile(r"x"),
            severity=Severity.LOW,
            confidence=0.5,
            category="test",
        )
        assert p.strip_full_block is True


# ---------------------------------------------------------------------------
# BUILTIN_PATTERNS list
# ---------------------------------------------------------------------------


class TestBuiltinPatterns:
    """Tests that verify the shape and correctness of the built-in registry."""

    def test_builtin_patterns_is_non_empty(self) -> None:
        assert len(BUILTIN_PATTERNS) > 0

    def test_all_names_are_unique(self) -> None:
        names = [p.name for p in BUILTIN_PATTERNS]
        assert len(names) == len(set(names)), "Duplicate pattern names detected."

    def test_all_patterns_have_valid_severity(self) -> None:
        for p in BUILTIN_PATTERNS:
            assert isinstance(p.severity, Severity)

    def test_all_patterns_have_compiled_regex(self) -> None:
        for p in BUILTIN_PATTERNS:
            assert isinstance(p.regex, re.Pattern)

    def test_copilot_agent_tips_block_matches_known_text(self) -> None:
        pattern = get_pattern_by_name("copilot_agent_tips_block")
        assert pattern is not None
        sample = (
            "Some text before\n"
            "START COPILOT CODING AGENT TIPS\n"
            "  - Tip one\n"
            "  - Tip two\n"
            "END COPILOT CODING AGENT TIPS\n"
            "Some text after"
        )
        assert pattern.regex.search(sample) is not None

    def test_copilot_agent_tips_block_is_case_insensitive(self) -> None:
        pattern = get_pattern_by_name("copilot_agent_tips_block")
        assert pattern is not None
        sample = "start copilot coding agent tips\nstuff\nend copilot coding agent tips"
        assert pattern.regex.search(sample) is not None

    def test_copilot_tips_header_matches_standalone_line(self) -> None:
        pattern = get_pattern_by_name("copilot_tips_header")
        assert pattern is not None
        assert pattern.regex.search("COPILOT CODING AGENT TIPS") is not None

    def test_copilot_generated_marker_matches(self) -> None:
        pattern = get_pattern_by_name("copilot_generated_marker")
        assert pattern is not None
        assert pattern.regex.search("Generated by GitHub Copilot") is not None
        assert pattern.regex.search("generated by github copilot") is not None

    def test_promotional_upgrade_cta_matches(self) -> None:
        pattern = get_pattern_by_name("promotional_upgrade_cta")
        assert pattern is not None
        assert pattern.regex.search("Upgrade to Pro today!") is not None
        assert pattern.regex.search("Try Enterprise for free") is not None

    def test_ai_generated_disclaimer_matches(self) -> None:
        pattern = get_pattern_by_name("ai_generated_disclaimer")
        assert pattern is not None
        assert pattern.regex.search("This PR description was AI-generated") is not None
        assert pattern.regex.search("auto-generated content") is not None

    def test_chatgpt_response_marker_matches(self) -> None:
        pattern = get_pattern_by_name("chatgpt_response_marker")
        assert pattern is not None
        assert pattern.regex.search("As an AI language model, I cannot") is not None
        assert pattern.regex.search("I am an AI assistant") is not None

    def test_bot_do_not_edit_comment_matches(self) -> None:
        pattern = get_pattern_by_name("bot_do_not_edit_comment")
        assert pattern is not None
        assert pattern.regex.search("DO NOT EDIT: auto-generated") is not None
        assert pattern.regex.search("do not edit - automatically generated") is not None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestGetPatternByName:
    """Tests for get_pattern_by_name."""

    def test_returns_correct_pattern(self) -> None:
        p = get_pattern_by_name("copilot_agent_tips_block")
        assert p is not None
        assert p.name == "copilot_agent_tips_block"

    def test_returns_none_for_unknown_name(self) -> None:
        p = get_pattern_by_name("nonexistent_pattern_xyz")
        assert p is None


class TestGetPatternsByCategory:
    """Tests for get_patterns_by_category."""

    def test_returns_copilot_patterns(self) -> None:
        patterns = get_patterns_by_category("copilot")
        assert len(patterns) > 0
        for p in patterns:
            assert p.category == "copilot"

    def test_returns_empty_for_unknown_category(self) -> None:
        patterns = get_patterns_by_category("this_category_does_not_exist")
        assert patterns == []

    def test_promotional_category(self) -> None:
        patterns = get_patterns_by_category("promotional")
        assert len(patterns) > 0


class TestGetPatternsBySeverity:
    """Tests for get_patterns_by_severity."""

    def test_low_threshold_returns_all(self) -> None:
        patterns = get_patterns_by_severity(Severity.LOW)
        assert len(patterns) == len(BUILTIN_PATTERNS)

    def test_critical_threshold_returns_only_critical(self) -> None:
        patterns = get_patterns_by_severity(Severity.CRITICAL)
        for p in patterns:
            assert p.severity == Severity.CRITICAL
        assert len(patterns) > 0

    def test_high_threshold_excludes_low_and_medium(self) -> None:
        patterns = get_patterns_by_severity(Severity.HIGH)
        for p in patterns:
            assert p.severity in {Severity.HIGH, Severity.CRITICAL}

    def test_medium_threshold_excludes_low(self) -> None:
        patterns = get_patterns_by_severity(Severity.MEDIUM)
        for p in patterns:
            assert p.severity in {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}


# ---------------------------------------------------------------------------
# build_custom_pattern
# ---------------------------------------------------------------------------


class TestBuildCustomPattern:
    """Tests for the build_custom_pattern factory."""

    def test_basic_custom_pattern(self) -> None:
        p = build_custom_pattern(
            name="my_pattern",
            pattern_str=r"SECRET\s+BLOCK",
            description="My custom pattern.",
            severity="high",
            confidence=0.90,
        )
        assert p.name == "my_pattern"
        assert p.severity == Severity.HIGH
        assert p.confidence == 0.90
        assert p.regex.search("SECRET BLOCK detected") is not None

    def test_invalid_severity_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid severity"):
            build_custom_pattern(
                name="p",
                pattern_str=r"test",
                severity="ultra",
            )

    def test_invalid_regex_raises(self) -> None:
        with pytest.raises(re.error):
            build_custom_pattern(
                name="p",
                pattern_str=r"[",  # unterminated character class
            )

    def test_default_category_is_custom(self) -> None:
        p = build_custom_pattern(name="p", pattern_str=r"test")
        assert p.category == "custom"

    def test_tags_are_preserved(self) -> None:
        p = build_custom_pattern(
            name="p",
            pattern_str=r"test",
            tags=["foo", "bar"],
        )
        assert p.tags == ["foo", "bar"]

    def test_all_severity_levels_accepted(self) -> None:
        for sev in ["low", "medium", "high", "critical"]:
            p = build_custom_pattern(name="p", pattern_str=r"test", severity=sev)
            assert p.severity.value == sev
