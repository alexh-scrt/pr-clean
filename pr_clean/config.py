"""Configuration loader for pr_clean.

This module is responsible for loading the default configuration, discovering
and parsing any user-supplied ``.pr_clean.yml`` file, merging the two, and
exposing the final resolved :class:`Config` object to the rest of the package.

Default behaviour (no config file):
    - All built-in patterns are enabled.
    - Severity threshold is ``"low"`` (i.e. report everything).
    - Strip mode is ``False`` (scan-only by default).
    - Output format is ``"table"``.

User overrides (via ``.pr_clean.yml``):
    - ``patterns``:  list of custom pattern definitions (merged with built-ins).
    - ``disable_patterns``: list of built-in pattern names to disable.
    - ``severity_threshold``: minimum severity to report (low/medium/high/critical).
    - ``strip``: bool - automatically strip matched blocks.
    - ``output_format``: ``"table"`` or ``"json"``.
    - ``whitelist``: mapping with ``repos`` and ``authors`` lists.
    - ``fail_on_match``: bool - exit non-zero when matches are found (for CI).

Typical usage::

    from pr_clean.config import load_config

    cfg = load_config(config_path=".pr_clean.yml")
    print(cfg.severity_threshold)
    for pattern in cfg.active_patterns:
        print(pattern.name)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import yaml

from pr_clean.patterns import (
    BUILTIN_PATTERNS,
    InjectionPattern,
    Severity,
    build_custom_pattern,
    get_pattern_by_name,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_FILENAME: str = ".pr_clean.yml"

_DEFAULT_SEVERITY_THRESHOLD: str = "low"
_DEFAULT_OUTPUT_FORMAT: str = "table"
_DEFAULT_STRIP: bool = False
_DEFAULT_FAIL_ON_MATCH: bool = True


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class WhitelistConfig:
    """Whitelist rules that suppress findings for specific repos or authors.

    Attributes:
        repos: List of ``owner/repo`` strings to skip entirely.
        authors: List of GitHub login names whose PRs are never flagged.
    """

    repos: List[str] = field(default_factory=list)
    authors: List[str] = field(default_factory=list)


@dataclass
class Config:
    """Fully resolved pr_clean configuration.

    This is the single source of truth consumed by the scanner, reporter,
    stripper, and CLI.  Instances are produced by :func:`load_config`.

    Attributes:
        severity_threshold: Minimum :class:`~pr_clean.patterns.Severity` level
            to include in reports.
        output_format: Either ``"table"`` or ``"json"``.
        strip: When ``True`` the stripper will remove matched blocks.
        fail_on_match: When ``True`` the CLI exits with code 1 if any matches
            are found (useful in CI pipelines).
        active_patterns: Ordered list of :class:`~pr_clean.patterns.InjectionPattern`
            objects that the scanner should apply.
        disabled_pattern_names: Set of pattern names that were explicitly
            disabled by the user config.
        whitelist: :class:`WhitelistConfig` instance.
        raw: The raw parsed YAML dict (may be empty); useful for debugging.
    """

    severity_threshold: Severity = Severity.LOW
    output_format: str = "table"
    strip: bool = False
    fail_on_match: bool = True
    active_patterns: List[InjectionPattern] = field(default_factory=list)
    disabled_pattern_names: List[str] = field(default_factory=list)
    whitelist: WhitelistConfig = field(default_factory=WhitelistConfig)
    raw: Dict[str, Any] = field(default_factory=dict)

    def is_repo_whitelisted(self, repo: str) -> bool:
        """Return ``True`` if the given ``owner/repo`` string is whitelisted.

        Args:
            repo: Repository identifier in ``owner/repo`` format.

        Returns:
            ``True`` if the repo is in the whitelist.
        """
        return repo in self.whitelist.repos

    def is_author_whitelisted(self, author: str) -> bool:
        """Return ``True`` if the given GitHub login is whitelisted.

        Args:
            author: GitHub login (username) string.

        Returns:
            ``True`` if the author is in the whitelist.
        """
        return author in self.whitelist.authors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_to_list(value: Any) -> List[str]:
    """Return a list of strings from ``value``, which may be a list or scalar.

    Args:
        value: A raw YAML value (list of strings, a single string, or None).

    Returns:
        A list of strings; empty list if ``value`` is falsy.
    """
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _validate_output_format(fmt: str) -> str:
    """Validate and normalise the output format string.

    Args:
        fmt: Raw output format value from config.

    Returns:
        Normalised lowercase format string.

    Raises:
        ValueError: If ``fmt`` is not one of the accepted values.
    """
    normalised = fmt.lower().strip()
    accepted = {"table", "json"}
    if normalised not in accepted:
        raise ValueError(
            f"Invalid output_format {fmt!r}. Must be one of: {sorted(accepted)}."
        )
    return normalised


def _parse_custom_pattern(entry: Dict[str, Any], index: int) -> InjectionPattern:
    """Parse a single custom pattern dict from user config YAML.

    Args:
        entry: Raw YAML dict with pattern fields.
        index: Zero-based index of this entry in the ``patterns`` list
            (used to generate a default name if one is absent).

    Returns:
        A compiled :class:`~pr_clean.patterns.InjectionPattern`.

    Raises:
        ValueError: If required fields are missing or invalid.
        re.error: If the ``regex`` field is not valid regex syntax.
    """
    if not isinstance(entry, dict):
        raise ValueError(
            f"Custom pattern at index {index} must be a mapping, got {type(entry).__name__!r}."
        )

    pattern_str: Optional[str] = entry.get("regex") or entry.get("pattern")
    if not pattern_str:
        raise ValueError(
            f"Custom pattern at index {index} is missing a 'regex' field."
        )

    name: str = entry.get("name") or f"custom_pattern_{index}"
    description: str = entry.get("description", "")
    severity: str = entry.get("severity", "medium")
    confidence: float = float(entry.get("confidence", 0.75))
    category: str = entry.get("category", "custom")
    strip_full_block: bool = bool(entry.get("strip_full_block", True))
    tags: List[str] = _coerce_to_list(entry.get("tags"))

    return build_custom_pattern(
        name=name,
        pattern_str=pattern_str,
        description=description,
        severity=severity,
        confidence=confidence,
        category=category,
        strip_full_block=strip_full_block,
        tags=tags,
    )


def _load_yaml_file(config_path: Union[str, Path]) -> Dict[str, Any]:
    """Read and parse a YAML config file.

    Args:
        config_path: Path to the YAML file.

    Returns:
        Parsed YAML as a dict.  Returns an empty dict if the file is empty.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
        TypeError: If the top-level YAML value is not a mapping.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise TypeError(
            f"Config file {path} must contain a YAML mapping at the top level, "
            f"got {type(data).__name__!r}."
        )

    return data


