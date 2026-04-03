"""Core scanning logic for pr_clean.

This module provides the :class:`Scanner` class, which accepts raw markdown
text and applies a registry of :class:`~pr_clean.patterns.InjectionPattern`
objects to detect AI-injected promotional content, Copilot tip blocks,
unsolicited agent output, and similar noise.

For each match found, the scanner produces a :class:`ScanMatch` dataclass
carrying:

* The matched text and its location (line numbers, character offsets).
* A reference to the :class:`~pr_clean.patterns.InjectionPattern` that
  triggered the match.
* An adjusted confidence score (may be boosted by context).
* A ``source`` label (``"body"`` or ``"comment"``) set by the caller.

Typical usage::

    from pr_clean.scanner import Scanner
    from pr_clean.config import load_config

    cfg = load_config()
    scanner = Scanner(config=cfg)
    matches = scanner.scan(markdown_text)
    for m in matches:
        print(m.pattern_name, m.line_start, m.severity)

Or with the convenience class-method::

    matches = Scanner.from_defaults().scan(text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from pr_clean.config import Config, load_config_from_dict
from pr_clean.patterns import InjectionPattern, Severity


# ---------------------------------------------------------------------------
# ScanMatch dataclass
# ---------------------------------------------------------------------------


@dataclass
class ScanMatch:
    """A single detected injection match within a piece of markdown text.

    Attributes:
        pattern_name: The :attr:`~pr_clean.patterns.InjectionPattern.name` of
            the pattern that produced this match.
        pattern: The full :class:`~pr_clean.patterns.InjectionPattern` instance
            for convenient access to metadata.
        matched_text: The exact substring that was matched by the regex.
        line_start: 1-based line number where the match begins.
        line_end: 1-based line number where the match ends (same as
            ``line_start`` for single-line matches).
        char_start: 0-based character offset of the start of the match within
            the full markdown string.
        char_end: 0-based character offset of the first character *after* the
            match within the full markdown string.
        severity: :class:`~pr_clean.patterns.Severity` of the matched pattern.
        confidence: Adjusted confidence score in [0.0, 1.0].
        source: Caller-supplied label such as ``"body"`` or ``"comment"``.
        context_lines: Up to 2 lines of surrounding context for display.
    """

    pattern_name: str
    pattern: InjectionPattern
    matched_text: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    severity: Severity
    confidence: float
    source: str = "body"
    context_lines: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_multiline(self) -> bool:
        """Return ``True`` if the match spans more than one line."""
        return self.line_end > self.line_start

    @property
    def severity_label(self) -> str:
        """Return the severity as a plain string (e.g. ``"high"``)."""
        return self.severity.value

    @property
    def line_range(self) -> str:
        """Human-readable line range, e.g. ``"12"`` or ``"12-15"``."""
        if self.is_multiline:
            return f"{self.line_start}-{self.line_end}"
        return str(self.line_start)

    def to_dict(self) -> dict:
        """Serialise the match to a plain dict suitable for JSON output.

        Returns:
            A dict with all match fields serialised to JSON-safe types.
        """
        return {
            "pattern_name": self.pattern_name,
            "matched_text": self.matched_text,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "severity": self.severity_label,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "category": self.pattern.category,
            "description": self.pattern.description,
            "context_lines": self.context_lines,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _char_offset_to_line_number(text: str, offset: int) -> int:
    """Convert a 0-based character offset to a 1-based line number.

    Args:
        text: The full markdown string.
        offset: 0-based character offset (clipped to ``[0, len(text)]``).

    Returns:
        1-based line number.
    """
    # Count newlines before the offset.
    clipped = max(0, min(offset, len(text)))
    return text[:clipped].count("\n") + 1


def _extract_context_lines(
    lines: List[str],
    line_start: int,
    line_end: int,
    context: int = 2,
) -> List[str]:
    """Return surrounding context lines around a matched range.

    Args:
        lines: All lines of the markdown text (0-indexed).
        line_start: 1-based start line of the match.
        line_end: 1-based end line of the match.
        context: Number of lines before and after to include.

    Returns:
        List of context line strings (may be empty if out of bounds).
    """
    total = len(lines)
    # Convert to 0-based indices.
    start_idx = max(0, line_start - 1 - context)
    end_idx = min(total, line_end + context)  # exclusive
    return lines[start_idx:end_idx]


def _adjust_confidence(
    base_confidence: float,
    matched_text: str,
    pattern: InjectionPattern,
) -> float:
    """Apply lightweight heuristic boosts / penalties to the base confidence.

    Current heuristics:

    * Long matches (> 200 chars) get a small boost because they are more
      likely to be genuine blocks rather than accidental phrase overlaps.
    * Multiline matches get a small boost.
    * Very short matches (<= 20 chars) get a small penalty.

    The result is clamped to ``[0.0, 1.0]``.

    Args:
        base_confidence: The pattern's baseline confidence score.
        matched_text: The exact matched substring.
        pattern: The triggering :class:`~pr_clean.patterns.InjectionPattern`.

    Returns:
        Adjusted confidence float in ``[0.0, 1.0]``.
    """
    score = base_confidence
    length = len(matched_text)

    if length > 200:
        score += 0.02
    elif length > 100:
        score += 0.01
    elif length <= 20:
        score -= 0.05

    if "\n" in matched_text:
        score += 0.01

    return max(0.0, min(1.0, score))


def _meets_severity_threshold(severity: Severity, threshold: Severity) -> bool:
    """Return ``True`` if *severity* is at or above *threshold*.

    Args:
        severity: The severity of a candidate match.
        threshold: The minimum severity required by the config.

    Returns:
        ``True`` if the match should be included.
    """
    order = [
        Severity.LOW,
        Severity.MEDIUM,
        Severity.HIGH,
        Severity.CRITICAL,
    ]
    return order.index(severity) >= order.index(threshold)


# ---------------------------------------------------------------------------
# Scanner class
# ---------------------------------------------------------------------------


class Scanner:
    """Apply a registry of injection patterns to markdown text.

    The scanner iterates over every active pattern in the supplied
    :class:`~pr_clean.config.Config` and searches the input text for regex
    matches.  It deduplicates overlapping matches from the same pattern,
    filters by the configured severity threshold, and returns an ordered list
    of :class:`ScanMatch` objects sorted by character offset.

    Args:
        config: A resolved :class:`~pr_clean.config.Config` instance.  When
            ``None`` the scanner uses all built-in defaults (all patterns
            enabled, ``LOW`` severity threshold).

    Example::

        scanner = Scanner()
        matches = scanner.scan("START COPILOT CODING AGENT TIPS\nstuff\nEND COPILOT CODING AGENT TIPS")
        assert matches[0].pattern_name == "copilot_agent_tips_block"
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        """Initialise the Scanner.

        Args:
            config: Optional resolved :class:`~pr_clean.config.Config`.
                Defaults to an all-defaults config when ``None``.
        """
        if config is None:
            config = load_config_from_dict({})
        self._config = config

    # ------------------------------------------------------------------
    # Class-method constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_defaults(cls) -> "Scanner":
        """Create a Scanner using only the built-in defaults (no config file).

        Returns:
            A :class:`Scanner` instance with all built-in patterns active and
            severity threshold set to ``LOW``.
        """
        return cls(config=load_config_from_dict({}))

    @classmethod
    def from_config(cls, config: Config) -> "Scanner":
        """Create a Scanner from an already-loaded :class:`~pr_clean.config.Config`.

        Args:
            config: A fully populated :class:`~pr_clean.config.Config`.

        Returns:
            A :class:`Scanner` wrapping the given config.
        """
        return cls(config=config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> Config:
        """The resolved :class:`~pr_clean.config.Config` in use."""
        return self._config

    @property
    def active_patterns(self) -> List[InjectionPattern]:
        """The list of patterns this scanner will apply."""
        return self._config.active_patterns

    def scan(
        self,
        text: str,
        source: str = "body",
    ) -> List[ScanMatch]:
        """Scan *text* for injection patterns and return all matches.

        Args:
            text: Raw markdown string to scan.  May be empty.
            source: Label for where this text came from (e.g. ``"body"`` or
                ``"comment"``).  Stored verbatim on each :class:`ScanMatch`.

        Returns:
            List of :class:`ScanMatch` objects sorted by ``char_start``.
            Returns an empty list when no patterns match or the text is empty.
        """
        if not text:
            return []

        lines = text.splitlines()
        matches: List[ScanMatch] = []

        for pattern in self._config.active_patterns:
            pattern_matches = self._apply_pattern(text, lines, pattern, source)
            matches.extend(pattern_matches)

        # Deduplicate: if two patterns produced an identical char range for
        # the same matched text, keep only the higher-severity one.
        matches = self._deduplicate(matches)

        # Filter by severity threshold.
        threshold = self._config.severity_threshold
        matches = [
            m for m in matches
            if _meets_severity_threshold(m.severity, threshold)
        ]

        # Sort by position in the document.
        matches.sort(key=lambda m: (m.char_start, m.char_end))

        return matches

    def scan_multiple(
        self,
        texts: Sequence[str],
        source: str = "comment",
    ) -> List[ScanMatch]:
        """Scan a sequence of text strings (e.g. PR comments) in order.

        Each text is scanned independently.  Matches from all texts are
        returned in a flat list preserving per-text ordering.

        Args:
            texts: Sequence of raw markdown strings to scan.
            source: Label applied to every match (default ``"comment"``).

        Returns:
            Flat list of :class:`ScanMatch` objects from all texts combined.
        """
        all_matches: List[ScanMatch] = []
        for text in texts:
            all_matches.extend(self.scan(text, source=source))
        return all_matches

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_pattern(
        self,
        text: str,
        lines: List[str],
        pattern: InjectionPattern,
        source: str,
    ) -> List[ScanMatch]:
        """Apply a single pattern to the full text and return all matches.

        Args:
            text: Full markdown string.
            lines: Pre-split lines of *text* (0-indexed).
            pattern: The :class:`~pr_clean.patterns.InjectionPattern` to apply.
            source: Source label to store on each match.

        Returns:
            List of :class:`ScanMatch` instances for every non-overlapping
            regex hit.
        """
        results: List[ScanMatch] = []

        try:
            for m in pattern.regex.finditer(text):
                char_start = m.start()
                char_end = m.end()
                matched_text = m.group(0)

                line_start = _char_offset_to_line_number(text, char_start)
                line_end = _char_offset_to_line_number(text, max(char_end - 1, char_start))

                confidence = _adjust_confidence(
                    pattern.confidence, matched_text, pattern
                )

                context = _extract_context_lines(lines, line_start, line_end)

                results.append(
                    ScanMatch(
                        pattern_name=pattern.name,
                        pattern=pattern,
                        matched_text=matched_text,
                        line_start=line_start,
                        line_end=line_end,
                        char_start=char_start,
                        char_end=char_end,
                        severity=pattern.severity,
                        confidence=confidence,
                        source=source,
                        context_lines=context,
                    )
                )
        except re.error:
            # Defensive: a malformed custom pattern should not crash the whole
            # scan.  Log nothing here; the reporter can surface an empty result.
            pass

        return results

    @staticmethod
    def _deduplicate(matches: List[ScanMatch]) -> List[ScanMatch]:
        """Remove duplicate matches that cover the identical character range.

        When two different patterns match exactly the same span in the text,
        only the one with the higher severity (and then higher confidence) is
        kept.

        Args:
            matches: Raw list of all matches (possibly containing duplicates).

        Returns:
            Deduplicated list.
        """
        severity_order = [
            Severity.LOW,
            Severity.MEDIUM,
            Severity.HIGH,
            Severity.CRITICAL,
        ]

        # Key: (char_start, char_end) → best match seen so far.
        best: dict[tuple[int, int], ScanMatch] = {}

        for m in matches:
            key = (m.char_start, m.char_end)
            existing = best.get(key)
            if existing is None:
                best[key] = m
            else:
                # Prefer higher severity; break ties by higher confidence.
                if severity_order.index(m.severity) > severity_order.index(
                    existing.severity
                ):
                    best[key] = m
                elif severity_order.index(m.severity) == severity_order.index(
                    existing.severity
                ) and m.confidence > existing.confidence:
                    best[key] = m

        return list(best.values())
