"""pr_clean - Scan and clean AI-injected promotional content from pull request descriptions and comments.

This package provides a CLI tool and GitHub Action integration that detects known
injection patterns (such as Copilot tips, promotional blocks, and unsolicited agent
output) in PR markdown text, reports findings with detailed metadata, and can
automatically strip those blocks to keep PR discussions clean and human-authored.

Typical usage::

    from pr_clean import Scanner, __version__

    scanner = Scanner()
    matches = scanner.scan(markdown_text)
    for match in matches:
        print(match.pattern_name, match.line_start, match.severity)
"""

from __future__ import annotations

__version__ = "0.1.0"
__author__ = "pr_clean contributors"
__license__ = "MIT"

# Scanner will be importable from pr_clean once phase 3 is implemented.
# We expose a lazy reference here so the public API surface is declared early.
__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "Scanner",
]


def __getattr__(name: str) -> object:
    """Lazy-load heavy submodules to keep import time low.

    Args:
        name: The attribute name being accessed.

    Returns:
        The requested attribute after lazy-loading its module.

    Raises:
        AttributeError: If the requested name is not a known lazy attribute.
    """
    if name == "Scanner":
        from pr_clean.scanner import Scanner  # noqa: PLC0415
        return Scanner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
