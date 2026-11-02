"""Closed-form references for the rectangular pontoon (wall-sided) stability gate.

Why a pontoon
-------------
A rectangular pontoon with ``Cb = Cm = Cwp = 1`` is the only hull form for which
*every* intact-stability quantity the ship-performance platform reports has a
textbook closed form, so the platform can be checked without using any of its
own intermediate results:

    displacement volume   vol   = L B T
    centre of buoyancy    KB    = T / 2                      (0 < phi < 90 deg)
    waterplane inertia    I     = L B^3 / 12                 (exact, box plan)
    metacentric radius    BM    = I / vol = B^2 / (12 T)
    transverse metacentre KM    = KB + BM
    metacentric height    GM    = KM - KG
    righting arm (wall-sided, deck edge still immersed)

        GZ(phi) = sin(phi) * ( GM + (BM/2) * tan^2(phi) )

    valid until the deck edge enters the water, i.e. until
        phi_edge = atan( 2 T / B )        (B/2 * tan(phi) = D - T  =>  with D=2T)

    Beyond ``phi_edge`` the immersed section is no longer a full rectangle and
    the closed form above stops applying; the reference therefore stops there
    instead of pretending to know the curve.

References: Rawson & Tupper, *Basic Ship Theory* (5th ed.), ch. 4-5;
Biran & López-Pulido, *Ship Hydrostatics and Stability* (2nd ed.), §4.2-4.6.

Everything here is exact arithmetic on the input geometry - no fitted
coefficient, no platform output involved.
"""

from __future__ import annotations

import math

G = 9.81


def pontoon_analytic(L: float, B: float, T: float, KG: float) -> dict:
    """Exact hydrostatics of a box-shaped pontoon (L x B x T, wall-sided)."""
    vol = L * B * T
    KB = T / 2.0
    I_wp = L * B ** 3 / 12.0
    BM = I_wp / vol
    KM = KB + BM
    GM = KM - KG
    phi_edge = math.degrees(math.atan(2.0 * T / B))  # deck at D = 2T
    return {
        "volume_m3": vol,
        "KB": KB,
        "I_waterplane_m4": I_wp,
        "BM": BM,
        "KM": KM,
        "GM": GM,
        "deck_edge_deg": phi_edge,
    }


def gz_wall_sided(phi_deg: float, GM: float, BM: float) -> float:
    """Righting arm of a wall-sided pontoon, valid while deck edge is immersed."""
    phi = math.radians(phi_deg)
    return math.sin(phi) * (GM + 0.5 * BM * math.tan(phi) ** 2)


def gz_curve(L: float, B: float, T: float, KG: float, step: float = 1.0) -> list[dict]:
    """Reference GZ curve up to (and including) the deck-edge immersion angle."""
    h = pontoon_analytic(L, B, T, KG)
    out = []
    phi = 0.0
    while phi <= h["deck_edge_deg"] + 1e-9:
        out.append({"phi_deg": round(phi, 6),
                    "GZ": gz_wall_sided(phi, h["GM"], h["BM"])})
        phi += step
    return out


def area_under(curve: list[dict], phi_hi: float) -> float:
    """Trapezoidal integral of GZ dphi (radians) between 0 and ``phi_hi`` deg.

    Linear interpolation is used at ``phi_hi`` so the value does not depend on
    where the sampling happened to stop.
    """
    pts = [(0.0, 0.0)] + [(c["phi_deg"], c["GZ"]) for c in curve]
    acc = 0.0
    for (a1, g1), (a2, g2) in zip(pts, pts[1:]):
        if a2 <= phi_hi:
            acc += 0.5 * (g1 + g2) * math.radians(a2 - a1)
            continue
        if a1 < phi_hi:
            t = (phi_hi - a1) / (a2 - a1)
            g = g1 + t * (g2 - g1)
            acc += 0.5 * (g1 + g) * math.radians(phi_hi - a1)
        break
    return acc


def rel_error(value: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return (value - reference) / reference