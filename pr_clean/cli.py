"""Click-based CLI entry point for pr_clean.

This module wires together all pr_clean sub-systems into a cohesive CLI tool
with three sub-commands:

``scan``
    Scan a PR body (supplied as a local file, piped text, or fetched from
    GitHub via ``--url``) and print findings.  Exits 1 when matches are found
    and ``fail_on_match`` is enabled.

``report``
    Alias for ``scan`` that always uses the table output format.

``strip``
    Scan a PR body and strip all matched injection blocks, then print the
    cleaned text.  Optionally push the result back to GitHub with ``--push``.

Usage examples::

    # Scan a local markdown file
    pr_clean scan --file pr_body.md

    # Scan a GitHub PR by URL
    pr_clean scan --url https://github.com/owner/repo/pull/42 --token $GITHUB_TOKEN

    # Scan and emit JSON for CI
    pr_clean scan --url owner/repo#42 --format json --token $GITHUB_TOKEN

    # Strip injections from a local file and print the result
    pr_clean strip --file pr_body.md

    # Strip a GitHub PR in-place (requires --token with write access)
    pr_clean strip --url https://github.com/owner/repo/pull/42 --push --token $GH_TOKEN

    # List all built-in patterns
    pr_clean patterns list
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from pr_clean import __version__
from pr_clean.config import Config, load_config
from pr_clean.patterns import BUILTIN_PATTERNS, Severity
from pr_clean.reporter import Reporter
from pr_clean.scanner import Scanner
from pr_clean.stripper import Stripper, strip_matches


# ---------------------------------------------------------------------------
# Shared console
# ---------------------------------------------------------------------------

_console = Console(highlight=False)
_err_console = Console(stderr=True, highlight=False)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _load_config_safe(config_path: Optional[str], no_colour: bool = False) -> Config:
    """Load config, printing a warning on errors and falling back to defaults.

    Args:
        config_path: Optional explicit path to a ``.pr_clean.yml`` file.
        no_colour: When ``True``, suppress colour in warning messages.

    Returns:
        A :class:`~pr_clean.config.Config` instance (defaults on failure).
    """
    reporter = Reporter(output_format="table", no_colour=no_colour)
    try:
        if config_path:
            return load_config(config_path=config_path)
        return load_config()
    except FileNotFoundError as exc:
        reporter.print_warning(f"Config file not found: {exc}. Using defaults.")
    except Exception as exc:  # noqa: BLE001
        reporter.print_warning(f"Failed to load config: {exc}. Using defaults.")

    # Fall back to all-defaults config.
    from pr_clean.config import load_config_from_dict
    return load_config_from_dict({})


def _read_stdin() -> str:
    """Read all text from *stdin* and return it.

    Returns:
        Text read from *stdin*.
    """
    return sys.stdin.read()


def _fetch_pr_text(
    url: str,
    token: Optional[str],
    reporter: Reporter,
) -> tuple[str, str]:
    """Fetch a PR body from GitHub.

    Args:
        url: PR URL or ``owner/repo#number`` shorthand.
        token: GitHub personal access token.
        reporter: Reporter for error output.

    Returns:
        ``(pr_body_text, repo_identifier)`` tuple.

    Raises:
        SystemExit: On authentication or network errors.
    """
    from pr_clean.github_client import GitHubClient, parse_pr_reference

    if not token:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        reporter.print_error(
            "A GitHub token is required to fetch PRs. "
            "Provide --token or set the GITHUB_TOKEN environment variable."
        )
        raise SystemExit(2)

    try:
        repo, pr_number = parse_pr_reference(url)
        client = GitHubClient(token=token)
        pr_data = client.get_pr(repo, pr_number)
        return pr_data.body, repo
    except ValueError as exc:
        reporter.print_error(str(exc))
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001
        reporter.print_error(f"Failed to fetch PR from GitHub: {exc}")
        raise SystemExit(2)


def _resolve_text(
    url: Optional[str],
    file: Optional[str],
    token: Optional[str],
    reporter: Reporter,
) -> tuple[str, str]:
    """Resolve the markdown text to scan from the supplied options.

    Priority: *url* > *file* > *stdin*.

    Args:
        url: Optional GitHub PR URL or shorthand.
        file: Optional local file path.
        token: Optional GitHub token (used when *url* is set).
        reporter: Reporter for error output.

    Returns:
        ``(text, source_label)`` tuple.

    Raises:
        SystemExit: On file-not-found or other read errors.
    """
    if url:
        text, repo = _fetch_pr_text(url, token, reporter)
        return text, f"PR {url}"

    if file:
        path = Path(file)
        if not path.exists():
            reporter.print_error(f"File not found: {path}")
            raise SystemExit(2)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            reporter.print_error(f"Cannot read file {path}: {exc}")
            raise SystemExit(2)
        return text, str(path)

    # Fall back to stdin.
    if sys.stdin.isatty():
        reporter.print_error(
            "No input provided. Use --url, --file, or pipe markdown text via stdin."
        )
        raise SystemExit(2)
    text = _read_stdin()
    return text, "stdin"


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="pr_clean")
@click.pass_context
def main(ctx: click.Context) -> None:
    """pr_clean — scan and clean AI-injected content from pull requests.

    Run a sub-command with --help for more details.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# Shared options (reused across sub-commands)
