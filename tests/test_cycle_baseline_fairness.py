"""IN55W3-N08 · 调度公平性检测器（fairness 层）测试。

判据 (b)：饥饿 / 优先级反转检测器在**带标签合成夹具**上 F1 ≥ 0.9。
另外锁死两个容易退化的性质：
* 边界样本（歧义标签）必须被剔除而不是算作假阳/假阴；
* 无证据时阈值返回 None（不能凭空造阈值）。
"""
from __future__ import annotations

import pytest

from tools.cycle_baseline import fairness, fixtures, segments, spec


def test_worker_fairness_perfectly_even_is_one():
    records = segments.build_records([
        {"mission_id": i, "repo": "r0", "status": "succeeded", "worker": f"w{i % 4}",
         "created_at": "2026-11-01T00:00:00+00:00",
         "claimed_at": "2026-11-01T00:10:00+00:00",
         "finished_at": "2026-11-01T00:20:00+00:00"}
        for i in range(8)])
    w = fairness.worker_fairness(records)
    assert w["n_workers"] == 4
    assert w["jain_count"] == pytest.approx(1.0)
    assert w["gini_count"] == pytest.approx(0.0)
    assert w["jain_busy_time"] == pytest.approx(1.0)


def test_worker_fairness_uneven_detected():
    rows = []
    for i in range(8):
        rows.append({"mission_id": i, "repo": "r0", "status": "succeeded",
                     "worker": "w0" if i < 5 else f"w{i}",
                     "created_at": "2026-11-01T00:00:00+00:00",
                     "claimed_at": "2026-11-01T00:10:00+00:00",
                     "finished_at": "2026-11-01T00:20:00+00:00"})
    w = fairness.worker_fairness(segments.build_records(rows))
    assert w["jain_count"] < 0.6
    assert w["gini_count"] > 0.3


def test_starvation_threshold_none_without_evidence():
    recs = segments.build_records([{"mission_id": 1, "status": "succeeded"}])
    assert fairness.starvation_threshold(recs) is None
    det = fairness.detect_starvation(recs)
    assert det["n_eligible"] == 0 and det["n_flagged"] == 0
    assert det["flag_rate"] is None


def test_starvation_detector_f1_meets_gate():
    recs, labels = fixtures.starvation_fixture(seed=spec.DEFAULT_SEED)
    det = fairness.detect_starvation(segments.build_records(recs))
    score = fairness.score_detector([f["mission_id"] for f in det["flagged"]], labels)
    assert score["f1"] is not None and score["f1"] >= spec.DETECTOR_F1_MIN
    # 正例必须全部抓到（漏检在调度治理里比误检更危险）
    assert score["recall"] == pytest.approx(1.0)
    assert score["n_ambiguous_excluded"] > 0


def test_priority_inversion_f1_meets_gate():
    recs, labels = fixtures.inversion_fixture(seed=spec.DEFAULT_SEED)
    det = fairness.detect_priority_inversions(segments.build_records(recs))
    score = fairness.score_detector([f["mission_id"] for f in det["flagged"]], labels)
    assert score["f1"] is not None and score["f1"] >= spec.DETECTOR_F1_MIN
    assert score["recall"] == pytest.approx(1.0)
    # gap 诱饵（计划间隔 < 判定阈）不得被误判
    assert score["precision"] == pytest.approx(1.0)


def test_inversion_gap_threshold_is_respected():
    """计划间隔 10 s（< gap 60 s）的乱序 → 不算反转；间隔 3600 s 的乱序 → 算。"""
    base = "2026-11-01T00:00:00+00:00"
    rows = [
        {"mission_id": 1, "status": "succeeded", "plan_id": 7, "planned_start": base,
         "created_at": base, "claimed_at": "2026-11-01T00:10:00+00:00",
         "finished_at": "2026-11-01T00:20:00+00:00"},
        {"mission_id": 2, "status": "succeeded", "plan_id": 7,
         "planned_start": "2026-11-01T00:00:10+00:00",
         "created_at": "2026-11-01T00:00:10+00:00",
         "claimed_at": "2026-11-01T00:05:00+00:00",
         "finished_at": "2026-11-01T00:15:00+00:00"},
    ]
    det = fairness.detect_priority_inversions(segments.build_records(rows))
    assert det["n_flagged"] == 0
    rows[1]["planned_start"] = "2026-11-01T02:00:00+00:00"
    det2 = fairness.detect_priority_inversions(segments.build_records(rows))
    assert det2["n_flagged"] == 1 and det2["flagged"][0]["mission_id"] == 1


def test_score_detector_excludes_ambiguous_and_handles_zero_denominator():
    labels = {1: True, 2: False, 3: None}
    s = fairness.score_detector([1], labels)
    assert (s["tp"], s["fp"], s["fn"]) == (1, 0, 0)
    assert s["f1"] == pytest.approx(1.0)
    s2 = fairness.score_detector([3], labels)       # 只命中歧义样本 → 不计分
    assert s2["n_flagged"] == 0 and s2["fp"] == 0
    s3 = fairness.score_detector([], {2: False})
    assert s3["precision"] is None and s3["f1"] is None