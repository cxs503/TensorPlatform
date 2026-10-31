"""Unit / physics tests for the wind-current-ice coupled manoeuvring module.

Mission W3-N08 acceptance criterion (d): at least 12 passing tests covering the
wind load model, the current load model, the ice model, the MMG core, the
turning-circle / zigzag simulators and the coupling.

Run with::

    cd backend && python -m pytest tests/test_ship_env_maneuvering.py -q
"""

from __future__ import annotations

import math
import os
import sys

import pytest

# The physics package lives in ``backend/physics``; ``backend/tests/conftest.py``
# imports the whole FastAPI service stack (optional heavy deps), so the pure
# physics tests live here and bootstrap the import path explicitly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from physics.ship_env_maneuvering import data as D
from physics.ship_env_maneuvering import loads as L
from physics.ship_env_maneuvering import mmg as M
from physics.ship_env_maneuvering import validation as V

VESSEL = D.VESSEL_VLCC
SPEED = 7.717          # m/s (15 kn service speed of the reference hull)


# ---------------------------------------------------------------- geometry ---
def test_vessel_particulars_are_consistent():
    assert VESSEL.L == pytest.approx(320.0)
    assert VESSEL.volume == pytest.approx(
        VESSEL.Cb * VESSEL.L * VESSEL.B * VESSEL.T)
    # wetted surface of a 320 m VLCC is of order 25 000 - 30 000 m^2
    assert 2.4e4 < VESSEL.S_wetted < 3.1e4
    # mass ~ displacement of a loaded VLCC
    assert 2.8e8 < VESSEL.mass < 3.6e8
    # rudder area ratio in the typical VLCC range
    assert 0.008 < VESSEL.A_R / (VESSEL.L * VESSEL.T) < 0.02


# ------------------------------------------------------------------ wind ----
def test_wind_load_scales_with_speed_squared():
    a = L.wind_load(VESSEL, 10.0, 60.0)
    b = L.wind_load(VESSEL, 20.0, 60.0)
    assert b.magnitude / a.magnitude == pytest.approx(4.0, rel=1e-9)
    assert b.N / a.N == pytest.approx(4.0, rel=1e-9)


def test_wind_coefficient_symmetry_invariants():
    for psi in (0.0, 30.0, 45.0, 90.0, 135.0, 180.0):
        assert L.WIND_CX(psi) == pytest.approx(L.WIND_CX(-psi), abs=1e-9)
        assert L.WIND_CY(-psi) == pytest.approx(-L.WIND_CY(psi), abs=1e-9)
        assert L.WIND_CN(-psi) == pytest.approx(-L.WIND_CN(psi), abs=1e-9)
    # zero-crossings of the odd coefficients at bow / stern wind
    assert abs(L.WIND_CY(0.0)) < 1e-6 and abs(L.WIND_CN(180.0)) < 1e-6


def test_wind_load_sign_conventions():
    head = L.wind_load(VESSEL, 20.0, 0.0)
    assert head.X < 0.0 and abs(head.Y) < 1e-6      # head wind pushes aft
    follow = L.wind_load(VESSEL, 20.0, 180.0)
    assert follow.X > 0.0                            # following wind pushes fwd
    beam = L.wind_load(VESSEL, 20.0, 90.0)           # wind from starboard
    assert beam.Y < 0.0                              # -> force to port
    assert beam.N > 0.0                              # windage aft -> bow to stbd
    port_beam = L.wind_load(VESSEL, 20.0, -90.0)
    assert port_beam.Y == pytest.approx(-beam.Y, rel=1e-9)
    assert port_beam.N == pytest.approx(-beam.N, rel=1e-9)


def test_wind_load_matches_independent_projected_area_model():
    """Acceptance criterion (a): < 10 % at psi = 0/45/90/135 deg."""
    result = V.wind_criterion(VESSEL)
    assert result["passed"] is True
    assert result["max_rel_err_pct"] < V.THRESHOLD_PCT
    assert len(result["rows"]) == 4
    for row in result["rows"]:
        assert row["force_rel_err_pct"] < V.THRESHOLD_PCT


