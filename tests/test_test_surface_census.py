"""Gate: keep silently dead test code out of the suite.

Companion test for ``tools/audit/test_surface_census.py``.  It has two jobs:

1. **negative controls** — synthesise one module per defect class and prove the
   detector flags it (a detector that never fires is itself a dead test);
2. **regression gate** — run the census over the real tree and fail when new
   test-surface debt appears that is not recorded in ``BASELINE`` below.

Baseline entries are *documented debts*: each carries the reason it is
tolerated.  Add an entry only after deciding the debt is intentional.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "audit"))

from test_surface_census import (  # noqa: E402
    check_against_baseline,
    census_file,
    collectible_test_symbols,
    debt_keys,
    has_oracle,
    run_census,
)

#: Documented, intentional test-surface debts (kind::path::symbol -> reason).
BASELINE_ENTRIES = {
    # The audit needs a live :8001 ship platform; skipping the module is the
    # documented behaviour when the platform is unreachable (env guard, not a
    # hidden failure).
    "module_level_skip::tests/test_intact_stability_platform_audit.py::<module>": (
        "environment guard: module skips itself when :8001 is unreachable"
    ),
    # Known physics gap tracked by m1797 (zero-frequency added mass deviates
    # -11%..-48% from the analytic reference).  xfail is deliberate and carries
    # its evidence in the docstring; it must flip to XPASS once the kernel is
    # fixed.
    "unconditional_skip::tests/verif/test_seakeeping_section_analytic.py::test_known_gap_zero_freq_added_mass_within_3pct": (
        "documented known gap (m1797), body is pytest.xfail with evidence"
    ),
    "assertion_free::tests/verif/test_seakeeping_section_analytic.py::test_known_gap_zero_freq_added_mass_within_3pct": (
        "same test: xfail body has no oracle by construction"
    ),
}


def _module(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _findings(tmp_path: Path, name: str, source: str):
    path = _module(tmp_path, name, source)
    return census_file(path, tmp_path)[3]


# --------------------------------------------------------------------------- #
# Negative controls: the detector must fire on each defect class
# --------------------------------------------------------------------------- #
def test_detects_vacuous_module(tmp_path):
    src = """
        import sys

        def check_everything():
            assert 1 + 1 == 2

        def main():
            check_everything()
            print("PASS")

        if __name__ == "__main__":
            sys.exit(main())
    """
    kinds = {f.kind for f in _findings(tmp_path, "test_vacuous.py", src)}
    assert "vacuous_module" in kinds


def test_detects_assertion_free_test(tmp_path):
    src = """
        def test_prints_only():
            print("all criteria PASSED")
    """
    kinds = {f.kind for f in _findings(tmp_path, "test_printer.py", src)}
    assert "assertion_free" in kinds


def test_detects_orphan_oracle(tmp_path):
    src = """
        def check_reference():
            assert 2 ** 10 == 1024

        def test_something_else():
            assert True
    """
    findings = _findings(tmp_path, "test_orphan.py", src)
    assert any(f.kind == "orphan_oracle" and f.symbol == "check_reference" for f in findings)


def test_detects_module_level_skip(tmp_path):
    src = """
        import pytest

        pytest.skip("platform down", allow_module_level=True)

        def test_never():
            assert False
    """
    kinds = {f.kind for f in _findings(tmp_path, "test_skipmod.py", src)}
    assert "module_level_skip" in kinds


def test_detects_import_side_effect(tmp_path):
    src = """
        import os

        os.environ["DATABASE_URL"] = "sqlite://"

        def test_x():
            assert True
    """
    kinds = {f.kind for f in _findings(tmp_path, "test_sideeffect.py", src)}
    assert "import_side_effect" in kinds


# --------------------------------------------------------------------------- #
# Positive controls: no false positives on legitimate patterns
# --------------------------------------------------------------------------- #
def test_clean_module_has_no_findings(tmp_path):
    src = """
        import pytest

        def helper():
            assert True

        def test_pass():
            helper()
            assert 1 + 1 == 2

        def test_raises():
            with pytest.raises(ValueError):
                int("nope")
    """
    assert _findings(tmp_path, "test_clean.py", src) == []


def test_unittest_subclass_is_recognised(tmp_path):
    src = """
        import unittest

        class MyChecks(unittest.TestCase):
            def test_it(self):
                self.assertEqual(1, 1)
    """
    path = _module(tmp_path, "test_unittest_style.py", src)
    is_test, symbols, _, findings = census_file(path, tmp_path)
    assert is_test == 1 and symbols == 1
    assert findings == []


def test_exception_oracle_is_recognised(tmp_path):
    src = """
        import json

        def test_serialisable():
            json.dumps({"a": 1})
    """
    kinds = {f.kind for f in _findings(tmp_path, "test_raise_oracle.py", src)}
    assert "assertion_free" not in kinds


def test_transitively_referenced_helper_is_not_orphan(tmp_path):
    src = """
        def leaf():
            assert True

        def middle():
            leaf()

        def test_top():
            middle()
    """
    assert _findings(tmp_path, "test_chain.py", src) == []


def test_script_entry_point_is_not_orphan(tmp_path):
    src = """
        def write_artifact():
            assert True

        def test_live():
            assert True

        if __name__ == "__main__":
            write_artifact()
    """
    assert _findings(tmp_path, "test_entry.py", src) == []


# --------------------------------------------------------------------------- #
# Regression gate on the real tree
# --------------------------------------------------------------------------- #
def test_tree_has_no_vacuous_test_modules():
    census = run_census([ROOT / "tests", ROOT / "tests_broken_ice"], ROOT)
    vacuous = [f for f in census.findings if f.kind == "vacuous_module"]
    assert not vacuous, "pytest collects these modules but they define zero tests: " + ", ".join(
        f.path for f in vacuous
    )


def test_tree_has_no_new_test_surface_debt():
    census = run_census([ROOT / "tests", ROOT / "tests_broken_ice"], ROOT)
    baseline = {"entries": [{"key": k, "reason": v} for k, v in BASELINE_ENTRIES.items()]}
    new_debt = check_against_baseline(census, baseline)
    assert not new_debt, "new test-surface debt (fix it or baseline it with a reason): " + ", ".join(new_debt)


def test_census_is_deterministic():
    first = run_census([ROOT / "tests"], ROOT)
    second = run_census([ROOT / "tests"], ROOT)
    assert first.test_functions == second.test_functions
    assert debt_keys(first.findings) == debt_keys(second.findings)


def test_class_methods_are_counted_by_the_collection_model(tmp_path):
    """Collection model must see class methods, not just module-level ``def test_``."""
    src = """
        class TestGroup:
            def test_one(self):
                assert True

            def test_two(self):
                assert True
    """
    path = _module(tmp_path, "test_class_style.py", src)
    _, symbols, _, findings = census_file(path, tmp_path)
    assert symbols == 2
    assert findings == []
