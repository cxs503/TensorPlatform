"""G-OPT adjoint-protocol tests: analytic adjoint vs finite difference + negative control.

Run: ``pytest tests/test_gopt_adjoint.py -q``
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
from optimizer.gopt.adjoint_reference import (  # noqa: E402
    FunctionAdjointSolver,
    LinearStateAdjointSolver,
    LinearStateSpec,
    diffusion_element_operators,
)
from optimizer.gopt.interfaces import (  # noqa: E402
    InjectedAdjointObjective,
    adjoint_consistency_check,
    finite_difference_gradient,
)
from optimizer.gopt.slsqp import SLSQPOptimizer  # noqa: E402


def _solver(n_elem: int = 6, c_value: float = 0.05):
    ops = diffusion_element_operators(n_elem)
    n = n_elem + 1
    b = np.linspace(1.0, 0.2, n)
    Q = np.zeros((n, n))
    Q[-1, -1] = 1.0
    c = np.full(n_elem, c_value)
    return LinearStateAdjointSolver(LinearStateSpec(ops, b, Q, c))


def test_adjoint_gradient_matches_finite_difference():
    sol = _solver(6)
    x = np.full(6, 0.7)
    report = adjoint_consistency_check(sol, x)
    assert report["grad_rel_err"] <= 5e-6, report["grad_rel_err"]
    assert report["taylor_order"] >= 0.9, report["taylor_order"]
    assert report["passes"] is True
    assert report["evidence_grade"] == "REAL"


def test_taylor_series_order_is_first_order():
    """(J(x+hd)-J(x))/h -> <g,d> at first order: measured slope must be ~1."""
    sol = _solver(5)
    report = adjoint_consistency_check(sol, np.full(5, 0.4), h0=1e-2, n_halvings=5)
    assert 0.85 <= report["taylor_order"] <= 1.15


def test_corrupted_gradient_is_caught():
    """Negative control: a sign-flipped gradient must fail the audit."""
    sol = _solver(6)
    x = np.full(6, 0.7)

    class Broken(type(sol)):
        def gradient(self, xx):
            return -super().gradient(xx)

    broken = Broken(sol.spec)
    report = adjoint_consistency_check(broken, x)
    assert report["passes"] is False
    assert report["evidence_grade"] == "WITHHELD"
    assert report["grad_rel_err"] > 1e-3


def test_adjoint_costs_two_solves_independent_of_dimension():
    counts = {}
    for n in (4, 12, 30):
        sol = _solver(n)
        counts[n] = sol.cost_comparison(np.full(n, 0.5))
    for n, rec in counts.items():
        assert rec["adjoint"]["primal_solves"] == 1
        assert rec["adjoint"]["adjoint_solves"] == 1
        assert rec["finite_difference_primal_solves"] == 1 + 2 * n
        assert rec["speedup_factor"] > 1.0


def test_function_adapter_is_accepted_by_the_auditor():
    p = B.BENCHMARKS["ellipsoid5"]
    solver = FunctionAdjointSolver(p.fun, p.jac, dim=p.dim, name="ellipsoid5")
    report = adjoint_consistency_check(solver, np.array([0.5, -0.4, 0.3, 0.2, -0.1]))
    assert report["passes"], report


def test_slsqp_optimises_through_the_adjoint_protocol():
    """The optimizer must consume the solver (1 primal + 1 adjoint per iteration)."""
    # c = 2 penalises large design values: the state term and the linear term
    # balance inside the box (uniform scan minimum near x = 1.4), so the KKT point
    # is interior and the bound multipliers are inactive
    sol = _solver(6, c_value=2.0)
    obj = InjectedAdjointObjective(sol)
    bounds = np.array([[0.05, 3.0]] * 6)
    res = SLSQPOptimizer(bounds=bounds, max_iter=60).optimize(obj, x0=np.full(6, 1.0))
    assert res.success, res.message
    assert sol.n_primal > 0 and sol.n_adjoint > 0
    # the converged point must be a local (here: global, convex) optimum: gradient ~ 0
    g = sol.gradient(res.x)
    free = (res.x > bounds[:, 0] + 1e-9) & (res.x < bounds[:, 1] - 1e-9)
    assert free.sum() >= 4, "expected a mostly interior KKT point, not a pure bound solution"
    assert np.max(np.abs(g[free])) < 1e-5
    # bound multipliers must have the correct sign. With x >= lo and x <= hi:
    # at the lower bound grad J = mu >= 0, at the upper bound grad J = -mu <= 0.
    at_upper = res.x > bounds[:, 1] - 1e-9
    at_lower = res.x < bounds[:, 0] + 1e-9
    assert np.all(g[at_upper] <= 1e-5) and np.all(g[at_lower] >= -1e-5)


def test_finite_difference_helper_stencils_agree():
    f = lambda x: float(np.sum(np.exp(x) + x ** 3))
    x = np.array([0.3, -0.2, 0.7])
    exact = np.exp(x) + 3 * x ** 2
    for scheme in ("central", "fivediagonal", "one_sided"):
        g = finite_difference_gradient(f, x, h=1e-5, scheme=scheme)
        assert np.allclose(g, exact, rtol=1e-5, atol=1e-7), scheme