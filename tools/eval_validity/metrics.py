"""纯统计核：外部效度校准用到的全部指标（无 IO、无 DB、可单测）。

约定
----
``y`` 为 0/1 标签（1 = 下游好结果 / merge），``p`` 为**预测概率**（越高越好）。
所有函数对空集 / 单类样本返回 ``nan`` 而不是抛异常——上游如实把 nan 记为
WITHHELD，绝不用 0.5 之类的默认值把缺失伪装成「无判别力」。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

#: 校准曲线分箱边界（任务书口径：0-0.1, 0.1-0.2, ..., 0.9-1.0）
BIN_EDGES: tuple[float, ...] = tuple(i / 10.0 for i in range(11))


# ─────────────────────────────── 基础 ───────────────────────────────

def _as_arrays(y: Iterable[float], p: Iterable[float]):
    yy = np.asarray(list(y), dtype=float)
    pp = np.asarray(list(p), dtype=float)
    if yy.shape != pp.shape:
        raise ValueError(f"y/p 长度不一致：{yy.shape} vs {pp.shape}")
    if np.isnan(yy).any() or np.isnan(pp).any():
        raise ValueError("y/p 含 nan")
    return yy, pp


def auc_roc(y: Sequence[float], p: Sequence[float]) -> float:
    """AUC-ROC（Mann-Whitney 秩和，平局取平均秩）。

    值 = P(p_pos > p_neg) + 0.5·P(p_pos == p_neg)。单类样本时返回 nan。
    """
    yy, pp = _as_arrays(y, p)
    pos = yy == 1
    n_pos = int(pos.sum())
    n_neg = int(len(yy) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")

    order = np.argsort(pp, kind="mergesort")
    ps = pp[order]
    ranks = np.empty(len(pp), dtype=float)
    i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def brier_score(y: Sequence[float], p: Sequence[float]) -> float:
    """Brier 分数 = mean((p - y)^2)；越小越好（0 = 完美）。"""
    yy, pp = _as_arrays(y, p)
    if len(yy) == 0:
        return float("nan")
    return float(np.mean((pp - yy) ** 2))


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例的 Wilson 置信区间（小样本比正态近似稳）。"""
    if n <= 0:
        return (float("nan"), float("nan"))
    phat = k / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def calibration_bins(y: Sequence[float], p: Sequence[float],
                     edges: Sequence[float] = BIN_EDGES) -> list[dict]:
    """把预测概率分箱，逐箱给出 n / 平均预测 / 实际正例率（含 Wilson CI）。

    返回列表长度 = len(edges)-1；空箱也保留（n=0, rate=None），这样曲线图
    不会因为缺箱而骗人地「连续」。
    """
    yy, pp = _as_arrays(y, p)
    out: list[dict] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        last = hi >= edges[-1] - 1e-12
        mask = (pp >= lo) & ((pp <= hi) if last else (pp < hi))
        n = int(mask.sum())
        k = int(yy[mask].sum())
        lo_ci, hi_ci = wilson_interval(k, n)
        out.append({
            "lo": round(lo, 6),
            "hi": round(hi, 6),
            "n": n,
            "mean_pred": float(pp[mask].mean()) if n else None,
            "emp_rate": (k / n) if n else None,
            "ci95": [lo_ci, hi_ci] if n else None,
        })
    return out


def expected_calibration_error(bins: Sequence[dict]) -> float:
    """ECE = Σ (n_b/n)·|emp_rate_b - mean_pred_b|（空箱不计）。"""
    total = sum(b["n"] for b in bins)
    if total == 0:
        return float("nan")
    acc = 0.0
    for b in bins:
        if b["n"] and b["emp_rate"] is not None:
            acc += (b["n"] / total) * abs(b["emp_rate"] - b["mean_pred"])
    return float(acc)


# ───────────────────────────── 时间窗切分 ─────────────────────────────

