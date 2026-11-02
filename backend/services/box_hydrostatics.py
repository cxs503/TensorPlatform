"""Exact hydrostatic reference for rectangular (wall-sided) barges.

Why this module exists
----------------------
The platform's ``POST /api/v1/calculations/stability`` endpoint returns
KB / BM / GM / GZmax / vanishing angle / IMO areas from an empirical
formulation.  Empirical ship-hull formulae cannot be validated against
themselves, so we need a case with a *closed-form* answer.  A rectangular
barge (box-shaped, ``Cb = 1``, wall-sided sides) is exactly that case:

* displacement          ``∇ = L·B·T``
* centre of buoyancy    ``KB = T/2``                     (exact)
* transverse metacentre ``BM = I_T/∇ = B²/(12·T)``       (exact, ``I_T = L·B³/12``)
* righting arm, wall-sided regime (waterline still cuts both sides and the
  deck edge is not immersed)::

      GZ(φ) = ( GM + ½·BM·tan²φ ) · sinφ          (exact identity for a box)

  This is the classical wall-sided formula; for a box it is *not* an
  approximation -- it follows exactly from the trapezoidal immersed section
  (see ``docs/verification/stability_engine_verification_m1597.md``, §2).

Outside that regime (bilge emergence on the high side, or deck-edge
immersion on the low side) no closed form exists, so the immersed section
is integrated exactly as a clipped polygon (:func:`immersed_section`).
No grid / no discretisation error: the polygon clip is analytic, the only
iteration is a bisection on the waterline offset.

Conventions
-----------
* body axes: ``y`` to starboard, ``z`` up, origin on the keel centreline;
* heel ``φ > 0`` = starboard down;
* the centre of gravity sits on the centreline at height ``KG``;
* the water surface written in body axes is ``z·cosφ − y·sinφ = c``
  (deeper on ``+y``), with ``c`` fixed by ``area(c) = B·T``.

Reference: classical wall-sided stability (e.g. Biran & López-Pulido,
*Ship Hydrostatics and Stability*, §6.3) — no empirical coefficient is
used anywhere in this module.
"""
from __future__ import annotations

import math
from typing import List, Tuple

__all__ = [
    "box_hydrostatics",
    "wallsided_gz",
    "wallsided_regime_limits_deg",
    "immersed_section",
    "box_gz",
    "gz_curve",
    "gz_area",
    "vanishing_angle_deg",
]


# ---------------------------------------------------------------------------
# Base hydrostatics
# ---------------------------------------------------------------------------

def box_hydrostatics(length: float, beam: float, draft: float) -> dict:
    """Exact ``KB``/``BM``/``KM`` of a rectangular barge (``Cb = 1``)."""
    if length <= 0 or beam <= 0 or draft <= 0:
        raise ValueError("length, beam and draft must be positive")
    nabla = length * beam * draft          # m³
    kb = draft / 2.0                       # exact, trapezoid-free
    it = length * beam ** 3 / 12.0         # m⁴
    bm = it / nabla                        # = B²/(12 T)
    return {
        "displacement_m3": nabla,
        "kb_m": kb,
        "bm_m": bm,
        "km_m": kb + bm,
        "it_m4": it,
    }


def wallsided_regime_limits_deg(beam: float, draft: float, depth: float) -> Tuple[float, float]:
    """Heel angles at which the wall-sided assumptions break.

    Returns ``(bilge_emergence_deg, deck_immersion_deg)`` where

    * bilge emergence: ``tanφ = 2·T/B`` (high-side bilge leaves the water);
    * deck immersion : ``tanφ = (D − T)/(B/2)`` (low-side deck edge floods).

    The wall-sided formula is exact for ``0 ≤ φ ≤ min(both)``.
    """
    bilge = math.degrees(math.atan2(2.0 * draft, beam))
    deck = math.degrees(math.atan2(max(depth - draft, 0.0), beam / 2.0))
    return bilge, deck


def wallsided_gz(phi_deg: float, gm: float, bm: float) -> float:
    """Wall-sided righting arm ``(GM + ½·BM·tan²φ)·sinφ`` (exact for a box)."""
    phi = math.radians(phi_deg)
    return (gm + 0.5 * bm * math.tan(phi) ** 2) * math.sin(phi)


# ---------------------------------------------------------------------------
# Exact immersed section (polygon clipping, no grid)
# ---------------------------------------------------------------------------

def _clip_halfplane(poly: List[Tuple[float, float]], normal: Tuple[float, float],
                    offset: float) -> List[Tuple[float, float]]:
    """Sutherland–Hodgman clip of *poly* to ``n·x <= offset``."""
    ny, nz = normal
    if not poly:
        return []

    def f(p):
        return ny * p[0] + nz * p[1] - offset

    out: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        fc, fn = f(cur), f(nxt)
        if fc <= 0.0:
            out.append(cur)
        if (fc < 0.0 and fn > 0.0) or (fc > 0.0 and fn < 0.0):
            t = fc / (fc - fn)
            out.append((cur[0] + t * (nxt[0] - cur[0]),
                        cur[1] + t * (nxt[1] - cur[1])))
    return out


