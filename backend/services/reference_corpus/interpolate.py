"""Monotone (PCHIP) and linear interpolation for reference-value tables.

Design notes
------------
* Reference tables are *served* by interpolation, so the interpolation scheme is
  part of the accuracy budget.  Piecewise-cubic Hermite with Fritsch-Carlson
  monotonicity limits (`pchip`) is used by default: it preserves monotonicity of
  the tabulated quantity (no overshoot), which matters for e.g. the ITTC-1957
  friction coefficient and the deflection influence functions.
* Linear interpolation is kept for compatibility and is used by the
  interpolation-consistency acceptance criterion
  (``max_rel_diff(pchip, linear) <= 1e-3`` on monotone segments, i.e. the
  tabulated grids are dense enough for linear reading to be acceptable).
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np


def _as_arrays(xs: Sequence[float], ys: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or x.size != y.size:
        raise ValueError("xs and ys must be 1-D and of equal length")
    if x.size < 2:
        raise ValueError("at least two tabulated points are required")
    if not np.all(np.diff(x) > 0):
        raise ValueError("abscissa must be strictly increasing")
    return x, y


def _pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Fritsch-Carlson monotone piecewise-cubic slopes (shape preserving)."""
    h = np.diff(x)
    delta = np.diff(y) / h
    n = x.size
    d = np.zeros(n)
    if n == 2:
        d[:] = delta[0]
        return d

    # interior nodes
    for k in range(1, n - 1):
        if delta[k - 1] * delta[k] <= 0.0:
            d[k] = 0.0
        else:
            w1 = 2.0 * h[k] + h[k - 1]
            w2 = h[k] + 2.0 * h[k - 1]
            d[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])

    # one-sided estimates at the ends (Fritsch-Butland / standard PCHIP end rules)
    def _end(h0: float, h1: float, d0: float, d1: float) -> float:
        s = ((2.0 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if s * d0 <= 0.0:
            return 0.0
        if d0 * d1 <= 0.0 and abs(s) > abs(3.0 * d0):
            return 3.0 * d0
        return s

    d[0] = _end(h[0], h[1], delta[0], delta[1])
    d[-1] = _end(h[-1], h[-2], delta[-1], delta[-2])
    return d


def pchip(xs: Sequence[float], ys: Sequence[float], xq: Sequence[float]) -> np.ndarray:
    """Monotone piecewise-cubic Hermite interpolation."""
    x, y = _as_arrays(xs, ys)
    q = np.atleast_1d(np.asarray(xq, dtype=float))
    d = _pchip_slopes(x, y)

    idx = np.clip(np.searchsorted(x, q, side="right") - 1, 0, x.size - 2)
    h = x[idx + 1] - x[idx]
    t = (q - x[idx]) / h
    h00 = (2 * t**3) - (3 * t**2) + 1
    h10 = t**3 - 2 * t**2 + t
    h01 = (-2 * t**3) + (3 * t**2)
    h11 = t**3 - t**2
    return h00 * y[idx] + h10 * h * d[idx] + h01 * y[idx + 1] + h11 * h * d[idx + 1]


def linear(xs: Sequence[float], ys: Sequence[float], xq: Sequence[float]) -> np.ndarray:
    """Piecewise-linear interpolation (compatibility / cross-check scheme)."""
    x, y = _as_arrays(xs, ys)
    q = np.atleast_1d(np.asarray(xq, dtype=float))
    return np.interp(q, x, y)


def monotone_segments(xs: Sequence[float], ys: Sequence[float]) -> List[Tuple[int, int]]:
    """Index ranges ``[i0, i1]`` of maximal monotone (incl. flat) runs."""
    x, y = _as_arrays(xs, ys)
    dy = np.diff(y)
    if dy.size == 0:
        return []
    segs: List[Tuple[int, int]] = []
    start = 0
    sign = np.sign(dy[0])
    for k in range(1, dy.size):
        s = np.sign(dy[k])
        if s == 0 or sign == 0 or s == sign:
            if sign == 0:
                sign = s
            continue
        segs.append((start, k))
        start = k
        sign = s
    segs.append((start, dy.size))
    # drop degenerate two-point segments with identical endpoints
    return [(a, b) for a, b in segs if b - a >= 1]


def interpolation_consistency(
    xs: Sequence[float],
    ys: Sequence[float],
    samples_per_interval: int = 7,
    min_abs_scale: float = 0.0,
) -> dict:
    """Compare PCHIP vs linear on monotone segments.

    Returns ``{"max_rel_diff", "n_points", "worst_x", "segments"}`` where the
    relative difference is measured against ``max(|y|, min_abs_scale)`` so that
    sign changes of the tabulated quantity cannot blow the metric up.
    """
    x, y = _as_arrays(xs, ys)
    segs = monotone_segments(x, y)
    scale = max(min_abs_scale, float(np.max(np.abs(y))) * 1e-12)
    worst = 0.0
    worst_x = float("nan")
    n = 0
    for a, b in segs:
        sx = x[a : b + 1]
        sy = y[a : b + 1]
        if sx.size < 2:
            continue
        qs = []
        for i in range(sx.size - 1):
            for f in range(1, samples_per_interval + 1):
                qs.append(sx[i] + (sx[i + 1] - sx[i]) * f / (samples_per_interval + 1))
        if not qs:
            continue
        q = np.asarray(qs)
        yp = pchip(sx, sy, q)
        yl = linear(sx, sy, q)
        denom = np.maximum(np.abs(yl), scale)
        rel = np.abs(yp - yl) / denom
        k = int(np.argmax(rel))
        if rel[k] > worst:
            worst = float(rel[k])
            worst_x = float(q[k])
        n += q.size
    return {
        "max_rel_diff": worst,
        "n_points": n,
        "worst_x": worst_x,
        "segments": segs,
    }