# ---------------------------------------------------------------------------

_url_option = click.option(
    "--url", "-u",
    metavar="URL",
    default=None,
    help="GitHub PR URL or 'owner/repo#number' shorthand.",
)
_file_option = click.option(
    "--file", "-f",
    metavar="PATH",
    default=None,
    type=click.Path(exists=False),  # We check existence ourselves for better errors.
    help="Path to a local markdown file to scan.",
)
_token_option = click.option(
    "--token", "-t",
    metavar="TOKEN",
    default=None,
    envvar="GITHUB_TOKEN",
    help="GitHub personal access token (also read from GITHUB_TOKEN env var).",
)
_config_option = click.option(
    "--config", "-c",
    metavar="PATH",
    default=None,
    type=click.Path(exists=False),
    help="Path to a .pr_clean.yml config file.",
)
_format_option = click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default=None,
    help="Output format: 'table' (default) or 'json'.",
)
_severity_option = click.option(
    "--severity", "-s",
    type=click.Choice(["low", "medium", "high", "critical"], case_sensitive=False),
    default=None,
    help="Minimum severity level to report.",
)
_no_colour_option = click.option(
    "--no-colour", "--no-color",
    is_flag=True,
    default=False,
    help="Disable colour output.",
)
_no_fail_option = click.option(
    "--no-fail",
    is_flag=True,
    default=False,
    help="Always exit 0, even when matches are found (overrides config fail_on_match).",
)


# ---------------------------------------------------------------------------
# scan sub-command
# ---------------------------------------------------------------------------


@main.command("scan")
@_url_option
@_file_option
@_token_option
@_config_option
@_format_option
@_severity_option
@_no_colour_option
@_no_fail_option
@click.pass_context
def scan_command(
    ctx: click.Context,
    url: Optional[str],
    file: Optional[str],
    token: Optional[str],
    config: Optional[str],
    output_format: Optional[str],
    severity: Optional[str],
    no_colour: bool,
    no_fail: bool,
) -> None:
    """Scan markdown text for AI injection patterns.

    Input is resolved in priority order: --url (GitHub API) > --file (local
    markdown file) > stdin.

    Examples:

    \b
        # Scan a local file
        pr_clean scan --file pr_body.md

    \b
        # Scan a GitHub PR
        pr_clean scan --url https://github.com/owner/repo/pull/42

    \b
        # Pipe text from stdin
        cat pr_body.md | pr_clean scan

    \b
        # Machine-readable JSON output
        pr_clean scan --file pr_body.md --format json
    """
    cfg = _load_config_safe(config, no_colour=no_colour)

    # CLI flags override config values.
    if output_format:
        cfg.output_format = output_format.lower()
    if severity:
        cfg.severity_threshold = Severity(severity.lower())

    reporter = Reporter(output_format=cfg.output_format, no_colour=no_colour)
    text, source = _resolve_text(url, file, token, reporter)

    scanner = Scanner(config=cfg)
    matches = scanner.scan(text, source=source)

    reporter.print_results(matches, source=source)

    # Determine exit code.
    fail_on_match = cfg.fail_on_match and not no_fail
    if matches and fail_on_match:
        ctx.exit(1)


# ---------------------------------------------------------------------------
# report sub-command (alias for scan with forced table output)
# ---------------------------------------------------------------------------


@main.command("report")
@_url_option
@_file_option
@_token_option
@_config_option
@_severity_option
@_no_colour_option
@_no_fail_option
@click.pass_context
def report_command(
    ctx: click.Context,
    url: Optional[str],
    file: Optional[str],
    token: Optional[str],
    config: Optional[str],
    severity: Optional[str],
    no_colour: bool,
    no_fail: bool,
) -> None:
    """Scan and display a Rich colour-coded table report.

    This is identical to the 'scan' sub-command but always uses the table
    output format regardless of the config setting.

    Examples:

    \b
        pr_clean report --file pr_body.md
        pr_clean report --url https://github.com/owner/repo/pull/42
    """
    cfg = _load_config_safe(config, no_colour=no_colour)
    cfg.output_format = "table"  # Always table for 'report'.

    if severity:
        cfg.severity_threshold = Severity(severity.lower())

    reporter = Reporter(output_format="table", no_colour=no_colour)
    text, source = _resolve_text(url, file, token, reporter)

    scanner = Scanner(config=cfg)
    matches = scanner.scan(text, source=source)

    reporter.print_table(matches, source=source)

    fail_on_match = cfg.fail_on_match and not no_fail
    if matches and fail_on_match:
        ctx.exit(1)


