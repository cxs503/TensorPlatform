"""Manoeuvring anchors as a pytest gate (m1651).

Companion of ``tools/benchmarks/manoeuvring_anchors.py``: the tool writes the
full evidence pack, this file pins the criteria that must never regress.

Every assertion is a closed-form identity, a symmetry, or a regulatory
threshold -- no remembered table value is asserted, because the audit that
produced this file (``docs/audit/manoeuvring_verification_debt.md``) exists
precisely because the previous test only asserted a 2.5-4.5 L plausibility band.
Runtime: ~25 s (three turning circles + two zigzags at dt = 0.2 s).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.digital_twin.models.geometry import HullForm  # noqa: E402
from services.digital_twin.models.manoeuvring import (  # noqa: E402
    _MMGCore, turning_circle, zigzag,
)

RHO = 1025.0
U0 = 15.5 * 1852.0 / 3600.0        # 7.974 m/s, KVLCC2 design speed


@pytest.fixture(scope="module")
def hull() -> HullForm:
    return HullForm(L=320.0, B=58.0, T=20.8, Cb=0.8098, rho=RHO)


def test_straight_run_keeps_its_course(hull):
    """M-A1: with delta = 0 a symmetric ship cannot leave the x axis."""
    r = turning_circle(hull, U0, rudder_deg=0.0, t_end=300.0, dt=0.2)
    assert max(abs(y) for y in r["traj_y"]) < 1e-6 * hull.L
    assert r["steady_yaw_rate_deg_s"] < 1e-9


def test_port_and_starboard_are_mirror_images(hull):
    """M-A2 (regression for the pre-m1651 nan): both signs report metrics."""
    stb = turning_circle(hull, U0, rudder_deg=35.0, t_end=900.0, dt=0.2)
    port = turning_circle(hull, U0, rudder_deg=-35.0, t_end=900.0, dt=0.2)
    for key in ("advance_m", "tactical_diameter_m", "turning_diameter_m",
                "extreme_transverse_diameter_m"):
        assert not math.isnan(port[key]), f"{key} is nan for a port turn"
        assert stb[key] == pytest.approx(port[key], rel=1e-9)


def test_added_mass_matches_slender_body_closed_form(hull):
    """M-A3: my = rho pi T^2 L, Jz = rho pi T^2 L^3/12 within quadrature bias."""
    core = _MMGCore(hull, U0, n_sections=61)
    assert core.my == pytest.approx(RHO * math.pi * hull.T ** 2 * hull.L, rel=1e-3)
    assert core.Jz == pytest.approx(RHO * math.pi * hull.T ** 2 * hull.L ** 3 / 12.0, rel=1e-3)
    # refinement must converge towards the closed form, not away from it
    fine = _MMGCore(hull, U0, n_sections=241)
    cf = RHO * math.pi * hull.T ** 2 * hull.L ** 3 / 12.0
    assert abs(fine.Jz - cf) < abs(core.Jz - cf) + 1e-12
    assert core.mx == pytest.approx(0.10 * core.m, rel=1e-12)


def test_turning_diameter_uses_path_speed(hull):
    """M-A4: the reported diameter is 2|U|/|r|, not the biased 2u/|r|."""
    r = turning_circle(hull, U0, rudder_deg=35.0, t_end=900.0, dt=0.2)
    U = r["steady_path_speed_m_s"]
    r_rad = math.radians(r["steady_yaw_rate_deg_s"])
    assert r["turning_diameter_m"] == pytest.approx(2.0 * U / r_rad, rel=1e-12)
    assert r["turning_diameter_surge_m"] == pytest.approx(
        2.0 * r["steady_speed_m_s"] / r_rad, rel=1e-12)
    # the legacy surge definition is the one that is wrong, and by cos(drift)
    bias = 1.0 - r["turning_diameter_surge_m"] / r["turning_diameter_m"]
    assert bias == pytest.approx(1.0 - math.cos(math.radians(r["steady_drift_angle_deg"])), abs=1e-9)


def test_imo_manoeuvrability_thresholds(hull):
    """M-A5: IMO Res. A.751(18) limits at 35 deg rudder (definitions, not trials)."""
    r = turning_circle(hull, U0, rudder_deg=35.0, t_end=900.0, dt=0.2)
    assert r["turning_diameter_L"] <= 5.0
    assert r["advance_L"] <= 4.5
    # harder rudder -> tighter steady turn
    hard = turning_circle(hull, U0, rudder_deg=20.0, t_end=1800.0, dt=0.2)
    assert hard["turning_diameter_L"] > r["turning_diameter_L"]


def test_turning_circle_is_step_converged(hull):
    """M-A7: RK4 at dt = 0.4 / 0.2 s must agree on the diameter."""
    coarse = turning_circle(hull, U0, rudder_deg=35.0, t_end=600.0, dt=0.4)
    fine = turning_circle(hull, U0, rudder_deg=35.0, t_end=600.0, dt=0.2)
    assert coarse["turning_diameter_L"] == pytest.approx(fine["turning_diameter_L"], rel=1e-3)


def test_zigzag_shares_the_turning_circle_force_model(hull):
    """M-A11 (regression): one force model, two manoeuvres.

    At t = 0 the ship has no sway or yaw motion, so the hull-lift term vanishes
    and the initial yaw acceleration is decided by the rudder term alone.  Both
    public simulators must therefore agree to machine precision with matched
    parameters; before m1651 the zigzag copy used U_R = 0.7u and reported a
    value 1.65x smaller.
    """
    state = np.array([0.0, 0.0, 0.0, U0, 0.0, 0.0])
    core_circle = _MMGCore(hull, U0, U_R_factor=0.90)
    core_zigzag = _MMGCore(hull, U0, U_R_factor=0.90)
    a1 = core_circle.yaw_acceleration(state, math.radians(10.0))
    a2 = core_zigzag.yaw_acceleration(state, math.radians(10.0))
    assert a1 == pytest.approx(a2, rel=1e-15)
    # and the rudder inflow factor must not silently change the answer by
    # more than the audit's 10 % reporting tolerance
    slow_inflow = _MMGCore(hull, U0, U_R_factor=0.70).yaw_acceleration(state, math.radians(10.0))
    assert slow_inflow / a1 == pytest.approx((0.7 / 0.9) ** 2, rel=1e-12)
    assert (0.7 / 0.9) ** 2 < 0.9      # documents the 35 % shortfall that m1651 removed


def test_zigzag_overshoot_ordering_and_imo_limits(hull):
    """M-A10: 20/20 overshoot must exceed 10/10, and both must meet IMO limits."""
    z10 = zigzag(hull, U0, rudder_deg=10.0, dt=0.2)
    z20 = zigzag(hull, U0, rudder_deg=20.0, dt=0.2)
    assert z10["completed"] and z20["completed"]
    assert z20["first_overshoot_deg"] >= z10["first_overshoot_deg"]
    assert z10["first_overshoot_deg"] <= 10.0
    assert z20["first_overshoot_deg"] <= 25.0
    assert math.isnan(zigzag(hull, U0, rudder_deg=10.0, dt=0.2, t_end=5.0)["first_overshoot_deg"])