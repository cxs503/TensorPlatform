"""Cohort SLO 验收测试（IN38-N09 第 4、5 条）。

判据：
1. 积压年龄起点是 ``mission.finished_at``（不是记录 ``created_at``）。
2. P90 评价时延 = ``min(decided_at) - finished_at``；阈值 24h。
3. 积压阈值 20 行（pending 记录行数）。
4. 无样本时 P90 为 ``None`` 且状态 ``undefined``（不当 0h 通过）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.evaluation_ops import slo as sl  # noqa: E402

AS_OF = "2026-11-07T00:00:00+00:00"


def _missions(n: int, *, finished="2026-11-06T00:00:00+00:00"):
    return [{"id": i + 1, "repo": "SyntheticRepo", "finished_at": finished} for i in range(n)]


def _decided(mid: int, hours: float):
    return {"mission_id": mid, "status": "confirmed",
            "decided_at": sl.parse_ts("2026-11-06T00:00:00+00:00").isoformat()
            if hours is None else None}


def decided_after(finished: str, hours: float) -> str:
    dt = sl.parse_ts(finished)
    from datetime import timedelta

    return (dt + timedelta(hours=hours)).isoformat()


def test_slo_ok_case():
    missions = _missions(10)
    records = [{"mission_id": i, "status": "confirmed",
                "decided_at": decided_after("2026-11-06T00:00:00+00:00", 2.0)}
               for i in range(1, 11)]
    snap = sl.ops_snapshot(missions, records, as_of=AS_OF)
    out = sl.evaluate_slo(snap)
    assert snap["p90_latency_hours"] == pytest.approx(2.0)
    assert snap["backlog_rows"] == 0
    assert out["states"] == {"latency_p90": "ok", "backlog_rows": "ok"}
    assert out["slo_met"] is True
    assert out["breaches"] == []


def test_slo_breaches_latency_and_backlog():
    missions = _missions(30)
    records = [{"mission_id": 1, "status": "confirmed",
                "decided_at": decided_after("2026-11-06T00:00:00+00:00", 48.0)}]
    records += [{"mission_id": i, "status": "pending", "decided_at": None} for i in range(2, 23)]
    snap = sl.ops_snapshot(missions, records, as_of=AS_OF)
    out = sl.evaluate_slo(snap)
    assert snap["p90_latency_hours"] == pytest.approx(48.0)
    assert snap["backlog_rows"] == 21
    assert out["states"] == {"latency_p90": "breach", "backlog_rows": "breach"}
    assert out["slo_met"] is False
    assert {b["key"] for b in out["breaches"]} == {"latency_p90", "backlog_rows"}


def test_slo_backlog_boundary_is_inclusive_ok():
    missions = _missions(25)
    records = [{"mission_id": i, "status": "pending", "decided_at": None} for i in range(1, 21)]
    snap = sl.ops_snapshot(missions, records, as_of=AS_OF)
    out = sl.evaluate_slo(snap)
    assert snap["backlog_rows"] == 20
    assert out["states"]["backlog_rows"] == "ok"      # ≤20 行达标
    snap21 = sl.ops_snapshot(missions, records + [
        {"mission_id": 21, "status": "pending", "decided_at": None}], as_of=AS_OF)
    assert sl.evaluate_slo(snap21)["states"]["backlog_rows"] == "breach"


def test_undefined_p90_is_none_not_zero():
    missions = _missions(3)
    records = [{"mission_id": 1, "status": "pending", "decided_at": None}]
    snap = sl.ops_snapshot(missions, records, as_of=AS_OF)
    out = sl.evaluate_slo(snap)
    assert snap["p90_latency_hours"] is None
    assert out["states"]["latency_p90"] == "undefined"
    assert out["slo_met"] is False            # 无定义不能算通过
    assert out["slo_met_defined_only"] is True
    assert "p90_latency_hours" in snap["undefined_metrics"]


def test_backlog_age_origin_is_finished_at():
    missions = [
        {"id": 1, "repo": "SyntheticRepo", "finished_at": "2026-11-01T00:00:00+00:00"},
        {"id": 2, "repo": "SyntheticRepo", "finished_at": "2026-11-05T00:00:00+00:00"},
    ]
    # 两条记录同时创建（created_at 相同）→ 若按 created_at 算，年龄必然相同
    records = [
        {"mission_id": 1, "status": "pending", "decided_at": None,
         "created_at": "2026-11-06T23:00:00+00:00"},
        {"mission_id": 2, "status": "pending", "decided_at": None,
         "created_at": "2026-11-06T23:00:00+00:00"},
    ]
    snap = sl.ops_snapshot(missions, records, as_of=AS_OF)
    assert snap["backlog_rows"] == 2
    assert snap["backlog_age_origin"] == "mission.finished_at"
    assert snap["backlog_age_max_hours"] == pytest.approx(144.0)   # 11-01 → 11-07 = 6 天


def test_view_days_filters_by_finished_at():
    missions = [
        {"id": 1, "repo": "SyntheticRepo", "finished_at": "2026-10-01T00:00:00+00:00"},
        {"id": 2, "repo": "SyntheticRepo", "finished_at": "2026-11-06T00:00:00+00:00"},
    ]
    records = [{"mission_id": 1, "status": "pending", "decided_at": None},
               {"mission_id": 2, "status": "pending", "decided_at": None}]
    wide = sl.ops_snapshot(missions, records, as_of=AS_OF)
    narrow = sl.ops_snapshot(missions, records, as_of=AS_OF, view_days=7)
    assert wide["missions_in_scope"] == 2 and wide["backlog_rows"] == 2
    assert narrow["missions_in_scope"] == 1 and narrow["backlog_rows"] == 1


def test_latency_anomaly_excluded_from_percentile():
    missions = _missions(2, finished="2026-11-06T00:00:00+00:00")
    records = [
        {"mission_id": 1, "status": "confirmed", "decided_at": "2026-11-05T00:00:00+00:00"},
        {"mission_id": 2, "status": "confirmed", "decided_at": "2026-11-06T06:00:00+00:00"},
    ]
    snap = sl.ops_snapshot(missions, records, as_of=AS_OF)
    assert snap["latency_n"] == 1
    assert snap["p90_latency_hours"] == pytest.approx(6.0)
    assert len(snap["latency_anomalies"]) == 1


def test_slo_report_multi_view_consistent_thresholds():
    missions = _missions(10)
    records = [{"mission_id": i, "status": "confirmed",
                "decided_at": decided_after("2026-11-06T00:00:00+00:00", 3.0)}
               for i in range(1, 11)]
    rep = sl.slo_report(missions, records, as_of=AS_OF, view_days=(None, 7, 30, 90))
    assert set(rep["views"]) == {"all", "7d", "30d", "90d"}
    assert rep["thresholds"]["latency_p90_hours_max"] == 24.0
    assert rep["thresholds"]["backlog_rows_max"] == 20.0
    assert rep["primary_slo"]["states"]["latency_p90"] == "ok"
    assert all(v["slo"]["thresholds"] == rep["thresholds"] for v in rep["views"].values())