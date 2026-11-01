"""
Exact reference for a pontoon (box barge) — first-principles righting arms.

A box barge is the one hull for which the hydrostatics at *any* heel angle can be
computed exactly, without any empirical correction: the immersed section is a
rectangle cut by a straight waterline, so its area and centroid are given by
elementary polygon geometry. This makes it the standard textbook benchmark for
large-angle stability (Biran, *Ship Hydrostatics and Stability*, §5.4 "the
box-shaped vessel"; Rawson & Tupper §8; NAVSEA DTNSRDC-0790-006 §3).

Method (no platform code is imported or re-used):

  section:  y in [-B/2, B/2], z in [0, D]   (breadth / depth, keel at z=0)
  heel phi (starboard down): free surface in body coordinates is the line
            z*cos(phi) - y*sin(phi) = c,   immersed side:  <= c
  c is solved by bisection so that the immersed area equals the displacement
  (constant displacement; the heave is free, which is what a GZ calculation
  with free trim/heave assumes)
  B position = centroid of the immersed polygon (exact, shoelace formula)
  KN = y_B*cos(phi) + z_B*sin(phi)
  GZ = KN - KG*sin(phi)

For angles below the deck-edge immersion the result reduces to the closed form
GZ = sin(phi)*(GM + 0.5*BM*tan^2(phi)), which is used as a self-check.
"""
from __future__ import annotations

import math
from typing import Iterable


def _clip_polygon(poly: list[tuple[float, float]], a: float, b: float, c: float) -> list[tuple[float, float]]:
    """Clip a convex polygon to the half-plane ``a*y + b*z <= c``."""
    out: list[tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        f0 = a * y0 + b * z0 - c
        f1 = a * y1 + b * z1 - c
        if f0 <= 0:
            out.append((y0, z0))
        if (f0 < 0 < f1) or (f1 < 0 < f0):
            t = f0 / (f0 - f1)
            out.append((y0 + t * (y1 - y0), z0 + t * (z1 - z0)))
    return out


def _polygon_area_centroid(poly: list[tuple[float, float]]) -> tuple[float, tuple[float, float]]:
    """Area and centroid of a simple polygon (shoelace formulas)."""
    if len(poly) < 3:
        return 0.0, (0.0, 0.0)
    a2 = 0.0
    cy = 0.0
    cz = 0.0
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a2 += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    area = 0.5 * a2
    if abs(area) < 1e-15:
        return 0.0, (0.0, 0.0)
    return abs(area), (cy / (3.0 * a2), cz / (3.0 * a2))


class BoxBarge:
    """Exact hydrostatics of a rectangular pontoon section."""

    def __init__(self, beam: float, draft: float, depth: float, kg: float):
        self.B = float(beam)
        self.T = float(draft)
        self.D = float(depth)
        self.KG = float(kg)
        self.section = [
            (-self.B / 2.0, 0.0),
            (self.B / 2.0, 0.0),
            (self.B / 2.0, self.D),
            (-self.B / 2.0, self.D),
        ]
        self.area0 = self.B * self.T  # displacement per unit length

    # -- closed-form initial stability -------------------------------------
    @property
    def kb(self) -> float:
        return self.T / 2.0

    @property
    def bm(self) -> float:
        return self.B ** 2 / (12.0 * self.T)

    @property
    def gm(self) -> float:
        return self.kb + self.bm - self.KG

    @property
    def deck_edge_angle(self) -> float:
        """Heel at which the deck edge immerses [deg] (starboard waterline)."""
        return math.degrees(math.atan2(2.0 * (self.D - self.T), self.B))

    # -- exact large-angle hydrostatics ------------------------------------
    def immersed(self, phi_deg: float) -> tuple[float, tuple[float, float], float]:
        """Immersed area, centroid, and waterline offset c at heel ``phi_deg``."""
        phi = math.radians(phi_deg)
        lo, hi = -self.D - self.B, self.D + self.B  # wide bracket for c
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            poly = _clip_polygon(self.section, a=-math.sin(phi), b=math.cos(phi), c=mid)
            area, _ = _polygon_area_centroid(poly)
            if area < self.area0:
                lo = mid
            else:
                hi = mid
        poly = _clip_polygon(self.section, a=-math.sin(phi), b=math.cos(phi), c=0.5 * (lo + hi))
        area, cen = _polygon_area_centroid(poly)
        return area, cen, 0.5 * (lo + hi)

    def kn(self, phi_deg: float) -> float:
        _, (yb, zb), _ = self.immersed(phi_deg)
        phi = math.radians(phi_deg)
        return yb * math.cos(phi) + zb * math.sin(phi)

    def gz(self, phi_deg: float) -> float:
        return self.kn(phi_deg) - self.KG * math.sin(math.radians(phi_deg))

    def gz_curve(self, step: float = 5.0, max_angle: float = 90.0) -> list[dict]:
        out = []
        a = 0.0
        while a <= max_angle + 1e-9:
            out.append({"angle": round(a, 6), "gz": self.gz(a)})
            a += step
        return out

    def area(self, a0: float, a1: float, step: float = 0.1) -> float:
        """Area under the GZ curve by fine Simpson integration [m.rad]."""
        n = max(2, int(round((a1 - a0) / step)))
        if n % 2:
            n += 1
        h = (a1 - a0) / n
        s = self.gz(a0) + self.gz(a1)
        for i in range(1, n):
            w = 4.0 if i % 2 else 2.0
            s += w * self.gz(a0 + i * h)
        return s * h / 3.0 * math.radians(1.0)


def wall_sided_check(barge: BoxBarge, phi_deg: float) -> float:
    """Closed-form wall-sided GZ, valid below deck-edge immersion [m]."""
    return math.sin(math.radians(phi_deg)) * (
        barge.gm + 0.5 * barge.bm * math.tan(math.radians(phi_deg)) ** 2
    )