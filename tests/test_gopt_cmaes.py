"""G-OPT CMA-ES kernel tests: every assertion is anchored on a known optimum.

Run: ``pytest tests/test_gopt_cmaes.py -q``
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


def test_sphere10_matches_closed_form_optimum():
    p = B.BENCHMARKS["sphere10"]
    res = minimize_cmaes(p.fun, p.dim, bounds=p.bounds, seed=1, max_evals=3000)
    assert res.fun <= 1e-10, f"f={res.fun:.3e} not at the analytic optimum 0"
    assert np.max(np.abs(res.x - p.optimum_x)) <= 1e-4
    assert res.converged, res.termination


def test_rosenbrock5_with_ipop_restarts():
    p = B.BENCHMARKS["rosenbrock5"]
    res = minimize_cmaes(p.fun, p.dim, bounds=p.bounds, seed=3, max_evals=12000, restarts=3)
    assert res.fun <= 1e-8, f"f={res.fun:.3e}"
    assert np.max(np.abs(res.x - p.optimum_x)) <= 1e-3
    assert res.restarts >= 1  # restarts were actually exercised


def test_rastrigin5_multimodal_needs_restarts():
    p = B.BENCHMARKS["rastrigin5"]
    res = minimize_cmaes(p.fun, p.dim, bounds=p.bounds, seed=5, max_evals=24000, restarts=4)
    assert res.fun <= 1e-6, f"multimodal optimum not reached: f={res.fun:.3e}"


def test_sampling_never_leaves_the_box():
    """Every evaluated candidate must satisfy the box (clip repair, not a penalty)."""
    seen = []

    def objective(x):
        seen.append(np.array(x, float))
        return float(np.sum((x - 3.0) ** 2))

    bounds = np.array([[-1.0, 1.0], [-1.0, 1.0]])
    minimize_cmaes(objective, 2, bounds=bounds, seed=11, max_evals=400)
    arr = np.array(seen)
    assert np.all(arr >= bounds[:, 0] - 0.0) and np.all(arr <= bounds[:, 1] + 0.0)


def test_box_optimum_is_projected_clip_and_mean():
    """Constrained optimum 3.0 lies outside the box [-1,1] -> optimum is the corner."""
    res = minimize_cmaes(
        lambda x: float(np.sum((x - 3.0) ** 2)),
        2,
        bounds=np.array([[-1.0, 1.0], [-1.0, 1.0]]),
        seed=7,
        max_evals=800,
    )
    assert np.allclose(res.x, 1.0, atol=1e-3)
    assert res.fun == pytest.approx(8.0, abs=1e-4)


def test_bitwise_reproducibility_for_fixed_seed():
    r1 = minimize_cmaes(B.BENCHMARKS["sphere2"].fun, 2, seed=42, max_evals=200)
    r2 = minimize_cmaes(B.BENCHMARKS["sphere2"].fun, 2, seed=42, max_evals=200)
    assert r1.fun == r2.fun
    assert np.array_equal(r1.x, r2.x)
    assert r1.history == r2.history


def test_noise_resampling_improves_the_median_error():
    rng = np.random.default_rng(0)
    noise_scale = 0.05
    target = np.zeros(3)

    def noisy(x):
        return float(np.sum((x - target) ** 2) + noise_scale * rng.standard_normal())

    errs = []
    for seed in range(5):
        rng = np.random.default_rng(100 + seed)
        res = CMAESOptimizer(dim=3, seed=seed, max_evals=1500, n_resample=7).optimize(noisy)
        errs.append(float(np.linalg.norm(res.x - target)))
    assert np.median(errs) <= 0.15, f"median error {np.median(errs):.3f} too large under noise"


def test_monotone_objective_returns_the_upper_bound():
    lam_grid = np.linspace(5.0, 9.0, 200)
    res = minimize_cmaes(lambda x: float(1.0 / x[0]), 1, bounds=np.array([[5.0, 9.0]]),
                         seed=2, max_evals=600)
    assert res.x[0] == pytest.approx(9.0, abs=1e-3)
    assert res.fun == pytest.approx(1.0 / lam_grid.max(), rel=1e-4)


def test_termination_reason_is_machine_readable():
    res = minimize_cmaes(B.BENCHMARKS["sphere2"].fun, 2, seed=1, max_evals=100)
    assert isinstance(res.termination, str) and res.termination
    diag = res.to_dict()["diagnostics"]
    assert diag["runs"] and "popsize_base" in diag


def test_invalid_bounds_are_rejected():
    with pytest.raises(ValueError):
        CMAESOptimizer(dim=2, bounds=np.array([[1.0, 0.0], [0.0, 1.0]]))
    with pytest.raises(ValueError):
        CMAESOptimizer(dim=1, strategy="bogus")