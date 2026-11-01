"""Stability input-determinacy audit: exact GZ for a hull *family* that shares
(L, B, T, Cb, KG) but differs in section shape / longitudinal scaling.

Motivation (verification debt, report #1150 / m1565): the serving endpoint
POST /api/v1/calculations/stability accepts
(length, beam, draft, depth, displacement, cb, kg) and returns a single
(kb, bm, gm, gz_max, areas ...) tuple.  That tuple cannot depend on the section
shape, because the section shape is not an input.  This module constructs a
family of *fully specified* hulls that are indistinguishable in the endpoint's
input space, computes their hydrostatics / GZ by exact section integration
(no platform code in the loop), and therefore quantifies the irreducible error
band of any shape-blind answer.

Reference chain (three independent paths, cross-checked in tests):
  R1 wall-sided closed form (box barge, Biran §5.4 / 1150 report):
     GZ = sin(p) * (GM + BM/2 * tan^2 p)          -- exact below deck-edge angle
  R2 section integration with exact polygon clipping, 2 knots per flange
  R3 section integration with the same shape sampled at many extra collinear
     knots (must match R2 to machine precision for piecewise-linear f) ->
     guards the clipping/centroid code path.

Hull definition (everything explicit):
  zeta = z / T                        (immersed range 0..1)
  half-breadth y(x, z) = s(x) * (B/2) * f(min(z/T, 1))
  section area coefficient  c_s = int_0^1 f dzeta
  block coefficient         Cb  = mean_x(s) * c_s
Above the waterline the topsides continue vertically up to the deck at z = D
(standard closed-vessel convention, flagged in the report).  s(x) is normalised
so that mean_x(s) == Cb / c_s exactly (trapezoid quadrature consistent with the
volume integral).

Equilibrium: for each heel angle phi the free surface in body axes is
  z cos(phi) - y sin(phi) = d(phi)
with d(phi) solved by bisection so that displaced volume equals the design
displacement.  Then
  GZ(phi) = y_B cos(phi) + z_B sin(phi) - KG sin(phi).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Sequence, Tuple

RHO_SEA = 1025.0  # kg/m^3, ITTC/IMO standard sea water

# ---------------------------------------------------------------------------
# section shape library: piecewise-linear f(zeta) + its break points
# ---------------------------------------------------------------------------

Shape = Tuple[Callable[[float], float], List[float]]

SHAPES: Dict[str, Shape] = {
    # wall-sided / box section: f = 1, c_s = 1, section centroid 0.5 T
    "box": (lambda z: 1.0, [0.0, 1.0]),
    # V section, apex at keel: c_s = 1/2, section centroid 2/3 T
    "vee": (lambda z: z, [0.0, 1.0]),
    # trapezoidal section (bottom half-breadth = 0.5 of deck): c_s = 3/4
    "trapezoid": (lambda z: 0.5 + 0.5 * z, [0.0, 1.0]),
    # knuckle section, f = min(1, 2 zeta): same c_s = 3/4 as trapezoid but a
    # much lower section centroid -> same Cb, different KB
    "knuckle": (lambda z: min(1.0, 2.0 * z), [0.0, 0.5, 1.0]),
}

DEFAULT_SHAPES = ("box", "trapezoid", "knuckle", "vee")


def _simpson(f: Callable[[float], float], a: float, b: float, n: int = 2000) -> float:
    if n % 2:
        n += 1
    h = (b - a) / n
    acc = f(a) + f(b)
    for i in range(1, n):
        acc += (4.0 if i % 2 else 2.0) * f(a + i * h)
    return acc * h / 3.0


def section_area_coefficient(shape: str) -> float:
    f, _ = SHAPES[shape]
    return _simpson(f, 0.0, 1.0)


def section_centroid_height(shape: str) -> float:
    """Dimensionless centroid of the immersed section area (in units of T)."""
    f, _ = SHAPES[shape]
    return _simpson(lambda z: z * f(z), 0.0, 1.0) / _simpson(f, 0.0, 1.0)


# ---------------------------------------------------------------------------
# polygon utilities
# ---------------------------------------------------------------------------


def section_polygon(shape: str, s: float, B: float, T: float, D: float,
                    extra_knots: int = 0) -> List[Tuple[float, float]]:
    """Closed section polygon: right topside up, deck across, left side down."""
    f, knots = SHAPES[shape]
    zs = [k * T for k in knots if k * T < D]
    zs.append(D)
    if extra_knots:
        for i in range(1, extra_knots):
            z = D * i / extra_knots
            if all(abs(z - zz) > 1e-12 for zz in zs):
                zs.append(z)
    zs.sort()
    right = [(s * 0.5 * B * f(min(z / T, 1.0)), z) for z in zs]
    left = [(-y, z) for y, z in reversed(right)]
    pts = right + left
    out: List[Tuple[float, float]] = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 1e-12 or abs(p[1] - out[-1][1]) > 1e-12:
            out.append(p)
    return out


def clip_halfplane(poly: Sequence[Tuple[float, float]], cos_p: float,
                   sin_p: float, d: float) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman against z*cos(p) - y*sin(p) <= d."""
    def g(p):
        return p[1] * cos_p - p[0] * sin_p - d

    out: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        ga, gb = g(a), g(b)
        if ga <= 0.0:
            out.append(a)
        if (ga < 0.0 < gb) or (gb < 0.0 < ga):
            t = ga / (ga - gb)
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


