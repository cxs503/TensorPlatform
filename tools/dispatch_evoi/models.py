"""Two comparable probability candidates + metrics, in plain numpy.

(A) :class:`LogisticModel` -- ridge-penalised Newton/IRLS on a standardised
    design matrix.  Interpretable: every coefficient maps to one named column
    (see ``feature_report``), which is what a reviewer needs to argue that the
    ranking is not spurious.
(B) :class:`GBDT` -- shallow (depth 2) gradient boosting on logistic loss with
    Newton leaf values, histogram split candidates.  Non-parametric control for
    the functional form: if (A) and (B) agree, the signal is not an artifact of
    the linear specification.

Both accept ``sample_weight``, which the backtest uses to apply the IPS weight
``1/pi0(a0|s)`` and thereby correct the *self-selection* of the logged sample
(only dispatched orders have labels).  No scikit-learn dependency: the working
tree's evaluation environment only guarantees numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

__all__ = ["LogisticModel", "GBDT", "auc", "brier", "sigmoid", "precision_at_fraction", "ips_weights"]


def sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def auc(y: Sequence[float], s: Sequence[float]) -> float:
    """Rank (Mann-Whitney) AUC, ties averaged.  ``nan`` on a single-class input."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    n_pos = float((y > 0.5).sum())
    n_neg = float((y <= 0.5).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    sorted_s = s[order]
    i = 0
    while i < len(sorted_s):
        j = i
        while j + 1 < len(sorted_s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = avg_rank
        i = j + 1
    sum_pos = float(ranks[y > 0.5].sum())
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def brier(y: Sequence[float], p: Sequence[float]) -> float:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return float(np.mean((p - y) ** 2))


def precision_at_fraction(
    y: Sequence[float], score: Sequence[float], fraction: float = 0.20
) -> float:
    """Share of positives inside the top ``fraction`` of the ranking."""
    y = np.asarray(y, dtype=float)
    score = np.asarray(score, dtype=float)
    n = len(score)
    k = max(1, int(round(fraction * n)))
    order = np.argsort(-score, kind="mergesort")
    return float(y[order[:k]].mean())


def ips_weights(propensity: Sequence[float], clip: float | None = None) -> np.ndarray:
    """``1/pi0`` weights, normalised to mean 1 (so the ridge penalty stays comparable)."""
    p = np.asarray(propensity, dtype=float)
    if np.any(p <= 0):
        raise ValueError("propensities must be strictly positive (positivity assumption)")
    w = 1.0 / p
    if clip is not None:
        w = np.minimum(w, clip)
    return w * (len(w) / w.sum())


@dataclass
class LogisticModel:
    """Ridge logistic regression fitted by damped Newton steps."""

    l2: float = 1.0
    max_iter: int = 80
    tol: float = 1e-9

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray | None = None,
        names: Sequence[str] | None = None,
    ) -> "LogisticModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, d = X.shape
        self.names_ = list(names) if names is not None else [f"x{i}" for i in range(d)]
        self.mu_ = X.mean(axis=0)
        sd = X.std(axis=0)
        sd[sd < 1e-12] = 1.0
        self.sd_ = sd
        Z = np.hstack([np.ones((n, 1)), (X - self.mu_) / self.sd_])
        w = np.ones(n) if sample_weight is None else np.asarray(sample_weight, dtype=float)
        w = w * (n / w.sum())
        reg = np.full(Z.shape[1], self.l2, dtype=float)
        reg[0] = 0.0  # never penalise the intercept
        beta = np.zeros(Z.shape[1], dtype=float)
        # start from the weighted base rate
        base = np.clip((w * y).sum() / w.sum(), 1e-6, 1 - 1e-6)
        beta[0] = np.log(base / (1 - base))
        self.converged_ = False
        for _ in range(self.max_iter):
            p = sigmoid(Z @ beta)
            grad = Z.T @ (w * (y - p)) - reg * beta
            W = w * p * (1 - p)
            H = Z.T @ (Z * W[:, None]) + np.diag(reg + 1e-10)
            try:
                step = np.linalg.solve(H, grad)
            except np.linalg.LinAlgError:  # pragma: no cover - defensive
                break
            beta = beta + step
            if float(np.max(np.abs(step))) < self.tol:
                self.converged_ = True
                break
        self.beta_ = beta
        self.n_iter_ = _ + 1
        return self

    def decision(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        Z = np.hstack([np.ones((len(X), 1)), (X - self.mu_) / self.sd_])
        return Z @ self.beta_

    def predict(self, X: np.ndarray) -> np.ndarray:
        return sigmoid(self.decision(X))

    def feature_report(self) -> list[dict[str, Any]]:
        """Coefficients on the standardised scale (log-odds per +1 sigma)."""
        rows = [{"name": "intercept", "coef": float(self.beta_[0])}]
        for name, b in zip(self.names_, self.beta_[1:]):
            rows.append({"name": name, "coef": float(b)})
        return rows


# ────────────────────────────── shallow GBDT ──────────────────────────────


@dataclass
class _Node:
    feature: int = -1
    threshold: float = 0.0
    left: "_Node | None" = None
    right: "_Node | None" = None
    value: float = 0.0


def _best_split(
    X: np.ndarray,
    g: np.ndarray,
    h: np.ndarray,
    idx: np.ndarray,
    min_leaf: int,
    l2: float,
    n_bins: int,
) -> tuple[int, float, float]:
    """Return (feature, threshold, gain) maximising the Newton gain."""
    best = (-1, 0.0, 0.0)
    n = len(idx)
    G_all = float(g[idx].sum())
    H_all = float(h[idx].sum())
    parent = G_all * G_all / (H_all + l2)
    for f in range(X.shape[1]):
        col = X[idx, f]
        uniq = np.unique(col)
        if len(uniq) < 2:
            continue
        if len(uniq) > n_bins:
            qs = np.linspace(0, 1, n_bins + 2)[1:-1]
            cand = np.unique(np.quantile(col, qs))
        else:
            cand = (uniq[:-1] + uniq[1:]) / 2.0
        for thr in cand:
            left = col <= thr
            nl = int(left.sum())
            if nl < min_leaf or n - nl < min_leaf:
                continue
            Gl = float(g[idx][left].sum())
            Hl = float(h[idx][left].sum())
            Gr, Hr = G_all - Gl, H_all - Hl
            gain = Gl * Gl / (Hl + l2) + Gr * Gr / (Hr + l2) - parent
            if gain > best[2]:
                best = (f, float(thr), float(gain))
    return best


def _grow(
    X: np.ndarray,
    g: np.ndarray,
    h: np.ndarray,
    idx: np.ndarray,
    depth: int,
    max_depth: int,
    min_leaf: int,
    l2: float,
    n_bins: int,
) -> _Node:
    node = _Node(value=float(g[idx].sum() / (h[idx].sum() + l2)))
    if depth >= max_depth or len(idx) < 2 * min_leaf:
        return node
    f, thr, gain = _best_split(X, g, h, idx, min_leaf, l2, n_bins)
    if f < 0 or gain <= 1e-12:
        return node
    left_mask = X[idx, f] <= thr
    node.feature, node.threshold = f, thr
    node.left = _grow(X, g, h, idx[left_mask], depth + 1, max_depth, min_leaf, l2, n_bins)
    node.right = _grow(X, g, h, idx[~left_mask], depth + 1, max_depth, min_leaf, l2, n_bins)
    return node


def _apply(node: _Node, X: np.ndarray) -> np.ndarray:
    """Leaf values for ``X``.  Recursion returns arrays (never writes into a
    boolean-mask view, which would be a copy and silently drop the update)."""
    out = np.empty(len(X), dtype=float)
    if node.feature < 0:
        out[:] = node.value
        return out
    mask = X[:, node.feature] <= node.threshold
    if mask.any():
        out[mask] = _apply(node.left, X[mask])
    if (~mask).any():
        out[~mask] = _apply(node.right, X[~mask])
    return out


@dataclass
class GBDT:
    """Depth-limited gradient boosting on logistic loss (Newton leaves)."""

    n_estimators: int = 60
    learning_rate: float = 0.1
    max_depth: int = 2
    min_samples_leaf: int = 20
    n_bins: int = 12
    l2: float = 1.0
    #: row subsample per boosting iteration -- the only stochastic component of
    #: the pipeline, and the thing the reproducibility check measures
    subsample: float = 0.8
    random_state: int = 0

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray | None = None,
    ) -> "GBDT":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n = len(X)
        w = np.ones(n) if sample_weight is None else np.asarray(sample_weight, dtype=float)
        w = w * (n / w.sum())
        base = np.clip((w * y).sum() / w.sum(), 1e-6, 1 - 1e-6)
        self.base_ = float(np.log(base / (1 - base)))
        F = np.full(n, self.base_)
        idx_all = np.arange(n)
        rng = np.random.default_rng(self.random_state)
        self.trees_: list[_Node] = []
        for _ in range(self.n_estimators):
            p = sigmoid(F)
            g = w * (y - p)
            h = w * p * (1 - p)
            if 0.0 < self.subsample < 1.0:
                k = max(int(round(self.subsample * n)), 2 * self.min_samples_leaf)
                fit_idx = np.sort(rng.choice(n, size=min(k, n), replace=False))
            else:
                fit_idx = idx_all
            tree = _grow(
                X, g, h, fit_idx, 0, self.max_depth, self.min_samples_leaf, self.l2, self.n_bins
            )
            F += self.learning_rate * _apply(tree, X)
            self.trees_.append(tree)
        return self

    def decision(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        F = np.full(len(X), self.base_)
        for tree in self.trees_:
            F += self.learning_rate * _apply(tree, X)
        return F

    def predict(self, X: np.ndarray) -> np.ndarray:
        return sigmoid(self.decision(X))