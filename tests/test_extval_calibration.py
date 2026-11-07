"""IN56-N07 外部效度校准的单元/集成测试（不依赖数据库）。

覆盖：度量定义、Shapley 效率性、估计器正确性、walk-forward 时序纪律、
标签构造口径、以及"退化可识别性"这条本任务的核心结论。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.evaluation_ops.extval import analysis, dataset as ds_mod, models


# --------------------------------------------------------------------------
# 1. 度量：AUC 的解析基准与并列处理
# --------------------------------------------------------------------------

def test_roc_auc_matches_mann_whitney_reference():
    # 4 个正例 / 4 个负例，手工可数的 AUC = 20/16 归一化 -> 0.875
    y = [1, 1, 1, 1, 0, 0, 0, 0]
    s = [0.9, 0.8, 0.7, 0.4, 0.6, 0.5, 0.3, 0.2]
    # 正例得分: .9 .8 .7 .4 ; 负例: .6 .5 .3 .2
    wins = sum(1 for a in [0.9, 0.8, 0.7, 0.4] for b in [0.6, 0.5, 0.3, 0.2] if a > b)
    assert models.roc_auc(y, s) == pytest.approx(wins / 16)


def test_roc_auc_ties_count_half():
    # 全部并列 -> 0.5；单个并列对 -> 0.5*(1) 半步
    assert models.roc_auc([1, 0], [0.5, 0.5]) == pytest.approx(0.5)
    assert models.roc_auc([1, 0, 1, 0], [0.5, 0.5, 0.9, 0.1]) == pytest.approx((2 + 1 + 0.5) / 4)


def test_roc_auc_undefined_for_single_class():
    assert models.roc_auc([1, 1, 1], [0.1, 0.2, 0.3]) is None
    assert models.roc_auc([0, 0], [0.1, 0.2]) is None


def test_brier_and_calibration_identities():
    # 完美预测 -> Brier 0；常数 0.5 -> 0.25
    assert models.brier_score([1, 0], [1.0, 0.0]) == pytest.approx(0.0)
    assert models.brier_score([1, 0, 1, 0], [0.5] * 4) == pytest.approx(0.25)
    # 可靠性曲线覆盖全部样本，且分箱内 mean_pred 与 observed 落在 [0,1]
    y = [1, 0, 1, 1, 0, 1]
    p = [0.9, 0.1, 0.8, 0.4, 0.6, 0.7]
    curve = models.reliability_curve(y, p, n_bins=4)
    assert sum(c["n"] for c in curve) == len(y)
    assert all(0.0 <= c["observed"] <= 1.0 for c in curve)
    # ECE 在完美校准时为 0
    assert models.expected_calibration_error([1, 0], [1.0, 0.0], n_bins=2) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# 2. 估计器
# --------------------------------------------------------------------------

def test_logistic_regression_recovers_separable_signal():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(400, 3))
    y = (X[:, 0] + 0.5 * X[:, 2] > 0).astype(int)
    mdl = models.LogisticRegression(l2=1e-3).fit(X, y)
    p = mdl.predict_proba(X)
    assert models.roc_auc(y, p) > 0.9
    # 系数符号：第 0/2 维为正贡献，第 1 维为噪声（|coef| 更小）
    coef = mdl.coef_original_scale()
    assert coef[0] > 0 and coef[2] > 0
    assert abs(coef[1]) < abs(coef[0])


def test_logistic_regression_probabilities_bounded_and_monotone():
    X = np.array([[-2.0], [-1.0], [0.0], [1.0], [2.0], [-1.5], [1.5], [0.5]])
    y = np.array([0, 0, 0, 1, 1, 0, 1, 1])
    p = models.LogisticRegression().fit(X, y).predict_proba(X)
    assert np.all((p > 0) & (p < 1))
    assert np.all(np.diff(p[np.argsort(X[:, 0])]) >= -1e-9)


def test_random_forest_beats_chance_on_nonlinear_rule():
    rng = np.random.default_rng(11)
    X = rng.normal(size=(300, 4))
    y = ((X[:, 0] > 0) ^ (X[:, 1] > 0)).astype(int)
    rf = models.RandomForest(n_trees=60, max_depth=3, min_leaf=4, seed=1).fit(X, y)
    assert models.roc_auc(y, rf.predict_proba(X)) > 0.85
    assert rf.predict_proba(X[:5]).shape == (5,)


# --------------------------------------------------------------------------
# 3. Shapley 归因
# --------------------------------------------------------------------------

def test_sampling_shapley_satisfies_efficiency_and_sign():
    rng = np.random.default_rng(5)
    X = rng.normal(size=(120, 4))
    y = (X[:, 0] - X[:, 1] > 0).astype(int)
    mdl = models.LogisticRegression(l2=0.5).fit(X, y)
    Xe = X[:6]
    res = models.sampling_shapley(
        lambda M: mdl.predict_proba(M), Xe, X.mean(axis=0)[None, :], n_permutations=120
    )
    phi = np.asarray(res["phi"])
    assert phi.shape == Xe.shape
    assert max(abs(g) for g in res["efficiency_gap"]) < 1e-9
    # 正系数特征的 |phi| 应大于噪声特征
    assert np.abs(phi[:, 0]).mean() > np.abs(phi[:, 3]).mean()


def test_sampling_shapley_is_deterministic_for_fixed_seed():
    rng = np.random.default_rng(9)
    X = rng.normal(size=(40, 3))
    mdl = models.LogisticRegression().fit(X, (X[:, 0] > 0).astype(int))
    f = lambda M: mdl.predict_proba(M)  # noqa: E731
    a = models.sampling_shapley(f, X[:4], X.mean(axis=0)[None, :], n_permutations=30, seed=42)
    b = models.sampling_shapley(f, X[:4], X.mean(axis=0)[None, :], n_permutations=30, seed=42)
    assert a["phi"] == b["phi"]


# --------------------------------------------------------------------------
# 4. walk-forward 时序纪律
# --------------------------------------------------------------------------

def test_expanding_splits_train_strictly_before_test():
    times = [f"2026-11-0{1 + i // 5}T0{i % 5}:00:00+00:00" for i in range(25)]
    splits = analysis.expanding_splits(times, n_folds=4, min_train_frac=0.4)
    assert len(splits) == 4
    analysis.assert_no_leakage(splits)
    for sp in splits:
        assert set(sp.train_idx).isdisjoint(sp.test_idx)
        assert max(times[i] for i in sp.train_idx) < min(times[i] for i in sp.test_idx)


def test_assert_no_leakage_rejects_bad_split():
    bad = analysis.Split(train_idx=[0, 1], test_idx=[1, 2],
                         t_train_max="2026-11-02T00:00:00+00:00", t_test_min="2026-11-01T00:00:00+00:00")
    with pytest.raises(AssertionError):
        analysis.assert_no_leakage([bad])


def test_walk_forward_detects_injected_signal_and_flags_degenerate_labels():
    """正控（有时间铺开的信号）应判可识别；单波负类标签应被判为不可识别。"""
    rng = np.random.default_rng(21)
    n = 300
    X = rng.normal(size=(n, 3))
    times = [f"2026-11-{d:02d}T{(i % 20):02d}:00:00+00:00" for i, d in enumerate(rng.integers(1, 8, n))]
    times = sorted(times)
    y_good = (X[:, 0] + rng.normal(0, 0.3, n) > 0).astype(int)
    good = analysis.walk_forward(X, y_good, times, analysis.make_logreg, n_folds=4)
    assert good["identifiable"] is True
    assert good["pooled_auc"] > 0.85

    # 退化：负类全部集中在时间序列尾部（模拟"一次回填波"造成的结构性无负类训练集）
    y_bad = np.ones(n, dtype=int)
    y_bad[int(0.8 * n):] = 0
    bad = analysis.walk_forward(X, y_bad, times, analysis.make_logreg, n_folds=5)
    assert bad["identifiable"] is False
    assert bad["folds_with_single_class_train"] >= 1


def test_stratified_cv_on_pure_noise_is_near_chance():
    rng = np.random.default_rng(33)
    X = rng.normal(size=(200, 4))
    y = rng.integers(0, 2, 200)
    res = analysis.stratified_cv(X, y, analysis.make_logreg, k=5, reps=3, seed=1)
    assert res["auc_mean"] is not None
    assert 0.35 < res["auc_mean"] < 0.65


# --------------------------------------------------------------------------
# 5. 标签构造（纯函数，无数据库）
# --------------------------------------------------------------------------

def _mission(mid, status="succeeded", finished="2026-11-03T10:00:00+00:00",
             created="2026-11-03T08:00:00+00:00", result=None, goal="验证 x"):
    return {
        "id": mid, "status": status, "goal": goal, "repo": "TensorX",
        "budget": {"max_tool_calls": 80},
        "result": result if result is not None else {"tool_calls": 40, "compactions": 1,
                                                     "changed_files": ["a.py", "b.py"],
                                                     "checks": "ok", "accepted": True,
                                                     "git_status": ""},
        "created_at": created, "finished_at": finished,
    }


def _grade(mid, grade="S2", e="E2"):
    return {"mission_id": mid, "grade": grade, "e_level": e, "evidence_refs": '["r1","r2"]',
            "reason_codes": "[]", "status": "confirmed",
            "decided_at": "2026-11-07T00:00:00+00:00"}


def _pr(mid, state, at):
    return {"mission_id": mid, "action": "pr_gate", "detail": {"state": state}, "created_at": at}


def test_derive_samples_labels_from_downstream_events():
    missions = [
        _mission(1), _mission(2), _mission(3),
        _mission(4, status="failed"),
        _mission(5),  # 无任何下游事件 -> 剔除
    ]
    grades = [_grade(i) for i in (1, 2, 3, 4, 5)]
    lifecycle = [
        _pr(1, "merged", "2026-11-03T12:00:00+00:00"),
        _pr(2, "open", "2026-11-03T12:00:00+00:00"),
        _pr(3, "closed", "2026-11-03T13:00:00+00:00"),
    ]
    ds = ds_mod.derive_samples(missions, grades, lifecycle, as_of="2026-11-07T00:00:00+00:00", horizon_hours=48)
    by_id = {s.mission_id: s for s in ds.samples}
    assert by_id[1].y == 1 and by_id[1].label_kind == "merged"
    assert by_id[2].y == 0 and by_id[2].label_kind == "open_timeout"
    assert by_id[3].y == 0 and by_id[3].label_kind == "closed"
    assert by_id[4].y == 0 and by_id[4].label_kind == "status_fail"
    assert 5 not in by_id
    assert ds.excluded_counts.get("no_downstream_event", 0) == 1
    # 每行都满足泄漏闸门
    assert all(s.t_features < s.t_label for s in ds.samples)


def test_derive_samples_drops_post_hoc_rows_and_respects_horizon():
    # PR open 才 1 小时（< 48h 观察窗）-> 剔除为 pr_open_within_horizon
    missions = [_mission(1, finished="2026-11-06T23:00:00+00:00"), _mission(2)]
    grades = [_grade(1), _grade(2)]
    lifecycle = [
        _pr(1, "open", "2026-11-06T23:30:00+00:00"),
        # 合并事件早于 mission 完结（回填/时钟错序）-> 必须被泄漏闸门剔除
        _pr(2, "merged", "2026-11-03T09:00:00+00:00"),
    ]
    ds = ds_mod.derive_samples(missions, grades, lifecycle, as_of="2026-11-07T00:00:00+00:00", horizon_hours=48)
    assert ds.n == 0
    assert ds.excluded_counts.get("pr_open_within_horizon", 0) == 1
    assert ds.excluded_counts.get("label_not_after_features", 0) == 1


def test_build_features_is_deterministic_and_bounded():
    f1 = ds_mod.build_features(_mission(1), _grade(1, "S-N", "E3"))
    f2 = ds_mod.build_features(_mission(1), _grade(1, "S-N", "E3"))
    assert f1 == f2
    assert f1["s_ord"] == 0.0 and f1["e_ord"] == 3.0   # S-N 走新颖性分支
    assert f1["evidence_n"] == 2.0
    assert f1["changed_files_n"] == 2.0
    assert set(f1) == set(ds_mod.FEATURE_NAMES)


def test_simple_rule_and_grade_only_baselines_are_ordered():
    F = list(ds_mod.FEATURE_NAMES)
    X = np.array([
        [1, 2, 3, 0, 1, 1, 40, 0, 2, 0.2, 0, 1, 0, 1.0, 80, 0.1],   # S1E2 -> 规则判成功
        [1, 1, 3, 0, 1, 1, 40, 0, 2, 0.2, 0, 1, 0, 1.0, 80, 0.1],   # S1E1 -> 规则判失败
        [4, 3, 3, 0, 1, 1, 40, 0, 2, 0.2, 0, 1, 0, 1.0, 80, 0.1],   # S4E3 -> 规则判失败
    ], dtype=float)
    score = analysis.simple_rule_score(X, F)
    assert score[0] > score[1] and score[0] > score[2]
    y = np.array([1, 0, 0])
    assert analysis.rule_metrics(y, score)["auc"] == pytest.approx(1.0)


def test_positive_and_negative_controls_behave_as_designed():
    rng = np.random.default_rng(77)
    n = 240
    X = rng.normal(size=(n, len(ds_mod.FEATURE_NAMES)))
    times = sorted(f"2026-11-{1 + i % 6:02d}T{i % 23:02d}:00:00+00:00" for i in range(n))
    pos = analysis.positive_control(X, times, n_folds=4, seed=1)
    assert pos["pooled_auc"] > 0.75 and pos["identifiable"] is True

    y = rng.integers(0, 2, n)
    neg = analysis.negative_control(X, y, times, n_folds=4, seed=1)
    assert neg["pooled_auc"] is None or 0.2 < neg["pooled_auc"] < 0.8


def test_evaluate_criteria_requires_identifiability_for_auc():
    dataset = {
        "n": 250, "n_positive": 125, "excluded_counts": {}, "label_kind_counts": {"merged": 125},
        "samples": [{"mission_id": i, "t_features": "2026-11-01T00:00:00+00:00",
                     "t_label": "2026-11-02T00:00:00+00:00", "y": 1} for i in range(250)],
    }
    wf = {
        "pooled_auc": 0.80, "pooled_brier": 0.10, "pooled_ece": 0.05,
        "identifiable": False, "folds_with_single_class_train": 0, "n_folds": 5,
        "folds_with_single_class_test": 5, "scored_classes": [1],
        "fold_auc_defined": 0, "n_scored": 150, "coverage": 0.6,
        "auc_ci": {"lo": 0.7, "hi": 0.9}, "folds": [],
    }
    result = {
        "walk_forward": {"primary": wf, "logreg": wf},
        "n": 250, "primary_model": "logreg",
        "metrics": {"simple_rule": {"auc": 0.5, "n_pos_pred": 10},
                    "random_baseline": {"brier_base_rate": 0.25}},
        "shap": {"importance": [{"feature": "s_ord"}], "max_abs_efficiency_gap": 1e-12},
        "controls": {}, "structural": {
            "walk_forward_identifiable": False, "folds_with_single_class_train": 0, "n_folds": 5,
            "folds_with_single_class_test": 5, "scored_classes": [1],
            "negative_max_t_features": "2026-11-01T00:00:00+00:00",
            "positive_after_last_negative": 0,
            "oof_coverage": 0.6, "negative_class_distinct_finish_days": 1,
            "negative_by_finish_day": {}, "grade_counts": {}},
        "association": {"full": {}, "grade_only": {}, "batch_only": None, "no_batch": {},
                        "permutation_null_full": {}, "permutation_null_grade_only": {},
                        "within_delivery_wave": {}},
        "batch_proxy_features": ["duration_h"],
    }
    from tools.evaluation_ops.extval import acceptance as acc_mod

    acc = acc_mod.evaluate_criteria(dataset, result, pytest_cases=12, pytest_passed=12)
    crit = {c["criterion"]: c for c in acc["criteria"]}
    assert crit["walk_forward_auc>=0.65"]["pass"] is False   # 不可识别 -> 不得判 PASS
    assert crit["brier<=0.25"]["status"] == "WITHHELD"       # 折外只有单类 -> Brier 无意义
    assert crit["no_temporal_leakage"]["pass"] is True
    assert crit["n_samples>=200"]["pass"] is True
    assert crit["pytest>=10_cases"]["pass"] is True
    assert acc["verdict"] == "FAIL"