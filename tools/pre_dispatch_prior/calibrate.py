"""派发前可行性先验 —— 校准核心（纯 numpy，无 sklearn 依赖）。

三层
----
1. 基学习器 ``LogisticModel``：L2 逻辑回归（Newton/IRLS，截距不正则），对标准化特征
   给出**原始分数**（未校准 logit）。原始分数在分布漂移下系统性偏差：训练窗的先验
   与派发窗不同，因此必须有第 2 层。
2. 校准映射：
   * ``PlattCalibrator`` —— 在**校准切分**（训练窗尾部、按时间取）上拟合
     ``p = σ(a·s + b)``，2 参 Newton 解。Platt 只能修仿射偏差，形式简单、样本效率高。
   * ``IsotonicCalibrator`` —— PAV（pool adjacent violators）单调回归，非参、能修任意
     单调变形，但小样本会在台阶处过拟合；输出块均值 + 块中心线性插值 + 两端 clip。
3. 度量：Brier / Brier skill（相对气候基准）/ AUC（含并列秩处理）/ log loss /
   ECE 与可靠性表（等宽分箱，空箱剔除并报有效箱数）。

全部函数是纯的、可复算的；``fit`` 只吃 numpy 数组，测试可用合成数据钉死性质。
"""
from __future__ import annotations

import dataclasses
import math
from typing import Sequence

import numpy as np

EPS = 1e-12


# ───────────────────────────── 基础函数 ─────────────────────────────

def sigmoid(z):
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1.0 - EPS)
    return np.log(p / (1.0 - p))


