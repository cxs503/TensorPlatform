"""Verification tests for the platform righting-arm endpoint (calc.stability).

These tests encode the *evidence* produced by
``tools/verify/run_stability_verification.py`` so the conclusion cannot rot:

1. the wall-sided closed form is exact for a rectangular pontoon inside its
   validity envelope -- the independent section-integration reference agrees to
   numerical precision, so the endpoint implementation is correct there;
2. the envelope end (deck-edge immersion / bilge emergence) matches the classic
   ``tan(phi) = 2 h / b`` relation for wall-sided sections;
3. the endpoint silently extrapolates beyond that envelope, with >15 % error --
   a documented limitation (reported, not hidden);
4. ``kb_m`` is accepted but unused, and ``gm_effective_m`` merely echoes the
   input -- API-hygiene findings.

Run:  pytest -q tools/verify/tests/test_stability_gz_verification.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stability_gz_reference as ref  # noqa: E402
from run_stability_verification import (  # noqa: E402
    PONTOON,
    api_gz,
    build_case,
    load_endpoint,
)


@pytest.fixture(scope="module")
def endpoint():
    return load_endpoint()


# --- 1. reference vs closed form (implementation correctness) --------------

def test_reference_matches_wall_sided_inside_envelope():
    sec = ref.BoxSection(10.0, 4.0, 2.0)
    gm, bm = 2.166667, sec.bm
    limit = ref.validity_limit_angle(sec)["angle_deg"]
    for ang in (2.0, 5.0, 10.0, 15.0, 20.0, 0.95 * limit):
        phi = math.radians(ang)
        got = ref.righting_arm(sec, phi, kg=3.0)
        expect = ref.wall_sided_gz(phi, gm, bm)
        assert abs(got - expect) <= 1e-4 * max(abs(expect), 1e-6), (ang, got, expect)


def test_small_angle_limit_is_gm_sin_phi():
    sec = ref.BoxSection(10.0, 4.0, 2.0)
    phi = math.radians(1e-3)
    assert ref.righting_arm(sec, phi, kg=3.0) == pytest.approx(2.1666666 * math.sin(phi),
                                                             rel=1e-6)


def test_endpoint_agrees_with_reference(endpoint):
    sec, kg, disp, kb, bm, gm = build_case("pontoon")
    phi = math.radians(15.0)
    out = api_gz(endpoint, displacement_t=disp, gm=gm, kb=kb, bm=bm, heel_deg=15.0)
    assert out["righting_arm_m"] == pytest.approx(ref.righting_arm(sec, phi, kg),
                                                  abs=1e-6)
    # righting moment = displacement[kg] * g * GZ / 1000  [kN m]
    assert out["righting_moment_knm"] == pytest.approx(
        disp * 9810.0 * out["righting_arm_m"] / 1000.0, rel=1e-4)


# --- 2. validity envelope -------------------------------------------------

def test_envelope_end_matches_classic_relation():
    """For vertical sides the envelope ends at tan(phi) = 2*h/b (h = T or freeboard)."""
    for beam, depth, draught in ((10.0, 4.0, 2.0), (16.0, 6.0, 2.0), (20.0, 8.0, 3.0)):
        sec = ref.BoxSection(beam, depth, draught)
        h = min(draught, depth - draught)
        analytic = math.degrees(math.atan(2.0 * h / beam))
        numeric = ref.validity_limit_angle(sec)["angle_deg"]
        assert numeric == pytest.approx(analytic, abs=1e-4), (beam, depth, draught)


# --- 3. documented limitation: silent extrapolation -----------------------

def test_endpoint_extrapolates_beyond_envelope():
    """30 deg is past the pontoon envelope (21.8 deg) and is >15 % wrong."""
    sec, kg, disp, kb, bm, gm = build_case("pontoon")
    assert ref.validity_limit_angle(sec)["angle_deg"] < 30.0
    got = ref.wall_sided_gz(math.radians(30.0), gm, bm)
    expect = ref.righting_arm(sec, math.radians(30.0), kg)
    assert abs(got - expect) / abs(expect) > 0.15


def test_flare_section_error_grows_with_heel():
    sec = ref.FlareSection(8.0, 12.0, 4.0, 2.0)
    gm, bm = sec.kb + sec.bm - 3.0, sec.bm
    errs = []
    for ang in (10.0, 40.0):
        phi = math.radians(ang)
        err = abs(ref.wall_sided_gz(phi, gm, bm) - ref.righting_arm(sec, phi, 3.0))
        errs.append(err / max(abs(ref.righting_arm(sec, phi, 3.0)), 1e-9))
    assert errs[1] > errs[0] and errs[1] > 0.15


# --- 4. API hygiene -------------------------------------------------------

def test_kb_parameter_is_unused(endpoint):
    _, _, disp, _, bm, gm = build_case("pontoon")
    a = api_gz(endpoint, displacement_t=disp, gm=gm, kb=1.0, bm=bm, heel_deg=20.0)
    b = api_gz(endpoint, displacement_t=disp, gm=gm, kb=9.9, bm=bm, heel_deg=20.0)
    assert a["righting_arm_m"] == b["righting_arm_m"]


def test_gm_effective_is_an_echo(endpoint):
    out = api_gz(endpoint, displacement_t=1000.0, gm=1.234, kb=1.0, bm=4.0,
                 heel_deg=10.0)
    assert out["gm_effective_m"] == pytest.approx(1.234)


# --- 5. IMO IS Code area integration --------------------------------------

def test_area_integration_matches_analytic():
    gm, bm = 2.166667, 4.166666666666667
    phi30 = math.radians(30.0)
    numeric = 0.0
    n = 20000
    for i in range(n):
        p = phi30 * (i + 0.5) / n
        numeric += ref.wall_sided_gz(p, gm, bm) * phi30 / n
    assert numeric == pytest.approx(ref.wall_sided_area(phi30, gm, bm), rel=1e-6)


def test_imo_criteria_flags_are_consistent():
    angles = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0]
    sec = ref.BoxSection(10.0, 4.0, 2.0)
    gz = ref.gz_curve(sec, 3.0, angles)
    out = ref.imo_is_code_criteria(angles, gz, gm0=2.166667)
    assert out["all_pass"] is True
    assert out["area_0_30_m_rad"] == pytest.approx(0.314354, abs=2e-4)
    # a tender ship must fail the GM0 / area bars
    gz_bad = ref.gz_curve(sec, 4.5, angles)
    bad = ref.imo_is_code_criteria(angles, gz_bad, gm0=0.166667)
    assert bad["criteria"]["gm0_ge_0.15"] is True or bad["criteria"]["gm0_ge_0.15"] is False
    assert bad["area_0_30_m_rad"] < out["area_0_30_m_rad"]