def walk_forward_splits(times: Sequence[float], n_windows: int = 4,
                        min_train: int = 30, min_test: int = 10) -> list[dict]:
    """扩展窗（expanding-window）时间切分：只用**过去的窗**训练，预测下一窗。

    按 ``times`` 升序切成 ``n_windows + 1`` 个连续块；第 k 个切分
    （k=1..n_windows）训练集 = 块 0..k-1，测试集 = 块 k。
    样本不足（train < min_train 或 test < min_test）的切分被跳过并留痕，
    不做「偷看未来」的随机 K 折。
    """
    if n_windows < 1:
        raise ValueError("n_windows 必须 ≥ 1")
    n = len(times)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (times[i], i))
    n_blocks = n_windows + 1
    if n < min_train + min_test:
        return [{
            "fold": 0, "train_idx": [], "test_idx": [],
            "train_span": None, "test_span": None,
            "skipped": f"样本不足：n={n} < min_train+min_test={min_train + min_test}",
        }]
    size = n / n_blocks
    blocks = []
    for b in range(n_blocks):
        lo = int(round(b * size))
        hi = int(round((b + 1) * size))
        blocks.append(order[lo:hi])

    splits: list[dict] = []
    for k in range(1, n_blocks):
        train_idx = [i for blk in blocks[:k] for i in blk]
        test_idx = list(blocks[k])
        skipped = None
        if len(train_idx) < min_train:
            skipped = f"训练窗过小：{len(train_idx)} < {min_train}"
        elif len(test_idx) < min_test:
            skipped = f"测试窗过小：{len(test_idx)} < {min_test}"
        splits.append({
            "fold": k,
            "train_idx": train_idx if not skipped else [],
            "test_idx": test_idx if not skipped else [],
            "train_span": [float(times[min(train_idx)]), float(times[max(train_idx)])]
            if train_idx else None,
            "test_span": [float(times[min(test_idx)]), float(times[max(test_idx)])]
            if test_idx else None,
            "skipped": skipped,
        })
    return splits


# ───────────────────────────── 概率模型 ─────────────────────────────

def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 iters: int = 800, lr: float = 0.5) -> dict:
    """L2 正则逻辑回归（全批量梯度下降 + 特征标准化）。

    输入规模很小（n ≲ 10^3, d ≤ 3），手写 20 行足够、且完全确定性（可单测），
    不引入 scikit-learn 依赖。
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    n, d = X.shape
    if n == 0:
        raise ValueError("空训练集")
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    Z = np.hstack([np.ones((n, 1)), Z])
    w = np.zeros(d + 1)
    reg = np.ones(d + 1)
    reg[0] = 0.0  # 截距不正则化
    for _ in range(iters):
        z = Z @ w
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))
        grad = Z.T @ (p - y) / n + l2 * reg * w / n
        w -= lr * grad
    return {"w": w.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "d": d}


def predict_logistic(model: dict, X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    mu = np.asarray(model["mu"], dtype=float)
    sd = np.asarray(model["sd"], dtype=float)
    Z = (X - mu) / sd
    Z = np.hstack([np.ones((Z.shape[0], 1)), Z])
    w = np.asarray(model["w"], dtype=float)
    z = Z @ w
    return 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))


def fit_prior_probability(y: Sequence[float], alpha: float = 1.0,
                          beta: float = 1.0) -> float:
    """Beta(alpha, beta) 后验均值——退化/单类训练窗的兜底概率（不返回 0/1）。"""
    yy = np.asarray(list(y), dtype=float)
    if len(yy) == 0:
        return (alpha) / (alpha + beta)
    return float((yy.sum() + alpha) / (len(yy) + alpha + beta))


# ───────────────────────── 置换负控 / bootstrap ─────────────────────────

def permutation_pvalue(observed: float, null_samples: Sequence[float],
                       alternative: str = "greater") -> float:
    """置换检验 p 值，含 +1 平滑（(k+1)/(K+1)），避免报 0。"""
    vals = [v for v in null_samples if not (isinstance(v, float) and math.isnan(v))]
    if not vals or math.isnan(observed):
        return float("nan")
    if alternative == "greater":
        k = sum(1 for v in vals if v >= observed)
    elif alternative == "less":
        k = sum(1 for v in vals if v <= observed)
    else:
        k = sum(1 for v in vals if abs(v) >= abs(observed))
    return (k + 1) / (len(vals) + 1)


def bootstrap_auc_ci(y: Sequence[float], p: Sequence[float], n_boot: int = 1000,
                     seed: int = 20261106, alpha: float = 0.05) -> dict:
    """分层 bootstrap 的 AUC 置信区间（重采样后保证两类都非空）。"""
    yy, pp = _as_arrays(y, p)
    pos_idx = np.flatnonzero(yy == 1)
    neg_idx = np.flatnonzero(yy == 0)
    if len(pos_idx) == 0 or len(neg_idx) == 0:
        return {"auc": float("nan"), "lo": float("nan"), "hi": float("nan"),
                "n_boot": 0}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        bi = np.concatenate([
            rng.choice(pos_idx, size=len(pos_idx), replace=True),
            rng.choice(neg_idx, size=len(neg_idx), replace=True),
        ])
        vals.append(auc_roc(yy[bi], pp[bi]))
    vals = np.asarray([v for v in vals if not math.isnan(v)])
    return {
        "auc": auc_roc(yy, pp),
        "lo": float(np.quantile(vals, alpha / 2)) if len(vals) else float("nan"),
        "hi": float(np.quantile(vals, 1 - alpha / 2)) if len(vals) else float("nan"),
        "n_boot": int(len(vals)),
    }