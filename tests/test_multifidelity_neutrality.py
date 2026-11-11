"""Platform-neutrality and dependency-boundary tests for the multi-fidelity package.

The package is a *platform* mechanism: solver repositories import it, so it may

  * not contain domain vocabulary (the lexicon is the platform's existing
    configuration file, reused rather than duplicated here);
  * import only the standard library, numpy (and matplotlib in the figure
    module, which never runs inside a solver loop);
  * not reach into any domain repository.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tools.evidence_domain_scan import load_lexicon, scan_paths

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "multifidelity"
TOOLING = ROOT / "tools" / "multifidelity_maturity"
TEST_FILES = sorted(str(p) for p in (ROOT / "tests").glob("test_multifidelity_*.py"))

CORE_ALLOWED = {
    "__future__", "dataclasses", "enum", "fractions", "hashlib", "json", "math",
    "time", "typing", "collections", "functools", "itertools", "abc", "numpy",
    "os", "sys", "pathlib",
}
TOOLING_ALLOWED = CORE_ALLOWED | {
    "argparse", "subprocess", "matplotlib", "src", "tools",
}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # relative import inside the package
                continue
            if node.module:
                yield node.module.split(".")[0]


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_core_package_imports_are_light(path):
    allowed = CORE_ALLOWED | {"fractions", "multifidelity"}
    for name in _imports(path):
        assert name in allowed, f"{path.name} imports {name}"


@pytest.mark.parametrize("path", sorted(TOOLING.glob("*.py")), ids=lambda p: p.name)
def test_tooling_imports_are_bounded(path):
    for name in _imports(path):
        assert name in TOOLING_ALLOWED, f"{path.name} imports {name}"


def test_no_domain_vocabulary_in_package_tooling_or_tests():
    lexicon = load_lexicon()
    targets = [str(PACKAGE), str(TOOLING), *TEST_FILES]
    report = scan_paths(targets, lexicon=lexicon, root=ROOT)
    assert report["hits"] == [], f"domain vocabulary in a platform module: {report['hits'][:5]}"
    assert report["scanned_files"] > 0


def test_package_imports_without_pulling_a_solver():
    before = set(sys.modules)
    import src.multifidelity  # noqa: F401
    new = {m.split(".")[0] for m in set(sys.modules) - before}
    forbidden = {"matplotlib", "torch", "tensorflow", "jax"}
    assert not (new & forbidden), new & forbidden


def test_public_api_is_exported():
    import src.multifidelity as mf
    for name in ("GlobalClockManager", "Strategy", "SyncGrid", "Channel", "ChannelLedger",
                 "exchange_synced", "exchange_free_running", "interpolation_audit",
                 "DistillConfig", "StudentSurrogate", "HighFidelityTeacher",
                 "EmpiricalFormulaStudent", "pareto_sweep"):
        assert hasattr(mf, name), name