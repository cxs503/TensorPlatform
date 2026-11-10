"""IN55W3-N08 · 四段分解与分位账本（segments / stats 层）单元测试。

覆盖的可证伪点：
* 缺证据必须是 ``None`` 而不是 0（0 会被读成"无等待/无开销"）；
* 负段值（报告早于终态、落地早于终态）不得被静默 clamp 掉，必须留 ``negative_share``；
* complete-case 份额可加（Σshare = 1），不完备记录的 share 必须为 None；
* 覆盖 <80% → 该段等级 WITHHELD（判据 d）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.cycle_baseline import segments, spec
from tools.cycle_baseline.stats import gini, jain_index, percentile_linear

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _row(mid: int, queue_s, exec_s, merge_s=None, report_s=None, status="succeeded"):
    created = T0
    claimed = None if queue_s is None else created + timedelta(seconds=queue_s)
    finished = None if (claim_s := (None if exec_s is None or claimed is None else exec_s)) is None \
        else claimed + timedelta(seconds=claim_s)
    return {
        "mission_id": mid, "repo": "r0", "status": status, "worker": "w0",
        "created_at": _iso(created),
        "claimed_at": _iso(claimed) if claimed else None,
        "finished_at": _iso(finished) if finished else None,
        "merge_at": _iso(finished + timedelta(seconds=merge_s)) if (merge_s is not None and finished) else None,
        "report_at": _iso(finished + timedelta(seconds=report_s)) if (report_s is not None and finished) else None,
    }


# ── 纯统计层 ────────────────────────────────────────────────────────────
def test_percentile_matches_linear_interpolation():
    xs = [1.0, 2.0, 3.0, 4.0]
    assert percentile_linear(xs, 0.5) == pytest.approx(2.5)
    assert percentile_linear(xs, 0.9) == pytest.approx(3.7)
    assert percentile_linear([], 0.5) is None


def test_jain_and_gini_reference_values():
    assert jain_index([1, 1, 1, 1]) == pytest.approx(1.0)
    assert jain_index([1, 0, 0, 0]) == pytest.approx(0.25)   # 1/n 下界
    assert jain_index([]) is None
    assert gini([1, 1, 1, 1]) == pytest.approx(0.0)
    # 均值 2.5、样本 [1,2,3,4]：G = Σ|xi−xj| / (2n²μ) = 20 / (2*16*2.5) = 0.25
    assert gini([1, 2, 3, 4]) == pytest.approx(0.25)


# ── 段计算 ──────────────────────────────────────────────────────────────
def test_missing_evidence_is_none_not_zero():
    rec = segments.build_record(_row(1, queue_s=None, exec_s=None))
    assert rec["t_queue"] is None and rec["t_exec"] is None
    assert rec["segments_measured"] == 0
    assert rec["share_t_queue"] is None


def test_negative_merge_lag_preserved_and_clamped():
    rec = segments.build_record(_row(2, queue_s=100, exec_s=200, merge_s=-50, report_s=-20))
    assert rec["t_merge_raw"] == pytest.approx(-50)
    assert rec["t_merge"] == pytest.approx(0.0)          # 非执行开销口径 clamp
    assert rec["t_report_raw"] == pytest.approx(-20)
    assert rec["t_report"] == pytest.approx(0.0)
    ledger = segments.segment_ledger([rec])
    assert ledger["t_merge"]["negative_share"] == pytest.approx(1.0)
    assert ledger["t_report"]["negative_share"] == pytest.approx(1.0)


def test_cycle_definition_is_finished_minus_created():
    rec = segments.build_record(_row(3, queue_s=100, exec_s=200))
    assert rec["cycle_s"] == pytest.approx(300)


def test_complete_case_shares_sum_to_one_and_partial_is_none():
    complete = segments.build_record(_row(4, queue_s=100, exec_s=200, merge_s=50, report_s=50))
    partial = segments.build_record(_row(5, queue_s=100, exec_s=200))
    assert sum(complete[f"share_{s}"] for s in spec.SEGMENTS) == pytest.approx(1.0)
    assert complete["share_t_queue"] == pytest.approx(100 / 400)
    assert all(partial[f"share_{s}"] is None for s in spec.SEGMENTS)


def test_coverage_gate_downgrades_below_threshold():
    rows = [_row(10, queue_s=10, exec_s=20),
            _row(11, queue_s=None, exec_s=None),   # 无领取证据
            _row(12, queue_s=None, exec_s=None),
            _row(13, queue_s=30, exec_s=40, merge_s=5)]
    ledger = segments.segment_ledger(segments.build_records(rows))
    assert ledger["t_queue"]["coverage"] == pytest.approx(0.5)
    gate = segments.coverage_gate(ledger)
    assert set(gate["withheld_segments"]) == {"t_queue", "t_exec", "t_merge", "t_report"}
    assert gate["verdict"] == "WITHHELD"


def test_ledger_measured_when_coverage_high():
    rows = [_row(20 + i, queue_s=10 + i, exec_s=20, merge_s=5, report_s=5) for i in range(10)]
    ledger = segments.segment_ledger(segments.build_records(rows))
    gate = segments.coverage_gate(ledger)
    assert gate["verdict"] == "MEASURED"
    assert ledger["t_queue"]["grade"] == "MEASURED"
    assert ledger["t_queue"]["p50"] == pytest.approx(14.5)


def test_attribution_table_partitions_cohort_and_keeps_none():
    rows = [
        _row(30, queue_s=10, exec_s=20, merge_s=5, report_s=5),
        _row(31, queue_s=10, exec_s=20, status="failed"),
    ]
    rows[0]["repo"] = "r1"
    table = segments.attribution_table(segments.build_records(rows))
    assert sum(r["n"] for r in table) == 2
    failed = [r for r in table if r["status"] == "failed"][0]
    assert failed["n_complete_case"] == 0
    assert failed["share_t_merge"] is None      # 不完备 → None，不是 0
    ok = [r for r in table if r["status"] == "succeeded"][0]
    assert ok["non_exec_overhead_share"] == pytest.approx((5 + 5) / 40)

# ── 条件子总体（可证据总体） ────────────────────────────────────────────
def test_conditional_ledger_uses_eligible_population_and_flags_mnar():
    from tools.cycle_baseline import eligibility
    rows = []
    # 3 条有会话（可领取证据），其中 2 条真有 claim
    for i, claimed in enumerate([True, True, False]):
        r = _row(40 + i, queue_s=100 if claimed else None,
                 exec_s=200 if claimed else None, merge_s=5, report_s=5)
        r["session_id"] = 1000 + i
        r["report_owed"] = False
        r["n_reports"] = 1
        r["n_pr_events"] = 1
        if not claimed:
            r["finished_at"] = _iso(T0 + timedelta(seconds=60))   # 周期显著更短
        rows.append(r)
    # 2 条无会话（不可证据）：周期很长
    for i in range(2):
        r = _row(50 + i, queue_s=None, exec_s=None)
        r["session_id"] = None
        r["report_owed"] = True
        r["n_reports"] = 0
        r["n_pr_events"] = 0
        r["finished_at"] = _iso(T0 + timedelta(days=2))
        rows.append(r)
    cond = eligibility.conditional_ledger(segments.build_records(rows))
    q = cond["t_queue"]
    assert q["eligible_population"] == 3
    assert q["measured_n"] == 2
    assert q["coverage_within"] == pytest.approx(2 / 3)
    assert q["grade_label"] == "WITHHELD"          # 2/3 < 80%
    assert "MNAR" in q["representativeness"]["reading"]
    assert cond["t_merge"]["eligible_population"] == 3
    assert cond["t_report"]["bookkeeping_inconsistency"] is not None
    assert cond["summary"]["scope_warning"]
