# pr_clean

[![PyPI version](https://img.shields.io/pypi/v/pr_clean.svg)](https://pypi.org/project/pr_clean/)
[![Python versions](https://img.shields.io/pypi/pyversions/pr_clean.svg)](https://pypi.org/project/pr_clean/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**pr_clean** is a CLI tool and GitHub Action that scans pull request descriptions and comments for AI-injected promotional content, unsolicited tips, and known injection patterns (e.g. `START COPILOT CODING AGENT TIPS`).  It parses PR markdown using a configurable pattern registry, flags suspicious blocks with detailed reports, and can automatically strip them to keep PR discussions clean and human-authored.

Designed for teams who want **signal over noise** in their code review process.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
  - [scan](#scan)
  - [report](#report)
  - [strip](#strip)
  - [patterns list](#patterns-list)
  - [patterns show](#patterns-show)
- [GitHub Action](#github-action)
  - [Basic Usage](#basic-usage)
  - [Advanced Usage](#advanced-usage)
  - [Inputs](#inputs)
  - [Outputs](#outputs)
- [Configuration (.pr_clean.yml)](#configuration-pr_cleanyml)
  - [Custom Patterns](#custom-patterns)
  - [Disabling Built-in Patterns](#disabling-built-in-patterns)
  - [Whitelist](#whitelist)
- [Built-in Pattern Registry](#built-in-pattern-registry)
- [Output Formats](#output-formats)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Built-in pattern registry** covering known AI injection signatures like Copilot tips blocks, promotional footers, auto-generated summaries, CodeRabbit review blocks, and unsolicited agent output — all implemented as compiled regular expressions with metadata.
- **Configurable via `.pr_clean.yml`** — add custom patterns, set severity thresholds, disable built-ins, and whitelist specific repos or authors.
- **Strip mode** that surgically removes detected injection blocks from PR body and comments via the GitHub API while leaving human-authored content intact.
- **Rich CLI output** with color-coded tables showing matched patterns, line numbers, severity, and confidence scores alongside a machine-readable JSON mode for CI pipelines.
- **Ready-to-use GitHub Action** (`action.yml`) that runs on `pull_request` events, fails the check when injections are found, and optionally posts a summary comment with findings.
- **No false-positive anxiety** — every match carries a confidence score and a human-readable description of what was matched and why.

---

## Installation

```bash
pip install pr_clean
```

Requires Python 3.9 or higher.

For development (including test dependencies):

```bash
pip install "pr_clean[dev]"
```

---

## Quick Start

### Scan a local markdown file

```bash
pr_clean scan --file pr_body.md
```

### Scan a GitHub PR by URL

```bash
export GITHUB_TOKEN=ghp_...
pr_clean scan --url https://github.com/owner/repo/pull/42
```

### Scan and emit JSON for CI

```bash
pr_clean scan --url owner/repo#42 --format json
```

### Strip injection blocks and print the cleaned markdown

```bash
pr_clean strip --file pr_body.md
```

### Strip a GitHub PR in-place

```bash
pr_clean strip --url https://github.com/owner/repo/pull/42 --push
```

### Pipe markdown text from stdin

```bash
cat pr_body.md | pr_clean scan
```

---

## CLI Reference

All sub-commands share the following global option:

```
pr_clean [--version] [--help] <SUBCOMMAND> [OPTIONS]
```

### scan

Scan markdown text for AI injection patterns.

```
pr_clean scan [OPTIONS]
```

| Option | Short | Description |
|---|---|---|
| `--url URL` | `-u` | GitHub PR URL or `owner/repo#number` shorthand. |
| `--file PATH` | `-f` | Path to a local markdown file. |
| `--token TOKEN` | `-t` | GitHub PAT (also read from `GITHUB_TOKEN` env var). |
| `--config PATH` | `-c` | Path to a `.pr_clean.yml` config file. |
| `--format {table,json}` | | Output format. Default: `table`. |
| `--severity {low,medium,high,critical}` | `-s` | Minimum severity to report. Default: `low`. |
| `--no-colour` | | Disable Rich colour output. |
| `--no-fail` | | Always exit 0, even when matches are found. |

**Input resolution order:** `--url` (GitHub API) → `--file` (local file) → stdin.

**Exit codes:**
- `0` — no matches found, or `--no-fail` was passed.
- `1` — one or more injection patterns detected (when `fail_on_match` is enabled).
- `2` — usage or I/O error.

**Examples:**

```bash
# Scan a local file, table output
pr_clean scan --file pr_body.md

# Scan a GitHub PR, fail if injections found
pr_clean scan --url https://github.com/owner/repo/pull/42 --severity medium

# JSON output for scripting
pr_clean scan --file pr_body.md --format json | jq '.matches[].pattern_name'
```

---

### report

Alias for `scan` that always uses the Rich table output format.

```
pr_clean report [OPTIONS]
```

Accepts the same options as `scan` except `--format` (always `table`).

---

### strip

Scan markdown text and remove all detected injection blocks.

```
pr_clean strip [OPTIONS]
```

| Option | Short | Description |
|---|---|---|
| `--url URL` | `-u` | GitHub PR URL or `owner/repo#number` shorthand. |
| `--file PATH` | `-f` | Path to a local markdown file. |
| `--token TOKEN` | `-t` | GitHub PAT. |
| `--config PATH` | `-c` | Path to a `.pr_clean.yml` config file. |
| `--format {table,json}` | | Output format for the findings report. |
| `--severity {low,medium,high,critical}` | `-s` | Minimum severity to strip. |
| `--push` | | Push the cleaned body back to GitHub (requires `--url` and write-scoped token). |
| `--dry-run` | | Show what would be stripped without modifying anything. |
| `--output PATH` | `-o` | Write the cleaned markdown to a file instead of stdout. |
| `--no-colour` | | Disable colour output. |
| `--no-fail` | | Always exit 0. |

**Examples:**

```bash
# Strip a local file and print cleaned markdown to stdout
pr_clean strip --file pr_body.md

# Dry-run: show what would be removed
pr_clean strip --url https://github.com/owner/repo/pull/42 --dry-run

# Strip and push back to GitHub
pr_clean strip --url owner/repo#42 --push

# Write cleaned markdown to a file
pr_clean strip --file pr_body.md --output clean_pr.md
```

---

### patterns list

List all built-in injection patterns.

```
pr_clean patterns list [OPTIONS]
```

| Option | Description |
|---|---|
| `--category CATEGORY` | Filter by category (e.g. `copilot`, `promotional`). |
| `--severity {low,medium,high,critical}` | Filter by minimum severity. |
| `--format {table,json}` | Output format. Default: `table`. |
| `--no-colour` | Disable colour output. |

**Examples:**

```bash
pr_clean patterns list
pr_clean patterns list --category copilot
pr_clean patterns list --severity high --format json
```

---

### patterns show

Show detailed information about a single built-in pattern.

```
pr_clean patterns show NAME
```

**Example:**

```bash
pr_clean patterns show copilot_agent_tips_block
```

---

## GitHub Action

pr_clean ships a ready-to-use composite GitHub Action that you can add to any repository workflow.

### Basic Usage

Create `.github/workflows/pr_clean.yml` in your repository:

```yaml
name: Scan PR for AI injections

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write   # Only needed if post-comment or strip is enabled

jobs:
  pr-clean:
    name: pr_clean
    runs-on: ubuntu-latest
    steps:
      - name: Scan PR
        uses: pr-clean/pr_clean@v0.1.0
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

This will:
1. Scan the PR body for all built-in injection patterns.
2. Print a color-coded table of findings in the CI log.
3. **Fail the check** if any injection patterns are found (exit code 1).

---

### Advanced Usage

#### Scan, strip, and post a comment

```yaml
- name: Scan and auto-strip PR
  uses: pr-clean/pr_clean@v0.1.0
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    severity-threshold: medium
    strip: 'true'
    post-comment: 'true'
    output-format: table
    fail-on-match: 'true'
```

#### Use a custom configuration file

```yaml
- uses: actions/checkout@v4   # checkout needed to access .pr_clean.yml

- name: Scan PR with custom config
  uses: pr-clean/pr_clean@v0.1.0
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    config-file: .pr_clean.yml
    severity-threshold: low
```

#### JSON output and downstream processing

```yaml
- name: Scan PR (JSON)
  id: scan
  uses: pr-clean/pr_clean@v0.1.0
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    output-format: json
    fail-on-match: 'false'

- name: Print match count
  run: echo "Found ${{ steps.scan.outputs.match-count }} injection(s)"
```

#### Only warn, never fail

```yaml
- uses: pr-clean/pr_clean@v0.1.0
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    fail-on-match: 'false'
    post-comment: 'true'
```

---

### Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `github-token` | No | `${{ github.token }}` | GitHub token for API access. |
| `pr-url` | No | PR URL from event | GitHub PR URL or `owner/repo#number` shorthand. |
| `severity-threshold` | No | `low` | Minimum severity to report: `low`, `medium`, `high`, `critical`. |
| `output-format` | No | `table` | Output format: `table` or `json`. |
| `fail-on-match` | No | `true` | Exit non-zero when matches are found. |
| `strip` | No | `false` | Remove injection blocks via the GitHub API. |
| `post-comment` | No | `false` | Post a summary comment on the PR. |
| `config-file` | No | `''` | Path to a `.pr_clean.yml` config file. |
| `python-version` | No | `3.11` | Python version to use. |
| `install-extras` | No | `''` | Additional pip install arguments (e.g. pin a version). |

### Outputs

| Output | Description |
|---|---|
| `match-count` | Number of injection patterns detected. |
| `clean` | `'true'` when no patterns were found; `'false'` otherwise. |
| `json-report` | JSON-encoded scan results for downstream use. |

---

## Configuration (.pr_clean.yml)

pr_clean automatically discovers a `.pr_clean.yml` file by walking up from the current working directory.  You can also supply an explicit path with `--config`.

Copy `.pr_clean.yml.example` from this repository as a starting point:

```bash
cp .pr_clean.yml.example .pr_clean.yml
```

All fields are optional.  A minimal config looks like:

```yaml
# .pr_clean.yml
severity_threshold: medium
fail_on_match: true
strip: false
output_format: table
```

### Full Configuration Reference

```yaml
# Minimum severity level to report.
# Accepted: low (default) | medium | high | critical
severity_threshold: low

# Output format: table (default) | json
output_format: table

# Automatically strip matched blocks via the GitHub API.
# Default: false
strip: false

# Exit non-zero when matches are found (useful in CI).
# Default: true
fail_on_match: true

# List of built-in pattern names to disable.
# Run `pr_clean patterns list` to see all available names.
disable_patterns:
  - unsolicited_tips_header
  - github_actions_bot_footer

# Add custom injection patterns.
patterns:
  - name: my_internal_bot_block
    regex: 'START INTERNAL BOT OUTPUT.*?END INTERNAL BOT OUTPUT'
    description: Blocks injected by our internal release bot.
    severity: high
    confidence: 0.98
    category: internal_bot
    strip_full_block: true
    tags:
      - internal
      - bot

# Whitelist specific repos or authors.
whitelist:
  repos:
    - my-org/sandbox-repo
  authors:
    - dependabot[bot]
    - renovate[bot]
```

### Custom Patterns

Each entry in `patterns` supports the following fields:

| Field | Required | Default | Description |
|---|---|---|---|
| `regex` | **Yes** | — | Python regex string. `IGNORECASE` and `MULTILINE` flags applied by default. |
| `name` | No | `custom_pattern_N` | Unique identifier for this pattern. |
| `description` | No | `''` | Human-readable explanation. |
| `severity` | No | `medium` | `low` \| `medium` \| `high` \| `critical` |
| `confidence` | No | `0.75` | Baseline confidence score in `[0.0, 1.0]`. |
| `category` | No | `custom` | Grouping label for filtering. |
| `strip_full_block` | No | `true` | Strip the whole matched block (`true`) or just the matching substring (`false`). |
| `tags` | No | `[]` | Arbitrary tag strings for filtering. |

### Disabling Built-in Patterns

To see all available built-in pattern names:

```bash
pr_clean patterns list
```

Disable specific patterns in `.pr_clean.yml`:

```yaml
disable_patterns:
  - unsolicited_tips_header
  - github_actions_bot_footer
  - bot_do_not_edit_comment
```

### Whitelist

Patterns will not be reported for whitelisted repositories or PR authors:

```yaml
whitelist:
  repos:
    - my-org/sandbox
    - my-org/docs
  authors:
    - dependabot[bot]
    - renovate[bot]
    - my-trusted-bot
```

---

## Built-in Pattern Registry

pr_clean ships with the following built-in patterns:

| Name | Severity | Category | Description |
|---|---|---|---|
| `copilot_agent_tips_block` | CRITICAL | copilot | Matches `START/END COPILOT CODING AGENT TIPS` blocks. |
| `copilot_tips_header` | HIGH | copilot | Matches bare `COPILOT CODING AGENT TIPS` heading lines. |
| `copilot_generated_marker` | HIGH | copilot | Matches `Generated by GitHub Copilot` attribution lines. |
| `copilot_summary_header` | HIGH | copilot | Matches `## Copilot Summary/Description/Analysis` headings. |
| `ai_generated_disclaimer` | MEDIUM | ai_disclaimer | Matches `AI-generated` or `auto-generated` disclaimer phrases. |
| `llm_agent_start_marker` | CRITICAL | agent_output | Matches `START AGENT/BOT/AI OUTPUT` block delimiters. |
| `llm_agent_end_marker` | HIGH | agent_output | Matches `END AGENT/BOT/AI OUTPUT` block delimiters. |
| `chatgpt_response_marker` | HIGH | ai_disclaimer | Matches `As an AI language model` and similar GPT preamble. |
| `promotional_upgrade_cta` | HIGH | promotional | Matches unsolicited upgrade/upsell calls-to-action. |
| `promotional_powered_by` | MEDIUM | promotional | Matches `Powered by GitHub Copilot/OpenAI/Claude/…` footers. |
| `promotional_learn_more_link` | MEDIUM | promotional | Matches `Learn more about Copilot/AI features` links. |
| `copilot_workspace_block` | HIGH | copilot | Matches Copilot Workspace session artefact blocks. |
| `copilot_inline_suggestion_marker` | MEDIUM | copilot | Matches `<!-- copilot_suggestion … -->` HTML comment markers. |
| `auto_summary_section` | HIGH | bot_noise | Matches `## Auto-generated Summary/Changelog` headings. |
| `bot_do_not_edit_comment` | MEDIUM | bot_noise | Matches `DO NOT EDIT — auto-generated` banners. |
| `unsolicited_tips_header` | LOW | bot_noise | Matches unsolicited `## Tips and Tricks` headings. |
| `github_actions_bot_footer` | LOW | bot_noise | Matches `<sub>posted by GitHub Actions</sub>` footers. |
| `coderabbit_review_block` | MEDIUM | ai_review_bot | Matches CodeRabbit AI review summary blocks. |

To list patterns with filtering:

```bash
# All patterns
pr_clean patterns list

# Only Copilot patterns
pr_clean patterns list --category copilot

# Only high/critical patterns
pr_clean patterns list --severity high

# JSON output
pr_clean patterns list --format json
```

---

## Output Formats

### Table (default)

A Rich color-coded table is rendered to stdout showing:
- Match index
- Line range (e.g. `12` or `12-15`)
- Severity (color-coded: 🚨 CRITICAL / 🔴 HIGH / 🟡 MEDIUM / 🔵 LOW)
- Confidence score with a visual bar
- Pattern name and category
- Source label (body / comment)
- Truncated matched text preview

Example output:

```
╭──────────────────────────────────────────────────────────────────────────────╮
│          pr_clean — 3 matches found in body                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
  #  Lines   Severity            Confidence     Pattern                    ...
 ─────────────────────────────────────────────────────────────────────────────
  1  17-23   🚨 CRITICAL         ██████████ 100%  copilot_agent_tips_block  ...
  2  31      🔴 HIGH             █████████░  95%  copilot_generated_marker  ...
  3  37      🟡 MEDIUM           ████████░░  82%  promotional_powered_by    ...

Total: 3 matches  —  1 critical  1 high  1 medium
```

### JSON

Machine-readable JSON output for use in CI pipelines and scripting:

```json
{
  "match_count": 2,
  "matches": [
    {
      "pattern_name": "copilot_agent_tips_block",
      "matched_text": "START COPILOT CODING AGENT TIPS\n...",
      "line_start": 17,
      "line_end": 23,
      "char_start": 412,
      "char_end": 658,
      "severity": "critical",
      "confidence": 1.0,
      "source": "body",
      "category": "copilot",
      "description": "Matches the well-known 'START COPILOT CODING AGENT TIPS' block.",
      "context_lines": ["---", "START COPILOT CODING AGENT TIPS", "- Tip one"]
    }
  ]
}
```

---

## Development

### Prerequisites

- Python 3.9+
- [pip](https://pip.pypa.io/)

### Setup

```bash
git clone https://github.com/pr-clean/pr_clean.git
cd pr_clean
pip install -e ".[dev]"
```

### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=pr_clean --cov-report=term-missing

# Specific test file
pytest tests/test_scanner.py -v
```

### Project Structure

```
pr_clean/
├── __init__.py          # Package init; exposes Scanner and __version__
├── cli.py               # Click CLI entry point (scan, report, strip, patterns)
├── config.py            # Config loader (.pr_clean.yml parser and merger)
├── github_client.py     # PyGithub wrapper (fetch/update PR body and comments)
├── patterns.py          # Built-in injection pattern registry
├── reporter.py          # Rich-powered output formatter (table + JSON)
├── scanner.py           # Core scanning logic (ScanMatch, Scanner)
└── stripper.py          # Injection block removal (Stripper, strip_matches)
tests/
├── fixtures/
│   └── sample_pr_body.md  # Sample PR markdown with injection patterns
├── test_config.py
├── test_patterns.py
├── test_scanner.py
└── test_stripper.py
action.yml               # GitHub Actions composite action definition
.pr_clean.yml.example    # Example configuration file
pyproject.toml           # Project metadata and dependencies
README.md                # This file
```

### Adding a New Pattern

1. Open `pr_clean/patterns.py`.
2. Add a new `InjectionPattern` entry to the `BUILTIN_PATTERNS` list.
3. Write a test in `tests/test_patterns.py` that verifies the pattern matches expected text.
4. Run the test suite to confirm there are no regressions.

Example:

```python
InjectionPattern(
    name="my_new_pattern",
    description="Matches MyBot output blocks.",
    regex=_compile(r"START MYBOT OUTPUT.*?END MYBOT OUTPUT", re.DOTALL),
    severity=Severity.HIGH,
    confidence=0.95,
    category="mybot",
    strip_full_block=True,
    tags=["mybot", "block"],
),
```

---

## Contributing

Contributions are welcome!  Please:

1. Fork the repository.
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Write tests for any new functionality.
4. Ensure all tests pass: `pytest`
5. Open a pull request with a clear description of the change.

Please keep PR descriptions clean and human-authored 😉

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Acknowledgements

- [Rich](https://github.com/Textualize/rich) for beautiful terminal output.
- [PyGithub](https://github.com/PyGithub/PyGithub) for GitHub API access.
- [Click](https://click.palletsprojects.com/) for the CLI framework.
- [PyYAML](https://pyyaml.org/) for configuration parsing.
