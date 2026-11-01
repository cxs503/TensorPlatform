#!/usr/bin/env python3
"""Independent analytic references for intact-stability (GZ) verification.

Everything in this module is derived from first principles; no platform code
is imported, so a formula error inside the platform cannot leak into the
reference.

Reference hull: a *wall-sided* rectangular box barge (Cb = Cm = Cp = Cw = 1).

Body coordinate system
----------------------
``x`` transverse (starboard positive), ``z`` vertical (keel at z = 0),
origin on the centreline at the keel.  Heel angle ``phi`` (starboard positive)
is applied by tilting gravity, i.e. the submerged region of the (fixed) body
is bounded by a plane ``-x sin(phi) + z cos(phi) = c`` (starboard side deeper), with ``c`` chosen so the
displaced area equals the upright area ``B * T`` (constant displacement).

Closed form (valid while the waterplane spans the full breadth, i.e. no deck
immersion and no bilge emergence)::

    KB  = T / 2
    BM  = B^2 / (12 T)
    x_b = BM tan(phi)                     # transverse shift of the buoyancy centre
    z_b = KB + (BM / 2) tan^2(phi)        # rise of the buoyancy centre
    GZ  = x_b cos(phi) + (z_b - KG) sin(phi)
        = sin(phi) * (GM + BM/2 * tan^2(phi))        # wall-sided GZ formula

Validity limits::

    phi_deck  = atan(2 (D - T) / B)   # deck-edge immersion
    phi_bilge = atan(2 T / B)         # bilge emergence (bottom leaves the water)

A second, fully independent numerical method (polygon clipping + bisection on
the waterline offset) evaluates GZ at *any* heel angle, including past deck
immersion and bilge emergence, and is used (a) to prove the closed form and
(b) to extend the audit beyond the wall-sided range.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

RHO_STD = 1025.0  # kg/m^3, sea water density used by the platform defaults
G = 9.81

# ---------------------------------------------------------------------------
# Hull definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoxBarge:
    """Wall-sided rectangular box barge (exact analytic reference hull)."""

    length: float          # L [m]
    beam: float            # B [m]
    draft: float           # T [m]
    depth: float           # D [m] (moulded depth, deck at z = D)
    kg: float              # KG [m] (centre of gravity above keel)
    name: str = "box"

    # -- hydrostatics -------------------------------------------------------
    @property
    def cb(self) -> float:
        return 1.0

    @property
    def volume(self) -> float:
        return self.length * self.beam * self.draft

    @property
    def displacement_t(self) -> float:
        return RHO_STD * self.volume / 1000.0

    @property
    def kb(self) -> float:
        return self.draft / 2.0

    @property
    def bm(self) -> float:
        return self.beam**2 / (12.0 * self.draft)

    @property
    def km(self) -> float:
        return self.kb + self.bm

    @property
    def gm(self) -> float:
        return self.km - self.kg

    @property
    def waterplane_area(self) -> float:
        return self.length * self.beam

    @property
    def wetted_surface(self) -> float:
        """Exact wetted area of the box (bottom + 2 sides + 2 ends, no appendages)."""
        return (self.length * self.beam
                + 2.0 * self.length * self.draft
                + 2.0 * self.beam * self.draft)

    # -- validity limits ----------------------------------------------------
    @property
    def phi_deck_deg(self) -> float:
        return math.degrees(math.atan2(2.0 * (self.depth - self.draft), self.beam))

    @property
    def phi_bilge_deg(self) -> float:
        return math.degrees(math.atan2(2.0 * self.draft, self.beam))

    @property
    def wall_sided_limit_deg(self) -> float:
        return min(self.phi_deck_deg, self.phi_bilge_deg)

    # -- GZ ----------------------------------------------------------------
    def gz_wall_sided(self, phi_deg: float) -> float:
        """Closed-form GZ; callers must respect ``wall_sided_limit_deg``."""
        phi = math.radians(phi_deg)
        t = math.tan(phi)
        return math.sin(phi) * (self.gm + 0.5 * self.bm * t * t)

    def gz_numeric(self, phi_deg: float) -> float:
        """Independent numerical GZ (polygon clipping, valid at any heel angle)."""
        x_b, z_b = self.buoyancy_centre_numeric(phi_deg)
        phi = math.radians(phi_deg)
        return x_b * math.cos(phi) + (z_b - self.kg) * math.sin(phi)

    def buoyancy_centre_numeric(self, phi_deg: float) -> Tuple[float, float]:
        """Centroid of the heeled displacement volume, body frame.

        Solves ``area(S(c)) = B * T`` for the waterline offset ``c`` by
        bisection, where ``S(c) = rect ∩ {-x sin(phi) + z cos(phi) <= c}``.
        """
        phi = math.radians(phi_deg)
        s, co = math.sin(phi), math.cos(phi)
        target = self.beam * self.draft

        rect = _rect_polygon(self.beam, self.depth)
        # f = x sin + z cos is bounded by +-(B/2 |sin| + D |cos|); bracket safely.
        lo = -1.0 * (abs(s) * self.beam / 2.0 + co * self.depth) - 1.0
        hi = +1.0 * (abs(s) * self.beam / 2.0 + co * self.depth) + 1.0
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            poly = _clip_halfplane(rect, -s, co, mid)
            if _polygon_area(poly) < target:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-13 * max(1.0, abs(hi)):
                break
        poly = _clip_halfplane(rect, -s, co, 0.5 * (lo + hi))
        return _polygon_centroid(poly)


def _rect_polygon(beam: float, depth: float) -> List[Tuple[float, float]]:
    hb = beam / 2.0
    return [(-hb, 0.0), (hb, 0.0), (hb, depth), (-hb, depth)]


def _clip_halfplane(poly: Sequence[Tuple[float, float]], s: float, c: float,
                    k: float) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman clip of ``poly`` against ``x*s + z*c <= k``."""
    if not poly:
        return []
    out: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        fp = p[0] * s + p[1] * c - k
        fq = q[0] * s + q[1] * c - k
        if fp <= 0.0:
            out.append(p)
        if (fp < 0.0 < fq) or (fq < 0.0 < fp):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _polygon_area(poly: Sequence[Tuple[float, float]]) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % n]
        a += x1 * z2 - x2 * z1
    return 0.5 * a


