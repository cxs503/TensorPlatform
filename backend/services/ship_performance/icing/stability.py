"""Intact-stability bookkeeping with superstructure ice as an added top mass.

Hull idealisation
-----------------
The stability kernel works on a **wall-sided (barge-like) hull** with a
rectangular transverse section (beam ``B``, depth to freeboard deck ``D``) so
that the righting arm can be obtained from an *exact* section integration
instead of a truncated series: for each heel angle the submerged section is
obtained by clipping the section polygon against the inclined waterline, the
sinkage is adjusted until the submerged area matches the displacement, and

    GZ(phi) = (y_B - y_G) cos(phi) + (z_B - z_G) sin(phi).

This remains valid past deck-edge immersion, where the wall-sided formula
``GZ = GM sin(phi) + 0.5 BM tan^2(phi) sin(phi)`` breaks down — that matters
here because heavy superstructure icing is precisely the case where the range
of positive stability is consumed (the GZ maximum moves towards the deck-edge
immersion angle).

Ice loading
-----------
Ice mass ``m_i`` attached at panel centroid ``(y_i, z_i)`` acts as an added
weight: the vessel sinks by ``dm / (rho A_w)``, KG and the transverse centre of
gravity move, and (because the ice is carried asymmetrically) a heeling moment
``M_heel = g sum(m_i y_i)`` develops.  All the GM numbers reported by the model
are recomputed from these first principles; the "layered superposition" analytic
cross-check in :mod:`reference` only uses the closed-form versions.

References
----------
IMO IS Code 2008 (Res. MSC.267(85)), Part A, 2.2 — intact stability criteria.
IMO Polar Code (Res. MSC.385(94)), Part I-A, 6.3 — ice accretion allowance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Tuple

Point = Tuple[float, float]  # (y, z) in the transverse section

GRAVITY = 9.80665
RHO_SEA_WATER = 1025.0
RHO_ICE_DESIGN = 900.0      # Polar Code design ice density [kg m^-3]

#: IMO Polar Code Part I-A 6.3.1 icing allowance (metres of ice).
POLAR_CODE_ICE_ALLOWANCE = {
    "exposed": 0.30,        # exposed weather decks and superstructures
    "sheltered": 0.075,     # non-exposed surfaces
}


# ---------------------------------------------------------------------------
# Hull
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BargeHull:
    """Wall-sided hull: rectangular transverse section, prismatic length."""

    length: float
    beam: float
    depth: float          # baseline -> freeboard deck
    draft: float
    block_coefficient: float = 1.0

    def __post_init__(self) -> None:
        if min(self.length, self.beam, self.depth, self.draft) <= 0:
            raise ValueError("hull dimensions must be positive")
        if not (0.3 < self.block_coefficient <= 1.0):
            raise ValueError("block coefficient out of physical range")
        if self.draft > self.depth:
            raise ValueError("draft exceeds depth (hull submerged)")

    # -- geometry ---------------------------------------------------------
    @property
    def section(self) -> List[Point]:
        half = 0.5 * self.beam
        return [(-half, 0.0), (half, 0.0), (half, self.depth), (-half, self.depth)]

    @property
    def prismatic_length(self) -> float:
        """Length of the equivalent rectangular-section prism (captures Cb)."""
        return self.length * self.block_coefficient

    @property
    def waterplane_area(self) -> float:
        return self.prismatic_length * self.beam

    @property
    def displacement_volume(self) -> float:
        return self.waterplane_area * self.draft

    @property
    def displacement_mass(self) -> float:
        return self.displacement_volume * RHO_SEA_WATER

    @property
    def kb(self) -> float:
        return 0.5 * self.draft

    @property
    def waterplane_inertia(self) -> float:
        return self.prismatic_length * self.beam ** 3 / 12.0

    @property
    def bm(self) -> float:
        return self.waterplane_inertia / self.displacement_volume

    @property
    def km(self) -> float:
        return self.kb + self.bm

    def gm(self, kg: float) -> float:
        return self.km - kg

    def draft_with_added_mass(self, added_mass: float) -> float:
        """Draft after adding ``added_mass`` [kg] on the freeboard deck/above."""
        return self.draft + added_mass / (RHO_SEA_WATER * self.waterplane_area)

    def bm_at_draft(self, draft: float) -> float:
        return self.waterplane_inertia / (self.waterplane_area * draft)


# ---------------------------------------------------------------------------
# Polygon clipping / section integration
# ---------------------------------------------------------------------------
def clip_polygon_halfplane(
    polygon: Sequence[Point], height: float, angle: float
) -> List[Point]:
    """Clip by the half-plane ``z cos(phi) - y sin(phi) <= height`` (submerged).

    ``phi > 0`` is a heel to port (+y).  The signed distance uses the rotated
    frame: a body point is under water when ``z cos - y sin <= height``.
    """
    if not polygon:
        return []
    sin_a, cos_a = math.sin(angle), math.cos(angle)

    def signed(p: Point) -> float:
        return p[1] * cos_a - p[0] * sin_a - height

    out: List[Point] = []
    n = len(polygon)
    for i in range(n):
        cur = polygon[i]
        nxt = polygon[(i + 1) % n]
        d_cur = signed(cur)
        d_nxt = signed(nxt)
        if d_cur <= 0.0:
            out.append(cur)
            if d_nxt > 0.0:
                t = d_cur / (d_cur - d_nxt)
                out.append((cur[0] + t * (nxt[0] - cur[0]), cur[1] + t * (nxt[1] - cur[1])))
        elif d_nxt <= 0.0:
            t = d_cur / (d_cur - d_nxt)
            out.append((cur[0] + t * (nxt[0] - cur[0]), cur[1] + t * (nxt[1] - cur[1])))
    return out


def polygon_area_centroid(polygon: Sequence[Point]) -> tuple[float, float, float]:
    """Signed area and centroid of a simple polygon (shoelace formulas)."""
    if len(polygon) < 3:
        return 0.0, 0.0, 0.0
    a = 0.0
    cy = 0.0
    cz = 0.0
    n = len(polygon)
    for i in range(n):
        y0, z0 = polygon[i]
        y1, z1 = polygon[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    area = 0.5 * a
    if abs(area) < 1e-12:
        return 0.0, 0.0, 0.0
    return area, cy / (6.0 * area), cz / (6.0 * area)


def submerged_section(
    hull: BargeHull, heel: float, sinkage: float
) -> tuple[float, float, float]:
    """Submerged section area and centroid at ``heel`` with waterline offset."""
    clipped = clip_polygon_halfplane(hull.section, sinkage, heel)
    area, cy, cz = polygon_area_centroid(clipped)
    return abs(area), cy, cz


def solve_sinkage(hull: BargeHull, heel: float, target_area: float,
                  tol: float = 1e-14) -> float:
    """Find the waterline offset giving the required submerged area."""
    lo, hi = -2.0 * hull.depth, 3.0 * hull.depth
    f_lo = submerged_section(hull, heel, lo)[0] - target_area
    f_hi = submerged_section(hull, heel, hi)[0] - target_area
    if f_lo > 0.0 or f_hi < 0.0:
        return hi if f_lo > 0.0 else lo
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = submerged_section(hull, heel, mid)[0] - target_area
        if abs(f_mid) < tol * max(target_area, 1.0) or (hi - lo) < 1e-12:
            return mid
        if f_mid < 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Loading condition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IceLoad:
    """Lumped ice load: total mass plus its first moments."""

    mass: float            # kg
    moment_z: float        # kg m  (sum m_i z_i)
    moment_y: float        # kg m  (sum m_i y_i)

    @property
    def kg_shift(self) -> float:
        return self.moment_z / self.mass if self.mass > 0 else 0.0

    @property
    def y_centroid(self) -> float:
        return self.moment_y / self.mass if self.mass > 0 else 0.0


def ice_load_from_panels(panel_ice: Iterable[tuple[float, float, float]]) -> IceLoad:
    """Aggregate ``(mass, y, z)`` panel contributions into an :class:`IceLoad`."""
    m = 0.0
    mz = 0.0
    my = 0.0
    for mass, y, z in panel_ice:
        m += mass
        mz += mass * z
        my += mass * y
    return IceLoad(mass=m, moment_z=mz, moment_y=my)


@dataclass
class LoadingCondition:
    """Ship + ice loading; computed GM/KG and the resulting GZ curve."""

    hull: BargeHull
    kg_light: float
    heel_angle_deg: float = 0.0
    gm_light: float = field(init=False)
    ice: IceLoad = field(default_factory=lambda: IceLoad(0.0, 0.0, 0.0))
    draft_loaded: float = field(init=False)
    kg_loaded: float = field(init=False)
    yg_loaded: float = field(init=False)
    gm_loaded: float = field(init=False)
    heeling_moment_knm: float = field(init=False)

    def __post_init__(self) -> None:
        self.gm_light = self.hull.gm(self.kg_light)
        total_mass = self.hull.displacement_mass + self.ice.mass
        self.draft_loaded = self.hull.draft_with_added_mass(self.ice.mass)
        self.kg_loaded = (self.hull.displacement_mass * self.kg_light
                          + self.ice.moment_z) / total_mass
        self.yg_loaded = (0.0 + self.ice.moment_y) / total_mass
        bm = self.hull.bm_at_draft(self.draft_loaded)
        kb = 0.5 * self.draft_loaded
        self.gm_loaded = kb + bm - self.kg_loaded
        self.heeling_moment_knm = GRAVITY * self.ice.moment_y / 1000.0

    @property
    def gm_loss(self) -> float:
        return self.gm_loaded - self.gm_light

    @property
    def displacement_loaded(self) -> float:
        return self.hull.displacement_mass + self.ice.mass

    # -- GZ ---------------------------------------------------------------
    def gz(self, heel_deg: float) -> float:
        phi = math.radians(heel_deg)
        target = self.displacement_loaded / RHO_SEA_WATER / self.hull.prismatic_length
        sinkage = solve_sinkage(self.hull, phi, target)
        _, y_b, z_b = submerged_section(self.hull, phi, sinkage)
        return ((y_b - self.yg_loaded) * math.cos(phi)
                + (z_b - self.kg_loaded) * math.sin(phi))

    def gz_curve(self, max_deg: float = 60.0, step_deg: float = 0.5) -> List[dict]:
        pts = []
        n = int(round(max_deg / step_deg))
        for k in range(n + 1):
            a = k * step_deg
            pts.append({"heel_deg": a, "gz_m": self.gz(a)})
        return pts

    def heel_from_ice(self, max_deg: float = 25.0, tol: float = 1e-10) -> float:
        """Equilibrium heel angle in still water with the asymmetric ice load.

        The vessel heels *towards* the side carrying the extra ice (``G`` moves
        to that side, so ``GZ(0) = -y_G`` is destabilising there); the still-water
        equilibrium is the first heel angle at which ``GZ = 0``.
        """
        if abs(self.ice.moment_y) < 1e-12:
            return 0.0
        direction = 1.0 if self.yg_loaded > 0.0 else -1.0

        def f(a: float) -> float:
            return self.gz(direction * a)

        lo, hi = 0.0, max_deg
        f_lo, f_hi = f(lo), f(hi)
        if f_lo * f_hi > 0.0:
            return 0.0 if abs(f_lo) < abs(f_hi) else direction * hi
        for _ in range(120):
            mid = 0.5 * (lo + hi)
            f_mid = f(mid)
            if abs(f_mid) < tol or (hi - lo) < 1e-10:
                return direction * mid
            if f_mid * f_lo > 0.0:
                lo, f_lo = mid, f_mid
            else:
                hi = mid
        return direction * 0.5 * (lo + hi)

    # -- IS Code criteria --------------------------------------------------
    def stability_criteria(self, step_deg: float = 0.5, max_deg: float = 60.0) -> dict:
        """Evaluate IMO IS Code 2008 Part A 2.2 criteria on the loaded GZ curve.

        Only the criteria that are computable from GZ/GM alone are returned;
        weather-criteria items (wind pressure, passenger crowding) are out of
        scope and flagged as such.
        """
        curve = self.gz_curve(max_deg=max_deg, step_deg=step_deg)

        def area(a0: float, a1: float) -> float:
            vals = [p for p in curve if a0 - 1e-9 <= p["heel_deg"] <= a1 + 1e-9]
            acc = 0.0
            for p, q in zip(vals, vals[1:]):
                rad0, rad1 = math.radians(p["heel_deg"]), math.radians(q["heel_deg"])
                acc += 0.5 * (p["gz_m"] + q["gz_m"]) * (rad1 - rad0)
            return acc

        def gz_at(angle: float) -> float:
            return self.gz(angle)

        peak = max(curve, key=lambda p: p["gz_m"])
        checks = {
            "gm_initial": {
                "value": self.gm_loaded,
                "limit": 0.15,
                "pass": self.gm_loaded >= 0.15,
                "reference": "IS Code 2008 A.2.2.1 (GM >= 0.15 m)",
            },
            "area_0_30": {
                "value": area(0.0, 30.0),
                "limit": 0.055,
                "pass": area(0.0, 30.0) >= 0.055,
                "reference": "IS Code 2008 A.2.2.2 (A0-30 >= 0.055 m rad)",
            },
            "gz_at_30": {
                "value": gz_at(30.0),
                "limit": 0.20,
                "pass": gz_at(30.0) >= 0.20,
                "reference": "IS Code 2008 A.2.2.3 (GZ(30 deg) >= 0.20 m)",
            },
            "heel_at_max_gz": {
                "value": peak["heel_deg"],
                "limit": 25.0,
                "pass": peak["heel_deg"] >= 25.0,
                "reference": "IS Code 2008 A.2.2.4 (max GZ at >= 25 deg)",
            },
            "area_30_40": {
                "value": area(30.0, 40.0),
                "limit": 0.030,
                "pass": area(30.0, 40.0) >= 0.03,
                "reference": "IS Code 2008 A.2.2.5 (A30-40 >= 0.03 m rad)",
            },
        }
        checks["all_pass"] = {
            "value": all(v["pass"] for k, v in checks.items() if k != "all_pass"),
            "limit": True,
            "pass": all(v["pass"] for k, v in checks.items() if k != "all_pass"),
            "reference": "conjunction of IS Code 2008 A.2.2 with ice accretion",
        }
        return checks


def polar_code_design_ice_mass(
    panels: Iterable,
    exposed_thickness: float = POLAR_CODE_ICE_ALLOWANCE["exposed"],
    sheltered_thickness: float = POLAR_CODE_ICE_ALLOWANCE["sheltered"],
    rho_ice: float = RHO_ICE_DESIGN,
) -> IceLoad:
    """Polar Code Part I-A 6.3.1 design icing load on the exposed envelope."""
    m = 0.0
    mz = 0.0
    my = 0.0
    for p in panels:
        t = exposed_thickness if p.zone in ("windward", "side", "horizontal_up", "leeward") \
            else sheltered_thickness
        mass = rho_ice * t * p.area
        m += mass
        mz += mass * p.centroid[2]
        my += mass * p.centroid[1]
    return IceLoad(mass=m, moment_z=mz, moment_y=my)