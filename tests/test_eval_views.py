"""视图无关性验收测试（IN38-N09 第 6 条）。

判据：同一 cohort 在 7/30/90 天三种视图下逐字段数值**完全相等**（差异 = 0）。
负控：旧口径（最近 N 单）在同一数据上随窗口漂移 —— 证明这次改动确实修掉了窗口相关性。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.evaluation_ops import cohorts as ch  # noqa: E402
from tools.evaluation_ops import views as vw  # noqa: E402

AS_OF = "2026-11-07T00:00:00+00:00"

#: 6 个 ISO 周、每周 4 张 mission，覆盖率逐周不同（0 → 1），便于观察窗口效应
MISSIONS = []
RECORDS = []
for w, (week_start, decided_n) in enumerate([
    ("2026-09-28", 0), ("2026-10-05", 1), ("2026-10-12", 2),
    ("2026-10-19", 3), ("2026-10-26", 4), ("2026-11-02", 2),
]):
    for i in range(4):
        mid = w * 10 + i + 1
        MISSIONS.append({"id": mid, "repo": "SyntheticRepo",
                         "finished_at": f"{week_start}T0{i}:00:00+00:00"})
        if i < decided_n:
            RECORDS.append({"mission_id": mid, "status": "confirmed",
                            "decided_at": f"{week_start}T1{i}:00:00+00:00",
                            "created_at": f"{week_start}T09:00:00+00:00"})


def test_window_independence_zero_difference():
    cohorts = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    got = vw.window_independence(cohorts, as_of=AS_OF)
    assert got["identical"] is True
    assert got["diffs"] == []
    assert got["max_abs_diff"] == 0.0
    assert got["pairs_compared"] > 0
    assert got["views_days"] == [7, 30, 90]


def test_same_cohort_numbers_identical_across_views():
    cohorts = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    v7 = vw.filter_view(cohorts, as_of=AS_OF, days=7)
    v90 = vw.filter_view(cohorts, as_of=AS_OF, days=90)
    assert v7 and set(v7) <= set(v90)
    for key, row in v7.items():
        for field in ch.METRIC_FIELDS:
            assert row[field] == v90[key][field], (key, field)


def test_view_filter_is_monotone():
    cohorts = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    sets = [set(vw.filter_view(cohorts, as_of=AS_OF, days=d)) for d in (7, 30, 90)]
    assert sets[0] <= sets[1] <= sets[2]


def test_legacy_window_coverage_drifts_with_window():
    legacy = vw.legacy_window_sweep(MISSIONS, RECORDS, windows=(4, 8, 24))
    assert legacy["window_dependent"] is True
    assert legacy["spread"] > 0
    coverages = [row["coverage"] for row in legacy["rows"]]
    assert len(set(coverages)) > 1      # 同一数据、不同窗口 → 不同覆盖率


def test_cohort_vs_legacy_verdict_pass():
    cohorts = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    out = vw.cohort_vs_legacy(cohorts, MISSIONS, RECORDS, as_of=AS_OF, windows=(4, 8, 24))
    assert out["cohort_invariant"]["identical"] is True
    assert out["cohort_invariant"]["max_abs_diff"] == 0.0
    assert out["legacy_window_dependent"]["window_dependent"] is True
    assert out["verdict"].startswith("PASS")


def test_empty_view_has_no_diffs_and_no_division():
    cohorts = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    got = vw.window_independence(cohorts, as_of="2027-06-01T00:00:00+00:00", days=(7, 30, 90))
    assert got["common_cohorts"] == []
    assert got["diffs"] == []
    assert got["identical"] is True


def test_in_view_boundary_semantics():
    cohorts = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    far = min(cohorts)
    row = cohorts[far]
    # 视图远端边界：起步日早于 as_of-days 就不进视图（半开区间，无 ±1 天抖动）
    assert vw.in_view(row, as_of=AS_OF, days=7) is False
    assert vw.in_view(row, as_of=AS_OF, days=90) is True