def area_centroid(poly: Sequence[Tuple[float, float]]) -> Tuple[float, float, float]:
    """Shoelace area and centroid of a simple polygon (nullable if degenerate)."""
    n = len(poly)
    if n < 3:
        return 0.0, 0.0, 0.0
    a2 = cx = cz = 0.0
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cr = y0 * z1 - y1 * z0
        a2 += cr
        cx += (y0 + y1) * cr
        cz += (z0 + z1) * cr
    if abs(a2) < 1e-15:
        return 0.0, 0.0, 0.0
    return abs(0.5 * a2), cx / (3.0 * a2), cz / (3.0 * a2)


# ---------------------------------------------------------------------------
# longitudinal scaling profiles s(x), normalised to unit mean
# ---------------------------------------------------------------------------

PROFILES: Dict[str, Callable[[float], float]] = {
    "prismatic": lambda x: 1.0,
    "cosine-taper": lambda x: 1.0 - 0.5 * (1.0 - math.cos(2.0 * math.pi * x)),
    "midbody": lambda x: 1.0 if 0.25 <= x <= 0.75 else 0.5,
}


@dataclass
class Hull:
    name: str
    L: float = 100.0
    B: float = 20.0
    T: float = 8.0
    D: float = 12.0
    cb: float = 0.5
    kg: float = 7.0
    shape: str = "box"
    profile: str = "prismatic"
    n_stations: int = 20
    extra_knots: int = 0
    _cache: dict = field(default_factory=dict, repr=False, init=False)

    def __post_init__(self) -> None:
        self.cs = section_area_coefficient(self.shape)
        self.zinc = section_centroid_height(self.shape)
        prof = PROFILES[self.profile]
        n = self.n_stations
        acc = sum((0.5 if i in (0, n) else 1.0) * prof(i / n) for i in range(n + 1))
        mean_p = acc / n
        self.s_scale = (self.cb / self.cs) / mean_p
        pmax = max(prof(i / n) for i in range(n + 1))
        if self.s_scale * pmax > 1.0 + 1e-12:
            raise ValueError(
                f"{self.name}: shape {self.shape} (c_s={self.cs:.4f}, profile "
                f"{self.profile}) cannot reach Cb={self.cb} within B/2"
            )
        # station table: (weight, polygon) -- polygons are scale-invariant per
        # station, so build once at s = s(x)
        self._stations: List[Tuple[float, List[Tuple[float, float]]]] = []
        for i in range(n + 1):
            x = i / n
            s = self.s_at(x)
            w = 0.5 if i in (0, n) else 1.0
            self._stations.append(
                (w, section_polygon(self.shape, s, self.B, self.T, self.D, self.extra_knots))
            )
        self._dx = self.L / n

    # -- geometry ---------------------------------------------------------
    def s_at(self, x: float) -> float:
        return self.s_scale * PROFILES[self.profile](x)

    @property
    def displacement_volume(self) -> float:
        return self.cb * self.L * self.B * self.T

    @property
    def displacement_mass_t(self) -> float:
        return RHO_SEA * self.displacement_volume / 1000.0

    def half_beam_at_waterline(self) -> float:
        f, _ = SHAPES[self.shape]
        return self.s_scale * 0.5 * self.B * f(1.0)

    def _volume_and_centroid(self, phi: float, d: float) -> Tuple[float, float, float]:
        cos_p, sin_p = math.cos(phi), math.sin(phi)
        v = my = mz = 0.0
        for w, poly in self._stations:
            a, cy, cz = area_centroid(clip_halfplane(poly, cos_p, sin_p, d))
            v += w * a
            my += w * a * cy
            mz += w * a * cz
        return v * self._dx, my * self._dx, mz * self._dx

    def solve_floatation(self, phi_deg: float) -> Tuple[float, float, float]:
        """Bisection on d: displaced volume == design displacement."""
        phi = math.radians(phi_deg)
        target = self.displacement_volume
        span = 2.0 * (self.B + self.D) + 10.0
        lo, hi = -span, span
        if self._volume_and_centroid(phi, hi)[0] < target:
            raise RuntimeError("hull volume too small for displacement")
        for _ in range(90):
            mid = 0.5 * (lo + hi)
            if self._volume_and_centroid(phi, mid)[0] < target:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-11:
                break
        v, my, mz = self._volume_and_centroid(phi, mid)
        return mid, my / v, mz / v

    # -- hydrostatics -----------------------------------------------------
    def kb(self) -> float:
        return self.solve_floatation(0.0)[2]

    def waterplane_inertia(self) -> float:
        yw = self.half_beam_at_waterline()
        n = self.n_stations
        acc = 0.0
        for i in range(n + 1):
            w = 0.5 if i in (0, n) else 1.0
            y = self.s_at(i / n) * yw / max(self.s_scale, 1e-15)
            acc += w * (2.0 / 3.0) * y ** 3
        return acc * self._dx

    def bm(self) -> float:
        return self.waterplane_inertia() / self.displacement_volume

    def gm(self) -> float:
        return self.kb() + self.bm() - self.kg

    def gz(self, phi_deg: float) -> float:
        phi = math.radians(phi_deg)
        _, yb, zb = self.solve_floatation(phi_deg)
        return yb * math.cos(phi) + zb * math.sin(phi) - self.kg * math.sin(phi)

    def gz_curve(self, deg_max: float = 40.0, step: float = 1.0) -> List[Tuple[float, float]]:
        return [(i * step, self.gz(i * step)) for i in range(int(round(deg_max / step)) + 1)]

    def deck_edge_angle_deg(self) -> float:
        """Heel angle at which the waterline first reaches the deck edge (z = D)."""
        y_deck = self.half_beam_at_waterline()

        def excess(phi_deg: float) -> float:
            phi = math.radians(phi_deg)
            d = self.solve_floatation(phi_deg)[0]
            return d - (self.D * math.cos(phi) - y_deck * math.sin(phi))

        if excess(0.0) >= 0.0:
            return 0.0
        lo, hi = 0.0, 89.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if excess(mid) < 0.0:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


