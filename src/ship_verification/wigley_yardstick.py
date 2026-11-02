"""Independent thin-ship / hydrostatic yardstick for the Wigley parabolic hull.

Why this module exists
----------------------
The repository's only *analytic* wave-resistance reference was built by m1568 in
``docs/verification_debt/wigley_michell`` (Michell integral for the Wigley hull,
closed-form inner integrals).  That package shipped with a second, nominally
independent route (``rw_double_quad``) whose cross-route agreement was never
gated by a test -- and the route was in fact broken:

* the complex amplitude was cast with ``float(...)`` (``ComplexWarning: Casting
  complex values to real discards the imaginary part``), and the x-integrand is
  antisymmetric, so the surviving real part cancelled to ``Rw ~ 1e-28 N``;
* a spurious mirror factor of 2 was applied to the amplitude (=> 4x in ``Rw``)
  while the prefactor already carries the two-sided weight;
* a left-over ``phase = ... ; del phase`` placeholder computed a value that was
  thrown away.

So the "independent route" check that the m1568 ledger relied on was vacuous.
This module exposes the repaired yardstick plus a set of *normalization-free*
verdicts, so the ship platform can be re-audited without any hand-copied number.

Reference hierarchy (strongest first)
-------------------------------------
1. **Exact closed forms** (no discretisation): Wigley ``Cb = 4/9``,
   ``nabla = 4 L B T / 9``; rectangular pontoon ``KB = T/2``,
   ``BM = B^2/(12T)``, wall-sided ``GZ(phi) = sin(phi)(GM + BM/2 tan^2 phi)``.
   Wetted surface by Gauss-Legendre quadrature (spectral convergence, checked).
2. **Exact structural laws of the Michell integral** (scale free, and therefore
   independent of the convention constant):
   ``Rw  prop  B^2`` at fixed ``L, T, U``, and Froude scaling
   ``Rw(lambda L, lambda B, lambda T) = lambda^3 Rw`` at fixed ``Fn``.
3. **Two numerical routes** of the same integral must agree: closed-form inner
   integrals + theta quadrature (route A) vs 2-D surface quadrature (route B).

Known limitation (honest scope)
-------------------------------
The **absolute** prefactor of the Michell integral is a convention constant.
With the half-breadth derivative taken over ONE side of the hull, the classical
prefactor is ``4 rho g^2/(pi U^2)``; the repository code uses
``8 rho g^2/(pi U^2)`` (i.e. 2x, equivalently 4x on the amplitude), which is what
m1568's ledger numbers use.  No offline, citable table was available to settle
the convention, so platform-vs-reference wave-resistance verdicts here are
restricted to scale-free statistics (see :func:`normalization_span`).  Nothing
below claims better than WITHHELD evidence for the absolute ``Cw`` magnitude.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

__all__ = [
    "G",
    "RHO",
    "NU",
    "KNOT",
    "REF_DIR",
    "Wigley",
    "rw_route_a",
    "rw_route_b",
    "michell_cw_curve",
    "michell_cw",
    "exact_wigley_wetted_surface",
    "exact_wigley_cb",
    "exact_wigley_volume",
    "pontoon_hydrostatics",
    "pontoon_wall_sided_gz",
    "pontoon_wall_sided_valid_deg",
    "ittc57_cf",
    "implied_wetted_area",
    "normalization_span",
    "verdict",
]

G = 9.81
RHO = 1025.0
NU = 1.19e-6
KNOT = 0.5144444444444445

REF_DIR = (Path(__file__).resolve().parents[2] / "docs" / "verification_debt"
           / "wigley_michell")
if str(REF_DIR) not in sys.path:
    sys.path.insert(0, str(REF_DIR))

from michell_wigley import Wigley  # noqa: E402
from michell_wigley import rw_double_quad, rw_theta_integral, wetted_surface  # noqa: E402

#: Standard thin-ship Wigley: L/B = 10, B/T = 1.6, Cb = 4/9 (m1568 convention).
STANDARD_L, STANDARD_B, STANDARD_T = 100.0, 10.0, 6.25


# ---------------------------------------------------------------------------
# 1. Exact closed forms
# ---------------------------------------------------------------------------
def exact_wigley_cb() -> float:
    """Exact block coefficient of the Wigley parabolic hull: 4/9."""
    return 4.0 / 9.0


def exact_wigley_volume(L: float, B: float, T: float) -> float:
    """Exact displacement volume 4 L B T / 9 [m^3]."""
    return 4.0 * L * B * T / 9.0


def exact_wigley_wetted_surface(
    L: float = STANDARD_L, B: float = STANDARD_B, T: float = STANDARD_T,
    n_x: int = 800, n_z: int = 320,
) -> float:
    """Wetted surface [m^2] by spectral (Gauss-Legendre) quadrature.

    Smooth integrand on a rectangle => machine-precision convergence; the value
    is pinned by a convergence test, so it behaves as an exact reference here.
    """
    return float(wetted_surface(Wigley(L=L, B=B, T=T), n_x=n_x, n_z=n_z))


def pontoon_hydrostatics(B: float, T: float, KG: float) -> dict:
    """Exact wall-sided rectangular pontoon (Cb = Cm = 1)."""
    KB = 0.5 * T
    BM = B * B / (12.0 * T)
    KM = KB + BM
    return {"KB": KB, "BM": BM, "KM": KM, "GM": KM - KG}


def pontoon_wall_sided_valid_deg(B: float, T: float) -> float:
    """Dip of the deck edge (upper limit of the wall-sided GZ formula) [deg]."""
    return math.degrees(math.atan(2.0 * T / B))


def pontoon_wall_sided_gz(B: float, T: float, KG: float, phi_deg: float) -> float:
    """Exact GZ [m] while the deck edge stays immersed and the bilge submerged."""
    h = pontoon_hydrostatics(B, T, KG)
    phi = math.radians(phi_deg)
    return math.sin(phi) * (h["GM"] + 0.5 * h["BM"] * math.tan(phi) ** 2)


def ittc57_cf(re: float) -> float:
    """ITTC-1957 friction line Cf = 0.075/(log10 Re - 2)^2."""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def implied_wetted_area(Rf: float, U: float, re: float, rho: float = RHO) -> float:
    """Wetted area implied by a reported friction resistance, given ITTC-57."""
    return Rf / (0.5 * rho * ittc57_cf(re) * U * U)


# ---------------------------------------------------------------------------
# 2. Michell routes / reference curve
# ---------------------------------------------------------------------------
def rw_route_a(U: float, hull: Wigley | None = None, n_theta: int = 320) -> float:
    """Michell wave resistance [N], closed-form inner integrals (route A)."""
    h = hull if hull is not None else Wigley(L=STANDARD_L, B=STANDARD_B, T=STANDARD_T, rho=RHO)
    return float(rw_theta_integral(h, U, n_theta=n_theta))


def rw_route_b(
    U: float,
    hull: Wigley | None = None,
    n_x: int = 80,
    n_z: int = 60,
    n_theta: int = 96,
) -> float:
    """Michell wave resistance [N], 2-D surface quadrature (independent route B)."""
    h = hull if hull is not None else Wigley(L=STANDARD_L, B=STANDARD_B, T=STANDARD_T, rho=RHO)
    return float(rw_double_quad(h, U, n_x=n_x, n_z=n_z, n_theta=n_theta))


def michell_cw(U: float, L: float = STANDARD_L, B: float = STANDARD_B,
               T: float = STANDARD_T, s_ref: float | None = None) -> float:
    """Michell wave-resistance coefficient on 0.5 rho S U^2."""
    s = exact_wigley_wetted_surface(L, B, T) if s_ref is None else s_ref
    return rw_route_a(U, Wigley(L=L, B=B, T=T, rho=RHO)) / (0.5 * RHO * s * U * U)


def michell_cw_curve(
    fns, L: float = STANDARD_L, B: float = STANDARD_B, T: float = STANDARD_T,
    n_theta: int = 320,
) -> list[dict]:
    """Reference Cw(Fn) curve plus the theta-refinement increment."""
    hull = Wigley(L=L, B=B, T=T, rho=RHO)
    s = exact_wigley_wetted_surface(L, B, T)
    rows = []
    for fn in fns:
        U = float(fn) * math.sqrt(G * L)
        rw1 = float(rw_theta_integral(hull, U, n_theta=n_theta))
        rw2 = float(rw_theta_integral(hull, U, n_theta=2 * n_theta))
        rows.append({
            "Fn": float(fn),
            "U_ms": U,
            "Rw_ref_N": rw2,
            "cw_ref": rw2 / (0.5 * RHO * s * U * U),
            "theta_refine_rel": abs(rw1 - rw2) / max(abs(rw2), 1e-300),
            "rw_route_b_N": None,
        })
    return rows


# ---------------------------------------------------------------------------
# 3. Verdict rule engine
# ---------------------------------------------------------------------------
def verdict(name: str, measured, reference, tol: float, *, basis: str = "",
            note: str = "", lower_bound_only: bool = False) -> dict:
    """Turn a measurement into a machine-checkable verdict record.

    ``tol`` is a *relative* tolerance unless ``note`` says otherwise.  A missing
    or non-finite measurement is an ERROR, not a silent PASS.
    """
    rec = {"id": name, "measured": measured, "reference": reference, "tol": tol,
           "basis": basis, "note": note}
    if measured is None or isinstance(measured, bool):
        rec.update(rel_err=None, status="ERROR")
        return rec
    try:
        m = float(measured)
        r = float(reference)
    except (TypeError, ValueError):
        rec.update(rel_err=None, status="ERROR")
        return rec
    if not math.isfinite(m):
        rec.update(rel_err=None, status="ERROR")
        return rec
    if r == 0.0:
        rel = 0.0 if m == 0.0 else math.inf
    else:
        rel = abs(m - r) / abs(r)
    rec["rel_err"] = rel
    ok = (m >= r) if lower_bound_only else (rel <= tol)
    rec["status"] = "PASS" if ok else "FAIL"
    return rec


def normalization_span() -> dict:
    """Documented convention ambiguity of the Michell prefactor.

    ``Cw``/``Rw`` from this repository are scale-free correct but their absolute
    magnitude carries a factor-2 convention constant (one-sided half-breadth
    amplitude with 8 rho g^2/(pi U^2) vs the classical 4 rho g^2/(pi U^2)).
    """
    return {
        "prefactor_in_code": 8.0 / math.pi,
        "prefactor_classical_one_side": 4.0 / math.pi,
        "rw_scale_range": [0.5, 1.0],
        "cw_scale_range": [0.5, 1.0],
        "evidence": "WITHHELD",
        "why": ("no offline citable table for absolute Wigley Rw; the two "
                "printed conventions differ by a factor 2 in Rw. Scale-free "
                "verdicts (Froude invariance, ratios, Fn-shape) are unaffected."),
    }