"""Independent reference hydrostatics for righting-arm (GZ) verification.

Why this module exists
----------------------
``/calculations/stability`` evaluates the classic **wall-sided** closed form

    GZ(phi) = sin(phi) * (GM + 0.5 * BM * tan(phi)**2)

That expression is an *analytic identity*, not an empirical fit: it is exact
while the immersed section stays a parallel-sided trapezoid, i.e. as long as
neither the deck edge immerses nor a bilge leaves the water.  It therefore
cannot be verified by re-evaluating the same formula; it has to be compared
against an **independently integrated** hydrostatic solution.

This module provides that independent solution for the only geometry with an
exact answer: a **rectangular box barge** (vertical sides, flat bottom, full
waterplane, moulded depth ``D > T`` so there is freeboard to heel into).  The
immersed section is obtained by clipping the heeled rectangle against a
horizontal waterline and solving for the sinkage that conserves displacement
(constant-volume heel, no trim).  Area and centroid come from the
shoelace/centroid formula, so the result is exact to floating-point precision
and shares no code path with the wall-sided form.

Upright hydrostatics of the box (used as the wall-sided inputs)::

    KB = T / 2                 BM = B**2 / (12 * T)                 GM = KB + BM - KG

Consequences proved numerically in
``backend/tests/test_m1386_stability_gz_validation.py``:

* the wall-sided form reproduces the exact box GZ to < 1e-9 m for
  ``|phi| < atan(2*min(T, D - T) / B)`` -- i.e. the shipped implementation is
  the correct analytic solution *inside* its domain (this is the phase-1
  verification of the merged feature);
* beyond that angle it diverges and ultimately grows without bound
  (``tan(phi)**2``), while the true righting arm flattens out towards a finite
  maximum and then decreases -- so any stability number (GZ at 30/40 deg,
  IS-Code areas A30/A40, vanishing angle) computed from the closed form above
  the limit is not merely inaccurate, it is unphysical.

Sign convention: heel is positive to starboard, the body rotates by ``+phi``
about the longitudinal axis through the keel line, and the righting arm is the
horizontal offset between the centre of gravity and the centre of buoyancy,
``GZ = y_B - y_G`` (positive = restoring).  ``GZ(0) = 0`` and ``GZ ~ GM*phi``
for small angles, which the numeric solution reproduces.

Reference for the wall-sided identity and its validity range: standard
naval-architecture stability texts (e.g. Biran & Lopez-Pulido, *Ship
Hydrostatics and Stability*, ch. 6; Rawson & Tupper, *Basic Ship Theory*).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

__all__ = [
    "box_parameters",
    "exact_gz_box",
    "wall_sided_gz",
    "wall_sided_limit_deg",
    "gz_error_envelope",
    "max_true_gz",
]


# ---------------------------------------------------------------------------
# Upright hydrostatics of a rectangular box barge
# ---------------------------------------------------------------------------

def box_parameters(beam_m: float, draft_m: float, kg_m: float,
                   depth_m: Optional[float] = None) -> Dict[str, float]:
    """Upright hydrostatics of a rectangular box barge.

    ``KB = T/2`` (centroid of a rectangle), ``BM = B**2/(12*T)`` (waterplane
    second moment ``B**3*L/12`` divided by displaced volume ``B*T*L``),
    ``GM = KB + BM - KG``.  ``depth_m`` defaults to ``1.5 * T``.
    """
    if beam_m <= 0 or draft_m <= 0:
        raise ValueError("beam_m and draft_m must be positive")
    depth = 1.5 * draft_m if depth_m is None else depth_m
    if depth <= draft_m:
        raise ValueError("depth_m must exceed draft_m (freeboard required to heel into)")
    kb = draft_m / 2.0
    bm = beam_m ** 2 / (12.0 * draft_m)
    return {
        "beam_m": beam_m,
        "draft_m": draft_m,
        "depth_m": depth,
        "kg_m": kg_m,
        "kb_m": kb,
        "bm_m": bm,
        "gm_m": kb + bm - kg_m,
    }


def _rotate(y: float, z: float, phi: float) -> Tuple[float, float]:
    """Rotate a body-fixed point by ``+phi`` (see module docstring)."""
    c, s = math.cos(phi), math.sin(phi)
    return y * c + z * s, -y * s + z * c


def _clip_below(poly: List[Tuple[float, float]], zeta: float) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman clip of a polygon against the half plane ``z <= zeta``."""
    out: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        in0, in1 = z0 <= zeta, z1 <= zeta
        if in0:
            out.append((y0, z0))
        if in0 != in1:
            t = (zeta - z0) / (z1 - z0) if z1 != z0 else 0.0
            out.append((y0 + t * (y1 - y0), zeta))
    return out


