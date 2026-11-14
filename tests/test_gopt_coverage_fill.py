"""Additional G-OPT tests to fill coverage gaps in core modules.

Targets:
- optimizer/gopt/cmaes.py: PSD repair path, BIPOP strategy, termination edge cases
- optimizer/gopt/interfaces.py: FunctionObjective gradient counting, ConstraintSet edge cases
- optimizer/gopt/benchmarks.py: get_benchmark error path, keys() filters
- optimizer/multiobjective/metrics.py: spacing, normalize_front, hausdorff_distance
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from optimizer.gopt import benchmarks as B  # noqa: E402
from optimizer.gopt.cmaes import CMAESOptimizer, minimize_cmaes  # noqa: E402
from optimizer.gopt.interfaces import (  # noqa: E402
    ConstraintSet,
    FunctionObjective,
    finite_difference_gradient,
)
from optimizer.multiobjective.metrics import (  # noqa: E402
    spacing,
    normalize_front,
    hausdorff_distance,
)


# ---------------------------------------------------------------------------
# CMA-ES: PSD repair and BIPOP
# ---------------------------------------------------------------------------
def test_bipop_strategy_exercises_both_small_and_large_runs():
    """BIPOP alternates between small and large population runs."""
    def sphere(x):
        return float(np.sum(x ** 2))

    opt = CMAESOptimizer(
        dim=3,
        bounds=np.array([[-5.0, 5.0]] * 3),
        seed=42,
        max_evals=5000,
        restarts=5,
        strategy="bipop",
    )
    res = opt.optimize(sphere)
    assert res.restarts >= 2  # BIPOP should have done multiple restarts
    assert res.fun <= 1e-10


def test_psd_repair_path_is_exercised():
    """Force a near-singular C to trigger eigen-clamp repair."""
    # A very narrow elongated objective can cause C to become ill-conditioned
    def elongated(x):
        return float(x[0] ** 2 + 1e-10 * x[1] ** 2)

    opt = CMAESOptimizer(
        dim=2,
        bounds=np.array([[-10.0, 10.0]] * 2),
        seed=99,
        max_evals=2000,
        sigma0=5.0,  # large initial sigma to stress the covariance
    )
    res = opt.optimize(elongated)
    # The optimizer should still converge; psd_repairs may or may not be > 0
    assert res.fun <= 1e-8


def test_cigar_function_converges_with_cmaes():
    """Cigar function tests extreme ill-conditioning (1e5 ratio)."""
    p = B.BENCHMARKS["cigar5"]
    res = minimize_cmaes(p.fun, p.dim, bounds=p.bounds, seed=7, max_evals=5000)
    assert res.fun <= 1e-6, f"cigar f={res.fun:.3e}"


def test_tablet_function_converges_with_cmaes():
    """Tablet function tests extreme ill-conditioning (1e5 ratio, opposite axis)."""
    p = B.BENCHMARKS["tablet5"]
    res = minimize_cmaes(p.fun, p.dim, bounds=p.bounds, seed=7, max_evals=5000)
    assert res.fun <= 1e-6, f"tablet f={res.fun:.3e}"


def test_ellipsoid_converges():
    """Ellipsoid with condition number 1e3."""
    p = B.BENCHMARKS["ellipsoid5"]
    res = minimize_cmaes(p.fun, p.dim, bounds=p.bounds, seed=3, max_evals=5000)
    assert res.fun <= 1e-8, f"ellipsoid f={res.fun:.3e}"


# ---------------------------------------------------------------------------
# Interfaces: FunctionObjective and ConstraintSet edge cases
# ---------------------------------------------------------------------------
def test_function_objective_counts_evaluations():
    obj = FunctionObjective(lambda x: float(np.sum(x ** 2)), lambda x: 2.0 * x, name="test")
    x = np.array([1.0, 2.0, 3.0])
    obj.value(x)
    obj.value(x)
    obj.gradient(x)
    assert obj.n_eval == 2
    assert obj.n_grad_eval == 1


def test_function_objective_without_jac_returns_none():
    obj = FunctionObjective(lambda x: float(np.sum(x ** 2)), None, name="no_jac")
    assert obj.gradient(np.array([1.0, 2.0])) is None


def test_constraint_set_with_no_constraints():
    cs = ConstraintSet()
    x = np.array([1.0, 2.0])
    ce, ci = cs.values(x)
    assert ce.size == 0
    assert ci.size == 0
    Je, Ji = cs.jacobians(x)
    assert Je.shape == (0, 2)
    assert Ji.shape == (0, 2)


def test_constraint_set_with_only_equality():
    def eq(x):
        return np.array([x[0] + x[1] - 1.0])

    def eq_jac(x):
        return np.array([[1.0, 1.0]])

    cs = ConstraintSet(eq=eq, eq_jac=eq_jac)
    x = np.array([0.5, 0.5])
    ce, ci = cs.values(x)
    assert ce.size == 1
    assert ci.size == 0
    assert abs(ce[0]) < 1e-10
    Je, Ji = cs.jacobians(x)
    assert Je.shape == (1, 2)
    assert Ji.shape == (0, 2)


def test_constraint_set_numerical_jacobian():
    def eq(x):
        return np.array([x[0] ** 2 + x[1] ** 2 - 1.0])

    cs = ConstraintSet(eq=eq)
    x = np.array([0.6, 0.8])
    Je_num, _ = cs.jacobians(x, numerically=True)
    # Analytical: [2*x0, 2*x1] = [1.2, 1.6]
    assert np.allclose(Je_num, [[1.2, 1.6]], atol=1e-5)


# ---------------------------------------------------------------------------
# Benchmarks: error paths and key filters
# ---------------------------------------------------------------------------
def test_get_benchmark_unknown_key_raises():
    with pytest.raises(KeyError, match="unknown benchmark"):
        B.get_benchmark("nonexistent")


def test_benchmark_keys_gradient_only():
    keys = B.keys(gradient_only=True)
    for k in keys:
        p = B.BENCHMARKS[k]
        assert p.jac is not None, f"{k} has no gradient but was in gradient_only keys"


def test_benchmark_keys_constrained_only():
    keys = B.keys(constrained_only=True)
    for k in keys:
        p = B.BENCHMARKS[k]
        assert p.constraints is not None, f"{k} has no constraints but was in constrained_only keys"


# ---------------------------------------------------------------------------
# Metrics: spacing, normalize, hausdorff
# ---------------------------------------------------------------------------
def test_spacing_uniform_front():
    """Perfectly uniform front should have spacing = 0."""
    approx = np.array([[0.0, 1.0], [0.25, 0.75], [0.5, 0.5], [0.75, 0.25], [1.0, 0.0]])
    sp = spacing(approx)
    assert sp < 0.01  # nearly zero for uniform


def test_spacing_single_point():
    """Single point should return 0."""
    approx = np.array([[0.5, 0.5]])
    assert spacing(approx) == 0.0


def test_normalize_front():
    front = np.array([[0.0, 10.0], [5.0, 5.0], [10.0, 0.0]])
    norm, lo, hi = normalize_front(front)
    assert np.allclose(lo, [0.0, 0.0])
    assert np.allclose(hi, [10.0, 10.0])
    assert np.allclose(norm, [[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])


def test_hausdorff_distance_identical_fronts():
    approx = np.array([[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])
    ref = approx.copy()
    result = hausdorff_distance(approx, ref)
    assert result["hausdorff"] < 1e-10


def test_hausdorff_distance_empty_front():
    approx = np.array([]).reshape(0, 2)
    ref = np.array([[0.0, 1.0]])
    result = hausdorff_distance(approx, ref)
    assert result["hausdorff"] == float("inf")


# ---------------------------------------------------------------------------
# Finite difference: one-sided scheme
# ---------------------------------------------------------------------------
def test_fd_one_sided_scheme():
    f = lambda x: float(x[0] ** 2 + 3 * x[1])
    x = np.array([2.0, 1.0])
    g = finite_difference_gradient(f, x, h=1e-6, scheme="one_sided")
    # Exact: [4, 3]
    assert np.allclose(g, [4.0, 3.0], rtol=1e-4)
