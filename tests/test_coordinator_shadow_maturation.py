#!/usr/bin/env python3
"""IN28-N06 成熟化引擎用例：解析 / 匹配阶梯 / 回填幂等 / 判据数字。

判据先于实现：本文件把「匹配率 ≥ 0.90」「回填 ≥ 50 条」「分歧率是连续数字」三条
变成可执行的断言，用**合成账本**（解析解已知）而不是生产库来断言——生产库的数字会
随时间漂移，合成账本的期望值是闭式的。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tools.coordinator_shadow import maturation as M

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
T0 = NOW - timedelta(hours=3)


def beat(rid: int, offset_min: int, *, digest: dict | None = None,
         findings: list | None = None, would_do: list | None = None,
         v1: bool = True) -> dict:
    ts = T0 + timedelta(minutes=offset_min)
    verdict = {"schema": "coordinator-shadow-verdict/1", "as_of": ts.isoformat()}
    if findings is not None:
        verdict["findings"] = findings
    if would_do is not None:
        verdict["would_do"] = would_do
    return {
        "id": rid,
        "ts": ts,
        "model": "test-model",
        "beat_id": str(990000 + rid) if v1 else None,
        "schema": M.__dict__.get("V1_SCHEMA", "coordinator-shadow-beat/1") if v1 else None,
        "verdict": verdict,
        "pulse_digest": digest or {},
        "filings": [],
        "error": None,
    }


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_proposals_from_v1_findings_and_v0_would_do():
    rows = [
        beat(1, 0, findings=[{"organ": "adoption_stall", "metric": "queued_stuck_2h",
                              "threshold": 15, "direction": "higher_worse",
                              "task_key": "shadowdiag-abc", "symptom": "stuck"}]),
        beat(2, 30, would_do=[{"action": "file_mission", "target": "s2_campaign",
                               "reason": "campaign blocked"}], v1=False),
    ]
    props = M.proposals_from_beats(rows)
    assert len(props) == 2
    v1, v0 = props
    assert v1.proposal_id == "coordinator_shadow:beat1:finding0"
    assert v1.key == "shadowdiag-abc" and v1.metric == "queued_stuck_2h"
    assert v1.payload["writer"] == "v1"
    assert v0.proposal_id == "coordinator_shadow:beat2:would_do0"
    assert v0.organ == "s2_campaign" and v0.action == "file_mission"
    assert v0.payload["writer"] == "v0"


def test_telemetry_dispositions_route_grades():
    rows = [beat(1, 0, digest={"as_of": "x", "blocked_nodes": 11,
                               "adoption_stall_stuck": 18, "warn_clusters": []})]
    disps = {d.key: d for d in M.dispositions_from_beats(rows)}
    assert disps["telemetry:campaign_blocked"].payload["route_grade"] == "proxy"
    assert disps["telemetry:queued_stuck_2h"].payload["route_grade"] == "exact"
    assert "telemetry:as_of" not in disps          # 元数据不是观测
    assert "telemetry:warn_clusters" not in disps  # 结构体不是读数


def test_merge_channel_pairs_verdict_with_outcome():
    doc = {"entries": {
        "Repo#1": {"repo": "Repo", "pr": 1, "verdict": "可合", "outcome": "merged",
                   "first_seen": "2026-11-01 00:00:00", "outcome_ts": "2026-11-01 01:00:00"},
        "Repo#2": {"repo": "Repo", "pr": 2, "verdict": "不可合", "outcome": "merged",
                   "first_seen": "2026-11-01 00:00:00", "outcome_ts": "2026-11-01 01:00:00"},
        "Repo#3": {"repo": "Repo", "pr": 3, "verdict": "不可合",
                   "first_seen": "2026-11-01 00:00:00"},
    }}
    props, disps = M.proposals_and_dispositions_from_merge(doc)
    assert len(props) == 3 and len(disps) == 2
    recs = {r.proposal_id: r for r in M.mature(props, disps, NOW, lookahead_hours=48)}
    assert recs["shadow_merge:Repo#1"].state == "matched"
    assert recs["shadow_merge:Repo#2"].state == "diverged"
    assert recs["shadow_merge:Repo#3"].state == "unobservable"


# ---------------------------------------------------------------------------
# 匹配阶梯
# ---------------------------------------------------------------------------


def test_l1_mission_outcome_matched_and_diverged():
    rows = [beat(1, 0, findings=[{"organ": "adoption_stall", "metric": "queued_stuck_2h",
                                  "task_key": "shadowdiag-ok", "symptom": "x"},
                                 {"organ": "adoption_stall", "metric": "queued_stuck_2h",
                                  "task_key": "shadowdiag-dead", "symptom": "y"}])]
    missions = [
        {"id": 10, "task_key": "shadowdiag-ok", "status": "succeeded", "claimed_by": "tp-ver-7",
         "created_at": T0 + timedelta(minutes=1)},
        {"id": 11, "task_key": "shadowdiag-dead", "status": "failed", "claimed_by": "tp-ver-1",
         "created_at": T0 + timedelta(minutes=2)},
    ]
    props = M.proposals_from_beats(rows)
    disps = M.dispositions_from_missions(missions)
    recs = {r.proposal_id: r for r in M.mature(props, disps, NOW, lookahead_hours=1)}
    ok = recs["coordinator_shadow:beat1:finding0"]
    dead = recs["coordinator_shadow:beat1:finding1"]
    assert ok.level == "L1_mission_outcome" and ok.state == "matched"
    assert "tp-ver-7" in ok.note
    assert dead.state == "diverged" and dead.divergence_kind == "dead_mission"


def test_l1_ignores_missions_created_before_the_proposal():
    rows = [beat(1, 60, findings=[{"organ": "s2_campaign", "metric": "campaign_blocked",
                                   "task_key": "shadowdiag-old", "symptom": "x"}])]
    missions = [{"id": 1, "task_key": "shadowdiag-old", "status": "succeeded",
                 "created_at": T0}]
    props = M.proposals_from_beats(rows)
    disps = M.dispositions_from_missions(missions)
    disps += M.dispositions_from_beats(rows)
    recs = M.mature(props, disps, NOW, lookahead_hours=1)
    assert recs[0].level != "L1_mission_outcome"


def test_l2_upheld_and_resolved_against_registry_threshold():
    rows = [
        beat(1, 0, findings=[{"organ": "adoption_stall", "metric": "oldest_queued_minutes",
                              "threshold": 240, "direction": "higher_worse", "symptom": "a"}],
             digest={"oldest_queued_minutes": 0}),
        beat(2, 30, digest={"adoption_stall_minutes": 900}),          # 仍越界 → 前提成立
        beat(3, 90, digest={"adoption_stall_minutes": 3}),            # 已回界 → 条件消解
    ]
    props = M.proposals_from_beats(rows)
    disps = M.dispositions_from_beats(rows)
    rec = M.mature(props, disps, NOW, lookahead_hours=1)[0]
    assert rec.level == "L2_telemetry" and rec.state == "matched"
    assert rec.value == 900 and rec.threshold == 240

    # 最早的后续观测已回界 → 条件消解（另一次更晚的观测 900 不改变"首次观测"口径）
    rows[2] = beat(3, 15, digest={"adoption_stall_minutes": 3})
    props = M.proposals_from_beats(rows[:1])
    disps = M.dispositions_from_beats(rows)
    rec = M.mature(props, disps, NOW, lookahead_hours=1)[0]
    assert rec.state == "diverged" and rec.divergence_kind == "resolved_condition"


def test_pending_inside_lookahead_and_unobservable_without_evidence():
    rows = [beat(1, 0, findings=[{"organ": "credit_backlog", "metric": "credit_backlog",
                                  "threshold": 50, "direction": "higher_worse", "symptom": "a"},
                                 {"organ": "mystery_organ", "symptom": "b"}])]
    props = M.proposals_from_beats(rows)
    fresh = M.mature(props, [], NOW, lookahead_hours=4)[0]
    assert fresh.state == "pending"
    stale = M.mature(props, [], NOW + timedelta(hours=6), lookahead_hours=1)
    assert stale[0].state == "unobservable"
    assert stale[1].state == "unobservable"


def test_proxy_route_is_flagged_not_hidden():
    rows = [beat(1, 0, findings=[{"organ": "s2_campaign", "metric": "campaign_blocked",
                                  "threshold": 5, "direction": "higher_worse", "symptom": "a"}]),
            beat(2, 30, digest={"blocked_nodes": 15})]
    rec = M.mature(M.proposals_from_beats(rows[:1]), M.dispositions_from_beats(rows),
                   NOW, lookahead_hours=1)[0]
    assert rec.route_grade == "proxy"
    summary = M.summarise([rec], 1.0)["channels"]["coordinator_shadow"]
    assert summary["by_route_grade"]["proxy"] == 1
    assert summary["exact_total"] == 0 and summary["exact_match_rate"] is None


# ---------------------------------------------------------------------------
# 汇总口径（解析解）
# ---------------------------------------------------------------------------


def test_match_rate_and_divergence_rate_match_closed_form():
    records = [
        M.MatchRecord(proposal_id=f"p{i}", channel="coordinator_shadow", organ="x",
                      metric="m", action="file_mission",
                      level="L2_telemetry" if i < 7 else "none",
                      route_grade="exact",
                      state=("matched" if i < 5 else "diverged" if i < 7 else "pending"),
                      disposition_id=None if i >= 7 else f"d{i}")
        for i in range(10)
    ]
    agg = M.summarise(records, 1.0)["channels"]["coordinator_shadow"]
    assert agg["total"] == 10
    assert agg["attached"] == 7
    assert agg["match_rate"] == pytest.approx(0.7)
    assert agg["judged"] == 7
    assert agg["divergence_rate"] == pytest.approx(2 / 7)
    lo, hi = agg["divergence_rate_ci95"]
    assert lo < 2 / 7 < hi


def test_wilson_interval_edges():
    assert M.wilson(0, 0) is None
    lo, hi = M.wilson(0, 10)
    assert lo == 0.0 and 0 < hi < 0.35
    lo, hi = M.wilson(10, 10)
    assert hi == 1.0 and 0.6 < lo < 1.0


# ---------------------------------------------------------------------------
# 回填库
# ---------------------------------------------------------------------------


def _records(n_matched: int, n_diverged: int) -> list[M.MatchRecord]:
    out = []
    for i in range(n_matched + n_diverged):
        state = "matched" if i < n_matched else "diverged"
        out.append(M.MatchRecord(
            proposal_id=f"p{i}", channel="coordinator_shadow", organ="x", metric="m",
            action="file_mission", level="L2_telemetry", route_grade="exact", state=state,
            disposition_id=f"d{i}", record_hash=f"h{i}"))
    return out


def test_store_is_append_only_and_idempotent(tmp_path):
    store = M.MaturationStore(str(tmp_path / "m.sqlite3"))
    stats1 = store.append(_records(3, 1), NOW)
    digest1 = store.content_digest()
    stats2 = store.append(_records(3, 1), NOW + timedelta(minutes=5))
    digest2 = store.content_digest()
    assert stats1["inserted"] == 4 and stats2["inserted"] == 0
    assert stats2["after"] == 4 and digest1 == digest2
    assert store.count() == 4
    store.close()


def test_backfill_threshold_is_fifty():
    records = _records(45, 10)
    assert len(records) >= M.TARGET_BACKFILL
    report = {"aggregate": {"match_rate": 0.95, "exact_match_rate": 0.95},
              "sr1": {"value": 0.18, "status": "REAL"}}
    crit = M.apply_criteria(report, {"after": len(records)})
    assert crit["backfill_ok"] is True and crit["match_rate_ok"] is True
    assert crit["divergence_ok"] is True and crit["all_ok"] is True

    crit = M.apply_criteria({"aggregate": {"match_rate": 0.5, "exact_match_rate": 0.5},
                             "sr1": {"value": None, "status": "WITHHELD"}}, {"after": 10})
    assert crit["all_ok"] is False and crit["backfill_ok"] is False
    assert crit["divergence_ok"] is None


# ---------------------------------------------------------------------------
# 端到端（离线，合成账本；不连库、不写仓外）
# ---------------------------------------------------------------------------


def test_end_to_end_run_is_deterministic_and_sandboxed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # 引擎要求 artifact 落在"当前工作树"内
    rows = [
        beat(1, 0, findings=[{"organ": "credit_backlog", "metric": "credit_backlog",
                              "threshold": 50, "direction": "higher_worse", "symptom": "a"}],
             would_do=[{"action": "file_mission", "target": "credit_backlog", "reason": "b"}],
             digest={"credit_backlog": 0}),
        beat(2, 30, digest={"credit_backlog": 80}),
    ]
    merge = {"entries": {"R#1": {"repo": "R", "pr": 1, "verdict": "可合", "outcome": "merged",
                                  "first_seen": "2026-11-07 09:00:00",
                                  "outcome_ts": "2026-11-07 09:30:00"}}}
    out = tmp_path / "artifacts" / "m.json"
    sandbox = tmp_path / "sandbox"
    args = dict(dsn=None, now=NOW, beats=rows, missions=[], merge_doc=merge,
                window_hours=72, lookahead_hours=1)
    first = M.run(str(sandbox), str(out), **args)
    second = M.run(str(sandbox), str(out), **args)
    assert first["criteria"]["match_rate"] >= 0.9
    assert first["store"]["after"] == second["store"]["after"]
    assert first["store"]["content_digest"] == second["store"]["content_digest"]
    assert first["sandbox"]["committed_outside"] == 0
    assert first["failclosed"]["fail_closed"] is True
    saved = json.loads(out.read_text())
    assert saved["schema"] == M.SCHEMA
    assert saved["records_path"].endswith("records.json")
    # 人工计数：2 条提议，都挂上了后续观测 → 匹配率 1.0
    assert saved["channels"]["coordinator_shadow"]["match_rate"] == 1.0


def test_artifact_outside_repo_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        M.run(str(tmp_path / "sandbox"), "/etc/in28_maturation.json", dsn=None,
              now=NOW, beats=[], missions=[], merge_doc={"entries": {}})


def test_chronicle_payload_shape_is_reader_compatible():
    report = {
        "generated_at": "2026-11-07T12:00:00Z",
        "inputs": {"beats": 15},
        "aggregate": {"match_rate": 0.93},
        "sr1": {"value": 0.32, "status": "REAL", "target": 0.20, "meets_target": False,
                "counts": {"matched": 38, "diverged": 19, "pending": 4, "unobservable": 0},
                "items": 61, "maturity": "provisional", "lookahead_hours": 1.0},
        "criteria": {"backfill_records": 1848},
        "store": {"content_digest": "abc"},
    }
    payload = M.chronicle_payload(report)
    sr1 = payload["s8_harness"]["sr_inheritance"]["SR1"]
    assert sr1["value"] == 0.32 and sr1["status"] == "REAL"
    assert payload["kind"] == "sr_inheritance_readiness"
    assert payload["clock"]["slot"] == "s8"
    # sr_dashboard trend 读的就是这个键
    assert payload["sr1_maturation"]["backfill_records"] == 1848