def test_wind_load_matches_imo_is_code_beam_pressure():
    """psi = 90 deg must reproduce P = 0.504 V^2 of the IMO IS Code 2008."""
    result = V.wind_criterion(VESSEL)
    assert result["imo_anchor"]["rel_err_pct"] < 5.0


# --------------------------------------------------------------- current ----
def test_current_load_scaling_and_signs():
    a = L.current_load(VESSEL, 1.0, 60.0)
    b = L.current_load(VESSEL, 2.0, 60.0)
    assert b.magnitude / a.magnitude == pytest.approx(4.0, rel=1e-9)
    head = L.current_load(VESSEL, 1.0, 0.0)
    assert head.X < 0.0                              # head current pushes aft
    beam = L.current_load(VESSEL, 1.0, 90.0)
    assert beam.Y < 0.0 and beam.N > 0.0
    with pytest.raises(ValueError):
        L.current_load(VESSEL, -1.0, 90.0)


def test_current_load_matches_independent_reference():
    """Acceptance criterion (b): < 10 % at three current speeds."""
    result = V.current_criterion(VESSEL)
    assert result["passed"] is True
    assert result["max_rel_err_pct"] < V.THRESHOLD_PCT
    assert [row["current_speed_ms"] for row in result["rows"]] == [0.5, 1.0, 2.0]


def test_relative_field_conventions():
    # medium from starboard (+90 deg earth), ship at rest
    speed, psi = L.relative_field(0.0, 0.0, 0.0, 10.0, 90.0)
    assert speed == pytest.approx(10.0)
    assert psi == pytest.approx(90.0)
    # medium coming from astern (earth direction 180 deg = -x, ship heads +x)
    speed, psi = L.relative_field(0.0, 0.0, 0.0, 10.0, 180.0)
    assert speed == pytest.approx(10.0)
    assert abs(psi) == pytest.approx(180.0)
    # medium coming from ahead -> relative angle 0
    speed, psi = L.relative_field(0.0, 0.0, 0.0, 10.0, 0.0)
    assert speed == pytest.approx(10.0)
    assert psi == pytest.approx(0.0, abs=1e-9)
    # ship steaming into a beam medium: relative speed = sqrt(U^2 + c^2)
    speed, psi = L.relative_field(5.0, 0.0, 0.0, 10.0, 90.0)
    assert speed == pytest.approx(math.hypot(5.0, 10.0))
    assert psi == pytest.approx(math.degrees(math.atan2(10.0, 5.0)))


# -------------------------------------------------------------------- ice ---
def test_ice_resistance_scales_with_thickness_squared():
    ice1 = D.IceCondition(thickness=1.0)
    ice2 = D.IceCondition(thickness=2.0)
    r1 = L.riska_ice_resistance(VESSEL, ice1, 1.0).total
    r2 = L.riska_ice_resistance(VESSEL, ice2, 1.0).total
    assert r2 / r1 == pytest.approx(4.0, rel=0.02)


def test_ice_resistance_monotone_in_speed_and_friction_strength():
    ice = D.IceCondition(thickness=1.0)
    slow = L.riska_ice_resistance(VESSEL, ice, 0.5).total
    fast = L.riska_ice_resistance(VESSEL, ice, 2.0).total
    assert fast > slow
    strong = D.IceCondition(thickness=1.0, sigma_flexural=0.8e6)
    assert L.riska_ice_resistance(VESSEL, strong, 1.0).total > \
        L.riska_ice_resistance(VESSEL, ice, 1.0).total
    rough = D.IceCondition(thickness=1.0, mu=0.30)
    assert L.riska_ice_resistance(VESSEL, rough, 1.0).total > \
        L.riska_ice_resistance(VESSEL, ice, 1.0).total


