"""Unit tests for the pr_clean config loader (pr_clean/config.py).

Covers:

* :func:`~pr_clean.config._coerce_to_list` helper.
* :func:`~pr_clean.config._validate_output_format` helper.
* Default config values when no YAML is supplied.
* Severity threshold parsing and validation.
* Output format validation.
* Disabling built-in patterns.
* Custom pattern parsing and integration.
* Whitelist (repos and authors) configuration.
* Loading from disk (valid, empty, invalid, non-mapping YAML).
* :attr:`~pr_clean.config.Config.raw` preservation.
* :class:`~pr_clean.config.Config` helper methods.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from pr_clean.config import (
    Config,
    WhitelistConfig,
    _build_config,
    _coerce_to_list,
    _validate_output_format,
    load_config,
    load_config_from_dict,
)
from pr_clean.patterns import BUILTIN_PATTERNS, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yaml(text: str) -> Dict[str, Any]:
    """Parse a dedented YAML string to a dict."""
    return yaml.safe_load(textwrap.dedent(text)) or {}


# ---------------------------------------------------------------------------
# _coerce_to_list
# ---------------------------------------------------------------------------


class TestCoerceToList:
    """Tests for the _coerce_to_list internal helper."""

    def test_none_returns_empty(self) -> None:
        assert _coerce_to_list(None) == []

    def test_empty_list_returns_empty(self) -> None:
        assert _coerce_to_list([]) == []

    def test_list_of_strings(self) -> None:
        assert _coerce_to_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_single_string_returns_single_element_list(self) -> None:
        assert _coerce_to_list("only_one") == ["only_one"]

    def test_list_of_integers_coerced_to_strings(self) -> None:
        result = _coerce_to_list([1, 2, 3])
        assert result == ["1", "2", "3"]

    def test_false_returns_empty(self) -> None:
        assert _coerce_to_list(False) == []

    def test_zero_returns_empty(self) -> None:
        # 0 is falsy so should return empty.
        assert _coerce_to_list(0) == []

    def test_empty_string_returns_empty(self) -> None:
        assert _coerce_to_list("") == []

    def test_list_with_mixed_types(self) -> None:
        result = _coerce_to_list(["a", 2, True])
        assert result == ["a", "2", "True"]


# ---------------------------------------------------------------------------
# _validate_output_format
# ---------------------------------------------------------------------------


class TestValidateOutputFormat:
    """Tests for the _validate_output_format helper."""

    def test_table_is_valid(self) -> None:
        assert _validate_output_format("table") == "table"

    def test_json_is_valid(self) -> None:
        assert _validate_output_format("json") == "json"

    def test_uppercase_table_normalised(self) -> None:
        assert _validate_output_format("TABLE") == "table"

    def test_uppercase_json_normalised(self) -> None:
        assert _validate_output_format("JSON") == "json"

    def test_mixed_case_normalised(self) -> None:
        assert _validate_output_format("Table") == "table"

    def test_invalid_format_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid output_format"):
            _validate_output_format("xml")

    def test_invalid_csv_raises(self) -> None:
        with pytest.raises(ValueError):
            _validate_output_format("csv")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            _validate_output_format("")


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    """Verify that an empty dict produces the expected default Config."""

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

    def test_disabled_pattern_names_is_empty(self) -> None:
        assert self.cfg.disabled_pattern_names == []

    def test_whitelist_repos_is_empty(self) -> None:
        assert self.cfg.whitelist.repos == []

    def test_whitelist_authors_is_empty(self) -> None:
        assert self.cfg.whitelist.authors == []

    def test_raw_is_empty_dict(self) -> None:
        assert self.cfg.raw == {}

    def test_active_patterns_is_list(self) -> None:
        assert isinstance(self.cfg.active_patterns, list)

    def test_whitelist_is_whitelist_config_instance(self) -> None:
        assert isinstance(self.cfg.whitelist, WhitelistConfig)


# ---------------------------------------------------------------------------
# Severity threshold
# ---------------------------------------------------------------------------


class TestSeverityThreshold:
    """Tests for severity_threshold parsing from user config."""

    def test_low_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "low"})
        assert cfg.severity_threshold == Severity.LOW

    def test_medium_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "medium"})
        assert cfg.severity_threshold == Severity.MEDIUM

    def test_high_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "high"})
        assert cfg.severity_threshold == Severity.HIGH

    def test_critical_threshold(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "critical"})
        assert cfg.severity_threshold == Severity.CRITICAL

    def test_uppercase_severity_accepted(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "HIGH"})
        assert cfg.severity_threshold == Severity.HIGH

    def test_mixed_case_severity_accepted(self) -> None:
        cfg = load_config_from_dict({"severity_threshold": "Medium"})
        assert cfg.severity_threshold == Severity.MEDIUM

    def test_invalid_severity_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid severity_threshold"):
            load_config_from_dict({"severity_threshold": "extreme"})

    def test_empty_severity_raises(self) -> None:
        with pytest.raises(ValueError):
            load_config_from_dict({"severity_threshold": ""})


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


class TestOutputFormat:
    """Tests for output_format config field."""

    def test_json_format(self) -> None:
        cfg = load_config_from_dict({"output_format": "json"})
        assert cfg.output_format == "json"

    def test_table_format(self) -> None:
        cfg = load_config_from_dict({"output_format": "table"})
        assert cfg.output_format == "table"

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError):
            load_config_from_dict({"output_format": "csv"})


# ---------------------------------------------------------------------------
# Boolean fields
# ---------------------------------------------------------------------------


class TestBooleanFields:
    """Tests for strip and fail_on_match boolean config fields."""

    def test_strip_true(self) -> None:
        cfg = load_config_from_dict({"strip": True})
        assert cfg.strip is True

    def test_strip_false(self) -> None:
        cfg = load_config_from_dict({"strip": False})
        assert cfg.strip is False

    def test_fail_on_match_false(self) -> None:
        cfg = load_config_from_dict({"fail_on_match": False})
        assert cfg.fail_on_match is False

    def test_fail_on_match_true(self) -> None:
        cfg = load_config_from_dict({"fail_on_match": True})
        assert cfg.fail_on_match is True


# ---------------------------------------------------------------------------
# Disable patterns
# ---------------------------------------------------------------------------


class TestDisablePatterns:
    """Tests for disable_patterns configuration."""

    def test_disable_single_builtin_removes_it(self) -> None:
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

    def test_disable_all_but_one(self) -> None:
        all_names = [p.name for p in BUILTIN_PATTERNS]
        keep = all_names[0]
        disable = all_names[1:]
        cfg = load_config_from_dict({"disable_patterns": disable})
        names = [p.name for p in cfg.active_patterns]
        assert keep in names
        for name in disable:
            assert name not in names

    def test_disable_unknown_name_does_not_crash(self) -> None:
        # Should silently ignore unknown names.
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

    def test_disabling_reduces_active_count(self) -> None:
        cfg_full = load_config_from_dict({})
        cfg_disabled = load_config_from_dict(
            {"disable_patterns": ["copilot_agent_tips_block"]}
        )
        assert len(cfg_disabled.active_patterns) == len(cfg_full.active_patterns) - 1

    def test_disable_as_single_string(self) -> None:
        # Some users may supply a scalar instead of a list in YAML.
        cfg = load_config_from_dict(
            {"disable_patterns": "copilot_agent_tips_block"}
        )
        names = [p.name for p in cfg.active_patterns]
        assert "copilot_agent_tips_block" not in names


# ---------------------------------------------------------------------------
# Custom patterns
# ---------------------------------------------------------------------------


class TestCustomPatterns:
    """Tests for user-defined custom pattern parsing."""

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

    def test_custom_pattern_regex_compiles_and_matches(self) -> None:
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
        names = [p.name for p in cfg.active_patterns]
        assert "custom_pattern_0" in names

    def test_multiple_custom_patterns_default_names_indexed(self) -> None:
        data = {
            "patterns": [
                {"regex": r"PATTERN_A"},
                {"regex": r"PATTERN_B"},
            ]
        }
        cfg = load_config_from_dict(data)
        names = [p.name for p in cfg.active_patterns]
        assert "custom_pattern_0" in names
        assert "custom_pattern_1" in names

    def test_custom_pattern_missing_regex_raises(self) -> None:
        data = {"patterns": [{"name": "no_regex", "description": "oops"}]}
        with pytest.raises(ValueError, match="missing a 'regex' field"):
            load_config_from_dict(data)

    def test_custom_pattern_invalid_regex_raises(self) -> None:
        data = {"patterns": [{"name": "bad_regex", "regex": r"["}]}
        with pytest.raises(re.error):
            load_config_from_dict(data)

    def test_patterns_not_a_list_raises(self) -> None:
        with pytest.raises(ValueError, match="'patterns' must be a YAML list"):
            load_config_from_dict({"patterns": "not_a_list"})

    def test_custom_patterns_come_after_builtins(self) -> None:
        data = {"patterns": [{"name": "last_one", "regex": r"LAST"}]}
        cfg = load_config_from_dict(data)
        assert cfg.active_patterns[-1].name == "last_one"

    def test_custom_pattern_uses_alternative_pattern_field(self) -> None:
        # 'pattern' should be accepted as an alias for 'regex'.
        data = {"patterns": [{"name": "alt_field", "pattern": r"ALT_PATTERN"}]}
        cfg = load_config_from_dict(data)
        names = [p.name for p in cfg.active_patterns]
        assert "alt_field" in names

    def test_custom_pattern_severity_default_is_medium(self) -> None:
        data = {"patterns": [{"name": "default_sev", "regex": r"TEST"}]}
        cfg = load_config_from_dict(data)
        custom = next(p for p in cfg.active_patterns if p.name == "default_sev")
        assert custom.severity == Severity.MEDIUM

    def test_custom_pattern_confidence_default(self) -> None:
        data = {"patterns": [{"name": "default_conf", "regex": r"TEST"}]}
        cfg = load_config_from_dict(data)
        custom = next(p for p in cfg.active_patterns if p.name == "default_conf")
        assert custom.confidence == 0.75

    def test_custom_pattern_category_default(self) -> None:
        data = {"patterns": [{"name": "default_cat", "regex": r"TEST"}]}
        cfg = load_config_from_dict(data)
        custom = next(p for p in cfg.active_patterns if p.name == "default_cat")
        assert custom.category == "custom"

    def test_custom_pattern_not_dict_raises(self) -> None:
        data = {"patterns": ["this_is_a_string_not_a_dict"]}
        with pytest.raises(ValueError):
            load_config_from_dict(data)

    def test_custom_pattern_all_fields(self) -> None:
        data = {
            "patterns": [
                {
                    "name": "full_custom",
                    "regex": r"FULL_PATTERN",
                    "description": "A full custom pattern.",
                    "severity": "critical",
                    "confidence": 0.98,
                    "category": "my_category",
                    "strip_full_block": False,
                    "tags": ["tag1", "tag2"],
                }
            ]
        }
        cfg = load_config_from_dict(data)
        custom = next(p for p in cfg.active_patterns if p.name == "full_custom")
        assert custom.description == "A full custom pattern."
        assert custom.severity == Severity.CRITICAL
        assert custom.confidence == 0.98
        assert custom.category == "my_category"
        assert custom.strip_full_block is False
        assert custom.tags == ["tag1", "tag2"]


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------


class TestWhitelist:
    """Tests for whitelist configuration."""

    def test_repo_is_whitelisted(self) -> None:
        cfg = load_config_from_dict(
            {"whitelist": {"repos": ["my-org/sandbox"]}}
        )
        assert cfg.is_repo_whitelisted("my-org/sandbox") is True

    def test_non_whitelisted_repo(self) -> None:
        cfg = load_config_from_dict(
            {"whitelist": {"repos": ["my-org/sandbox"]}}
        )
        assert cfg.is_repo_whitelisted("my-org/production") is False

    def test_author_is_whitelisted(self) -> None:
        cfg = load_config_from_dict(
            {"whitelist": {"authors": ["dependabot[bot]", "renovate[bot]"]}}
        )
        assert cfg.is_author_whitelisted("dependabot[bot]") is True
        assert cfg.is_author_whitelisted("renovate[bot]") is True

    def test_non_whitelisted_author(self) -> None:
        cfg = load_config_from_dict(
            {"whitelist": {"authors": ["dependabot[bot]"]}}
        )
        assert cfg.is_author_whitelisted("alice") is False

    def test_whitelist_not_a_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="'whitelist' must be a YAML mapping"):
            load_config_from_dict({"whitelist": ["repo1"]})

    def test_empty_whitelist_both_lists_empty(self) -> None:
        cfg = load_config_from_dict({"whitelist": {}})
        assert cfg.whitelist.repos == []
        assert cfg.whitelist.authors == []

    def test_multiple_repos_whitelisted(self) -> None:
        repos = ["org/a", "org/b", "org/c"]
        cfg = load_config_from_dict({"whitelist": {"repos": repos}})
        for repo in repos:
            assert cfg.is_repo_whitelisted(repo)

    def test_whitelist_none_value_treated_as_empty(self) -> None:
        cfg = load_config_from_dict({"whitelist": {"repos": None, "authors": None}})
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

    def test_load_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yml"
        with pytest.raises(FileNotFoundError):
            load_config(config_path=missing)

    def test_load_empty_yaml_file_returns_defaults(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text("")
        cfg = load_config(config_path=config_file)
        assert cfg.severity_threshold == Severity.LOW
        assert len(cfg.active_patterns) == len(BUILTIN_PATTERNS)

    def test_load_invalid_yaml_raises_yaml_error(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text("{invalid: [yaml: content")
        with pytest.raises(yaml.YAMLError):
            load_config(config_path=config_file)

    def test_load_non_mapping_yaml_raises_type_error(self, tmp_path: Path) -> None:
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

    def test_load_config_with_disable_patterns_from_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text(
            textwrap.dedent("""\
            disable_patterns:
              - copilot_agent_tips_block
            """)
        )
        cfg = load_config(config_path=config_file)
        names = [p.name for p in cfg.active_patterns]
        assert "copilot_agent_tips_block" not in names

    def test_load_config_with_whitelist_from_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / ".pr_clean.yml"
        config_file.write_text(
            textwrap.dedent("""\
            whitelist:
              repos:
                - my-org/sandbox
              authors:
                - renovate[bot]
            """)
        )
        cfg = load_config(config_path=config_file)
        assert cfg.is_repo_whitelisted("my-org/sandbox")
        assert cfg.is_author_whitelisted("renovate[bot]")

    def test_load_config_no_search_parents_no_file(self, tmp_path: Path) -> None:
        # When search_parents=False and no config_path given, defaults are returned.
        import os
        original = os.getcwd()
        try:
            os.chdir(tmp_path)
            cfg = load_config(search_parents=False)
            assert cfg.severity_threshold == Severity.LOW
        finally:
            os.chdir(original)


# ---------------------------------------------------------------------------
# Config.raw preservation
# ---------------------------------------------------------------------------


class TestConfigRaw:
    """Tests that the raw input dict is preserved on the Config object."""

    def test_raw_is_preserved(self) -> None:
        data = {"severity_threshold": "high", "strip": True}
        cfg = load_config_from_dict(data)
        assert cfg.raw == data

    def test_raw_is_empty_dict_for_defaults(self) -> None:
        cfg = load_config_from_dict({})
        assert cfg.raw == {}

    def test_raw_includes_all_supplied_keys(self) -> None:
        data = {
            "severity_threshold": "medium",
            "output_format": "json",
            "strip": False,
            "fail_on_match": True,
        }
        cfg = load_config_from_dict(data)
        for key in data:
            assert key in cfg.raw


# ---------------------------------------------------------------------------
# Config helper methods
# ---------------------------------------------------------------------------


class TestConfigHelperMethods:
    """Tests for Config.is_repo_whitelisted and is_author_whitelisted."""

    def test_is_repo_whitelisted_true(self) -> None:
        cfg = load_config_from_dict({"whitelist": {"repos": ["org/repo"]}})
        assert cfg.is_repo_whitelisted("org/repo") is True

    def test_is_repo_whitelisted_false(self) -> None:
        cfg = load_config_from_dict({"whitelist": {"repos": ["org/repo"]}})
        assert cfg.is_repo_whitelisted("org/other") is False

    def test_is_author_whitelisted_true(self) -> None:
        cfg = load_config_from_dict({"whitelist": {"authors": ["bot[bot]"]}})
        assert cfg.is_author_whitelisted("bot[bot]") is True

    def test_is_author_whitelisted_false(self) -> None:
        cfg = load_config_from_dict({"whitelist": {"authors": ["bot[bot]"]}})
        assert cfg.is_author_whitelisted("human") is False

    def test_is_repo_whitelisted_empty_whitelist(self) -> None:
        cfg = load_config_from_dict({})
        assert cfg.is_repo_whitelisted("any/repo") is False

    def test_is_author_whitelisted_empty_whitelist(self) -> None:
        cfg = load_config_from_dict({})
        assert cfg.is_author_whitelisted("anyone") is False


# ---------------------------------------------------------------------------
# WhitelistConfig dataclass
# ---------------------------------------------------------------------------


class TestWhitelistConfig:
    """Tests for the WhitelistConfig dataclass."""

    def test_default_repos_empty(self) -> None:
        wl = WhitelistConfig()
        assert wl.repos == []

    def test_default_authors_empty(self) -> None:
        wl = WhitelistConfig()
        assert wl.authors == []

    def test_repos_set(self) -> None:
        wl = WhitelistConfig(repos=["org/a"])
        assert "org/a" in wl.repos

    def test_authors_set(self) -> None:
        wl = WhitelistConfig(authors=["alice"])
        assert "alice" in wl.authors


# ---------------------------------------------------------------------------
# load_config_from_dict
# ---------------------------------------------------------------------------


class TestLoadConfigFromDict:
    """Tests for the load_config_from_dict public API."""

    def test_returns_config_instance(self) -> None:
        cfg = load_config_from_dict({})
        assert isinstance(cfg, Config)

    def test_complex_config(self) -> None:
        data = {
            "severity_threshold": "medium",
            "output_format": "json",
            "strip": True,
            "fail_on_match": False,
            "disable_patterns": ["copilot_agent_tips_block"],
            "patterns": [
                {
                    "name": "my_pattern",
                    "regex": r"MY_PATTERN",
                    "severity": "high",
                }
            ],
            "whitelist": {
                "repos": ["org/sandbox"],
                "authors": ["bot[bot]"],
            },
        }
        cfg = load_config_from_dict(data)
        assert cfg.severity_threshold == Severity.MEDIUM
        assert cfg.output_format == "json"
        assert cfg.strip is True
        assert cfg.fail_on_match is False
        assert "copilot_agent_tips_block" not in [p.name for p in cfg.active_patterns]
        assert "my_pattern" in [p.name for p in cfg.active_patterns]
        assert cfg.is_repo_whitelisted("org/sandbox")
        assert cfg.is_author_whitelisted("bot[bot]")
