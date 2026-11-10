"""Unit tests for the generic multi-objective metrics / constraint machinery.

Domain-free: synthetic fronts only (no ship semantics), so this file can live in
the shared ``tests/`` tree.
"""
import numpy as np
import pytest

from optimizer.multiobjective.metrics import (
    _hypervolume_2d_exact,
    _hypervolume_2d_fast,
    _hypervolume_mc,
    hausdorff_distance,
    hypervolume,
    hypervolume_convergence,
    normalize_front,
    spacing,
)
from optimizer.multiobjective.nsga2 import (
    NSGA2,
    fast_constrained_non_dominated_sort,
    fast_non_dominated_sort,
)


def test_hausdorff_zero_for_identical_fronts():
    front = np.array([[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])
    hd = hausdorff_distance(front, front)
    assert hd["hausdorff"] == pytest.approx(0.0, abs=1e-12)
    assert hd["relative"] == pytest.approx(0.0, abs=1e-12)


def test_hausdorff_known_offset():
    ref = np.array([[0.0, 1.0], [1.0, 0.0]])
    shifted = ref + np.array([0.0, 0.1])
    hd = hausdorff_distance(shifted, ref, normalize=False)
    # each point is 0.1 away from its counterpart -> symmetric Hausdorff = 0.1
    assert hd["hausdorff"] == pytest.approx(0.1, rel=1e-9)
    assert hd["directed_ref_to_approx"] == pytest.approx(0.1, rel=1e-9)


def test_hausdorff_relative_is_normalised_by_extent():
    ref = np.array([[0.0, 1.0], [1.0, 0.0]])
    approx = ref * 0.5
    hd_norm = hausdorff_distance(approx, ref, normalize=True)
    hd_raw = hausdorff_distance(approx, ref, normalize=False)
    assert hd_norm["hausdorff"] == pytest.approx(hd_raw["hausdorff"], rel=0.5)
    assert hd_norm["relative"] < 0.5


def test_spacing_zero_for_equidistant_front():
    x = np.linspace(0.0, 1.0, 11)
    front = np.column_stack([x, 1.0 - x])       # uniform straight front
    assert spacing(front) == pytest.approx(0.0, abs=1e-12)


def test_spacing_detects_clustering():
    uniform = np.column_stack([np.linspace(0, 1, 21), np.linspace(1, 0, 21)])
    clustered = uniform.copy()
    clustered[:, 0] = clustered[:, 0] ** 3      # squeeze towards one end
    assert spacing(clustered) > spacing(uniform)
    assert spacing(clustered) > 0.0


def test_hv_2d_fast_matches_exact_loop():
    rng = np.random.RandomState(11)
    for _ in range(25):
        pts = rng.rand(rng.randint(1, 40), 2)
        ref = np.array([1.1, 1.2])
        assert _hypervolume_2d_fast(pts, ref) == pytest.approx(
            _hypervolume_2d_exact(pts, ref), rel=1e-12, abs=1e-12)


def test_hypervolume_3d_exact_matches_monte_carlo():
    """Regression test for the fixed 3-D sweep (old version overestimated 2-6 %)."""
    rng = np.random.RandomState(5)
    pts = rng.rand(25, 3) * 0.9
    ref = np.ones(3)
    exact = hypervolume(pts, ref)
    np.random.seed(3)
    mc = _hypervolume_mc(pts, ref, n_samples=400000)
    assert abs(exact - mc) / mc < 0.005


def test_hypervolume_convergence_window():
    hv = [1.0, 1.5, 1.9, 2.0, 2.005]
    conv = hypervolume_convergence(hv, last_k=2)
    assert conv["window"] == 2
    assert conv["delta"] == pytest.approx(abs(2.005 - 1.9) / 2.005, rel=1e-12)
    assert conv["finite"] is True
    assert hypervolume_convergence([1.0], last_k=5)["finite"] is False


def test_normalize_front_maps_to_unit_box():
    front = np.array([[1.0, 10.0], [3.0, 20.0], [2.0, 15.0]])
    norm, lo, hi = normalize_front(front)
    assert norm.min() == pytest.approx(0.0) and norm.max() == pytest.approx(1.0)
    assert np.allclose(lo, [1.0, 10.0]) and np.allclose(hi, [3.0, 20.0])


def test_constrained_sort_feasibility_rule():
    objs = np.array([[0.1, 0.1],    # infeasible but "better" objectives
                     [0.5, 0.5],    # feasible, worse objectives
                     [0.6, 0.4]])   # feasible
    violations = np.array([0.2, 0.0, 0.0])
    fronts = fast_constrained_non_dominated_sort(objs, violations)
    assert 1 in fronts[0] and 2 in fronts[0]
    assert fronts[0] == [1, 2]
    assert fronts[1] == [0]         # feasible points always dominate infeasible


def test_constrained_sort_orders_by_violation():
    objs = np.array([[0.3, 0.3], [0.2, 0.2], [0.9, 0.9]])
    violations = np.array([0.5, 0.1, 0.0])
    fronts = fast_constrained_non_dominated_sort(objs, violations)
    assert fronts[0] == [2]         # feasible
    assert fronts[1] == [1]         # smaller violation
    assert fronts[2] == [0]         # largest violation


def test_unconstrained_sort_unchanged_by_wrapper():
    objs = np.array([[1.0, 4.0], [2.0, 3.0], [4.0, 2.5], [3.5, 3.5]])
    assert fast_constrained_non_dominated_sort(objs, None) == \
        fast_non_dominated_sort(objs)


def test_nsga2_respects_constraints():
    """Every reported front member must satisfy the constraints it was given."""

    def obj(x):
        return np.array([x[0] ** 2, (x[0] - 1.0) ** 2])

    def cons(x):
        return np.array([0.5 - x[0]])          # x[0] >= 0.5 required

    opt = NSGA2(n_var=1, n_obj=2, pop_size=20, n_gen=15, seed=3)
    res = opt.optimize(obj, np.array([0.0]), np.array([2.0]), constraint_fn=cons)
    assert np.all(np.asarray(res["pareto_set"]) >= 0.5 - 1e-9)
    assert res["n_feasible"] == 20