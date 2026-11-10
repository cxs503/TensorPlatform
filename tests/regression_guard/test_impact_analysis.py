"""Analysis-level criteria (a), (b), (c), (e-negcontrol) + graph/scanner units."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.regression_guard.impact import ImpactAnalyzer, textual_import_edge
from tools.regression_guard.model import Change, ChangeManifest, TaskLedger
from tools.regression_guard.scanner import RepoGraph, path_matches, scan_file, module_key_from_path
from tools.regression_guard.scenarios import Scenario, evaluate_scenarios

from _fixtures import API_TESTS, CORE_TESTS, LEDGER


def _analyzer(graph, ledger, workspace) -> ImpactAnalyzer:
    return ImpactAnalyzer(ledger=ledger, graph=graph, root=workspace, test_roots=("tests",))


# --- criterion (a): solver-kernel change ------------------------------------
def test_solver_kernel_change_selects_all_dependent_tasks(graph, ledger, workspace):
    analyzer = _analyzer(graph, ledger, workspace)
    impact = analyzer.analyze(
        ChangeManifest(changes=[Change(path="pkgs/solver/kernel.py", kind="modified")])
    )

    assert len(impact.tasks) >= 3, f"expected >=3 dependent tasks, got {impact.tasks}"
    assert set(impact.tasks) == {"T-core-a", "T-core-b", "T-core-c"}
    # feature closure: feat.composite only depends on feat.solver_core
    assert "feat.composite" in impact.features
    # change classification comes from the injected ledger artifact kind
    assert impact.change_classes["modified:pkgs/solver/kernel.py"] == "solver_core"
    # every expected test selected exactly (no API test may be pulled in)
    expected = set(CORE_TESTS)
    assert expected <= set(impact.test_ids)
    assert not (set(API_TESTS) & set(impact.test_ids))
    # blast radius reported, not executed: n-integration and n-report lie downstream
    assert impact.nodes_at_risk == ["n-integration", "n-report"]
    assert all(sel.reasons for sel in impact.selections)


# --- criterion (b): platform API change -------------------------------------
def test_platform_api_change_selects_endpoint_tasks(graph, ledger, workspace):
    analyzer = _analyzer(graph, ledger, workspace)
    impact = analyzer.analyze(
        ChangeManifest(changes=[Change(path="pkgs/platform/api.py", kind="modified")])
    )
    assert set(impact.tasks) == {"T-api-a", "T-api-b", "T-api-c"}
    assert set(API_TESTS) <= set(impact.test_ids)
    assert not (set(CORE_TESTS) & set(impact.test_ids))
    assert impact.change_classes["modified:pkgs/platform/api.py"] == "platform_api"
    assert "n-integration" in impact.nodes_at_risk


# --- criterion (c): no change -> no test ------------------------------------
def test_empty_manifest_triggers_nothing(graph, ledger, workspace, guard_factory):
    guard = guard_factory(runner=None)
    report = guard.run(ChangeManifest(changes=[]), dry_run=True)
    assert report["selection"]["count"] == 0
    assert report["verdict"]["status"] == "no_dependents"
    assert report["verdict"]["exit_code"] == 0
    assert report["metrics"]["false_trigger_rate"] == 0.0


def test_untracked_change_matrix_false_trigger_rate_below_one_percent(graph, ledger, workspace):
    """200 changes to files no test observes: every one must select zero tests."""
    analyzer = _analyzer(graph, ledger, workspace)
    paths = [f"docs/note_{i}.md" for i in range(150)] + [
        f"artifacts/run_{i}.json" for i in range(50)
    ]
    scenarios = [
        Scenario(name=f"untracked-{i}", manifest=ChangeManifest(changes=[Change(path=p)]),
                 expected_tests=(), expect_no_dependents=True)
        for i, p in enumerate(paths)
    ]
    suite = evaluate_scenarios(analyzer, scenarios)
    assert suite["scenario_count"] == 200
    assert suite["selected_total"] == 0
    assert suite["false_trigger_rate"] == 0.0 < 0.01
    assert suite["negative_controls"]["all_exit_zero"] is True


# --- criterion (e): negative control ---------------------------------------
def test_negative_control_unreferenced_module(graph, ledger, workspace, guard_factory):
    guard = guard_factory(runner=None)
    report = guard.run(
        ChangeManifest(changes=[Change(path="pkgs/isolated/orphan.py")]), dry_run=True
    )
    assert report["selection"]["count"] == 0
    assert report["verdict"]["status"] == "no_dependents"
    assert report["verdict"]["exit_code"] == 0
    # no importer, no ledger artifact -> nothing selected, exit code 0 (negative control)
    assert report["impact"]["selections"] == []
    assert report["impact"]["change_classes"]["modified:pkgs/isolated/orphan.py"] == "import_only"


# --- symbol-level narrowing ------------------------------------------------
def test_symbol_narrowing_limits_import_selection(graph, ledger, workspace):
    """A symbol-scoped change only wakes importers of that symbol."""
    analyzer = _analyzer(graph, ledger, workspace)
    impact = analyzer.analyze(
        ChangeManifest(changes=[Change(path="pkgs/extra/helper.py", symbols=("fast_path",))])
    )
    assert impact.test_ids == ["tests/test_extra_fast.py"]
    whole = analyzer.analyze(
        ChangeManifest(changes=[Change(path="pkgs/extra/helper.py")])
    )
    assert set(whole.test_ids) == {"tests/test_extra_fast.py", "tests/test_extra_slow.py"}


def test_second_implementation_confirms_import_edges(graph, ledger, workspace):
    analyzer = _analyzer(graph, ledger, workspace)
    impact = analyzer.analyze(
        ChangeManifest(changes=[Change(path="pkgs/solver/kernel.py")])
    )
    import_reasons = [r for s in impact.selections for r in s.reasons if r.kind == "import"]
    assert import_reasons, "import closure produced no reasons"
    assert all("confirmed=true" in r.detail for r in import_reasons)
    # textual re-implementation agrees for every selected test file
    for sel in impact.selections:
        assert textual_import_edge(workspace, sel.test_id, "kernel")


# --- scanner units ---------------------------------------------------------
def test_scanner_resolves_imports_and_defs(workspace, graph):
    facts = graph.files["tests/test_core_b.py"]
    assert facts.imports == {"pkgs/solver/kernel.py": ("relax",)}
    kernel = graph.files["pkgs/solver/kernel.py"]
    assert set(kernel.defs) == {"SCHEME_VERSION", "relax", "damping"}
    assert module_key_from_path("pkgs/solver/kernel.py") == "pkgs.solver.kernel"


def test_import_path_chain_is_auditable(workspace, graph):
    chain = graph.import_path("tests/test_core_a.py", "pkgs/solver/kernel.py")
    assert chain == ["tests/test_core_a.py", "pkgs/solver/kernel.py"]


def test_path_matching_semantics():
    assert path_matches("pkgs/solver/kernel.py", "pkgs/solver/kernel.py")
    assert path_matches("pkgs/solver", "pkgs/solver/kernel.py")
    assert path_matches("pkgs/**/*.py", "pkgs/solver/kernel.py")
    assert not path_matches("pkgs/other", "pkgs/solver/kernel.py")
    assert not path_matches("pkgs/*.py", "pkgs/solver/kernel.py")  # `*` does not cross `/`


def test_ledger_validation_rejects_dangling_references():
    broken = json.loads(json.dumps(LEDGER))
    broken["tasks"].append({"task_id": "T-bad", "feature_ids": ["feat.ghost"],
                            "tests": ["tests/test_x.py"]})
    problems = TaskLedger.from_dict(broken).validate()
    assert any("feat.ghost" in p for p in problems)


def test_feature_ledger_rows_are_accepted(ledger):
    rows = [
        {"item_type": "artifact", "payload": json.dumps(a)} for a in LEDGER["artifacts"]
    ] + [
        {"item_type": "feature", "payload": f} for f in LEDGER["features"]
    ] + [
        {"item_type": "task", "payload": t} for t in LEDGER["tasks"]
    ] + [
        {"item_type": "campaign_node", "payload": n} for n in LEDGER["nodes"]
    ] + [{"item_type": "unrelated", "payload": {"x": 1}}]
    reconstructed = TaskLedger.from_feature_ledger_rows(rows)
    assert reconstructed.digest == ledger.digest
    assert reconstructed.validate() == []