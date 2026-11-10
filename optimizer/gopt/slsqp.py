"""G-OPT SLSQP: sequential least-squares (quadratic) programming, own implementation.

Gradient-based optimizer for *smooth* problems with equality and inequality
constraints -- the complement of the CMA-ES path in :mod:`optimizer.gopt.cmaes`.
Gradients are supplied through the :class:`~optimizer.gopt.interfaces.Objective`
callback (analytic, finite-difference, or an *adjoint* solve from a PDE code);
if the objective reports ``gradient(x) is None`` this optimizer refuses to run
rather than silently falling back to a derivative-free heuristic.

Algorithm (Kraft 1988, the same family as NLOPT's ``LD_SLSQP``):

1. linearise objective + constraints at ``x_k`` and solve the convex QP

   .. code-block:: text

       min_p  0.5 p^T B_k p + g_k^T p
       s.t.   J_eq(x_k) p = -c_eq(x_k)
              J_ineq(x_k) p >= -c_ineq(x_k)

   with ``B_k`` a BFGS approximation of the Hessian of the Lagrangian.  The QP
   is solved by this module (:func:`solve_qp`): a quadratic-penalty Newton
   phase followed by an exact active-set polish with multiplier-sign checks, so
   the returned direction carries KKT multipliers that are provably valid.
2. L1 merit-function line search, ``Phi(x) = f(x) + sum_i mu_i |c_i(x)|``, with
   the penalty ``mu`` raised above the largest QP multiplier (Kraft's rule);
3. damped (Powell-modified) BFGS update of ``B_k``;
4. KKT convergence test ``max|p| <= xtol`` and constraint violation <= ``ctol``.

Everything is checked against closed-form KKT solutions and against
``scipy.optimize.minimize(method="SLSQP")`` in ``tests/test_gopt_slsqp.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .interfaces import ConstraintSet, Objective

__all__ = ["QPResult", "solve_qp", "SLSQPResult", "SLSQPOptimizer", "minimize_slsqp"]


# ---------------------------------------------------------------------------
# Dense convex QP
# ---------------------------------------------------------------------------
@dataclass
class QPResult:
    p: np.ndarray
    lam_eq: np.ndarray
    lam_ineq: np.ndarray
    exact: bool
    n_iter: int
    kkt_residual: float
    active_set: List[int]


def _kkt_residual(B: np.ndarray, g: np.ndarray, p: np.ndarray, Aeq: np.ndarray,
                   beq: np.ndarray, Aineq: np.ndarray, bineq: np.ndarray,
                   mu: np.ndarray) -> float:
    """max of primal feasibility and stationarity residuals (multiplier-aware)."""
    parts = [np.linalg.norm(p, np.inf) * 0 + np.max(np.abs(B @ p + g) * 0)]  # placeholder 0
    res = 0.0
    if Aeq.size:
        res = max(res, float(np.max(np.abs(Aeq @ p - beq))))
    if Aineq.size:
        res = max(res, float(np.max(np.maximum(bineq - Aineq @ p, 0.0))))
        act = bineq - Aineq @ p <= 1e-9
        if np.any(act):
            res = max(res, float(np.max(np.maximum(-mu[act], 0.0))))
    grad = B @ p + g
    if Aeq.size or (Aineq.size and np.any(mu > 0)):
        A = np.vstack([Aeq, Aineq[mu > 0]]) if Aeq.size else Aineq[mu > 0]
        if A.size:
            # stationarity: B p + g = A^T mu
            resid = grad - A.T @ np.concatenate([np.zeros(Aeq.shape[0]), mu[mu > 0]]) if Aeq.size \
                else grad - A.T @ mu[mu > 0]
            res = max(res, float(np.max(np.abs(resid))))
        else:
            res = max(res, float(np.max(np.abs(grad))))
    else:
        res = max(res, float(np.max(np.abs(grad))))
    return res


def solve_qp(
    B: np.ndarray,
    g: np.ndarray,
    Aeq: Optional[np.ndarray] = None,
    beq: Optional[np.ndarray] = None,
    Aineq: Optional[np.ndarray] = None,
    bineq: Optional[np.ndarray] = None,
    max_iter: int = 60,
    tol: float = 1e-10,
) -> QPResult:
    """Solve ``min 0.5 p'B p + g'p`` s.t. ``Aeq p = beq``, ``Aineq p >= bineq``.

    Conventions: multipliers of the active inequalities satisfy ``mu >= 0`` and
    stationarity ``B p + g = Aeq^T nu + Aact^T mu``.  ``B`` must be SPD.
    """
    B = np.asarray(B, float)
    g = np.asarray(g, float).reshape(-1)
    n = g.size
    Aeq = np.zeros((0, n)) if Aeq is None else np.asarray(Aeq, float).reshape(-1, n)
    beq = np.zeros(Aeq.shape[0]) if beq is None else np.asarray(beq, float).reshape(-1)
    Aineq = np.zeros((0, n)) if Aineq is None else np.asarray(Aineq, float).reshape(-1, n)
    bineq = np.zeros(Aineq.shape[0]) if bineq is None else np.asarray(bineq, float).reshape(-1)

    if Aineq.shape[0] == 0 and Aeq.shape[0] == 0:
        p = np.linalg.solve(B, -g)
        return QPResult(p, np.zeros(0), np.zeros(0), True, 1,
                        float(np.max(np.abs(B @ p + g))), [])

    def stationarity_multipliers(p: np.ndarray, W: List[int]) -> Tuple[np.ndarray, np.ndarray]:
        A = np.vstack([Aeq] + ([Aineq[W]] if W else []))
        rhs = B @ p + g
        if A.size == 0:
            return np.zeros(0), np.zeros(0)
        sol, *_ = np.linalg.lstsq(A.T, rhs, rcond=None)
        nu = sol[: Aeq.shape[0]]
        mu = sol[Aeq.shape[0]:]
        return nu, mu

    def equality_qp(W: List[int]) -> np.ndarray:
        """Exact minimiser over the equality system defined by working set W."""
        A = np.vstack([Aeq] + ([Aineq[W]] if W else []))
        b = np.concatenate([beq] + ([bineq[W]] if W else []))
        if A.shape[0] == 0:
            return np.linalg.solve(B, -g)
        K = np.zeros((n + A.shape[0], n + A.shape[0]))
        K[:n, :n] = B
        K[:n, n:] = A.T
        K[n:, :n] = A
        rhs = np.concatenate([-g, b])
        sol, *_ = np.linalg.lstsq(K, rhs, rcond=None)
        return sol[:n]

    # ---- phase 1: quadratic penalty Newton, to reach the neighbourhood ------
    rho = 10.0 * max(1.0, float(np.linalg.norm(B, np.inf)))
    p = np.zeros(n)
    it_total = 0
    for _outer in range(14):
        for _inner in range(max_iter):
            it_total += 1
            # active set from current point (hinge linearisation)
            if Aineq.shape[0]:
                slack = Aineq @ p - bineq
                act = np.where(slack <= 1e-12)[0].tolist()
            else:
                act = []
            A = np.vstack([Aeq] + ([Aineq[act]] if act else []))
            b = np.concatenate([beq] + ([bineq[act]] if act else []))
            H = B.copy()
            rhs = -g.copy()
            if Aeq.shape[0]:
                H += rho * (Aeq.T @ Aeq)
                rhs += -rho * (Aeq.T @ (Aeq @ p - beq))
            if act:
                Aa = Aineq[act]
                H += rho * (Aa.T @ Aa)
                rhs += -rho * (Aa.T @ (Aa @ p - bineq[act]))
            try:
                p_new = np.linalg.solve(H, rhs)
            except np.linalg.LinAlgError:
                p_new = np.linalg.lstsq(H, rhs, rcond=None)[0]
            step = float(np.max(np.abs(p_new - p))) if n else 0.0
            p = p_new
            if step <= 1e-13 * max(1.0, float(np.max(np.abs(p)))):
                break
        viol = 0.0
        if Aeq.shape[0]:
            viol = max(viol, float(np.max(np.abs(Aeq @ p - beq))))
        if Aineq.shape[0]:
            viol = max(viol, float(np.max(np.maximum(bineq - Aineq @ p, 0.0))))
        if viol <= max(tol, 1e-12):
            break
        rho *= 10.0

    # ---- phase 2: exact active-set polish ----------------------------------
    W: List[int] = []
    if Aineq.shape[0]:
        W = np.where(Aineq @ p - bineq <= 1e-9)[0].tolist()
    exact = False
    for _it in range(6 * max(1, n) + 40):
        it_total += 1
        p = equality_qp(W)
        nu, mu = stationarity_multipliers(p, W)
        if mu.size and np.min(mu) < -1e-9:
            W.pop(int(np.argmin(mu)))
            continue
        if Aineq.shape[0]:
            slack = Aineq @ p - bineq
            worst = int(np.argmin(slack))
            if slack[worst] < -1e-9:
                if worst in W:
                    break
                W.append(worst)
                continue
        exact = True
        break

    nu, mu_W = stationarity_multipliers(p, W)
    mu = np.zeros(Aineq.shape[0])
    for k, idx in enumerate(W):
        mu[idx] = max(mu_W[k], 0.0)
    kkt = _kkt_residual(B, g, p, Aeq, beq, Aineq, bineq, mu)
    return QPResult(p, nu, mu, bool(exact and kkt < 1e-6), it_total, kkt, W)


# ---------------------------------------------------------------------------
# SLSQP
# ---------------------------------------------------------------------------
@dataclass
class SLSQPResult:
    x: np.ndarray
    fun: float
    success: bool
    message: str
    nit: int
    n_qp_iter: int
    n_fun: int
    n_jac: int
    constraint_violation: float
    history: List[Dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "x": np.asarray(self.x, float).tolist(),
            "fun": float(self.fun),
            "success": bool(self.success),
            "message": self.message,
            "nit": int(self.nit),
            "n_qp_iter": int(self.n_qp_iter),
            "n_fun": int(self.n_fun),
            "n_jac": int(self.n_jac),
            "constraint_violation": float(self.constraint_violation),
            "history": self.history,
        }


class SLSQPOptimizer:
    """SLSQP with L1 merit line search and damped BFGS Hessian approximation."""

    def __init__(
        self,
        bounds: Optional[np.ndarray] = None,
        max_iter: int = 200,
        ftol: float = 1e-10,
        ctol: float = 1e-9,
        xtol: float = 1e-10,
        c1: float = 1e-4,
        verbose: bool = False,
    ) -> None:
        self.bounds = None if bounds is None else np.asarray(bounds, float)
        self.max_iter = int(max_iter)
        self.ftol = float(ftol)
        self.ctol = float(ctol)
        self.xtol = float(xtol)
        self.c1 = float(c1)
        self.verbose = verbose

    # -- helpers -------------------------------------------------------------
    def _constraint_functions(self, constraints: Optional[ConstraintSet], n: int,
                              numerically: bool):
        """Return closures for values + Jacobians, with optional box bounds folded in."""
        def values(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
            ce, ci = (constraints.values(x) if constraints is not None
                      else (np.zeros(0), np.zeros(0)))
            if self.bounds is not None:
                lo, hi = self.bounds[:, 0], self.bounds[:, 1]
                ci = np.concatenate([ci, x - lo, hi - x])
            return ce, ci

        def jacobians(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
            Je, Ji = (constraints.jacobians(x, numerically=numerically)
                      if constraints is not None else (np.zeros((0, n)), np.zeros((0, n))))
            if self.bounds is not None:
                I = np.eye(n)
                Ji = np.vstack([Ji, I, -I])
            return Je, Ji

        return values, jacobians

    def optimize(self, objective: Objective, x0: Sequence[float],
                 constraints: Optional[ConstraintSet] = None) -> SLSQPResult:
        x = np.asarray(x0, float).reshape(-1).copy()
        n = x.size
        g0 = objective.gradient(x)
        if g0 is None:
            raise ValueError(
                f"{self.__class__.__name__} requires a gradient callback; objective "
                f"{getattr(objective, 'name', '?')!r} returned gradient=None. Use the "
                "CMA-ES path for derivative-free problems."
            )
        values, jacobians = self._constraint_functions(constraints, n, numerically=False)
        B = np.eye(n)
        n_fun = 0
        n_jac = 0
        n_qp = 0
        mu = 1.0
        history: List[Dict[str, float]] = []
        message = "Maximum iterations reached"
        success = False

        def eval_fun(xx: np.ndarray):
            nonlocal n_fun
            n_fun += 1
            val = float(objective.value(xx))
            ce, ci = values(xx)
            viol = 0.0
            if ce.size:
                viol = max(viol, float(np.max(np.abs(ce))))
            if ci.size:
                viol = max(viol, float(np.max(np.maximum(-ci, 0.0))))
            return val, ce, ci, viol

        f, ce, ci, viol = eval_fun(x)
        g = objective.gradient(x)
        n_jac += 1
        Je, Ji = jacobians(x)
        n_jac += 1

        for it in range(self.max_iter):
            qp = solve_qp(B, g, Je, -ce, Ji, -ci)
            n_qp += qp.n_iter
            p = qp.p
            pnorm = float(np.max(np.abs(p))) if n else 0.0

            lam_max = 0.0
            if qp.lam_eq.size:
                lam_max = max(lam_max, float(np.max(np.abs(qp.lam_eq))))
            if qp.lam_ineq.size:
                lam_max = max(lam_max, float(np.max(qp.lam_ineq)))
            mu = max(mu, 1.5 * lam_max + 1e-12)

            history.append(
                {
                    "iter": it,
                    "f": f,
                    "p_norm": pnorm,
                    "violation": viol,
                    "merit": f + mu * viol,
                    "penalty": mu,
                    "qp_kkt": qp.kkt_residual,
                    "qp_exact": float(qp.exact),
                    "n_active": float(len(qp.active_set)),
                }
            )
            if self.verbose:
                print(
                    f"[gopt/slsqp] it {it:3d} f={f:.8e} |p|={pnorm:.3e} viol={viol:.2e} mu={mu:.2e}"
                )
            if pnorm <= self.xtol and viol <= self.ctol:
                success = True
                message = "KKT conditions satisfied"
                break

            # L1 merit function and its directional derivative at alpha=0
            def merit(xx: np.ndarray):
                v, cee, cii, _ = eval_fun(xx)
                pen = 0.0
                if cee.size:
                    pen += float(np.sum(np.abs(cee)))
                if cii.size:
                    pen += float(np.sum(np.maximum(-cii, 0.0)))
                return v + mu * pen

            slope = float(np.dot(g, p))
            if ce.size:
                slope += mu * float(np.sum(np.sign(ce) * (Je @ p)))
            if ci.size:
                neg = ci < 0
                if np.any(neg):
                    slope -= mu * float(np.sum(Ji[neg] @ p))

            phi0 = merit(x)
            alpha = 1.0
            accepted = False
            for _ls in range(40):
                x_try = x + alpha * p
                phi1 = merit(x_try)
                if phi1 <= phi0 + self.c1 * alpha * slope:
                    accepted = True
                    break
                alpha *= 0.5
            if not accepted:
                message = "Line search failed (merit function cannot be decreased)"
                break

            x_new = x + alpha * p
            f_new, ce_new, ci_new, viol_new = eval_fun(x_new)
            g_new = objective.gradient(x_new)
            n_jac += 1
            Je_new, Ji_new = jacobians(x_new)
            n_jac += 1

            # damped BFGS on the Lagrangian Hessian approximation
            s_vec = x_new - x
            lam_eq = qp.lam_eq
            lam_in = qp.lam_ineq
            y_vec = g_new - g
            if ce.size and Je.shape[0] == Je_new.shape[0]:
                y_vec = y_vec + Je_new.T @ lam_eq - Je.T @ lam_eq
            if ci.size and Ji.shape[0] == Ji_new.shape[0]:
                y_vec = y_vec + Ji_new.T @ lam_in - Ji.T @ lam_in
            sBs = float(s_vec @ B @ s_vec)
            sy = float(s_vec @ y_vec)
            if sBs > 1e-300:
                theta = 1.0 if sy >= 0.2 * sBs else 0.8 * sBs / (sBs - sy)
                y_vec = theta * y_vec + (1 - theta) * (B @ s_vec)
                sy = float(s_vec @ y_vec)
            if sy > 1e-300:
                Bs = B @ s_vec
                B = B + np.outer(y_vec, y_vec) / sy - np.outer(Bs, Bs) / sBs

            # convergence on objective change
            f_improvement = abs(f - f_new)
            x, f, ce, ci, viol = x_new, f_new, ce_new, ci_new, viol_new
            g, Je, Ji = g_new, Je_new, Ji_new
            if f_improvement < self.ftol and pnorm < 1e-6 and viol < self.ctol:
                success = True
                message = "Objective change below ftol with feasible point"
                break
        else:
            message = "Maximum iterations reached"

        return SLSQPResult(
            x=x,
            fun=float(f),
            success=bool(success),
            message=message,
            nit=len(history),
            n_qp_iter=int(n_qp),
            n_fun=int(n_fun),
            n_jac=int(n_jac),
            constraint_violation=float(viol),
            history=history,
        )


def minimize_slsqp(
    fun: Callable[[np.ndarray], float],
    x0: Sequence[float],
    jac: Optional[Callable[[np.ndarray], np.ndarray]] = None,
    bounds: Optional[np.ndarray] = None,
    constraints: Optional[ConstraintSet] = None,
    max_iter: int = 200,
) -> SLSQPResult:
    """Convenience wrapper: plain callables in, :class:`SLSQPResult` out."""
    from .interfaces import FunctionObjective

    obj = FunctionObjective(fun, jac)
    opt = SLSQPOptimizer(bounds=bounds, max_iter=max_iter)
    return opt.optimize(obj, x0, constraints=constraints)