class Standardizer:
    """z-score 标准化（统计量只来自训练切分）。零方差列 scale=1。"""

    def __init__(self, mean: Sequence[float] | None = None, scale: Sequence[float] | None = None):
        self.mean = None if mean is None else np.asarray(mean, dtype=float)
        self.scale = None if scale is None else np.asarray(scale, dtype=float)

    def fit(self, X: np.ndarray) -> "Standardizer":
        self.mean = X.mean(axis=0)
        sd = X.std(axis=0)
        sd[sd < 1e-12] = 1.0
        self.scale = sd
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean) / self.scale

    def to_dict(self) -> dict:
        return {"mean": self.mean.tolist(), "scale": self.scale.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "Standardizer":
        return cls(d["mean"], d["scale"])


# ───────────────────────────── 基学习器 ─────────────────────────────

@dataclasses.dataclass
class LogisticModel:
    """L2 逻辑回归（Newton/IRLS）。截距项不正则化；特征需已标准化。"""

    coef: np.ndarray | None = None
    intercept: float = 0.0
    l2: float = 1.0
    n_iter: int = 60
    tol: float = 1e-8
    iterations_run: int = 0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LogisticModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, d = X.shape
        Xa = np.hstack([np.ones((n, 1)), X])
        w = np.zeros(d + 1)
        # 温和初始化：截距取先验 logit，避免分离数据上的极端初值
        p0 = float(np.clip(y.mean(), 1e-6, 1 - 1e-6))
        w[0] = math.log(p0 / (1 - p0))
        reg = np.full(d + 1, float(self.l2))
        reg[0] = 0.0
        for it in range(1, self.n_iter + 1):
            p = sigmoid(Xa @ w)
            W = np.clip(p * (1 - p), 1e-9, None)
            grad = Xa.T @ (p - y) + reg * w
            H = (Xa * W[:, None]).T @ Xa + np.diag(reg + 1e-9)
            try:
                step = np.linalg.solve(H, grad)
            except np.linalg.LinAlgError:      # pragma: no cover - 数值兜底
                step = np.linalg.lstsq(H, grad, rcond=None)[0]
            w = w - step
            self.iterations_run = it
            if np.max(np.abs(step)) < self.tol:
                break
        self.coef = w[1:]
        self.intercept = float(w[0])
        return self

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        return X @ self.coef + self.intercept

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return sigmoid(self.decision_function(X))

    def to_dict(self) -> dict:
        return {"coef": self.coef.tolist(), "intercept": self.intercept,
                "l2": self.l2, "iterations_run": self.iterations_run,
                "n_iter": self.n_iter, "tol": self.tol}

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticModel":
        m = cls(l2=d.get("l2", 1.0), n_iter=d.get("n_iter", 100), tol=d.get("tol", 1e-10))
        m.coef = np.asarray(d["coef"], dtype=float)
        m.intercept = float(d["intercept"])
        m.iterations_run = int(d.get("iterations_run", 0))
        return m


# ───────────────────────────── Platt scaling ─────────────────────────────

@dataclasses.dataclass
class PlattCalibrator:
    """2 参仿射校准 ``p = σ(a·s + b)``，s 为原始分数（logit 尺度）。

    Newton 迭代解凸问题（Platt 1999），带两项数值保护：

    * ``target_smoothing`` —— Platt 的 ``t = (N+ + 1)/(N+ + 2)`` 平滑目标，防可分数据上发散；
    * ``ridge`` + ``slope_bounds`` —— MAP 先验 ``a→1, b→0``（弱，正常数据不动）加上斜率硬
      上限 ``[0.05, 20]``。**动机是实测**：小样本 + 近似可分时最小二乘意义上的 Newton 会
      把斜率推到 1e10 量级，任何预测都被 σ 压成 1.0，排序信息全丢（本节点在合成 fixture
      上复现过）。上界只防退化，不改变正常数据的解；触发时 ``hit_bound=True`` 落账。
    """

    a: float = 1.0
    b: float = 0.0
    target_smoothing: bool = True
    ridge: float = 1.0
    slope_bounds: tuple[float, float] = (0.05, 20.0)
    n_iter: int = 200
    tol: float = 1e-12
    iterations_run: int = 0
    hit_bound: bool = False

    def fit(self, scores: np.ndarray, y: np.ndarray) -> "PlattCalibrator":
        scores = np.asarray(scores, dtype=float)
        y = np.asarray(y, dtype=float)
        n = len(y)
        n_pos = float(y.sum())
        n_neg = n - n_pos
        if self.target_smoothing:
            t = np.where(y > 0.5, (n_pos + 1.0) / (n_pos + 2.0), 1.0 / (n_neg + 2.0))
        else:
            t = y.copy()
        a, b = 1.0, 0.0
        lam = float(self.ridge)
        for it in range(1, self.n_iter + 1):
            p = sigmoid(a * scores + b)
            W = np.clip(p * (1 - p), 1e-12, None)
            g = np.array([np.sum((p - t) * scores) + lam * (a - 1.0),
                          np.sum(p - t) + lam * b])
            H = np.array([[np.sum(W * scores * scores), np.sum(W * scores)],
                          [np.sum(W * scores), np.sum(W)]])
            H = H + np.eye(2) * (lam + 1e-12)
            try:
                step = np.linalg.solve(H, g)
            except np.linalg.LinAlgError:      # pragma: no cover
                step = np.linalg.lstsq(H, g, rcond=None)[0]
            a, b = a - step[0], b - step[1]
            self.iterations_run = it
            if np.max(np.abs(step)) < self.tol:
                break
        lo, hi = self.slope_bounds
        if not np.isfinite(a):
            a = 1.0
        if a < lo or a > hi:
            self.hit_bound = True
            a = float(np.clip(a, lo, hi))
        self.a, self.b = float(a), float(b)
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        return sigmoid(self.a * np.asarray(scores, dtype=float) + self.b)

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "target_smoothing": self.target_smoothing,
                "ridge": self.ridge, "slope_bounds": list(self.slope_bounds),
                "hit_bound": self.hit_bound, "iterations_run": self.iterations_run}

    @classmethod
    def from_dict(cls, d: dict) -> "PlattCalibrator":
        c = cls(a=float(d["a"]), b=float(d["b"]),
                target_smoothing=bool(d.get("target_smoothing", True)),
                ridge=float(d.get("ridge", 1.0)),
                slope_bounds=tuple(d.get("slope_bounds") or (0.05, 20.0)))
        c.iterations_run = int(d.get("iterations_run", 0))
        c.hit_bound = bool(d.get("hit_bound", False))
        return c


# ───────────────────────────── 保序回归（PAV） ─────────────────────────────

