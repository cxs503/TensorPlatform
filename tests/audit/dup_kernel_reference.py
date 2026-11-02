"""Independent (platform-code-free) reference implementations for the
duplicate-kernel divergence audit (m1873).

Every function here is written from the textbook / rule definition and imports
**nothing** from ``backend/`` — that is what makes it usable as a baseline
against which the platform's own kernels are graded.

Contents
--------
``ittc57_cf``              ITTC-1957 model-ship correlation line (definition).
``box_wetted_surface``     Exact wetted area of a floating rectangular barge.
``box_hydrostatics``       Exact KB / BM / KM of a rectangular section.
``box_gz_exact``           Exact righting-arm of a rectangular section at heel,
                           by piecewise-analytic section integration + bisection
                           on the waterline constant (no polygon clipping, no
                           discretisation error).
``holtrop1982``            Holtrop & Mennen (1982) wetted-surface regression,
                           re-typed from the published formula.
``denny_mumford``          Denny-Mumford wetted-surface approximation.
"""

from __future__ import annotations

import math

G = 9.81
RHO_FRESH = 998.0
NU_FRESH = 1.004e-6


# ---------------------------------------------------------------------------
# 1. Friction line
# ---------------------------------------------------------------------------
def ittc57_cf(reynolds: float) -> float:
    """Cf = 0.075 / (log10(Re) - 2)^2  (ITTC-1957 model-ship correlation line)."""
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


def froude_number(speed_ms: float, length: float) -> float:
    return speed_ms / math.sqrt(G * length)


# ---------------------------------------------------------------------------
# 2. Canonical body: wetted area / hydrostatics (exact, 0 % reference)
# ---------------------------------------------------------------------------
def box_wetted_surface(L: float, B: float, T: float) -> float:
    """Floating rectangular barge: bottom + 2 sides + 2 ends (deck not wetted).

        S = L*B + 2*T*(L + B)
    """
    return L * B + 2.0 * T * (L + B)


def cylinder_wetted_surface(L: float, B: float) -> float:
    """Submerged horizontal cylinder, R = B/2, end caps included."""
    r = 0.5 * B
    return 2.0 * math.pi * r * L + 2.0 * math.pi * r * r


def sphere_wetted_surface(B: float) -> float:
    r = 0.5 * B
    return 4.0 * math.pi * r * r


def capsule_wetted_surface(L: float, B: float) -> float:
    r = 0.5 * B
    return 2.0 * math.pi * r * L + 4.0 * math.pi * r * r


def box_hydrostatics(B: float, T: float, KG: float) -> dict:
    """Exact metacentric quantities of a rectangular (wall-sided) section.

        KB = T/2          BM = I / nabla = (B^3 L / 12) / (L B T) = B^2/(12 T)
        KM = KB + BM      GM = KM - KG
    """
    kb = 0.5 * T
    bm = B * B / (12.0 * T)
    km = kb + bm
    return {"kb": kb, "bm": bm, "km": km, "gm": km - KG}


# ---------------------------------------------------------------------------
# 3. Exact righting arm of a rectangular section
# ---------------------------------------------------------------------------
def _submerged_moments(z0: float, B: float, depth: float, t: float):
    """Area, first moment in y and z-integral of the submerged region.

    Body frame: y in [-B/2, B/2], z in [0, depth]; free surface in body frame is
    the line ``z = z0 + t*y`` (t = tan(heel)).  The three y-bands
    (dry / partially submerged / fully submerged) are integrated analytically, so
    the result carries no discretisation error.
    """
    y1, y2 = -0.5 * B, 0.5 * B
    ya = -z0 / t                    # h = 0
    yb = (depth - z0) / t           # h = depth
    area = 0.0
    my = 0.0
    mz = 0.0
    # band 2: partially submerged, clipped to [y1, y2]
    a2, b2 = max(y1, ya), min(y2, yb)
    if b2 > a2:
        da, db = b2 - a2, b2 * b2 - a2 * a2
        dc, dd = b2 ** 3 - a2 ** 3, b2 ** 4 - a2 ** 4
        area += z0 * da + 0.5 * t * db
        my += 0.5 * z0 * db + (t / 3.0) * dc
        # int h^2/2 dy with h = z0 + t y
        mz += 0.5 * (z0 * z0 * da + z0 * t * db + (t * t / 3.0) * dc)
        _ = dd
    # band 3: fully submerged, y in [yb, y2]
    a3, b3 = max(y1, yb), y2
    if b3 > a3:
        w = b3 - a3
        area += depth * w
        my += 0.5 * depth * (b3 * b3 - a3 * a3)
        mz += 0.5 * depth * depth * w
    return area, my, mz


