"""Design of Experiments (DoE) framework — platform-level, zero domain semantics.

Generates experiment point arrays from parameter space + constraints,
supports: full-factorial, fractional-factorial, central-composite (response surface),
Box-Behnken, and D-optimal designs.  Outputs include D-efficiency metrics
and priority ordering for sequential execution.

All parameter names, ranges, and constraints are generic; no ship/ice/physics
terminology is embedded.  Synthetic names are used in tests.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParameterSpec:
    """A single experimental parameter."""
    name: str
    low: float
    high: float
    discrete_values: Optional[List[float]] = None  # if set, overrides low/high

    def __post_init__(self):
        if self.discrete_values is not None:
            if len(self.discrete_values) < 2:
                raise ValueError(f"Parameter {self.name}: discrete_values needs ≥2 entries")
        elif self.high <= self.low:
            raise ValueError(f"Parameter {self.name}: high ({self.high}) must be > low ({self.low})")


@dataclass(frozen=True)
class LinearConstraint:
    """A linear inequality: sum(coeff_i * x_i) <= rhs, where x_i ∈ [0,1] coded."""
    coefficients: Dict[str, float]
    rhs: float


@dataclass
class DoEResult:
    """Output of a DoE generator."""
    design_name: str
    points: np.ndarray  # shape (n_runs, n_params), coded [-1, +1]
    points_physical: np.ndarray  # shape (n_runs, n_params), in original units
    parameter_names: List[str]
    d_efficiency: float  # 0..1, D-optimal efficiency relative to full factorial
    run_order: List[int]  # permutation for execution priority
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utility: coding / decoding
# ---------------------------------------------------------------------------

def _code_value(value: float, low: float, high: float) -> float:
    """Map physical value to coded [-1, +1]."""
    return 2.0 * (value - low) / (high - low) - 1.0


def _decode_value(coded: float, low: float, high: float) -> float:
    """Map coded [-1, +1] to physical value."""
    return low + 0.5 * (coded + 1.0) * (high - low)


def _code_matrix(physical: np.ndarray, specs: List[ParameterSpec]) -> np.ndarray:
    coded = np.empty_like(physical, dtype=float)
    for j, sp in enumerate(specs):
        coded[:, j] = _code_value(physical[:, j], sp.low, sp.high)
    return coded


def _decode_matrix(coded: np.ndarray, specs: List[ParameterSpec]) -> np.ndarray:
    physical = np.empty_like(coded, dtype=float)
    for j, sp in enumerate(specs):
        physical[:, j] = _decode_value(coded[:, j], sp.low, sp.high)
    return physical


# ---------------------------------------------------------------------------
# D-efficiency computation
# ---------------------------------------------------------------------------

def _d_efficiency(design: np.ndarray, reference: np.ndarray) -> float:
    """Compute D-efficiency of *design* relative to *reference*.

    D-eff = (|X_d' X_d| / |X_r' X_r|)^(1/p)  where p = number of model terms.
    Both matrices include intercept column.
    """
    n_d, p_d = design.shape
    n_r, p_r = reference.shape
    # Build model matrices with intercept + main effects
    X_d = np.hstack([np.ones((n_d, 1)), design])
    X_r = np.hstack([np.ones((n_r, 1)), reference])
    p = X_d.shape[1]
    # Ensure square information matrices
    try:
        det_d = np.linalg.det(X_d.T @ X_d)
        det_r = np.linalg.det(X_r.T @ X_r)
    except np.linalg.LinAlgError:
        return 0.0
    if det_d <= 0 or det_r <= 0:
        return 0.0
    return (det_d / det_r) ** (1.0 / p)


def _model_matrix(coded: np.ndarray, include_interactions: bool = False,
                  include_quadratic: bool = False) -> np.ndarray:
    """Build model matrix from coded design."""
    n, p = coded.shape
    cols = [np.ones((n, 1)), coded]  # intercept + main effects
    if include_interactions and p >= 2:
        for i in range(p):
            for j in range(i + 1, p):
                cols.append((coded[:, i] * coded[:, j]).reshape(-1, 1))
    if include_quadratic:
        cols.append(coded ** 2)
    return np.hstack(cols)


# ---------------------------------------------------------------------------
# Constraint filtering
# ---------------------------------------------------------------------------

def _apply_constraints(coded: np.ndarray, specs: List[ParameterSpec],
                       constraints: Optional[List[LinearConstraint]]) -> np.ndarray:
    if not constraints:
        return coded
    keep = np.ones(coded.shape[0], dtype=bool)
    for c in constraints:
        lhs = np.zeros(coded.shape[0])
        for name, coeff in c.coefficients.items():
            idx = specs.index(next(s for s in specs if s.name == name))
            lhs += coeff * coded[:, idx]
        keep &= (lhs <= c.rhs + 1e-9)
    return coded[keep]


# ---------------------------------------------------------------------------
# Design generators
# ---------------------------------------------------------------------------

def full_factorial(specs: List[ParameterSpec],
                   constraints: Optional[List[LinearConstraint]] = None,
                   n_levels: int = 2) -> DoEResult:
    """Full factorial design (2-level or n-level per parameter)."""
    if n_levels < 2:
        raise ValueError("n_levels must be ≥ 2")
    grids = []
    for sp in specs:
        if sp.discrete_values is not None:
            grids.append(list(sp.discrete_values))
        else:
            grids.append(np.linspace(sp.low, sp.high, n_levels).tolist())
    points_phys = np.array(list(product(*grids)), dtype=float)
    points_coded = _code_matrix(points_phys, specs)
    if constraints:
        points_coded = _apply_constraints(points_coded, specs, constraints)
        points_phys = _decode_matrix(points_coded, specs)
    # D-efficiency relative to itself = 1.0
    d_eff = 1.0
    run_order = list(range(len(points_coded)))
    random.shuffle(run_order)
    return DoEResult(
        design_name=f"full_factorial_{n_levels}level",
        points=points_coded,
        points_physical=points_phys,
        parameter_names=[s.name for s in specs],
        d_efficiency=d_eff,
        run_order=run_order,
        metadata={"n_levels": n_levels, "n_runs": len(points_coded)},
    )


def fractional_factorial(specs: List[ParameterSpec],
                         resolution: int = 3,
                         constraints: Optional[List[LinearConstraint]] = None) -> DoEResult:
    """Regular fractional factorial (2^(k-p) design) using generator-based construction."""
    k = len(specs)
    if k < 2 or k < resolution:
        return full_factorial(specs, constraints, n_levels=2)
    # Find smallest full factorial that can accommodate the fraction
    # Use standard Plackett-Burman or 2^(k-p) approach
    # For simplicity, use a subset of full factorial with good projection properties
    full = full_factorial(specs, constraints, n_levels=2)
    n_full = len(full.points)
    # Target fraction: 1/2, 1/4, etc. based on resolution
    fraction = max(1, k - resolution + 1)
    target = max(2 ** (k - fraction), int(n_full * 0.5 ** fraction))
    target = min(target, n_full)
    # Select points using a simple aliasing strategy
    # Use the first `target` points from a sorted order that maximizes spread
    indices = _maximin_subset(full.points, target)
    points_coded = full.points[indices]
    points_phys = full.points_physical[indices]
    # Compute D-efficiency relative to full factorial
    d_eff = _d_efficiency(points_coded, full.points)
    run_order = list(range(len(points_coded)))
    random.shuffle(run_order)
    return DoEResult(
        design_name=f"fractional_factorial_res{resolution}",
        points=points_coded,
        points_physical=points_phys,
        parameter_names=[s.name for s in specs],
        d_efficiency=d_eff,
        run_order=run_order,
        metadata={"resolution": resolution, "n_runs": len(points_coded),
                  "fraction": f"1/{n_full // len(points_coded)}"},
    )


def central_composite(specs: List[ParameterSpec],
                      alpha: Optional[float] = None,
                      constraints: Optional[List[LinearConstraint]] = None) -> DoEResult:
    """Central Composite Design (CCD) for response surface methodology.

    Combines: 2-level factorial (or fractional) + center points + axial (star) points.
    """
    k = len(specs)
    if alpha is None:
        alpha = (2 ** k) ** 0.25  # rotatable design
    # Factorial portion
    ff = full_factorial(specs, constraints, n_levels=2)
    ff_coded = ff.points
    ff_phys = ff.points_physical
    # Center point
    center_coded = np.zeros((1, k))
    center_phys = np.array([[0.5 * (s.low + s.high) for s in specs]], dtype=float)
    # Axial (star) points: ±alpha on each axis
    axial_coded = []
    axial_phys = []
    for j in range(k):
        for sign in [-1, 1]:
            row_c = np.zeros(k)
            row_c[j] = sign * alpha
            axial_coded.append(row_c)
            row_p = [0.5 * (s.low + s.high) for s in specs]
            row_p[j] = _decode_value(sign * alpha, specs[j].low, specs[j].high)
            axial_phys.append(row_p)
    axial_coded = np.array(axial_coded, dtype=float)
    axial_phys = np.array(axial_phys, dtype=float)
    # Combine
    all_coded = np.vstack([ff_coded, center_coded, axial_coded])
    all_phys = np.vstack([ff_phys, center_phys, axial_phys])
    if constraints:
        all_coded = _apply_constraints(all_coded, specs, constraints)
        all_phys = _decode_matrix(all_coded, specs)
    # D-efficiency relative to full factorial
    d_eff = _d_efficiency(all_coded, ff.points)
    run_order = list(range(len(all_coded)))
    random.shuffle(run_order)
    return DoEResult(
        design_name="central_composite",
        points=all_coded,
        points_physical=all_phys,
        parameter_names=[s.name for s in specs],
        d_efficiency=d_eff,
        run_order=run_order,
        metadata={"alpha": alpha, "n_factorial": len(ff_coded),
                  "n_axial": len(axial_coded), "n_center": 1,
                  "n_runs": len(all_coded)},
    )


def box_behnken(specs: List[ParameterSpec],
                n_center: int = 3,
                constraints: Optional[List[LinearConstraint]] = None) -> DoEResult:
    """Box-Behnken design — efficient response surface without corner points."""
    k = len(specs)
    if k < 3:
        # Fall back to CCD for k < 3
        return central_composite(specs, constraints=constraints)
    # Generate BB points: for each pair of factors, set them at ±1, others at 0
    points_coded = []
    points_phys = []
    center_phys = [0.5 * (s.low + s.high) for s in specs]
    for i in range(k):
        for j in range(i + 1, k):
            for si in [-1, 1]:
                for sj in [-1, 1]:
                    row_c = np.zeros(k)
                    row_c[i] = si
                    row_c[j] = sj
                    points_coded.append(row_c)
                    row_p = list(center_phys)
                    row_p[i] = _decode_value(si, specs[i].low, specs[i].high)
                    row_p[j] = _decode_value(sj, specs[j].low, specs[j].high)
                    points_phys.append(row_p)
    # Add center points
    for _ in range(n_center):
        points_coded.append(np.zeros(k))
        points_phys.append(list(center_phys))
    all_coded = np.array(points_coded, dtype=float)
    all_phys = np.array(points_phys, dtype=float)
    if constraints:
        all_coded = _apply_constraints(all_coded, specs, constraints)
        all_phys = _decode_matrix(all_coded, specs)
    # Reference: full factorial for D-efficiency
    ff = full_factorial(specs, n_levels=2)
    d_eff = _d_efficiency(all_coded, ff.points)
    run_order = list(range(len(all_coded)))
    random.shuffle(run_order)
    return DoEResult(
        design_name="box_behnken",
        points=all_coded,
        points_physical=all_phys,
        parameter_names=[s.name for s in specs],
        d_efficiency=d_eff,
        run_order=run_order,
        metadata={"n_center": n_center, "n_runs": len(all_coded)},
    )


def d_optimal(specs: List[ParameterSpec],
              n_runs: int,
              candidate_pool: Optional[np.ndarray] = None,
              constraints: Optional[List[LinearConstraint]] = None,
              max_iter: int = 200,
              seed: int = 42) -> DoEResult:
    """D-optimal design via coordinate-exchange algorithm.

    Maximizes |X'X| by iteratively swapping points to improve determinant.
    """
    rng = np.random.RandomState(seed)
    k = len(specs)
    # Build candidate pool
    if candidate_pool is None:
        # Default: dense grid
        n_grid = max(3, int(n_runs ** (1.0 / k)) + 1)
        ff = full_factorial(specs, n_levels=n_grid)
        candidate_pool = ff.points
    if constraints:
        candidate_pool = _apply_constraints(candidate_pool, specs, constraints)
    n_candidates = len(candidate_pool)
    if n_candidates < n_runs:
        raise ValueError(f"Candidate pool ({n_candidates}) smaller than n_runs ({n_runs})")
    # Initialize: random subset
    indices = rng.choice(n_candidates, size=n_runs, replace=False)
    current_design = candidate_pool[indices]
    current_det = _info_determinant(current_design)
    # Coordinate exchange
    for iteration in range(max_iter):
        improved = False
        for i in range(n_runs):
            best_det = current_det
            best_idx = i
            for c in range(n_candidates):
                if c in set(indices):
                    continue
                trial = current_design.copy()
                trial[i] = candidate_pool[c]
                det = _info_determinant(trial)
                if det > best_det:
                    best_det = det
                    best_idx = c
                    improved = True
            if improved:
                old_idx = indices[i]
                indices[i] = best_idx
                current_design[i] = candidate_pool[best_idx]
                current_det = best_det
        if not improved:
            break
    # Decode to physical
    points_phys = _decode_matrix(current_design, specs)
    # D-efficiency relative to full factorial
    ff = full_factorial(specs, n_levels=2)
    d_eff = _d_efficiency(current_design, ff.points)
    run_order = list(range(n_runs))
    random.shuffle(run_order)
    return DoEResult(
        design_name="d_optimal",
        points=current_design,
        points_physical=points_phys,
        parameter_names=[s.name for s in specs],
        d_efficiency=d_eff,
        run_order=run_order,
        metadata={"n_runs": n_runs, "n_candidates": n_candidates,
                  "iterations": iteration + 1, "converged": not improved},
    )


def _info_determinant(design: np.ndarray) -> float:
    """Compute |X'X| for model with intercept + main effects."""
    n, p = design.shape
    X = np.hstack([np.ones((n, 1)), design])
    try:
        return np.linalg.det(X.T @ X)
    except np.linalg.LinAlgError:
        return 0.0