# ---------------------------------------------------------------------------
# strip sub-command
# ---------------------------------------------------------------------------


@main.command("strip")
@_url_option
@_file_option
@_token_option
@_config_option
@_format_option
@_severity_option
@_no_colour_option
@_no_fail_option
@click.option(
    "--push",
    is_flag=True,
    default=False,
    help="Push the cleaned PR body back to GitHub (requires --url and --token).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be stripped without modifying anything.",
)
@click.option(
    "--output", "-o",
    metavar="PATH",
    default=None,
    type=click.Path(),
    help="Write the cleaned markdown to a file instead of stdout.",
)
@click.pass_context
def strip_command(
    ctx: click.Context,
    url: Optional[str],
    file: Optional[str],
    token: Optional[str],
    config: Optional[str],
    output_format: Optional[str],
    severity: Optional[str],
    no_colour: bool,
    no_fail: bool,
    push: bool,
    dry_run: bool,
    output: Optional[str],
) -> None:
    """Strip AI injection blocks from markdown text.

    Scans the input text, removes all detected injection spans, and writes
    the cleaned markdown to stdout (or --output file).  Optionally pushes
    the result back to GitHub with --push.

    Examples:

    \b
        # Strip a local file and print cleaned markdown
        pr_clean strip --file pr_body.md

    \b
        # Dry-run: show what would be removed without writing anything
        pr_clean strip --url https://github.com/owner/repo/pull/42 --dry-run

    \b
        # Strip and push back to GitHub
        pr_clean strip --url owner/repo#42 --push --token $GITHUB_TOKEN

    \b
        # Write cleaned markdown to a file
        pr_clean strip --file pr_body.md --output clean_pr.md
    """
    cfg = _load_config_safe(config, no_colour=no_colour)

    if output_format:
        cfg.output_format = output_format.lower()
    if severity:
        cfg.severity_threshold = Severity(severity.lower())

    reporter = Reporter(output_format=cfg.output_format, no_colour=no_colour)
    text, source = _resolve_text(url, file, token, reporter)

    scanner = Scanner(config=cfg)
    matches = scanner.scan(text, source=source)

    if not matches:
        reporter.print_clean_panel = lambda **kw: reporter._print_clean_panel(source=source)  # type: ignore[attr-defined]
        reporter._print_clean_panel(source=source)
        ctx.exit(0)
        return

    # Show what was found.
    if cfg.output_format == "json":
        reporter.print_json(matches)
    else:
        reporter.print_table(matches, source=source, title=f"Injection patterns found in {source}")

    if dry_run:
        reporter.print_warning("Dry-run mode: no changes made.")
        # Still exit non-zero if fail_on_match.
        fail_on_match = cfg.fail_on_match and not no_fail
        if matches and fail_on_match:
            ctx.exit(1)
        return

    # Perform the strip.
    stripper = Stripper()
    result = stripper.strip(text, matches)
    clean_text = result.clean_text

    if push:
        if not url:
            reporter.print_error("--push requires --url to identify the GitHub PR.")
            ctx.exit(2)
            return
        _push_cleaned_body(url, clean_text, token, reporter, no_colour=no_colour)
        reporter.print_success(
            f"Cleaned PR body pushed to GitHub ({result.match_count} block(s) removed)."
        )
    elif output:
        output_path = Path(output)
        try:
            output_path.write_text(clean_text, encoding="utf-8")
            reporter.print_success(
                f"Cleaned markdown written to {output_path} ({result.match_count} block(s) removed)."
            )
        except OSError as exc:
            reporter.print_error(f"Cannot write to {output_path}: {exc}")
            ctx.exit(2)
            return
    else:
        # Default: print to stdout.
        click.echo(clean_text)
        _err_console.print(
            f"[green]✓[/green] {result.match_count} injection block(s) removed.",
        )

    fail_on_match = cfg.fail_on_match and not no_fail
    if matches and fail_on_match:
        ctx.exit(1)


