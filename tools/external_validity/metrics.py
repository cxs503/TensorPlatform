"""外部效度的四个度量族：判别力（AUC）、概率语义（Brier/校准）、漂移（CUSUM）。

全部 numpy/scipy 手写实现（仓内无 sklearn 依赖），每条都有闭式或可复算的验证面：

- :func:`auc_score` —— Mann-Whitney U 形式，结（ties）记 0.5；等价于
  ``P(score_pos > score_neg) + 0.5 P(equal)``。
- :func:`fit_logistic` —— IRLS（Newton）解 L2 正则逻辑回归，特征在训练集内标准化，
  避免测试集信息泄漏。
- :func:`walk_forward` —— 时间窗滚动：只用 ``day < d`` 的样本训练，预测 ``day == d``，
  逐窗产出 AUC / Brier / 平均预测 / 实际发生率。
- :func:`calibration_curve` —— 等宽分桶（默认 0.1）的 predicted vs actual。
- :func:`cusum_detect` —— 双侧 CUSUM 控制图，检测评分占比的持续漂移（评分通胀）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np


# --------------------------------------------------------------------------- 判别力
def auc_score(scores: Sequence[float], labels: Sequence[int]) -> float:
    """ROC-AUC = P(正样本得分 > 负样本得分) + 0.5 P(相等)。

    单类或无样本 → ``nan``（调用方必须显式处理，不许静默填 0.5）。
    """
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if s.size != y.size:
        raise ValueError("scores/labels 长度不一致")
    pos, neg = s[y == 1], s[y == 0]
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    # 秩和法（tie 自动记平均秩 → 等价于 0.5 权重）
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(allv.size, dtype=float)
    ranks[order] = np.arange(1, allv.size + 1, dtype=float)
    # 结修正：同值取平均秩
    sorted_v = allv[order]
    i = 0
    while i < sorted_v.size:
        j = i
        while j + 1 < sorted_v.size and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    r_pos = ranks[: pos.size].sum()
    u = r_pos - pos.size * (pos.size + 1) / 2.0
    return float(u / (pos.size * neg.size))


def stratified_auc(scores: Sequence[float], labels: Sequence[int],
                   strata: Sequence[str]) -> dict[str, Any]:
    """分层（按天）AUC —— 只在同一层内比大小，再加权汇总。

    动机：下游结果的发生率有强烈的"批次效应"（合并队列一天能清一批、另一天几乎不动）。
    不分层的 pooled AUC 会把"模型学到的当天基准率"误记成判别力（Simpson 型构成假象）。
    分层 AUC 只统计**同一天内**的正负样本对，是判别力的保守下界。

    权重 = 每层 ``n_pos * n_neg``（与 Mann-Whitney 的配对数一致）。
    """
    sc = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    st = np.asarray(strata, dtype=object)
    num = den = 0.0
    per_stratum: list[dict[str, Any]] = []
    for s in sorted(set(st.tolist())):
        m = st == s
        pos = int((y[m] == 1).sum())
        neg = int((y[m] == 0).sum())
        if pos == 0 or neg == 0:
            per_stratum.append({"stratum": s, "n": int(m.sum()), "n_pos": pos,
                                "n_neg": neg, "auc": float("nan"), "weight": 0})
            continue
        a = auc_score(sc[m], y[m])
        w = pos * neg
        num += a * w
        den += w
        per_stratum.append({"stratum": s, "n": int(m.sum()), "n_pos": pos,
                            "n_neg": neg, "auc": a, "weight": w})
    return {
        "auc": float(num / den) if den > 0 else float("nan"),
        "n_strata_used": int(sum(1 for r in per_stratum if r["weight"] > 0)),
        "pairs": float(den),
        "per_stratum": per_stratum,
    }


def bootstrap_auc_ci(scores: Sequence[float], labels: Sequence[int], *,
                     n_boot: int = 2000, alpha: float = 0.05,
                     seed: int = 20261107) -> dict[str, Any]:
    """AUC 的百分位 bootstrap 置信区间（按样本重采样，保留标签）。"""
    sc = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    if sc.size == 0 or len(set(y.tolist())) < 2:
        return {"auc": float("nan"), "lo": float("nan"), "hi": float("nan")}
    rng = np.random.default_rng(seed)
    point = auc_score(sc, y)
    vals: list[float] = []
    n = sc.size
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        yy = y[idx]
        if len(set(yy.tolist())) < 2:
            continue
        vals.append(auc_score(sc[idx], yy))
    arr = np.asarray(vals, dtype=float)
    return {
        "auc": float(point),
        "lo": float(np.quantile(arr, alpha / 2)) if arr.size else float("nan"),
        "hi": float(np.quantile(arr, 1 - alpha / 2)) if arr.size else float("nan"),
        "n_boot": int(arr.size),
    }


# ------------------------------------------------------------------- 概率语义 / 校准
def brier_score(probs: Sequence[float], labels: Sequence[int]) -> float:
    """Brier = mean((p - y)^2)。下界 0，气候态基线 = p̄(1-p̄)。"""
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    if p.size == 0:
        return float("nan")
    return float(np.mean((p - y) ** 2))


def brier_skill_score(probs: Sequence[float], labels: Sequence[int]) -> float:
    """相对气候态（base rate）的 Brier 技巧分：1 - BS/BS_ref，>0 才算比瞎猜好。"""
    y = np.asarray(labels, dtype=float)
    bs = brier_score(probs, labels)
    if y.size == 0:
        return float("nan")
    p_bar = float(y.mean())
    ref = p_bar * (1.0 - p_bar)
    if ref <= 0:
        return float("nan")
    return float(1.0 - bs / ref)


def calibration_curve(probs: Sequence[float], labels: Sequence[int],
                      n_bins: int = 10) -> list[dict[str, Any]]:
    """等宽分桶校准曲线：每桶 pred（平均预测）vs actual（实际发生率）。

    与 sklearn 的 ``strategy='uniform'`` 同义；空桶保留（``n=0``, 且
    ``pred``/``actual`` 为 ``nan``），便于画图时保持 0.1 网格完整。
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    bins: list[dict[str, Any]] = []
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    for k in range(n_bins):
        lo, hi = edges[k], edges[k + 1]
        sel = (p >= lo) & (p < hi) if k < n_bins - 1 else (p >= lo) & (p <= hi)
        n = int(sel.sum())
        bins.append({
            "bin_lo": round(float(lo), 4),
            "bin_hi": round(float(hi), 4),
            "n": n,
            "pred": float(p[sel].mean()) if n else float("nan"),
            "actual": float(y[sel].mean()) if n else float("nan"),
        })
    return bins