def _build_active_patterns(
    disabled_names: Sequence[str],
    custom_patterns: List[InjectionPattern],
) -> List[InjectionPattern]:
    """Merge built-in and custom patterns, removing any disabled ones.

    Built-in patterns come first; custom patterns are appended.  Any pattern
    whose :attr:`~pr_clean.patterns.InjectionPattern.name` appears in
    ``disabled_names`` is excluded.

    Args:
        disabled_names: Names of built-in patterns to exclude.
        custom_patterns: Additional user-defined patterns to include.

    Returns:
        Ordered list of active :class:`~pr_clean.patterns.InjectionPattern`.
    """
    disabled_set = set(disabled_names)
    active: List[InjectionPattern] = []

    for pattern in BUILTIN_PATTERNS:
        if pattern.name not in disabled_set:
            active.append(pattern)

    for pattern in custom_patterns:
        if pattern.name not in disabled_set:
            active.append(pattern)

    return active


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(
    config_path: Optional[Union[str, Path]] = None,
    search_parents: bool = True,
) -> Config:
    """Load and return the merged pr_clean configuration.

    Resolution order:

    1. Hard-coded defaults.
    2. ``.pr_clean.yml`` discovered by walking up from the current directory
       (when ``search_parents=True`` and ``config_path`` is not given).
    3. Explicit ``config_path`` (takes precedence over auto-discovery).

    Args:
        config_path: Optional explicit path to a ``.pr_clean.yml`` file.
            When provided, parent search is skipped.
        search_parents: When ``True`` and ``config_path`` is ``None``, search
            the current working directory and its ancestors for a
            ``.pr_clean.yml`` file.

    Returns:
        A fully populated :class:`Config` instance.

    Raises:
        FileNotFoundError: If an explicit ``config_path`` is provided but the
            file does not exist.
        yaml.YAMLError: If the config file contains invalid YAML.
        ValueError: If any config values fail validation.
    """
    raw: Dict[str, Any] = {}

    if config_path is not None:
        # Explicit path: must exist.
        raw = _load_yaml_file(config_path)
    elif search_parents:
        discovered = _discover_config_file()
        if discovered is not None:
            raw = _load_yaml_file(discovered)

    return _build_config(raw)


