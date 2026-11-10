"""
Multi-Objective Optimization Quality Metrics.

Convergence metrics:
- Generational Distance (GD): average distance from approx to reference
- Inverted Generational Distance (IGD): average distance from reference to approx

Diversity metrics:
- Spread: distribution uniformity measure
- Hypervolume (HV): volume of objective space dominated by approx front

Reference:
  Deb, K. (2001). Multi-Objective Optimization using Evolutionary Algorithms.
  Wiley & Sons.
"""
import numpy as np
from typing import Dict, Optional, Tuple


def generational_distance(approx: np.ndarray, reference: np.ndarray) -> float:
    """
    Generational Distance (GD).
    
    Measures how close the approximated Pareto front is to the true front.
    GD = (1/n) * sqrt(sum(d_i^2))
    where d_i = min distance from approx[i] to any point in reference.
    
    Lower is better. GD < 1e-3 indicates good convergence.
    
    Args:
        approx: (N, M) approximated Pareto front
        reference: (K, M) true/reference Pareto front
    
    Returns:
        GD value (float)
    """
    if len(approx) == 0:
        return float('inf')
    
    distances = []
    for a in approx:
        min_d = np.min(np.sqrt(np.sum((reference - a) ** 2, axis=1)))
        distances.append(min_d)
    
    return np.sqrt(np.sum(np.array(distances) ** 2)) / len(approx)


def inverted_generational_distance(approx: np.ndarray, reference: np.ndarray) -> float:
    """
    Inverted Generational Distance (IGD).
    
    Measures both convergence and diversity.
    IGD = (1/|P*|) * sum(d_i)
    where d_i = min distance from reference[i] to any point in approx.
    
    Lower is better. IGD < 1e-2 indicates good convergence and coverage.
    
    Args:
        approx: (N, M) approximated Pareto front
        reference: (K, M) true/reference Pareto front
    
    Returns:
        IGD value (float)
    """
    if len(approx) == 0:
        return float('inf')
    
    distances = []
    for r in reference:
        min_d = np.min(np.sqrt(np.sum((approx - r) ** 2, axis=1)))
        distances.append(min_d)
    
    return np.sum(distances) / len(reference)


def spread(approx: np.ndarray, reference: Optional[np.ndarray] = None) -> float:
    """
    Spread metric for 2D problems.
    
    Measures the distribution uniformity of the approximated front.
    Spread = (d_f + d_l + sum(|d_i - d_mean|)) / (d_f + d_l + n*d_mean)
    
    where d_f, d_l are distances to extreme points, d_i are consecutive distances.
    
    Lower is better. Spread < 0.5 indicates good diversity.
    Spread = 0 means perfectly uniform distribution.
    
    Args:
        approx: (N, 2) approximated Pareto front
        reference: (K, 2) true Pareto front (optional, for extreme points)
    
    Returns:
        Spread value (float)
    """
    if len(approx) < 2:
        return float('inf')
    
    # Sort by first objective
    sorted_idx = np.argsort(approx[:, 0])
    sorted_approx = approx[sorted_idx]
    
    # Compute distances between consecutive points
    d = np.sqrt(np.sum(np.diff(sorted_approx, axis=0) ** 2, axis=1))
    d_mean = np.mean(d)
    
    if d_mean < 1e-14:
        return float('inf')
    
    # Extreme points
    if reference is not None and len(reference) > 0:
        ref_sorted = reference[np.argsort(reference[:, 0])]
        d_f = np.sqrt(np.sum((sorted_approx[0] - ref_sorted[0]) ** 2))
        d_l = np.sqrt(np.sum((sorted_approx[-1] - ref_sorted[-1]) ** 2))
    else:
        d_f = 0.0
        d_l = 0.0
    
    spread_val = (d_f + d_l + np.sum(np.abs(d - d_mean))) / (d_f + d_l + len(d) * d_mean)
    
    return spread_val


def hypervolume(approx: np.ndarray, reference_point: np.ndarray) -> float:
    """
    Hypervolume indicator (2D exact, higher dimensions Monte Carlo).
    
    Measures the volume of objective space dominated by the approximated front
    and bounded by the reference point.
    
    Higher is better. HV > 0.8 * HV_ref indicates good performance.
    
    Args:
        approx: (N, M) approximated Pareto front
        reference_point: (M,) reference point (worst-case, must dominate nothing)
    
    Returns:
        Hypervolume value (float)
    """
    if len(approx) == 0:
        return 0.0
    
    M = approx.shape[1]
    
    # Filter points that are within the reference point
    valid = np.all(approx < reference_point, axis=1)
    pts = approx[valid]
    
    if len(pts) == 0:
        return 0.0
    
    if M == 2:
        return _hypervolume_2d_exact(pts, reference_point)
    elif M == 3:
        return _hypervolume_3d_exact(pts, reference_point)
    else:
        return _hypervolume_mc(pts, reference_point)