def _pav(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """pool adjacent violators：最小权重平方误差的单调非降拟合（值序列）。"""
    blocks: list[list[float]] = []   # [sum_w, sum_wy, weight]
    for v, w in zip(values, weights):
        cur = [float(w), float(w) * float(v), float(w)]
        blocks.append(cur)
        while len(blocks) > 1:
            prev, last = blocks[-2], blocks[-1]
            if prev[1] / prev[0] <= last[1] / last[0]:
                break
            prev[0] += last[0]
            prev[1] += last[1]
            prev[2] += last[2]
            blocks.pop()
    out = np.empty(len(values), dtype=float)
    i = 0
    for bw, swy, _ in blocks:
        k = int(round(bw)) if abs(bw - round(bw)) < 1e-9 else None
        if k is None:                       # pragma: no cover - 仅防御
            raise RuntimeError("non-integral block weight")
        out[i:i + k] = swy / bw
        i += k
    return out


@dataclasses.dataclass
class IsotonicCalibrator:
    """保序（单调非降）校准：对校准切分按分数排序做 PAV，返回块均值；预测时块中心线性插值 + 两端 clip。"""

    x: np.ndarray | None = None       # 块中心（升序）
    y: np.ndarray | None = None       # 块均值（非降）

    def fit(self, scores: np.ndarray, y: np.ndarray) -> "IsotonicCalibrator":
        scores = np.asarray(scores, dtype=float)
        y = np.asarray(y, dtype=float)
        order = np.argsort(scores, kind="mergesort")
        s_sorted = scores[order]
        y_sorted = y[order]
        fitted = _pav(y_sorted, np.ones(len(y_sorted)))
        # 合并同值块 → 以块内样本均值为代表点
        xs: list[float] = []
        ys: list[float] = []
        i = 0
        n = len(s_sorted)
        while i < n:
            j = i
            while j + 1 < n and (abs(fitted[j + 1] - fitted[i]) < 1e-15):
                j += 1
            xs.append(float(s_sorted[i:j + 1].mean()))
            ys.append(float(fitted[i]))
            i = j + 1
        self.x = np.asarray(xs, dtype=float)
        self.y = np.clip(np.asarray(ys, dtype=float), EPS, 1.0 - EPS)
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype=float)
        return np.clip(np.interp(scores, self.x, self.y), EPS, 1.0 - EPS)

    def is_monotone(self) -> bool:
        return bool(np.all(np.diff(self.y) >= -1e-12))

    def to_dict(self) -> dict:
        return {"x": self.x.tolist(), "y": self.y.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "IsotonicCalibrator":
        c = cls()
        c.x = np.asarray(d["x"], dtype=float)
        c.y = np.asarray(d["y"], dtype=float)
        return c


# ───────────────────────────── 度量 ─────────────────────────────

def brier(p: np.ndarray, y: np.ndarray) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2))


def brier_skill(p: np.ndarray, y: np.ndarray, ref: float | None = None) -> float:
    """Brier skill score = 1 - BS/BS_ref；ref 缺省为训练窗气候基准（样本先验）。"""
    y = np.asarray(y, dtype=float)
    ref = float(y.mean()) if ref is None else float(ref)
    bs_ref = brier(np.full(len(y), ref), y)
    if bs_ref <= 0:
        return 0.0
    return 1.0 - brier(p, y) / bs_ref


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(p: np.ndarray, y: np.ndarray) -> float:
    """ROC AUC（Mann–Whitney，并列取平均秩；单类返回 nan）。"""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    n_pos = int((y > 0.5).sum())
    n_neg = int((y <= 0.5).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    sorted_p = p[order]
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rank_sum = ranks[y > 0.5].sum()
    return float((rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def reliability_table(p: np.ndarray, y: np.ndarray, n_bins: int = 10,
                      strategy: str = "uniform") -> list[dict]:
    """可靠性表：每箱 (n, 预测均值, 实测频率, |差|)。空箱剔除（分母为 0 不写 0）。"""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    if strategy == "quantile":
        edges = np.unique(np.quantile(p, np.linspace(0, 1, n_bins + 1)))
    else:
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows: list[dict] = []
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        mask = (p >= lo) & (p < hi) if k < len(edges) - 2 else (p >= lo) & (p <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        rows.append({
            "bin_lo": float(lo), "bin_hi": float(hi), "n": n,
            "mean_pred": float(p[mask].mean()),
            "obs_freq": float(y[mask].mean()),
        })
    return rows


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10, strategy: str = "uniform") -> dict:
    """Expected Calibration Error（样本加权）。返回 ECE/MCE/有效箱数/可靠性表。"""
    table = reliability_table(p, y, n_bins=n_bins, strategy=strategy)
    p = np.asarray(p, dtype=float)
    total = len(p)
    if total == 0 or not table:
        return {"ece": None, "mce": None, "n_bins_used": 0, "table": []}
    gaps = [abs(r["obs_freq"] - r["mean_pred"]) for r in table]
    weights = [r["n"] / total for r in table]
    return {
        "ece": float(sum(g * w for g, w in zip(gaps, weights))),
        "mce": float(max(gaps)),
        "n_bins_used": len(table),
        "table": table,
    }


def binned_skill(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict]:
    """分箱校准曲线（等频分箱），供图与报告直接引用。"""
    return reliability_table(p, y, n_bins=n_bins, strategy="quantile")