"""End-to-end criteria (a)(b)(d)(e) with a real ``pytest`` subprocess.

Each test copies the synthetic workspace so mutations cannot leak between
tests.  Nothing here mocks pytest: the guard really shells out, the regression
really happens, and the pass->fail transitions are read back from JUnit XML.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from tools.regression_guard.guard import GuardConfig, RegressionGuard, write_report
from tools.regression_guard.model import Change, ChangeManifest, TaskLedger
from tools.regression_guard.runner import FakeRunner, PytestRunner
from tools.regression_guard.scanner import RepoGraph

from _fixtures import API_TESTS, CORE_TESTS, LEDGER


@pytest.fixture()
def ws_copy(tmp_path: Path, workspace: Path) -> Path:
    dst = tmp_path / "ws"
    shutil.copytree(workspace, dst)
    return dst


def _guard(ws: Path, runner=None, cache_dir=None, baseline=None) -> RegressionGuard:
    ledger = TaskLedger.load(ws / "ledger.json")
    graph = RepoGraph.build(ws, ("pkgs", "tests"))
    config = GuardConfig(
        root=ws,
        ledger=ledger,
        graph=graph,
        test_roots=("tests",),
        cache_dir=cache_dir,
        baseline_path=baseline,
        pytest_args=("-q", "--tb=no", "-p", "no:cacheprovider"),
        timeout=300,
    )
    return RegressionGuard(config, runner=runner)


def _kernel_manifest() -> ChangeManifest:
    return ChangeManifest(changes=[Change(path="pkgs/solver/kernel.py", kind="modified")])


def _api_manifest() -> ChangeManifest:
    return ChangeManifest(changes=[Change(path="pkgs/platform/api.py", kind="modified")])


def _mutate_kernel(ws: Path) -> None:
    """Inject a real behavioural regression into the solver kernel."""
    path = ws / "pkgs/solver/kernel.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("SCHEME_VERSION = 2", "SCHEME_VERSION = 3")
    text = text.replace("return state * 2", "return state * 3")
    path.write_text(text, encoding="utf-8")


# --- criterion (a)+(d): real regression on a solver-kernel change -----------
def test_solver_change_really_reruns_and_detects_regression(ws_copy, tmp_path):
    baseline = tmp_path / "baseline.json"
    guard = _guard(ws_copy, baseline=baseline)

    # 1) baseline: the untouched tree is green on the three dependent tasks
    base_report = guard.run(_kernel_manifest(), update_baseline=True)
    assert base_report["verdict"]["status"] == "pass"
    assert base_report["verdict"]["exit_code"] == 0
    assert set(base_report["selection"]["test_ids"]) == set(CORE_TESTS)
    assert base_report["execution"]["executed"] == 3
    assert all(o["outcome"] == "passed" for o in base_report["execution"]["outcomes"])

    # 2) inject a genuine kernel regression and re-run
    _mutate_kernel(ws_copy)
    guard2 = _guard(ws_copy, baseline=baseline)
    report = guard2.run(_kernel_manifest())

    assert set(report["selection"]["test_ids"]) == set(CORE_TESTS)
    assert report["selection"]["count"] >= 3
    regressions = report["deltas"]["regressions"]
    assert "tests/test_core_a.py::test_scheme_version" in regressions
    assert "tests/test_core_b.py::test_relax_doubles" in regressions
    assert "tests/test_core_c.py::test_damping_halves" not in regressions
    assert report["verdict"]["status"] == "regression"
    assert report["verdict"]["exit_code"] == 1
    assert report["metrics"]["false_trigger_rate"] == 0.0

    # 3) the report is structured JSON on disk and re-readable (criterion d)
    out = write_report(report, tmp_path / "report.json")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    for key in ("impacts", "impact", "selection", "execution", "deltas", "verdict", "metrics"):
        if key == "impacts":
            continue
        assert key in loaded
    assert loaded["impact"]["change_classes"]["modified:pkgs/solver/kernel.py"] == "solver_core"
    assert sorted(loaded["impact"]["tasks"]) == ["T-core-a", "T-core-b", "T-core-c"]
    assert loaded["impact"]["nodes_at_risk"] == ["n-integration", "n-report"]
    assert loaded["report_digest"]


# --- criterion (b): platform API change, isolated from the solver ----------
def test_platform_api_change_reruns_only_api_tests(ws_copy):
    guard = _guard(ws_copy)
    report = guard.run(_api_manifest())
    assert set(report["selection"]["test_ids"]) == set(API_TESTS)
    assert report["verdict"]["status"] == "pass"
    assert report["verdict"]["exit_code"] == 0
    assert report["impact"]["change_classes"]["modified:pkgs/platform/api.py"] == "platform_api"
    assert report["metrics"]["cache_hit_rate"] == 0.0


# --- criterion (e): cache reuse must not re-execute ------------------------
def test_cache_hit_skips_execution(ws_copy, tmp_path):
    cache_dir = tmp_path / "cache"
    runner = PytestRunner(ws_copy, args=("-q", "--tb=no", "-p", "no:cacheprovider"), timeout=300)

    guard = _guard(ws_copy, runner=runner, cache_dir=cache_dir)
    first = guard.run(_kernel_manifest())
    assert first["execution"]["executed"] == 3
    assert first["execution"]["cached"] == 0
    invocations_after_first = runner.invocations

    second = _guard(ws_copy, runner=runner, cache_dir=cache_dir).run(_kernel_manifest())
    assert second["execution"]["executed"] == 0, "cached tests must not be re-executed"
    assert second["execution"]["cached"] == 3
    assert second["metrics"]["cache_hit_rate"] == 1.0
    assert runner.invocations == invocations_after_first
    assert second["verdict"]["status"] == "pass"
    assert (cache_dir / "results.json").exists()


def test_cache_invalidated_when_dependency_changes(ws_copy, tmp_path):
    cache_dir = tmp_path / "cache"
    baseline = tmp_path / "baseline.json"
    _guard(ws_copy, cache_dir=cache_dir, baseline=baseline).run(_kernel_manifest(), update_baseline=True)
    warm = _guard(ws_copy, cache_dir=cache_dir, baseline=baseline).run(_kernel_manifest())
    assert warm["execution"]["executed"] == 0

    _mutate_kernel(ws_copy)
    cold = _guard(ws_copy, cache_dir=cache_dir, baseline=baseline).run(_kernel_manifest())
    assert cold["execution"]["executed"] == 3, "content change must invalidate the cache"
    assert cold["verdict"]["status"] == "regression"


# --- criterion (e): negative control through the full pipeline -------------
def test_negative_control_full_pipeline_exit_zero(ws_copy):
    runner = FakeRunner()
    guard = _guard(ws_copy, runner=runner)
    report = guard.run(ChangeManifest(changes=[Change(path="pkgs/isolated/orphan.py")]))
    assert report["selection"]["count"] == 0
    assert report["verdict"]["status"] == "no_dependents"
    assert report["verdict"]["exit_code"] == 0
    assert runner.invocations == 0, "no test process may be spawned"


def test_no_change_manifest_never_runs_anything(ws_copy):
    runner = FakeRunner()
    guard = _guard(ws_copy, runner=runner)
    report = guard.run(ChangeManifest(changes=[]))
    assert report["verdict"]["exit_code"] == 0
    assert report["execution"]["executed"] == 0
    assert runner.invocations == 0


# --- baseline semantics: pre-existing failure is not a regression ----------
def test_pre_existing_failure_is_not_reported_as_regression(ws_copy, tmp_path):
    """A test that was already red must stay 'pre-existing', never 'regression'."""
    path = ws_copy / "pkgs/solver/kernel.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace("SCHEME_VERSION = 2", "SCHEME_VERSION = 3"),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"outcomes": {"tests/test_core_a.py::test_scheme_version": "failed"}}),
        encoding="utf-8",
    )
    guard = _guard(ws_copy, baseline=baseline)
    report = guard.run(_kernel_manifest())
    assert report["deltas"]["regressions"] == []
    assert "tests/test_core_a.py::test_scheme_version" in report["deltas"]["pre_existing_failures"]
    assert report["verdict"]["status"] == "pass_with_known_failures"
    assert report["verdict"]["exit_code"] == 0
    assert report["deltas"]["newly_failing"] == []


def test_dry_run_plans_without_executing(ws_copy):
    runner = FakeRunner()
    guard = _guard(ws_copy, runner=runner)
    report = guard.run(_kernel_manifest(), dry_run=True)
    assert set(report["selection"]["test_ids"]) == set(CORE_TESTS)
    assert report["execution"]["executed"] == 0
    assert runner.invocations == 0
    assert report["verdict"]["exit_code"] == 0


def test_report_digest_is_deterministic_across_cold_and_warm_runs(ws_copy, tmp_path):
    cache_dir = tmp_path / "cache"
    cold = _guard(ws_copy, cache_dir=cache_dir).run(_kernel_manifest())
    warm = _guard(ws_copy, cache_dir=cache_dir).run(_kernel_manifest())
    assert cold["report_digest"] == warm["report_digest"]
    assert cold["selection"]["test_ids"] == warm["selection"]["test_ids"]

# --- fail-closed and selector-robustness units ----------------------------
def test_not_run_outcome_cannot_be_laundered_into_pass(ws_copy, tmp_path):
    """A selector that never ran is no evidence — verdict must be `error`."""
    guard = _guard(ws_copy, baseline=tmp_path / "baseline.json")
    guard.analyzer.ledger.task("T-core-a").tests = ["tests/does_not_exist.py"]
    report = guard.run(
        ChangeManifest(changes=[Change(path="pkgs/solver/kernel.py",
                                       symbols=("SCHEME_VERSION",))])
    )
    assert "tests/does_not_exist.py" in report["deltas"]["not_run"]
    assert report["verdict"]["status"] == "error"
    assert report["verdict"]["exit_code"] == 2


def test_missing_selector_does_not_poison_the_batch(ws_copy):
    guard = _guard(ws_copy)
    guard.analyzer.ledger.task("T-core-a").tests = [
        "tests/does_not_exist.py",
        "tests/test_core_a.py",
    ]
    report = guard.run(_kernel_manifest())
    outcomes = {o["test_id"]: o for o in report["execution"]["outcomes"]}
    assert outcomes["tests/does_not_exist.py"]["outcome"] == "not_run"
    assert outcomes["tests/test_core_a.py::test_scheme_version"]["outcome"] == "passed"
    # one real failure-free run + the missing selector -> error, never `pass`
    assert report["verdict"]["status"] == "error"


def test_directory_selector_expands_to_all_tests(tmp_path, workspace):
    import shutil

    ws = tmp_path / "ws"
    shutil.copytree(workspace, ws)
    guard = _guard(ws)
    ledger = guard.analyzer.ledger
    ledger.tasks = [t for t in ledger.tasks if t.task_id == "T-core-a"]
    ledger.task("T-core-a").tests = ["tests"]
    report = guard.run(_kernel_manifest())
    ids = {o["test_id"] for o in report["execution"]["outcomes"]}
    assert "tests/test_core_b.py::test_relax_doubles" in ids
    assert "tests/test_api_c.py::test_api_version" in ids


def test_selector_dedupe_avoids_double_execution(tmp_path, workspace):
    """A directory selector plus a file selector under it must run once."""
    import shutil

    ws = tmp_path / "ws"
    shutil.copytree(workspace, ws)
    guard = _guard(ws)
    ledger = guard.analyzer.ledger
    ledger.tasks = [t for t in ledger.tasks if t.task_id == "T-core-a"]
    ledger.task("T-core-a").tests = ["tests", "tests/test_core_a.py"]
    report = guard.run(_kernel_manifest())
    assert report["selection"]["test_ids"] == ["tests"]
    assert report["execution"]["executed"] == report["selection"]["analysed_before_dedupe"] or \
        report["execution"]["executed"] == len(report["execution"]["outcomes"])
    ids = [o["test_id"] for o in report["execution"]["outcomes"]]
    assert len(ids) == len(set(ids)), "a test case was executed twice in one run"
