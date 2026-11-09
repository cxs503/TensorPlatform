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
from typing import Optional


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


def _hypervolume_3d_exact(pts: np.ndarray, ref: np.ndarray) -> float:
    """Exact 3D hypervolume using sweep-plane algorithm."""
    sorted_idx = np.argsort(pts[:, 2])
    hv = 0.0
    prev_z = 0.0
    
    for k in range(len(sorted_idx)):
        idx = sorted_idx[k]
        if np.any(pts[idx] >= ref):
            continue
        
        dz = pts[idx, 2] - prev_z
        if dz > 0:
            # Compute 2D hypervolume of the slice at this z-level
            slice_points = pts[sorted_idx[:k + 1]]
            mask = slice_points[:, 2] <= pts[idx, 2]
            if np.any(mask):
                sp = slice_points[mask]
                hv_2d = _hypervolume_2d_exact(sp[:, :2], ref[:2])
                hv += hv_2d * dz
        
        prev_z = pts[idx, 2]
    
    # Add final slice from last z to ref[2]
    if prev_z < ref[2]:
        hv_2d = _hypervolume_2d_exact(pts[:, :2], ref[:2])
        hv += hv_2d * (ref[2] - prev_z)
    
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
