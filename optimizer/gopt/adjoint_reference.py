"""Reference implementation of the G-OPT adjoint protocol (domain-free).

The protocol a PDE solver has to implement is only three methods
(:class:`~optimizer.gopt.interfaces.AdjointSolver`)::

    primal(x)         -> PrimalResult(value, state)     # one forward solve
    adjoint(x, state) -> dJ/dx                          # one adjoint solve
    gradient(x)       -> dJ/dx                          # composition

This module provides a fully analytic *reference* instance of that protocol so
that (a) the protocol is executable and testable without a physics code, and
(b) any solver plugged in later (LBM / FVM / FEM / BEM) can be checked against
the identical audit (``adjoint_consistency_check``).

Model
-----
Discrete implicit state equation with design-dependent operator::

    A(x) u = b                                (the "PDE", one linear solve)
    J(x)   = 0.5 u^T Q u + c^T x              (the objective)

Implicit differentiation of ``A u = b`` gives ``A du/dx_j = -(dA/dx_j) u``, hence

    dJ/dx_j = c_j - w^T (dA/dx_j) u,      A^T w = Q u

so the *whole* gradient costs **one** extra linear solve (the adjoint) plus
``n`` cheap matrix-vector products, independent of the number of design
variables.  ``FiniteDifference`` needs ``n+1`` primal solves -- the speed-up is
reported by :meth:`LinearStateAdjointSolver.cost_comparison`.

The concrete operator family used as a demo is the 1-D finite-element diffusion
(stiffness) operator with per-element conductivities ``k_j = 1 + x_j``::

    A = sum_j k_j K_j + Dirichlet anchor at node 0,   K_j = (1/h)*[[1,-1],[-1,1]]

which is exactly the assembly pattern of an FEM/FVM code, so the reference
implementation exercises the same three ingredients (assembly, solve, transpose
solve) that a real solver has to provide.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .interfaces import AdjointSolver, PrimalResult

__all__ = [
    "LinearStateAdjointSolver",
    "FunctionAdjointSolver",
    "diffusion_element_operators",
]


# ---------------------------------------------------------------------------
# Adapter for "I already have value + gradient callables"
# ---------------------------------------------------------------------------
class FunctionAdjointSolver(AdjointSolver):
    """Wrap ``(value, gradient)`` callables into the solver protocol.

    Useful to run the *same* consistency audit on an analytic gradient (e.g. a
    closed-form empirical model) that is used on a PDE adjoint.
    """

    def __init__(self, value_fn: Callable[[np.ndarray], float],
                 grad_fn: Callable[[np.ndarray], np.ndarray],
                 dim: int, name: str = "function_solver") -> None:
        self._value = value_fn
        self._grad = grad_fn
        self.dim = int(dim)
        self.name = name
        self.n_primal = 0
        self.n_adjoint = 0

    def primal(self, x: np.ndarray) -> PrimalResult:
        self.n_primal += 1
        return PrimalResult(value=float(self._value(np.asarray(x, float))), state=None)

    def adjoint(self, x: np.ndarray, state: object) -> np.ndarray:
        self.n_adjoint += 1
        return np.asarray(self._grad(np.asarray(x, float)), float)

    def gradient(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, float)
        res = self.primal(x)
        return self.adjoint(x, res.state)


# ---------------------------------------------------------------------------
# Fully analytic linear-state adjoint reference
# ---------------------------------------------------------------------------
def diffusion_element_operators(n_elem: int, h: float = 1.0) -> List[np.ndarray]:
    """Element stiffness matrices of the 1-D linear-FEM diffusion operator.

    Returns ``n_elem`` dense ``(n_elem+1, n_elem+1)`` matrices ``K_j`` with
    ``A(x) = sum_j (1 + x_j) K_j`` -- the assembly pattern of an FEM/FVM code.
    """
    n_node = n_elem + 1
    ops = []
    for j in range(n_elem):
        K = np.zeros((n_node, n_node))
        K[j, j] += 1.0 / h
        K[j, j + 1] -= 1.0 / h
        K[j + 1, j] -= 1.0 / h
        K[j + 1, j + 1] += 1.0 / h
        ops.append(K)
    return ops


@dataclass
class LinearStateSpec:
    """Problem definition for :class:`LinearStateAdjointSolver`."""

    element_ops: Sequence[np.ndarray]   # dA/dx_j  (n_design matrices, same shape)
    b: np.ndarray                        # right-hand side
    Q: np.ndarray                        # objective weight on the state
    c: Optional[np.ndarray] = None       # direct design dependence c^T x


class LinearStateAdjointSolver(AdjointSolver):
    """``A(x) u = b``, ``J = 0.5 u^T Q u + c^T x`` with an exact adjoint gradient."""

    def __init__(self, spec: LinearStateSpec, name: str = "linear_state_solver") -> None:
        self.spec = spec
        self.name = name
        self.dim = len(spec.element_ops)
        self.n_primal = 0
        self.n_adjoint = 0
        self.n_matvec = 0

    # -- operator assembly ---------------------------------------------------
    def _A(self, x: np.ndarray) -> np.ndarray:
        n = self.spec.element_ops[0].shape[0]
        A = np.zeros((n, n))
        for j, K in enumerate(self.spec.element_ops):
            A += (1.0 + x[j]) * K
        # Dirichlet anchor at node 0: without it the pure-diffusion stiffness is
        # singular (constant mode). The anchor is design independent, so it does
        # not enter dA/dx.
        A[0, 0] += 1.0
        return A

    def primal(self, x: np.ndarray) -> PrimalResult:
        x = np.asarray(x, float)
        self.n_primal += 1
        A = self._A(x)
        u = np.linalg.solve(A, self.spec.b)
        J = 0.5 * float(u @ (self.spec.Q @ u))
        if self.spec.c is not None:
            J += float(np.dot(self.spec.c, x))
        return PrimalResult(value=J, state={"u": u, "A": A}, meta={"solve": "primal"})

    def adjoint(self, x: np.ndarray, state: object) -> np.ndarray:
        x = np.asarray(x, float)
        self.n_adjoint += 1
        if not isinstance(state, dict) or "u" not in state:
            res = self.primal(x)
            state = res.state
        u = state["u"]
        A = state["A"]
        w = np.linalg.solve(A.T, self.spec.Q @ u)      # one adjoint solve
        g = np.zeros(self.dim)
        for j, K in enumerate(self.spec.element_ops):
            self.n_matvec += 1
            g[j] = -float(w @ (K @ u))
        if self.spec.c is not None:
            g += self.spec.c
        return g

    def gradient(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, float)
        res = self.primal(x)
        return self.adjoint(x, res.state)

    def cost_comparison(self, x: np.ndarray, fd_builder: Optional[Callable] = None) -> Dict[str, object]:
        """Counts of linear solves for the adjoint vs finite-difference gradient."""
        self.n_primal = self.n_adjoint = self.n_matvec = 0
        _ = self.gradient(x)
        adj = {"primal_solves": self.n_primal, "adjoint_solves": self.n_adjoint,
               "matvecs": self.n_matvec}
        return {
            "dim": self.dim,
            "adjoint": adj,
            "finite_difference_primal_solves": 1 + 2 * self.dim,
            "speedup_factor": round((1 + 2 * self.dim) / max(1, adj["primal_solves"] + adj["adjoint_solves"]), 4),
        }