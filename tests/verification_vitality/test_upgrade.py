"""升账引擎测试：三条件齐升 REAL、任一不足 HOLD、幂等、回滚与回滚冲突。"""
from __future__ import annotations

import json

from verification_vitality.upgrade import (UpgradeCriteria, apply_upgrades, downgrade,
                                           evaluate_upgrades, extract_metrics, load_state,
                                           rollback, save_state)


def _cap(level="WITHHELD", **evidence):
    entry = {"id": "geo.volume", "claimed_level": level, "evidence": []}
    if evidence:
        entry["evidence"].append(evidence)
    return {"geo.volume": entry}


def _alive_report(*caps):
    return {"capabilities": [{"capability_id": c, "status": "alive"} for c in caps]}


def test_upgrade_requires_all_three_conditions():
    ledger = _cap()
    measured = {"geo.volume": {"pass_rate": 0.95, "benchmark_error_pct": 8.0}}
    decisions = evaluate_upgrades(ledger, patrol_report=_alive_report("geo.volume"),
                                  measured=measured)
    d = decisions[0]
    assert d["verdict"] == "UPGRADE" and d["proposed_level"] == "REAL"
    assert d["criteria"]["pass_rate"]["pass"] and d["criteria"]["benchmark_error_pct"]["pass"]
    assert d["criteria"]["surface_alive"]["pass"]


def test_low_pass_rate_holds():
    decisions = evaluate_upgrades(_cap(), patrol_report=_alive_report("geo.volume"),
                                  measured={"geo.volume": {"pass_rate": 0.85,
                                                           "benchmark_error_pct": 5.0}})
    assert decisions[0]["verdict"] == "HOLD"
    assert "pass_rate_below_bar" in decisions[0]["reasons"]
    assert decisions[0]["proposed_level"] == "WITHHELD"


def test_large_benchmark_error_holds():
    decisions = evaluate_upgrades(_cap(), patrol_report=_alive_report("geo.volume"),
                                  measured={"geo.volume": {"pass_rate": 1.0,
                                                           "benchmark_error_pct": 15.0}})
    assert decisions[0]["verdict"] == "HOLD"
    assert "benchmark_error_pct_below_bar" in decisions[0]["reasons"], "15% 是严格小于的门"


def test_dead_surface_blocks_upgrade_even_with_good_numbers():
    report = {"capabilities": [{"capability_id": "geo.volume", "status": "dead"}]}
    decisions = evaluate_upgrades(_cap(), patrol_report=report,
                                  measured={"geo.volume": {"pass_rate": 1.0,
                                                           "benchmark_error_pct": 1.0}})
    assert decisions[0]["verdict"] == "HOLD"
    assert "surface_alive_below_bar" in decisions[0]["reasons"]


def test_missing_evidence_holds_and_is_named():
    decisions = evaluate_upgrades(_cap(), patrol_report=_alive_report("geo.volume"))
    assert decisions[0]["verdict"] == "HOLD"
    assert "pass_rate_missing" in decisions[0]["reasons"]
    assert "benchmark_error_pct_missing" in decisions[0]["reasons"]
    assert decisions[0]["missing_evidence"] == ["pass_rate", "benchmark_error_pct"]


def test_already_real_is_noop():
    decisions = evaluate_upgrades(_cap("REAL"), patrol_report=_alive_report("geo.volume"))
    assert decisions[0]["verdict"] == "NOOP"
    assert decisions[0]["reasons"] == ["already_real"]


def test_extract_metrics_from_ledger_evidence_fields():
    entry = {"id": "x", "evidence": [{"pytest": {"passed": 45, "total": 50},
                                      "benchmark": {"error_pct": 7.5}}]}
    m = extract_metrics(entry)
    assert abs(m["pass_rate"] - 0.9) < 1e-9
    assert m["benchmark_error_pct"] == 7.5
    assert m["missing"] == []


def test_apply_is_idempotent_and_journaled(tmp_path):
    ledger = _cap()
    measured = {"geo.volume": {"pass_rate": 0.95, "benchmark_error_pct": 8.0}}
    decisions = evaluate_upgrades(ledger, patrol_report=_alive_report("geo.volume"),
                                  measured=measured)
    state = load_state(tmp_path / "state.json")
    journal = tmp_path / "journal.jsonl"
    first = apply_upgrades(decisions, state, journal_path=journal)
    assert first["n_applied"] == 1
    second = apply_upgrades(decisions, state, journal_path=journal)
    assert second["n_applied"] == 0, "同证据重复应用不得产生第二条流水"
    lines = journal.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["level_before"] == "WITHHELD" and event["level_after"] == "REAL"
    assert event["rollback_token"]