def _poly_area_centroid(poly: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    """Signed area and centroid of a simple polygon (shoelace)."""
    if len(poly) < 3:
        return 0.0, 0.0, 0.0
    a = 0.0
    cy = 0.0
    cz = 0.0
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    a *= 0.5
    if abs(a) < 1e-300:
        return 0.0, 0.0, 0.0
    return abs(a), cy / (6.0 * a), cz / (6.0 * a)


def immersed_section(beam: float, draft: float, depth: float, phi_deg: float,
                     tol: float = 1e-13, max_iter: int = 200) -> Tuple[float, float, float]:
    """Immersed section area and its centroid for a heeled box.

    Solves the waterline offset ``c`` such that the immersed area equals the
    upright displacement area ``B·T`` (hydrostatic equilibrium), then returns
    ``(area, y_B, z_B)`` in body axes.
    """
    phi = math.radians(phi_deg)
    cph, sph = math.cos(phi), math.sin(phi)
    half = beam / 2.0
    rect = [(-half, 0.0), (half, 0.0), (half, depth), (-half, depth)]
    target = beam * draft

    def area_at(c: float) -> Tuple[float, float, float]:
        poly = _clip_halfplane(rect, (-sph, cph), c)
        return _poly_area_centroid(poly)

    # bracket the offset: full rectangle area is B·D > B·T, empty below keel
    lo, hi = -1.0 * (beam + depth), 2.0 * (beam + depth)
    a_lo, _, _ = area_at(lo)
    a_hi, _, _ = area_at(hi)
    if abs(target) < 1e-15:
        return 0.0, 0.0, 0.0
    if a_hi < target or a_lo > target:
        raise ValueError("requested displacement area not attainable for this B/D/T")

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        a, yb, zb = area_at(mid)
        if a < target:
            lo = mid
        else:
            hi = mid
        if abs(a - target) <= tol * target:
            return a, yb, zb
    a, yb, zb = area_at(0.5 * (lo + hi))
    return a, yb, zb


def box_gz(phi_deg: float, beam: float, draft: float, depth: float, kg: float) -> float:
    """Exact righting arm of a box barge at heel *phi_deg* (any angle).

    ``GZ = y_B·cosφ + (z_B − KG)·sinφ`` in world-horizontal components.
    """
    _, yb, zb = immersed_section(beam, draft, depth, phi_deg)
    phi = math.radians(phi_deg)
    return yb * math.cos(phi) + (zb - kg) * math.sin(phi)


def gz_curve(angles_deg, beam: float, draft: float, depth: float, kg: float) -> List[float]:
    return [box_gz(a, beam, draft, depth, kg) for a in angles_deg]


# ---------------------------------------------------------------------------
# Derived IMO quantities
# ---------------------------------------------------------------------------

def gz_area(angles_deg, gz_values, start: float, end: float) -> float:
    """Area under the GZ curve between *start* and *end* degrees (Simpson).

    The sample set must cover ``[start, end]`` with a uniform step; a
    composite Simpson rule is used (even number of intervals required,
    otherwise the last interval falls back to a trapezoid).
    """
    if len(angles_deg) != len(gz_values) or len(angles_deg) < 3:
        raise ValueError("need matching angles/GZ arrays with >= 3 samples")
    step = angles_deg[1] - angles_deg[0]
    idx = [i for i, a in enumerate(angles_deg) if start <= a <= end]
    if len(idx) < 3:
        # single-interval fallback
        i0 = min(range(len(angles_deg)), key=lambda i: abs(angles_deg[i] - start))
        i1 = min(range(len(angles_deg)), key=lambda i: abs(angles_deg[i] - end))
        return 0.5 * (gz_values[i0] + gz_values[i1]) * math.radians(end - start)
    i0, i1 = idx[0], idx[-1]
    total = 0.0
    i = i0
    while i + 2 <= i1:
        total += (gz_values[i] + 4.0 * gz_values[i + 1] + gz_values[i + 2]) * (math.radians(step) / 3.0)
        i += 2
    if i < i1:  # odd tail
        total += 0.5 * (gz_values[i] + gz_values[i + 1]) * math.radians(step)
    return total


def vanishing_angle_deg(beam: float, draft: float, depth: float, kg: float,
                        max_angle: float = 120.0, step: float = 0.25) -> float:
    """First heel angle where the exact GZ becomes non-positive after its peak.

    Returns ``max_angle`` when the curve stays positive up to ``max_angle``
    (reported explicitly so a censored value is never mistaken for a
    computed one).
    """
    prev_pos = True
    a = step
    while a <= max_angle + 1e-9:
        gz = box_gz(a, beam, draft, depth, kg)
        if prev_pos and gz <= 0.0:
            lo, hi = a - step, a
            for _ in range(80):
                mid = 0.5 * (lo + hi)
                if box_gz(mid, beam, draft, depth, kg) > 0.0:
                    lo = mid
                else:
                    hi = mid
            return 0.5 * (lo + hi)
        prev_pos = gz > 0.0
        a += step
    return max_angle