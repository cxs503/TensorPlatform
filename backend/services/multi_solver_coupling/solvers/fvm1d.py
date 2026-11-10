"""1-D finite-volume solver for steady/transient heat conduction.

Discretization
--------------
Cell-centred finite volumes on ``[a, b]`` with ``N`` cells of width
``h = (b - a)/N``.  Steady conduction solves the conservative form

.. math::  \\frac{dq}{dx} = f, \\qquad q = -k\\,\\frac{dT}{dx}

so that each cell obeys the *discrete* conservation law
``q_{i+1/2} - q_{i-1/2} = f_i h`` **by construction**.  Face conductivities use
the harmonic mean ``k_f = 2 k_i k_{i+1} / (k_i + k_{i+1})``, which is the
correct interpolation for a series resistance and keeps the scheme exact for
piecewise-constant ``k``.

Boundary handling: a Dirichlet end is closed through a semi-cell resistance
``h/(2k)`` (second order); a Neumann end prescribes the face flux directly.
The face fluxes are returned in the canonical ``+x`` convention (see
:mod:`services.multi_solver_coupling.solvers.base`).

Transient conduction uses explicit FTCS with the von Neumann limit
``dt <= 0.5 h^2 / alpha``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Union

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve

from .base import BoundaryCondition, Solution, dirichlet

Conductivity = Union[float, Callable[[float], float]]


@dataclass
class FVM1DResult:
    """Legacy steady result container (kept for API compatibility)."""

    T: np.ndarray
    x: np.ndarray
    q_left: float
    q_right: float


class FVM1D:
    """Steady / transient 1-D heat conduction by finite volumes."""

    name = "FVM"

    def __init__(
        self,
        a: float,
        b: float,
        n: int,
        k: Conductivity = 1.0,
        alpha: float = 1.0,
        source: Optional[Callable[[float], float]] = None,
    ) -> None:
        if n < 2:
            raise ValueError("need at least 2 cells")
        if not b > a:
            raise ValueError("need b > a")
        self.a, self.b, self.n = float(a), float(b), int(n)
        self.k = k
        self.alpha = float(alpha)
        self.source = source
        self.h = (self.b - self.a) / self.n
        self.x = self.a + (np.arange(self.n) + 0.5) * self.h

    # -- conductivity -------------------------------------------------------
    def k_at(self, x: float) -> float:
        return float(self.k(x)) if callable(self.k) else float(self.k)

    def _cell_k(self) -> np.ndarray:
        if callable(self.k):
            return np.array([float(self.k(xi)) for xi in self.x])
        return np.full(self.n, float(self.k))

    def _face_k(self) -> np.ndarray:
        """Harmonic-mean face conductivities, ``n+1`` faces."""
        kc = self._cell_k()
        kf = np.empty(self.n + 1)
        kf[0] = self.k_at(self.a)
        kf[-1] = self.k_at(self.b)
        for i in range(1, self.n):
            kf[i] = 2.0 * kc[i - 1] * kc[i] / (kc[i - 1] + kc[i])
        return kf

    def _cell_source(self) -> np.ndarray:
        if self.source is None:
            return np.zeros(self.n)
        return np.array([float(self.source(xi)) for xi in self.x])

    def source_integral(self) -> float:
        """Volume integral of the source over the domain (unit cross-section)."""
        return float(np.sum(self._cell_source()) * self.h)

    # -- uniform coupling interface ----------------------------------------
    def solve(self, left: BoundaryCondition, right: BoundaryCondition) -> Solution:
        if left.kind != "dirichlet" and right.kind != "dirichlet":
            raise ValueError("both-Neumann problem is undetermined")
        n, h = self.n, self.h
        kf = self._face_k()
        fh = self._cell_source() * h
        main = np.zeros(n)
        lower = np.zeros(n - 1)
        upper = np.zeros(n - 1)
        rhs = fh.copy()

        # interior faces
        for i in range(1, n):
            main[i - 1] += kf[i] / h
            main[i] += kf[i] / h
            lower[i - 1] = -kf[i] / h
            upper[i - 1] = -kf[i] / h

        # left end
        if left.is_dirichlet:
            main[0] += 2.0 * kf[0] / h
            rhs[0] += 2.0 * kf[0] * left.value / h
        else:
            rhs[0] += left.canonical_flux("left")

        # right end
        if right.is_dirichlet:
            main[-1] += 2.0 * kf[-1] / h
            rhs[-1] += 2.0 * kf[-1] * right.value / h
        else:
            rhs[-1] += right.canonical_flux("right")

        A = diags([lower, main, upper], offsets=[-1, 0, 1], format="csc")
        T = spsolve(A, rhs)
        T = np.asarray(T, dtype=float)

        # face fluxes in canonical +x
        if left.is_dirichlet:
            q_left = 2.0 * kf[0] * (left.value - T[0]) / h
        else:
            q_left = left.canonical_flux("left")
        if right.is_dirichlet:
            q_right = 2.0 * kf[-1] * (T[-1] - right.value) / h
        else:
            q_right = right.canonical_flux("right")

        # end-face temperatures (Dirichlet: imposed; Neumann: semi-cell
        # reconstruction, consistent with the discretization above)
        t_left = left.value if left.is_dirichlet else T[0] + q_left * h / (2.0 * kf[0])
        t_right = right.value if right.is_dirichlet else T[-1] - q_right * h / (2.0 * kf[-1])

        return Solution(
            solver=self.name,
            a=self.a,
            b=self.b,
            x=self.x,
            T=T,
            q_left=float(q_left),
            q_right=float(q_right),
            t_left=float(t_left),
            t_right=float(t_right),
        )

    # -- legacy API ---------------------------------------------------------
    def solve_steady(
        self,
        t_left: Optional[float] = None,
        q_left: Optional[float] = None,
        t_right: Optional[float] = None,
        q_right: Optional[float] = None,
    ) -> FVM1DResult:
        left = dirichlet(t_left) if t_left is not None else None
        right = dirichlet(t_right) if t_right is not None else None
        if left is None:
            if q_left is None:
                raise ValueError("left end needs t_left or q_left")
            left = BoundaryCondition("neumann", q_left)
        if right is None:
            if q_right is None:
                raise ValueError("right end needs t_right or q_right")
            right = BoundaryCondition("neumann", q_right)
        sol = self.solve(left, right)
        return FVM1DResult(T=sol.T, x=sol.x, q_left=sol.q_left, q_right=sol.q_right)

    # -- transient ----------------------------------------------------------
    def solve_transient(
        self,
        t_end: float,
        dt: float,
        t_left_fn: Callable[[float], float],
        t_right_fn: Callable[[float], float],
        t0: Optional[np.ndarray] = None,
    ):
        """Explicit FTCS integration of ``dT/dt = alpha d^2T/dx^2``.

        Returns ``(T_history, x, t_history, q_left_history, q_right_history)``
        where the flux histories are in the canonical ``+x`` convention.
        """
        if self.source is not None:
            raise NotImplementedError("transient path does not carry a source term")
        n, h, alpha = self.n, self.h, self.alpha
        steps = int(round(t_end / dt))
        if abs(steps * dt - t_end) > 1e-12 * max(1.0, t_end):
            raise ValueError("t_end must be a multiple of dt")
        if dt > 0.5 * h * h / alpha + 1e-15:
            raise ValueError("dt exceeds FTCS stability limit 0.5 h^2 / alpha")

        T = np.zeros(n) if t0 is None else np.asarray(t0, dtype=float).copy()
        t_hist: List[float] = [0.0]
        T_hist: List[np.ndarray] = [T.copy()]
        ql_hist = [self._face_flux_left(T, t_left_fn(0.0))]
        qr_hist = [self._face_flux_right(T, t_right_fn(0.0))]

        for m in range(1, steps + 1):
            t = m * dt
            tl, tr = t_left_fn(t), t_right_fn(t)
            Tn = T.copy()
            T[1:-1] = Tn[1:-1] + (alpha * dt / h**2) * (Tn[2:] - 2.0 * Tn[1:-1] + Tn[:-2])
            T[0] = Tn[0] + (alpha * dt / h**2) * (Tn[1] - 2.0 * Tn[0] + tl)
            T[-1] = Tn[-1] + (alpha * dt / h**2) * (tr - 2.0 * Tn[-1] + Tn[-2])
            t_hist.append(t)
            T_hist.append(T.copy())
            ql_hist.append(self._face_flux_left(T, tl))
            qr_hist.append(self._face_flux_right(T, tr))

        return (
            np.array(T_hist),
            self.x,
            np.array(t_hist),
            np.array(ql_hist),
            np.array(qr_hist),
        )

    def _face_flux_left(self, T: np.ndarray, t_left: float) -> float:
        return self.k_at(self.a) * (t_left - T[0]) / (2.0 * self.h)

    def _face_flux_right(self, T: np.ndarray, t_right: float) -> float:
        return self.k_at(self.b) * (T[-1] - t_right) / (2.0 * self.h)