def wall_sided_gz(phi_deg: float, gm: float, bm: float) -> float:
    """Exact GZ of a wall-sided (box) hull below deck-edge immersion."""
    p = math.radians(phi_deg)
    return math.sin(p) * (gm + 0.5 * bm * math.tan(p) ** 2)


def simpson(ys: Sequence[float], h: float) -> float:
    n = len(ys) - 1
    if n % 2:
        raise ValueError("simpson needs an even number of intervals")
    acc = ys[0] + ys[-1]
    for i in range(1, n):
        acc += (4.0 if i % 2 else 2.0) * ys[i]
    return acc * h / 3.0


# ---------------------------------------------------------------------------
# IMO IS Code 2008 (Res. MSC.267(85)) Part A 2.2.1 criteria
# ---------------------------------------------------------------------------

def imo_verdicts(gm: float, gz_max: float, ang_max: float, gz30: float,
                 a030: float, a3040: float, a040: float) -> Dict[str, bool]:
    return {
        "area_0_30 >= 0.055": a030 >= 0.055,
        "area_30_40 >= 0.030": a3040 >= 0.030,
        "area_0_40 >= 0.090": a040 >= 0.090,
        "gz_30 >= 0.20": gz30 >= 0.20,
        "gz_max >= 0.20": gz_max >= 0.20,
        "angle_gz_max >= 25deg": ang_max >= 25.0,
        "gm0 >= 0.15": gm >= 0.15,
    }


