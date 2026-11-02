"""Self-contained, analytic-reference verification case for the GCI chain.

Physical problem
----------------
Two-dimensional radiation (added-mass) problem of a circular cylinder section
in an infinite ideal fluid -- the canonical section problem behind the
strip-theory added mass that the platform's seakeeping module relies on.
The section translates with unit velocity in the ``x`` direction:

    Laplace:   d2phi/dr2 + (1/r) dphi/dr + (1/r^2) d2phi/dtheta2 = 0
    body BC:   dphi/dn = cos(theta)      at r = a        (Neumann)
    far field: phi -> 0                  as r -> infinity

Exact solution (closed-form reference, independent of any grid)::

    phi(r, theta) = -a^2 cos(theta) / r
    A11 = -rho * oint phi n_x ds = rho * pi * a^2
    C_a = A11 / (rho * pi * a^2) = 1        (exact for every refinement level)

so the QoI ``C_a`` has a reference that cannot be contaminated by the grid,
which is what a GCI study needs.

Discretisation
--------------
Body-fitted polar grid written in the log-radial coordinate ``s = ln(r/a)``.
In that variable the polar Laplacian loses its first derivative and becomes
the isotropic constant-coefficient operator ``phi_ss + phi_thetatheta = 0``,
which makes the grid *self-similar* under refinement (uniform ``ds`` and
uniform ``dtheta``, and a doubling of the grid exactly halves both spacings):

    phi_ss + phi_thetatheta = 0
    phi_s(0, theta) = a cos(theta)     (Neumann at r = a,  r phi_r = phi_s)
    phi(s_max, theta) = 0              (Dirichlet at r = R_out)

Second-order central differences in both directions; the Neumann condition is
a ghost-node elimination (second order, no half-cell offset).  The observed
order of accuracy of the triplet should therefore approach 2.

Added mass from the trapezoidal contour integral on ``r = a``::

    A11 = -rho * a * dtheta * sum_j phi(0, theta_j) cos(theta_j)
    C_a = A11 / (rho pi a^2) = -(dtheta / (pi a)) * sum_j phi(0,theta_j) cos(theta_j)

Documented error sources
------------------------
* discretisation error ``O(ds^2) + O(dtheta^2)`` -- exactly what GCI measures;
* far-field truncation ``O((a/R_out)^2)`` -- a *common* error that does not
  shrink with refinement (``R_out = 200 a`` gives ~2.5e-5 relative); it shows
  up as a floor in the extrapolated value, not in ``U_V``;
* the periodic trapezoid contour integral is exact for the ``cos(theta)``
  mode, so it does not contribute to the discretisation error.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, List

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

__all__ = ["circle_added_mass", "refinement_levels", "ANALYTIC_CA", "CaseResult"]

ANALYTIC_CA = 1.0    # exact added-mass coefficient of a circular section
R_OUT_RATIO = 200.0  # outer boundary radius in units of the body radius


@dataclass
class CaseResult:
    nr: int
    ntheta: int
    ds: float
    dtheta: float
    ca: float
    rel_err_vs_analytic: float
    resid: float
    nnz: int

    def to_dict(self) -> dict:
        return asdict(self)


def circle_added_mass(
    nr: int,
    ntheta: int,
    *,
    a: float = 1.0,
    r_out_ratio: float = R_OUT_RATIO,
    rho: float = 1.0,
) -> CaseResult:
    """Solve the 2D section radiation problem on an ``(nr, ntheta)`` grid.

    ``nr`` = number of radial intervals in ``s = ln(r/a)`` (nodes ``i = 0..nr``,
    ``i = 0`` on the body), ``ntheta`` = number of periodic circumferential
    nodes.  Returns the dimensionless added-mass coefficient ``C_a``.
    """
    if nr < 4 or ntheta < 8:
        raise ValueError("need nr >= 4 and ntheta >= 8 for a meaningful solve")
    s_max = math.log(r_out_ratio)
    ds = s_max / nr
    dth = 2.0 * math.pi / ntheta
    cos_t = np.cos(np.arange(ntheta) * dth)

    n_unknown = (nr + 1) * ntheta  # i = nr is the Dirichlet node

    def idx(i: int, j: int) -> int:
        return i * ntheta + (j % ntheta)

    rows: List[int] = []
    cols: List[int] = []
    vals: List[float] = []
    rhs = np.zeros(n_unknown, dtype=float)

    inv_ds2 = 1.0 / (ds * ds)
    inv_dth2 = 1.0 / (dth * dth)

    # --- inner block i = 0: Neumann via ghost node ------------------------
    # (2 phi1 - 2 phi0)/ds^2 - 2 a cos/ds + (theta term) = 0
    for j in range(ntheta):
        row = idx(0, j)
        rows += [row, row, row, row]
        cols += [row, idx(1, j), idx(0, j + 1), idx(0, j - 1)]
        vals += [-2.0 * inv_ds2 - 2.0 * inv_dth2, 2.0 * inv_ds2, inv_dth2, inv_dth2]
        rhs[row] = 2.0 * a * cos_t[j] / ds

    # --- interior nodes i = 1 .. nr-1 ------------------------------------
    for i in range(1, nr):
        for j in range(ntheta):
            row = idx(i, j)
            rows += [row, row, row, row, row]
            cols += [row, idx(i - 1, j), idx(i + 1, j), idx(i, j + 1), idx(i, j - 1)]
            vals += [-2.0 * inv_ds2 - 2.0 * inv_dth2, inv_ds2, inv_ds2, inv_dth2, inv_dth2]

    # --- outer node i = nr: Dirichlet ------------------------------------
    for j in range(ntheta):
        row = idx(nr, j)
        rows.append(row)
        cols.append(row)
        vals.append(1.0)
        rhs[row] = 0.0

    mat = coo_matrix((vals, (rows, cols)), shape=(n_unknown, n_unknown)).tocsr()
    phi = spsolve(mat, rhs)
    if not np.all(np.isfinite(phi)):
        raise RuntimeError("linear solve produced non-finite potential")

    resid = float(np.max(np.abs(mat @ phi - rhs)))

    a11 = -rho * a * dth * float(np.dot(phi[:ntheta], cos_t))
    ca = a11 / (rho * math.pi * a * a)
    return CaseResult(
        nr=nr,
        ntheta=ntheta,
        ds=ds,
        dtheta=dth,
        ca=ca,
        rel_err_vs_analytic=abs(ca - ANALYTIC_CA) / ANALYTIC_CA,
        resid=resid,
        nnz=int(mat.nnz),
    )


def refinement_levels(
    base_nr: int = 40, base_ntheta: int = 32, ratio: int = 2, levels: int = 3
) -> List[Dict[str, int]]:
    """``levels`` grids with a constant refinement ratio, coarse -> fine."""
    if ratio < 2 or levels < 3:
        raise ValueError("need ratio >= 2 and levels >= 3")
    return [{"nr": base_nr * ratio ** k, "ntheta": base_ntheta * ratio ** k} for k in range(levels)]