def max_calibration_deviation(curve: Sequence[dict[str, Any]],
                              min_bin_n: int = 10) -> float:
    """最大 |pred - actual|（只统计样本量 ≥ ``min_bin_n`` 的桶）。

    小桶噪声大，纳入会变成"抽签"；``min_bin_n`` 必须在报告里如实披露。
    """
    devs = [abs(b["pred"] - b["actual"]) for b in curve
            if b["n"] >= min_bin_n and not math.isnan(b["pred"])]
    return float(max(devs)) if devs else float("nan")


# ---------------------------------------------------------------------- 逻辑回归
@dataclass
class LogisticModel:
    """L2 逻辑回归（IRLS/Newton）。标准化的 mu/sigma 随模型一起保存。"""

    coef: np.ndarray
    intercept: float
    mu: np.ndarray
    sigma: np.ndarray
    feature_names: tuple[str, ...] = ()
    n_train: int = 0
    converged: bool = True

    def predict_proba(self, X: Sequence[Sequence[float]]) -> np.ndarray:
        Z = (np.asarray(X, dtype=float) - self.mu) / self.sigma
        return _sigmoid(Z @ self.coef + self.intercept)

    def predict_raw(self, X: Sequence[Sequence[float]]) -> np.ndarray:
        """线性预测子（未过 sigmoid）——AUC 对单调变换不变，用原始值更稳。"""
        Z = (np.asarray(X, dtype=float) - self.mu) / self.sigma
        return Z @ self.coef + self.intercept

    def as_dict(self) -> dict[str, Any]:
        return {
            "intercept": float(self.intercept),
            "coef": [float(c) for c in self.coef],
            "feature_names": list(self.feature_names),
            "mu": [float(v) for v in self.mu],
            "sigma": [float(v) for v in self.sigma],
            "n_train": int(self.n_train),
            "converged": bool(self.converged),
        }


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def fit_logistic(X: Sequence[Sequence[float]], y: Sequence[int], *,
                 l2: float = 1.0, iters: int = 100, tol: float = 1e-8,
                 feature_names: tuple[str, ...] = ()) -> LogisticModel:
    """IRLS 求解逻辑回归；``l2`` 只加在斜率上（不加在截距上）。"""
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)
    if Xa.ndim == 1:
        Xa = Xa.reshape(-1, 1)
    mu = Xa.mean(axis=0)
    sigma = Xa.std(axis=0)
    sigma[sigma < 1e-12] = 1.0
    Z = (Xa - mu) / sigma
    Z1 = np.c_[np.ones(Z.shape[0]), Z]
    beta = np.zeros(Z1.shape[1])
    converged = False
    for _ in range(iters):
        p = _sigmoid(Z1 @ beta)
        w = np.clip(p * (1.0 - p), 1e-9, None)
        grad = Z1.T @ (ya - p)
        hess = Z1.T @ (Z1 * w[:, None])
        reg = l2 * np.eye(Z1.shape[1])
        reg[0, 0] = 0.0
        grad = grad - reg @ beta
        step = np.linalg.solve(hess + reg, grad)
        beta = beta + step
        if float(np.max(np.abs(step))) < tol:
            converged = True
            break
    return LogisticModel(coef=beta[1:], intercept=float(beta[0]), mu=mu,
                         sigma=sigma, feature_names=tuple(feature_names),
                         n_train=int(Xa.shape[0]), converged=converged)


