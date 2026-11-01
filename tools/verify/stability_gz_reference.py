"""Independent reference hydrostatics for righting-arm (GZ) verification.

Why this module exists
----------------------
``backend/api/calculations.py::calc_stability`` returns a righting arm from the
classical *wall-sided* formula

    GZ(phi) = GM * sin(phi) + 0.5 * BM * tan(phi)**2 * sin(phi)

and is advertised (capability audit) as ``calc.stability = REAL``.  No benchmark,
analytic cross-check or validity envelope existed for that endpoint (see
``docs/verifier/STABILITY_GZ_VERIFICATION.md``).  This module supplies an
*independent* reference: the same GZ is recomputed from first principles by
clipping the heeled transverse section against the waterline, imposing volume
(area) conservation, and taking the first moment of the submerged area about the
centre of gravity.  Nothing here reuses the wall-sided closed form, so agreement
between the two is a genuine verification statement rather than a tautology.

Conventions
-----------
* Body frame: ``y`` athwartships (starboard positive), ``z`` up, origin at the
  keel on the centreline.  The section polygon is given in this frame.
* Heel ``phi > 0`` puts the starboard side down.  The waterplane normal, expressed
  in body coordinates, is ``n = (-sin(phi), cos(phi))``, and the submerged region
  is ``{p : p . n <= c}`` with ``c`` fixed by area (volume) conservation.
* ``GZ`` is the horizontal offset of the centre of buoyancy from ``G`` taken along
  the in-plane horizontal direction ``t = (cos(phi), sin(phi))``::

      GZ = (y_B - y_G) * cos(phi) + (z_B - z_G) * sin(phi)

  For small angles this collapses to ``(GM) * sin(phi)`` with
  ``GM = KB + BM - KG``, which is the check used in the tests.

Only the standard library is used so the reference can run anywhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

Point = Tuple[float, float]


# ---------------------------------------------------------------------------
# Planar polygon utilities (sections are convex, so a single half-plane clip
# per waterline is exact; no general boolean library is required).
# ---------------------------------------------------------------------------

def polygon_area(poly: Sequence[Point]) -> float:
    """Signed area (shoelace).  Positive for counter-clockwise ordering."""
    if len(poly) < 3:
        return 0.0
    s = 0.0
    for (y0, z0), (y1, z1) in zip(poly, list(poly[1:]) + [poly[0]]):
        s += y0 * z1 - y1 * z0
    return 0.5 * s


def polygon_centroid(poly: Sequence[Point]) -> Point:
    """Area centroid of a simple polygon."""
    a = polygon_area(poly)
    if abs(a) < 1e-18:
        raise ValueError("degenerate polygon: zero area")
    cy = cz = 0.0
    for (y0, z0), (y1, z1) in zip(poly, list(poly[1:]) + [poly[0]]):
        cross = y0 * z1 - y1 * z0
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    return cy / (6.0 * a), cz / (6.0 * a)


def clip_half_plane(poly: Sequence[Point], n: Point, c: float) -> List[Point]:
    """Sutherland-Hodgman clip keeping the part with ``p . n <= c``."""
    if not poly:
        return []
    ny, nz = n
    out: List[Point] = []
    prev = poly[-1]
    prev_d = prev[0] * ny + prev[1] * nz - c
    for cur in poly:
        cur_d = cur[0] * ny + cur[1] * nz - c
        if (cur_d <= 0.0) != (prev_d <= 0.0):
            # segment crosses the waterline -> add intersection
            t = prev_d / (prev_d - cur_d)
            out.append((prev[0] + t * (cur[0] - prev[0]),
                        prev[1] + t * (cur[1] - prev[1])))
        if cur_d <= 0.0:
            out.append(cur)
        prev, prev_d = cur, cur_d
    return out


# ---------------------------------------------------------------------------
# Section definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BoxSection:
    """Rectangular pontoon section: vertical sides (the wall-sided hull family)."""
    beam: float          # B [m]
    depth: float         # D [m] (keel -> deck edge)
    draught: float       # T [m]

    @property
    def polygon(self) -> List[Point]:
        hb = 0.5 * self.beam
        return [(-hb, 0.0), (hb, 0.0), (hb, self.depth), (-hb, self.depth)]

    @property
    def bm(self) -> float:
        """Exact waterplane inertia / displaced volume: B**2 / (12 T)."""
        return self.beam ** 2 / (12.0 * self.draught)

    @property
    def kb(self) -> float:
        return 0.5 * self.draught


@dataclass(frozen=True)
class FlareSection:
    """Trapezoid section: bottom beam ``beam_bottom``, deck beam ``beam_deck``.

    Used to show what the wall-sided formula does to a hull that is *not*
    wall-sided (flare / tumblehome).  ``beam_deck < beam_bottom`` reproduces
    tumblehome.
    """
    beam_bottom: float
    beam_deck: float
    depth: float
    draught: float

    @property
    def polygon(self) -> List[Point]:
        hb0, hb1 = 0.5 * self.beam_bottom, 0.5 * self.beam_deck
        return [(-hb0, 0.0), (hb0, 0.0), (hb1, self.depth), (-hb1, self.depth)]

    @property
    def waterline_beam(self) -> float:
        """Beam at the upright waterline (linear interpolation in z)."""
        if self.depth <= 0.0:
            return self.beam_bottom
        f = self.draught / self.depth
        return self.beam_bottom + f * (self.beam_deck - self.beam_bottom)

    @property
    def _submerged_area(self) -> float:
        return submerged_area_upright(self)

    @property
    def bm(self) -> float:
        """Upright metacentric radius of the (trapezoidal) waterplane.

        The waterplane is a single surface, so its transverse second moment is
        ``b_wl**3 / 12`` per unit length (no z-integration), and
        ``BM = I / (submerged area)``.
        """
        return (self.waterline_beam ** 3 / 12.0) / self._submerged_area

    @property
    def kb(self) -> float:
        return centre_of_buoyancy_upright(self)[1]


def submerged_area_upright(section) -> float:
    """Area of the section below the upright waterline (exact for any polygon)."""
    return polygon_area(clip_half_plane(section.polygon, (0.0, 1.0), section.draught))


def centre_of_buoyancy_upright(section) -> Point:
    """Upright centre of buoyancy (y_B == 0 by symmetry, z_B from first moment)."""
    poly = clip_half_plane(section.polygon, (0.0, 1.0), section.draught)
    area = polygon_area(poly)
    _, zc = polygon_centroid(poly)
    return 0.0, zc


# ---------------------------------------------------------------------------
# Heeled equilibrium + GZ
# ---------------------------------------------------------------------------

def waterline_offset(section, phi: float, tol: float = 1e-13) -> float:
    """Solve for ``c`` in ``{p . n <= c}`` so that the submerged area == B*T."""
    poly = section.polygon
    n = (-math.sin(phi), math.cos(phi))
    target = submerged_area_upright(section)
    vals = [p[0] * n[0] + p[1] * n[1] for p in poly]
    lo, hi = min(vals) - 1.0, max(vals) + 1.0

    def area_at(c: float) -> float:
        return polygon_area(clip_half_plane(poly, n, c))

    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if area_at(mid) > target:
            hi = mid          # too much area submerged -> lower the waterline
        else:
            lo = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def righting_arm(section, phi: float, kg: float, yg: float = 0.0) -> float:
    """Reference GZ [m] at heel ``phi`` [rad] for a centre of gravity at (yg, kg)."""
    poly = section.polygon
    n = (-math.sin(phi), math.cos(phi))
    c = waterline_offset(section, phi)
    sub = clip_half_plane(poly, n, c)
    area = polygon_area(sub)
    if area <= 1e-12:
        return 0.0
    yb, zb = polygon_centroid(sub)
    return (yb - yg) * math.cos(phi) + (zb - kg) * math.sin(phi)


def wall_sided_gz(phi: float, gm: float, bm: float) -> float:
    """The formula implemented by the platform endpoint (reproduced verbatim)."""
    return gm * math.sin(phi) + 0.5 * bm * (math.tan(phi) ** 2) * math.sin(phi)


def wall_sided_area(phi: float, gm: float, bm: float) -> float:
    """Analytic integral of the wall-sided GZ, ``int_0^phi GZ dphi`` [m*rad].

    ``int tan^2 t sin t dt = sec t + cos t``, so::

        area(phi) = GM*(1 - cos(phi)) + 0.5*BM*(sec(phi) + cos(phi) - 2)
    """
    return gm * (1.0 - math.cos(phi)) + 0.5 * bm * (
        1.0 / math.cos(phi) + math.cos(phi) - 2.0
    )


# ---------------------------------------------------------------------------
# Validity envelope: deck-edge immersion / bilge emergence
# ---------------------------------------------------------------------------

def validity_limit_angle(section, hi: float = math.radians(89.5)) -> dict:
    """End of the wall-sided validity envelope, and which event ends it.

    The wall-sided closed form assumes the heeled waterplane still cuts the two
    vertical sides, i.e. neither of the two events below has happened:

    * ``deck_immersion`` -- the leeward deck edge goes under water (waterplane
      starts to shrink from above);
    * ``bilge_emergence`` -- the windward bilge corner leaves the water
      (waterplane starts to shrink from below).

    Returns ``{"angle_rad", "angle_deg", "event"}``; ``event`` is ``None`` when
    neither happens before ``hi``.
    """
    poly = section.polygon
    zmin = min(p[1] for p in poly)
    zmax = max(p[1] for p in poly)
    bottom = [p for p in poly if abs(p[1] - zmin) < 1e-12]
    deck = [p for p in poly if abs(p[1] - zmax) < 1e-12]
    eps = 1e-10

    def limit_of(kind: str) -> float | None:
        def hit(phi: float) -> bool:
            n = (-math.sin(phi), math.cos(phi))
            c = waterline_offset(section, phi)
            if kind == "deck_immersion":
                # the down-side (leeward) deck edge is the first to go under;
                # once it does, the waterplane leaves the side wall.
                return any(p[0] * n[0] + p[1] * n[1] <= c + eps for p in deck)
            return any(p[0] * n[0] + p[1] * n[1] > c + eps for p in bottom)

        if hit(0.0):
            return 0.0
        lo, up = 0.0, hi
        if not hit(up):
            return None
        for _ in range(90):
            mid = 0.5 * (lo + up)
            if hit(mid):
                up = mid
            else:
                lo = mid
        return 0.5 * (lo + up)

    cands = {}
    for kind in ("deck_immersion", "bilge_emergence"):
        a = limit_of(kind)
        if a is not None:
            cands[kind] = a
    if not cands:
        return {"angle_rad": hi, "angle_deg": math.degrees(hi), "event": None}
    event, ang = min(cands.items(), key=lambda kv: kv[1])
    return {"angle_rad": ang, "angle_deg": math.degrees(ang), "event": event}


def deck_immersion_angle(section, hi: float = math.radians(89.5)) -> float:
    """Heel angle [rad] at which the wall-sided assumption ends (see above)."""
    return validity_limit_angle(section, hi)["angle_rad"]


# ---------------------------------------------------------------------------
# IMO IS Code 2008 (Part A, 2.2) intact-stability criteria
# ---------------------------------------------------------------------------

def gz_curve(section, kg: float, angles_deg: Iterable[float]) -> List[float]:
    return [righting_arm(section, math.radians(a), kg) for a in angles_deg]


def _trapz(xs: Sequence[float], ys: Sequence[float]) -> float:
    return sum(0.5 * (ys[i] + ys[i + 1]) * (xs[i + 1] - xs[i])
               for i in range(len(xs) - 1))


def imo_is_code_criteria(angles_deg: Sequence[float], gz: Sequence[float],
                         gm0: float) -> Dict[str, object]:
    """Evaluate the IS Code 2008 (A.749(18) lineage) criteria on a GZ curve.

    ``angles_deg`` must start at 0 and be monotone; the curve is interpolated by
    the trapezoid rule, which is what the code's area language implies in
    practice.  Returns measured values plus pass/fail flags.
    """
    for a in angles_deg:
        pass  # ordering validated below
    if angles_deg[0] != 0.0:
        raise ValueError("GZ curve must start at 0 deg")
    if any(b <= a for a, b in zip(angles_deg, angles_deg[1:])):
        raise ValueError("angles must be strictly increasing")

    phi = [math.radians(a) for a in angles_deg]

    def area(a0: float, a1: float) -> float:
        xs, ys = [0.0], [0.0]
        for p, g in zip(phi, gz):
            if p <= math.radians(a0) + 1e-12:
                xs[-1], ys[-1] = p, g
            if a0 < math.degrees(p) <= a1 + 1e-12:
                xs.append(p)
                ys.append(g)
        return _trapz(xs, ys)

    a30 = area(0.0, 30.0)
    a40 = area(0.0, 40.0)
    a3040 = area(30.0, 40.0)
    gz_max = max(gz)
    gz_max_angle = angles_deg[gz.index(gz_max)]

    def gz_at(target: float) -> float:
        for a, g in zip(angles_deg, gz):
            if abs(a - target) < 1e-9:
                return g
        raise ValueError(f"{target} deg not present in curve")

    checks = {
        "area_0_30_ge_0.055": a30 >= 0.055,
        "area_0_40_ge_0.090": a40 >= 0.090,
        "area_30_40_ge_0.030": a3040 >= 0.030,
        "gz_at_30_ge_0.20": gz_at(30.0) >= 0.20,
        "gz_max_angle_ge_25": gz_max_angle >= 25.0,
        "gm0_ge_0.15": gm0 >= 0.15,
    }
    return {
        "area_0_30_m_rad": round(a30, 6),
        "area_0_40_m_rad": round(a40, 6),
        "area_30_40_m_rad": round(a3040, 6),
        "gz_at_30_m": round(gz_at(30.0), 6),
        "gz_max_m": round(gz_max, 6),
        "gz_max_angle_deg": round(gz_max_angle, 3),
        "gm0_m": round(gm0, 6),
        "criteria": checks,
        "all_pass": all(checks.values()),
    }