def stability_report(hull: Hull) -> Dict[str, object]:
    curve = hull.gz_curve(40.0, 1.0)
    angles = [a for a, _ in curve]
    gzs = [g for _, g in curve]
    i30, i40 = angles.index(30.0), angles.index(40.0)
    h = math.pi / 180.0  # degrees -> radians
    a030 = simpson(gzs[: i30 + 1], h)
    a3040 = simpson(gzs[i30 : i40 + 1], h)
    a040 = simpson(gzs[: i40 + 1], h)
    gz_max = max(gzs)
    ang_max = angles[gzs.index(gz_max)]
    gm = hull.gm()
    v = imo_verdicts(gm, gz_max, ang_max, gzs[i30], a030, a3040, a040)
    return {
        "hull": hull.name,
        "shape": hull.shape,
        "profile": hull.profile,
        "cb": hull.cb,
        "cs": hull.cs,
        "section_centroid_over_T": hull.zinc,
        "s_scale": hull.s_scale,
        "length_m": hull.L,
        "beam_m": hull.B,
        "draft_m": hull.T,
        "depth_m": hull.D,
        "kg_m": hull.kg,
        "displacement_t": hull.displacement_mass_t,
        "kb_m": hull.kb(),
        "bm_m": hull.bm(),
        "gm_m": gm,
        "half_beam_wl_m": hull.half_beam_at_waterline(),
        "deck_edge_angle_deg": hull.deck_edge_angle_deg(),
        "gz_max_m": gz_max,
        "gz_max_angle_deg": ang_max,
        "gz_30_m": gzs[i30],
        "area_0_30_mrad": a030,
        "area_30_40_mrad": a3040,
        "area_0_40_mrad": a040,
        "imo_pass": all(v.values()),
        "imo_verdicts": v,
        "gz_curve": curve,
    }


def family(cb: float, kg: float, L=100.0, B=20.0, T=8.0, D=12.0,
           shapes: Sequence[str] = DEFAULT_SHAPES,
           profiles: Sequence[str] = ("prismatic", "cosine-taper")) -> List[Hull]:
    hulls: List[Hull] = []
    for prof in profiles:
        for sh in shapes:
            try:
                hulls.append(Hull(f"{sh}/{prof}", L=L, B=B, T=T, D=D, cb=cb, kg=kg,
                                  shape=sh, profile=prof))
            except ValueError:
                pass
    return hulls


CASES = (
    {"id": "A_cb050_kg45", "cb": 0.50, "kg": 4.5,
     "why": "all members stable; family spans GM 0.5..9.2 m on one payload"},
    {"id": "B_cb070_kg60", "cb": 0.70, "kg": 6.0,
     "why": "realistic cargo-ship block coefficient; 3 feasible sections"},
    {"id": "C_cb100_kg70", "cb": 1.00, "kg": 7.0,
     "why": "fully determined box barge = reference case of audit #1150"},
)

QUANTITIES = ("gm_m", "gz_max_m", "area_0_40_mrad")


def rel_err(ref: float, got: float) -> float:
    """Relative error with an absolute floor so that ~0 references stay finite."""
    floor = 1e-2
    return abs(got - ref) / max(abs(ref), floor)


def minimax_floor(values: Sequence[float], n_grid: int = 20001) -> Dict[str, float]:
    """Best achievable worst-case relative error for a shape-blind scalar answer.

    Any single answer q_hat must serve every family member; the minimax-optimal
    q_hat and its worst-case relative error are computed on a dense grid.
    """
    lo, hi = min(values), max(values)
    pad = 0.5 * max(hi - lo, 1e-3)
    best = (float("inf"), float("nan"))
    for i in range(n_grid):
        q = lo - pad + (hi - lo + 2 * pad) * i / (n_grid - 1)
        worst = max(rel_err(v, q) for v in values)
        if worst < best[0]:
            best = (worst, q)
    return {"worst_case_rel_err": best[0], "optimal_answer": best[1]}


