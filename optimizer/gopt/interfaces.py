"""G-OPT protocol layer: solver -> gradient -> optimizer contracts.

This module is deliberately *domain free*: it only speaks about scalar
objectives, constraint sets, primal/adjoint evaluations and finite-difference
gradient checks.  Concrete physics models are injected by callers (see
``optimizer/gopt/cases/``), never hardcoded here.

Three contracts
---------------
1. :class:`Objective` -- ``value(x) -> float`` plus an *optional* analytic or
   adjoint gradient ``gradient(x) -> ndarray``.  ``gradient is None`` marks a
   gradient-free problem (CMA-ES path).
2. :class:`AdjointSolver` -- the standard "solver -> gradient function ->
   optimizer" protocol used by PDE codes: ``primal(x)`` returns the objective
   value *and* the converged state; ``adjoint(state)`` maps the state back to
   dJ/dx.  This is what a LBM/FVM/FEM/BEM solver has to implement to plug into a
   gradient-based optimizer.
3. :class:`ConstraintSet` -- equality (``c_eq(x) == 0``) and inequality
   (``c_ineq(x) >= 0``) constraints with Jacobians.

Verification helpers
--------------------
``adjoint_consistency_check`` implements the two canonical adjoint audits:

* **Taylor / directional-derivative test** -- ``(J(x+h d) - J(x)) / h`` must
  converge to ``<g, d>`` at *first* order in ``h``; the reported convergence
  order (fitted over a Richardson halving sequence) must be >= 0.9.
* **central-difference comparison** -- relative error between the adjoint
  gradient and an independent 5-point central difference must be below the
  caller's tolerance (default ``5e-6``).

Both are machine-checkable, so a solver that silently returns a wrong gradient
cannot pass.  ``evidence_grade`` in the returned report summarises the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "Objective",
    "FunctionObjective",
    "ConstraintSet",
    "PrimalResult",
    "AdjointSolver",
    "finite_difference_gradient",
    "adjoint_consistency_check",
    "InjectedAdjointObjective",
]


# ---------------------------------------------------------------------------
# Objective / constraint contracts
# ---------------------------------------------------------------------------
class Objective:
    """Scalar objective with an optional gradient callback.

    Subclasses (or duck-typed objects) must provide ``value``.  ``gradient``
    returning ``None`` is legal and means "gradient information unavailable";
    gradient-based optimizers must then refuse to run (fail loud, not silent).
    """

    name: str = "objective"

    def value(self, x: np.ndarray) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def gradient(self, x: np.ndarray) -> Optional[np.ndarray]:
        return None

    def __call__(self, x: np.ndarray) -> float:
        return self.value(x)


class FunctionObjective(Objective):
    """Wrap two plain callables into an :class:`Objective`."""

    def __init__(
        self,
        fun: Callable[[np.ndarray], float],
        jac: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        name: str = "objective",
    ) -> None:
        self._fun = fun
        self._jac = jac
        self.name = name
        self.n_eval = 0
        self.n_grad_eval = 0

    def value(self, x: np.ndarray) -> float:
        self.n_eval += 1
        return float(self._fun(np.asarray(x, dtype=float)))

    def gradient(self, x: np.ndarray) -> Optional[np.ndarray]:
        if self._jac is None:
            return None
        self.n_grad_eval += 1
        return np.asarray(self._jac(np.asarray(x, dtype=float)), dtype=float)


@dataclass
class ConstraintSet:
    """Equality / inequality constraints with optional Jacobians.

    Convention: ``c_eq(x) == 0`` and ``c_ineq(x) >= 0`` (the SLSQP classic
    convention used by Kraft 1988; callers converting from ``scipy``'s
    ``<= 0`` convention must flip the sign).
    """

    eq: Optional[Callable[[np.ndarray], np.ndarray]] = None
    eq_jac: Optional[Callable[[np.ndarray], np.ndarray]] = None
    ineq: Optional[Callable[[np.ndarray], np.ndarray]] = None
    ineq_jac: Optional[Callable[[np.ndarray], np.ndarray]] = None

    def values(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        ce = np.atleast_1d(self.eq(x)) if self.eq is not None else np.zeros(0)
        ci = np.atleast_1d(self.ineq(x)) if self.ineq is not None else np.zeros(0)
        return np.asarray(ce, float), np.asarray(ci, float)

    def jacobians(
        self, x: np.ndarray, numerically: bool = False, h: float = 1e-6
    ) -> Tuple[np.ndarray, np.ndarray]:
        n = x.size
        if self.eq is not None:
            Je = (
                np.asarray(self.eq_jac(x), float).reshape(-1, n)
                if (self.eq_jac is not None and not numerically)
                else _fd_jacobian(self.eq, x, h)
            )
        else:
            Je = np.zeros((0, n))
        if self.ineq is not None:
            Ji = (
                np.asarray(self.ineq_jac(x), float).reshape(-1, n)
                if (self.ineq_jac is not None and not numerically)
                else _fd_jacobian(self.ineq, x, h)
            )
        else:
            Ji = np.zeros((0, n))
        return Je, Ji


def _fd_jacobian(f, x: np.ndarray, h: float) -> np.ndarray:
    """Central-difference Jacobian of a vector function."""
    f0 = np.atleast_1d(f(x)).astype(float)
    J = np.zeros((f0.size, x.size))
    for i in range(x.size):
        dx = np.zeros_like(x)
        dx[i] = h * max(1.0, abs(x[i]))
        J[:, i] = (np.atleast_1d(f(x + dx)) - np.atleast_1d(f(x - dx))) / (2 * dx[i])
    return J


# ---------------------------------------------------------------------------
# Adjoint (solver -> gradient) protocol
# ---------------------------------------------------------------------------
@dataclass
class PrimalResult:
    """Output of one primal solve: objective value plus whatever state the
    adjoint sweep needs (fields, fluxes, factorization, checkpoint, ...)."""

    value: float
    state: object = None
    meta: Dict[str, object] = field(default_factory=dict)


class AdjointSolver:
    """Standard gradient callback protocol for PDE / iterative solvers.

    ``primal(x)`` solves the state equation for design ``x`` and returns
    ``(J, state)``; ``adjoint(x, state)`` solves the adjoint (transposed) system
    once and returns ``dJ/dx`` with the *cost of one extra linear solve*,
    independent of ``dim(x)``.  ``dim`` must be the design dimension.
    """

    dim: int = 0
    name: str = "adjoint_solver"

    def primal(self, x: np.ndarray) -> PrimalResult:  # pragma: no cover
        raise NotImplementedError

    def adjoint(self, x: np.ndarray, state: object) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    # convenience: total derivative dJ/dx = partial + adjoint contribution
    def gradient(self, x: np.ndarray) -> np.ndarray:  # pragma: no cover
        x = np.asarray(x, float)
        res = self.primal(x)
        return np.asarray(self.adjoint(x, res.state), float)

    def value(self, x: np.ndarray) -> float:
        return float(self.primal(np.asarray(x, float)).value)


class InjectedAdjointObjective(Objective):
    """Adapt an :class:`AdjointSolver` to the :class:`Objective` interface.

    This is the single seam through which an external solver (LBM/FVM/FEM/BEM)
    plugs into *both* optimizers: gradient-free CMA-ES only calls ``value``
    (primal solve), SLSQP calls ``value`` + ``gradient`` (primal + adjoint
    solve).
    """

    def __init__(self, solver: AdjointSolver) -> None:
        self.solver = solver
        self.name = getattr(solver, "name", "adjoint_solver")
        self.n_primal = 0
        self.n_adjoint = 0

    def value(self, x: np.ndarray) -> float:
        self.n_primal += 1
        return float(self.solver.primal(np.asarray(x, float)).value)

    def gradient(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(self.solver.gradient(np.asarray(x, float)), float)


# ---------------------------------------------------------------------------
# Gradient verification utilities
# ---------------------------------------------------------------------------
def finite_difference_gradient(
    fun: Callable[[np.ndarray], float], x: np.ndarray, h: float = 1e-6,
    scheme: str = "central",
) -> np.ndarray:
    """Standalone finite-difference gradient (5-point stencil by default order)."""
    x = np.asarray(x, float)
    g = np.zeros_like(x)
    for i in range(x.size):
        step = h * max(1.0, abs(x[i]))
        e = np.zeros_like(x)
        e[i] = step
        if scheme == "central":
            g[i] = (fun(x + e) - fun(x - e)) / (2 * step)
        elif scheme == "fivediagonal":
            g[i] = (
                -fun(x + 2 * e) + 8 * fun(x + e) - 8 * fun(x - e) + fun(x - 2 * e)
            ) / (12 * step)
        else:  # one-sided
            g[i] = (fun(x + e) - fun(x)) / step
    return g


def _taylor_order(errors: Sequence[float], steps: Sequence[float]) -> float:
    """Least-squares slope of log(error) vs log(step)."""
    e = np.asarray([max(v, 1e-300) for v in errors], float)
    h = np.asarray(steps, float)
    if e.size < 2:
        return float("nan")
    A = np.vstack([np.log(h), np.ones_like(h)]).T
    slope = np.linalg.lstsq(A, np.log(e), rcond=None)[0][0]
    return float(slope)


def adjoint_consistency_check(
    solver: AdjointSolver,
    x: np.ndarray,
    h0: float = 1e-3,
    n_halvings: int = 4,
    grad_tol: float = 5e-6,
    order_tol: float = 0.9,
    fd_stencil: str = "fivediagonal",
) -> Dict[str, object]:
    """Audit an adjoint gradient against two independent references.

    Returns a dict with

    ``grad_rel_err``      max_j |g_adj - g_fd| / (|g_fd| + 1e-30)
    ``taylor_errors``     |(J(x+hd) - J(x))/h - <g_adj, d>| for a fixed random
                          direction ``d`` over a Richardson halving sequence
    ``taylor_order``      fitted convergence order (first order expected -> ~1)
    ``passes``            bool, both criteria satisfied
    ``evidence_grade``    "REAL" when passing, "WITHHELD" otherwise
    """
    x = np.asarray(x, float)
    g_adj = np.asarray(solver.gradient(x), float)
    g_fd = finite_difference_gradient(solver.value, x, h=max(1e-6, 1e-3 * h0),
                                     scheme=fd_stencil)
    scale = np.maximum(np.abs(g_fd), 1e-30)
    rel_err_vec = np.abs(g_adj - g_fd) / scale
    grad_rel_err = float(np.max(rel_err_vec)) if rel_err_vec.size else 0.0

    rng = np.random.default_rng(20261110)
    d = rng.standard_normal(x.size)
    d /= np.linalg.norm(d)
    directional = float(np.dot(g_adj, d))
    j0 = float(solver.value(x))
    steps, errors = [], []
    h = h0
    for _ in range(n_halvings + 1):
        j1 = float(solver.value(x + h * d))
        err = abs((j1 - j0) / h - directional)
        steps.append(h)
        errors.append(err)
        h *= 0.5
    order = _taylor_order(errors, steps)

    passes = bool(grad_rel_err <= grad_tol and order >= order_tol)
    return {
        "x": x.tolist(),
        "grad_adjoint": g_adj.tolist(),
        "grad_fd": g_fd.tolist(),
        "grad_rel_err": grad_rel_err,
        "grad_rel_err_per_component": rel_err_vec.tolist(),
        "grad_tol": grad_tol,
        "taylor_steps": steps,
        "taylor_errors": errors,
        "taylor_order": order,
        "taylor_order_tol": order_tol,
        "directional_derivative_adjoint": directional,
        "passes": passes,
        "evidence_grade": "REAL" if passes else "WITHHELD",
    }