def _area_centroid(poly: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    """Area and centroid of a simple polygon (shoelace formula)."""
    n = len(poly)
    if n < 3:
        return 0.0, 0.0, 0.0
    a2 = 0.0
    cy = 0.0
    cz = 0.0
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a2 += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    pixel = a2 / 2.0
    if abs(a2) < 1e-15:
        return 0.0, 0.0, 0.0
    return abs(pixel), cy / (3.0 * a2), cz / (3.0 * a2)


def _immersed_section(beam_m: float, draft_m: float, depth_m: float, phi: float,
                      iters: int = 200) -> Tuple[float, float, float, float]:
    """Immersed section of a heeled box: ``(area, y_B, z_B, waterline z)``."""
    b2 = beam_m / 2.0
    body = [(-b2, 0.0), (b2, 0.0), (b2, depth_m), (-b2, depth_m)]
    poly = [_rotate(y, z, phi) for (y, z) in body]
    target = beam_m * draft_m  # constant displacement, no trim (uniform along L)

    zs = [p[1] for p in poly]
    lo, hi = min(zs) - 1.0, max(zs) + 1.0
    zeta = 0.0
    for _ in range(iters):
        zeta = 0.5 * (lo + hi)
        area, _, _ = _area_centroid(_clip_below(poly, zeta))
        if area < target:
            lo = zeta
        else:
            hi = zeta
    area, y_b, z_b = _area_centroid(_clip_below(poly, zeta))
    return area, y_b, z_b, zeta


# ---------------------------------------------------------------------------
# Public verification helpers
# ---------------------------------------------------------------------------

def exact_gz_box(beam_m: float, draft_m: float, kg_m: float, phi_rad: float,
                 depth_m: Optional[float] = None) -> Dict[str, float]:
    """Exact righting arm of a rectangular box barge at heel ``phi_rad``.

    Constant-displacement heel: sinkage solved so the immersed area equals
    ``B*T`` (no trim, box uniform along its length).  ``GZ`` is the horizontal
    distance from G to the vertical through B.
    """
    depth = 1.5 * draft_m if depth_m is None else depth_m
    area, y_b, z_b, zeta = _immersed_section(beam_m, draft_m, depth, phi_rad)
    y_g, z_g = _rotate(0.0, kg_m, phi_rad)
    return {
        "heel_deg": math.degrees(phi_rad),
        "immersed_area_m2": area,
        "yb_m": y_b,
        "zb_m": z_b,
        "yg_m": y_g,
        "zg_m": z_g,
        "waterline_z_m": zeta,
        "gz_m": y_b - y_g,
    }


def wall_sided_gz(gm_m: float, bm_m: float, phi_rad: float) -> float:
    """The closed form currently shipped by ``/calculations/stability``."""
    return gm_m * math.sin(phi_rad) + 0.5 * bm_m * math.tan(phi_rad) ** 2 * math.sin(phi_rad)


def wall_sided_limit_deg(beam_m: float, draft_m: float,
                         depth_m: Optional[float] = None) -> float:
    """Upper heel limit of the wall-sided identity for a box section.

    Whatever is hit first of

    * **bilge emergence** (low side leaves the water): ``tan(phi) = 2T/B``
    * **deck-edge immersion** (high side submerges):   ``tan(phi) = 2(D-T)/B``

    At that angle the immersed section stops being a parallel-sided trapezoid,
    the waterplane width starts to shrink, ``BM`` is no longer constant and the
    closed form loses its exactness.
    """
    if beam_m <= 0 or draft_m <= 0:
        raise ValueError("beam_m and draft_m must be positive")
    depth = 1.5 * draft_m if depth_m is None else depth_m
    freeboard = depth - draft_m
    if freeboard <= 0:
        raise ValueError("depth_m must exceed draft_m")
    return math.degrees(math.atan(2.0 * min(draft_m, freeboard) / beam_m))


def gz_error_envelope(beam_m: float, draft_m: float, kg_m: float,
                      heel_deg_seq: Optional[List[float]] = None,
                      depth_m: Optional[float] = None) -> List[Dict[str, float]]:
    """Compare wall-sided GZ against the exact box solution over heel angles."""
    if heel_deg_seq is None:
        heel_deg_seq = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0,
                        60.0, 70.0, 80.0, 89.0]
    par = box_parameters(beam_m, draft_m, kg_m, depth_m)
    rows: List[Dict[str, float]] = []
    for hd in heel_deg_seq:
        phi = math.radians(hd)
        exact = exact_gz_box(beam_m, draft_m, kg_m, phi, par["depth_m"])["gz_m"]
        wall = wall_sided_gz(par["gm_m"], par["bm_m"], phi)
        rows.append({
            "heel_deg": hd,
            "gz_exact_m": exact,
            "gz_wall_sided_m": wall,
            "abs_err_m": wall - exact,
            "rel_err_pct": (wall - exact) / abs(exact) * 100.0 if abs(exact) > 1e-9 else float("nan"),
        })
    return rows


def max_true_gz(beam_m: float, draft_m: float, kg_m: float,
                depth_m: Optional[float] = None, step_deg: float = 0.25) -> Dict[str, float]:
    """Maximum exact righting arm and the heel where it occurs (GZmax / phi_GZmax)."""
    depth = 1.5 * draft_m if depth_m is None else depth_m
    best_gz, best_deg, prev = -1e9, 0.0, None
    hd = 0.0
    while hd <= 90.0 + 1e-9:
        gz = exact_gz_box(beam_m, draft_m, kg_m, math.radians(hd), depth)["gz_m"]
        if gz > best_gz:
            best_gz, best_deg = gz, hd
        prev = gz
        hd += step_deg
    return {"gz_max_m": best_gz, "heel_at_gz_max_deg": best_deg}