def _polygon_centroid(poly: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    a = _polygon_area(poly)
    if abs(a) < 1e-15:
        raise ValueError("degenerate polygon: zero area")
    cx = cz = 0.0
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % n]
        cross = x1 * z2 - x2 * z1
        cx += (x1 + x2) * cross
        cz += (z1 + z2) * cross
    return cx / (6.0 * a), cz / (6.0 * a)


# ---------------------------------------------------------------------------
# Friction-line references (used as a PASS control for the same platform)
# ---------------------------------------------------------------------------


def ittc57_cf(re: float) -> float:
    """ITTC-1957 model-ship correlation line: Cf = 0.075 / (log10(Re) - 2)^2."""
    if re <= 100.0:
        raise ValueError("Re too small for ITTC-57")
    return 0.075 / (math.log10(re) - 2.0) ** 2


def morrish_kb(draft: float, cb: float, cwp: float) -> float:
    """Morrish (1892) approximate KB for wall-sided ship forms.

    KB = T * (5/6 - Cb / (3 * Cwp)).  Reduces to T/2 for a box (Cb = Cwp = 1).
    """
    if cwp <= 0.0:
        raise ValueError("Cwp must be positive")
    return draft * (5.0 / 6.0 - cb / (3.0 * cwp))


def cwp_estimate(cb: float) -> float:
    """Common merchant-form estimate Cwp = 0.75 + 0.25 Cb (>= 1 is clipped)."""
    return min(1.0, 0.75 + 0.25 * cb)


# ---------------------------------------------------------------------------
# Curve-level helpers (same trapezoidal quadrature as the platform, so that
# only the GZ *formula* is audited, not the integration rule)
# ---------------------------------------------------------------------------


def gz_curve(phi_deg_list: Sequence[float], gz_fn) -> List[float]:
    return [gz_fn(p) for p in phi_deg_list]


def trapezoid_area(angles: Sequence[float], gz: Sequence[float],
                   start: float, end: float) -> float:
    """Integral of GZ over [start, end] deg using the platform's 5 deg rule."""
    area = 0.0
    for i in range(len(angles) - 1):
        if angles[i] >= start and angles[i + 1] <= end:
            area += 0.5 * (gz[i] + gz[i + 1]) * math.radians(angles[i + 1] - angles[i])
    return area


def curve_summary(angles: Sequence[float], gz: Sequence[float]) -> Dict[str, float]:
    """Engineer-level descriptors of a GZ curve (same definitions as platform)."""
    imax = max(range(len(gz)), key=lambda i: gz[i])
    gz_max = gz[imax]
    vanish = 0.0
    for i in range(1, len(gz)):
        if gz[i] <= 0.0 and gz[i - 1] > 0.0:
            # linear interpolation of the zero crossing
            a0, a1, g0, g1 = angles[i - 1], angles[i], gz[i - 1], gz[i]
            vanish = a0 + (a1 - a0) * g0 / (g0 - g1)
            break
    return {
        "gz_max": gz_max,
        "gz_max_angle": angles[imax],
        "vanishing_angle": vanish,
        "area_0_30": trapezoid_area(angles, gz, 0.0, 30.0),
        "area_30_40": trapezoid_area(angles, gz, 30.0, 40.0),
        "area_0_40": trapezoid_area(angles, gz, 0.0, 40.0),
    }


def relative_error_pct(calc: float, ref: float) -> float:
    """Signed relative error in percent, (calc - ref) / |ref| * 100."""
    if ref == 0.0:
        return float("nan") if calc == 0.0 else float("inf")
    return (calc - ref) / abs(ref) * 100.0