def box_gz_exact(B: float, T: float, depth: float, KG: float, phi_deg: float,
                 iters: int = 200) -> float:
    """Exact GZ of a rectangular section at heel ``phi_deg`` (degrees).

    Constant displacement (area = B*T) is enforced by bisecting the waterline
    constant ``z0``; the buoyancy centre follows from the analytic section
    moments, then ``GZ = Y_B - KG*sin(phi)`` in the world frame.
    """
    phi = math.radians(phi_deg)
    if abs(phi) < 1e-12:
        return 0.0
    t = math.tan(phi)
    target = B * T
    lo, hi = -abs(t) * B - depth, abs(t) * B + depth
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        a, _, _ = _submerged_moments(mid, B, depth, t)
        if a < target:
            lo = mid
        else:
            hi = mid
    z0 = 0.5 * (lo + hi)
    area, my, mz = _submerged_moments(z0, B, depth, t)
    y_bar = my / area
    z_bar = mz / area
    c, s = math.cos(phi), math.sin(phi)
    y_b = y_bar * c + z_bar * s
    return y_b - KG * s


def box_gz_wallsided(GM: float, BM: float, phi_deg: float) -> float:
    """Wall-sided closed form  GZ = sin(phi) * (GM + 0.5*BM*tan^2(phi))."""
    phi = math.radians(phi_deg)
    return math.sin(phi) * (GM + 0.5 * BM * math.tan(phi) ** 2)


# ---------------------------------------------------------------------------
# 4. Wetted-surface regressions (literature)
# ---------------------------------------------------------------------------
def holtrop1982(L: float, B: float, T: float, cb: float, cm: float, cwp: float,
                abt: float = 0.0) -> float:
    """Holtrop & Mennen (1982), outboard stem, no bulb (ABT = 0).

        S = L(2T+B) sqrt(Cm) (0.453 + 0.4425Cb - 0.2862Cm
                              - 0.003467 B/T + 0.3696Cwp) + 2.38 ABT/Cb
    """
    inner = (0.453 + 0.4425 * cb - 0.2862 * cm
             - 0.003467 * (B / T) + 0.3696 * cwp)
    return L * (2.0 * T + B) * math.sqrt(cm) * inner + 2.38 * abt / cb


def cwp_schneekluth(cb: float) -> float:
    """Standard waterplane-coefficient estimate Cwp = (1 + 2Cb)/3."""
    return (1.0 + 2.0 * cb) / 3.0


def denny_mumford(L: float, B: float, T: float, cb: float) -> float:
    """Denny-Mumford (SNAME) approximation  S = L(1.7T + Cb*B)."""
    return L * (1.7 * T + cb * B)


def axisym_surface(L: float, B: float, cb: float) -> float:
    """Legacy axisymmetric estimate  S = pi*B*L*(0.5 + 0.25Cb)."""
    return math.pi * B * L * (0.5 + 0.25 * cb)


# ---------------------------------------------------------------------------
# 5. Resistance decomposition (reference chain, ITTC-57 + the platform's
#    documented Fn^2.5 residual fit re-typed from the docstring)
# ---------------------------------------------------------------------------
def resistance_chain(speed_ms: float, L: float, B: float, cb: float,
                     S: float, rho: float = RHO_FRESH, nu: float = NU_FRESH) -> dict:
    q = 0.5 * rho * S * speed_ms ** 2
    re = speed_ms * L / nu
    fn = froude_number(speed_ms, L)
    cf = ittc57_cf(re)
    cr = 0.002 * (1.0 + 2.0 * cb) * fn ** 2.5 if fn > 0.05 else 0.0
    return {
        "q": q, "re": re, "fn": fn, "cf": cf, "cr": cr,
        "rf": q * cf, "rr": q * cr, "rt": q * (cf + cr),
        "ct": cf + cr,
    }