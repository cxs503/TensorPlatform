"""Hermite beam-column buckling FEM for the stiffened-panel interframe limit state.

Physical problem
----------------
A longitudinal stiffener of a ship's bottom/side shell, spanning between two
transverse frames (spacing ``L``), is loaded by the hull-girder axial
compression ``P``.  It is restrained laterally by the attached plating, which
is modelled as a Winkler foundation of modulus ``k`` (per unit length).  The
governing equation is the beam-column / Hetenyi equation

    EI w'''' + P w'' + k w = 0,
    w(0) = w(L) = 0,  w''(0) = w''(L) = 0   (simply supported ends)

and the limit state is *elastic bifurcation* (interframe buckling), i.e. the
smallest eigenvalue ``P_cr``.

Discretisation
--------------
Two-node Hermite cubic elements (``w`` and ``w'`` at each node), consistent
elastic, foundation and geometric stiffness matrices; the discrete buckling
problem ``(K_e + k K_f) phi = P K_g phi`` is solved as a generalized
eigenvalue problem.  Cubic Hermite elements are fourth-order accurate for this
operator, so a three-grid GCI study should recover ``p_obs ~ 4`` — a useful
independent check of the GCI kernel on a *different* discretisation family
(FEM instead of FVM/LBM).

Analytic reference (independent of any grid)
-------------------------------------------
Substituting ``w = sin(m pi x / L)`` gives the closed form

    P_m = EI (m pi / L)**2 + k L**2 / (m**2 pi**2),    P_cr = min_m P_m.

For ``k = 0`` this is the Euler load ``pi**2 EI / L**2``.

Scope note (honest limitation)
------------------------------
This is the *elastic bifurcation* limit state of the interframe panel.  The
elasto-plastic ultimate strength (von Karman large-deflection + material
non-linearity) is **not** implemented in this node; the campaign asks for the
numerical uncertainty of a structural case, and the elastic eigenvalue problem
supplies it with an analytic reference, which the ultimate-strength problem
cannot (it has no closed-form solution).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import scipy.linalg as sla

__all__ = [
    "PanelCase",
    "DEFAULT_PANEL",
    "analytic_buckling_load",
    "buckling_fem",
    "run_buckling_study",
]


@dataclass(frozen=True)
class PanelCase:
    """Stiffened-panel interframe buckling case (SI units)."""

    name: str
    E: float            # Young's modulus [Pa]
    I: float            # stiffener second moment of area [m^4]
    L: float            # frame spacing [m]
    k: float            # Winkler foundation modulus [N/m^2]
    area: float         # stiffener cross-section area [m^2]
    sigma_yield: float  # yield stress [Pa]
    description: str = ""

    @property
    def EI(self) -> float:
        return self.E * self.I

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(EI=self.EI)
        return d


DEFAULT_PANEL = PanelCase(
    name="stiffened_panel_interframe",
    E=2.06e11,
    I=3.4e-6,
    L=2.4,
    k=7.2e5,
    area=1.8e-3,        # 150 x 12 mm flat-bar stiffener
    sigma_yield=315e6,
    description=(
        "bottom-shell longitudinal (150x12 flat bar) between transverse frames, "
        "plate strip b = 0.8 m, t = 20 mm -> Winkler modulus k"
    ),
)


def analytic_buckling_load(case: PanelCase, n_modes: int = 40) -> Tuple[float, int, List[float]]:
    """Closed-form simply-supported beam-column on Winkler foundation."""
    best: Optional[float] = None
    best_m = 0
    loads: List[float] = []
    for m in range(1, n_modes + 1):
        km = m * math.pi / case.L
        p = case.EI * km ** 2 + case.k / km ** 2
        loads.append(p)
        if best is None or p < best:
            best = p
            best_m = m
    return float(best), best_m, loads


def _element_matrices(case: PanelCase, le: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dimensionless Hermite element matrices: elastic, foundation, geometric.

    The assembly is done in non-dimensional form (``x/L``, ``w/L``,
    ``P_hat = P L**2/EI``, ``k_hat = k L**4/EI``) because in SI units the
    elastic and geometric matrices differ by ~1e10 in magnitude, and the
    eigen-solve then floors at ~1e-6 relative error.  That flooring is visible
    as a spurious 1e-6 error plateau in the node study and is *not* a
    discretisation error.
    """
    k_hat = case.k * case.L ** 4 / case.EI
    ke = 1.0 / le ** 3 * np.array([
        [12.0, 6.0 * le, -12.0, 6.0 * le],
        [6.0 * le, 4.0 * le ** 2, -6.0 * le, 2.0 * le ** 2],
        [-12.0, -6.0 * le, 12.0, -6.0 * le],
        [6.0 * le, 2.0 * le ** 2, -6.0 * le, 4.0 * le ** 2],
    ])
    kf = k_hat * le / 420.0 * np.array([
        [156.0, 22.0 * le, 54.0, -13.0 * le],
        [22.0 * le, 4.0 * le ** 2, 13.0 * le, -3.0 * le ** 2],
        [54.0, 13.0 * le, 156.0, -22.0 * le],
        [-13.0 * le, -3.0 * le ** 2, -22.0 * le, 4.0 * le ** 2],
    ])
    kg = 1.0 / (30.0 * le) * np.array([
        [36.0, 3.0 * le, -36.0, 3.0 * le],
        [3.0 * le, 4.0 * le ** 2, -3.0 * le, -1.0 * le ** 2],
        [-36.0, -3.0 * le, 36.0, -3.0 * le],
        [3.0 * le, -1.0 * le ** 2, -3.0 * le, 4.0 * le ** 2],
    ])
    return ke, kf, kg


