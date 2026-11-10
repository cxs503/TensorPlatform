"""1-D boundary-element solver for steady conduction with constant ``k``.

Formulation
-----------
The 1-D Laplace operator has the fundamental solution ``G(x, xi) = -|x-xi|/(2k)``
(``k G'' = -delta``).  Green's second identity on ``[a, b]``, ``integral(u G'' - G u'') = [u G' - G u']``,
with ``G'' = -delta/k`` and ``u'' = 0``, gives the exact boundary
representation in terms of boundary data only (canonical ``+x`` flux
``q = -k u'``)::

    u(xi) = (u(a) + u(b)) / 2 + [ (b - xi) q_b - (xi - a) q_a ] / (2 k)

Only *boundary* data (``T_a, T_b, q_a, q_b``) enter, which is the defining
property of a boundary-element method: no interior discretization is ever
assembled.  For a 1-D domain the boundary is two points, so the influence
system is 2x2 and the trace solution is exact up to roundoff.

Degeneracy (stated honestly)
----------------------------
Because the 1-D boundary is 0-dimensional, the usual panel discretization and
the singular jump coefficient ``c_i`` degenerate; `BEM1D` therefore solves the
2x2 trace system in closed form and exposes
:meth:`BEM1D.representation_residual`, which re-evaluates the boundary integral
representation above at interior points.  A non-zero residual would mean the
kernel implementation is inconsistent.  The non-degenerate BEM kernel (panels,
influence matrices, singular self-terms) is exercised separately in
:mod:`services.multi_solver_coupling.solvers.bem2d`.
"""
from __future__ import annotations

from typing import Callable, Optional, Union

import numpy as np

from .base import BoundaryCondition, Solution

Conductivity = Union[float, Callable[[float], float]]


class BEM1D:
    """Exact boundary-integral solver for 1-D ``k T'' = 0``."""

    name = "BEM"

    def __init__(
        self,
        a: float,
        b: float,
        n: int,
        k: Conductivity = 1.0,
        source: Optional[Callable[[float], float]] = None,
    ) -> None:
        if callable(k):
            raise TypeError("BEM1D requires a constant conductivity")
        if source is not None:
            raise TypeError("BEM1D solves the homogeneous problem (no source term)")
        if not b > a:
            raise ValueError("need b > a")
        self.a, self.b = float(a), float(b)
        self.k = float(k)
        self.n = int(n)  # kept for interface parity; unused (no panels in 1-D)
        self.source = None
        self.x = np.linspace(self.a, self.b, max(2, self.n + 1))

    # -- helpers ------------------------------------------------------------
    def source_integral(self) -> float:
        return 0.0

    def _traces(self, left: BoundaryCondition, right: BoundaryCondition) -> tuple[float, float]:
        """Solve the 2x2 trace system for ``(T_a, q)``."""
        L = self.b - self.a
        rows: list[list[float]] = []
        rhs: list[float] = []
        for bc, side in ((left, "left"), (right, "right")):
            if bc.is_dirichlet:
                # T_a + s * q = value, with s = 0 at a and s = -L/k at b
                s = 0.0 if side == "left" else -L / self.k
                rows.append([1.0, s])
                rhs.append(bc.value)
            else:
                rows.append([0.0, 1.0])
                rhs.append(bc.canonical_flux(side))
        A = np.array(rows, dtype=float)
        bvec = np.array(rhs, dtype=float)
        if abs(np.linalg.det(A)) < 1e-14 * max(1.0, np.linalg.norm(A) ** 2):
            raise ValueError("singular boundary data (inconsistent Neumann pair)")
        z = np.linalg.solve(A, bvec)
        return float(z[0]), float(z[1])

    # -- solve --------------------------------------------------------------
    def solve(self, left: BoundaryCondition, right: BoundaryCondition) -> Solution:
        T_a, q = self._traces(left, right)
        T = T_a - q * (self.x - self.a) / self.k
        T_a = float(T[0])
        T_b = float(T[-1])
        # canonical flux is constant along the sub-domain
        return Solution(
            solver=self.name,
            a=self.a,
            b=self.b,
            x=self.x,
            T=T,
            q_left=float(q),
            q_right=float(q),
            t_left=T_a,
            t_right=T_b,
        )

    # -- internal consistency check ----------------------------------------
    def representation_residual(self, solution: Solution, samples: int = 17) -> float:
        """Max deviation of the boundary-integral representation from the field.

        Evaluated at interior points strictly inside ``(a, b)`` -- exactly the
        identity the kernel must satisfy.  Machine-precision small when the
        kernel is implemented consistently.
        """
        xi = np.linspace(self.a, self.b, samples + 2)[1:-1]
        qa, qb = solution.q_left, solution.q_right
        k = self.k
        ua, ub = solution.t_left, solution.t_right
        rep = (
            (ua + ub) / 2.0
            + ((self.b - xi) * qb - (xi - self.a) * qa) / (2.0 * k)
        )
        direct = np.interp(xi, solution.x, solution.T)
        scale = max(float(np.max(np.abs(direct))), 1e-30)
        return float(np.max(np.abs(rep - direct)) / scale)