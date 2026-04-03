"""Injection block stripper for pr_clean.

This module provides the :class:`Stripper` class and the :func:`strip_matches`
convenience function, which accept a piece of raw markdown text together with
a list of :class:`~pr_clean.scanner.ScanMatch` objects produced by the
:class:`~pr_clean.scanner.Scanner`, and return a cleaned version of the
markdown with all matched injection blocks surgically removed.

The stripper operates in character-offset space rather than line space so that
it can handle both single-line and multi-line block removals without
accidentally mangling surrounding content.  After removing each matched span
it also collapses sequences of more than two consecutive blank lines that are
often left behind by block removal.

Typical usage::

    from pr_clean.scanner import Scanner
    from pr_clean.stripper import Stripper

    scanner = Scanner()
    matches = scanner.scan(pr_body)

    stripper = Stripper()
    clean_body = stripper.strip(pr_body, matches)

Or using the module-level convenience function::

    from pr_clean.stripper import strip_matches

    clean_body = strip_matches(pr_body, matches)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from pr_clean.scanner import ScanMatch


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex that collapses 3+ consecutive blank lines into exactly two.
_MULTI_BLANK_RE: re.Pattern[str] = re.compile(r"\n{3,}")

# Regex to strip trailing whitespace from every line.
_TRAILING_WHITESPACE_RE: re.Pattern[str] = re.compile(r"[ \t]+$", re.MULTILINE)


# ---------------------------------------------------------------------------
# StripResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class StripResult:
    """The outcome of a single strip operation.

    Attributes:
        original_text: The unmodified input text.
        clean_text: The text after all matched spans have been removed.
        removed_spans: List of ``(char_start, char_end, pattern_name)`` tuples
            describing every span that was excised.
        match_count: Number of matches that were processed.
        changed: ``True`` when *clean_text* differs from *original_text*.
    """

    original_text: str
    clean_text: str
    removed_spans: List[Tuple[int, int, str]] = field(default_factory=list)

    @property
    def match_count(self) -> int:
        """Return the number of spans that were removed."""
        return len(self.removed_spans)

    @property
    def changed(self) -> bool:
        """Return ``True`` if the text was modified."""
        return self.original_text != self.clean_text

    def to_dict(self) -> dict:
        """Serialise the result to a plain dict.

        Returns:
            A dict with all result fields serialised to JSON-safe types.
        """
        return {
            "changed": self.changed,
            "match_count": self.match_count,
            "removed_spans": [
                {
                    "char_start": start,
                    "char_end": end,
                    "pattern_name": name,
                }
                for start, end, name in self.removed_spans
            ],
            "clean_text": self.clean_text,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_non_overlapping_spans(
    matches: Sequence[ScanMatch],
) -> List[Tuple[int, int, str]]:
    """Convert scan matches to a sorted, non-overlapping list of character spans.

    When two matches overlap, the one that starts earlier is preferred; if they
    start at the same position the longer (larger ``char_end``) span wins.

    Args:
        matches: The :class:`~pr_clean.scanner.ScanMatch` objects to process.

    Returns:
        Sorted list of ``(char_start, char_end, pattern_name)`` tuples with
        no overlapping intervals.
    """
    # Sort by start position, then by descending end position so that longer
    # spans are preferred when two matches start at the same position.
    sorted_spans: List[Tuple[int, int, str]] = sorted(
        [(m.char_start, m.char_end, m.pattern_name) for m in matches],
        key=lambda t: (t[0], -(t[1])),
    )

    merged: List[Tuple[int, int, str]] = []
    for start, end, name in sorted_spans:
        if not merged:
            merged.append((start, end, name))
            continue
        prev_start, prev_end, prev_name = merged[-1]
        if start < prev_end:
            # Overlapping: extend the previous span if this one reaches further.
            if end > prev_end:
                merged[-1] = (prev_start, end, prev_name)
        else:
            merged.append((start, end, name))

    return merged


def _expand_span_to_full_lines(
    text: str,
    char_start: int,
    char_end: int,
    strip_full_block: bool,
) -> Tuple[int, int]:
    """Optionally expand a character span to the boundaries of complete lines.

    When *strip_full_block* is ``True`` (the default for most patterns), the
    span is expanded so that the entire line(s) containing the match are
    removed rather than just the matching substring.  This avoids leaving
    orphaned newlines or partial lines.

    When *strip_full_block* is ``False``, only the matched substring itself is
    removed and the line boundaries are left intact.

    Args:
        text: The full markdown string.
        char_start: 0-based start offset of the match.
        char_end: 0-based end offset (exclusive) of the match.
        strip_full_block: Whether to expand to full line boundaries.

    Returns:
        ``(expanded_start, expanded_end)`` pair of character offsets.
    """
    if not strip_full_block:
        return char_start, char_end

    # Expand start: walk back to the start of the line (or start of string).
    expanded_start = char_start
    while expanded_start > 0 and text[expanded_start - 1] != "\n":
        expanded_start -= 1

    # Expand end: walk forward to consume the trailing newline of the last
    # matched line (so we don't leave a blank line stub).
    expanded_end = char_end
    while expanded_end < len(text) and text[expanded_end - 1] != "\n":
        expanded_end += 1
    # Consume the newline itself if present.
    if expanded_end < len(text) and text[expanded_end] == "\n":
        expanded_end += 1

    return expanded_start, expanded_end


def _collapse_excess_blank_lines(text: str) -> str:
    """Replace runs of 3+ consecutive newlines with exactly two newlines.

    Args:
        text: Markdown string that may have had blocks removed.

    Returns:
        Text with excess blank lines collapsed.
    """
    return _MULTI_BLANK_RE.sub("\n\n", text)


def _strip_trailing_whitespace(text: str) -> str:
    """Remove trailing spaces/tabs from every line.

    Args:
        text: Markdown string to clean.

    Returns:
        Text with trailing whitespace stripped from each line.
    """
    return _TRAILING_WHITESPACE_RE.sub("", text)


def _apply_spans(
    text: str,
    spans: List[Tuple[int, int, str]],
    matches_by_name: dict,
) -> Tuple[str, List[Tuple[int, int, str]]]:
    """Remove the given character spans from *text*.

    Each span is optionally expanded to full line boundaries according to the
    ``strip_full_block`` attribute of the triggering pattern.

    Args:
        text: Original markdown string.
        spans: Non-overlapping ``(char_start, char_end, pattern_name)`` tuples
            sorted by ``char_start``.
        matches_by_name: Mapping from pattern name to :class:`ScanMatch` for
            looking up the ``strip_full_block`` flag.  The first match for
            each pattern name is used.

    Returns:
        ``(clean_text, removed_spans)`` where *removed_spans* records the
        actual character ranges that were excised (after any expansion).
    """
    removed: List[Tuple[int, int, str]] = []
    parts: List[str] = []
    cursor = 0

    for char_start, char_end, pattern_name in spans:
        # Skip spans that were already consumed (shouldn't happen after
        # deduplication, but be defensive).
        if char_start < cursor:
            continue

        # Determine whether to expand to full lines.
        match_obj: Optional[ScanMatch] = matches_by_name.get(pattern_name)
        strip_full_block = match_obj.pattern.strip_full_block if match_obj else True

        exp_start, exp_end = _expand_span_to_full_lines(
            text, char_start, char_end, strip_full_block
        )
        # Clamp to cursor to avoid going backwards after expansion.
        exp_start = max(exp_start, cursor)
        exp_end = min(exp_end, len(text))

        if exp_start > cursor:
            parts.append(text[cursor:exp_start])

        removed.append((exp_start, exp_end, pattern_name))
        cursor = exp_end

    # Append whatever remains after the last span.
    if cursor < len(text):
        parts.append(text[cursor:])

    clean_text = "".join(parts)
    return clean_text, removed


# ---------------------------------------------------------------------------
# Stripper class
# ---------------------------------------------------------------------------


class Stripper:
    """Remove matched injection blocks from markdown text.

    The :class:`Stripper` accepts a list of :class:`~pr_clean.scanner.ScanMatch`
    objects and surgically excises their character spans from the original
    markdown, optionally expanding each span to the boundaries of the
    containing line(s).  After removal, excess blank lines are collapsed so
    that the resulting text looks clean.

    Example::

        from pr_clean.scanner import Scanner
        from pr_clean.stripper import Stripper

        scanner = Scanner()
        matches = scanner.scan(pr_body)
        stripper = Stripper()
        result = stripper.strip(pr_body, matches)
        print(result.clean_text)
    """

    def strip(
        self,
        text: str,
        matches: Sequence[ScanMatch],
        collapse_blank_lines: bool = True,
        strip_trailing_whitespace: bool = True,
    ) -> StripResult:
        """Remove all matched injection spans from *text*.

        Args:
            text: The original markdown string to clean.
            matches: Sequence of :class:`~pr_clean.scanner.ScanMatch` objects
                identifying the injection spans to remove.
            collapse_blank_lines: When ``True`` (default), runs of 3+ blank
                lines left by removal are collapsed to two blank lines.
            strip_trailing_whitespace: When ``True`` (default), trailing
                spaces and tabs are removed from every line after stripping.

        Returns:
            A :class:`StripResult` describing the outcome.
        """
        if not matches or not text:
            return StripResult(
                original_text=text,
                clean_text=text,
                removed_spans=[],
            )

        # Build a mapping from pattern_name to the first ScanMatch with that
        # name so we can look up strip_full_block later.
        matches_by_name: dict[str, ScanMatch] = {}
        for m in matches:
            if m.pattern_name not in matches_by_name:
                matches_by_name[m.pattern_name] = m

        # Build a sorted, non-overlapping list of spans to remove.
        spans = _build_non_overlapping_spans(matches)

        # Remove the spans.
        clean_text, removed_spans = _apply_spans(text, spans, matches_by_name)

        # Post-process.
        if collapse_blank_lines:
            clean_text = _collapse_excess_blank_lines(clean_text)
        if strip_trailing_whitespace:
            clean_text = _strip_trailing_whitespace(clean_text)

        return StripResult(
            original_text=text,
            clean_text=clean_text,
            removed_spans=removed_spans,
        )

    def strip_text(
        self,
        text: str,
        matches: Sequence[ScanMatch],
        collapse_blank_lines: bool = True,
        strip_trailing_whitespace: bool = True,
    ) -> str:
        """Convenience wrapper that returns only the cleaned text string.

        Args:
            text: The original markdown string.
            matches: Matched injection spans to remove.
            collapse_blank_lines: Collapse excess blank lines after removal.
            strip_trailing_whitespace: Strip trailing whitespace per line.

        Returns:
            The cleaned markdown string.
        """
        result = self.strip(
            text,
            matches,
            collapse_blank_lines=collapse_blank_lines,
            strip_trailing_whitespace=strip_trailing_whitespace,
        )
        return result.clean_text


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def strip_matches(
    text: str,
    matches: Sequence[ScanMatch],
    collapse_blank_lines: bool = True,
    strip_trailing_whitespace: bool = True,
) -> str:
    """Remove matched injection spans from *text* and return the cleaned string.

    This is a thin convenience wrapper around :class:`Stripper` for callers
    that only need the cleaned text and not the full :class:`StripResult`.

    Args:
        text: The original markdown string.
        matches: :class:`~pr_clean.scanner.ScanMatch` objects identifying the
            injection spans to remove.
        collapse_blank_lines: When ``True`` (default), collapse runs of 3+
            blank lines to two blank lines.
        strip_trailing_whitespace: When ``True`` (default), remove trailing
            whitespace from each line.

    Returns:
        The cleaned markdown string.  Returns *text* unchanged when *matches*
        is empty or *text* is empty.
    """
    return Stripper().strip_text(
        text,
        matches,
        collapse_blank_lines=collapse_blank_lines,
        strip_trailing_whitespace=strip_trailing_whitespace,
    )


def strip_result(
    text: str,
    matches: Sequence[ScanMatch],
    collapse_blank_lines: bool = True,
    strip_trailing_whitespace: bool = True,
) -> StripResult:
    """Remove matched injection spans and return the full :class:`StripResult`.

    Args:
        text: The original markdown string.
        matches: :class:`~pr_clean.scanner.ScanMatch` objects to strip.
        collapse_blank_lines: Collapse excess blank lines after removal.
        strip_trailing_whitespace: Strip trailing whitespace per line.

    Returns:
        A :class:`StripResult` with the cleaned text and metadata about what
        was removed.
    """
    return Stripper().strip(
        text,
        matches,
        collapse_blank_lines=collapse_blank_lines,
        strip_trailing_whitespace=strip_trailing_whitespace,
    )
