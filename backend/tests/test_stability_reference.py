"""Verification tests for the analytic wall-sided/barge stability reference.

These tests pin the *reference implementation* against (a) closed-form
hydrostatics, (b) the small-angle limit, (c) an independent polygon-clipping
computation, and (d) the documented validity envelope of the wall-sided
formula.  They are the machine-checkable evidence for
``docs/verification/stability_analytic_benchmark.md``.

Run:  cd backend && python -m pytest tests/test_stability_reference.py -q
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.stability_reference import (  # noqa: E402
    BargeGeometry,
    box_hydrostatics,
    exact_gz,
    exact_gz_curve,
    imo_areas,
    validity_angles_deg,
    wall_sided_gz,
)

BARGE = BargeGeometry(length=100.0, beam=20.0, draft=6.0, depth=10.0, kg=5.0, rho=1.025)
ANGLES = [0.0, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 21.0]


# ---------------------------------------------------------------------------
# Independent reference #2: polygon clipping (Sutherland-Hodgman)
# ---------------------------------------------------------------------------

def _clip_halfplane(poly, keep_below_z=0.0):
    """Clip a convex polygon [(Y, Z), ...] to Z <= keep_below_z."""
    out = []
    n = len(poly)
    for i in range(n):
        cur, nxt = poly[i], poly[(i + 1) % n]
        cur_in = cur[1] <= keep_below_z
        nxt_in = nxt[1] <= keep_below_z
        if cur_in:
            out.append(cur)
        if cur_in != nxt_in:
            t = (keep_below_z - cur[1]) / (nxt[1] - cur[1])
            out.append((cur[0] + t * (nxt[0] - cur[0]), keep_below_z))
    return out


def _poly_area_centroid(poly):
    a = cx = cz = 0.0
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a += cross
        cx += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    if abs(a) < 1e-14:
        raise ValueError("degenerate polygon")
    a *= 0.5
    return abs(a), cx / (6.0 * a), cz / (6.0 * a)


def gz_polygon(geom: BargeGeometry, phi_deg: float) -> float:
    """GZ of the heeled box from polygon clipping (independent algorithm)."""
    phi = math.radians(phi_deg)
    s, c = math.sin(phi), math.cos(phi)
    y0, y1 = -0.5 * geom.beam, 0.5 * geom.beam
    corners = [(y0, 0.0), (y1, 0.0), (y1, geom.depth), (y0, geom.depth)]
    rotated = [(y * c + z * s, -y * s + z * c) for y, z in corners]
    target = geom.beam * geom.draft

    # find the waterplane offset delta (world Z of the free surface) by bisection
    lo, hi = -geom.depth - geom.beam, geom.depth + geom.beam
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        poly = _clip_halfplane([(Y, Z - mid) for Y, Z in rotated])
        area = _poly_area_centroid(poly)[0] if len(poly) > 2 else 0.0
        if area < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-13:
            break
    mid = 0.5 * (lo + hi)
    poly = _clip_halfplane([(Y, Z - mid) for Y, Z in rotated])
    _, Yb, _ = _poly_area_centroid(poly)
    return Yb - geom.kg * s


# ---------------------------------------------------------------------------
# (a) closed-form hydrostatics
# ---------------------------------------------------------------------------

def test_box_hydrostatics_closed_form():
    hs = box_hydrostatics(BARGE)
    assert hs["kb"] == pytest.approx(BARGE.draft / 2.0, rel=0, abs=1e-12)
    assert hs["bm"] == pytest.approx(BARGE.beam ** 2 / (12.0 * BARGE.draft), rel=1e-14)
    assert hs["gm"] == pytest.approx(hs["kb"] + hs["bm"] - BARGE.kg, rel=1e-14)
    assert hs["displacement_t"] == pytest.approx(100.0 * 20.0 * 6.0 * 1.025, rel=1e-14)
    assert hs["cb"] == pytest.approx(1.0, abs=1e-15)


def test_geometry_sanity_rejects_inconsistent_hull():
    assert BARGE.sanity_check() == []
    assert BargeGeometry(100, 20, 10, 10, 5).sanity_check()  # draft == depth
    assert BargeGeometry(100, 20, 6, 10, 12).sanity_check()  # kg above deck
    assert BargeGeometry(20, 20, 6, 10, 5).sanity_check()    # L/B too small


# ---------------------------------------------------------------------------
# (b) physical limit cases
# ---------------------------------------------------------------------------

def test_gz_zero_upright():
    """Upright: KN = 0 by symmetry, so GZ(0) = 0 exactly."""
    r = exact_gz(BARGE, 0.0)
    assert r["gz"] == pytest.approx(0.0, abs=1e-12)
    assert r["kn"] == pytest.approx(0.0, abs=1e-12)


def test_submerged_area_conserved():
    """Constant-displacement solve must reproduce B*T at every heel angle."""
    for phi in [0.0, 5.0, 15.0, 25.0, 35.0, 45.0, 60.0]:
        r = exact_gz(BARGE, phi)
        assert r["submerged_area"] == pytest.approx(BARGE.beam * BARGE.draft, rel=1e-9)


def test_small_angle_limit_matches_metacentric_height():
    hs = box_hydrostatics(BARGE)
    for phi in (0.5, 1.0):
        gz = exact_gz(BARGE, phi)["gz"]
        linear = hs["gm"] * math.sin(math.radians(phi))
        assert gz == pytest.approx(linear, rel=2e-3)


# ---------------------------------------------------------------------------
# (c) independent polygon-clipping cross-check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phi", [2.0, 10.0, 21.0, 25.0, 35.0, 45.0, 60.0])
def test_exact_gz_matches_polygon_clipping(phi):
    ref = gz_polygon(BARGE, phi)
    got = exact_gz(BARGE, phi)["gz"]
    assert got == pytest.approx(ref, rel=2e-6, abs=1e-9), (phi, got, ref)


# ---------------------------------------------------------------------------
# (d) validity envelope of the wall-sided formula
# ---------------------------------------------------------------------------

def test_validity_envelope_angles():
    va = validity_angles_deg(BARGE)
    # deck edge immersion: tan(phi) = freeboard / (B/2) = 4/10
    assert va["deck_immersion_deg"] == pytest.approx(math.degrees(math.atan(0.4)), rel=1e-12)
    assert 20.0 < va["exact_limit_deg"] < 22.5


def test_wall_sided_exact_inside_envelope():
    """Wall-sided formula is exact for a box up to deck-edge immersion."""
    hs = box_hydrostatics(BARGE)
    limit = validity_angles_deg(BARGE)["exact_limit_deg"]
    for phi in ANGLES:
        if phi > limit:
            continue
        exact = exact_gz(BARGE, phi)["gz"]
        ws = wall_sided_gz(hs["gm"], hs["bm"], phi)
        assert ws == pytest.approx(exact, rel=1e-4, abs=1e-9), phi


def test_wall_sided_diverges_beyond_envelope():
    """Beyond deck immersion the formula over-predicts: >2% at 25 deg, >10% at 30 deg."""
    hs = box_hydrostatics(BARGE)
    e25 = exact_gz(BARGE, 25.0)["gz"]
    e30 = exact_gz(BARGE, 30.0)["gz"]
    assert wall_sided_gz(hs["gm"], hs["bm"], 25.0) / e25 - 1.0 > 0.02
    assert wall_sided_gz(hs["gm"], hs["bm"], 30.0) / e30 - 1.0 > 0.10


def test_exact_curve_has_maximum_and_vanishing_angle():
    """A box barge peaks near 40 deg and vanishes at 90 deg (reaches its side)."""
    curve = exact_gz_curve(BARGE, [float(a) for a in range(0, 91, 2)])
    gz = [p["gz"] for p in curve]
    peak = max(range(len(gz)), key=lambda i: gz[i])
    assert 30.0 <= curve[peak]["phi_deg"] <= 50.0
    # monotone decrease from the peak to 90 deg, where GZ returns to ~0
    tail = gz[peak:]
    assert all(b <= a + 1e-12 for a, b in zip(tail[:-1], tail[1:]))
    assert curve[-1]["gz"] == pytest.approx(0.0, abs=2e-3)


# ---------------------------------------------------------------------------
# (e) IMO area integration
# ---------------------------------------------------------------------------

def test_imo_areas_constant_curve_is_analytic():
    curve = [(float(a), 1.0) for a in range(0, 41)]
    areas = imo_areas(curve)
    assert areas["area_0_30"] == pytest.approx(math.radians(30.0), rel=1e-12)
    assert areas["area_30_40"] == pytest.approx(math.radians(10.0), rel=1e-12)
    assert areas["area_0_40"] == pytest.approx(math.radians(40.0), rel=1e-12)


def test_imo_areas_linear_curve_is_analytic():
    # GZ = a * phi(rad) -> area over [0, 30] = 0.5 * a * phi_rad^2
    a = 2.0
    curve = [(float(x), a * math.radians(x)) for x in range(0, 41)]
    areas = imo_areas(curve)
    assert areas["area_0_30"] == pytest.approx(0.5 * a * math.radians(30.0) ** 2, rel=1e-9)