# ------------------------------------------------------------------ walk-forward
def walk_forward(samples: Sequence[dict[str, Any]], label_key: str, *,
                 min_train_days: int = 2, min_test_n: int = 10,
                 l2: float = 1.0) -> dict[str, Any]:
    """时间序 walk-forward：逐日滚动的"看历史、判当天"。

    ``samples`` 每项需含 ``day`` / ``x`` / ``label_key``。训练集 = 所有 ``day < d``
    的样本（expanding window）；测试集 = ``day == d``。某窗口若训练集只有一类、
    或测试集样本不足 ``min_test_n``，则跳过并在 ``skipped`` 里留痕（不静默丢弃）。
    """
    ordered = sorted(samples, key=lambda s: s["day"])
    days = sorted({s["day"] for s in ordered})
    windows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    pooled_scores: list[float] = []
    pooled_probs: list[float] = []
    pooled_labels: list[int] = []
    pooled_days: list[str] = []

    for i, day in enumerate(days):
        if i < min_train_days:
            skipped.append({"day": day, "why": f"warmup(<{min_train_days}d train)"})
            continue
        train = [s for s in ordered if s["day"] < day]
        test = [s for s in ordered if s["day"] == day]
        y_tr = [int(s[label_key]) for s in train]
        if len(test) < min_test_n:
            skipped.append({"day": day, "why": f"test_n={len(test)}<{min_test_n}"})
            continue
        if len(set(y_tr)) < 2:
            skipped.append({"day": day, "why": "train single-class"})
            continue
        model = fit_logistic([s["x"] for s in train], y_tr, l2=l2)
        raw = model.predict_raw([s["x"] for s in test])
        prob = model.predict_proba([s["x"] for s in test])
        y_te = [int(s[label_key]) for s in test]
        windows.append({
            "day": day,
            "n_train": len(train),
            "n_test": len(test),
            "train_pos_rate": float(np.mean(y_tr)),
            "actual_rate": float(np.mean(y_te)),
            "auc": auc_score(raw, y_te),
            "brier": brier_score(prob, y_te),
            "mean_pred": float(np.mean(prob)),
        })
        pooled_days.extend([day] * len(y_te))
        pooled_scores.extend(raw.tolist())
        pooled_probs.extend(prob.tolist())
        pooled_labels.extend(y_te)

    valid = [w for w in windows if not math.isnan(w["auc"])]
    return {
        "windows": windows,
        "skipped": skipped,
        "n_windows": len(windows),
        "n_scored_windows": len(valid),
        "n_test_total": len(pooled_labels),
        "pooled_auc": auc_score(pooled_scores, pooled_labels),
        "pooled_brier": brier_score(pooled_probs, pooled_labels),
        "window_mean_auc": float(np.mean([w["auc"] for w in valid])) if valid else float("nan"),
        "window_min_auc": float(np.min([w["auc"] for w in valid])) if valid else float("nan"),
        "window_max_auc": float(np.max([w["auc"] for w in valid])) if valid else float("nan"),
        "pooled": {"scores": pooled_scores, "probs": pooled_probs,
                   "labels": pooled_labels, "days": pooled_days},
    }


# ------------------------------------------------------------------------ CUSUM
@dataclass
class CusumResult:
    """双侧 CUSUM 结果（含完整轨迹，便于画控制图）。"""

    series: list[float]
    target: float
    slack: float
    threshold: float
    s_pos: list[float] = field(default_factory=list)
    s_neg: list[float] = field(default_factory=list)
    alarms: list[int] = field(default_factory=list)

    @property
    def alarm(self) -> bool:
        return bool(self.alarms)

    @property
    def max_drift(self) -> float:
        """全过程最大偏移（以目标值为基准，带符号，+ = 上行漂移）。"""
        return float(max(self.s_pos)) if self.s_pos else 0.0


