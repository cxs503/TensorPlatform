"""
Independent hydrostatic cross-check for the wall-sided GZ formula.

``POST /calculations/stability`` computes the righting arm with the wall-sided
formula

    GZ(phi) = GM sin(phi) + 0.5 * BM * tan^2(phi) * sin(phi)

which is exact only while the deck edge stays dry (the "wall-sided" condition:
both sides still immersed and the waterline on the vertical sides). The
endpoint previously applied it at *any* heel angle, e.g. GM=1 m, BM=3 m at
60 deg returns GZ = 4.76 m — a righting arm larger than the beam of most
vessels, i.e. outside the derivation's domain with no warning.

This module supplies the judge:

* :func:`numeric_gz_box` integrates the heeled section of a wall-sided box
  numerically (constant displacement, waterline solved by bisection, buoyancy
  centroid from the section polygon) — completely independent of the closed
  form;
* :func:`deck_edge_heel_deg` gives the analytic validity limit for that box
  (the heel at which the deck edge immerses), tan(phi) = 2*freeboard/B.

Agreement between the two is the evidence that the closed form is implemented
correctly inside its validity range; the limit tells the API when to warn.
"""
from __future__ import annotations

import math
from typing import List, Tuple

G = 9.81


# ---------------------------------------------------------------------------
# closed form (what the platform computes)
# ---------------------------------------------------------------------------

def wall_sided_gz(gm_m: float, bm_m: float, heel_deg: float) -> float:
    """Wall-sided righting arm in metres."""
    phi = math.radians(heel_deg)
    return gm_m * math.sin(phi) + 0.5 * bm_m * math.tan(phi) ** 2 * math.sin(phi)


def deck_edge_heel_deg(beam_m: float, freeboard_m: float) -> float:
    """Heel at which the deck edge immerses for a wall-sided (box) hull.

    tan(phi_lim) = 2 * freeboard / beam.
    """
    if beam_m <= 0:
        raise ValueError("beam must be positive")
    if freeboard_m < 0:
        raise ValueError("freeboard must be non-negative")
    return math.degrees(math.atan(2.0 * freeboard_m / beam_m))


# ---------------------------------------------------------------------------
# numeric hydrostatics of a heeled box section (per metre length)
# ---------------------------------------------------------------------------

def _rotate(y: float, z: float, phi: float) -> Tuple[float, float]:
    return (y * math.cos(phi) + z * math.sin(phi),
            -y * math.sin(phi) + z * math.cos(phi))


def _clip_below(poly: List[Tuple[float, float]], h: float) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman clip of a polygon to the half-plane Z <= h."""
    out: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        in0, in1 = z0 <= h, z1 <= h
        if in0:
            out.append((y0, z0))
        if in0 != in1:
            t = (h - z0) / (z1 - z0)
            out.append((y0 + t * (y1 - y0), h))
    return out


def _area_centroid(poly: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    """Signed area and centroid of a polygon (shoelace)."""
    n = len(poly)
    if n < 3:
        return 0.0, 0.0, 0.0
    a = 0.0
    cy = 0.0
    cz = 0.0
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    a *= 0.5
    if abs(a) < 1e-15:
        return 0.0, 0.0, 0.0
    return a, cy / (6.0 * a), cz / (6.0 * a)


def numeric_gz_box(
    beam_m: float,
    draft_m: float,
    depth_m: float,
    kg_m: float,
    heel_deg: float,
    section_length_m: float = 1.0,
) -> float:
    """Righting arm of a heeled wall-sided box, by direct section integration.

    The section (``beam`` x ``depth``, draught ``draft``) is rotated about the
    keel centreline, the waterline height is solved so that the immersed area
    stays ``beam * draft`` (constant displacement), the buoyancy centroid is
    taken from the clipped polygon and GZ is the lever between the vertical
    through B and the centre of gravity G.

    ``section_length_m`` only scales the volume; GZ is length-independent, so
    the default of 1 m is sufficient.
    """
    if not (beam_m > 0 and draft_m > 0 and depth_m > draft_m):
        raise ValueError("require beam>0, depth>draft>0")
    phi = math.radians(heel_deg)
    a0 = beam_m * draft_m

    corners = [(-beam_m / 2.0, 0.0), (beam_m / 2.0, 0.0),
               (beam_m / 2.0, depth_m), (-beam_m / 2.0, depth_m)]
    rotated = [_rotate(y, z, phi) for y, z in corners]

    # area below the waterline is monotonically decreasing in h
    lo = min(z for _, z in rotated) - 1.0
    hi = max(z for _, z in rotated) + 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        area, _, _ = _area_centroid(_clip_below(rotated, mid))
        # immersed area grows with the waterline height
        if area < a0:
            lo = mid
        else:
            hi = mid
    h = 0.5 * (lo + hi)
    area, yb, zb = _area_centroid(_clip_below(rotated, h))
    if area <= 0:
        raise ValueError("empty immersed section; geometry inconsistent")

    # centre of gravity in the same (world) frame
    yg, zg = _rotate(0.0, kg_m, phi)
    return yb - yg


def box_metacentric_height(beam_m: float, draft_m: float, kg_m: float) -> Tuple[float, float]:
    """(BM, GM) of a box-shaped hull: BM = B^2/(12 T), KB = T/2."""
    bm = beam_m ** 2 / (12.0 * draft_m)
    kb = draft_m / 2.0
    return bm, kb + bm - kg_m