# pr_clean 🧹

> Keep your pull requests human. Strip the noise.

[![PyPI version](https://img.shields.io/pypi/v/pr_clean.svg)](https://pypi.org/project/pr_clean/)
[![Python versions](https://img.shields.io/pypi/pyversions/pr_clean.svg)](https://pypi.org/project/pr_clean/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**pr_clean** is a CLI tool and GitHub Action that scans pull request descriptions and comments for AI-injected promotional content, unsolicited tips, and known injection patterns (e.g. `START COPILOT CODING AGENT TIPS`). It parses PR markdown using a configurable pattern registry, flags suspicious blocks with detailed reports, and can automatically strip them — keeping your PR discussions clean and human-authored. Designed for teams who want **signal over noise** in their code review process.

---

## Quick Start

```bash
# Install
pip install pr_clean

# Scan a local markdown file
pr_clean scan pr_body.md

# Scan a live GitHub PR (requires GITHUB_TOKEN)
export GITHUB_TOKEN=ghp_yourtoken
pr_clean scan --url https://github.com/owner/repo/pull/42

# Strip injection blocks and print cleaned markdown
pr_clean strip pr_body.md

# Strip and push changes back to GitHub
pr_clean strip --url https://github.com/owner/repo/pull/42 --push
```

After running `scan`, you'll see a color-coded table of findings. Exit code is `0` when clean, `1` when injections are found (configurable via `fail_on_match`).

---

## Features

- **Built-in pattern registry** covering known AI injection signatures — Copilot tip blocks, promotional agent output, and more — with regex-based matching and confidence scores
- **Strip mode** that surgically removes detected injection blocks from PR body and comments via the GitHub API, leaving all human-authored content intact
- **Configurable via `.pr_clean.yml`** — add custom patterns, set severity thresholds, whitelist repos or authors, and toggle CI failure behavior
- **Rich CLI output** with color-coded tables showing matched patterns, line numbers, severity, and confidence; plus a `--json` flag for machine-readable output in CI pipelines
- **Ready-to-use GitHub Action** (`action.yml`) that runs on `pull_request` events, fails the check when injections are found, and posts a summary comment with findings

---

## Usage Examples

### Scan a local file

```bash
pr_clean scan pr_body.md
```

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Pattern                  ┃ Lines  ┃ Severity ┃ Confidence ┃ Matched Text                             ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ copilot_tips_block       │ 16–23  │ high     │ 0.97       │ START COPILOT CODING AGENT TIPS\n…       │
│ promotional_agent_output │ 31–35  │ medium   │ 0.85       │ > 💡 Tip: You can also ask Copilot to… │
└──────────────────────────┴────────┴──────────┴────────────┴──────────────────────────────────────────┘
2 injection(s) found.
```

### Scan a GitHub PR and output JSON

```bash
pr_clean scan --url https://github.com/owner/repo/pull/42 --json
```

```json
[
  {
    "pattern_name": "copilot_tips_block",
    "severity": "high",
    "confidence": 0.97,
    "line_start": 16,
    "line_end": 23,
    "source": "body",
    "matched_text": "START COPILOT CODING AGENT TIPS\n..."
  }
]
```

### Strip injections and push to GitHub

```bash
pr_clean strip --url https://github.com/owner/repo/pull/42 --push
```

```
Stripped 2 injection block(s). PR body and 1 comment updated.
```

### Use in Python

```python
from pr_clean import Scanner
from pr_clean.stripper import Stripper

markdown = open("pr_body.md").read()

scanner = Scanner()
matches = scanner.scan(markdown)

for match in matches:
    print(match.pattern_name, match.severity, match.confidence)

cleaned = Stripper().strip(markdown, matches)
print(cleaned)
```

### GitHub Actions

Add pr_clean to your workflow to automatically flag or block PRs with AI-injected content:

```yaml
# .github/workflows/pr_clean.yml
name: PR Clean

on:
  pull_request:
    types: [opened, edited, synchronize]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: pr-clean/pr_clean@v0.1.0
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          severity-threshold: medium
          fail-on-match: 'true'
          strip: 'false'
          output-format: table
```

---

## Project Structure

```
pr_clean/
├── pr_clean/
│   ├── __init__.py        # Exposes Scanner class and version string
│   ├── cli.py             # Click CLI: scan, report, strip sub-commands
│   ├── scanner.py         # Core scanning logic; returns ScanMatch list
│   ├── patterns.py        # Built-in regex pattern registry with metadata
│   ├── stripper.py        # Removes matched injection blocks from markdown
│   ├── reporter.py        # Rich table and JSON output formatter
│   ├── github_client.py   # PyGithub wrapper for fetching/updating PRs
│   └── config.py          # Loads and merges .pr_clean.yml with defaults
├── tests/
│   ├── test_scanner.py    # Unit tests for scanner logic
│   ├── test_stripper.py   # Unit tests for injection block removal
│   ├── test_config.py     # Tests for config loading and YAML merging
│   └── fixtures/
│       └── sample_pr_body.md  # Sample PR markdown with injection patterns
├── action.yml             # GitHub Actions composite action definition
├── .pr_clean.yml.example  # Example configuration file
└── pyproject.toml         # Project metadata and dependencies
```

---

## Configuration

Create a `.pr_clean.yml` in your repository root to customize behavior. All fields are optional.

```yaml
# .pr_clean.yml

# Minimum severity to report: low | medium | high | critical
severity_threshold: medium

# Output format: table | json
output_format: table

# Exit non-zero when matches found (useful in CI)
fail_on_match: true

# Auto-strip matched blocks (use with caution)
strip: false

# Disable specific built-in patterns by name
disable_patterns:
  - promotional_agent_output

# Add your own custom patterns
patterns:
  - name: my_custom_pattern
    regex: 'AUTOMATED SUMMARY START.*?AUTOMATED SUMMARY END'
    severity: high
    description: Internal automated summary blocks

# Whitelist specific repos or authors
whitelist:
  authors:
    - dependabot[bot]
    - renovate[bot]
  repos:
    - owner/internal-tools
```

You can also point to a config file explicitly:

```bash
pr_clean scan --config /path/to/.pr_clean.yml pr_body.md
```

### All Configuration Options

| Option | Type | Default | Description |
|---|---|---|---|
| `severity_threshold` | string | `low` | Minimum severity to report (`low`/`medium`/`high`/`critical`) |
| `output_format` | string | `table` | Output format (`table` or `json`) |
| `fail_on_match` | bool | `false` | Exit non-zero when any match is found |
| `strip` | bool | `false` | Automatically strip matched blocks |
| `disable_patterns` | list | `[]` | Built-in pattern names to disable |
| `patterns` | list | `[]` | Custom pattern definitions to add |
| `whitelist.authors` | list | `[]` | Authors whose PRs are skipped |
| `whitelist.repos` | list | `[]` | Repos to skip entirely |

---

## Running Tests

```bash
pip install -e '.[dev]'
pytest

# With coverage
pytest --cov=pr_clean --cov-report=term-missing
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

*Built with [Jitter](https://github.com/jitter-ai) - an AI agent that ships code daily.*
