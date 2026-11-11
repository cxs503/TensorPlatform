"""案例重跑器与指标插件单元测试。"""
from __future__ import annotations

from pathlib import Path

from tools.capability_drift import model, runner, sandbox


def test_runner_reads_metrics_from_sandbox_case(sandbox2):
    cap = sandbox2.capabilities[0]
    case = model.Case(id="c0", node=cap.case_node, metrics=tuple(cap.metrics))
    run = runner.run_case(case, sandbox2.repo)
    assert run.ok, run.stdout_tail
    assert "energy_00" in run.metrics and "peak_00" in run.metrics
    assert run.metrics["energy_00"] > 0.0
    assert run.duration_s > 0


def test_runner_reports_failed_case(sandbox2):
    bad = sandbox2.repo / "verif" / "test_fails.py"
    bad.write_text(
        "from tools.capability_drift.plugin import record\n"
        "def test_bad():\n    record('m', 1.0)\n    assert False, 'boom'\n",
        encoding="utf-8",
    )
    run = runner.run_case(model.Case(id="bad", node="verif/test_fails.py::test_bad"), sandbox2.repo)
    assert run.status == runner.FAILED
    assert run.returncode == 1


def test_runner_reports_timeout(sandbox2):
    slow = sandbox2.repo / "verif" / "test_slow.py"
    slow.write_text(
        "import time\nfrom tools.capability_drift.plugin import record\n"
        "def test_slow():\n    time.sleep(5)\n    record('m', 1.0)\n",
        encoding="utf-8",
    )
    run = runner.run_case(
        model.Case(id="slow", node="verif/test_slow.py::test_slow"), sandbox2.repo, timeout=1.0
    )
    assert run.status == runner.TIMEOUT


def test_case_without_metrics_is_error(sandbox2):
    empty = sandbox2.repo / "verif" / "test_empty.py"
    empty.write_text("def test_empty():\n    assert True\n", encoding="utf-8")
    run = runner.run_case(model.Case(id="e", node="verif/test_empty.py::test_empty"), sandbox2.repo)
    assert run.status == runner.ERROR
    assert "no metrics" in run.error


def test_run_cases_parallel_returns_all(sandbox2):
    bundle = model.load_bundle(sandbox2.registry_dir)
    cases = [c for cap in bundle.capabilities for c in cap.cases]
    runs = runner.run_cases(cases, sandbox2.repo, workers=2)
    assert len(runs) == len(cases)
    assert all(r.ok for r in runs.values())


def test_plugin_rejects_non_finite():
    from tools.capability_drift import plugin

    try:
        plugin.record("bad", float("nan"))
    except ValueError as exc:
        assert "not finite" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("NaN must be rejected")