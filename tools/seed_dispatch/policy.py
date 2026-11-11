"""派单排序策略：P(验收通过) × 期望信息增益 / 成本 的 EVOI，walk-forward + off-policy 校正。

数学口径（全部落进报告的「怎么做的」节）
----------------------------------------
验收概率模型：对标准化后的派单时刻特征做 L2 正则逻辑回归

    z = b + x·w,   P(pass | x) = sigmoid(z),   loss = -(1/n)Σ[y log p + (1-y)log(1-p)] + λ‖w‖²/2

确定性全批梯度下降（无随机初始化），特征均值/方差只在训练窗内估计。

信息增益代理 ``gain``：蒸馏层给种子的新颖性 ``novelty ∈ [0,1]``（1 - 与已存在提案的最大相似度）；
历史任务没有簇规模，故统一 ``gain = max(novelty, gain_floor)`` —— 单一公式同一口径，
种子与历史可比（簇规模另列 ``breadth``，只报不参与排序）。

成本代理 ``cost``：历史用 ``result.tool_calls``（事后成本，只作拟合标签）；对候选（含种子）
用对数线性成本模型 ``log cost = a + b1·log_budget + b2·log_len`` 预测。

    EVOI_i = P(pass_i) · gain_i / cost_i

排序与判据：按 ``created_at`` 切 ``windows`` 个时序窗，第 k 窗只用 <k 窗训练（严格 walk-forward），
窗内按 ``top_frac`` 取前 20% 算 precision@20%，与 FIFO（平台今天的排队顺序）、随机、
最省成本三条基线比。

Off-policy：真实日志里每个 slate（时间桶）实际只"派出"了 FIFO 最早的一条——要评估把一条
轮次的名额给 EVOI 最高项会怎样，必须用记录下来的行为策略 propensity 做 IPS/SNIPS：

    π0(a | s) = (1-ε)·softmax(β·s_a) + ε/|s|,   s_a = 1 - rank_a/(|s|-1)
    V_IPS  = (1/R) Σ_r  1{a*_r = a_r} / π0(a_r | s_r) · y_r
    V_SNIPS= Σ_r w_r y_r / Σ_r w_r,   w_r = 1{a*_r = a_r}/π0(a_r|s_r)
    ESS    = (Σ w)² / Σ w²

``support`` = a*_r = a_r 的轮次占比；重叠太小则 IPS 方差不可控，报告须写明。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

__all__ = [
    "FEATURES",
    "LogisticModel",
    "CostModel",
    "SlateResult",
    "auc",
    "evoi",
    "fit_cost_model",
    "fit_logistic",
    "precision_at_frac",
    "predict",
    "walk_forward",
    "offpolicy_summary",
    "build_slates",
]

#: 设计矩阵列序（固定顺序 → 可复算）
FEATURES: tuple[str, ...] = (
    "f_repo_pass",
    "f_repo_recent_fail",
    "f_global_recent_fail",
    "f_log_len",
    "f_log_budget",
    "f_novelty",
    "f_is_child",
    "f_co_repos",
)


@dataclass
class LogisticModel:
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray
    intercept: float
    n_train: int
    train_loss: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": list(FEATURES),
            "mean": [round(float(x), 6) for x in self.mean],
            "std": [round(float(x), 6) for x in self.std],
            "coef": [round(float(x), 6) for x in self.coef],
            "intercept": round(float(self.intercept), 6),
            "n_train": int(self.n_train),
            "train_loss": round(float(self.train_loss), 6),
        }


@dataclass
class CostModel:
    intercept: float
    coef: np.ndarray
    fallback: float

    def predict(self, rows: np.ndarray) -> np.ndarray:
        if rows.size == 0:
            return np.zeros(0)
        log_cost = self.intercept + rows @ self.coef
        return np.exp(np.clip(log_cost, 0.0, 20.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "intercept": round(float(self.intercept), 6),
            "coef": [round(float(x), 6) for x in self.coef],
            "features": ["f_log_budget", "f_log_len"],
            "fallback": round(float(self.fallback), 6),
        }


def matrix(records: Iterable[Mapping[str, Any]]) -> np.ndarray:
    rows = []
    for rec in records:
        feats = rec["features"] if "features" in rec else rec
        rows.append([float(feats.get(name, 0.0)) for name in FEATURES])
    return np.asarray(rows, dtype=float) if rows else np.zeros((0, len(FEATURES)))


def fit_logistic(
    X: np.ndarray,
    y: np.ndarray,
    *,
    ridge: float = 1.0,
    epochs: int = 400,
    lr: float = 0.35,
    sample_weight: np.ndarray | None = None,
) -> LogisticModel:
    """标准化 + L2 逻辑回归（全批梯度，确定性）。"""
    if X.shape[0] == 0:
        raise ValueError("empty design matrix")
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    Z = (X - mean) / std
    y = y.astype(float)
    w = np.zeros(Z.shape[1])
    b = 0.0
    sw = np.ones_like(y) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    sw_sum = max(sw.sum(), 1e-9)
    for _ in range(epochs):
        p = _sigmoid(Z @ w + b)
        grad_w = (Z * (sw * (p - y))[:, None]).sum(axis=0) / sw_sum + ridge * w / sw_sum
        grad_b = float((sw * (p - y)).sum() / sw_sum)
        w -= lr * grad_w
        b -= lr * grad_b
    p = _sigmoid(Z @ w + b)
    eps = 1e-12
    loss = float(-(sw * (y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))).sum() / sw_sum)
    loss += float(0.5 * ridge * (w @ w) / sw_sum)
    return LogisticModel(mean=mean, std=std, coef=w, intercept=b,
                         n_train=int(X.shape[0]), train_loss=loss)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def predict(model: LogisticModel, X: np.ndarray) -> np.ndarray:
    if X.shape[0] == 0:
        return np.zeros(0)
    Z = (X - model.mean) / model.std
    return _sigmoid(Z @ model.coef + model.intercept)


def fit_cost_model(records: Sequence[Mapping[str, Any]]) -> CostModel:
    """对数线性成本模型：log(tool_calls) ~ 1 + f_log_budget + f_log_len。"""
    obs = [r for r in records if r.get("cost_tool_calls")]
    if not obs:
        return CostModel(intercept=math.log(120.0), coef=np.zeros(2), fallback=120.0)
    A = np.asarray(
        [[1.0, float(r["features"]["f_log_budget"]), float(r["features"]["f_log_len"])]
         for r in obs],
        dtype=float,
    )
    b = np.log(np.asarray([float(r["cost_tool_calls"]) for r in obs], dtype=float))
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    fallback = float(np.exp(np.median(b)))
    return CostModel(intercept=float(sol[0]), coef=sol[1:], fallback=fallback)


def evoi(p_pass: np.ndarray, gain: np.ndarray, cost: np.ndarray, *,
         min_cost: float = 1.0) -> np.ndarray:
    """EVOI = P(pass) · gain / cost（成本下限 clip，防除零）。"""
    return (p_pass * gain) / np.clip(cost, min_cost, None)


def precision_at_frac(scores: Sequence[float], labels: Sequence[int], frac: float) -> float:
    """按分数降序（同分按原始次序）取前 ``frac``，返回其中正例占比。"""
    if not labels:
        return 0.0
    idx = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))
    k = max(1, int(round(len(idx) * frac)))
    top = idx[:k]
    return sum(1 for i in top if int(labels[i]) == 1) / k


def auc(labels: Sequence[int], scores: Sequence[float]) -> float:
    """ROC AUC（Mann-Whitney U，同分计 0.5）。"""
    pairs = [(float(s), int(y)) for s, y in zip(scores, labels)]
    n_pos = sum(1 for _, y in pairs if y == 1)
    n_neg = len(pairs) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    pairs.sort(key=lambda t: t[0])
    rank_sum = 0.0
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            if pairs[k][1] == 1:
                rank_sum += avg_rank
        i = j + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


@dataclass
class SlateResult:
    rounds: int = 0
    support: float = 0.0
    naive_value: float = 0.0
    ips_value: float = 0.0
    snips_value: float = 0.0
    ess: float = 0.0
    mean_propensity: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "rounds": int(self.rounds),
            "support": round(float(self.support), 6),
            "naive_value": round(float(self.naive_value), 6),
            "ips_value": round(float(self.ips_value), 6),
            "snips_value": round(float(self.snips_value), 6),
            "ess": round(float(self.ess), 3),
            "mean_propensity": round(float(self.mean_propensity), 6),
        }


def build_slates(
    records: Sequence[Mapping[str, Any]],
    scores: np.ndarray,
    *,
    hours: int = 1,
    min_slate: int = 2,
    fifo_beta: float = 2.0,
    explore_eps: float = 0.05,
) -> list[dict[str, Any]]:
    """按时间桶切 slate；记录 FIFO 顺序、propensity 与 EVOI 分数。"""
    buckets: dict[int, list[int]] = {}
    for i, rec in enumerate(records):
        key = int(float(rec["ts"]) // (hours * 3600))
        buckets.setdefault(key, []).append(i)
    slates: list[dict[str, Any]] = []
    for key in sorted(buckets):
        ids = sorted(buckets[key], key=lambda i: (float(records[i]["ts"]), records[i]["mission_id"]))
        if len(ids) < min_slate:
            continue
        n = len(ids)
        pri = np.asarray([1.0 - k / (n - 1) for k in range(n)])
        logits = fifo_beta * pri
        logits -= logits.max()
        soft = np.exp(logits)
        soft /= soft.sum()
        pi0 = (1.0 - explore_eps) * soft + explore_eps / n
        labelled = [i for i in ids if records[i]["label"] is not None]
        if len(labelled) < min_slate:
            continue
        slates.append(
            {
                "bucket": key,
                "ids": labelled,
                "logged": labelled[0],
                "pi0": {i: float(pi0[ids.index(i)]) for i in labelled},
                "target": labelled[int(np.argmax([scores[i] for i in labelled]))],
                "labels": {i: int(records[i]["label"]) for i in labelled},
            }
        )
    return slates


def offpolicy_summary(slates: Sequence[Mapping[str, Any]]) -> SlateResult:
    """IPS / SNIPS / ESS（确定性目标策略 → 指示函数权重）。"""
    if not slates:
        return SlateResult()
    weights: list[float] = []
    rewards: list[float] = []
    hits = 0
    naive: list[float] = []
    prop_sum = 0.0
    prop_n = 0
    for slate in slates:
        logged = int(slate["logged"])
        target = int(slate["target"])
        y = float(slate["labels"][logged])
        pi = float(slate["pi0"][logged])
        naive.append(float(slate["labels"][target]))
        prop_sum += pi
        prop_n += 1
        if logged == target:
            hits += 1
            weights.append(1.0 / max(pi, 1e-9))
            rewards.append(y)
    rounds = len(slates)
    w_arr = np.asarray(weights, dtype=float)
    r_arr = np.asarray(rewards, dtype=float)
    ips = float((w_arr * r_arr).sum() / rounds) if rounds else 0.0
    snips = float((w_arr * r_arr).sum() / w_arr.sum()) if len(w_arr) else 0.0
    ess = float(w_arr.sum() ** 2 / (w_arr ** 2).sum()) if len(w_arr) else 0.0
    return SlateResult(
        rounds=rounds,
        support=hits / rounds if rounds else 0.0,
        naive_value=float(np.mean(naive)) if naive else 0.0,
        ips_value=ips,
        snips_value=snips,
        ess=ess,
        mean_propensity=prop_sum / max(prop_n, 1),
    )


def walk_forward(
    records: Sequence[Mapping[str, Any]],
    *,
    windows: int = 5,
    top_frac: float = 0.2,
    min_train: int = 300,
    ridge: float = 1.0,
    epochs: int = 400,
    lr: float = 0.35,
    random_repeats: int = 25,
    gain_floor: float = 0.05,
    seed: int = 7,
) -> dict[str, Any]:
    """时序 walk-forward 评估：EVOI 策略 vs FIFO / 随机 / 最省成本基线。"""
    labelled = [r for r in records if r.get("label") is not None]
    if len(labelled) < min_train + windows:
        raise ValueError("not enough labelled records for walk-forward")
    n = len(labelled)
    bounds = [int(round(k * n / windows)) for k in range(windows + 1)]
    rng = np.random.default_rng(seed)
    per_window: list[dict[str, Any]] = []
    pooled_scores: list[float] = []
    pooled_labels: list[int] = []
    ib = FEATURES.index("f_log_budget")
    il = FEATURES.index("f_log_len")
    ino = FEATURES.index("f_novelty")
    for k in range(windows):
        lo, hi = bounds[k], bounds[k + 1]
        if hi - lo < 2 or lo < min_train:
            continue
        train = labelled[:lo]
        cost_model = fit_cost_model(train)
        model = fit_logistic(matrix(train), np.asarray([int(r["label"]) for r in train], float),
                             ridge=ridge, epochs=epochs, lr=lr)
        test = labelled[lo:hi]
        X_test = matrix(test)
        p = predict(model, X_test)
        y_test = [int(r["label"]) for r in test]
        cost = cost_model.predict(X_test[:, [ib, il]])
        gain = np.maximum(X_test[:, ino], gain_floor)
        score_evoi = evoi(p, gain, cost)
        score_fifo = [-float(r["ts"]) for r in test]          # 越早越高（平台今天的排队顺序）
        score_cheap = [-float(c) for c in cost]
        random_vals = []
        for _ in range(random_repeats):
            perm = rng.permutation(len(test))
            random_vals.append(precision_at_frac([float(x) for x in perm], y_test, top_frac))
        fifo_prec = precision_at_frac(score_fifo, y_test, top_frac)
        evoi_prec = precision_at_frac([float(x) for x in score_evoi], y_test, top_frac)
        cheap_prec = precision_at_frac(score_cheap, y_test, top_frac)
        per_window.append(
            {
                "window": k,
                "train_n": len(train),
                "test_n": len(test),
                "test_pass_rate": round(float(np.mean(y_test)), 6),
                "precision_at_frac": {
                    "evoi": round(evoi_prec, 6),
                    "fifo": round(fifo_prec, 6),
                    "cheapest": round(cheap_prec, 6),
                    "random": round(float(np.mean(random_vals)), 6),
                },
                "auc_evoi_score": round(auc(y_test, [float(x) for x in score_evoi]), 6),
                "auc_p_pass": round(auc(y_test, [float(x) for x in p]), 6),
                "model": model.to_dict(),
                "cost_model": cost_model.to_dict(),
                "evoi_mean": round(float(np.mean(score_evoi)), 6),
            }
        )
        pooled_scores.extend(float(x) for x in score_evoi)
        pooled_labels.extend(y_test)
    if not per_window:
        raise ValueError("no evaluable window (increase data or lower min_train)")
    keys = ("evoi", "fifo", "cheapest", "random")
    weights = np.asarray([w["test_n"] for w in per_window], dtype=float)
    weighted_prec = {
        key: float(np.average([w["precision_at_frac"][key] for w in per_window], weights=weights))
        for key in keys
    }
    baseline_best = max(weighted_prec["fifo"], weighted_prec["cheapest"], weighted_prec["random"])
    return {
        "windows": per_window,
        "top_frac": top_frac,
        "evaluated_records": int(weights.sum()),
        "pooled_pass_rate": round(
            float(np.average([w["test_pass_rate"] for w in per_window], weights=weights)), 6),
        "precision_weighted": {k: round(v, 6) for k, v in weighted_prec.items()},
        "baseline_fifo": round(weighted_prec["fifo"], 6),
        "baseline_best": round(baseline_best, 6),
        "gain_vs_fifo_pp": round((weighted_prec["evoi"] - weighted_prec["fifo"]) * 100.0, 3),
        "gain_vs_best_baseline_pp": round((weighted_prec["evoi"] - baseline_best) * 100.0, 3),
        "pooled_auc": round(auc(pooled_labels, pooled_scores), 6),
        "last_model": per_window[-1]["model"],
    }