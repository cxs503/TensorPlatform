"""退化分类器 + 自适应判据族 + 回放重判测试（含负控与不变量）。"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from tools.criteria_metrology.adaptive import (
    declared_scale,
    evaluate_family,
    family_for,
    merge_verdicts,
)
from tools.criteria_metrology.degeneracy import (
    DegeneracyPolicy,
    classify_degeneracy,
)
from tools.criteria_metrology.model import Criterion, DegeneracyClass, Interval, Metric, Verdict, normalize_criterion
from tools.criteria_metrology.rejudge import agreement_stats, rejudge_one

DATA = Path(__file__).resolve().parents[1] / "data" / "criteria_metrology"


def C(**kw) -> Criterion:
    kw.setdefault("criterion_id", "x")
    return normalize_criterion(Criterion(**kw))


# --------------------------------------------------------------------------- #
# 三分类
# --------------------------------------------------------------------------- #


def test_t1_zero_reference():
    f = classify_degeneracy(C(y=4.2e-9, y_ref=0.0, tau=0.03))
    assert f.degeneracy is DegeneracyClass.T1_ZERO_REFERENCE
    assert any("reference_near_zero" in r for r in f.reasons)


def test_t1_magnitude_collapse():
    f = classify_degeneracy(C(y=1e6, y_ref=1e-3, tau=0.10))
    assert f.degeneracy is DegeneracyClass.T1_ZERO_REFERENCE


def test_t2_sign_opposite():
    f = classify_degeneracy(C(y=-0.42, y_ref=0.37, tau=0.05))
    assert f.degeneracy is DegeneracyClass.T2_SIGN_CROSSING
    assert any("sign_opposite" in r for r in f.reasons)


def test_t2_denominator_distortion():
    f = classify_degeneracy(C(y=104.0, y_ref=4.0, tau=0.10))
    assert f.degeneracy is DegeneracyClass.T2_SIGN_CROSSING
    assert any("100pct" in r for r in f.reasons)


def test_t3_interval_reference_is_structural():
    f = classify_degeneracy(C(y=0.175, y_ref=None, tau=0.10, reference_interval=Interval(0.4, 0.6)))
    assert f.degeneracy is DegeneracyClass.T3_MULTI_METRIC


def test_t3_parallel_metrics():
    f = classify_degeneracy(
        C(y=1.04, y_ref=1.0, tau=0.03, source={"alt_metrics": ["relative", "absolute"]})
    )
    assert f.degeneracy is DegeneracyClass.T3_MULTI_METRIC


def test_benign_point_reference_is_none():
    assert classify_degeneracy(C(y=3.8324, y_ref=3.8317, tau=0.01)).degeneracy is DegeneracyClass.NONE


def test_derived_records_do_not_get_sign_evidence():
    """由报告误差反推 y 的记录方向不可知：不得据符号判 T2，但分母失真仍可判。"""
    derived = C(y=6.4, y_ref=1.0, tau=0.03, source={"derived_y": True, "reported_error": 5.4})
    f = classify_degeneracy(derived)
    assert f.degeneracy is DegeneracyClass.T2_SIGN_CROSSING
    assert all("sign_opposite" not in r for r in f.reasons)


def test_policy_override_is_respected():
    strict = DegeneracyPolicy(sign_cross_err_ratio=100.0)
    c = C(y=104.0, y_ref=4.0, tau=0.10)
    assert classify_degeneracy(c).degeneracy is DegeneracyClass.T2_SIGN_CROSSING
    assert classify_degeneracy(c, strict).degeneracy is DegeneracyClass.NONE


# --------------------------------------------------------------------------- #
# 自适应判据族
# --------------------------------------------------------------------------- #


def test_zero_reference_without_declared_scale_is_undecidable_not_pass():
    c = C(y=7.4e-10, y_ref=0.0, tau=0.02)
    res = evaluate_family(c)
    assert res.verdict is Verdict.UNDECIDABLE
    assert "zero_reference_requires_declared_scale" in " ".join(res.notes)


def test_zero_reference_with_declared_scale_is_judged():
    c = C(y=7.4e-10, y_ref=0.0, tau=0.02, scale=1.0, scale_source="declared:budget=1")
    assert declared_scale(c) == pytest.approx(1.0)
    res = evaluate_family(c)
    assert res.verdict is Verdict.PASS
    assert res.value == pytest.approx(7.4e-10)


def test_interval_containment_flags_out_of_band():
    c = C(y=0.0175, y_ref=None, tau=0.10, reference_interval=Interval(0.04, 0.06))
    res = evaluate_family(c)
    assert res.family_member.startswith("A4")
    assert res.verdict is Verdict.FAIL
    # 越界距离 / 区间宽度 = (0.036 - 0.0175) / 0.02
    assert res.value == pytest.approx((0.04 * 0.9 - 0.0175) / 0.02)


def test_interval_containment_accepts_in_band():
    c = C(y=0.05, y_ref=None, tau=0.10, reference_interval=Interval(0.04, 0.06))
    assert evaluate_family(c).verdict is Verdict.PASS


def test_log_ratio_is_symmetric_under_swap():
    a = C(y=1.1, y_ref=1.0, tau=0.15)
    b = C(y=1.0, y_ref=1.1, tau=0.15)
    from tools.criteria_metrology.adaptive import _a2_log_ratio_band

    va = _a2_log_ratio_band(a)[1]
    vb = _a2_log_ratio_band(b)[1]
    assert va == pytest.approx(vb)


def test_one_sided_uses_bound_not_relative_error():
    c = C(y=7.5, y_ref=None, tau=20.0, metric=Metric.ONE_SIDED, comparator="<=")
    res = evaluate_family(c)
    assert res.family_member.startswith("A5")
    assert res.verdict is Verdict.PASS


def test_merge_rule_never_lets_weakest_member_pass():
    assert merge_verdicts([Verdict.PASS, Verdict.FAIL]) is Verdict.FAIL
    assert merge_verdicts([Verdict.PASS, Verdict.UNDECIDABLE]) is Verdict.UNDECIDABLE
    assert merge_verdicts([Verdict.PASS, Verdict.NOT_APPLICABLE]) is Verdict.PASS
    assert merge_verdicts([Verdict.NOT_APPLICABLE, Verdict.NOT_APPLICABLE]) is Verdict.UNDECIDABLE
    assert merge_verdicts([]) is Verdict.UNDECIDABLE


def test_family_selection_tracks_metric():
    assert family_for(DegeneracyClass.NONE, C(y=1.0, y_ref=1.0, tau=0.03, metric=Metric.ONE_SIDED))[0] == "A5"
    assert family_for(DegeneracyClass.T3_MULTI_METRIC, C(y=1.0, y_ref=None, tau=0.03, reference_interval=Interval(1, 2)))[0] == "A4"


# --------------------------------------------------------------------------- #
# 不变量：基准趋零不得返回无界相对误差
# --------------------------------------------------------------------------- #


def test_no_unbounded_relative_error_over_1000_samples():
    rnd = random.Random(20261111)
    mags = [0.0, 1e-300, 1e-12, 1e-6, 1e-3, 1.0, 1e6]
    for i in range(1200):
        y_ref = mags[i % len(mags)] * rnd.choice([1.0, -1.0, 0.5, 7.3])
        y = y_ref * rnd.uniform(-3, 3) if i % 2 else rnd.uniform(-1e6, 1e6)
        c = C(y=y, y_ref=y_ref, tau=0.03)
        res = evaluate_family(c)
        if res.value is not None:
            assert math.isfinite(res.value)
        if y_ref == 0.0:
            assert res.degeneracy is not DegeneracyClass.NONE or c.y != 0.0


def test_none_propagation_is_safe():
    c = C(y=None, y_ref=None, tau=None)
    res = evaluate_family(c)
    assert res.verdict is Verdict.UNDECIDABLE
    assert rejudge_one(c)["adaptive_verdict"] == "UNDECIDABLE"


# --------------------------------------------------------------------------- #
# 回放 + 负控 + 一致率（用仓内快照，不联网）
# --------------------------------------------------------------------------- #


def test_replay_corpus_meets_acceptance_lines():
    from tools.criteria_metrology.harvest import load_snapshot
    from tools.criteria_metrology.pipeline import run_replay

    snap = load_snapshot(DATA / "corpus.json")
    summary = run_replay(snap, DegeneracyPolicy())["summary"]
    assert summary["n_records"] >= 200
    assert summary["degenerate_rate"] >= 0.05
    assert all(r["adaptive_member"] for r in summary["degenerate_rows"])
    assert all(r["source"]["path"] or r["source"]["report_id"] for r in summary["degenerate_rows"])


def test_negative_controls():
    from tools.criteria_metrology.pipeline import run_controls

    ctl = json.loads((DATA / "controls.json").read_text(encoding="utf-8"))
    res = run_controls(ctl, DegeneracyPolicy())
    assert res["n_degenerate_hit"] == 12
    assert res["n_benign_clean"] == 12


def test_four_cases_direction_and_m7241_fail():
    from tools.criteria_metrology.pipeline import run_cases

    res = run_cases(DATA / "cases_m7241_m7248.json", DegeneracyPolicy())
    assert res["n_cases"] == 4
    assert res["n_direction_ok"] == 4
    m7241 = next(c for c in res["cases"] if c["case_id"] == "m7241")
    assert m7241["degeneracy"] == "T3"
    assert m7241["adaptive_verdict"] == "FAIL"
    # 替代判据数值必须可复算（inputs 全在）
    assert m7241["recompute"]["inputs"]["interval"]["lo"] == pytest.approx(0.04)


def test_agreement_labels():
    from tools.criteria_metrology.pipeline import run_agreement

    res = run_agreement(DATA / "labels_40.json", DegeneracyPolicy())
    assert res["n"] == 40
    assert res["agreement"] >= 0.90


def test_agreement_stats_kappa_math():
    stats = agreement_stats(["a", "b", "a", "b"], ["a", "a", "b", "b"])
    assert stats["n"] == 4
    assert stats["agreement"] == pytest.approx(0.5)
    assert stats["kappa"] == pytest.approx(0.0, abs=1e-12)
    perfect = agreement_stats(["a"] * 5, ["a"] * 5)
    assert perfect["kappa"] is None or perfect["kappa"] == pytest.approx(1.0)