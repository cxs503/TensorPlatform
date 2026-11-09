"""Pareto-front quality metrics (convergence, spread, dominance, volume).

Definitions follow the standard literature so that the numbers are comparable
with published benchmarks:

* **GD** (generational distance; Van Veldhuizen & Lamont 2000)
  ``GD = sqrt(mean_i min_j ||p_i - a_j||^2)`` — distance from the obtained set
  to the analytic/true front.  Vanishes only when every obtained point lies on
  the front.  *Convergence* metric.
* **IGD** (inverted generational distance; Zitzler et al. 2000)
  ``IGD = sqrt(mean_j min_i ||a_j - p_i||^2)`` — distance from a dense analytic
  front to the obtained set.  Penalises both divergence and missing coverage.
* **Spacing** (Schott 1995; the variant used in ZDT 2000 §V)
  ``S = sqrt(1/(n-1) * sum_i (dbar - d_i)^2)`` with ``d_i = min_{j!=i} |.|
  using the L1 (Manhattan) distance in objective space.  Measures uniformity of
  the spread; a perfectly uniform front gives ``S = 0``.  *Diversity* metric.
* **Hypervolume (2-D exact)** w.r.t. a reference point: area dominated by the
  set, computed by a sort + sweep.

All functions are pure numpy and deterministic.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

__all__ = [
    "generational_distance",
    "inverted_generational_distance",
    "spacing",
    "hypervolume_2d",
    "coverage",
    "evaluate_front",
]


def _pairwise_distances(P: np.ndarray, A: np.ndarray) -> np.ndarray:
    """Euclidean distance matrix between point sets ``P`` (n, m) and ``A`` (k, m)."""
    P = np.asarray(P, dtype=float)
    A = np.asarray(A, dtype=float)
    if P.ndim == 1:
        P = P[None, :]
    if A.ndim == 1:
        A = A[None, :]
    diff = P[:, None, :] - A[None, :, :]
    return np.sqrt(np.einsum("ijk,ijk->ij", diff, diff))


def generational_distance(points: np.ndarray, front: np.ndarray) -> float:
    """GD = sqrt(mean_i min_j ||p_i - a_j||^2). Lower is better."""
    points = np.asarray(points, dtype=float)
    if points.size == 0:
        return float("inf")
    d = _pairwise_distances(points, front).min(axis=1)
    return float(np.sqrt(np.mean(d ** 2)))


def inverted_generational_distance(points: np.ndarray, front: np.ndarray) -> float:
    """IGD = sqrt(mean_j min_i ||a_j - p_i||^2). Lower is better."""
    points = np.asarray(points, dtype=float)
    if points.size == 0:
        return float("inf")
    d = _pairwise_distances(front, points).min(axis=1)
    return float(np.sqrt(np.mean(d ** 2)))


def spacing(points: np.ndarray) -> float:
    """Schott/ZDT spacing over the L1 distance between neighbouring solutions."""
    points = np.asarray(points, dtype=float)
    n = points.shape[0]
    if n < 3:
        return float("inf")
    d = np.abs(points[:, None, :] - points[None, :, :]).sum(axis=2)
    np.fill_diagonal(d, np.inf)
    d_min = d.min(axis=1)
    dbar = d_min.mean()
    return float(np.sqrt(np.sum((dbar - d_min) ** 2) / (n - 1)))


def hypervolume_2d(points: np.ndarray, reference: np.ndarray) -> float:
    """Exact 2-D hypervolume dominated by ``points`` w.r.t. ``reference``."""
    points = np.asarray(points, dtype=float)
    reference = np.asarray(reference, dtype=float)
    if points.size == 0:
        return 0.0
    pts = points[np.all(points <= reference, axis=1)]
    if pts.shape[0] == 0:
        return 0.0
    order = np.argsort(pts[:, 0])
    pts = pts[order]
    hv = 0.0
    prev_y = reference[1]
    for x, y in pts:
        if y < prev_y:
            hv += (reference[0] - x) * (prev_y - y)
            prev_y = y
    return float(hv)


def coverage(points: np.ndarray, front: np.ndarray, tol: float = 1e-3) -> float:
    """Fraction of obtained points within ``tol`` of the analytic front."""
    points = np.asarray(points, dtype=float)
    if points.size == 0:
        return 0.0
    d = _pairwise_distances(points, front).min(axis=1)
    return float(np.mean(d <= tol))


def evaluate_front(
    points: np.ndarray,
    analytic_front: np.ndarray,
    reference_point: np.ndarray = None,
) -> Dict[str, float]:
    """Bundle all front metrics for one snapshot of a population."""
    points = np.asarray(points, dtype=float)
    metrics = {
        "n_points": int(points.shape[0]),
        "gd": generational_distance(points, analytic_front),
        "igd": inverted_generational_distance(points, analytic_front),
        "spacing": spacing(points),
        "coverage": coverage(points, analytic_front),
    }
    if reference_point is not None:
        metrics["hypervolume"] = hypervolume_2d(points, reference_point)
    return metrics