def cusum_detect(series: Sequence[float], *, target: float,
                 slack: float | None = None,
                 threshold: float | None = None,
                 sigma: float | None = None) -> CusumResult:
    """双侧 CUSUM 控制图检测均值漂移（评分通胀/通缩）。

    经典形式：``S+_t = max(0, S+_{t-1} + (x_t - target - k))``，
    ``S-_t = max(0, S-_{t-1} + (target - x_t - k))``；超过 ``h`` 报警。

    ``k``/``h`` 缺省按工程惯例取 ``k = 0.5σ``、``h = 5σ``（σ 由序列自身估，
    至少 1e-3，避免常数序列除零）。
    """
    x = np.asarray(series, dtype=float)
    if x.size == 0:
        return CusumResult([], target, 0.0, 0.0)
    sig = float(sigma) if sigma is not None else max(float(np.std(x, ddof=1))
                                                     if x.size > 1 else 0.0, 1e-3)
    k = float(slack) if slack is not None else 0.5 * sig
    h = float(threshold) if threshold is not None else 5.0 * sig
    s_pos: list[float] = []
    s_neg: list[float] = []
    alarms: list[int] = []
    sp = sn = 0.0
    for i, v in enumerate(x):
        sp = max(0.0, sp + (v - target - k))
        sn = max(0.0, sn + (target - v - k))
        s_pos.append(sp)
        s_neg.append(sn)
        if sp > h or sn > h:
            alarms.append(i)
    return CusumResult(series=x.tolist(), target=float(target), slack=k,
                       threshold=h, s_pos=s_pos, s_neg=s_neg, alarms=alarms)


def trailing_drift(series: Sequence[float], window: int, *,
                   baseline: float | None = None) -> dict[str, Any]:
    """最近 ``window`` 个点的均值相对基线（缺省=全序列均值）的漂移。"""
    x = np.asarray(series, dtype=float)
    if x.size == 0:
        return {"window": window, "n": 0, "drift": float("nan")}
    w = int(min(window, max(1, x.size // 2)))
    # 窗口法在点数不足时恒等于全序列均值（drift≡0，会假通过），故退化为两段对比。
    tail = x[-w:]
    base = float(np.mean(x[:-w])) if (baseline is None and x.size > w) else \
        (float(np.mean(x)) if baseline is None else float(baseline))
    return {
        "window": window,
        "effective_window": w,
        "n": int(tail.size),
        "baseline": base,
        "recent_mean": float(np.mean(tail)),
        "drift": float(np.mean(tail) - base),
        "abs_drift": float(abs(np.mean(tail) - base)),
    }


def random_label_control(samples: Sequence[dict[str, Any]], label_key: str, *,
                         n_draws: int = 20, seed: int = 20261107,
                         test_frac: float = 0.4) -> dict[str, Any]:
    """负控：打乱标签后重算 AUC，应回落到 0.5 附近。

    做法：固定随机种子，把样本按时间切出后 40% 作测试集，用真实特征 + 随机标签
    训练逻辑回归，再在同样随机的测试标签上算 AUC。重复 ``n_draws`` 次取分布。
    """
    ordered = sorted(samples, key=lambda s: s["day"])
    cut = int(round(len(ordered) * (1.0 - test_frac)))
    cut = max(1, min(cut, len(ordered) - 1))
    train, test = ordered[:cut], ordered[cut:]
    X_tr = [s["x"] for s in train]
    X_te = [s["x"] for s in test]
    rng = np.random.default_rng(seed)
    aucs: list[float] = []
    for _ in range(n_draws):
        y_tr = rng.permutation([int(s[label_key]) for s in train])
        y_te = rng.permutation([int(s[label_key]) for s in test])
        if len(set(y_tr.tolist())) < 2 or len(set(y_te.tolist())) < 2:
            continue
        model = fit_logistic(X_tr, y_tr)
        aucs.append(auc_score(model.predict_raw(X_te), y_te))
    arr = np.asarray(aucs, dtype=float)
    return {
        "n_draws": int(arr.size),
        "seed": seed,
        "mean_auc": float(arr.mean()) if arr.size else float("nan"),
        "sd_auc": float(arr.std(ddof=1)) if arr.size > 1 else float("nan"),
        "min_auc": float(arr.min()) if arr.size else float("nan"),
        "max_auc": float(arr.max()) if arr.size else float("nan"),
        "within_0.45_0.55": bool(arr.size and np.all((arr >= 0.45) & (arr <= 0.55))),
        "aucs": [float(a) for a in aucs],
    }