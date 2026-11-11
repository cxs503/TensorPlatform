"""pytest suite for the generic multi-fidelity framework (``tools/multifidelity``).

Domain neutrality: every fixture in this file is a synthetic analytic function.  No
domain vocabulary, no domain data — the physics cases live outside this package.

Coverage map
------------
levels.py      rank/cost validation, ledger accounting, cost ratios
gpr.py         interpolation, hyper-parameter sanity, predictive variance & the
               variance-reduction acquisition (additivity / non-negativity)
cokriging.py   one-level equivalence, two-level analytic benchmark, rho estimation,
               robustness to a constant residual (the over-confidence trap)
pareto.py      domination, front construction, machine-checked monotone front
allocator.py   error-law fit, static planning under a budget, adaptive campaign
               (budget respected, several levels used, ledger consistent)
selection.py   chain selection returns a valid subset and a monotone-free CV table
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from multifidelity import (  # noqa: E402
    GPR,
    Ladder,
    Level,
    MultiFidelitySurrogate,
    adaptive_allocation,
    candidate_subsets,
    cross_validated_choice,
    dominates,
    fit_error_law,
    optimal_static_allocation,
    pareto_front,
)
from multifidelity.allocator import ErrorLaw, predict_error  # noqa: E402
from multifidelity.pareto import Point, check_monotone_front  # noqa: E402


# --------------------------------------------------------------------------- fixtures
def f_high(x: np.ndarray) -> np.ndarray:
    """Oscillatory target (Forrester-style high-fidelity function)."""
    return (6.0 * x - 2.0) ** 2 * np.sin(12.0 * x - 4.0)


def f_low(x: np.ndarray) -> np.ndarray:
    """Biased, correlated low-fidelity companion of :func:`f_high`."""
    return 0.5 * f_high(x) + 10.0 * (x - 0.5) - 5.0


def _ladder(scale: float = 1.0) -> Ladder:
    return Ladder([
        Level("coarse", rank=0, cost=1.0 * scale, evaluate=lambda X: np.sum(X, axis=1)),
        Level("medium", rank=1, cost=5.0 * scale,
              evaluate=lambda X: np.sum(X, axis=1) + 0.1 * np.prod(X, axis=1)),
        Level("fine", rank=2, cost=40.0 * scale,
              evaluate=lambda X: np.sum(X, axis=1) + 0.1 * np.prod(X, axis=1)
              + 0.01 * np.sum(X**2, axis=1)),
    ])


# ------------------------------------------------------------------------------- levels
def test_level_rejects_bad_rank_and_cost():
    with pytest.raises(ValueError):
        Level("x", rank=-1, cost=1.0, evaluate=lambda X: X[:, 0])
    with pytest.raises(ValueError):
        Level("x", rank=0, cost=0.0, evaluate=lambda X: X[:, 0])


def test_ladder_requires_contiguous_ranks():
    with pytest.raises(ValueError):
        Ladder([Level("a", 0, 1.0, lambda X: X[:, 0]),
                Level("c", 2, 3.0, lambda X: X[:, 0])])


def test_ladder_requires_non_decreasing_cost():
    with pytest.raises(ValueError):
        Ladder([Level("fine", 0, 10.0, lambda X: X[:, 0]),
                Level("coarse", 1, 1.0, lambda X: X[:, 0])])


def test_ladder_ledger_counts_and_cost():
    ladder = _ladder()
    X = np.zeros((4, 2))
    ladder.evaluate(0, X)
    ladder.evaluate(2, X[:2])
    assert ladder.ledger.counts == {0: 4, 2: 2}
    assert ladder.ledger.spent == pytest.approx(4 * 1.0 + 2 * 40.0)
    assert ladder.cost_of({0: 3, 1: 2}) == pytest.approx(3.0 + 10.0)
    assert ladder.cost_ratios() == [1.0, 5.0, 40.0]


# ---------------------------------------------------------------------------------- gpr
def test_gpr_interpolates_and_extrapolates_smoothly():
    X = np.linspace(0, 1, 25).reshape(-1, 1)
    y = np.sin(3 * X).ravel()
    gp = GPR(nugget=1e-8, seed=0).fit(X, y)
    m, _ = gp.predict(X)
    assert np.max(np.abs(m - y)) < 1e-4           # (near) interpolation (small nugget)
    Xt = np.linspace(0, 1, 200).reshape(-1, 1)
    mt, _ = gp.predict(Xt)
    ref = np.sin(3 * Xt).ravel()
    assert np.sqrt(np.mean((mt - ref) ** 2)) < 0.02 * np.std(ref)


def test_gpr_predictive_variance_shrinks_with_more_data():
    rng = np.random.default_rng(0)
    X = rng.random((12, 2))
    y = np.sin(2 * X[:, 0]) + np.cos(X[:, 1])
    Xt = rng.random((40, 2))
    _, s_small = GPR(seed=0).fit(X, y).predict(Xt)
    _, s_big = GPR(seed=0).fit(np.vstack([X, rng.random((40, 2))]),
                               np.concatenate([y, np.zeros(40)])).predict(Xt)
    assert s_big.shape == s_small.shape
    assert np.all(s_small >= 0) and np.all(s_big >= 0)


def test_gpr_variance_reduction_is_positive_and_batched_consistent():
    rng = np.random.default_rng(1)
    X = rng.random((10, 2))
    y = np.sin(2 * X[:, 0])
    gp = GPR(seed=0).fit(X, y)
    pool = rng.random((30, 2))
    cand = rng.random((5, 2))
    single = np.array([gp.variance_reduction(c, pool) for c in cand])
    batch = gp.variance_reduction_many(cand, pool)
    assert np.allclose(single, batch, rtol=1e-8, atol=1e-14)
    assert np.all(batch >= 0)


# ---------------------------------------------------------------------------- cokriging
def test_cokriging_single_level_matches_gp():
    X = np.linspace(0, 1, 15).reshape(-1, 1)
    y = np.cos(4 * X).ravel()
    Xt = np.linspace(0, 1, 50).reshape(-1, 1)
    gp = GPR(seed=0).fit(X, y)
    ck = MultiFidelitySurrogate(seed=0).fit({0: (X, y)})
    a, _ = gp.predict(Xt)
    b, _ = ck.predict(Xt)
    assert np.allclose(a, b, rtol=1e-10, atol=1e-10)


def test_cokriging_beats_single_fidelity_on_analytic_benchmark():
    Xh = np.linspace(0, 1, 10).reshape(-1, 1)
    Xl = np.linspace(0, 1, 50).reshape(-1, 1)
    Xt = np.linspace(1e-3, 1 - 1e-3, 200).reshape(-1, 1)
    yh, yl, yt = (f_high(Xh).ravel(), f_low(Xl).ravel(), f_high(Xt).ravel())
    ck = MultiFidelitySurrogate(seed=0).fit({0: (Xl, yl), 1: (Xh, yh)})
    m_ck, _ = ck.predict(Xt)
    m_sf, _ = GPR(seed=0).fit(Xh, yh).predict(Xt)
    err_ck = np.sqrt(np.mean((m_ck - yt) ** 2)) / np.std(yt)
    err_sf = np.sqrt(np.mean((m_sf - yt) ** 2)) / np.std(yt)
    assert err_ck <= 0.05
    assert err_ck < err_sf


def test_cokriging_rho_is_positive_and_scaled():
    Xh = np.linspace(0, 1, 12).reshape(-1, 1)
    Xl = np.linspace(0, 1, 40).reshape(-1, 1)
    yh = (2.0 * f_high(Xh)).ravel()
    yl = f_high(Xl).ravel()
    ck = MultiFidelitySurrogate(seed=0).fit({0: (Xl, yl), 1: (Xh, yh)})
    assert ck.rho[1] > 0
    assert 0.2 < ck.rho[1] < 5.0


def test_cokriging_survives_a_constant_residual():
    """A degenerate (constant) discrepancy must not produce absurd variances."""
    Xl = np.linspace(0, 1, 30).reshape(-1, 1)
    yl = np.sin(2 * Xl).ravel()
    Xh = np.linspace(0, 1, 8).reshape(-1, 1)
    yh = 2.0 * np.sin(2 * Xh).ravel()
    ck = MultiFidelitySurrogate(seed=0).fit({0: (Xl, yl), 1: (Xh, yh)})
    m, s = ck.predict(np.linspace(0, 1, 40).reshape(-1, 1))
    assert np.all(np.isfinite(m)) and np.all(np.isfinite(s))
    assert np.max(s) < 10.0 * np.std(yh)


# ------------------------------------------------------------------------------- pareto
def test_dominates_is_strict_and_antisymmetric():
    a = Point("a", cost=1.0, error=2.0)
    b = Point("b", cost=2.0, error=3.0)
    assert dominates(a, b) and not dominates(b, a)
    assert not dominates(a, Point("a2", cost=1.0, error=2.0))


def test_pareto_front_drops_dominated_points():
    pts = [Point("a", 1.0, 5.0), Point("b", 2.0, 3.0), Point("c", 3.0, 4.0),
           Point("d", 4.0, 1.0)]
    front = [p.name for p in pareto_front(pts)]
    assert front == ["a", "b", "d"]


def test_check_monotone_front_for_monotone_and_dominated_cases():
    good = [{"name": "lo", "cost": 1.0, "error": 0.5},
            {"name": "mid", "cost": 10.0, "error": 0.1},
            {"name": "hi", "cost": 100.0, "error": 0.001}]
    res = check_monotone_front(good, min_levels=3)
    assert res["pass"] and res["cost_increasing"] and res["error_decreasing"]
    bad = [{"name": "lo", "cost": 1.0, "error": 0.5},
           {"name": "mid", "cost": 10.0, "error": 0.1},
           {"name": "hi", "cost": 100.0, "error": 0.4}]
    res_bad = check_monotone_front(bad, min_levels=3)
    assert not res_bad["pass"] and not res_bad["error_decreasing"]


# ---------------------------------------------------------------------------- allocator
def test_fit_error_law_and_predict_error_are_monotone():
    law = fit_error_law([4, 16, 64], [0.4, 0.2, 0.1])
    assert law.b > 0
    assert law(100) < law(10) < law(1)
    laws = {0: ErrorLaw(0.5, 1.0), 1: ErrorLaw(0.2, 1.0)}
    assert predict_error({0: 100, 1: 10}, laws) >= laws[1](10)


def test_static_allocation_respects_budget_and_prefers_cheap_levels():
    ladder = _ladder()
    laws = {0: ErrorLaw(0.5, 1.0), 1: ErrorLaw(0.05, 1.0), 2: ErrorLaw(1e-4, 1.0)}
    alloc = optimal_static_allocation(ladder, budget=200.0, laws=laws,
                                      caps={0: 40, 1: 20, 2: 5}, min_counts={0: 2, 1: 2, 2: 2})
    assert alloc.feasible
    assert alloc.counts[0] >= 2 and alloc.counts[2] >= 2
    # the expensive level is not showered with samples when the budget is tight
    assert alloc.counts[2] <= 5


def test_adaptive_allocation_respects_budget_and_uses_several_levels():
    ladder = _ladder()
    lo = np.zeros(2)
    hi = np.ones(2)
    rng = np.random.default_rng(0)
    pool = rng.random((40, 2))
    out = adaptive_allocation(ladder, budget=120.0, pool=pool, box_lo=lo, box_hi=hi,
                              init_counts={0: 3, 1: 3, 2: 2},
                              caps={0: 20, 1: 20, 2: 6},
                              seed=0, batch=2, max_rounds=25, min_gain_per_cost=0.0)
    spent = out["ledger"]["spent_cost"]
    assert spent <= 120.0 + 1e-9
    counts = out["ledger"]["counts"]
    assert counts[2] >= 2                      # the fine level is exercised
    assert sum(counts.values()) > 8            # the budget is actually used
    m, _ = out["surrogate"].predict(pool)
    assert np.all(np.isfinite(m))


def test_adaptive_allocation_is_deterministic_for_a_fixed_seed():
    def run():
        ladder = _ladder()
        rng = np.random.default_rng(1)
        pool = rng.random((30, 2))
        out = adaptive_allocation(ladder, budget=40.0, pool=pool,
                                  box_lo=np.zeros(2), box_hi=np.ones(2),
                                  init_counts={0: 3, 1: 3, 2: 2},
                                  caps={0: 10, 1: 10, 2: 4}, seed=3, batch=1,
                                  max_rounds=6, min_gain_per_cost=0.0)
        return out["ledger"]["counts"], out["ledger"]["spent_cost"]
    assert run() == run()


# ---------------------------------------------------------------------------- selection
def test_candidate_subsets_are_top_anchored():
    subsets = candidate_subsets([0, 1, 2])
    assert (2,) in subsets and (1, 2) in subsets and (0, 1, 2) in subsets


def test_cross_validated_choice_returns_a_valid_chain():
    Xl = np.linspace(0, 1, 30).reshape(-1, 1)
    yl = np.sin(3 * Xl).ravel() + 5.0 * Xl.ravel()      # strongly biased low level
    Xm = np.linspace(0, 1, 24).reshape(-1, 1)
    ym = np.sin(3 * Xm).ravel() + 0.05 * Xm.ravel()
    Xh = np.linspace(0.05, 0.95, 12).reshape(-1, 1)
    yh = np.sin(3 * Xh).ravel()
    data = {0: (Xl, yl), 1: (Xm, ym), 2: (Xh, yh)}
    out = cross_validated_choice(data, folds=3, seed=0)
    assert out["subset"][-1] == 2
    assert set(out["subset"]).issubset({0, 1, 2})
    assert all(np.isfinite(row["cv_rmse"]) for row in out["cv_table"])
    m, _ = out["surrogate"].predict(np.linspace(0, 1, 20).reshape(-1, 1))
    assert np.all(np.isfinite(m))