def buckling_fem(case: PanelCase, n_elem: int) -> Dict[str, float]:
    """Lowest elastic bifurcation load on ``n_elem`` equal Hermite elements."""
    if n_elem < 2:
        raise ValueError("need at least two elements")
    le = 1.0 / n_elem          # dimensionless element length (x_hat = x/L)
    ndof = 2 * (n_elem + 1)
    ke, kf, kg = _element_matrices(case, le)
    K = np.zeros((ndof, ndof))
    Kg = np.zeros((ndof, ndof))
    for e in range(n_elem):
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        for a in range(4):
            for b in range(4):
                K[dofs[a], dofs[b]] += ke[a, b] + kf[a, b]
                Kg[dofs[a], dofs[b]] += kg[a, b]
    # simply supported: w = 0 at both ends, rotations free
    free = [i for i in range(ndof) if i not in (0, ndof - 2)]
    Kff = K[np.ix_(free, free)]
    Kgff = Kg[np.ix_(free, free)]
    # (Kff) phi = P Kgff phi  ->  eigenvalues of Kff^-1 Kgff are 1/P
    a = sla.solve(Kff, Kgff, assume_a="sym")
    lam = np.linalg.eigvals(a)
    lam = lam[np.abs(lam.imag) < 1e-8 * np.max(np.abs(lam) + 1e-30)].real
    lam = lam[np.abs(lam) > 1e-12]
    loads = np.sort(1.0 / lam)
    positive = loads[loads > 0.0]
    if positive.size == 0:  # pragma: no cover
        raise RuntimeError("no positive buckling load found")
    p_hat = float(positive[0])
    p_cr = p_hat * case.EI / case.L ** 2
    return {
        "p_cr": p_cr,
        "p_hat": p_hat,
        "n_elem": n_elem,
        "ndof": ndof,
        "le": le * case.L,
        "sigma_cr": p_cr / case.area,
        "n_positive_modes": int(positive.size),
    }


#: mesh triple (coarse, medium, fine) element counts, r = 2.  The coarse
#: levels (<= 20 elements) are outside the asymptotic range: the buckling
#: eigenvalue error is non-monotone there (measured: 1.6e-3 -> 1.7e-3 -> 3.0e-4),
#: so the GCI study starts at 40 elements.
MESH_TRIPLE = (40, 80, 160)


def run_buckling_study(
    case: PanelCase = DEFAULT_PANEL,
    meshes: Sequence[int] = MESH_TRIPLE,
) -> Dict[str, object]:
    """Three-mesh FEM study against the closed-form buckling load."""
    analytic, mode, loads = analytic_buckling_load(case)
    results = [buckling_fem(case, n) for n in meshes]
    return {
        "case": case.to_dict(),
        "analytic": {
            "p_cr": analytic,
            "mode": mode,
            "sigma_cr": analytic / case.area,
            "euler_k0": math.pi ** 2 * case.EI / case.L ** 2,
            "yield_load": case.sigma_yield * case.area,
            "first_three_modes": loads[:3],
        },
        "meshes": list(meshes),
        "results": results,
        "errors": [abs(r["p_cr"] - analytic) / analytic for r in results],
        "plastic_check": {
            "sigma_cr": analytic / case.area,
            "sigma_yield": case.sigma_yield,
            "elastic_buckling_below_yield": bool(analytic / case.area < case.sigma_yield),
        },
    }