"""KKT residual for a candidate solution -- engine independent.

Adapters report whatever multipliers they happen to expose (SciPy SLSQP and
``trust-constr`` do, NLopt and pyoptsparse's SNOPT do not), so a solver-agnostic
measure is needed to compare "did it really converge" across engines.  The
residual computed here is the standard first-order optimality test for

    minimise  f(x)          s.t.   g_i(x) >= 0,  h_j(x) = 0

1. *stationarity* -- ``|| grad f - A mu ||_inf`` where the columns of ``A`` are
   ``grad g_i`` (all inequalities) and ``grad h_j`` (all equalities) and
   ``mu >= 0`` for the inequality block is estimated by non-negative least
   squares.  All inequalities enter the system: inactive ones may only receive a
   multiplier at the price of violating complementarity (term 4), so the pair
   (1,4) is what actually pins the multipliers down;
2. *primal feasibility* -- ``max(max_i(-g_i), max_j|h_j|, 0)``;
3. *dual feasibility* -- ``max(0, -min mu_i)`` (non-negative least squares makes
   this identically zero; it is kept as an explicit guard against a future
   unconstrained solve);
4. *complementarity* -- ``max_i |mu_i g_i|``.

``kkt_residual`` is the maximum of the four, with (1) and (4) divided by
``max(1, ||grad f||_inf)`` so the measure is dimensionless -- comparable across
a badly scaled engineering problem and a unit-scale test function.

The finite-difference fallback uses the problem's own gradient routine, so a
problem without analytic gradients gets a KKT residual limited by FD accuracy
(~1e-6 relative), which is documented rather than hidden.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
from scipy.optimize import nnls

from .compiled import CompiledProblem


def kkt_diagnostics(
    problem: CompiledProblem,
    x,
    multipliers_ineq: Optional[np.ndarray] = None,
    active_tol: float = 1e-6,
) -> Dict[str, float]:
    """Return the four KKT components plus the aggregate ``kkt_residual``."""
    x = np.asarray(x, dtype=float)
    grad_f = np.asarray(problem.gradient(x), dtype=float)
    g = problem.ineq_values(x)
    h = problem.eq_values(x)
    jg = problem.ineq_jacobian(x)
    jh = problem.eq_jacobian(x)

    if len(g) and jg is None:
        jg = _fd_jac(lambda z: problem.ineq_values(z), x)
    if len(h) and jh is None:
        jh = _fd_jac(lambda z: problem.eq_values(z), x)

    columns = []
    n_ineq = 0 if jg is None else jg.shape[0]
    if jg is not None:
        columns.extend(jg)
        n_ineq = jg.shape[0]
    if jh is not None:
        columns.extend(jh)

    mu_full = np.zeros(n_ineq)
    mu_max = 0.0
    if columns:
        A = np.vstack(columns).T                      # (n, m)
        mu, _ = nnls(A, grad_f)                       # min ||A mu - grad f||, mu >= 0
        stationarity_vec = grad_f - A @ mu
        if n_ineq:
            mu_full = mu[:n_ineq]
            if mu_full.size:
                mu_max = float(np.max(mu_full))
    else:
        stationarity_vec = grad_f

    stationarity = float(np.max(np.abs(stationarity_vec))) if stationarity_vec.size else 0.0
    primal = 0.0
    if len(g):
        primal = max(primal, float(np.max(-g)))
    if len(h):
        primal = max(primal, float(np.max(np.abs(h))))
    primal = max(primal, 0.0)
    dual = float(max(0.0, -mu_max)) if mu_max else 0.0
    complementarity = 0.0
    if n_ineq and mu_full.size:
        complementarity = float(np.max(np.abs(mu_full * g)))

    scale = max(1.0, float(np.max(np.abs(grad_f))) if grad_f.size else 1.0)
    components = {
        "stationarity": stationarity / scale,
        "primal_feasibility": primal,
        "dual_feasibility": dual,
        "complementarity": complementarity / scale,
    }
    components["kkt_residual"] = max(components.values())
    components["max_multiplier"] = mu_max
    components["gradient_norm_inf"] = scale
    return components


def _fd_jac(fun, x, step: float = 1e-6) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    base = np.atleast_1d(np.asarray(fun(x), dtype=float))
    J = np.zeros((base.size, x.size))
    for i in range(x.size):
        h = step * max(1.0, abs(float(x[i])))
        xp = x.copy()
        xm = x.copy()
        xp[i] += h
        xm[i] -= h
        J[:, i] = (np.atleast_1d(fun(xp)) - np.atleast_1d(fun(xm))) / (2.0 * h)
    return J