"""Platform-neutrality and repository-boundary tests for the coupling package.

Node IN383-PLAT01 is a *platform* mechanism, so two boundaries are machine-checked:

  C8  no domain vocabulary in ``tools/coupling`` or its tests (the lexicon that
      defines "domain vocabulary" is the existing platform configuration file,
      reused rather than duplicated here);
  boundary  no imports of domain repositories/packages and no heavy framework
      imports —— only the standard library plus numpy/scipy.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.evidence_domain_scan import load_lexicon, scan_paths

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tools" / "coupling"
TEST_FILES = [
    ROOT / "tests" / "test_coupling_remap.py",
    ROOT / "tests" / "test_coupling_clock.py",
    ROOT / "tests" / "test_coupling_twin.py",
    ROOT / "tests" / "test_coupling_platform_neutrality.py",
    ROOT / "tests" / "test_multiphysics_coupling.py",
]

# core modules: what a solver loop may depend on
ALLOWED_TOP_LEVEL = {
    "__future__", "dataclasses", "enum", "hashlib", "json", "math", "typing", "collections",
    "functools", "itertools", "abc", "numpy", "scipy", "os", "sys", "pathlib",
    "ast", "pytest", "tools",
}
# CLI/reporting helpers may additionally use a CLI parser and the plotting stack
TOOLING_MODULES = {"acceptance.py", "figures.py", "report.py", "multiphysics_figures.py"}
ALLOWED_TOOLING_EXTRA = {"argparse", "matplotlib", "base64"}


def _py_files():
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, "coupling package has no modules"
    return files


def test_domain_vocabulary_scan_is_clean():
    """C8: zero domain-word hits over the package and its tests."""
    lexicon = load_lexicon()
    targets = [str(p.relative_to(ROOT)) for p in _py_files()] + [
        str(p.relative_to(ROOT)) for p in TEST_FILES if p.exists()
    ]
    result = scan_paths(targets, lexicon=lexicon, root=ROOT)
    assert result["scanned_files"] >= len(targets) - 1
    assert result["hits"] == [], result["hits"]


def test_package_is_import_graph_clean():
    """Only stdlib + numpy/scipy (+ this repo's own tools.*) may be imported."""
    offenders = []
    for path in _py_files():
        allowed = ALLOWED_TOP_LEVEL | (ALLOWED_TOOLING_EXTRA if path.name in TOOLING_MODULES else set())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in allowed:
                        offenders.append((path.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level:            # relative import inside the package
                    continue
                top = (node.module or "").split(".")[0]
                if top not in allowed:
                    offenders.append((path.name, node.module))
    assert offenders == [], offenders


def test_no_backend_or_framework_dependency():
    """The package must not drag the API layer or a web framework into a solver loop."""
    banned = ("backend", "fastapi", "sqlalchemy", "torch", "tensorflow", "requests")
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert f"import {token}" not in text
            assert f"from {token}" not in text


def test_public_api_is_exported_and_documented():
    import tools.coupling as coupling

    for name in ("remap", "check", "measure", "run_twin", "ClockContract",
                 "TimeSeriesExchange", "ExchangeLedger", "BoxMesh", "Field"):
        assert hasattr(coupling, name), name
    assert coupling.__doc__ and "Domain" in coupling.__doc__


@pytest.mark.parametrize(
    "module",
    ["mesh", "remap", "conservation", "clock", "twin", "provenance",
     "multiphysics", "multiphysics_figures"],
)
def test_each_module_has_a_module_docstring(module):
    import importlib

    mod = importlib.import_module(f"tools.coupling.{module}")
    assert mod.__doc__ and len(mod.__doc__) > 100