def test_rollback_restores_previous_level(tmp_path):
    ledger = _cap("MOCK")
    measured = {"geo.volume": {"pass_rate": 0.95, "benchmark_error_pct": 8.0}}
    decisions = evaluate_upgrades(ledger, patrol_report=_alive_report("geo.volume"),
                                 measured=measured)
    state = load_state(tmp_path / "state.json")
    journal = tmp_path / "journal.jsonl"
    apply_upgrades(decisions, state, journal_path=journal)
    token = state["entries"]["geo.volume"]["rollback_token"]
    res = rollback(state, "geo.volume", token=token, journal_path=journal)
    assert res["status"] == "ROLLED_BACK" and res["to"] == "MOCK"
    assert state["entries"]["geo.volume"]["level"] == "MOCK"
    assert json.loads(journal.read_text().strip().splitlines()[-1])["event"] == "rollback"


def test_rollback_conflict_on_token_mismatch(tmp_path):
    decisions = evaluate_upgrades(_cap(), patrol_report=_alive_report("geo.volume"),
                                 measured={"geo.volume": {"pass_rate": 1.0,
                                                          "benchmark_error_pct": 1.0}})
    state = load_state(tmp_path / "state.json")
    apply_upgrades(decisions, state, journal_path=tmp_path / "j.jsonl")
    res = rollback(state, "geo.volume", token="deadbeef")
    assert res["status"] == "ROLLBACK_CONFLICT"
    assert state["entries"]["geo.volume"]["level"] == "REAL", "冲突时不得改动记账"


def test_rollback_untracked_capability(tmp_path):
    state = load_state(tmp_path / "state.json")
    assert rollback(state, "never.seen")["status"] == "NOT_TRACKED"


def test_downgrade_from_cross_suggestions(tmp_path):
    suggestions = [{"capability_id": "geo.volume", "action": "DOWNGRADE", "from": "REAL",
                    "to": "WITHHELD", "policy": "fail-closed", "evidence": {"dead_units": ["t"]}}]
    state = load_state(tmp_path / "state.json")
    res = downgrade(suggestions, state, journal_path=tmp_path / "j.jsonl")
    assert res["n_applied"] == 1
    assert state["entries"]["geo.volume"]["level"] == "WITHHELD"
    assert state["counters"]["downgraded"] == 1


def test_state_roundtrip(tmp_path):
    state = load_state(tmp_path / "s.json")
    state["entries"]["x"] = {"level": "REAL", "prev_level": "WITHHELD",
                             "evidence_digest": "abc", "rollback_token": "t"}
    save_state(state, tmp_path / "s.json")
    again = load_state(tmp_path / "s.json")
    assert again["entries"]["x"]["level"] == "REAL"
    assert again["schema"] == "capability-upgrade-state/1.0"


def test_criteria_are_configurable():
    decisions = evaluate_upgrades(_cap(), patrol_report=_alive_report("geo.volume"),
                                 criteria=UpgradeCriteria(min_pass_rate=0.5,
                                                          max_benchmark_error_pct=50.0),
                                 measured={"geo.volume": {"pass_rate": 0.6,
                                                          "benchmark_error_pct": 40.0}})
    assert decisions[0]["verdict"] == "UPGRADE"

def test_real_capability_with_dead_surface_is_flagged_not_silently_noop():
    """台账已 REAL、验证面却死了 —— 不许静悄悄 NOOP（要 HOLD + 原因）。"""
    report = {"capabilities": [{"capability_id": "geo.volume", "status": "dead"}]}
    decisions = evaluate_upgrades(_cap("REAL"), patrol_report=report)
    assert decisions[0]["verdict"] == "HOLD"
    assert decisions[0]["reasons"] == ["already_real", "surface_alive_below_bar"]


def test_real_capability_with_alive_surface_is_noop():
    decisions = evaluate_upgrades(_cap("REAL"), patrol_report=_alive_report("geo.volume"))
    assert decisions[0]["verdict"] == "NOOP"
