"""Independent analytic baseline for intact-stability GZ (verification debt, QR-INTACT).

Purpose
-------
Provide a *closed baseline* against which an empirical/approximate GZ module can be
audited.  The baseline is exact for a rectangular box-shaped barge (``Cb = Cm = Cw = 1``):

* the heeled underwater section is the rectangle clipped by the waterline half-plane;
* the waterline offset ``d`` is solved from displaced-volume conservation ``A(d) = B*T``;
* ``KN(phi)`` is the earth-frame transverse coordinate of the section centroid;
* ``GZ(phi) = KN(phi) - KG*sin(phi)``.

For small heel and while the hull is *wall-sided* (no deck-edge immersion, no bilge
emergence) the same quantity has the textbook closed form

    GZ(phi) = sin(phi) * ( GM + 0.5 * BM * tan(phi)**2 ),      GM = KB + BM - KG
    KB = T/2,               BM = B**2 / (12*T)                  (box, exact)

which is used as an *independent* consistency check of the numerical baseline
(``wall_sided_gz``), i.e. the baseline validates itself against a formula derived
by a different route (see ``docs/reports/QR-INTACT-intact-stability-baseline.md``).

Physical validity (documented, not silently extended)
-----------------------------------------------------
For an **open-top** barge the righting curve is only meaningful up to the deck-edge
immersion (downflooding) angle

    phi_flood = atan2(D - T, B/2)

beyond which the vessel floods and the GZ curve terminates.  ``box_kn_centroid``
keeps returning the *closed-box* (watertight-deck) answer for any heel, so the
caller decides which branch is physical; ``flooding_angle_deg`` exposes the limit.
This matters for the audit: a GZ module that ignores freeboard cannot represent
this termination at all.

Pure Python (no third-party dependency) so it can run inside any CI image.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]


# ---------------------------------------------------------------------------
# 1. Textbook closed forms (independent route #1)
# ---------------------------------------------------------------------------
def box_kb(T: float) -> float:
    """Centre of buoyancy height of a rectangular section, exact: KB = T/2."""
    return 0.5 * T


def box_bm(B: float, T: float) -> float:
    """Transverse metacentric radius of a rectangular waterplane, exact: BM = B^2/(12T)."""
    return B * B / (12.0 * T)


def box_gm(B: float, T: float, KG: float) -> float:
    """Initial metacentric height of a box barge: GM = KB + BM - KG."""
    return box_kb(T) + box_bm(B, T) - KG


def wall_sided_gz(phi_deg: float, GM: float, BM: float) -> float:
    """Textbook wall-sided righting arm: GZ = sin(phi)*(GM + 0.5*BM*tan^2(phi))."""
    phi = math.radians(phi_deg)
    return math.sin(phi) * (GM + 0.5 * BM * math.tan(phi) ** 2)


def flooding_angle_deg(B: float, D: float, T: float) -> float:
    """Deck-edge (downflooding) heel angle of an open-top barge, degrees."""
    return math.degrees(math.atan2(D - T, B / 2.0))


def bilge_emergence_angle_deg(B: float, T: float) -> float:
    """Heel angle at which the waterline leaves the bilge on the high side, degrees."""
    return math.degrees(math.atan2(2.0 * T, B))


def wall_sided_valid_limit_deg(B: float, D: float, T: float) -> float:
    """Upper bound of validity of :func:`wall_sided_gz` for this barge."""
    return min(flooding_angle_deg(B, D, T), bilge_emergence_angle_deg(B, T))


# ---------------------------------------------------------------------------
# 2. Exact section geometry (independent route #2)
# ---------------------------------------------------------------------------
def _clip_halfplane(poly: Sequence[Point], nx: float, ny: float, d: float) -> List[Point]:
    """Sutherland-Hodgman clip of `poly` against nx*x + ny*y <= d."""
    out: List[Point] = []
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        f1 = nx * x1 + ny * y1 - d
        f2 = nx * x2 + ny * y2 - d
        if f1 <= 0.0:
            out.append((x1, y1))
        if (f1 < 0.0 < f2) or (f2 < 0.0 < f1):
            t = f1 / (f1 - f2)
            out.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
    return out


def _area_centroid(poly: Sequence[Point]) -> Tuple[float, Point]:
    """Shoelace area and centroid of a simple polygon."""
    if len(poly) < 3:
        return 0.0, (0.0, 0.0)
    a = 0.0
    cx = 0.0
    cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    a *= 0.5
    if abs(a) < 1e-15:
        return 0.0, (0.0, 0.0)
    return abs(a), (cx / (6.0 * a), cy / (6.0 * a))


def box_section_submerged(B: float, D: float, T: float, phi_deg: float,
                          tol: float = 1e-14) -> Tuple[float, Point]:
    """Submerged section (area, ship-frame centroid) of a heeled box barge.

    The waterline in ship frame is ``-sin(phi)*y + cos(phi)*z = d``; ``d`` is solved
    by bisection so that the submerged area equals the upright ``B*T`` (no trim).
    """
    phi = math.radians(phi_deg)
    nx = -math.sin(phi)
    ny = math.cos(phi)
    rect = [(-B / 2.0, 0.0), (B / 2.0, 0.0), (B / 2.0, D), (-B / 2.0, D)]
    target = B * T

    def sub(d: float) -> Tuple[float, Point]:
        return _area_centroid(_clip_halfplane(rect, nx, ny, d))

    lo, hi = -(B + D), (B + D)
    a_lo, _ = sub(lo)
    a_hi, _ = sub(hi)
    # submerged area grows monotonically with the waterline offset d
    if not (a_lo < target < a_hi):
        raise ValueError(f"volume not attainable: area in [{a_lo:.6g},{a_hi:.6g}], target {target:.6g}")
    d = 0.0
    for _ in range(200):
        d = 0.5 * (lo + hi)
        area, _c = sub(d)
        if area < target:
            lo = d
        else:
            hi = d
        if hi - lo < tol:
            break
    return sub(0.5 * (lo + hi))


def box_kn(B: float, D: float, T: float, phi_deg: float) -> float:
    """Exact KN(phi) of a box barge (closed box), m."""
    _area, (y_c, z_c) = box_section_submerged(B, D, T, phi_deg)
    phi = math.radians(phi_deg)
    return y_c * math.cos(phi) + z_c * math.sin(phi)


def box_gz(B: float, D: float, T: float, KG: float, phi_deg: float) -> float:
    """Exact GZ(phi) of a (closed) box barge, m.  GZ = KN - KG*sin(phi)."""
    return box_kn(B, D, T, phi_deg) - KG * math.sin(math.radians(phi_deg))


def gz_curve(B: float, D: float, T: float, KG: float,
             angles: Iterable[float] = range(0, 91, 5)) -> List[float]:
    """Exact GZ curve of a box barge on the given heel grid."""
    return [box_gz(B, D, T, KG, a) for a in angles]


# ---------------------------------------------------------------------------
# 3. IMO IS Code 2008 Part A 2.2 / 2.3 criteria (areas in m*rad)
# ---------------------------------------------------------------------------
def area_under(angles: Sequence[float], gz: Sequence[float],
               start: float = 0.0, end: float = 40.0) -> float:
    """Trapezoidal integral of GZ over [start, end] in m*rad (grid nodes respected)."""
    total = 0.0
    for i in range(len(angles) - 1):
        a1, a2 = angles[i], angles[i + 1]
        lo, hi = max(a1, start), min(a2, end)
        if hi <= lo:
            continue
        # linear interpolation of GZ at the clipped abscissae
        def _gz(a: float) -> float:
            w = 0.0 if a2 == a1 else (a - a1) / (a2 - a1)
            return gz[i] * (1.0 - w) + gz[i + 1] * w
        total += 0.5 * (_gz(lo) + _gz(hi)) * math.radians(hi - lo)
    return total


def imo_areas(angles: Sequence[float], gz: Sequence[float]) -> Dict[str, float]:
    """GZ area criteria of IS Code 2008 (m*rad)."""
    a_0_30 = area_under(angles, gz, 0.0, 30.0)
    a_30_40 = area_under(angles, gz, 30.0, 40.0)
    return {"area_0_30": a_0_30, "area_30_40": a_30_40, "area_0_40": a_0_30 + a_30_40}


def imo_criteria(GM: float, gz_max: float, phi_gzmax: float,
                 areas: Dict[str, float], vanishing_deg: float) -> Dict[str, bool]:
    """IS Code 2008 Part A 2.2/2.3 checks, keyed like the audited API."""
    return {
        "GM >= 0.15m": GM >= 0.15,
        "GZmax >= 0.20m": gz_max >= 0.20,
        "phi_GZmax >= 25deg": phi_gzmax >= 25.0,
        "Area_30_40 >= 0.03 m*rad": areas["area_30_40"] >= 0.03,
        "Area_0_40 >= 0.055 m*rad": areas["area_0_40"] >= 0.055,
        "Vanishing angle >= 55deg": vanishing_deg >= 55.0,
    }


def gz_max_and_angle(angles: Sequence[float], gz: Sequence[float]) -> Tuple[float, float]:
    """Discrete GZ maximum and the angle at which it occurs."""
    k = max(range(len(gz)), key=lambda i: gz[i])
    return gz[k], float(angles[k])


def vanishing_angle(angles: Sequence[float], gz: Sequence[float]) -> float:
    """Angle at which the righting curve ends (GZ returns to zero or curve terminates).

    Scanning starts at the GZ maximum (GZ(0)=0 is by construction not a crossing), so
    for a closed box that stays positive the function returns the last grid angle, and
    for a curve truncated at downflooding it returns the truncation angle -- which is
    the physically correct "usable range ends here" statement of IS Code 2008 2.3.
    """
    if max(gz) <= 0.0:
        return 0.0
    k = max(range(len(gz)), key=lambda i: gz[i])
    for i in range(k, len(gz) - 1):
        if gz[i] > 0.0 >= gz[i + 1]:
            w = gz[i] / (gz[i] - gz[i + 1])
            return angles[i] + w * (angles[i + 1] - angles[i])
    return float(angles[-1])


# ---------------------------------------------------------------------------
# 4. Baseline bundle for one hull (what a verification run compares against)
# ---------------------------------------------------------------------------
def barge_baseline(B: float, D: float, T: float, KG: float,
                   angles: Optional[Sequence[float]] = None,
                   open_top: bool = True) -> Dict[str, object]:
    """Full analytic reference bundle for a box barge.

    ``open_top=True`` truncates the curve at the downflooding angle, i.e. reports the
    physically admissible righting curve of an open-top barge.
    """
    GM = box_gm(B, T, KG)
    BM = box_bm(B, T)
    phi_f = flooding_angle_deg(B, D, T)
    if angles is None:
        angles = list(range(0, 91))
        if open_top and phi_f < 90.0:
            angles = sorted(set(angles) | {round(phi_f, 9)})
    angles = list(angles)
    gz = gz_curve(B, D, T, KG, angles)
    valid = [i for i, a in enumerate(angles) if (a <= phi_f + 1e-9 if open_top else True)]
    a_valid = [angles[i] for i in valid]
    gz_valid = [gz[i] for i in valid]
    areas = imo_areas(a_valid, gz_valid)
    gmax, amax = gz_max_and_angle(a_valid, gz_valid)
    van = vanishing_angle(a_valid, gz_valid)
    return {
        "geometry": {"B": B, "D": D, "T": T, "KG": KG, "cb": 1.0},
        "kb": box_kb(T), "bm": BM, "gm": GM,
        "flooding_angle_deg": phi_f,
        "wall_sided_valid_limit_deg": wall_sided_valid_limit_deg(B, D, T),
        "angles": list(angles),
        "gz": gz,
        "curve_angles": a_valid,
        "curve_gz": gz_valid,
        "gz_max": gmax,
        "gz_max_angle": amax,
        "vanishing_angle": van,
        "areas": areas,
        "imo": imo_criteria(GM, gmax, amax, areas, van),
        "open_top": open_top,
    }


if __name__ == "__main__":  # tiny smoke print
    b = barge_baseline(B=20.0, D=10.0, T=6.0, KG=5.0)
    print({k: (round(v, 4) if isinstance(v, float) else v)
           for k, v in b.items() if k in ("kb", "bm", "gm", "gz_max",
                                          "gz_max_angle", "vanishing_angle",
                                          "flooding_angle_deg")})