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

# ---------------------------------------------------------------------------
# Realistic hull family: sloped bilge + vertical topsides (Cb <= 1)
# ---------------------------------------------------------------------------
#
# Why a second hull family
# ------------------------
# The box barge (Cb = 1) can only judge platform stability code on a hull shape
# no ship has.  Real hulls carry Cb ~ 0.55-0.85 and the whole point of ``KB``
# is that it depends on how the sectional area is distributed in depth, so a
# box-only reference leaves every real-hull verdict WITHHELD.
#
# Section geometry (transverse, symmetric, prismatic along the length):
#
#     keel half width b0 at z = 0, growing linearly to B/2 at z = h_k
#     ("bilge rise"), then *vertical* sides up to the deck at z = D.
#
# With k = h_k/T and r = b0/(B/2) the sectional area gives
#
#     Cb = k (1 + r)/2 + (1 - k)      ->      r = 2 (Cb - 1 + k)/k - 1,
#
# so the family is parametrised by (Cb, k) for any feasible pair (0 <= r <= 1).
# Two special cases make it the right adjudicator:
#
#   * ``k = 1`` (sloped sides all the way to the waterline) reduces the exact
#     KB below to ``T (1 + 1/(2 Cb))/3`` -- i.e. exactly the corrected kernel
#     shipped in ``src/ship_perf/hydrostatics.py`` (m1473), so this family is
#     an *independent* check of that fix;
#   * ``k <= ~0.5`` keeps the waterplane full-breadth over a wide heel range,
#     so the wall-sided closed form stays an identity there and the numerical
#     GZ solver can be cross-checked on a non-box hull.
#
# The platform's own approximation ``KB = T (1 - 0.5 (1 - Cb))`` is wrong on
# every member of this family (it returns T for Cb = 1 and 0.9 T for Cb = 0.8,
# against the exact 0.5 T and 0.5625 T).


def section_polygon(beam: float, draft: float, depth: float, cb: float,
                    bilge_rise_frac: float = 1.0) -> List[Tuple[float, float]]:
    """Closed transverse section (sloped bilge, vertical topsides)."""
    if not (0.5 <= cb <= 1.0):
        raise ValueError("cb must lie in [0.5, 1.0] for this section family")
    if not (0.0 < bilge_rise_frac <= 1.0):
        raise ValueError("bilge_rise_frac must lie in (0, 1]")
    if not (depth > draft > 0.0 and beam > 0.0):
        raise ValueError("require depth > draft > 0 and beam > 0")
    hw = beam / 2.0
    k = bilge_rise_frac
    r = 2.0 * (cb - 1.0 + k) / k - 1.0
    if not (-1e-12 <= r <= 1.0 + 1e-12):
        raise ValueError(
            f"infeasible section: cb={cb}, bilge_rise_frac={k} gives "
            f"keel half width ratio r={r:.3f} outside [0, 1]; "
            f"need cb >= 1 - k/2")
    b0 = max(0.0, min(1.0, r)) * hw
    hk = k * draft
    return [(-b0, 0.0), (b0, 0.0), (hw, hk), (hw, depth), (-hw, depth), (-hw, hk)]


def section_kb(draft: float, beam: float, cb: float,
               bilge_rise_frac: float = 1.0) -> float:
    """Exact KB of the sloped-bilge section (first moment / area).

    ``KB = [h_k^2 (b0 + 2 (B/2 - b0)/3) + (B/2) (T^2 - h_k^2)] / (Cb B T)``,
    which reduces to ``T (1 + 1/(2 Cb))/3`` for ``h_k = T``.
    """
    hw = beam / 2.0
    k = bilge_rise_frac
    r = 2.0 * (cb - 1.0 + k) / k - 1.0
    b0 = max(0.0, min(1.0, r)) * hw
    hk = k * draft
    area = cb * beam * draft
    m1 = hk * hk * (b0 + 2.0 * (hw - b0) / 3.0) + hw * (draft * draft - hk * hk)
    return m1 / area


