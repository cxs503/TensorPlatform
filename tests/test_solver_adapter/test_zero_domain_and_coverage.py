"""C5 (zero domain semantics), C1 (coverage) and repository-boundary tests.

The domain vocabulary is *not* redefined here: the scan reuses the platform's
existing lexicon (``tools.evidence_domain_scan.load_lexicon``), so "domain word"
means exactly what it means everywhere else on this platform.  A hit is a
failure, and the test prints the offending file/line/term.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.evidence_domain_scan import load_lexicon, scan_paths
from tools.solver_adapter import (
    NATIVE_FORMATS,
    SOLVER_IDS,
    CoverageError,
    IOFormat,
    formats_for,
    scan_coverage,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "tools" / "solver_adapter"
TEST_FILES = sorted((ROOT / "tests" / "test_solver_adapter").glob("*.py"))
DOC_FILES = sorted((ROOT / "docs" / "solver_adapter").glob("*.md"))

ALLOWED_TOP_LEVEL = {
    "__future__", "abc", "dataclasses", "enum", "functools", "json", "re", "typing",
    "hashlib", "math", "pathlib", "numpy", "scipy", "pytest", "tools", "jsonschema",
    "argparse", "base64", "sys", "os", "collections", "itertools", "textwrap",
    "subprocess",
}
#: the acceptance runner is a CLI/reporting helper, not a kernel module, so it may
#: also use the plotting stack (same exemption policy as tools/coupling)
TOOLING_MODULES = {"acceptance.py"}
TOOLING_EXTRA = {"matplotlib"}


def _package_files():
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, "solver_adapter package has no modules"
    return files


# ------------------------------------------------------------------ C5
def test_package_has_zero_domain_vocabulary():
    targets = [str(p.relative_to(ROOT)) for p in _package_files()]
    result = scan_paths(targets, lexicon=load_lexicon(), root=ROOT)
    assert result["hits"] == [], result["hits"]
    assert result["scanned_files"] >= 1


def test_contract_tests_have_zero_domain_vocabulary():
    targets = [str(p.relative_to(ROOT)) for p in TEST_FILES]
    result = scan_paths(targets, lexicon=load_lexicon(), root=ROOT)
    assert result["hits"] == [], result["hits"]


def test_docs_have_zero_domain_vocabulary():
    targets = [str(p.relative_to(ROOT)) for p in DOC_FILES]
    if not targets:
        pytest.skip("protocol docs not written yet")
    result = scan_paths(targets, lexicon=load_lexicon(), root=ROOT)
    assert result["hits"] == [], result["hits"]


def test_schema_artifact_has_zero_domain_vocabulary():
    schema = ROOT / "schemas" / "solver_protocol_v1.json"
    result = scan_paths([str(schema.relative_to(ROOT))], lexicon=load_lexicon(), root=ROOT)
    assert result["hits"] == [], result["hits"]


@pytest.mark.parametrize("path", _package_files(), ids=lambda p: p.name)
def test_module_imports_stay_inside_the_allowed_layer(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed = ALLOWED_TOP_LEVEL | (TOOLING_EXTRA if path.name in TOOLING_MODULES else set())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in allowed:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            module = (node.module or "").split(".")[0]
            if module and module not in allowed:
                offenders.append(node.module)
    assert offenders == [], f"{path.name} imports outside the allowed layer: {offenders}"


@pytest.mark.parametrize("path", _package_files(), ids=lambda p: p.name)
def test_module_has_no_backend_or_framework_dependency(path):
    text = path.read_text(encoding="utf-8")
    for forbidden in ("import backend", "from backend", "flask", "fastapi", "sqlalchemy",
                      "torch", "TensorLBM", "TensorFEM"):
        assert forbidden not in text, f"{path.name} references {forbidden}"


def test_fixtures_use_synthetic_names():
    """Every solver id and field name comes from the protocol's own vocabulary."""
    from tools.solver_adapter.adapters import ADAPTER_CLASSES

    assert set(ADAPTER_CLASSES) == set(SOLVER_IDS)
    for solver_id, cls in ADAPTER_CLASSES.items():
        for spec in cls.FIELDS:
            assert spec.name.islower()
            assert spec.name.replace("_", "").isalnum()


# ------------------------------------------------------------------ C1
def test_coverage_is_complete_for_all_eight_solvers():
    report = scan_coverage()
    assert report["coverage"] == 1.0
    assert report["ok"] is True
    assert report["records_mapped"] == report["records_total"]
    assert report["problems"] == []
    assert set(report["solvers"]) == set(SOLVER_IDS)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_solver_declares_both_directions(solver_id):
    entries = formats_for(solver_id)
    directions = {e.direction for e in entries}
    assert {"input", "output"} <= directions
    report = scan_coverage()["per_solver"][solver_id]
    assert report["unmapped"] == 0
    assert report["mapped"] == report["records"]


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_native_record_maps_to_a_declared_protocol_field(solver_id):
    from tools.solver_adapter import adapter_class

    declared = {spec.name: spec for spec in adapter_class(solver_id).FIELDS}
    for entry in formats_for(solver_id):
        assert entry.protocol_field in declared, (solver_id, entry)
        assert tuple(entry.dim_vector) == declared[entry.protocol_field].dim_vector


def test_coverage_gate_can_fail_on_an_unmapped_record():
    broken = {**NATIVE_FORMATS}
    broken["lbm"] = tuple(NATIVE_FORMATS["lbm"]) + (
        IOFormat("lbm", "output", "mystery", "(n_cells,)", "float64", (1, -3, 0, 0), ""),
    )
    with pytest.raises(CoverageError):
        scan_coverage(broken)
    report = scan_coverage(broken, raise_on_missing=False)
    assert report["ok"] is False
    assert report["coverage"] < 1.0
    assert any("mystery" in p for p in report["problems"])


def test_coverage_gate_can_fail_on_dimensional_mismatch():
    broken = {**NATIVE_FORMATS}
    broken["fem"] = tuple(NATIVE_FORMATS["fem"]) + (
        IOFormat("fem", "output", "odd_units", "(n_cells,)", "float64", (2, -3, 0, 0), "density"),
    )
    report = scan_coverage(broken, raise_on_missing=False)
    assert report["ok"] is False
    assert any("dimensional mismatch" in p for p in report["problems"])


def test_coverage_gate_can_fail_on_a_missing_direction():
    broken = {**NATIVE_FORMATS}
    broken["cad"] = tuple(e for e in NATIVE_FORMATS["cad"] if e.direction == "output")
    report = scan_coverage(broken, raise_on_missing=False)
    assert report["ok"] is False
    assert any("no input records" in p for p in report["problems"])


def test_coverage_gate_can_fail_on_a_missing_solver():
    broken = {k: v for k, v in NATIVE_FORMATS.items() if k != "dg"}
    report = scan_coverage(broken, raise_on_missing=False)
    assert report["ok"] is False
    assert any(p.startswith("dg:") for p in report["problems"])


def test_coverage_report_states_provenance_honestly():
    report = scan_coverage()
    assert report["verified_against_repo"] == 0
    assert "verified_against_repo=0" in report["note"]