def load_config_from_dict(data: Dict[str, Any]) -> Config:
    """Build a :class:`Config` from an already-parsed dict.

    This is primarily useful for testing without writing files to disk.

    Args:
        data: A dict following the same schema as a parsed ``.pr_clean.yml``.

    Returns:
        A fully populated :class:`Config` instance.

    Raises:
        ValueError: If any config values fail validation.
    """
    return _build_config(data)


def _discover_config_file() -> Optional[Path]:
    """Search the current directory and its parents for a ``.pr_clean.yml``.

    Args: None.

    Returns:
        The first :class:`~pathlib.Path` found, or ``None``.
    """
    current = Path(os.getcwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / DEFAULT_CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _build_config(raw: Dict[str, Any]) -> Config:
    """Construct a :class:`Config` from a raw (already-parsed) dict.

    Args:
        raw: Parsed YAML dict (may be empty for all-defaults config).

    Returns:
        Populated :class:`Config`.

    Raises:
        ValueError: If any field fails validation.
    """
    # --- Severity threshold ------------------------------------------------
    severity_str: str = str(
        raw.get("severity_threshold", _DEFAULT_SEVERITY_THRESHOLD)
    ).lower().strip()
    try:
        severity_threshold = Severity(severity_str)
    except ValueError:
        valid = [s.value for s in Severity]
        raise ValueError(
            f"Invalid severity_threshold {severity_str!r}. Must be one of: {valid}."
        )

    # --- Output format -----------------------------------------------------
    output_format = _validate_output_format(
        str(raw.get("output_format", _DEFAULT_OUTPUT_FORMAT))
    )

    # --- Strip / fail_on_match booleans ------------------------------------
    strip: bool = bool(raw.get("strip", _DEFAULT_STRIP))
    fail_on_match: bool = bool(raw.get("fail_on_match", _DEFAULT_FAIL_ON_MATCH))

    # --- Disabled built-in patterns ----------------------------------------
    disabled_pattern_names: List[str] = _coerce_to_list(
        raw.get("disable_patterns")
    )

    # Warn (but don't crash) if a disabled name doesn't exist in builtins
    for name in disabled_pattern_names:
        if get_pattern_by_name(name) is None:
            # We still accept it; might be a custom pattern name.
            pass

    # --- Custom patterns ---------------------------------------------------
    custom_patterns: List[InjectionPattern] = []
    patterns_raw = raw.get("patterns", []) or []
    if not isinstance(patterns_raw, list):
        raise ValueError(
            f"'patterns' must be a YAML list, got {type(patterns_raw).__name__!r}."
        )
    for idx, entry in enumerate(patterns_raw):
        custom_patterns.append(_parse_custom_pattern(entry, idx))

    # --- Whitelist ---------------------------------------------------------
    whitelist_raw = raw.get("whitelist", {}) or {}
    if not isinstance(whitelist_raw, dict):
        raise ValueError(
            f"'whitelist' must be a YAML mapping, got {type(whitelist_raw).__name__!r}."
        )
    whitelist = WhitelistConfig(
        repos=_coerce_to_list(whitelist_raw.get("repos")),
        authors=_coerce_to_list(whitelist_raw.get("authors")),
    )

    # --- Build active pattern list -----------------------------------------
    active_patterns = _build_active_patterns(disabled_pattern_names, custom_patterns)

    return Config(
        severity_threshold=severity_threshold,
        output_format=output_format,
        strip=strip,
        fail_on_match=fail_on_match,
        active_patterns=active_patterns,
        disabled_pattern_names=disabled_pattern_names,
        whitelist=whitelist,
        raw=raw,
    )