def _hypervolume_2d_exact(pts: np.ndarray, ref: np.ndarray) -> float:
    """Exact 2D hypervolume by sorting."""
    sorted_idx = np.argsort(pts[:, 0])
    pts = pts[sorted_idx]
    
    hv = 0.0
    prev_y = ref[1]
    for i in range(len(pts)):
        width = ref[0] - pts[i, 0]
        height = prev_y - pts[i, 1]
        if height > 0:
            hv += width * height
        prev_y = min(prev_y, pts[i, 1])
    
    return hv


def _hypervolume_2d_fast(pts: np.ndarray, ref: np.ndarray) -> float:
    """Vectorised exact 2D hypervolume (O(n log n), same value as the loop form).

    ``HV = sum_i (x_{i+1} - x_i) * (ref_y - min(y_0..y_i))`` for points sorted by
    x ascending (``x_{n} = ref_x``), which is the staircase area under the front.
    """
    if len(pts) == 0:
        return 0.0
    x = pts[:, 0]
    y = pts[:, 1]
    order = np.argsort(x, kind='stable')
    x = x[order]
    y = y[order]
    prefix_min = np.minimum.accumulate(y)
    heights = ref[1] - prefix_min
    heights = np.maximum(heights, 0.0)
    x_next = np.append(x[1:], ref[0])
    widths = np.maximum(x_next - x, 0.0)
    return float(np.sum(widths * heights))


def _hypervolume_3d_exact(pts: np.ndarray, ref: np.ndarray) -> float:
    """Exact 3D hypervolume by sweep over the third coordinate (z-slices).

    The volume is ``sum_k (z_k - z_{k-1}) * HV2D({p : p_z <= z_{k-1}})`` plus a
    final slice up to ``ref[2]``.  Only points *strictly below* the slice level
    dominate that slice: including the slice point itself (``z == z_k``) has
    measure zero in z and biases the volume upwards (fixed here — the previous
    version used ``sorted_idx[:k+1]`` and overestimated by 2-6 % on random
    fronts, see tests/multiobjective/test_hv_3d_accuracy.py).
    """
    order = np.argsort(pts[:, 2], kind='stable')
    sp = pts[order]
    hv = 0.0
    prev_z = 0.0
    for k in range(len(sp)):
        dz = sp[k, 2] - prev_z
        if dz > 0 and k > 0:
            hv += _hypervolume_2d_fast(sp[:k, :2], ref[:2]) * dz
        prev_z = sp[k, 2]
    if prev_z < ref[2]:
        hv += _hypervolume_2d_fast(sp[:, :2], ref[:2]) * (ref[2] - prev_z)
    return hv


def _hypervolume_mc(pts: np.ndarray, ref: np.ndarray, n_samples: int = 10000) -> float:
    """Monte Carlo hypervolume estimation for M > 3."""
    M = pts.shape[1]
    samples = np.random.uniform(0, 1, (n_samples, M)) * ref
    dominated = np.zeros(n_samples, dtype=bool)
    
    for i in range(pts.shape[0]):
        is_dominated = np.all(samples >= pts[i], axis=1)
        dominated |= is_dominated
    
    volume = np.prod(ref)
    return volume * np.mean(dominated)


def compute_all_metrics(approx: np.ndarray,
                        reference: Optional[np.ndarray] = None,
                        reference_point: Optional[np.ndarray] = None) -> dict:
    """
    Compute all quality metrics for an approximated Pareto front.
    
    Args:
        approx: (N, M) approximated Pareto front
        reference: (K, M) true Pareto front (for GD, IGD, Spread)
        reference_point: (M,) reference point (for Hypervolume)
    
    Returns:
        Dictionary with metric values
    """
    metrics = {}
    
    if reference is not None:
        metrics['gd'] = generational_distance(approx, reference)
        metrics['igd'] = inverted_generational_distance(approx, reference)
        if approx.shape[1] == 2:
            metrics['spread'] = spread(approx, reference)
    
    if reference_point is not None:
        metrics['hypervolume'] = hypervolume(approx, reference_point)
    
    return metrics


def spacing(approx: np.ndarray) -> float:
    """Schott's spacing metric (distribution uniformity of an approximated front).

    ``S = sqrt( 1/(n-1) * sum_i (d_mean - d_i)^2 )`` with ``d_i`` the distance from
    solution *i* to its nearest neighbour in objective space (Schott 1995;
    Deb 2001, eq. 5.5).  ``S = 0`` is a perfectly equidistant front; the value is
    reported in the units of the objective vector, so normalise the objectives
    (``spacing(normalise(P))``) when comparing fronts of different scale.

    Args:
        approx: (N, M) approximated Pareto front

    Returns:
        Spacing value (float, ``0`` for n < 2).
    """
    pts = np.asarray(approx, dtype=float)
    n = len(pts)
    if n < 2:
        return 0.0
    d = np.full(n, np.inf)
    for i in range(n):
        diff = np.delete(pts, i, axis=0) - pts[i]
        d[i] = np.min(np.sqrt(np.sum(diff ** 2, axis=1)))
    d_mean = float(np.mean(d))
    return float(np.sqrt(np.sum((d_mean - d) ** 2) / (n - 1)))


