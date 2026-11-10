"""1-D finite-element solver: P1 (linear) Galerkin for heat conduction.

Weak form solved::

    integral k T' v' dx = integral f v dx + q_a v(a) - q_b v(b)

with ``q`` in the canonical ``+x`` convention (``q = -k dT/dx``), so a Neumann
end contributes ``+q`` at ``x=a`` and ``-q`` at ``x=b`` to the load vector.

Properties used by the coupling layer
-------------------------------------
* Element conductivity `k_e` is evaluated at the element midpoint; the recovered
  end flux is ``-k_e (T_{j+1} - T_j)/h`` on the first/last element, which is the
  *variationally consistent* flux.  Consequently, if a Neumann value is imposed
  on an end, the recovered flux equals the imposed value to machine precision --
  that identity is what makes cross-solver flux closure testable.
* Temperatures live on nodes, so the interface trace ``T(x_I)`` is a primary
  unknown (no reconstruction needed).
* Source integration uses 2-point Gauss (exact for the P1 load of polynomial
  sources up to degree 3).
"""
from __future__ import annotations

from typing import Callable, Optional, Union

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from .base import BoundaryCondition, Solution

Conductivity = Union[float, Callable[[float], float]]

_GAUSS = 1.0 / np.sqrt(3.0)


class FEM1D:
    """Steady 1-D conduction with linear elements."""

    name = "FEM"

    def __init__(
        self,
        a: float,
        b: float,
        n: int,
        k: Conductivity = 1.0,
        source: Optional[Callable[[float], float]] = None,
    ) -> None:
        if n < 1:
            raise ValueError("need at least one element")
        if not b > a:
            raise ValueError("need b > a")
        self.a, self.b, self.n = float(a), float(b), int(n)
        self.k = k
        self.source = source
        self.h = (self.b - self.a) / self.n
        self.x = self.a + np.arange(self.n + 1) * self.h
        self.k_elem = np.array([self.k_at(xi + 0.5 * self.h) for xi in self.x[:-1]])

    def k_at(self, x: float) -> float:
        return float(self.k(x)) if callable(self.k) else float(self.k)

    def source_integral(self) -> float:
        if self.source is None:
            return 0.0
        total = 0.0
        for e in range(self.n):
            x0 = self.a + e * self.h
            for g in (-_GAUSS, _GAUSS):
                xg = x0 + 0.5 * self.h * (1.0 + g)
                total += 0.5 * self.h * float(self.source(xg))
        return float(total)

    # -- assembly -----------------------------------------------------------
    def _assemble(self) -> tuple[sp.csc_matrix, np.ndarray]:
        n, h = self.n, self.h
        rows, cols, vals = [], [], []
        F = np.zeros(n + 1)

        for e in range(n):
            ke = self.k_elem[e]
            f = ke / h
            local = np.array([[f, -f], [-f, f]])
            for i in range(2):
                for j in range(2):
                    rows.append(e + i)
                    cols.append(e + j)
                    vals.append(local[i, j])
            if self.source is not None:
                x0 = self.a + e * h
                for g in (-_GAUSS, _GAUSS):
                    xg = x0 + 0.5 * h * (1.0 + g)
                    phi = np.array([0.5 * (1.0 - g), 0.5 * (1.0 + g)])
                    w = 0.5 * h * float(self.source(xg))
                    F[e] += w * phi[0]
                    F[e + 1] += w * phi[1]

        K = sp.coo_matrix((vals, (rows, cols)), shape=(n + 1, n + 1)).tocsc()
        return K, F

    # -- solve --------------------------------------------------------------
    def solve(self, left: BoundaryCondition, right: BoundaryCondition) -> Solution:
        if left.kind != "dirichlet" and right.kind != "dirichlet":
            raise ValueError("both-Neumann problem is undetermined")
        K, F = self._assemble()
        # consistent nodal loads of the source term, needed to recover the
        # *physical* end fluxes (see module docstring)
        self._src_load = F.copy()

        if left.is_dirichlet:
            F[0] += 0.0  # Dirichlet is enforced strongly below
        else:
            F[0] += left.canonical_flux("left")
        if right.is_dirichlet:
            F[-1] += 0.0
        else:
            F[-1] += -right.canonical_flux("right")

        K = K.tolil()
        for node, bc in ((0, left), (self.n, right)):
            if bc.is_dirichlet:
                K.rows[node] = [node]
                K.data[node] = [1.0]
                F[node] = bc.value
        K = K.tocsc()
        T = np.asarray(spsolve(K, F), dtype=float)

        # Element equilibrium at the first/last node:
        #   k_e (T_0 - T_1)/h = q_a + src_0      k_e (T_N - T_{N-1})/h = -q_b + src_N
        # so the physical end fluxes (canonical +x) are the element-gradient
        # estimate *minus* the source load lumped on that node.
        q_left = -self.k_elem[0] * (T[1] - T[0]) / self.h - self._src_load[0]
        q_right = self._src_load[-1] - self.k_elem[-1] * (T[-1] - T[-2]) / self.h

        return Solution(
            solver=self.name,
            a=self.a,
            b=self.b,
            x=self.x,
            T=T,
            q_left=float(q_left),
            q_right=float(q_right),
            t_left=float(T[0]),
            t_right=float(T[-1]),
        )