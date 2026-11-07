"""外部效度校准模块的验证面（pytest）。

分四组：
1. 度量本身的解析/构造性验证（AUC 与暴力配对一致、Brier 闭式、CUSUM 阶跃）；
2. 方法学要害：**分层 AUC 必须能拆掉"批次效应"造成的假判别力**（tests 14 号用例，
   这是本节点最重要的红/绿线：构造 pooled AUC≈1 但日内 AUC=0.5 的数据）；
3. 标签与 cohort 构造（下游结果四分类的优先级、返工点名解析、假 DSN 连接）；
4. 验收面（J1..J7 的判定逻辑与可复现性）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.external_validity import acceptance as acc
from tools.external_validity import ledger as lg
from tools.external_validity import metrics as mt


# --------------------------------------------------------------------- 1. AUC
def test_auc_perfect_separation_is_one():
    assert mt.auc_score([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == pytest.approx(1.0)


def test_auc_reversed_is_zero():
    assert mt.auc_score([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == pytest.approx(0.0)


def test_auc_all_ties_is_half():
    assert mt.auc_score([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == pytest.approx(0.5)


def test_auc_single_class_is_nan():
    assert math.isnan(mt.auc_score([0.1, 0.2], [1, 1]))


def test_auc_matches_bruteforce_pair_counting():
    rng = np.random.default_rng(7)
    scores = rng.integers(0, 5, 60).astype(float)   # 整数 → 大量结，考验 tie 处理
    labels = rng.integers(0, 2, 60)
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    brute = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))
    assert mt.auc_score(scores, labels) == pytest.approx(brute)


# ------------------------------------------------------------- 2. 概率语义/校准
def test_brier_closed_form():
    assert mt.brier_score([0.9, 0.1], [1, 0]) == pytest.approx((0.01 + 0.01) / 2)


def test_brier_skill_positive_when_better_than_climatology():
    probs = [0.95] * 10 + [0.05] * 10
    labels = [1] * 10 + [0] * 10
    assert mt.brier_skill_score(probs, labels) > 0.9


def test_calibration_curve_bins_and_empty_buckets():
    curve = mt.calibration_curve([0.05, 0.15, 0.95], [0, 0, 1], n_bins=10)
    assert len(curve) == 10
    assert curve[0]["n"] == 1 and curve[0]["actual"] == pytest.approx(0.0)
    assert curve[5]["n"] == 0 and math.isnan(curve[5]["pred"])
    assert sum(b["n"] for b in curve) == 3


def test_max_calibration_deviation_respects_min_bin_n():
    curve = [{"bin_lo": 0, "bin_hi": 0.1, "n": 3, "pred": 0.5, "actual": 0.0},
             {"bin_lo": 0.1, "bin_hi": 0.2, "n": 50, "pred": 0.15, "actual": 0.18}]
    assert mt.max_calibration_deviation(curve, min_bin_n=10) == pytest.approx(0.03)


# ------------------------------------------------------------- 3. 逻辑回归
def test_logistic_recovers_sign_and_is_converged():
    rng = np.random.default_rng(3)
    x = rng.normal(size=400)
    p = 1 / (1 + np.exp(-(2.0 * x)))
    y = (rng.random(400) < p).astype(int)
    model = mt.fit_logistic(x.reshape(-1, 1), y)
    assert model.converged
    assert model.coef[0] > 0.5


def test_logistic_probs_in_unit_interval_and_monotone():
    rng = np.random.default_rng(5)
    x = rng.normal(size=200)
    y = (x + rng.normal(scale=0.5, size=200) > 0).astype(int)
    model = mt.fit_logistic(x.reshape(-1, 1), y)
    probs = model.predict_proba(np.array([[-2.0], [0.0], [2.0]]))
    assert np.all((probs >= 0) & (probs <= 1))
    assert probs[0] < probs[1] < probs[2]


# ------------------------------------------------------------- 4. walk-forward
def _synthetic(day_counts, signal=1.0, seed=11, base=None):
    rng = np.random.default_rng(seed)
    out = []
    for d, (n, rate) in enumerate(day_counts):
        for _ in range(n):
            x1 = rng.normal()
            logit = signal * x1 + (np.log(rate / (1 - rate)) if rate else -6.0)
            y = int(rng.random() < 1 / (1 + np.exp(-logit)))
            out.append({"day": f"2026-11-{d + 1:02d}", "x": [x1], "y": y})
    return out


def test_walk_forward_trains_only_on_past_days():
    samples = _synthetic([(40, 0.5)] * 5)
    wf = mt.walk_forward(samples, "y", min_train_days=2, min_test_n=5)
    for w in wf["windows"]:
        expected = sum(1 for s in samples if s["day"] < w["day"])
        assert w["n_train"] == expected          # 无未来信息泄漏
        assert w["n_test"] == 40


def test_walk_forward_records_warmup_skips():
    samples = _synthetic([(40, 0.5)] * 4)
    wf = mt.walk_forward(samples, "y", min_train_days=2, min_test_n=5)
    assert len(wf["skipped"]) == 2
    assert all("warmup" in s["why"] for s in wf["skipped"])


def test_walk_forward_detects_real_signal():
    samples = _synthetic([(120, 0.5)] * 5, signal=1.6, seed=2)
    wf = mt.walk_forward(samples, "y", min_train_days=2, min_test_n=10)
    assert wf["pooled_auc"] > 0.7


# ------------------------------------- 5. 分层 AUC（本节点的方法学红/绿线）
def test_stratified_auc_removes_batch_effect_artifact():
    """构造：每天内标签与特征完全无关（AUC=0.5），但天的基准率与特征均值强相关。

    pooled AUC 会显著 > 0.5（假判别力），stratified AUC 必须回到 0.5 附近。
    """
    rng = np.random.default_rng(0)
    scores, labels, strata = [], [], []
    for d, (shift, rate) in enumerate([(0.0, 0.0), (3.0, 0.9), (6.0, 1.0)]):
        for _ in range(200):
            scores.append(rng.normal(loc=shift))       # 特征随天漂移
            labels.append(int(rng.random() < rate))    # 标签只看天，不看特征
            strata.append(f"d{d}")
    pooled = mt.auc_score(scores, labels)
    strat = mt.stratified_auc(scores, labels, strata)
    assert pooled > 0.75                       # 假象成立
    assert abs(strat["auc"] - 0.5) < 0.06      # 分层后拆掉假象


def test_stratified_auc_equals_weighted_stratum_mean():
    scores = [0.9, 0.4, 0.3, 0.1, 0.8, 0.2]
    labels = [1, 0, 0, 1, 1, 0]
    strata = ["a", "a", "a", "a", "b", "b"]
    res = mt.stratified_auc(scores, labels, strata)
    weighted = sum(r["auc"] * r["weight"] for r in res["per_stratum"]
                   if r["weight"]) / sum(r["weight"] for r in res["per_stratum"])
    assert res["auc"] == pytest.approx(weighted)


def test_stratified_auc_ignores_single_class_strata():
    res = mt.stratified_auc([0.9, 0.1, 0.5], [1, 1, 1], ["a", "a", "b"])
    assert math.isnan(res["auc"]) and res["n_strata_used"] == 0


# ------------------------------------------------------------------- 6. CUSUM
def test_cusum_no_alarm_on_stationary_noise():
    rng = np.random.default_rng(4)
    res = mt.cusum_detect(rng.normal(loc=0.3, scale=0.02, size=60), target=0.3)
    assert not res.alarm


def test_cusum_alarms_on_step_shift():
    series = [0.1] * 30 + [0.6] * 15
    res = mt.cusum_detect(series, target=0.1, slack=0.02, threshold=0.2)
    assert res.alarm and res.s_pos[-1] > 0.2


def test_trailing_drift_does_not_degenerate_when_window_exceeds_series():
    """踩过的坑：窗口 > 序列长度时 drift 恒为 0（假通过）。"""
    series = [0.0, 0.0, 0.0, 0.5, 0.6, 0.7]
    drift = mt.trailing_drift(series, window=30)
    assert drift["effective_window"] == 3
    assert drift["abs_drift"] > 0.4


# ------------------------------------------------------- 7. 负控与标签构造
def test_random_label_control_centers_on_half():
    samples = _synthetic([(80, 0.5)] * 5, signal=1.2, seed=8)
    for s in samples:
        s["y"] = s["y"]
    ctrl = mt.random_label_control(samples, "y", n_draws=12, seed=99)
    assert 0.35 <= ctrl["mean_auc"] <= 0.65


@pytest.mark.parametrize("row,kwargs,expected", [
    ({"pr_state": "merged", "status": "succeeded"}, {}, "merged"),
    ({"pr_state": "closed", "status": "succeeded"}, {}, "discarded"),
    ({"pr_state": "open", "status": "succeeded"}, {}, "pending"),
    ({"pr_state": "merged", "status": "succeeded"}, {"requeued": True}, "rework"),
    ({"pr_state": "merged", "status": "succeeded", "pr_hist":
      [{"note": "reverted due to red CI"}]}, {}, "reverted"),
    ({"pr_state": None, "status": "revoked"}, {}, "discarded"),
])
def test_classify_outcome_precedence(row, kwargs, expected):
    assert lg.classify_outcome(row, **kwargs)["outcome"] == expected


def test_rework_targets_only_counts_earlier_missions():
    goals = [(600, "修复 m500 的失败案例"), (1500, "修复 m3000 的问题"),
             (700, "清点 m500 的资产清单")]
    out = lg.rework_targets(goals)
    assert out == {500: 1}         # m3000 比点名者更新 → 不算；无返工语义的 goal 不计数


def test_grade_and_e_ordinals_default_to_one():
    assert lg.grade_ordinal("S4") == 4 and lg.grade_ordinal(None) == 1
    assert lg.e_ordinal("E3") == 3 and lg.e_ordinal("E?") == 1


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """假 DSN 连接：按 SQL 特征返回预置行，用于验证 cohort 组装（不碰真库）。"""

    def __init__(self, missions, goals, requests):
        self.missions, self.goals, self.requests = missions, goals, requests

    def execute(self, sql):
        s = str(sql)
        if "tensor_dev_missions m" in s:
            return _FakeResult(self.missions)
        if "mission_lifecycle_log" in s:
            return _FakeResult(self.requests)
        return _FakeResult(self.goals)


def test_build_cohort_assembles_labels_and_features():
    missions = [
        (1, "TensorLBM", "succeeded", "2026-11-04", "S2", "E2", True,
         '{"ok": true}', '{"a.py": "unverified"}', "[]", "merged", [], "{}", "goal"),
        (2, "TensorLBM", "succeeded", "2026-11-05", "S1", "E1", False,
         '{"ok": false}', "{}", '["FLAG"]', "open", [], "{}", "goal"),
    ]
    goals = [(9, "诊断并修复 m002 的缺陷")]
    conn = _FakeConn(missions, goals, [(2, "custody_requeue")])
    cohort = lg.build_cohort(conn)
    by_id = {c["mission_id"]: c for c in cohort}
    assert by_id[1]["y_merge"] == 1 and by_id[1]["observable_pr"] is True
    assert by_id[1]["x"] == [2.0, 2.0, 1.0, 1.0, 1.0, 0.0]
    assert by_id[2]["observable_pr"] is True and by_id[2]["y_merge"] == 0
    assert by_id[2]["rework"] is True and by_id[2]["y_defect"] == 1


def test_cohort_summary_counts():
    cohort = [
        {"day": "2026-11-04", "grade": "S2", "e_level": "E2", "repo": "R",
         "outcome": "merged", "y_merge": 1, "y_defect": 0},
        {"day": "2026-11-05", "grade": "S1", "e_level": "E1", "repo": "R",
         "outcome": "rework", "y_merge": 0, "y_defect": 1},
    ]
    s = lg.summarize(cohort)
    assert s["n"] == 2 and s["outcome_counts"]["merged"] == 1
    assert s["y_defect_positives"] == 1 and s["n_days"] == 2


# ------------------------------------------------------------- 8. 验收面
def _cohort_from_synthetic(samples, n_per_day_pos_neg=None):
    return [{"mission_id": i, "day": s["day"], "grade": "S2", "e_level": "E2",
             "repo": "R", "status": "succeeded", "observable_pr": True,
             "resolved": True, "outcome": "merged" if s["y"] else "rework",
             "merged": bool(s["y"]), "rework": not s["y"], "reverted": False,
             "discarded": False, "y_merge": int(s["y"]), "y_defect": int(not s["y"]),
             "x": s["x"], "evidence": []}
            for i, s in enumerate(samples)]


def test_acceptance_pass_path_on_strong_synthetic_signal(tmp_path):
    samples = _synthetic([(150, 0.5)] * 6, signal=2.2, seed=21)
    cohort = _cohort_from_synthetic(samples)
    res = acc.evaluate(cohort, repo_root=tmp_path / "none", n_control_draws=8, n_boot=200)
    assert res["checks"]["J1_auc_merge"]["pooled_pass"] is True
    assert res["checks"]["J4_random_control"]["pass"] is True


def test_acceptance_j5_flags_inflation(tmp_path):
    """S 高分占比从 0 涨到 0.7 → 漂移 > 5%，J5 必须判 FAIL。"""
    samples = _synthetic([(150, 0.5)] * 6, signal=2.0, seed=23)
    cohort = _cohort_from_synthetic(samples)
    for i, c in enumerate(cohort):
        c["grade"] = "S2" if i >= len(cohort) // 2 else "S1"
    res = acc.evaluate(cohort, repo_root=tmp_path / "none", n_control_draws=4, n_boot=50)
    assert res["checks"]["J5_inflation_cusum"]["pass"] is False
    assert res["checks"]["J5_inflation_cusum"]["value"] > 0.05


def test_acceptance_is_reproducible(tmp_path):
    samples = _synthetic([(60, 0.5)] * 5, signal=1.2, seed=31)
    cohort = _cohort_from_synthetic(samples)
    a = acc.evaluate(cohort, repo_root=tmp_path / "none", n_control_draws=5, n_boot=50, seed=7)
    b = acc.evaluate(cohort, repo_root=tmp_path / "none", n_control_draws=5, n_boot=50, seed=7)
    assert a["checks"]["J1_auc_merge"]["value"] == b["checks"]["J1_auc_merge"]["value"]
    assert a["checks"]["J4_random_control"]["value"] == b["checks"]["J4_random_control"]["value"]


def test_count_tests_finds_this_module_tests():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert acc.count_tests(root) >= 10          # J7 自证：本文件自己的用例数


def test_select_excludes_unobservable_pr_samples():
    cohort = [
        {"day": "2026-11-05", "observable_pr": False, "resolved": True, "y_merge": 0},
        {"day": "2026-11-05", "observable_pr": True, "resolved": True, "y_merge": 1},
    ]
    assert len(acc._select(cohort, "y_merge")) == 1
    assert len(acc._select(cohort, "y_defect")) == 2