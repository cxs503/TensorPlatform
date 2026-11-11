"""判据层单元测试：阈值、不可验证、白名单、健康度统计。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.capability_drift import detector, model
from tools.capability_drift.detector import CaseRun, Gate, evaluate, evaluate_capability
from tools.capability_drift.tests.conftest import write_waiver


def _cap(rel_tol: float = 0.05) -> model.Capability:
    return model.Capability(
        id="synth.solver.m",
        solver="synth",
        cases=(model.Case(id="c1", node="verif/t.py::test_m", metrics=("m",)),),
        metrics=("m",),
        gates=(Gate("m", rel_tol=rel_tol, abs_floor=1e-12),),
    )


def _run(value: float, status: str = "passed") -> dict:
    return {"c1": CaseRun("c1", "verif/t.py::test_m", status, metrics={"m": value})}


def test_within_tolerance_is_green():
    v = evaluate_capability(_cap(), _run(1.02), {"m": 1.0}, ())
    assert v.status == detector.GREEN
    assert v.comparisons[0].rel_err == pytest.approx(0.02)


def test_three_percent_is_not_red():
    v = evaluate_capability(_cap(), _run(1.03), {"m": 1.0}, ())
    assert v.status == detector.GREEN
    assert v.comparisons[0].status == detector.COMPARISON_OK


def test_six_percent_is_red():
    v = evaluate_capability(_cap(), _run(1.06), {"m": 1.0}, ())
    assert v.status == detector.RED
    assert v.comparisons[0].rel_err == pytest.approx(0.06)
    assert v.drifted == ["m"]


def test_threshold_is_adjustable():
    strict = evaluate_capability(_cap(0.02), _run(1.03), {"m": 1.0}, ())
    relaxed = evaluate_capability(_cap(0.10), _run(1.06), {"m": 1.0}, ())
    assert strict.status == detector.RED
    assert relaxed.status == detector.GREEN


def test_threshold_scale_parameter():
    v = evaluate_capability(_cap(0.05), _run(1.06), {"m": 1.0}, (), threshold_scale=2.0)
    assert v.comparisons[0].threshold == pytest.approx(0.10)
    assert v.status == detector.GREEN


def test_missing_metric_is_yellow_not_green():
    runs = {"c1": CaseRun("c1", "verif/t.py::test_m", "passed", metrics={})}
    v = evaluate_capability(_cap(), runs, {"m": 1.0}, ())
    assert v.status == detector.YELLOW
    assert v.comparisons[0].status == detector.COMPARISON_UNVERIFIABLE


def test_failed_case_is_yellow():
    v = evaluate_capability(_cap(), _run(1.0, status="failed"), {"m": 1.0}, ())
    assert v.status == detector.YELLOW


def test_no_baseline_is_yellow_and_not_alarm():
    v = evaluate_capability(_cap(), _run(1.0), {}, ())
    assert v.status == detector.YELLOW
    assert v.comparisons[0].status == detector.COMPARISON_NO_BASELINE
    assert v.drifted == []


def test_zero_baseline_uses_abs_floor():
    v = evaluate_capability(_cap(), _run(1e-9), {"m": 0.0}, ())
    assert v.comparisons[0].rel_err == pytest.approx(1e-9 / 1e-12)


def test_report_health_counts():
    cap2 = replace(_cap(), id="synth.solver.n",
                   cases=(model.Case(id="c2", node="verif/t.py::test_n", metrics=("m",)),))
    bundle = model.Bundle(
        root=model.Path("."), repo_root=model.Path("."),
        capabilities=(_cap(), cap2), baseline={"synth.solver.m": {"m": 1.0},
                                               "synth.solver.n": {"m": 1.0}},
        whitelist=(), file_sha256={},
    )
    runs = {"synth.solver.m": _run(1.06), "synth.solver.n": _run(1.01)}
    rep = evaluate(bundle, runs, scope=["synth.solver.m", "synth.solver.n"])
    health = rep.health()
    assert health["red"] == 1 and health["green"] == 1
    assert health["status"] == detector.RED
    assert rep.drifted_capabilities() == ["synth.solver.m"]


def test_signed_waiver_suppresses_red(calibrated, key_env):
    sb, values = calibrated
    cap_id = sb.capabilities[0].id
    metric = "energy_00"
    cap = model.load_bundle(sb.registry_dir).capability(cap_id)
    bundle = model.load_bundle(sb.registry_dir)
    runs = {"c1": CaseRun(cap.cases[0].id, cap.cases[0].node, "passed",
                          metrics={metric: values[cap_id][metric] * 1.20})}
    red = evaluate(bundle, {cap_id: runs}, scope=[cap_id])
    assert red.red, "without waiver the drift must be red"

    write_waiver(sb, key_env, cap_id, metric)
    bundle2 = model.load_bundle(sb.registry_dir)
    waived = evaluate(bundle2, {cap_id: runs}, scope=[cap_id], approval_key=key_env)
    assert not waived.red
    assert waived.verdicts[0].waived == [metric]
    assert waived.verdicts[0].comparisons[0].status == detector.COMPARISON_WAIVED


def test_forged_waiver_signature_still_red(calibrated, key_env):
    sb, values = calibrated
    cap_id = sb.capabilities[0].id
    metric = "energy_00"
    bundle = model.load_bundle(sb.registry_dir)
    cap = bundle.capability(cap_id)
    runs = {cap_id: {"c1": CaseRun(cap.cases[0].id, cap.cases[0].node, "passed",
                                   metrics={metric: values[cap_id][metric] * 1.20})}}
    write_waiver(sb, key_env, cap_id, metric, signature="0" * 64)
    bundle2 = model.load_bundle(sb.registry_dir)
    rep = evaluate(bundle2, runs, scope=[cap_id], approval_key=key_env)
    assert rep.red


def test_waiver_with_too_small_ceiling_still_red(calibrated, key_env):
    sb, values = calibrated
    cap_id = sb.capabilities[0].id
    metric = "energy_00"
    bundle = model.load_bundle(sb.registry_dir)
    cap = bundle.capability(cap_id)
    runs = {cap_id: {"c1": CaseRun(cap.cases[0].id, cap.cases[0].node, "passed",
                                   metrics={metric: values[cap_id][metric] * 1.20})}}
    write_waiver(sb, key_env, cap_id, metric, max_rel_err=0.05)
    bundle2 = model.load_bundle(sb.registry_dir)
    assert evaluate(bundle2, runs, scope=[cap_id], approval_key=key_env).red