def test_ice_load_opposes_motion_and_reacts_to_drift():
    ice = D.IceCondition(thickness=1.0)
    straight = L.ice_load(VESSEL, ice, 3.0, drift_angle_deg=0.0)
    assert straight.X < 0.0 and abs(straight.Y) < 1e-6
    drifting = L.ice_load(VESSEL, ice, 3.0, drift_angle_deg=10.0)
    assert drifting.X < 0.0 and drifting.Y < 0.0     # opposes the drift too
    assert drifting.magnitude == pytest.approx(straight.magnitude, rel=1e-9)
    with pytest.raises(ValueError):
        L.ice_load(VESSEL, ice, 0.0)


def test_ice_models_agree_within_a_reported_band():
    band = V.ice_uncertainty_band(VESSEL)
    # both models must give a physically sensible positive resistance
    for row in band["rows"]:
        assert row["riska_kN"] > 0.0
        assert row["lindqvist_like_kN"] > 0.0
    # the band is reported honestly rather than as a pass/fail claim
    assert band["passed"] is None
    assert 0.0 < band["mean_deviation_pct"] < 100.0


# -------------------------------------------------------------------- MMG ---
def test_propeller_equilibrium_balances_calm_water_resistance():
    n = M.required_rpm(VESSEL, M.KVLCC2_REFERENCE, SPEED)
    assert 0.6 < n < 2.5
    f = M.manoeuvring_forces((0.0, 0.0, 0.0, SPEED, 0.0, 0.0), 0.0, n,
                             VESSEL, M.KVLCC2_REFERENCE, M.Environment())
    q_lt = 0.5 * D.RHO_WATER * VESSEL.L * VESSEL.T * SPEED ** 2
    assert f.X == pytest.approx(0.0, abs=1e-3 * q_lt * 0.01)


def test_rudder_force_direction_and_symmetry():
    n = M.required_rpm(VESSEL, M.KVLCC2_REFERENCE, SPEED)
    starboard = M.manoeuvring_forces(
        (0.0, 0.0, 0.0, SPEED, 0.0, 0.0), math.radians(35.0), n, VESSEL,
        M.KVLCC2_REFERENCE, M.Environment())
    port = M.manoeuvring_forces(
        (0.0, 0.0, 0.0, SPEED, 0.0, 0.0), math.radians(-35.0), n, VESSEL,
        M.KVLCC2_REFERENCE, M.Environment())
    assert starboard.Y < 0.0        # rudder to starboard -> force to port
    assert starboard.N > 0.0        # -> bow turns to starboard
    assert starboard.Y == pytest.approx(-port.Y, rel=1e-9)
    assert starboard.N == pytest.approx(-port.N, rel=1e-9)


def test_environment_loads_are_superimposed_in_the_manoeuvring_model():
    n = M.required_rpm(VESSEL, M.KVLCC2_REFERENCE, SPEED)
    state = (0.0, 0.0, 0.0, SPEED, 0.0, 0.0)
    calm = M.manoeuvring_forces(state, 0.0, n, VESSEL,
                                M.KVLCC2_REFERENCE, M.Environment())
    windy = M.manoeuvring_forces(
        state, 0.0, n, VESSEL, M.KVLCC2_REFERENCE,
        M.Environment(wind_speed=20.0, wind_from_deg=90.0))
    rel_speed, rel_angle = L.relative_field(SPEED, 0.0, 0.0, 20.0, 90.0)
    windy_load = L.wind_load(VESSEL, rel_speed, rel_angle)
    assert windy.Y - calm.Y == pytest.approx(windy_load.Y, rel=1e-9)
    assert windy.X - calm.X == pytest.approx(windy_load.X, rel=1e-9)
    icy = M.manoeuvring_forces(
        state, 0.0, n, VESSEL, M.KVLCC2_REFERENCE,
        M.Environment(ice_thickness=1.0))
    assert icy.X < calm.X            # ice adds resistance
    assert "R_ice" in icy.parts


