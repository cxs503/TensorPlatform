"""Walk-forward 交叉验证 + 关联性诊断 + 基线 + Shapley + 出图。

三层口径（层级越靠后越"宽松"，报告中必须分层报，不得混用）：

- **L1 primary（判据口径）**：扩张窗口 walk-forward，按 ``t_features``（mission 完结时刻）
  排序，每折 train 全早于 test。这是唯一满足"时间序列交叉验证无数据泄露"的口径。
- **L2 within-period（批次控制口径）**：把队列限制在**同一交付波（同一天）**内做分层
  k 折。用途是剔除"批次/时代"混淆——负类在平台上只由一次回填波产生，
  跨时间的差异未必是判级的判别力。
- **L3 controls（仪器自检）**：正控（向真实特征注入已知信号）与负控（标签置换），
  用来证明"L1 的退化是数据结构性质，不是估计器的 bug"。

判据数字一律取 L1；L2/L3 只作解释性证据。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np

from . import models

SCHEMA_VERSION = "extval.v1"

#: 批次代理特征（非因果、在负类/正类之间相差数倍，等价于"交付波"标识）
BATCH_PROXY_FEATURES = ("duration_h", "goal_len")


# --------------------------------------------------------------------------
# 时间序列切分
# --------------------------------------------------------------------------

@dataclass
class Split:
    train_idx: list[int]
    test_idx: list[int]
    t_train_max: str
    t_test_min: str


def expanding_splits(times: Sequence[str], n_folds: int = 5, min_train_frac: float = 0.4) -> list[Split]:
    """按时间的扩张窗口切分；每折保证 train 全早于 test（同刻按索引归块）。"""
    order = list(range(len(times)))
    order.sort(key=lambda i: (times[i], i))
    n = len(order)
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    min_train = max(1, int(round(n * min_train_frac)))
    if n - min_train < n_folds:
        n_folds = max(1, n - min_train)
    if n_folds < 1 or n <= min_train:
        raise ValueError("not enough samples for walk-forward split")
    remaining = n - min_train
    blocks = np.array_split(np.arange(remaining), n_folds)
    splits: list[Split] = []
    for block in blocks:
        if block.size == 0:
            continue
        start = min_train + int(block[0])
        end = min_train + int(block[-1]) + 1
        # 同刻样本不得跨折（否则 train_max == test_min，信息同时可得，必须整体归入 test 侧）
        while start < end and times[order[start - 1]] == times[order[start]]:
            start += 1
        if start >= end:
            continue
        splits.append(
            Split(
                train_idx=order[:start],
                test_idx=order[start:end],
                t_train_max=max(times[i] for i in order[:start]),
                t_test_min=min(times[i] for i in order[start:end]),
            )
        )
    return splits


def assert_no_leakage(splits: Sequence[Split]) -> None:
    for k, sp in enumerate(splits):
        if not sp.t_train_max < sp.t_test_min:
            raise AssertionError(
                f"temporal leakage at fold {k}: train_max={sp.t_train_max} >= test_min={sp.t_test_min}"
            )
        if set(sp.train_idx) & set(sp.test_idx):
            raise AssertionError(f"index overlap at fold {k}")


# --------------------------------------------------------------------------
# 估计器工厂
# --------------------------------------------------------------------------

def make_logreg() -> Any:
    return models.LogisticRegression(l2=1.0)


def make_forest() -> Any:
    return models.RandomForest(n_trees=200, max_depth=4, min_leaf=5)


# --------------------------------------------------------------------------
# L1：walk-forward
# --------------------------------------------------------------------------

def walk_forward(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    times: Sequence[str],
    model_factory: Callable[[], Any],
    n_folds: int = 5,
) -> dict[str, Any]:
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=int)
    splits = expanding_splits(times, n_folds=n_folds)
    assert_no_leakage(splits)
    oof_pred = np.full(len(y_arr), np.nan)
    oof_fold = np.full(len(y_arr), -1, dtype=int)
    folds: list[dict[str, Any]] = []
    model_name = type(model_factory()).__name__
    for k, sp in enumerate(splits):
        model = model_factory()
        tr_y = y_arr[sp.train_idx]
        model.fit(X_arr[sp.train_idx], tr_y)
        p = np.asarray(model.predict_proba(X_arr[sp.test_idx]), dtype=float)
        oof_pred[sp.test_idx] = p
        oof_fold[sp.test_idx] = k
        folds.append(
            {
                "fold": k,
                "n_train": len(sp.train_idx),
                "n_test": len(sp.test_idx),
                "n_pos_train": int(tr_y.sum()),
                "n_pos_test": int(y_arr[sp.test_idx].sum()),
                "train_single_class": bool(len(np.unique(tr_y)) < 2),
                "n_neg_train": int((tr_y == 0).sum()),
                "n_neg_test": int((y_arr[sp.test_idx] == 0).sum()),
                "test_single_class": bool(len(np.unique(y_arr[sp.test_idx])) < 2),
                "t_train_max": sp.t_train_max,
                "t_test_min": sp.t_test_min,
                "auc": models.roc_auc(y_arr[sp.test_idx], p),
                "brier": models.brier_score(y_arr[sp.test_idx], p),
            }
        )
    covered = ~np.isnan(oof_pred)
    y_cov = y_arr[covered]
    pooled_auc = models.roc_auc(y_cov, oof_pred[covered])
    pooled_brier = models.brier_score(y_cov, oof_pred[covered])
    fold_aucs = [f["auc"] for f in folds if f["auc"] is not None]
    fold_briers = [f["brier"] for f in folds if f["brier"] is not None]
    single_class_train = sum(1 for f in folds if f["train_single_class"])
    return {
        "model": model_name,
        "n_folds": len(folds),
        "folds": folds,
        "fold_auc_defined": len(fold_aucs),
        "folds_with_single_class_train": single_class_train,
        "folds_with_single_class_test": sum(1 for f in folds if f["test_single_class"]),
        "scored_classes": sorted(int(v) for v in np.unique(y_cov)) if y_cov.size else [],
        "identifiable": bool(fold_aucs and len(fold_aucs) == len(folds)),
        "pooled_auc": pooled_auc,
        "pooled_brier": pooled_brier,
        "pooled_log_loss": models.log_loss(y_cov, oof_pred[covered]),
        "pooled_ece": models.expected_calibration_error(y_cov, oof_pred[covered]),
        "auc_fold_mean": float(np.mean(fold_aucs)) if fold_aucs else None,
        "auc_fold_std": float(np.std(fold_aucs)) if fold_aucs else None,
        "brier_fold_mean": float(np.mean(fold_briers)) if fold_briers else None,
        "n_scored": int(covered.sum()),
        "coverage": float(covered.mean()),
        "auc_ci": models.bootstrap_auc_ci(y_cov, oof_pred[covered]),
        "oof_pred": oof_pred,
        "oof_fold": oof_fold,
        "splits": splits,
    }


# --------------------------------------------------------------------------
# L2：分层 k 折（批次控制/关联性口径）
# --------------------------------------------------------------------------

def stratified_cv(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    model_factory: Callable[[], Any] = make_logreg,
    k: int = 5,
    reps: int = 5,
    seed: int = 20261107,
) -> dict[str, Any]:
    """重复分层 k 折；返回 AUC 均值/标准差、Brier、以及最后一次重复的折外预测。"""
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=int)
    if len(np.unique(y_arr)) < 2:
        return {"auc_mean": None, "auc_std": None, "brier_mean": None, "reps": 0, "oof": None}
    rng = np.random.default_rng(seed)
    aucs, briers = [], []
    oof_first = None
    for rep in range(reps):
        oof = np.full(len(y_arr), np.nan)
        pos = np.where(y_arr == 1)[0].copy()
        neg = np.where(y_arr == 0)[0].copy()
        rng.shuffle(pos)
        rng.shuffle(neg)
        for f in range(k):
            te = np.concatenate([pos[f::k], neg[f::k]])
            if te.size == 0:
                continue
            tr = np.setdiff1d(np.arange(len(y_arr)), te)
            if len(np.unique(y_arr[tr])) < 2:
                continue
            mdl = model_factory()
            mdl.fit(X_arr[tr], y_arr[tr])
            oof[te] = np.asarray(mdl.predict_proba(X_arr[te]), dtype=float)
        cov = ~np.isnan(oof)
        if cov.sum() == 0:
            continue
        aucs.append(models.roc_auc(y_arr[cov], oof[cov]))
        briers.append(models.brier_score(y_arr[cov], oof[cov]))
        if rep == 0:
            oof_first = oof
    aucs_v = [a for a in aucs if a is not None]
    briers_v = [b for b in briers if b is not None]
    return {
        "auc_mean": float(np.mean(aucs_v)) if aucs_v else None,
        "auc_std": float(np.std(aucs_v)) if aucs_v else None,
        "brier_mean": float(np.mean(briers_v)) if briers_v else None,
        "reps": len(aucs_v),
        "oof": oof_first,
    }


def permutation_null(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    model_factory: Callable[[], Any] = make_logreg,
    n_permutations: int = 40,
    seed: int = 20261107,
) -> dict[str, Any]:
    """标签置换零分布：p 值 = P(AUC_null ≥ AUC_obs)。"""
    X_arr = np.asarray(X, dtype=float)
    y_arr = np.asarray(y, dtype=int)
    obs = stratified_cv(X_arr, y_arr, model_factory, reps=3, seed=seed)["auc_mean"]
    rng = np.random.default_rng(seed + 7)
    null = []
    for i in range(n_permutations):
        yp = rng.permutation(y_arr)
        res = stratified_cv(X_arr, yp, model_factory, reps=1, seed=seed + 100 + i)["auc_mean"]
        if res is not None:
            null.append(res)
    null_arr = np.asarray(null)
    if obs is None or null_arr.size == 0:
        return {"observed": obs, "n_permutations": 0, "p_value": None, "null_mean": None, "null_p95": None}
    return {
        "observed": obs,
        "null_mean": float(null_arr.mean()),
        "null_p95": float(np.percentile(null_arr, 95)),
        "n_permutations": int(null_arr.size),
        "p_value": float((null_arr >= obs).mean()),
    }


# --------------------------------------------------------------------------
# 基线
# --------------------------------------------------------------------------

def grade_ordinals(X: np.ndarray, feature_names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    return X[:, feature_names.index("s_ord")], X[:, feature_names.index("e_ord")]


def simple_rule_score(X: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
    """任务书口径的简单规则：S1/S2 判成功、E≤1（证据弱）判失败。

    score = [S1,S2] - [E≤1] + 0.5·[S1,S2 且 E≥2]；末项只细化同级内次序。
    """
    s, e = grade_ordinals(X, feature_names)
    good_s = ((s >= 1) & (s <= 2)).astype(float)
    weak_e = (e <= 1).astype(float)
    return good_s - weak_e + 0.5 * good_s * (e >= 2)


def rule_metrics(y: Sequence[int], score: Sequence[float]) -> dict[str, Any]:
    y_arr = np.asarray(y, dtype=int)
    s_arr = np.asarray(score, dtype=float)
    span = float(np.ptp(s_arr)) or 1.0
    p = (s_arr - s_arr.min()) / span
    return {
        "auc": models.roc_auc(y_arr, s_arr),
        "brier": models.brier_score(y_arr, p),
        "n_pos_pred": int((s_arr > 0.5).sum()),
    }


def constant_baseline(y: Sequence[int]) -> dict[str, Any]:
    y_arr = np.asarray(y, dtype=int)
    rate = float(y_arr.mean()) if y_arr.size else None
    return {
        "auc": 0.5 if y_arr.size else None,
        "brier_base_rate": models.brier_score(y_arr, np.full(y_arr.size, rate if rate is not None else 0.5)),
        "positive_rate": rate,
        "note": "随机猜测：AUC 恒 0.5；Brier 取训练正类率常数预测（无技能参考）。",
    }


# --------------------------------------------------------------------------
# 仪器控制（L3）
# --------------------------------------------------------------------------

def positive_control(
    X: Sequence[Sequence[float]],
    times: Sequence[str],
    n_folds: int = 5,
    noise: float = 0.15,
    seed: int = 20261107,
) -> dict[str, Any]:
    """正控：用真实特征的一个已知线性组合造标签（加噪），跑同一 walk-forward 管线。

    若管线正确，应当把注入的判别力找回来（AUC 明显 > 0.5）；否则说明是代码缺陷。
    """
    X_arr = np.asarray(X, dtype=float)
    rng = np.random.default_rng(seed)
    z = (
        0.9 * _z(X_arr[:, 0])
        - 0.6 * _z(X_arr[:, 1])
        + 0.7 * _z(X_arr[:, min(6, X_arr.shape[1] - 1)])
        + 0.5 * _z(X_arr[:, min(13, X_arr.shape[1] - 1)])
    )
    z = z + rng.normal(0, noise, size=z.size)
    y_syn = (z > np.median(z)).astype(int)
    res = walk_forward(X_arr, y_syn, times, make_logreg, n_folds=n_folds)
    return {
        "pooled_auc": res["pooled_auc"],
        "pooled_brier": res["pooled_brier"],
        "identifiable": res["identifiable"],
        "n_pos": int(y_syn.sum()),
        "noise": noise,
        "note": "已知信号注入；AUC 应显著 > 0.5 且 Brier 应低。",
    }


def negative_control(
    X: Sequence[Sequence[float]], y: Sequence[int], times: Sequence[str],
    n_folds: int = 5, seed: int = 20261107,
) -> dict[str, Any]:
    """负控：标签随机置换（保留类别比例与时序位置），AUC 应回落到 ~0.5。"""
    y_arr = np.asarray(y, dtype=int)
    rng = np.random.default_rng(seed)
    y_perm = rng.permutation(y_arr)
    res = walk_forward(np.asarray(X, dtype=float), y_perm, times, make_logreg, n_folds=n_folds)
    return {
        "pooled_auc": res["pooled_auc"],
        "pooled_brier": res["pooled_brier"],
        "note": "标签置换负控；AUC 应 ≈ 0.5。",
    }


def _z(v: np.ndarray) -> np.ndarray:
    sd = float(np.std(v)) or 1.0
    return (v - float(np.mean(v))) / sd


# --------------------------------------------------------------------------
# 主分析
# --------------------------------------------------------------------------

def analyze(
    dataset: dict[str, Any],
    n_folds: int = 5,
    shap_sample: int = 80,
    shap_permutations: int = 120,
    assoc_reps: int = 5,
    null_permutations: int = 40,
    seed: int = 20261107,
) -> dict[str, Any]:
    feature_names = list(dataset["feature_names"])
    rows = dataset["samples"]
    X = np.asarray([[r["features"][k] for k in feature_names] for r in rows], dtype=float)
    y = np.asarray([r["y"] for r in rows], dtype=int)
    times = [r["t_features"] for r in rows]

    lr = walk_forward(X, y, times, make_logreg, n_folds=n_folds)
    rf = walk_forward(X, y, times, make_forest, n_folds=n_folds)
    s_idx, e_idx = feature_names.index("s_ord"), feature_names.index("e_ord")
    grade_wf = walk_forward(X[:, [s_idx, e_idx]], y, times, make_logreg, n_folds=n_folds)

    # 判据口径：主模型 = L1 pooled AUC 较高者（两者都可比才选；退化时选 logreg）
    primary_name = "logreg"
    primary = lr
    if (rf["pooled_auc"] or 0) > (lr["pooled_auc"] or 0) + 1e-9:
        primary_name, primary = "random_forest", rf

    # ---- L2：关联性（全体 + 批次控制 + 判级单变量）----
    batch_idx = [feature_names.index(f) for f in BATCH_PROXY_FEATURES if f in feature_names]
    keep_no_batch = [j for j in range(len(feature_names)) if j not in batch_idx]
    assoc_full = stratified_cv(X, y, make_logreg, reps=assoc_reps, seed=seed)
    assoc_grade = stratified_cv(X[:, [s_idx, e_idx]], y, make_logreg, reps=assoc_reps, seed=seed)
    assoc_batch = stratified_cv(X[:, batch_idx], y, make_logreg, reps=assoc_reps, seed=seed) if batch_idx else None
    assoc_no_batch = stratified_cv(X[:, keep_no_batch], y, make_logreg, reps=assoc_reps, seed=seed)
    null_full = permutation_null(X, y, make_logreg, n_permutations=null_permutations, seed=seed)
    null_grade = permutation_null(X[:, [s_idx, e_idx]], y, make_logreg, n_permutations=max(10, null_permutations // 2), seed=seed)

    # ---- L2b：同交付波（同一天）批次控制队列 ----
    days: dict[str, list[int]] = {}
    for i, t in enumerate(times):
        days.setdefault(t[:10], []).append(i)
    within = {}
    for day, idx in sorted(days.items()):
        y_day = y[idx]
        if 0 < int(y_day.sum()) < len(idx):
            Xd = X[idx]
            within[day] = {
                "n": len(idx),
                "n_pos": int(y_day.sum()),
                "full": stratified_cv(Xd, y_day, make_logreg, reps=assoc_reps, seed=seed)["auc_mean"],
                "grade_only": stratified_cv(Xd[:, [s_idx, e_idx]], y_day, make_logreg, reps=assoc_reps, seed=seed)["auc_mean"],
                "batch_only": (
                    stratified_cv(Xd[:, batch_idx], y_day, make_logreg, reps=assoc_reps, seed=seed)["auc_mean"]
                    if batch_idx
                    else None
                ),
                "no_batch": stratified_cv(Xd[:, keep_no_batch], y_day, make_logreg, reps=assoc_reps, seed=seed)["auc_mean"],
            }
            within[day]["oof"] = stratified_cv(Xd, y_day, make_logreg, reps=1, seed=seed)["oof"]

    rule_score = simple_rule_score(X, feature_names)
    rule = rule_metrics(y, rule_score)
    rand = constant_baseline(y)

    # ---- L3：仪器控制 ----
    controls = {
        "positive": positive_control(X, times, n_folds=n_folds, seed=seed),
        "negative": negative_control(X, y, times, n_folds=n_folds, seed=seed),
    }

    # ---- Shapley（全量拟合；背景=训练均值单点，效率性精确）----
    full_model = models.LogisticRegression(l2=1.0).fit(X, y)
    rng = np.random.default_rng(seed)
    explain_idx = rng.choice(len(y), size=min(shap_sample, len(y)), replace=False)
    background = X[explain_idx].mean(axis=0)[None, :]
    shap = models.sampling_shapley(
        lambda M: models.LogisticRegression.predict_proba(full_model, M),
        X[explain_idx],
        background,
        n_permutations=shap_permutations,
        seed=seed,
    )
    phi = np.asarray(shap["phi"], dtype=float)
    mean_abs = np.abs(phi).mean(axis=0)
    order = np.argsort(-mean_abs)
    importance = [
        {
            "feature": feature_names[i],
            "mean_abs_shap": float(mean_abs[i]),
            "mean_shap": float(phi[:, i].mean()),
            "coef_original_scale": float(full_model.coef_original_scale()[i]),
            "is_batch_proxy": feature_names[i] in BATCH_PROXY_FEATURES,
        }
        for i in order
    ]

    # ---- 森林置换重要性（第二证据源）----
    rng_p = np.random.default_rng(seed + 1)
    rf_full = models.RandomForest(n_trees=120, seed=seed).fit(X, y)
    base_auc = models.roc_auc(y, rf_full.predict_proba(X)) or 0.0
    perm_rows = []
    for j, name in enumerate(feature_names):
        drops = []
        for _ in range(5):
            Xp = X.copy()
            Xp[:, j] = Xp[rng_p.permutation(len(Xp)), j]
            drops.append(base_auc - (models.roc_auc(y, rf_full.predict_proba(Xp)) or 0.0))
        perm_rows.append({"feature": name, "perm_auc_drop": float(np.mean(drops))})
    perm_rows.sort(key=lambda r: -r["perm_auc_drop"])

    structural = _structural_audit(dataset, lr, rf, grade_wf, rule)

    return {
        "schema_version": SCHEMA_VERSION,
        "n": int(len(y)),
        "n_positive": int(y.sum()),
        "positive_rate": float(y.mean()),
        "n_folds": n_folds,
        "feature_names": feature_names,
        "batch_proxy_features": list(BATCH_PROXY_FEATURES),
        "primary_model": primary_name,
        "walk_forward": {
            "logreg": _strip(lr),
            "random_forest": _strip(rf),
            "grade_only": _strip(grade_wf),
            "primary": _strip(primary),
        },
        "metrics": {"primary": _strip(primary), "random_baseline": rand, "simple_rule": rule},
        "association": {
            "full": _strip_assoc(assoc_full),
            "grade_only": _strip_assoc(assoc_grade),
            "batch_only": _strip_assoc(assoc_batch) if assoc_batch else None,
            "no_batch": _strip_assoc(assoc_no_batch),
            "permutation_null_full": null_full,
            "permutation_null_grade_only": null_grade,
            "within_delivery_wave": {
                k: {kk: vv for kk, vv in v.items() if kk != "oof"} for k, v in within.items()
            },
        },
        "controls": controls,
        "structural": structural,
        "shap": {
            "n_explain": int(len(explain_idx)),
            "n_permutations": shap_permutations,
            "background": "训练集被解释子集的均值单点 -> 效率性精确成立",
            "base_value": shap["base_value"],
            "max_abs_efficiency_gap": float(np.max(np.abs(shap["efficiency_gap"]))) if len(shap["efficiency_gap"]) else None,
            "importance": importance,
            "phi_explain": phi.tolist(),
            "explain_mission_ids": [rows[i]["mission_id"] for i in explain_idx],
            "explain_y": [int(y[i]) for i in explain_idx],
        },
        "permutation_importance": perm_rows,
        "oof": {
            "logreg": lr["oof_pred"].tolist(),
            "random_forest": rf["oof_pred"].tolist(),
            "grade_only": grade_wf["oof_pred"].tolist(),
            "fold": lr["oof_fold"].tolist(),
            "y": y.tolist(),
            "times": times,
            "within_wave": {k: (v["oof"].tolist() if v.get("oof") is not None else None) for k, v in within.items()},
            "assoc_full": assoc_full["oof"].tolist() if assoc_full["oof"] is not None else None,
            "assoc_grade_only": assoc_grade["oof"].tolist() if assoc_grade["oof"] is not None else None,
        },
        "rule_score": rule_score.tolist(),
    }


def _structural_audit(dataset, lr, rf, grade_wf, rule) -> dict[str, Any]:
    excluded = dataset["excluded"]
    n_excl = sum(dataset["excluded_counts"].values())
    decided = [s.get("grade_decided_at") for s in dataset["samples"]]
    post_hoc = sum(1 for s in dataset["samples"] if s.get("grade_decided_at") and s["grade_decided_at"] > s["t_label"])
    neg_wave: dict[str, int] = {}
    for s in dataset["samples"]:
        if s["y"] == 0:
            neg_wave[s["t_features"][:10]] = neg_wave.get(s["t_features"][:10], 0) + 1
    return {
        "n_cohort": dataset["n"],
        "n_excluded": n_excl,
        "excluded_counts": dataset["excluded_counts"],
        "label_kind_counts": dataset["label_kind_counts"],
        "grade_counts": dataset["grade_counts"],
        "e_level_counts": dataset["e_level_counts"],
        "negative_by_finish_day": dict(sorted(neg_wave.items())),
        "negative_class_distinct_finish_days": len(neg_wave),
        "grade_decided_after_label": {"n": post_hoc, "of": len(decided)},
        "walk_forward_identifiable": bool(lr["identifiable"]),
        "folds_with_single_class_train": lr["folds_with_single_class_train"],
        "folds_with_single_class_test": lr["folds_with_single_class_test"],
        "scored_classes": lr["scored_classes"],
        "n_folds": lr["n_folds"],
        "negative_max_t_features": max(
            (s["t_features"] for s in dataset["samples"] if s["y"] == 0), default=None),
        "positive_after_last_negative": sum(
            1 for s in dataset["samples"]
            if s["y"] == 1 and dataset["samples"]
            and s["t_features"] > max((q["t_features"] for q in dataset["samples"] if q["y"] == 0), default="")),
        "oof_coverage": lr["coverage"],
        "simple_rule_hit_rate": rule["n_pos_pred"],
    }


def _strip(res: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in res.items() if k not in ("oof_pred", "oof_fold", "splits")}


def _strip_assoc(res: dict[str, Any] | None) -> dict[str, Any] | None:
    if res is None:
        return None
    return {k: v for k, v in res.items() if k != "oof"}


def _strip_deep(result: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in result.items() if k not in ("oof", "shap", "rule_score")}
    out["shap"] = {k: v for k, v in result.get("shap", {}).items() if k != "phi_explain"}
    return out


# --------------------------------------------------------------------------
# 出图
# --------------------------------------------------------------------------

def _roc_points(y: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(-s, kind="mergesort")
    y_sorted = y[order]
    tp = np.cumsum(y_sorted)
    fp = np.cumsum(1 - y_sorted)
    tpr = tp / max(1, tp[-1]) if len(tp) else np.array([0.0])
    fpr = fp / max(1, fp[-1]) if len(fp) else np.array([0.0])
    return np.concatenate([[0.0], fpr]), np.concatenate([[0.0], tpr])


def make_figures(dataset: dict[str, Any], result: dict[str, Any], outdir: str) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    paths: list[str] = []
    y = np.asarray(result["oof"]["y"], dtype=int)

    # 1) ROC：walk-forward（主判据） + 关联性（批次控制）
    fig, ax = plt.subplots(figsize=(6.2, 5.4))
    for key, label, color, ls in (
        ("logreg", "walk-forward LR (16 feat)", "#1f4e79", "-"),
        ("random_forest", "walk-forward RF (16 feat)", "#2e8b57", "-"),
        ("grade_only", "walk-forward LR (S/E only)", "#c1440e", "-"),
    ):
        p = np.asarray(result["oof"][key], dtype=float)
        m = ~np.isnan(p)
        auc = models.roc_auc(y[m], p[m])
        fpr, tpr = _roc_points(y[m], p[m])
        txt = "undefined" if auc is None else f"{auc:.3f}"
        ax.plot(fpr, tpr, label=f"{label}  AUC={txt}", color=color, lw=1.8, ls=ls)
    assoc = result["oof"].get("assoc_full")
    if assoc is not None:
        pa = np.asarray(assoc, dtype=float)
        ma = ~np.isnan(pa)
        if len(np.unique(y[ma])) == 2:
            fpr, tpr = _roc_points(y[ma], pa[ma])
            ax.plot(fpr, tpr, color="#8e44ad", lw=1.3, ls="-.",
                    label=f"stratified-CV LR (L2 association, batch-confounded) AUC={models.roc_auc(y[ma], pa[ma]):.3f}")
    fpr, tpr = _roc_points(y, np.asarray(result["rule_score"], dtype=float))
    rule_auc = models.roc_auc(y, result["rule_score"])
    ax.plot(fpr, tpr, label=f"simple rule  AUC={rule_auc:.3f}", color="#7f7f7f", ls="--", lw=1.4)
    ax.plot([0, 1], [0, 1], color="k", ls=":", lw=1.0, label="random AUC=0.5")
    ax.set_xlabel("false positive rate")
    ax.set_ylabel("true positive rate")
    wf_lr = result["walk_forward"]["logreg"]
    note = (
        f"L1 identifiability = {wf_lr['identifiable']} "
        f"(single-class test folds {wf_lr['folds_with_single_class_test']}/{wf_lr['n_folds']})"
    )
    ax.set_title("IN56-N07 · ROC: walk-forward (L1) vs association (L2)\n" + note, fontsize=9.5)
    ax.legend(loc="lower right", fontsize=7.5)
    ax.grid(alpha=0.3)
    p1 = os.path.join(outdir, "fig1_roc_walkforward.png")
    fig.tight_layout(); fig.savefig(p1, dpi=160); plt.close(fig); paths.append(p1)

    # 2) 校准（可靠性）曲线
    fig, ax = plt.subplots(figsize=(5.8, 5.0))
    for key, label, color in (("logreg", "LR", "#1f4e79"), ("random_forest", "RF", "#2e8b57")):
        p = np.asarray(result["oof"][key], dtype=float)
        m = ~np.isnan(p)
        if m.sum() == 0:
            continue
        curve = models.reliability_curve(y[m], p[m], n_bins=8)
        xs = [c["mean_pred"] for c in curve]
        ys = [c["observed"] for c in curve]
        sizes = [max(20, c["n"] * 2.5) for c in curve]
        ax.plot(xs, ys, "o-", color=color, lw=1.6, ms=0,
                label=f"{label}  Brier={models.brier_score(y[m], p[m]):.3f}  ECE={models.expected_calibration_error(y[m], p[m]):.3f}")
        ax.scatter(xs, ys, s=sizes, color=color, alpha=0.55)
    ax.plot([0, 1], [0, 1], color="k", ls=":", lw=1.0)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_xlabel("predicted P(merged)")
    ax.set_ylabel("observed merge rate")
    ax.set_title("calibration (marker size ∝ bin n)")
    ax.legend(loc="upper left", fontsize=7.5)
    ax.grid(alpha=0.3)
    p2 = os.path.join(outdir, "fig2_calibration.png")
    fig.tight_layout(); fig.savefig(p2, dpi=160); plt.close(fig); paths.append(p2)

    # 3) Shapley 重要性
    imp = result["shap"]["importance"]
    fig, ax = plt.subplots(figsize=(6.6, 4.8))
    names = [r["feature"] for r in imp][::-1]
    vals = [r["mean_abs_shap"] for r in imp][::-1]
    colors = ["#c1440e" if n in result["batch_proxy_features"] else "#1f4e79" for n in names]
    ax.barh(names, vals, color=colors)
    ax.set_xlabel("mean |Shapley value| (log-odds)")
    ax.set_title(f"SHAP importance (sampling Shapley, n={result['shap']['n_explain']}); red = batch proxy")
    ax.grid(axis="x", alpha=0.3)
    p3 = os.path.join(outdir, "fig3_shap_importance.png")
    fig.tight_layout(); fig.savefig(p3, dpi=160); plt.close(fig); paths.append(p3)

    # 4) 切分与标签构成 + 负类的时间集中性
    fig, axes = plt.subplots(2, 1, figsize=(7.4, 6.0), height_ratios=[3, 2.4])
    ax1 = axes[0]
    times = result["oof"]["times"]
    order = np.argsort(times)
    y_sorted = y[order]
    ax1.step(np.arange(len(order)), np.cumsum(y_sorted) / np.arange(1, len(order) + 1),
             color="#1f4e79", lw=1.6, label="cumulative merge rate")
    for f in result["walk_forward"]["logreg"]["folds"]:
        ax1.axvline(f["n_train"], color="#999", ls="--", lw=0.9)
    ax1.set_xlabel("mission index (chronological)")
    ax1.set_ylabel("cumulative merge rate")
    ax1.set_title("walk-forward expanding splits (dashed = fold boundary)")
    ax1.grid(alpha=0.3); ax1.legend(fontsize=8)
    ax2 = axes[1]
    kinds = dataset["label_kind_counts"]
    ax2.bar(list(kinds.keys()), list(kinds.values()), color="#2e8b57")
    for i, (k, v) in enumerate(kinds.items()):
        ax2.text(i, v, str(v), ha="center", va="bottom", fontsize=8)
    ax2.set_ylabel("missions")
    ax2.set_title("label composition (downstream outcome kind)")
    p4 = os.path.join(outdir, "fig4_splits_labels.png")
    fig.tight_layout(); fig.savefig(p4, dpi=160); plt.close(fig); paths.append(p4)

    # 5) 批次混淆诊断：duration_h / goal_len 按类分离
    S = dataset["samples"]
    dur_pos = [s["features"]["duration_h"] for s in S if s["y"] == 1]
    dur_neg = [s["features"]["duration_h"] for s in S if s["y"] == 0]
    gl_pos = [s["features"]["goal_len"] for s in S if s["y"] == 1]
    gl_neg = [s["features"]["goal_len"] for s in S if s["y"] == 0]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.2))
    for ax, a, b, name, unit in (
        (axes[0], dur_pos, dur_neg, "duration_h", "hours"),
        (axes[1], gl_pos, gl_neg, "goal_len", "×100 chars"),
    ):
        bins = np.histogram(np.concatenate([a, b]), bins=20)[1]
        ax.hist(a, bins=bins, alpha=0.62, label="y=1 merged", color="#2e8b57")
        ax.hist(b, bins=bins, alpha=0.62, label="y=0 not integrated", color="#c1440e")
        ax.set_xlabel(f"{name} ({unit})")
        ax.set_ylabel("missions")
        ax.set_title(f"{name}: batch proxy (single-feature AUC={models.roc_auc(y, [s['features'][name] for s in S]):.3f})")
        ax.legend(fontsize=8); ax.grid(alpha=0.3)
    p5 = os.path.join(outdir, "fig5_batch_confound.png")
    fig.tight_layout(); fig.savefig(p5, dpi=160); plt.close(fig); paths.append(p5)

    # 6) 仪器控制：正控 / 负控 / 实测
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    labels = ["negative control\n(label shuffle)", "observed L1\n(walk-forward)", "positive control\n(injected signal)"]
    vals = [
        result["controls"]["negative"]["pooled_auc"] or 0.0,
        result["walk_forward"]["primary"]["pooled_auc"] or 0.0,
        result["controls"]["positive"]["pooled_auc"] or 0.0,
    ]
    colors = ["#c1440e", "#1f4e79", "#2e8b57"]
    bars = ax.bar(labels, vals, color=colors)
    ax.axhline(0.65, color="k", ls="--", lw=1.2, label="acceptance AUC ≥ 0.65")
    ax.axhline(0.5, color="k", ls=":", lw=1.0, label="chance 0.5")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v:.3f}", ha="center", fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("pooled AUC")
    ax.set_title("instrument controls: is a low AUC a data property or a code bug?")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="y", alpha=0.3)
    p6 = os.path.join(outdir, "fig6_controls.png")
    fig.tight_layout(); fig.savefig(p6, dpi=160); plt.close(fig); paths.append(p6)

    # 7) 同交付波（批次控制）内的判别力对比
    within = result["association"]["within_delivery_wave"]
    if within:
        fig, ax = plt.subplots(figsize=(6.8, 4.2))
        days = list(within.keys())
        series = [("full", "full 16 feat", "#1f4e79"), ("grade_only", "S/E only", "#c1440e"),
                  ("batch_only", "batch proxy only", "#7f7f7f"), ("no_batch", "full minus batch proxy", "#2e8b57")]
        width = 0.8 / len(series)
        xpos = np.arange(len(days))
        for i, (key, label, color) in enumerate(series):
            vals = [within[d][key] if within[d][key] is not None else 0.0 for d in days]
            ax.bar(xpos + i * width - 0.4 + width / 2, vals, width, label=label, color=color)
        ax.axhline(0.5, color="k", ls=":", lw=1.0)
        ax.set_xticks(xpos)
        ax.set_xticklabels([f"{d}\n(n={within[d]['n']}, pos={within[d]['n_pos']})" for d in days], fontsize=8)
        ax.set_ylim(0, 1.0)
        ax.set_ylabel("stratified-CV AUC")
        ax.set_title("within delivery-wave (batch-controlled) discriminative power")
        ax.legend(fontsize=7.5, loc="upper right")
        ax.grid(axis="y", alpha=0.3)
        p7 = os.path.join(outdir, "fig7_within_wave.png")
        fig.tight_layout(); fig.savefig(p7, dpi=160); plt.close(fig); paths.append(p7)
    return paths