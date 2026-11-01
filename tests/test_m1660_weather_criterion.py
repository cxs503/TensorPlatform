"""Acceptance tests for the IS Code 2008 §2.3 weather criterion (m1660).

Anchors (no tabulated reference values are used as evidence anywhere):

A  quadrature vs the **closed-form** anti-derivative of the wall-sided arm
B  Simpson convergence order recovered numerically (~4)
C  wind-lever identities: linear in ``P*A*Z``, exact inverse in displacement,
   ``lw2 = 1.5 lw1``
D  roll-angle expression structure (monotonicity / limits), the transcribed
   tables are explicitly *not* asserted for values
E  criterion algebra: ``lw1 -> 0`` degenerates to the pure area test; the
   pass/fail boundary moves monotonically with windage area
F  vanishing-angle detection is self-consistent (``GZ(theta_f) ~ 0``)
G  domain boundary of the shipped wall-sided form is *demonstrated*, so the
   report's "extrapolation" caveat is machine-checked rather than claimed
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.ship_performance.stability import weather_criterion as wc  # noqa: E402
from services.stability_reference import (  # noqa: E402
    box_parameters,
    exact_gz_box,
    wall_sided_gz,
    wall_sided_limit_deg,
)

BOX = dict(beam_m=16.0, draft_m=6.0, kg_m=6.0, depth_m=9.0)
DISP_T = 100.0 * 16.0 * 6.0 * 1.025  # 100 m box barge -> 9840 t


def _gz_wall(deg: float) -> float:
    p = box_parameters(**BOX)
    return wall_sided_gz(p["gm_m"], p["bm_m"], math.radians(deg))


def _gz_exact(deg: float) -> float:
    return exact_gz_box(BOX["beam_m"], BOX["draft_m"], BOX["kg_m"],
                        math.radians(deg), BOX["depth_m"])["gz_m"]


# --------------------------------------------------------------------------- #
# A — quadrature against closed form
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("gm,bm,a0,a1", [
    (0.556, 3.5556, 21.0, 40.0),
    (0.20, 8.0, 5.0, 40.0),
    (1.50, 1.0, 0.0, 30.0),
    (0.05, 12.0, 25.0, 40.0),
])
def test_simpson_matches_closed_form_antiderivative(gm, bm, a0, a1):
    """A: Simpson vs F(th) = -GM cos th + 0.5 BM (sec th + cos th)."""
    f = lambda d: wall_sided_gz(gm, bm, math.radians(d))  # noqa: E731
    num = wc.simpson_integrate(f, a0, a1, 400)
    ana = wc.wall_sided_area_analytic(gm, bm, a0, a1)
    assert abs(num - ana) / abs(ana) < 1e-9


def test_antiderivative_derivative_is_righting_arm():
    """A: d/dth F(th) == GZ(th) by central difference (guards the algebra)."""
    gm, bm = 0.556, 3.5556
    h = 1e-3
    for deg in (10.0, 20.0, 30.0, 38.0):
        d = (wc.wall_sided_area_analytic(gm, bm, deg - h, deg + h)
             ) / (2.0 * math.radians(h))
        gz = wall_sided_gz(gm, bm, math.radians(deg))
        assert abs(d - gz) < 1e-8 * max(1.0, abs(gz))


# --------------------------------------------------------------------------- #
# B — convergence order
# --------------------------------------------------------------------------- #
def test_simpson_is_fourth_order():
    """B: halving the step must cut the error by ~2^4."""
    gm, bm = 0.556, 3.5556
    f = lambda d: wall_sided_gz(gm, bm, math.radians(d))  # noqa: E731
    ana = wc.wall_sided_area_analytic(gm, bm, 21.0, 40.0)
    errs = [abs(wc.simpson_integrate(f, 21.0, 40.0, n) - ana) for n in (20, 40, 80)]
    assert errs[0] > errs[1] > errs[2] > 0.0
    order = math.log2(errs[0] / errs[2]) / 2.0
    assert 3.7 < order < 4.3, order


# --------------------------------------------------------------------------- #
# C — wind heeling lever identities
# --------------------------------------------------------------------------- #
def test_lw1_linear_in_windage_moment():
    a = wc.wind_heeling_levers(300.0, 7.5, DISP_T)["lw1_m"]
    b = wc.wind_heeling_levers(600.0, 7.5, DISP_T)["lw1_m"]
    c = wc.wind_heeling_levers(300.0, 15.0, DISP_T)["lw1_m"]
    assert abs(b - 2.0 * a) < 1e-15 and abs(c - 2.0 * a) < 1e-15


def test_lw1_inverse_in_displacement():
    a = wc.wind_heeling_levers(300.0, 7.5, DISP_T)["lw1_m"]
    b = wc.wind_heeling_levers(300.0, 7.5, 2.0 * DISP_T)["lw1_m"]
    assert abs(b - 0.5 * a) / a < 1e-15


def test_lw1_value_and_gust_factor():
    """C: closed-form value at P = 504 Pa, plus lw2 = 1.5 lw1."""
    le = wc.wind_heeling_levers(300.0, 7.5, DISP_T)
    manual = 504.0 * 300.0 * 7.5 / (1000.0 * 9.80665 * DISP_T)
    assert abs(le["lw1_m"] - manual) < 1e-15
    assert abs(le["lw2_m"] - 1.5 * le["lw1_m"]) < 1e-15
    assert wc.WIND_PRESSURE_PA == 504.0 and wc.LW2_FACTOR == 1.5


def test_wind_levers_are_a_length():
    """C: dimensional plausibility — 504 Pa on a 300 m^2 panel gives cm-scale."""
    le = wc.wind_heeling_levers(300.0, 7.5, DISP_T)
    assert 0.005 < le["lw1_m"] < 0.05


# --------------------------------------------------------------------------- #
# D — roll-angle expression structure (values are NOT asserted)
# --------------------------------------------------------------------------- #
def test_r_factor_identity():
    """D: r = 0.73 + 0.6 OG/d -> r = 0.73 exactly when KG = d."""
    assert abs(wc.r_factor(6.0, 6.0) - 0.73) < 1e-15
    assert abs(wc.r_factor(7.2, 6.0) - (0.73 + 0.6 * 0.2)) < 1e-15


def test_theta1_is_monotone_in_inputs():
    base = wc.theta1_is_code(beam_m=16.0, draft_m=6.0, cb=0.70,
                             kg_m=6.0, roll_period_s=12.0)["theta1_deg"]
    wide = wc.theta1_is_code(beam_m=20.0, draft_m=6.0, cb=0.70,
                             kg_m=6.0, roll_period_s=12.0)["theta1_deg"]
    slow = wc.theta1_is_code(beam_m=16.0, draft_m=6.0, cb=0.70,
                             kg_m=6.0, roll_period_s=20.0)["theta1_deg"]
    assert wide < base, "wider hull (larger B/T) must reduce the roll back angle"
    assert slow < base, "longer roll period must reduce the roll back angle"
    assert 0.0 < base < 45.0


def test_theta1_provenance_is_withheld():
    """D: the transcribed tables must never be presented as verified evidence."""
    out = wc.theta1_is_code(beam_m=16.0, draft_m=6.0, cb=0.70,
                            kg_m=6.0, roll_period_s=12.0)
    assert out["provenance"].startswith("WITHHELD")
    assert wc.COEFFICIENT_PROVENANCE["b_area"].startswith("REAL")
    assert wc.COEFFICIENT_PROVENANCE["theta1_tables"].startswith("WITHHELD")


def test_table_lookup_is_clamped_and_monotone():
    assert wc.x2_block_coefficient(0.70) == pytest.approx(1.0)
    assert wc.x2_block_coefficient(0.45) == pytest.approx(0.75)
    xs = [wc.x1_beam_draft(2.4 + 0.5 * k, 1.0) for k in range(20)]
    assert all(x0 >= x1 for x0, x1 in zip(xs, xs[1:]))
    ss = [wc.s_roll_period(t) for t in (5, 8, 12, 16, 20, 24)]
    assert all(s0 > s1 for s0, s1 in zip(ss, ss[1:]))
    with pytest.raises(ValueError):
        wc.x2_block_coefficient(0.0)


# --------------------------------------------------------------------------- #
# E/F — criterion algebra
# --------------------------------------------------------------------------- #
def test_zero_wind_degenerates_to_area_test():
    """E: with lw1 = 0 the criterion reduces to integral(GZ) >= 0."""
    res = wc.weather_criterion(_gz_exact, theta1_deg=21.0, windage_area_m2=0.0,
                               windage_centroid_height_m=0.0, displacement_t=DISP_T)
    assert res.lw1_m == 0.0 and res.area_a_m_rad == 0.0
    assert res.verdict == ("pass" if res.area_gz_m_rad > 0 else "fail")
    assert abs(res.margin_m_rad - res.area_gz_m_rad) < 1e-15


def test_pass_fail_boundary_moves_monotonically_with_windage():
    """E: sweeping the windage area must flip pass->fail exactly once."""
    theta1 = 21.0
    verdicts = []
    for area in (0.0, 200.0, 600.0, 1200.0, 2400.0, 4800.0):
        r = wc.weather_criterion(_gz_exact, theta1_deg=theta1,
                                 windage_area_m2=area,
                                 windage_centroid_height_m=7.5,
                                 displacement_t=DISP_T)
        verdicts.append((area, r.margin_m_rad, r.verdict))
    margins = [v[1] for v in verdicts]
    assert all(m0 > m1 for m0, m1 in zip(margins, margins[1:])), margins
    assert verdicts[0][2] == "pass"
    assert verdicts[-1][2] == "fail"


def test_criterion_undefined_above_b_area_limit():
    res = wc.weather_criterion(_gz_exact, theta1_deg=45.0,
                               windage_area_m2=300.0,
                               windage_centroid_height_m=7.5,
                               displacement_t=DISP_T)
    assert res.verdict == "undefined"


def test_vanishing_angle_is_self_consistent():
    """F: the detected theta_f must be a zero of the righting arm (or the cap)."""
    # KG = 6.0 -> GM = 0.556 m: the box still has righting moment at 40 deg,
    # so the criterion must cap theta_f at the 40 deg limit.
    res = wc.weather_criterion(_gz_exact, theta1_deg=21.0,
                               windage_area_m2=300.0,
                               windage_centroid_height_m=7.5,
                               displacement_t=DISP_T)
    assert res.theta_f_deg == 40.0
    assert _gz_exact(40.0) > 0.0

    # KG = 6.5 -> GM = 0.056 m: vanishing stability below 40 deg, and the
    # detected angle must be an actual zero of the arm.
    def gz_light(deg: float) -> float:
        return exact_gz_box(BOX["beam_m"], BOX["draft_m"], 6.5,
                            math.radians(deg), BOX["depth_m"])["gz_m"]

    res2 = wc.weather_criterion(gz_light, theta1_deg=21.0,
                                windage_area_m2=300.0,
                                windage_centroid_height_m=7.5,
                                displacement_t=DISP_T)
    assert 30.0 < res2.theta_f_deg < 40.0
    assert abs(gz_light(res2.theta_f_deg)) < 5e-3


def test_explicit_downflooding_angle_is_honoured():
    res = wc.weather_criterion(_gz_exact, theta1_deg=21.0, theta_f_deg=25.0,
                               windage_area_m2=300.0,
                               windage_centroid_height_m=7.5,
                               displacement_t=DISP_T)
    assert res.theta_f_deg == 25.0
    assert any("down-flooding" in n for n in res.notes)


# --------------------------------------------------------------------------- #
# G — the wall-sided domain boundary (machine-checked caveat)
# --------------------------------------------------------------------------- #
def test_wall_sided_is_exact_inside_its_domain_and_diverges_outside():
    limit = wall_sided_limit_deg(BOX["beam_m"], BOX["draft_m"], BOX["depth_m"])
    assert 18.0 < limit < 25.0
    inside = abs(_gz_wall(limit - 2.0) - _gz_exact(limit - 2.0))
    outside = abs(_gz_wall(limit + 15.0) - _gz_exact(limit + 15.0))
    assert inside < 5e-3
    assert outside > 20.0 * inside


def test_verdict_differs_between_shipped_and_exact_righting_arm():
    """G: the headline risk — the extrapolated arm can flip the verdict."""
    common = dict(theta1_deg=21.0, windage_area_m2=300.0,
                  windage_centroid_height_m=7.5, displacement_t=DISP_T)
    r_wall = wc.weather_criterion(_gz_wall, **common)
    r_exact = wc.weather_criterion(_gz_exact, **common)
    assert r_wall.margin_ratio > 1.0
    assert r_exact.margin_m_rad < r_wall.margin_m_rad


def test_area_under_curve_trapezoid_matches_simpson_for_polyline():
    degs = [21.0 + 0.5 * k for k in range(39)]
    vals = [_gz_exact(d) for d in degs]
    trap = wc.area_under_curve(degs, vals)
    simp = wc.simpson_integrate(_gz_exact, degs[0], degs[-1], 400)
    assert abs(trap - simp) / abs(simp) < 5e-3


def test_result_is_serialisable():
    res = wc.weather_criterion(_gz_exact, theta1_deg=21.0,
                               windage_area_m2=300.0,
                               windage_centroid_height_m=7.5,
                               displacement_t=DISP_T)
    d = res.as_dict()
    assert set(d) >= {"verdict", "lw1_m", "lw2_m", "area_a_m_rad",
                      "area_b_m_rad", "margin_m_rad", "provenance"}
    assert d["verdict"] in {"pass", "fail", "undefined"}


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        wc.wind_heeling_levers(300.0, 7.5, 0.0)
    with pytest.raises(ValueError):
        wc.simpson_integrate(math.sin, 40.0, 20.0, 10)
    with pytest.raises(ValueError):
        wc.wall_sided_area_analytic(0.5, 3.0, 40.0, 20.0)