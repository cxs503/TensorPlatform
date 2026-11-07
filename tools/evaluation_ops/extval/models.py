"""纯 numpy 估计器与度量（宿主无 sklearn / shap 依赖）。

全部实现都小而可证：

- :class:`LogisticRegression`  L2 正则 + Newton-IRLS（每步用 pinv 解正规方程，
  秩亏也稳定），特征标准化内嵌；
- :class:`RandomForest`       CART（Gini，逐特征阈值扫描）+ bootstrap 自助采样，
  predict_proba 取树均值；
- :func:`roc_auc`             Mann-Whitney 秩统计量（含 ties 的平均秩）；
- :func:`brier_score` / :func:`reliability_curve` / :func:`expected_calibration_error`；
- :func:`sampling_shapley`    置换采样 Shapley（满足效率性 sum(phi)=f(x)-E[f]）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

EPS = 1e-12


# --------------------------------------------------------------------------
# 度量
# --------------------------------------------------------------------------

def _rankdata(values: np.ndarray) -> np.ndarray:
    """平均秩（ascending），与 scipy.stats.rankdata 的 method='average' 同义。"""
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    sorted_vals = values[order]
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = avg
        i = j + 1
    return ranks


def roc_auc(y: Sequence[int], score: Sequence[float]) -> float | None:
    """AUC = P(score_pos > score_neg) + 0.5 P(==) ；单类时返回 None。"""
    y_arr = np.asarray(y, dtype=int)
    s_arr = np.asarray(score, dtype=float)
    n_pos = int((y_arr == 1).sum())
    n_neg = int((y_arr == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = _rankdata(s_arr)
    rank_pos = ranks[y_arr == 1].sum()
    return float((rank_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def brier_score(y: Sequence[int], p: Sequence[float]) -> float | None:
    y_arr = np.asarray(y, dtype=float)
    p_arr = np.asarray(p, dtype=float)
    if y_arr.size == 0:
        return None
    return float(np.mean((p_arr - y_arr) ** 2))


def log_loss(y: Sequence[int], p: Sequence[float]) -> float | None:
    y_arr = np.asarray(y, dtype=float)
    p_arr = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
    if y_arr.size == 0:
        return None
    return float(-np.mean(y_arr * np.log(p_arr) + (1 - y_arr) * np.log(1 - p_arr)))


def reliability_curve(
    y: Sequence[int], p: Sequence[float], n_bins: int = 8
) -> list[dict[str, float | int]]:
    """等宽分箱的可靠性表（预测均值 vs 实际频率）。空箱跳过，不折成 0。"""
    y_arr = np.asarray(y, dtype=float)
    p_arr = np.asarray(p, dtype=float)
    out: list[dict[str, float | int]] = []
    if y_arr.size == 0:
        return out
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p_arr >= lo) & (p_arr < hi if i < n_bins - 1 else p_arr <= hi)
        if not mask.any():
            continue
        out.append(
            {
                "bin_lo": float(lo),
                "bin_hi": float(hi),
                "n": int(mask.sum()),
                "mean_pred": float(p_arr[mask].mean()),
                "observed": float(y_arr[mask].mean()),
            }
        )
    return out


def expected_calibration_error(
    y: Sequence[int], p: Sequence[float], n_bins: int = 8
) -> float | None:
    curve = reliability_curve(y, p, n_bins)
    total = sum(int(row["n"]) for row in curve)
    if total == 0:
        return None
    ece = 0.0
    for row in curve:
        ece += (int(row["n"]) / total) * abs(float(row["mean_pred"]) - float(row["observed"]))
    return float(ece)


def bootstrap_auc_ci(
    y: Sequence[int], score: Sequence[float], n_boot: int = 1000, seed: int = 20261107
) -> dict[str, float | None]:
    """非参数 bootstrap 的 AUC 95% 区间（固定种子，可复现）。"""
    y_arr = np.asarray(y, dtype=int)
    s_arr = np.asarray(score, dtype=float)
    if y_arr.size == 0:
        return {"lo": None, "hi": None, "n_boot": 0}
    rng = np.random.default_rng(seed)
    vals = []
    n = y_arr.size
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        auc = roc_auc(y_arr[idx], s_arr[idx])
        if auc is not None:
            vals.append(auc)
    if not vals:
        return {"lo": None, "hi": None, "n_boot": 0}
    return {
        "lo": float(np.percentile(vals, 2.5)),
        "hi": float(np.percentile(vals, 97.5)),
        "n_boot": len(vals),
    }


# --------------------------------------------------------------------------
# 逻辑回归（Newton-IRLS + L2）
# --------------------------------------------------------------------------

@dataclass
class LogisticRegression:
    l2: float = 1.0
    max_iter: int = 100
    tol: float = 1e-8

    def __post_init__(self) -> None:
        self.coef_: np.ndarray | None = None
        self.intercept_: float = 0.0
        self.mu_: np.ndarray | None = None
        self.sigma_: np.ndarray | None = None
        self.n_iter_: int = 0
        self.converged_: bool = False

    def _standardize(self, X: np.ndarray) -> np.ndarray:
        assert self.mu_ is not None and self.sigma_ is not None
        return (X - self.mu_) / self.sigma_

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> "LogisticRegression":
        X_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        if X_arr.ndim != 2 or X_arr.shape[0] != y_arr.shape[0]:
            raise ValueError("X/y shape mismatch")
        if X_arr.shape[0] == 0:
            raise ValueError("empty training set")
        self.mu_ = X_arr.mean(axis=0)
        sigma = X_arr.std(axis=0)
        sigma[sigma < EPS] = 1.0
        self.sigma_ = sigma
        Z = self._standardize(X_arr)
        n, d = Z.shape
        A = np.hstack([np.ones((n, 1)), Z])
        theta = np.zeros(d + 1)
        ridge = np.full(d + 1, float(self.l2))
        ridge[0] = 0.0  # 不惩罚截距
        for it in range(self.max_iter):
            eta = A @ theta
            p = 1.0 / (1.0 + np.exp(-np.clip(eta, -35, 35)))
            w = np.clip(p * (1 - p), 1e-8, None)
            grad = A.T @ (y_arr - p) - ridge * theta
            hess = A.T @ (A * w[:, None]) + np.diag(ridge)
            step = np.linalg.pinv(hess) @ grad
            theta = theta + step
            self.n_iter_ = it + 1
            if float(np.max(np.abs(step))) < self.tol:
                self.converged_ = True
                break
        self.intercept_ = float(theta[0])
        self.coef_ = theta[1:]
        return self

    def decision_function(self, X: Sequence[Sequence[float]]) -> np.ndarray:
        if self.coef_ is None:
            raise RuntimeError("model not fitted")
        Z = self._standardize(np.asarray(X, dtype=float))
        return self.intercept_ + Z @ self.coef_

    def predict_proba(self, X: Sequence[Sequence[float]]) -> np.ndarray:
        eta = np.clip(self.decision_function(X), -35, 35)
        return 1.0 / (1.0 + np.exp(-eta))

    def coef_original_scale(self) -> np.ndarray:
        assert self.coef_ is not None and self.sigma_ is not None
        return self.coef_ / self.sigma_


# --------------------------------------------------------------------------
# CART 随机森林
# --------------------------------------------------------------------------

class _Tree:
    """定长数组树（遍历用向量化，避免逐行 python 循环）。"""

    __slots__ = ("feature", "threshold", "left", "right", "value")

    def __init__(self) -> None:
        self.feature: list[int] = []
        self.threshold: list[float] = []
        self.left: list[int] = []
        self.right: list[int] = []
        self.value: list[float] = []

    def _new_node(self) -> int:
        self.feature.append(-1)
        self.threshold.append(0.0)
        self.left.append(-1)
        self.right.append(-1)
        self.value.append(0.0)
        return len(self.value) - 1

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.asarray(self.feature, dtype=int),
            np.asarray(self.threshold, dtype=float),
            np.asarray(self.left, dtype=int),
            np.asarray(self.right, dtype=int),
            np.asarray(self.value, dtype=float),
        )


def _build_tree(
    X: np.ndarray,
    y: np.ndarray,
    idx: np.ndarray,
    depth: int,
    max_depth: int,
    min_leaf: int,
    rng: np.random.Generator,
    max_features: int,
) -> _Tree:
    tree = _Tree()

    def grow(node_idx: np.ndarray, level: int) -> int:
        node = tree._new_node()
        tree.value[node] = float(y[node_idx].mean()) if node_idx.size else 0.0
        if (
            level >= max_depth
            or node_idx.size < 2 * min_leaf
            or float(y[node_idx].mean()) in (0.0, 1.0)
        ):
            return node
        d = X.shape[1]
        feats = rng.choice(d, size=min(max_features, d), replace=False)
        parent = y[node_idx].mean()
        base = len(node_idx)
        imp = parent * (1 - parent)
        best: tuple[float, int, float, np.ndarray, np.ndarray] | None = None
        for f in feats:
            col = X[node_idx, f]
            order = np.argsort(col, kind="mergesort")
            col_sorted = col[order]
            y_sorted = y[node_idx][order]
            cum = np.cumsum(y_sorted)
            for pos in range(min_leaf, base - min_leaf + 1):
                if col_sorted[pos - 1] == col_sorted[pos]:
                    continue
                left_n = pos
                right_n = base - pos
                left_mean = cum[pos - 1] / left_n
                right_mean = (cum[-1] - cum[pos - 1]) / right_n
                score = (
                    imp
                    - (left_n / base) * left_mean * (1 - left_mean)
                    - (right_n / base) * right_mean * (1 - right_mean)
                )
                if best is None or score > best[0] + 1e-12:
                    thr = 0.5 * (col_sorted[pos - 1] + col_sorted[pos])
                    best = (score, int(f), float(thr), None, None)
        if best is None or best[0] <= 1e-12:
            return node
        _, f, thr, _, _ = best
        mask = X[node_idx, f] <= thr
        left_idx, right_idx = node_idx[mask], node_idx[~mask]
        if left_idx.size < min_leaf or right_idx.size < min_leaf:
            return node
        tree.feature[node] = f
        tree.threshold[node] = thr
        tree.left[node] = grow(left_idx, level + 1)
        tree.right[node] = grow(right_idx, level + 1)
        return node

    grow(idx, 0)
    return tree


def _tree_predict(tree: _Tree, X: np.ndarray) -> np.ndarray:
    feat, thr, left, right, value = tree.arrays()
    n = X.shape[0]
    rows = np.arange(n)
    node = np.zeros(n, dtype=int)
    for _ in range(feat.size):  # 深度不超过节点数
        f = feat[node]
        active = f >= 0
        if not active.any():
            break
        safe_f = np.where(active, f, 0)
        go_left = X[rows, safe_f] <= thr[node]
        nxt = np.where(go_left, left[node], right[node])
        node = np.where(active, nxt, node)
    return value[node]


@dataclass
class RandomForest:
    n_trees: int = 200
    max_depth: int = 4
    min_leaf: int = 5
    max_features: int | None = None
    seed: int = 20261107

    def __post_init__(self) -> None:
        self.trees_: list[_Tree] = []

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> "RandomForest":
        X_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        if X_arr.shape[0] == 0:
            raise ValueError("empty training set")
        rng = np.random.default_rng(self.seed)
        n, d = X_arr.shape
        mf = self.max_features or max(1, int(round(math.sqrt(d))))
        self.trees_ = []
        for _ in range(self.n_trees):
            idx = rng.integers(0, n, n)
            if y_arr[idx].min() == y_arr[idx].max():
                continue  # bootstrap 抽到单类：跳过（不伪造分裂）
            self.trees_.append(
                _build_tree(X_arr, y_arr, idx, 0, self.max_depth, self.min_leaf, rng, mf)
            )
        if not self.trees_:
            self.trees_.append(_build_tree(X_arr, y_arr, np.arange(n), 0, 0, self.min_leaf, rng, mf))
        return self

    def predict_proba(self, X: Sequence[Sequence[float]]) -> np.ndarray:
        X_arr = np.asarray(X, dtype=float)
        preds = np.vstack([_tree_predict(t, X_arr) for t in self.trees_])
        return preds.mean(axis=0)


# --------------------------------------------------------------------------
# 采样 Shapley（模型无关，效率性可验）
# --------------------------------------------------------------------------

def sampling_shapley(
    predict: Callable[[np.ndarray], np.ndarray],
    X_explain: Sequence[Sequence[float]],
    X_background: Sequence[Sequence[float]],
    n_permutations: int = 200,
    seed: int = 20261107,
) -> dict[str, list[list[float]]]:
    """置换采样 Shapley 值。

    对每个待解释样本走 ``n_permutations`` 个随机特征排列：沿排列逐个揭示特征，
    边际贡献 ``phi_i += v(S ∪ {i}) - v(S)``。未揭示的特征用背景均值代入
    （``v(S) = f(x_S, mean_{back})``）。置换即对 Shapley 权重分布的蒙特卡洛抽样
    （不是 KernelSHAP 的回归近似），当背景只有一行时**效率性精确成立**：
    ``sum(phi) = f(x) - f(背景)``。
    """
    Xe = np.asarray(X_explain, dtype=float)
    Xb = np.asarray(X_background, dtype=float)
    if Xe.ndim != 2 or Xb.ndim != 2:
        raise ValueError("X_explain / X_background must be 2-D")
    if Xe.size == 0 or Xb.size == 0:
        raise ValueError("need non-empty explain/background matrices")
    d = Xe.shape[1]
    ref = np.mean(Xb, axis=0)
    base = float(np.mean(predict(Xb))) if Xb.shape[0] == 1 else float(predict(ref[None, :])[0])
    rng = np.random.default_rng(seed)
    phi = np.zeros_like(Xe, dtype=float)
    for _ in range(n_permutations):
        perm = rng.permutation(d)
        cur = np.tile(ref, (Xe.shape[0], 1))
        prev = np.full(Xe.shape[0], base, dtype=float)
        for feat in perm:
            cur[:, feat] = Xe[:, feat]
            vals = np.asarray(predict(cur), dtype=float)
            phi[:, feat] += vals - prev
            prev = vals
    phi /= float(n_permutations)
    return {
        "phi": phi.tolist(),
        "base_value": base,
        "efficiency_gap": (phi.sum(axis=1) - (np.asarray(predict(Xe), dtype=float) - base)).tolist(),
    }
