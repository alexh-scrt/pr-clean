"""Unit tests for the pr_clean config loader (pr_clean/config.py)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from pr_clean.config import (
    Config,
    WhitelistConfig,
    load_config,
    load_config_from_dict,
    _build_config,
    _coerce_to_list,
    _validate_output_format,
)
from pr_clean.patterns import BUILTIN_PATTERNS, Severity


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def yaml_to_dict(yaml_str: str) -> Dict[str, Any]:
    """Parse a YAML string to a dict."""
    return yaml.safe_load(textwrap.dedent(yaml_str)) or {}


# ---------------------------------------------------------------------------
# _coerce_to_list
# ---------------------------------------------------------------------------


class TestCoerceToList:
    def test_none_returns_empty(self) -> None:
        assert _coerce_to_list(None) == []

    def test_empty_list_returns_empty(self) -> None:
        assert _coerce_to_list([]) == []

    def test_list_of_strings(self) -> None:
        assert _coerce_to_list(["a", "b"]) == ["a", "b"]

    def test_single_string_returns_list(self) -> None:
        assert _coerce_to_list("only_one") == ["only_one"]

    def test_list_of_mixed_types_coerced(self) -> None:
        result = _coerce_to_list([1, 2, 3])
        assert result == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# _validate_output_format
# ---------------------------------------------------------------------------


class TestValidateOutputFormat:
    def test_table_is_valid(self) -> None:
        assert _validate_output_format("table") == "table"

    def test_json_is_valid(self) -> None:
        assert _validate_output_format("json") == "json"

    def test_case_insensitive(self) -> None:
        assert _validate_output_format("TABLE") == "table"
        assert _validate_output_format("JSON") == "json"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid output_format"):
            _validate_output_format("xml")


# ---------------------------------------------------------------------------
# Default config (empty dict)
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    """Verify that an empty config dict yields sensible defaults."""

    def setup_method(self) -> None:
        self.cfg = load_config_from_dict({})

    def test_severity_threshold_is_low(self) -> None:
        assert self.cfg.severity_threshold == Severity.LOW

    def test_output_format_is_table(self) -> None:
        assert self.cfg.output_format == "table"

    def test_strip_is_false(self) -> None:
        assert self.cfg.strip is False

    def test_fail_on_match_is_true(self) -> None:
        assert self.cfg.fail_on_match is True

    def test_all_builtin_patterns_active(self) -> None:
        assert len(self.cfg.active_patterns) == len(BUILTIN_PATTERNS)

    def test_disabled_pattern_names_empty(self) -> None:
        assert self.cfg.disabled_pattern_names == []

    def test_whitelist_is_empty(self) -> None:
        assert self.cfg.whitelist.repos == []
        assert self.cfg.whitelist.authors == []


# ---------------------------------------------------------------------------
# Severity threshold
# ---------------------------------------------------------------------------


class TestSeverityThreshold:
    def test_medium_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "medium"})
        assert cfg.severity_threshold == Severity.MEDIUM

    def test_high_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "high"})
        assert cfg.severity_threshold == Severity.HIGH

    def test_critical_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "critical"})
        assert cfg.severity_threshold == Severity.CRITICAL

    def test_case_insensitive(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "HIGH"})
        assert cfg.severity_threshold == Severity.HIGH

    def test_invalid_severity_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid severity_threshold"):
            load_config_from_dict({"severity_threshold": "extreme"})


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


class TestOutputFormat:
    def test_json_format(self) -> None:
        cfg = load_config_from_dict({"output_format": "json"})
        assert cfg.output_format == "json"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError):
            load_config_from_dict({"output_format": "csv"})


# ---------------------------------------------------------------------------
# Disable patterns
# ---------------------------------------------------------------------------


class TestDisablePatterns:
    def test_disable_single_builtin(self) -> None:
        cfg = load_config_from_dict(
            {"disable_patterns": ["copilot_agent_tips_block"]}
        )
        names = [p.name for p in cfg.active_patterns]
        assert "copilot_agent_tips_block" not in names

    def test_disable_multiple_builtins(self) -> None:
        to_disable = ["copilot_agent_tips_block", "copilot_tips_header"]
        cfg = load_config_from_dict({"disable_patterns": to_disable})
        names = [p.name for p in cfg.active_patterns]
        for name in to_disable:
            assert name not in names

    def test_disable_unknown_name_does_not_crash(self) -> None:
        # Should not raise; just ignores the unknown name.
        cfg = load_config_from_dict(
            {"disable_patterns": ["totally_unknown_pattern_xyz"]}
        )
        # All built-in patterns should still be active.
        assert len(cfg.active_patterns) == len(BUILTIN_PATTERNS)

    def test_disabled_names_stored_on_config(self) -> None:
        cfg = load_config_from_dict(
            {"disable_patterns": ["copilot_agent_tips_block"]}
        )
        assert "copilot_agent_tips_block" in cfg.disabled_pattern_names


# ---------------------------------------------------------------------------
# Custom patterns
# ---------------------------------------------------------------------------


class TestCustomPatterns:
    def test_single_custom_pattern_added(self) -> None:
        data = {
            "patterns": [
                {
                    "name": "my_custom",
                    "regex": r"MY\s+SECRET\s+BLOCK",
                    "severity": "high",
                    "confidence": 0.9,
                }
            ]
        }
        cfg = load_config_from_dict(data)
        names = [p.name for p in cfg.active_patterns]
        assert "my_custom" in names

    def test_custom_pattern_regex_compiles(self) -> None:
        data = {
            "patterns": [
                {
                    "name": "my_custom",
                    "regex": r"HELLO\s+WORLD",
                }
            ]
        }
        cfg = load_config_from_dict(data)
        custom = next(p for p in cfg.active_patterns if p.name == "my_custom")
        assert custom.regex.search("HELLO WORLD") is not None

    def test_custom_pattern_without_name_gets_default_name(self) -> None:
        data = {"patterns": [{"regex": r"SOME_PATTERN"}]}
        cfg = load_config_from_dict(data)
        # Should have generated name "custom_pattern_0"
        names = [p.name for p in cfg.active_patterns]
        assert "custom_pattern_0" in names

    def test_custom_pattern_missing_regex_raises(self) -> None:
        data = {"patterns": [{"name": "no_regex", "description": "oops"}]}
        with pytest.raises(ValueError, match="missing a 'regex' field"):
            load_config_from_dict(data)

    def test_custom_pattern_invalid_regex_raises(self) -> None:
        import re

        data = {"patterns": [{"name": "bad_regex", "regex": r"["}]}
        with pytest.raises(re.error):
            load_config_from_dict(data)

    def test_patterns_not_a_list_raises(self) -> None:
        with pytest.raises(ValueError, match="'patterns' must be a YAML list"):
            load_config_from_dict({"patterns": "not_a_list"})

    def test_custom_patterns_come_after_builtins(self) -> None:
        data = {
            "patterns": [
                {"name": "last_one", "regex": r"LAST"}
            ]
        }
        cfg = load_config_from_dict(data)
        # The last pattern should be our custom one.
        assert cfg.active_patterns[-1].name == "last_one"


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------


class TestWhitelist:
    def test_repo_whitelist(self) -> None:
        cfg = load_config_from_dict(
            {"whitelist": {"repos": ["my-org/sandbox"]}}
        )
        assert cfg.is_repo_whitelisted("my-org/sandbox")
        assert not cfg.is_repo_whitelisted("my-org/production")

    def test_author_whitelist(self) -> None:
        cfg = load_config_from_dict(
            {"whitelist": {"authors": ["dependabot[bot]", "renovate[bot]"]}}
        )
        assert cfg.is_author_whitelisted("dependabot[bot]")
        assert cfg.is_author_whitelisted("renovate[bot]")
        assert not cfg.is_author_whitelisted("alice")

    def test_whitelist_not_a_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="'whitelist' must be a YAML mapping"):
            load_config_from_dict({"whitelist": ["repo1"]})

    def test_empty_whitelist(self) -> None:
        cfg = load_config_from_dict({"whitelist": {}})
        assert cfg.whitelist.repos == []
        assert cfg.whitelist.authors == []


# ---------------------------------------------------------------------------
# load_config from file
# ---------------------------------------------------------------------------


class TestLoadConfigFromFile:
    """Integration tests that write temp YAML files and load them."""

    def test_load_valid_yaml_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text(
            textwrap.dedent("""\
            severity_threshold: high
            output_format: json
            strip: true
            fail_on_match: false
            """)
        )
        cfg = load_config(config_path=config_file)
        assert cfg.severity_threshold == Severity.HIGH
        assert cfg.output_format == "json"
        assert cfg.strip is True
        assert cfg.fail_on_match is False

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yml"
        with pytest.raises(FileNotFoundError):
            load_config(config_path=missing)

    def test_load_empty_yaml_file_returns_defaults(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text("")
        cfg = load_config(config_path=config_file)
        assert cfg.severity_threshold == Severity.LOW

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text("{invalid: [yaml: content")
        with pytest.raises(yaml.YAMLError):
            load_config(config_path=config_file)

    def test_load_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text("- item1\n- item2\n")
        with pytest.raises(TypeError, match="YAML mapping"):
            load_config(config_path=config_file)

    def test_load_config_with_custom_patterns_from_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text(
            textwrap.dedent("""\
            patterns:
              - name: file_custom
                regex: 'FILE_CUSTOM_PATTERN'
                severity: medium
                confidence: 0.88
            """)
        )
        cfg = load_config(config_path=config_file)
        names = [p.name for p in cfg.active_patterns]
        assert "file_custom" in names


# ---------------------------------------------------------------------------
# Config.raw
# ---------------------------------------------------------------------------


class TestConfigRaw:
    def test_raw_is_preserved(self) -> None:
        data = {"severity_threshold": "high", "strip": True}
        cfg = load_config_from_dict(data)
        assert cfg.raw == data

    def test_raw_is_empty_for_defaults(self) -> None:
        cfg = load_config_from_dict({})
        assert cfg.raw == {}
