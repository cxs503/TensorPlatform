"""G-OPT SLSQP tests: analytic KKT points, scipy cross-check, QP KKT certificates.

Run: ``pytest tests/test_gopt_slsqp.py -q``
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
from optimizer.gopt.interfaces import ConstraintSet, FunctionObjective  # noqa: E402
from optimizer.gopt.slsqp import SLSQPOptimizer, minimize_slsqp, solve_qp  # noqa: E402

scipy_opt = pytest.importorskip("scipy.optimize")


def _run(key: str, x0=None):
    p = B.BENCHMARKS[key]
    start = p.optimum_x + 0.5 if x0 is None else np.asarray(x0, float)
    return p, minimize_slsqp(p.fun, start, jac=p.jac, bounds=p.bounds, constraints=p.constraints)


@pytest.mark.parametrize("key", ["sphere2", "rosenbrock2", "eq_quadratic", "box_quadratic",
                                "eq_ineq_quadratic"])
def test_closed_form_kkt_optima(key):
    p = B.BENCHMARKS[key]
    _, res = _run(key)
    assert res.success, res.message
    assert res.fun == pytest.approx(p.optimum_f, abs=p.f_tol_abs * 10 + 1e-8)
    assert np.max(np.abs(res.x - p.optimum_x)) <= p.x_tol
    assert res.constraint_violation <= 1e-8


def test_nocedal_example_17_3_reproduces_published_solution():
    """x* = (0.822876, 0.911438), f* = 1.393465 from the textbook."""
    p, res = _run("nocedal_17_3")
    assert res.success
    assert res.fun == pytest.approx(1.393465, abs=1e-5)
    assert np.allclose(res.x, [0.822876, 0.911438], atol=1e-4)


def test_derivative_free_objective_is_rejected_loudly():
    with pytest.raises(ValueError, match="requires a gradient callback"):
        SLSQPOptimizer().optimize(FunctionObjective(lambda x: float(x @ x), None), [1.0, 1.0])


@pytest.mark.parametrize("key", ["nocedal_17_3", "eq_ineq_quadratic"])
def test_scipy_cross_check(key):
    p = B.BENCHMARKS[key]
    _, ours = _run(key)
    cons = []
    if p.constraints is not None and p.constraints.ineq is not None:
        cons.append({"type": "ineq", "fun": p.constraints.ineq, "jac": p.constraints.ineq_jac})
    if p.constraints is not None and p.constraints.eq is not None:
        cons.append({"type": "eq", "fun": p.constraints.eq, "jac": p.constraints.eq_jac})
    ref = scipy_opt.minimize(p.fun, ours.x + 0.1, jac=p.jac, constraints=cons,
                             method="SLSQP", options={"ftol": 1e-12, "maxiter": 300})
    assert ref.success
    assert ours.fun == pytest.approx(float(ref.fun), rel=1e-6, abs=1e-8)


def test_scipy_cross_check_on_smooth_convex_problem():
    """Random SPD quadratic with two linear inequalities: agreement with scipy."""
    rng = np.random.default_rng(4)
    M = rng.standard_normal((4, 4))
    H = M @ M.T + np.eye(4)
    c = rng.standard_normal(4)
    A = rng.standard_normal((2, 4))
    b = np.array([0.2, -0.3])

    fun = lambda x: float(0.5 * x @ H @ x + c @ x)
    jac = lambda x: H @ x + c
    ineq = lambda x: A @ x - b
    ineq_jac = lambda x: A

    cons = ConstraintSet(ineq=ineq, ineq_jac=ineq_jac)
    ours = minimize_slsqp(fun, np.zeros(4), jac=jac, constraints=cons)
    ref = scipy_opt.minimize(fun, np.zeros(4), jac=jac, method="SLSQP",
                             constraints=[{"type": "ineq", "fun": ineq, "jac": ineq_jac}],
                             options={"ftol": 1e-12, "maxiter": 500})
    assert ref.success
    assert ours.fun == pytest.approx(float(ref.fun), rel=1e-7, abs=1e-9)
    assert np.allclose(ours.x, ref.x, atol=1e-4)


def test_bounds_are_respected():
    p = B.BENCHMARKS["box_quadratic"]
    _, res = _run("box_quadratic", x0=[0.0, 0.0])
    assert np.all(res.x >= p.bounds[:, 0] - 1e-12)
    assert np.all(res.x <= p.bounds[:, 1] + 1e-12)


def test_qp_solver_certificate_on_random_problems():
    """KKT certificate (primal feasibility + stationarity + multiplier sign)."""
    rng = np.random.default_rng(11)
    n, m_eq, m_ineq = 5, 1, 4
    worst = 0.0
    for _ in range(25):
        M = rng.standard_normal((n, n))
        Bm = M @ M.T + 0.5 * np.eye(n)
        g = rng.standard_normal(n)
        Aeq = rng.standard_normal((m_eq, n))
        beq = rng.standard_normal(m_eq)
        Aineq = rng.standard_normal((m_ineq, n))
        bineq = rng.standard_normal(m_ineq)
        qp = solve_qp(Bm, g, Aeq, beq, Aineq, bineq)
        p = qp.p
        # primal feasibility
        res_eq = np.max(np.abs(Aeq @ p - beq)) if Aeq.size else 0.0
        res_in = np.max(np.maximum(bineq - Aineq @ p, 0.0)) if Aineq.size else 0.0
        # stationarity with the reported multipliers
        grad = Bm @ p + g - Aeq.T @ qp.lam_eq - Aineq.T @ qp.lam_ineq
        worst = max(worst, res_eq, res_in, float(np.max(np.abs(grad))),
                    float(np.max(-qp.lam_ineq)) if qp.lam_ineq.size else 0.0)
        assert np.all(qp.lam_ineq >= -1e-9)
    assert worst < 1e-6, f"worst KKT residual {worst:.2e}"


def test_qp_solver_unconstrained_matches_linear_solve():
    rng = np.random.default_rng(2)
    M = rng.standard_normal((3, 3))
    Bm = M @ M.T + np.eye(3)
    g = rng.standard_normal(3)
    qp = solve_qp(Bm, g)
    assert np.allclose(qp.p, np.linalg.solve(Bm, -g), atol=1e-12)


def test_history_records_kkt_and_penalty():
    _, res = _run("nocedal_17_3")
    assert res.history
    assert {"iter", "f", "violation", "merit", "penalty"} <= set(res.history[0])
    assert res.n_jac >= 2