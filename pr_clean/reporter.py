"""Rich-powered reporter for pr_clean scan results.

This module provides the :class:`Reporter` class and associated helpers that
format :class:`~pr_clean.scanner.ScanMatch` objects into human-readable Rich
tables or machine-readable JSON output.

Color coding follows a severity-to-colour mapping:

* ``critical`` → bold red
* ``high``     → red
* ``medium``   → yellow
* ``low``      → cyan

Typical usage::

    from pr_clean.reporter import Reporter
    from pr_clean.scanner import Scanner

    scanner = Scanner()
    matches = scanner.scan(markdown_text)

    reporter = Reporter(output_format="table")
    reporter.print_results(matches, source="body")

For JSON output::

    reporter = Reporter(output_format="json")
    reporter.print_results(matches)

Or to get the JSON string directly::

    json_str = reporter.to_json(matches)
"""

from __future__ import annotations

import json
import sys
from typing import IO, Dict, List, Optional, Sequence

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from pr_clean.patterns import Severity
from pr_clean.scanner import ScanMatch


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEVERITY_COLOURS: Dict[str, str] = {
    Severity.CRITICAL.value: "bold red",
    Severity.HIGH.value: "red",
    Severity.MEDIUM.value: "yellow",
    Severity.LOW.value: "cyan",
}

