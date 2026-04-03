"""Test suite for pr_clean.

This package contains unit and integration tests organised by the module they cover:

- ``test_scanner``   - Tests for the core scanning logic in ``pr_clean.scanner``.
- ``test_stripper``  - Tests for injection-block removal in ``pr_clean.stripper``.
- ``test_config``    - Tests for config loading and merging in ``pr_clean.config``.

Fixtures (sample markdown, YAML configs, etc.) live under ``tests/fixtures/``.

Run the full suite with::

    pytest

or with coverage::

    pytest --cov=pr_clean --cov-report=term-missing
"""
