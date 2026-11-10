"""Feasibility-prior calibration: turn a stage's own score into a probability.

What this is
------------
Many pipeline stages emit a *score* (an optimality residual, a gate margin, a
surrogate distance) and are then asked "is this candidate feasible?".  Answering
that with a hard threshold is unfalsifiable: nothing checks whether the threshold
means anything.  This module calibrates the score into a probability instead, and
reports the two standard falsification statistics:

* **Brier score** -- mean squared error of the predicted probability (lower is
  better; a constant base-rate predictor is the honest reference);
* **ECE** (expected calibration error) -- binwise ``|predicted - observed|``
  averaged with bin occupancy as the weight, so a model that is right on average
  but wrong bin by bin is caught.

The fitted link is a logistic on the scalar score (monotone by construction: the
slope must be positive for "score up => more feasible", and a negative/zero slope
is reported instead of being silently accepted).  Internal standardisation keeps
Newton's method well conditioned for arbitrary score units, and a tiny ridge term
keeps separable data finite.

Domain-free: the caller supplies numbers.  Nothing here knows what a score is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

import numpy as np


class CalibrationError(ValueError):
    """Raised on unusable calibration input (fail-closed)."""


@dataclass(frozen=True)
class CalibrationReport:
    """Fit + calibration statistics for one score series."""

    n: int
    base_rate: float
    intercept: float           # on standardised scores
    slope: float               # on standardised scores, must be > 0
    score_mean: float
    score_std: float
    brier: float
    brier_reference: float     # Brier of the constant base-rate predictor
    ece: float
    max_bin_gap: float
    auroc: float
    monotone: bool             # same direction as the label, i.e. slope > 0
    bins: Tuple[Dict[str, float], ...] = ()

    @property
    def brier_skill(self) -> float:
        """1 - brier/reference; > 0 means the score beats the base rate."""
        if self.brier_reference <= 0.0:
            return 0.0
        return 1.0 - self.brier / self.brier_reference

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n": self.n,
            "base_rate": self.base_rate,
            "intercept": self.intercept,
            "slope": self.slope,
            "score_mean": self.score_mean,
            "score_std": self.score_std,
            "brier": self.brier,
            "brier_reference": self.brier_reference,
            "brier_skill": self.brier_skill,
            "ece": self.ece,
            "max_bin_gap": self.max_bin_gap,
            "auroc": self.auroc,
            "monotone": self.monotone,
            "bins": [dict(b) for b in self.bins],
        }

    def probability(self, score: float) -> float:
        """Calibrated feasibility probability at a raw score."""
        return _sigmoid(self.intercept + self.slope * ((score - self.score_mean) / self.score_std))

    def cutoff(self, target: float) -> float:
        """Raw score whose calibrated probability equals ``target``."""
        if not 0.0 < target < 1.0:
            raise CalibrationError("target must lie strictly inside (0, 1)")
        if self.slope <= 0.0:
            raise CalibrationError("cannot invert a non-increasing score")
        logit = math.log(target / (1.0 - target))
        return self.score_mean + self.score_std * (logit - self.intercept) / self.slope


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _as_arrays(scores: Sequence[float], labels: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    s = np.asarray(scores, dtype=float).reshape(-1)
    y = np.asarray(labels, dtype=float).reshape(-1)
    if s.size != y.size:
        raise CalibrationError(f"length mismatch: {s.size} scores vs {y.size} labels")
    if s.size < 4:
        raise CalibrationError("need at least 4 observations")
    if not np.all(np.isfinite(s)):
        raise CalibrationError("scores contain non-finite values")
    if not np.all((y == 0.0) | (y == 1.0)):
        raise CalibrationError("labels must be 0/1")
    if y.min() == y.max():
        raise CalibrationError("labels are single-class; calibration is undefined")
    return s, y


def fit_logistic(
    scores: Sequence[float],
    labels: Sequence[float],
    ridge: float = 1e-6,
    iters: int = 100,
    tol: float = 1e-10,
) -> Tuple[float, float, float, float]:
    """IRLS fit of ``P(label=1|s) = sigmoid(b + a*z)``, ``z`` standardised.

    Returns ``(intercept, slope, score_mean, score_std)``.  A tiny ridge
    regulariser is applied to the slope so perfectly separable data stay finite.
    """
    s, y = _as_arrays(scores, labels)
    mu = float(s.mean())
    sd = float(s.std(ddof=0))
    if sd <= 0.0:
        raise CalibrationError("scores are constant; no ordering information")
    z = (s - mu) / sd

    beta = np.array([math.log(y.mean() / (1.0 - y.mean())), 0.0])
    for _ in range(iters):
        eta = beta[0] + beta[1] * z
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.maximum(p * (1.0 - p), 1e-12)
        grad = np.array([float(np.sum(y - p)), float(np.sum((y - p) * z))])
        grad[1] -= ridge * beta[1]                      # ridge only on the slope
        X = np.column_stack([np.ones_like(z), z])
        hess = (X * w[:, None]).T @ X
        hess[1, 1] += ridge
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:  # pragma: no cover - defensive
            break
        beta = beta + step
        if float(np.max(np.abs(step))) < tol:
            break
    return float(beta[0]), float(beta[1]), mu, sd


def _auroc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank-based AUROC (ties handled by mid-ranks); 0.5 == no information."""
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(scores.size, dtype=float)
    sorted_scores = scores[order]
    i = 0
    while i < scores.size:
        j = i
        while j + 1 < scores.size and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    n_pos = float(labels.sum())
    n_neg = float(scores.size - n_pos)
    return float((ranks[labels == 1].sum() - n_pos * (n_pos + 1.0) / 2.0) / (n_pos * n_neg))


def calibrate(
    scores: Sequence[float],
    labels: Sequence[float],
    n_bins: int = 10,
) -> CalibrationReport:
    """Fit the logistic link and report Brier / ECE / AUROC / reliability bins."""
    s, y = _as_arrays(scores, labels)
    if n_bins < 2:
        raise CalibrationError("n_bins must be >= 2")
    b, a, mu, sd = fit_logistic(s, y)
    z = (s - mu) / sd
    p = 1.0 / (1.0 + np.exp(-(b + a * z)))

    base = float(y.mean())
    brier = float(np.mean((p - y) ** 2))
    brier_ref = float(np.mean((base - y) ** 2))

    # equal-count bins on the predicted probability (occupancy-weighted ECE)
    order = np.argsort(p, kind="mergesort")
    p_sorted, y_sorted = p[order], y[order]
    edges = np.linspace(0, p_sorted.size, int(n_bins) + 1).astype(int)
    bins = []
    ece = 0.0
    max_gap = 0.0
    for k in range(int(n_bins)):
        lo, hi = edges[k], edges[k + 1]
        if hi <= lo:
            continue
        frac = (hi - lo) / p_sorted.size
        mean_p = float(p_sorted[lo:hi].mean())
        mean_y = float(y_sorted[lo:hi].mean())
        gap = abs(mean_p - mean_y)
        ece += frac * gap
        max_gap = max(max_gap, gap)
        bins.append({
            "predicted": mean_p,
            "observed": mean_y,
            "count": float(hi - lo),
            "gap": gap,
        })

    return CalibrationReport(
        n=int(s.size),
        base_rate=base,
        intercept=b,
        slope=a,
        score_mean=mu,
        score_std=sd,
        brier=brier,
        brier_reference=brier_ref,
        ece=float(ece),
        max_bin_gap=float(max_gap),
        auroc=_auroc(s, y),
        monotone=bool(a > 0.0),
        bins=tuple(bins),
    )