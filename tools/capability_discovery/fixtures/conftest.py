"""Collection guard for the synthetic fixture suites.

These are **inputs** for ``tools.capability_discovery``, not tests of this
repository: some of them are deliberately failing / skipped / xfail cases, and
fixture suites are meant to be copied to a temporary directory by
``tests/test_capability_discovery_plugin.py`` before being executed under
pytest.  Keeping them out of the repository-wide ``pytest tests/`` collection
prevents the fixture outcomes from being mistaken for platform test results.
"""

collect_ignore_glob = ["*"]