_SEVERITY_EMOJI: Dict[str, str] = {
    Severity.CRITICAL.value: "🚨",
    Severity.HIGH.value: "🔴",
    Severity.MEDIUM.value: "🟡",
    Severity.LOW.value: "🔵",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _severity_text(severity_value: str) -> Text:
    """Return a Rich :class:`~rich.text.Text` for a severity label.

    Args:
        severity_value: Lowercase severity string (e.g. ``"critical"``)

    Returns:
        Styled :class:`~rich.text.Text` instance.
    """
    colour = _SEVERITY_COLOURS.get(severity_value, "white")
    emoji = _SEVERITY_EMOJI.get(severity_value, "")
    label = f"{emoji} {severity_value.upper()}" if emoji else severity_value.upper()
    return Text(label, style=colour)


def _truncate(text: str, max_length: int = 60) -> str:
    """Truncate *text* to *max_length* characters, appending ``…`` if needed.

    Args:
        text: String to truncate.
        max_length: Maximum allowed length before truncation.

    Returns:
        Possibly-truncated string.
    """
    # Replace newlines with a visible marker for compact display.
    single_line = text.replace("\n", " ↵ ")
    if len(single_line) <= max_length:
        return single_line
    return single_line[: max_length - 1] + "…"


def _confidence_bar(confidence: float, width: int = 10) -> str:
    """Return a simple ASCII progress bar for a confidence score.

    Args:
        confidence: Float in [0.0, 1.0].
        width: Total bar width in characters.

    Returns:
        A string like ``"████████░░"``.
    """
    filled = round(confidence * width)
    empty = width - filled
    return "█" * filled + "░" * empty


# ---------------------------------------------------------------------------
# Reporter class
# ---------------------------------------------------------------------------


class Reporter:
    """Format and output pr_clean scan results.

    Supports two output modes:

    * ``"table"`` — Rich colour-coded table rendered to a :class:`~rich.console.Console`.
    * ``"json"``  — Machine-readable JSON written to *stdout* (or a supplied stream).

    Args:
        output_format: Either ``"table"`` (default) or ``"json"``.
        console: Optional :class:`~rich.console.Console` to use for table output.
            Defaults to a new console writing to *stdout*.
        no_colour: When ``True``, disable Rich colour output (useful for CI
            environments where ANSI codes are unwanted).

    Example::

        reporter = Reporter(output_format="table")
        reporter.print_results(matches)
    """

    def __init__(
        self,
        output_format: str = "table",
        console: Optional[Console] = None,
        no_colour: bool = False,
    ) -> None:
        """Initialise the Reporter.

        Args:
            output_format: ``"table"`` or ``"json"``.
            console: Optional Rich Console for table rendering.
            no_colour: Disable colour output.

        Raises:
            ValueError: If *output_format* is not ``"table"`` or ``"json"``.
        """
        normalised = output_format.lower().strip()
        if normalised not in {"table", "json"}:
            raise ValueError(
                f"Invalid output_format {output_format!r}. Must be 'table' or 'json'."
            )
        self._format = normalised
        self._console = console or Console(
            highlight=False,
            no_color=no_colour,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def output_format(self) -> str:
        """The active output format string (``"table"`` or ``"json"``)."""
        return self._format

    def print_results(
        self,
        matches: Sequence[ScanMatch],
        source: str = "",
        title: str = "",
        file: Optional[IO[str]] = None,
    ) -> None:
        """Print scan results to the console or *file*.

        Dispatches to :meth:`print_table` or :meth:`print_json` based on
        the configured output format.

        Args:
            matches: Ordered list of :class:`~pr_clean.scanner.ScanMatch` objects.
            source: Optional source label shown in the table title (e.g.
                ``"body"`` or ``"comment #42"``).
            title: Optional custom title string.  When empty a default title is
                generated from *source* and match count.
            file: Optional writable file-like object.  For JSON output this
                overrides the default of *sys.stdout*; for table output it is
                passed to the underlying console.
        """
        if self._format == "json":
            self.print_json(matches, file=file or sys.stdout)
        else:
            self.print_table(matches, source=source, title=title)

    def print_table(
        self,
        matches: Sequence[ScanMatch],
        source: str = "",
        title: str = "",
    ) -> None:
        """Render scan results as a Rich colour-coded table.

        When *matches* is empty a brief "no findings" panel is printed instead
        of an empty table.

        Args:
            matches: Ordered list of :class:`~pr_clean.scanner.ScanMatch` objects.
            source: Source label used in the table header.
            title: Optional custom title for the table panel.
        """
        if not matches:
            self._print_clean_panel(source=source)
            return

        table_title = title or self._make_title(matches, source)
        table = self._build_table(matches, table_title)
        self._console.print(table)
        self._print_summary_line(matches)

    def print_json(
        self,
        matches: Sequence[ScanMatch],
        file: Optional[IO[str]] = None,
    ) -> None:
        """Serialise scan results to JSON and write to *file* (default *stdout*).

        Args:
            matches: Ordered list of :class:`~pr_clean.scanner.ScanMatch` objects.
            file: Writable stream; defaults to *sys.stdout*.
        """
        output = file or sys.stdout
        payload = {
            "match_count": len(matches),
            "matches": [m.to_dict() for m in matches],
        }
        json.dump(payload, output, indent=2, ensure_ascii=False)
        output.write("\n")

    def to_json(
        self,
        matches: Sequence[ScanMatch],
        indent: int = 2,
    ) -> str:
        """Serialise scan results to a JSON string.

        Args:
            matches: Ordered list of :class:`~pr_clean.scanner.ScanMatch` objects.
            indent: JSON indentation level.

        Returns:
            Formatted JSON string.
        """
        payload = {
            "match_count": len(matches),
            "matches": [m.to_dict() for m in matches],
        }
        return json.dumps(payload, indent=indent, ensure_ascii=False)

    def print_header(
        self,
        text: str,
        style: str = "bold blue",
    ) -> None:
        """Print a styled header line to the console.

        Args:
            text: Header text to display.
            style: Rich style string.  Defaults to ``"bold blue"``.
        """
        self._console.print(text, style=style)

    def print_error(
        self,
        message: str,
    ) -> None:
        """Print an error message in bold red.

        Args:
            message: Error text to display.
        """
        self._console.print(f"[bold red]ERROR:[/bold red] {message}")

    def print_warning(
        self,
        message: str,
    ) -> None:
        """Print a warning message in yellow.

        Args:
            message: Warning text to display.
        """
        self._console.print(f"[yellow]WARNING:[/yellow] {message}")

    def print_success(
        self,
        message: str,
    ) -> None:
        """Print a success message in green.

        Args:
            message: Success text to display.
        """
        self._console.print(f"[green]✓[/green] {message}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_title(self, matches: Sequence[ScanMatch], source: str) -> str:
        """Build a default table title string.

        Args:
            matches: List of scan matches.
            source: Source label string.

        Returns:
            Title string.
        """
        count = len(matches)
        noun = "match" if count == 1 else "matches"
        if source:
            return f"pr_clean — {count} {noun} found in {source}"
        return f"pr_clean — {count} {noun} found"

    def _build_table(
        self,
        matches: Sequence[ScanMatch],
        title: str,
    ) -> Table:
        """Construct a Rich :class:`~rich.table.Table` from scan matches.

        Args:
            matches: Ordered list of :class:`~pr_clean.scanner.ScanMatch` objects.
            title: Table title string.

        Returns:
            A fully populated :class:`~rich.table.Table` instance ready to render.
        """
        table = Table(
            title=title,
            box=box.ROUNDED,
            show_lines=True,
            title_style="bold",
            header_style="bold white on dark_blue",
            expand=False,
        )

        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Lines", style="bold", width=8, justify="center")
        table.add_column("Severity", width=16, justify="center")
        table.add_column("Confidence", width=16, justify="center")
        table.add_column("Pattern", style="bold cyan", width=32)
        table.add_column("Category", style="dim", width=16)
        table.add_column("Source", style="dim", width=10)
        table.add_column("Matched Text", width=55)

        for idx, match in enumerate(matches, start=1):
            sev_text = _severity_text(match.severity_label)
            conf_bar = _confidence_bar(match.confidence)
            conf_label = f"{conf_bar} {match.confidence:.0%}"
            matched_preview = _truncate(match.matched_text, 52)

            table.add_row(
                str(idx),
                match.line_range,
                sev_text,
                conf_label,
                match.pattern_name,
                match.pattern.category,
                match.source,
                matched_preview,
            )

        return table

    def _print_clean_panel(self, source: str = "") -> None:
        """Print a 'no findings' success panel.

        Args:
            source: Source label to include in the message.
        """
        msg = "No injection patterns detected."
        if source:
            msg = f"No injection patterns detected in {source}."
        panel = Panel(
            Text(f"✓ {msg}", style="bold green"),
            title="pr_clean",
            border_style="green",
        )
        self._console.print(panel)

    def _print_summary_line(self, matches: Sequence[ScanMatch]) -> None:
        """Print a one-line summary after the table.

        Args:
            matches: All matches that were displayed.
        """
        counts: Dict[str, int] = {}
        for m in matches:
            counts[m.severity_label] = counts.get(m.severity_label, 0) + 1

        parts: List[str] = []
        for sev in ["critical", "high", "medium", "low"]:
            if sev in counts:
                colour = _SEVERITY_COLOURS.get(sev, "white")
                parts.append(f"[{colour}]{counts[sev]} {sev}[/{colour}]")

        summary = "  ".join(parts)
        total = len(matches)
        noun = "match" if total == 1 else "matches"
        self._console.print(f"\n[bold]Total:[/bold] {total} {noun}  —  {summary}\n")


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def print_results(
    matches: Sequence[ScanMatch],
    output_format: str = "table",
    source: str = "",
    title: str = "",
    no_colour: bool = False,
    file: Optional[IO[str]] = None,
) -> None:
    """Convenience wrapper: print scan results without instantiating a Reporter.

    Args:
        matches: Ordered list of :class:`~pr_clean.scanner.ScanMatch` objects.
        output_format: ``"table"`` or ``"json"``.
        source: Source label for the table title.
        title: Optional custom title override.
        no_colour: Disable Rich colour output.
        file: Optional output stream for JSON mode.
    """
    reporter = Reporter(output_format=output_format, no_colour=no_colour)
    reporter.print_results(matches, source=source, title=title, file=file)


def to_json(
    matches: Sequence[ScanMatch],
    indent: int = 2,
) -> str:
    """Serialise scan results to a JSON string.

    Args:
        matches: Ordered list of :class:`~pr_clean.scanner.ScanMatch` objects.
        indent: JSON indentation level.

    Returns:
        Formatted JSON string.
    """
    return Reporter(output_format="json").to_json(matches, indent=indent)
