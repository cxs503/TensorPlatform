#!/usr/bin/env python3
"""
Independent exact reference for the righting arm GZ of a rectangular (wall-sided)
transverse section — used to audit the platform's wall-sided GZ formula

    GZ_ws(phi) = sin(phi) * (GM + 0.5 * BM * tan^2(phi))

Method (deliberately *not* the closed form above, so the check is independent):
  1. The section is a rectangle of breadth B and moulded depth D, kept in the
     body frame (keel centreline at the origin, z up, y to starboard).
  2. For heel angle phi the section is rotated in the world frame; the upright
     waterline zeta = h is found by **volume balance** (bisection on h) so that
     the submerged area equals the design displacement area A0 = B * T (constant
     since the ship is 2-D / prismatic).
  3. The submerged region is the intersection of the rotated rectangle with the
     half-plane zeta <= h, computed by Sutherland-Hodgman polygon clipping; its
     centroid is the centre of buoyancy B(phi).
  4. GZ = horizontal (world) distance between G and B, signed positive for a
     righting moment.

Because steps 2-4 use only clipping/bisection and exact polygon centroids, the
reference carries **no discretisation error** — every reported difference against
the closed form is pure modelling error of the wall-sided assumption.

Sign convention: heel to starboard by +phi, GZ > 0 => righting.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

Vec = Tuple[float, float]


# --------------------------------------------------------------------------- #
# planar helpers
# --------------------------------------------------------------------------- #
def _rotate(points: Sequence[Vec], phi_rad: float) -> List[Vec]:
    """Rotate body-frame points into the world frame (heel to starboard = +phi)."""
    c, s = math.cos(phi_rad), math.sin(phi_rad)
    return [(c * y - s * z, s * y + c * z) for (y, z) in points]


def _clip_below(poly: Sequence[Vec], h: float) -> List[Vec]:
    """Sutherland-Hodgman clip of a polygon against the half-plane zeta <= h."""
    out: List[Vec] = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ina, inb = a[1] <= h, b[1] <= h
        if ina:
            out.append(a)
        if ina != inb:
            t = (h - a[1]) / (b[1] - a[1])
            out.append((a[0] + t * (b[0] - a[0]), h))
    return out


def _area_centroid(poly: Sequence[Vec]) -> Tuple[float, Vec]:
    """Exact area and centroid of a simple polygon (shoelace, signed area > 0)."""
    n = len(poly)
    if n < 3:
        return 0.0, (0.0, 0.0)
    a2 = cx = cy = 0.0
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a2 += cross
        cx += (y0 + y1) * cross
        cy += (z0 + z1) * cross
    if abs(a2) < 1e-30:
        return 0.0, (0.0, 0.0)
    area = a2 / 2.0
    return abs(area), (cx / (3.0 * a2), cy / (3.0 * a2))


# --------------------------------------------------------------------------- #
# reference solver
# --------------------------------------------------------------------------- #
@dataclass
class BoxCase:
    """Rectangular (wall-sided) section: breadth B, draft T, depth D, KG above keel."""

    beam_m: float
    draft_m: float
    depth_m: float
    kg_m: float
    length_m: float = 1.0

    @property
    def section_area(self) -> float:
        return self.beam_m * self.draft_m

    @property
    def kb_m(self) -> float:
        return 0.5 * self.draft_m

    @property
    def bm_m(self) -> float:
        """Metacentric radius of a rectangular section: I/∇ = (L B^3/12)/(L B T)."""
        return self.beam_m ** 2 / (12.0 * self.draft_m)

    @property
    def gm_m(self) -> float:
        return self.kb_m + self.bm_m - self.kg_m

    @property
    def freeboard_m(self) -> float:
        return self.depth_m - self.draft_m

    @property
    def deck_edge_immersion_deg(self) -> float:
        """phi at which the lee deck edge touches the waterline: tan(phi) = 2 f / B."""
        return math.degrees(math.atan(2.0 * self.freeboard_m / self.beam_m))


def exact_gz(case: BoxCase, phi_deg: float, tol: float = 1e-12) -> dict:
    """Exact GZ of a wall-sided box section at heel ``phi_deg`` (bisection + clipping)."""
    phi = math.radians(phi_deg)
    B, D = case.beam_m, case.depth_m
    corners = [(-B / 2.0, 0.0), (B / 2.0, 0.0), (B / 2.0, D), (-B / 2.0, D)]
    world = _rotate(corners, phi)

    target = case.section_area
    lo = min(z for _, z in world) - 1.0
    hi = max(z for _, z in world) + 1.0

    def submerged(h: float) -> Tuple[float, Vec]:
        return _area_centroid(_clip_below(world, h))

    for _ in range(200):
        mid = 0.5 * (lo + hi)
        a, _c = submerged(mid)
        if a < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    waterline = 0.5 * (lo + hi)
    area, b_centre = submerged(waterline)

    g_world = _rotate([(0.0, case.kg_m)], phi)[0]
    gz = g_world[0] - b_centre[0]  # +phi gives righting arm > 0
    return {
        "heel_deg": phi_deg,
        "gz_m": gz,
        "submerged_area_m2": area,
        "waterline_offset_m": waterline,
        "b_centre_m": b_centre,
        "delta_area_rel": abs(area - target) / target,
    }


def wall_sided_gz(gm_m: float, bm_m: float, phi_deg: float) -> float:
    """The platform formula (closed form), reproduced verbatim for comparison."""
    r = math.radians(phi_deg)
    return gm_m * math.sin(r) + 0.5 * bm_m * math.tan(r) ** 2 * math.sin(r)


def vanishing_angle_deg(case: BoxCase, lo: float = 0.0, hi: float = 89.9) -> float | None:
    """First positive heel where exact GZ falls back through zero (angle of vanishing stability)."""
    prev_phi, prev_gz = exact_gz(case, lo)["gz_m"], None
    prev_gz = exact_gz(case, lo)
    prev_phi, prev = lo, prev_gz["gz_m"]
    steps = 600
    for i in range(1, steps + 1):
        phi = lo + (hi - lo) * i / steps
        gz = exact_gz(case, phi)["gz_m"]
        if prev > 0.0 >= gz:
            a, b = prev_phi, phi
            for _ in range(80):
                m = 0.5 * (a + b)
                if exact_gz(case, m)["gz_m"] > 0.0:
                    a = m
                else:
                    b = m
            return 0.5 * (a + b)
        prev_phi, prev = phi, gz
    return None


if __name__ == "__main__":  # tiny smoke check
    case = BoxCase(beam_m=20.0, draft_m=8.0, depth_m=12.0, kg_m=6.0)
    print(f"KB={case.kb_m:.4f} BM={case.bm_m:.4f} GM={case.gm_m:.4f} "
          f"phi_de={case.deck_edge_immersion_deg:.2f} deg")
    for phi in (5, 10, 20, 25, 30, 45, 60):
        e = exact_gz(case, phi)
        w = wall_sided_gz(case.gm_m, case.bm_m, phi)
        print(f"phi={phi:5.1f}  exact={e['gz_m']:9.5f}  wall_sided={w:9.5f}  "
              f"err={w - e['gz_m']:+9.5f}  dA/A={e['delta_area_rel']:.1e}")
    print("vanishing angle:", vanishing_angle_deg(case))