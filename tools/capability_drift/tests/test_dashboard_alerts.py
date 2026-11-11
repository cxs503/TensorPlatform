"""仪表板/告警单元测试：健康度、历史、图、事件结构、离线投递。"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.capability_drift import alerts, dashboard, model
from tools.capability_drift.detector import CaseRun, Gate, evaluate


def _report():
    cap = model.Capability(
        id="synth.solver.m", solver="synth",
        cases=(model.Case(id="c1", node="verif/t.py::test_m", metrics=("m",)),),
        metrics=("m",), gates=(Gate("m", rel_tol=0.05),),
    )
    bundle = model.Bundle(
        root=Path("."), repo_root=Path("."), capabilities=(cap,),
        baseline={"synth.solver.m": {"m": 1.0}}, whitelist=(), file_sha256={},
    )
    runs = {"synth.solver.m": {"c1": CaseRun("c1", "verif/t.py::test_m", "passed",
                                             metrics={"m": 1.2})}}
    return evaluate(bundle, runs)


def test_health_summary_marks_red():
    h = dashboard.health_summary(_report())
    assert h["status"] == "red" and h["dashboard"] == "RED" and h["red"] == 1


def test_history_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "history.jsonl"
    rep = _report()
    dashboard.history_append(path, dashboard.history_record(rep, commit="abc", changed=["a.py"]))
    dashboard.history_append(path, dashboard.history_record(rep, commit="def"))
    rows = dashboard.history_load(path)
    assert len(rows) == 2
    assert rows[0]["commit"] == "abc"
    assert rows[0]["health"]["red"] == 1


def test_history_load_missing_file_is_empty(tmp_path):
    assert dashboard.history_load(tmp_path / "nope.jsonl") == []


def test_verdict_table_contains_numbers():
    md = dashboard.verdict_table_markdown(_report())
    assert "synth.solver.m" in md and "20.00 %" in md


def test_figures_are_written(tmp_path):
    rep = _report()
    p1 = dashboard.figure_drift_bars(rep, tmp_path / "drift.png")
    dashboard.history_append(tmp_path / "h.jsonl", dashboard.history_record(rep))
    p2 = dashboard.figure_health_history(dashboard.history_load(tmp_path / "h.jsonl"),
                                         tmp_path / "hist.png")
    run = CaseRun("c1", "verif/t.py::test_m", "passed", metrics={"m": 1.0}, duration_s=0.5)
    p3 = dashboard.figure_case_timings([run], tmp_path / "timing.png")
    for p in (p1, p2, p3):
        assert p and Path(p).stat().st_size > 1000


def test_figure_drift_bars_empty_report(tmp_path):
    empty = evaluate(model.Bundle(root=Path("."), repo_root=Path("."),
                                  capabilities=(), baseline={}, whitelist=(), file_sha256={}), {})
    assert dashboard.figure_drift_bars(empty, tmp_path / "x.png") is None


def test_event_carries_numbers_and_severity():
    rep = _report()
    event = alerts.build_event(rep, {"synth.solver.m": [{"commit": "a" * 40, "short": "a" * 10}]})
    assert event["event"] == alerts.EVENT_RED
    assert event["severity"] == "red"
    d = event["drifted"][0]
    assert d["baseline"] == pytest.approx(1.0)
    assert d["observed"] == pytest.approx(1.2)
    assert d["rel_err"] == pytest.approx(0.2)
    assert event["suspected_commits"] == ["a" * 40]


def test_event_green_when_no_drift():
    cap = model.Capability(id="synth.ok", solver="synth",
                           cases=(model.Case(id="c", node="v/t.py::t", metrics=("m",)),),
                           metrics=("m",), gates=(Gate("m"),))
    bundle = model.Bundle(root=Path("."), repo_root=Path("."), capabilities=(cap,),
                          baseline={"synth.ok": {"m": 1.0}}, whitelist=(), file_sha256={})
    rep = evaluate(bundle, {"synth.ok": {"c": CaseRun("c", "v/t.py::t", "passed",
                                                      metrics={"m": 1.01})}})
    event = alerts.build_event(rep)
    assert event["event"] == alerts.EVENT_GREEN
    assert event["severity"] == "green"


def test_emit_appends_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    event = alerts.build_event(_report())
    alerts.emit(event, path)
    alerts.emit(event, path)
    assert len(path.read_text().strip().splitlines()) == 2


def test_post_event_offline_is_recorded_not_raised(monkeypatch):
    monkeypatch.delenv("TENSOR_API_BASE", raising=False)
    out = alerts.post_event(alerts.build_event(_report()), base_url="")
    assert out["delivered"] is False
    assert "TENSOR_API_BASE" in out["error"]