@dataclass(frozen=True)
class SlopedBilgeHull:
    """Prismatic hull: sloped bilge of rise ``k*T`` then vertical topsides."""

    length: float
    beam: float
    draft: float
    depth: float
    kg: float
    cb: float = 0.7
    bilge_rise_frac: float = 1.0
    name: str = "sloped"

    # -- geometry ----------------------------------------------------------
    @property
    def half_beam(self) -> float:
        return self.beam / 2.0

    @property
    def bilge_rise(self) -> float:
        return self.bilge_rise_frac * self.draft

    @property
    def bottom_half_width(self) -> float:
        hw, k = self.half_beam, self.bilge_rise_frac
        r = 2.0 * (self.cb - 1.0 + k) / k - 1.0
        return max(0.0, min(1.0, r)) * hw

    def section(self) -> List[Tuple[float, float]]:
        return section_polygon(self.beam, self.draft, self.depth, self.cb,
                               self.bilge_rise_frac)

    @property
    def volume(self) -> float:
        return self.cb * self.length * self.beam * self.draft

    @property
    def displacement_t(self) -> float:
        return RHO_STD * self.volume / 1000.0

    @property
    def waterplane_area(self) -> float:
        return self.length * self.beam

    # -- upright hydrostatics ---------------------------------------------
    @property
    def kb(self) -> float:
        return section_kb(self.draft, self.beam, self.cb, self.bilge_rise_frac)

    def kb_numeric(self) -> float:
        """Independent KB: z-centroid of the *immersed* upright section."""
        return self.buoyancy_centre_numeric(0.0)[1]

    @property
    def bm(self) -> float:
        """Full-breadth rectangular waterplane: BM = I/nabla = B^2 / (12 Cb T).

        The Cb factor is *not* optional here: the waterplane is the same
        rectangle for every member of the family, but the displaced volume
        shrinks with Cb, so a Cb-less BM inflates the righting lever (it made
        dGZ/dphi disagree with GM by +18 % in the first cut of this module).
        """
        return self.beam**2 / (12.0 * self.cb * self.draft)

    @property
    def km(self) -> float:
        return self.kb + self.bm

    @property
    def gm(self) -> float:
        return self.km - self.kg

    # -- heeled hydrostatics ----------------------------------------------
    def waterline_offset(self, phi_deg: float) -> float:
        """Waterline offset ``c`` for constant displacement at heel ``phi_deg``."""
        phi = math.radians(phi_deg)
        s, co = math.sin(phi), math.cos(phi)
        poly = self.section()
        target = self.cb * self.beam * self.draft
        proj = [q[0] * (-s) + q[1] * co for q in poly]
        lo, hi = min(proj) - 1.0, max(proj) + 1.0
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if _polygon_area(_clip_halfplane(poly, -s, co, mid)) < target:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-13 * max(1.0, abs(hi)):
                break
        return 0.5 * (lo + hi)

    def buoyancy_centre_numeric(self, phi_deg: float) -> Tuple[float, float]:
        phi = math.radians(phi_deg)
        s, co = math.sin(phi), math.cos(phi)
        c = self.waterline_offset(phi_deg)
        return _polygon_centroid(_clip_halfplane(self.section(), -s, co, c))

    def gz_numeric(self, phi_deg: float) -> float:
        x_b, z_b = self.buoyancy_centre_numeric(phi_deg)
        phi = math.radians(phi_deg)
        return x_b * math.cos(phi) + (z_b - self.kg) * math.sin(phi)

    def gz_wall_sided(self, phi_deg: float) -> float:
        phi = math.radians(phi_deg)
        t = math.tan(phi)
        return math.sin(phi) * (self.gm + 0.5 * self.bm * t * t)

    def waterplane_span(self, phi_deg: float) -> float:
        """Width of the heeled waterplane chord (== B while wall-sided)."""
        phi = math.radians(phi_deg)
        s, co = math.sin(phi), math.cos(phi)
        c = self.waterline_offset(phi_deg)
        clipped = _clip_halfplane(self.section(), -s, co, c)
        xs = [q[0] for q in clipped if abs(-q[0] * s + q[1] * co - c) < 1e-7]
        if len(xs) < 2:
            return 0.0
        return max(xs) - min(xs)

    def deck_immersed(self, phi_deg: float) -> bool:
        phi = math.radians(phi_deg)
        s, co = math.sin(phi), math.cos(phi)
        c = self.waterline_offset(phi_deg)
        return min(-x * s + self.depth * co
                   for x in (self.half_beam, -self.half_beam)) < c - 1e-12

    def wall_sided_exact(self, phi_deg: float) -> bool:
        """True while the closed form is an identity (full waterplane, deck dry)."""
        if self.deck_immersed(phi_deg):
            return False
        return self.waterplane_span(phi_deg) >= self.beam - 1e-7

    @property
    def phi_deck_deg(self) -> float:
        return math.degrees(math.atan2(2.0 * (self.depth - self.draft), self.beam))

    @property
    def phi_bilge_deg(self) -> float:
        """Low-side waterline reaches the bilge knuckle (top of the slant).

        For ``Cb = 1`` the section is a rectangle whatever ``k`` is, so the
        knuckle does not exist and the classical ``atan(2T/B)`` applies.
        """
        if self.bottom_half_width >= self.half_beam - 1e-12:
            return math.degrees(math.atan2(2.0 * self.draft, self.beam))
        return math.degrees(math.atan2(2.0 * (self.draft - self.bilge_rise), self.beam))

    @property
    def wall_sided_limit_deg(self) -> float:
        """First heel where the closed form stops being an identity (numeric)."""
        if not self.wall_sided_exact(0.0):
            raise ValueError("closed form invalid even upright")
        lo, hi = 0.0, 89.9
        if self.wall_sided_exact(hi):
            return hi
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self.wall_sided_exact(mid):
                lo = mid
            else:
                hi = mid
        return lo


def gz_error_envelope_section(hull: SlopedBilgeHull,
                              heel_deg_seq: Sequence[float]) -> List[Dict[str, float]]:
    """Closed form vs independent numerical GZ over heel angle for a section hull."""
    rows: List[Dict[str, float]] = []
    limit = hull.wall_sided_limit_deg
    for hd in heel_deg_seq:
        exact = hull.gz_numeric(hd)
        wall = hull.gz_wall_sided(hd)
        rows.append({
            "heel_deg": hd,
            "gz_numeric_m": exact,
            "gz_wall_sided_m": wall,
            "abs_err_m": wall - exact,
            "rel_err_pct": ((wall - exact) / abs(exact) * 100.0) if abs(exact) > 1e-9 else float("nan"),
            "wall_sided_exact": hd <= limit + 1e-9,
        })
    return rows