def _maximin_subset(points: np.ndarray, target: int) -> List[int]:
    """Greedy max-min distance subset selection."""
    n = len(points)
    if target >= n:
        return list(range(n))
    # Start with first point
    selected = [0]
    remaining = list(range(1, n))
    while len(selected) < target:
        best_idx = -1
        best_min_dist = -1
        for idx in remaining:
            min_dist = min(np.linalg.norm(points[idx] - points[s]) for s in selected)
            if min_dist > best_min_dist:
                best_min_dist = min_dist
                best_idx = idx
        selected.append(best_idx)
        remaining.remove(best_idx)
    return selected


# ---------------------------------------------------------------------------
# Unified generator interface
# ---------------------------------------------------------------------------

DESIGN_TYPES = {
    "full_factorial": full_factorial,
    "fractional_factorial": fractional_factorial,
    "central_composite": central_composite,
    "box_behnken": box_behnken,
    "d_optimal": d_optimal,
}


def generate_design(
    design_type: str,
    specs: List[ParameterSpec],
    constraints: Optional[List[LinearConstraint]] = None,
    **kwargs,
) -> DoEResult:
    """Unified entry point for DoE generation."""
    if design_type not in DESIGN_TYPES:
        raise ValueError(f"Unknown design type: {design_type}. Available: {list(DESIGN_TYPES.keys())}")
    return DESIGN_TYPES[design_type](specs, constraints=constraints, **kwargs)


__all__ = [
    "ParameterSpec", "LinearConstraint", "DoEResult",
    "full_factorial", "fractional_factorial", "central_composite",
    "box_behnken", "d_optimal", "generate_design", "DESIGN_TYPES",
    "_d_efficiency",
]