# --------------------------------------------------------------- turning ----
def test_turning_circle_is_physical_and_inside_imo_limits():
    res = M.simulate_turning_circle(VESSEL, speed=SPEED, dt=0.5, t_max=3000.0)
    m = res.metrics
    # engineering envelope of a VLCC at 35 deg rudder
    assert 2.0 < m["tactical_diameter_over_L"] < 4.5
    assert 2.0 < m["advance_over_L"] < 4.5
    assert 0.8 < m["transfer_over_L"] < 2.5
    # IMO Res. A.751(18) manoeuvring standard
    assert m["tactical_diameter_over_L"] < 5.0
    assert m["advance_over_L"] < 4.5
    # steady drift angle of a tanker in a full-rudder turn
    assert 5.0 < abs(m["steady_drift_angle_deg"]) < 25.0


def test_turning_circle_is_time_step_converged():
    coarse = M.simulate_turning_circle(VESSEL, speed=SPEED, dt=0.5,
                                       t_max=3000.0)
    fine = M.simulate_turning_circle(VESSEL, speed=SPEED, dt=0.25,
                                     t_max=3000.0)
    rel = abs(fine.metrics["tactical_diameter"]
              - coarse.metrics["tactical_diameter"]) \
        / coarse.metrics["tactical_diameter"]
    assert rel < 0.01


def test_turning_circle_matches_independent_equilibrium_solve():
    td = M.simulate_turning_circle(VESSEL, speed=SPEED, dt=0.5, t_max=3000.0)
    ss = M.steady_turning_solution(VESSEL, speed=SPEED)
    rel = abs(td.metrics["tactical_diameter"]
              - ss["steady_turning_diameter"]) / ss["steady_turning_diameter"]
    assert rel < 0.10
    assert abs(ss["residual_N"]) < 1.0        # equilibrium actually converged


def test_ice_increases_resistance_and_slows_the_turn():
    calm = M.steady_turning_solution(VESSEL, speed=SPEED, env=M.Environment())
    ice = M.steady_turning_solution(
        VESSEL, speed=SPEED, env=M.Environment(ice_thickness=1.0))
    assert ice["speed_ratio"] < calm["speed_ratio"]
    assert ice["steady_turning_diameter_over_L"] > \
        calm["steady_turning_diameter_over_L"]


def test_current_is_advected_in_the_water_frame():
    """A beam current must shift the trajectory but not destroy the circle."""
    td = M.simulate_turning_circle(
        VESSEL, speed=SPEED, dt=0.5, t_max=3000.0,
        env=M.Environment(current_speed=1.0, current_from_deg=90.0))
    assert 1.5 < td.metrics["tactical_diameter_over_L"] < 6.0
    ss = M.steady_turning_solution(
        VESSEL, speed=SPEED,
        env=M.Environment(current_speed=1.0, current_from_deg=90.0))
    assert abs(ss["drift_angle_deg"]) > 15.0     # current drags the hull along


# --------------------------------------------------------------- zigzag -----
def test_zigzag_10_10_produces_plausible_overshoot_angles():
    res = M.simulate_zigzag(VESSEL, speed=SPEED, rudder_deg=10.0,
                            heading_deg=10.0, t_max=2000.0)
    assert res.metrics["completed"] is True
    # overshoot angles are reported as the absolute heading reached at the
    # yaw-rate zero crossing, so both must exceed the 10 deg check angle
    assert 10.0 < res.metrics["first_overshoot_deg"] < 30.0
    assert 10.0 < res.metrics["second_overshoot_deg"] < 30.0


# ------------------------------------------------------------- validation ---
def test_aggregate_validation_reports_all_criteria():
    report = V.run_validation()
    assert report["mission_id"] == "W3-N08"
    assert set(report["criteria"]) == {"a_wind_load", "b_current_load",
                                       "c_coupled_turning_circle",
                                       "ice_resistance"}
    assert report["checks_passed"]["a_wind_load"] is True
    assert report["checks_passed"]["b_current_load"] is True
    assert report["criteria"]["c_coupled_turning_circle"]["verification_passed"]
    # the withheld claims must be stated explicitly, not silently dropped
    assert len(report["withheld"]) == 4