def reference_validation() -> Dict[str, object]:
    """R1 vs R2: box barge GZ against the wall-sided closed form."""
    h = Hull("box-Cb1", cb=1.0, kg=7.0)
    gm, bm = h.gm(), h.bm()
    ang = h.deck_edge_angle_deg()
    rows = []
    worst = 0.0
    for a in (5.0, 10.0, 15.0, 20.0):
        num = h.gz(a)
        clo = wall_sided_gz(a, gm, bm)
        e = abs(num - clo) / max(abs(clo), 1e-12)
        worst = max(worst, e)
        rows.append({"phi_deg": a, "gz_section_integration": num,
                     "gz_wall_sided_closed_form": clo, "rel_err": e})
    return {
        "hull": "box barge L=100 B=20 T=8 KG=7 Cb=1.0",
        "kb_m": h.kb(), "bm_m": bm, "gm_m": gm,
        "deck_edge_angle_deg": ang,
        "rows": rows,
        "max_rel_err": worst,
        "note": "closed form valid strictly below the deck-edge angle; "
                f"compared at 5/10/15/20 deg (< {ang:.2f} deg)",
    }


def audit(cb: float, kg: float, L=100.0, B=20.0, T=8.0, D=12.0) -> Dict[str, object]:
    hulls = family(cb, kg, L, B, T, D)
    reports = [stability_report(h) for h in hulls]
    spread = {}
    for key in ("kb_m", "bm_m", "gm_m", "gz_max_m", "area_0_40_mrad",
                "deck_edge_angle_deg"):
        vals = [r[key] for r in reports]
        spread[key] = {"min": min(vals), "max": max(vals)}
    for a in (10.0, 30.0):
        vals = [h.gz(a) for h in hulls]
        spread[f"gz_at_{int(a)}deg"] = {"min": min(vals), "max": max(vals)}
    floors = {}
    for key in QUANTITIES:
        vals = [float(r[key]) for r in reports]
        if max(abs(v) for v in vals) < 1e-9:
            continue
        floors[key] = minimax_floor(vals)
    return {
        "case": {"length_m": L, "beam_m": B, "draft_m": T, "depth_m": D,
                 "cb": cb, "kg_m": kg,
                 "displacement_t": RHO_SEA * cb * L * B * T / 1000.0},
        "endpoint_payload": {"length": L, "beam": B, "draft": T, "depth": D,
                             "displacement": RHO_SEA * cb * L * B * T,
                             "cb": cb, "kg": kg},
        "members": reports,
        "spread": spread,
        "imo_verdict": {
            "n_pass": sum(1 for r in reports if r["imo_pass"]),
            "n_fail": sum(1 for r in reports if not r["imo_pass"]),
        },
        "minimax_floor": floors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="stability input-determinacy audit")
    ap.add_argument("--out", default="artifacts/verifier/stability_indeterminacy/evidence.json")
    ap.add_argument("--captures", default=None,
                    help="optional JSON list of captured endpoint responses")
    args = ap.parse_args(argv)

    out: Dict[str, object] = {
        "generated_by": "tools/verifier/stability_indeterminacy.py",
        "rho_sea_kg_m3": RHO_SEA,
        "reference_validation": reference_validation(),
        "cases": {},
    }
    for case in CASES:
        res = audit(case["cb"], case["kg"])
        res["why"] = case["why"]
        out["cases"][case["id"]] = res
        print(f"== {case['id']}: {case['why']}")
        for r in res["members"]:
            print(f"   {r['hull']:>22s} cs={r['cs']:.2f} s={r['s_scale']:.3f} "
                  f"KB={r['kb_m']:.3f} BM={r['bm_m']:.3f} GM={r['gm_m']:.3f} "
                  f"GZmax={r['gz_max_m']:.3f} A040={r['area_0_40_mrad']:.4f} "
                  f"IMO={'PASS' if r['imo_pass'] else 'FAIL'}")
        print("   spread GM:", res["spread"]["gm_m"],
              "| IMO pass/fail:", res["imo_verdict"]["n_pass"], "/",
              res["imo_verdict"]["n_fail"])
        print("   minimax:", {k: round(v["worst_case_rel_err"], 4)
                              for k, v in res["minimax_floor"].items()})

    if args.captures:
        with open(args.captures, encoding="utf-8") as fh:
            out["endpoint_captures"] = json.load(fh)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