def normalize_front(front: np.ndarray,
                    ideal: Optional[np.ndarray] = None,
                    nadir: Optional[np.ndarray] = None
                    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Min-max normalise an objective matrix to the unit box (per-column)."""
    f = np.asarray(front, dtype=float)
    lo = np.min(f, axis=0) if ideal is None else np.asarray(ideal, dtype=float)
    hi = np.max(f, axis=0) if nadir is None else np.asarray(nadir, dtype=float)
    span = hi - lo
    span[span < 1e-12] = 1.0
    return (f - lo) / span, lo, hi


def hausdorff_distance(approx: np.ndarray,
                       reference: np.ndarray,
                       normalize: bool = True) -> Dict[str, float]:
    """Symmetric Hausdorff distance between approximated and reference fronts.

    ``H(A, R) = max( sup_a inf_r ||a - r||, sup_r inf_a ||a - r|| )``

    With ``normalize=True`` both fronts are first min-max normalised over their
    union (``front / front_extent``), so the value is a *relative* distance with
    respect to the objective-space extent of the reference front — directly
    comparable with a "< x% of front extent" acceptance criterion.  The relative
    figure is also returned explicitly as ``relative`` (``H / diag`` where
    ``diag`` is the diagonal length of the union bounding box).

    Args:
        approx: (N, M) approximated Pareto front
        reference: (K, M) reference/analytical Pareto front
        normalize: scale to the unit box of the union of both fronts

    Returns:
        dict with ``hausdorff`` (absolute), ``relative`` (H / bbox diagonal),
        ``directed_approx_to_ref``, ``directed_ref_to_approx``, ``extent``.
    """
    a = np.atleast_2d(np.asarray(approx, dtype=float))
    r = np.atleast_2d(np.asarray(reference, dtype=float))
    if len(a) == 0 or len(r) == 0:
        return {'hausdorff': float('inf'), 'relative': float('inf'),
                'directed_approx_to_ref': float('inf'),
                'directed_ref_to_approx': float('inf'), 'extent': float('inf')}

    union = np.vstack([a, r])
    lo = np.min(union, axis=0)
    hi = np.max(union, axis=0)
    if normalize:
        span = hi - lo
        span[span < 1e-12] = 1.0
        a = (a - lo) / span
        r = (r - lo) / span
        lo, hi = np.zeros_like(lo), np.ones_like(hi)

    extent = float(np.sqrt(np.sum((hi - lo) ** 2)))

    def _directed(src: np.ndarray, dst: np.ndarray) -> float:
        best = 0.0
        for p in src:
            d = float(np.min(np.sqrt(np.sum((dst - p) ** 2, axis=1))))
            if d > best:
                best = d
        return best

    d_ar = _directed(a, r)
    d_ra = _directed(r, a)
    h = max(d_ar, d_ra)
    return {'hausdorff': float(h),
            'relative': float(h / extent) if extent > 0 else float('inf'),
            'directed_approx_to_ref': float(d_ar),
            'directed_ref_to_approx': float(d_ra),
            'extent': extent}


def hypervolume_convergence(history_hv, last_k: int = 20) -> Dict[str, float]:
    """Relative hypervolume change over the final ``last_k`` generations.

    ``delta = |HV[-1] - HV[-1-last_k]| / HV[-1]`` — the "HV has stabilised"
    criterion of the acceptance checklist (e.g. ``delta < 0.01``).

    Args:
        history_hv: per-generation hypervolume sequence (ascending generation)
        last_k: number of generations spanned by the comparison window

    Returns:
        dict with ``hv_final``, ``hv_window_start``, ``delta``, ``window``,
        ``finite`` (False when the sequence is shorter than ``last_k + 1``).
    """
    hv = np.asarray([float(v) for v in history_hv], dtype=float)
    if len(hv) < 2:
        return {'hv_final': float(hv[-1]) if len(hv) else 0.0,
                'hv_window_start': float('nan'), 'delta': float('inf'),
                'window': 0, 'finite': False}
    k = min(last_k, len(hv) - 1)
    hv_end = float(hv[-1])
    hv_start = float(hv[-1 - k])
    delta = abs(hv_end - hv_start) / hv_end if hv_end > 0 else float('inf')
    return {'hv_final': hv_end, 'hv_window_start': hv_start,
            'delta': float(delta), 'window': int(k),
            'finite': bool(np.isfinite(delta) and np.isfinite(hv_end))}