def _push_cleaned_body(
    url: str,
    clean_body: str,
    token: Optional[str],
    reporter: Reporter,
    no_colour: bool = False,
) -> None:
    """Push a cleaned PR body back to GitHub.

    Args:
        url: PR URL or shorthand.
        clean_body: Cleaned markdown string.
        token: GitHub token.
        reporter: Reporter for error messages.
        no_colour: Disable colour in messages.

    Raises:
        SystemExit: On authentication or API errors.
    """
    from pr_clean.github_client import GitHubClient, parse_pr_reference

    if not token:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        reporter.print_error(
            "A GitHub token is required to push changes. "
            "Provide --token or set the GITHUB_TOKEN environment variable."
        )
        raise SystemExit(2)

    try:
        repo, pr_number = parse_pr_reference(url)
        client = GitHubClient(token=token)
        client.update_pr_body(repo, pr_number, clean_body)
    except ValueError as exc:
        reporter.print_error(str(exc))
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001
        reporter.print_error(f"Failed to push changes to GitHub: {exc}")
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# patterns sub-group
# ---------------------------------------------------------------------------


@main.group("patterns")
def patterns_group() -> None:
    """Inspect and manage the built-in injection pattern registry."""


@patterns_group.command("list")
@click.option(
    "--category", "-C",
    default=None,
    metavar="CATEGORY",
    help="Filter patterns by category (e.g. 'copilot', 'promotional').",
)
@click.option(
    "--severity", "-s",
    type=click.Choice(["low", "medium", "high", "critical"], case_sensitive=False),
    default=None,
    help="Filter patterns by minimum severity.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format: 'table' (default) or 'json'.",
)
@click.option("--no-colour", "--no-color", is_flag=True, default=False)
def patterns_list_command(
    category: Optional[str],
    severity: Optional[str],
    output_format: str,
    no_colour: bool,
) -> None:
    """List all built-in injection patterns.

    Examples:

    \b
        pr_clean patterns list
        pr_clean patterns list --category copilot
        pr_clean patterns list --severity high
        pr_clean patterns list --format json
    """
    from pr_clean.patterns import get_patterns_by_category, get_patterns_by_severity
    from rich.table import Table
    from rich import box

    patterns = list(BUILTIN_PATTERNS)

    if severity:
        min_sev = Severity(severity.lower())
        order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        min_idx = order.index(min_sev)
        patterns = [p for p in patterns if order.index(p.severity) >= min_idx]

    if category:
        patterns = [p for p in patterns if p.category == category]

    console = Console(highlight=False, no_color=no_colour)

    if output_format == "json":
        payload = [
            {
                "name": p.name,
                "description": p.description,
                "severity": p.severity.value,
                "confidence": p.confidence,
                "category": p.category,
                "strip_full_block": p.strip_full_block,
                "tags": p.tags,
            }
            for p in patterns
        ]
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    table = Table(
        title=f"Built-in Patterns ({len(patterns)})",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold white on dark_blue",
    )
    table.add_column("Name", style="bold cyan")
    table.add_column("Category", style="dim")
    table.add_column("Severity", width=14, justify="center")
    table.add_column("Confidence", width=10, justify="right")
    table.add_column("Description")

    from pr_clean.reporter import _severity_text

    for p in patterns:
        table.add_row(
            p.name,
            p.category,
            _severity_text(p.severity.value),
            f"{p.confidence:.0%}",
            p.description,
        )

    console.print(table)


@patterns_group.command("show")
@click.argument("name")
@click.option("--no-colour", "--no-color", is_flag=True, default=False)
def patterns_show_command(name: str, no_colour: bool) -> None:
    """Show details for a single built-in pattern by NAME.

    Example:

    \b
        pr_clean patterns show copilot_agent_tips_block
    """
    from pr_clean.patterns import get_pattern_by_name
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    console = Console(highlight=False, no_color=no_colour)
    pattern = get_pattern_by_name(name)

    if pattern is None:
        console.print(f"[bold red]ERROR:[/bold red] Pattern {name!r} not found.")
        raise SystemExit(1)

    from pr_clean.reporter import _severity_text

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Field", style="bold dim", width=20)
    table.add_column("Value")

    table.add_row("Name", f"[bold cyan]{pattern.name}[/bold cyan]")
    table.add_row("Description", pattern.description)
    table.add_row("Category", pattern.category)
    table.add_row("Severity", _severity_text(pattern.severity.value))
    table.add_row("Confidence", f"{pattern.confidence:.0%}")
    table.add_row("Strip Full Block", str(pattern.strip_full_block))
    table.add_row("Tags", ", ".join(pattern.tags) if pattern.tags else "(none)")
    table.add_row("Regex", f"[dim]{pattern.regex.pattern}[/dim]")

    panel = Panel(table, title=f"Pattern: {pattern.name}", border_style="cyan")
    console.print(panel)


# ---------------------------------------------------------------------------
# version sub-command (convenience)
# ---------------------------------------------------------------------------


@main.command("version")
def version_command() -> None:
    """Print the installed pr_clean version."""
    click.echo(f"pr_clean version {__version__}")


# ---------------------------------------------------------------------------
# Entry point guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    main()
