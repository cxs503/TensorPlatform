"""Tests for the hull-girder structural strength module (discipline: 结构).

Every assertion compares the implementation with a reference that is derived
independently of it - a textbook closed form, the rigid-plastic limit, a
hand-derived two-flange couple, a rule-formula property or a physical bound - so
a green run means the code reproduces mathematics, not its own output.

Run:  python3 -m pytest tests/test_hull_girder.py -q
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify.hull_girder import loads, section, smith  # noqa: E402

SY = 235e6
E_STEEL = 206e9
NU = 0.3


# ------------------------------------------------------------------ section props
def test_box_section_matches_closed_form():
    """A and I of a concentric box vs A = BD - bh, I = (BD^3 - bh^3)/12."""
    B, D, t = 20.0, 12.0, 0.020
    sec = section.make_box_section(B=B, D=D, t=t)
    a, zc, i = sec.rect_moments()
    a_ref, zc_ref, i_ref = section.box_closed_form(B, D, t)
    assert a == pytest.approx(a_ref, rel=1e-12)
    assert zc == pytest.approx(zc_ref, rel=1e-12)
    assert i == pytest.approx(i_ref, rel=1e-12)


def test_green_theorem_agrees_with_rectangle_integration():
    """Two independent algorithms, same section: agreement to float precision."""
    sec = section.make_midship_section()
    a_r, z_r, i_r = sec.rect_moments()
    a_p, z_p, i_p = sec.polygon_moments()
    assert a_p == pytest.approx(a_r, rel=1e-9)
    assert z_p == pytest.approx(z_r, rel=1e-9)
    assert i_p == pytest.approx(i_r, rel=1e-9)


def test_strip_model_converges_to_exact_inertia():
    sec = section.make_midship_section()
    _, _, i_ref = sec.rect_moments()
    errs = []
    for n in (10, 40, 100):
        _, _, i = sec.strip_props(n_strips=n)
        errs.append(abs(i - i_ref) / i_ref)
    assert errs[1] < 1e-3, errs
    assert errs[2] < errs[0], "refinement must reduce the discretisation error"


def test_section_modulus_ordering():
    """Thicker bottom plate -> larger Z_bottom than Z_deck (asymmetry is captured)."""
    sec = section.make_midship_section(t_deck=0.020, t_bottom=0.040)
    z_bot, z_deck = sec.section_moduli()
    assert z_bot > 0 and z_deck > 0
    assert z_bot > z_deck


# --------------------------------------------------------------- plastic modulus
def test_plastic_modulus_of_box_matches_closed_form():
    """Concentric box: Z_p = (B D^2 - b h^2)/4 (hand derivation)."""
    B, D, t = 20.0, 12.0, 0.020
    sec = section.make_box_section(B=B, D=D, t=t)
    zp, z_eq = smith.plastic_modulus(sec.parts)
    b, h = B - 2 * t, D - 2 * t
    assert zp == pytest.approx((B * D ** 2 - b * h ** 2) / 4.0, rel=1e-12)
    assert z_eq == pytest.approx(D / 2.0, rel=1e-9)


def test_equal_area_axis_splits_area_in_half():
    sec = section.make_midship_section()
    z_eq = smith.equal_area_axis(sec.parts)
    below = 0.0
    for p in sec.parts:
        top = max(p.z0, min(z_eq, p.z1))
        if top > p.z0:
            below += (p.y1 - p.y0) * (top - p.z0)
    assert below == pytest.approx(sec.area() / 2.0, rel=1e-6)


# ------------------------------------------------------- Smith / rigid-plastic
@pytest.mark.parametrize("name,sec_builder", [
    ("box-B20", lambda: section.make_box_section(B=20.0, D=12.0, t=0.030, panel_breadth=0.30)),
    ("box-B30", lambda: section.make_box_section(B=30.0, D=18.0, t=0.040, panel_breadth=0.35)),
])
def test_smith_reproduces_rigid_plastic_limit(name, sec_builder):
    """Stocky plating -> element law is LEPP -> M_u must equal sigma_y Z_p."""
    sec = sec_builder()
    res = smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU, mode="euler")
    assert min(e.sigma_cr for e in res["elements"]) >= SY, "fixture must be stocky"
    assert res["M_u"] == pytest.approx(res["M_p"], rel=0.03), (name, res["M_u"], res["M_p"])


def test_two_flange_closed_form_both_directions():
    """Buckling regime: M_u = min(sigma_u A_comp, sigma_y A_ten) * D."""
    sec = section.make_two_flange_section(B=20.0, D=12.0, t_deck=0.020, t_bottom=0.030,
                                          panel_breadth=1.2)
    a_deck, a_bot = 20.0 * 0.020, 20.0 * 0.030
    scr_deck = section.plate_buckling_stress(0.020, 1.2, E=E_STEEL, nu=NU)
    scr_bot = section.plate_buckling_stress(0.030, 1.2, E=E_STEEL, nu=NU)
    for direction, a_comp, a_ten, scr in ((-1, a_deck, a_bot, scr_deck),
                                          (+1, a_bot, a_deck, scr_bot)):
        res = smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU, direction=direction)
        ref = smith.two_flange_closed_form(a_comp, a_ten, 12.0, SY, min(SY, scr))
        assert res["na_failures"] == 0
        assert abs(res["M_u"]) == pytest.approx(ref["M"], rel=0.03), (direction, res["M_u"], ref["M"])


def test_collapse_is_bracketed_by_elastic_and_plastic_moments():
    """0 < M_u <= M_p everywhere; M_u must not exceed the rigid-plastic theorem bound."""
    for pb in (0.6, 1.0, 2.0, 3.0):
        sec = section.make_midship_section(panel_breadth=pb)
        res = smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU)
        assert 0.0 < res["M_u"] <= res["M_p"] * (1 + 1e-9), (pb, res["M_u"], res["M_p"])


def test_ultimate_moment_falls_with_plate_slenderness():
    mus = []
    for pb in (0.6, 1.0, 1.5, 2.0, 3.0):
        sec = section.make_midship_section(panel_breadth=pb)
        mus.append(smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU)["M_u"])
    assert all(mus[i] >= mus[i + 1] for i in range(len(mus) - 1)), mus


def test_hogging_and_sagging_are_both_resolved():
    """Regression guard: the neutral-axis bisection used to be sign-dependent and
    returned a non-equilibrium axis for sagging (direction = -1)."""
    sec = section.make_midship_section()
    hog = smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU, direction=+1)
    sag = smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU, direction=-1)
    assert hog["na_failures"] == 0 and sag["na_failures"] == 0
    assert hog["M_u"] > 0 and sag["M_u"] < 0
    # the deck (0.035 m) and bottom (0.038 m) differ, so the two capacities differ
    assert abs(abs(hog["M_u"]) - abs(sag["M_u"])) / abs(hog["M_u"]) > 1e-3


def test_von_karman_mode_is_not_weaker_than_euler():
    """Post-buckling plateau sqrt(sigma_y sigma_cr) >= sigma_cr, so M_u can only grow."""
    sec = section.make_midship_section()
    e = smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU, mode="euler")["M_u"]
    v = smith.collapse_analysis(sec, sigma_y=SY, E=E_STEEL, nu=NU, mode="von_karman")["M_u"]
    assert v >= e


# --------------------------------------------------------------------- S11 loads
def test_s11_coefficient_joins_and_plateau():
    assert loads.s11_C(300.0 - 1e-9) == pytest.approx(10.75, rel=1e-6)
    assert loads.s11_C(300.0 + 1e-9) == pytest.approx(10.75, rel=1e-6)
    assert loads.s11_C(350.0 - 1e-9) == pytest.approx(10.75, rel=1e-6)
    assert loads.s11_C(350.0 + 1e-9) == pytest.approx(10.75, rel=1e-6)
    assert loads.s11_C(320.0) == pytest.approx(10.75, rel=1e-12)
    assert loads.s11_C(90.0) == pytest.approx(10.75 - 2.1 ** 1.5, rel=1e-12)


def test_s11_coefficient_endpoint_values():
    """C(90) = 10.75 - 2.1^1.5 = 7.70681, C(500) = 10.75 - 1.0 = 9.75."""
    assert loads.s11_C(90.0) == pytest.approx(7.70681, abs=1e-4)
    assert loads.s11_C(500.0) == pytest.approx(9.75, abs=1e-12)


def test_s11_wave_moment_matches_hand_calculation():
    """VLCC-like: L=320 m, B=58 m, Cb=0.8098 -> M_wh = 0.19*10.75*L^2*B*Cb."""
    m = loads.s11_wave_bending_moments(320.0, 58.0, 0.8098)
    hand = 0.19 * 10.75 * 320.0 ** 2 * 58.0 * 0.8098
    assert m["M_wh_kNm"] == pytest.approx(hand, rel=1e-12)
    assert m["M_wh_kNm"] / 1e6 == pytest.approx(9.82, abs=0.02)  # [GNm]
    assert m["hogging_over_sagging"] == pytest.approx(0.19 / 0.11, rel=1e-12)


def test_s11_rejects_out_of_range_length():
    with pytest.raises(ValueError):
        loads.s11_C(80.0)
    with pytest.raises(ValueError):
        loads.s11_C(501.0)


def test_bending_stress_and_rule_allowable():
    assert loads.bending_stress(1e9, 10.0) == pytest.approx(100e6)
    assert loads.rule_allowable_stress(320.0) == pytest.approx(175e6)
    assert loads.rule_allowable_stress(320.0, k=1.39) == pytest.approx(175e6 / 1.39)
    with pytest.raises(ValueError):
        loads.rule_allowable_stress(80.0)


# ---------------------------------------------------------------- harness contract
def test_benchmark_harness_all_pass():
    """The full verification harness must be green (25 checks)."""
    from tools.verify.hull_girder import benchmarks

    out = benchmarks.run_all(probe_platform=False)
    payload = out["payload"]
    failures = [r["id"] for r in payload["benchmarks"] if not r["passed"]]
    assert not failures, failures
    assert payload["summary"]["n"] >= 25
    assert payload["section_summary"]["na_failures"] == 0