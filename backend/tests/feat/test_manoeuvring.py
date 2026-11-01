"""Verification tests for the MMG 3-DOF manoeuvring baseline (manoeuvring domain).

Every assertion is a *closed-form / self-consistency* gate: geometry gate,
mass plausibility, straight-course equilibrium, Newton steady state vs time
marching, Nomoto linearisation vs nonlinear simulation, circle-fit kinematics,
port/starboard symmetry, small-rudder radius scaling, speed-loss and drift
signs, time-step sensitivity, IMO criteria evaluator and the HTTP endpoint.
"""
from __future__ import annotations

import math
import sys

import pytest

from methods.manoeuvring import (
    HullParameters, IMO_LIMITS, MMGModel, ModelParameters,
    circle_fit, evaluate_imo, nomoto_indices, nomoto_response,
    run_constant_rudder, steady_state, turning_circle_indicators, zigzag,
)

# KVLCC2-like full-form tanker (public principal particulars)
KVLCC2 = dict(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
              design_speed=7.0, displacement_t=320000.0)


def _hull(**kw):
    d = dict(KVLCC2)
    d.update(kw)
    return HullParameters(**d)


def _model(**kw):
    params = kw.pop("params", None)
    return MMGModel(_hull(**kw), params)


# ---------------------------------------------------------------------------
# geometry gate / parameter plausibility
# ---------------------------------------------------------------------------

def test_geometry_gate_rejects_inconsistent_displacement():
    with pytest.raises(ValueError, match="geometry inconsistent"):
        HullParameters(length=320.0, beam=58.0, draft=20.8, cb=0.8098,
                       design_speed=7.0, displacement_t=400000.0)


def test_geometry_gate_rejects_out_of_range_cb_and_ratios():
    with pytest.raises(ValueError, match="Cb"):
        HullParameters(length=320.0, beam=58.0, draft=20.8, cb=0.95, design_speed=7.0)
    with pytest.raises(ValueError, match="L/B"):
        HullParameters(length=100.0, beam=58.0, draft=20.8, cb=0.8, design_speed=5.0)
    with pytest.raises(ValueError, match="B/T"):
        HullParameters(length=320.0, beam=58.0, draft=4.0, cb=0.8, design_speed=5.0)


def test_mass_and_added_mass_are_physical():
    m = _model()
    assert abs(m.m - 3.20e8) / 3.20e8 < 0.01          # rho*Cb*L*B*T
    assert 0.02 < m.m_x / m.m < 0.15                  # surge added mass / m
    assert 0.15 < m.m_y / m.m < 0.60                  # sway added mass / m
    assert 0.0 < m.J_zz / m.I_zz < 1.0


# ---------------------------------------------------------------------------
# equilibrium / symmetry / limits
# ---------------------------------------------------------------------------

def test_straight_course_is_an_exact_equilibrium():
    m = _model()
    s = m.initial_state()
    for _ in range(600):                              # 300 s at dt=0.5
        s = m.step(s, 0.0, 0.5)
    assert abs(s[0] - m.u0) / m.u0 < 1e-9
    assert abs(s[1]) < 1e-9 and abs(s[2]) < 1e-12


def test_zero_rudder_angle_gives_zero_rudder_force():
    """Aligned inflow + zero deflection must produce no rudder force."""
    m = _model()
    for u in (7.0, 5.0, 4.0):
        fx, fy, fn = m.rudder_forces(u, 0.0, 0.0, 0.0)
        assert abs(fx) < 1e-9 and abs(fy) < 1e-9 and abs(fn) < 1e-9


def test_rudder_force_sign_and_scaling():
    """delta > 0 gives a starboard yaw moment and a port-directed rudder force."""
    m = _model()
    u = m.u0
    _, fy, fn = m.rudder_forces(u, 0.0, 0.0, math.radians(20.0))
    assert fn > 0.0 and fy < 0.0
    # force scales with inflow speed squared (Fujii normal-force model)
    _, _, fn_half = m.rudder_forces(0.5 * u, 0.0, 0.0, math.radians(20.0))
    assert fn_half / fn == pytest.approx(0.25, rel=0.02)


def test_port_starboard_symmetry():
    m = _model()
    left = run_constant_rudder(m, math.radians(-35.0), 300.0, 0.5)
    right = run_constant_rudder(m, math.radians(35.0), 300.0, 0.5)
    for pl, pr in zip(left.psi[-10:], right.psi[-10:]):
        assert pl == pytest.approx(-pr, rel=1e-9, abs=1e-12)
    for xl, xr in zip(left.x[-10:], right.x[-10:]):
        assert xl == pytest.approx(xr, rel=1e-9, abs=1e-6)


def test_steady_state_newton_matches_time_marching():
    m = _model()
    u_n, v_n, r_n = steady_state(m, math.radians(35.0))
    trace = run_constant_rudder(m, math.radians(35.0), 4000.0, 0.5)
    assert trace.r[-1] == pytest.approx(r_n, rel=5e-3)
    assert trace.u[-1] == pytest.approx(u_n, rel=5e-3)
    assert trace.v[-1] == pytest.approx(v_n, rel=5e-3, abs=5e-4)


def test_small_rudder_steady_radius_scales_inverse_with_rudder():
    m = _model()
    radii = []
    for deg in (1.0, 2.0, 4.0):
        u, _, r = steady_state(m, math.radians(deg))
        radii.append(u / r)
    # linear regime: R proportional to 1/delta
    scaled = [radii[0] * 1.0, radii[1] * 2.0, radii[2] * 4.0]
    ref = sum(scaled) / 3.0
    for s in scaled:
        assert abs(s - ref) / ref < 0.03


# ---------------------------------------------------------------------------
# Nomoto linearisation (analytic reference)
# ---------------------------------------------------------------------------

def test_nomoto_steady_gain_matches_nonlinear_simulation():
    m = _model()
    delta = math.radians(0.5)
    K = nomoto_indices(m)["K"]
    u, v, r = steady_state(m, delta)
    assert r == pytest.approx(K * delta, rel=0.03)


def test_nomoto_transient_matches_simulation():
    m = _model()
    delta = math.radians(0.5)
    idx = nomoto_indices(m)
    K, T = idx["K"], idx["T"]
    trace = run_constant_rudder(m, delta, 3.0 * T, 0.5)
    worst = 0.0
    for t, r in zip(trace.t, trace.r):
        if t < 0.3 * T:
            continue
        r_an, _ = nomoto_response(K, T, delta, t)
        worst = max(worst, abs(r - r_an) / r_an)
    assert worst < 0.20, f"transient deviation {100 * worst:.2f}%"


def test_linear_model_is_directionally_stable_or_flag_reported():
    idx = nomoto_indices(_model())
    assert math.isfinite(idx["K"]) and math.isfinite(idx["T"])
    assert idx["T"] > 0
    assert isinstance(idx["eig_oscillatory"], float)


def test_munk_moment_is_destabilising():
    """Turning Munk moment on must slow the decay of a pure-sway disturbance."""
    base = _model(params=ModelParameters(munk_factor=0.0))
    with_munk = _model(params=ModelParameters(munk_factor=1.0))
    def residual(model):
        s = (model.u0, 0.05, 0.0, 0.0, 0.0, 0.0)   # 5 cm/s sway kick
        for _ in range(200):                      # 100 s
            s = model.step(s, 0.0, 0.5)
        return abs(s[1])
    assert residual(with_munk) > residual(base)


# ---------------------------------------------------------------------------
# manoeuvre indicators
# ---------------------------------------------------------------------------

def test_circle_fit_recovers_known_circle():
    xc, yc, rad = 1234.5, -678.9, 250.0
    pts = [(xc + rad * math.cos(a), yc + rad * math.sin(a))
           for a in [2 * math.pi * i / 360 for i in range(360)]]
    xf, yf, rf = circle_fit(pts)
    assert xf == pytest.approx(xc, abs=1e-6)
    assert yf == pytest.approx(yc, abs=1e-6)
    assert rf == pytest.approx(rad, rel=1e-9)


def test_turning_circle_indicators_are_self_consistent():
    m = _model()
    trace = run_constant_rudder(m, math.radians(35.0), 1800.0, 0.5)
    ind = turning_circle_indicators(m, trace)
    assert ind["advance_m"] > 0 and ind["transfer_m"] > 0
    # advance >= transfer for any conventional ship (geometry of the path)
    assert ind["advance_m"] >= ind["transfer_m"] > 0
    # kinematic diameter (u/r) vs geometric fit of the track must agree
    rel = abs(ind["steady_turning_diameter_fit_m"] - ind["steady_turning_diameter_m"]) \
        / ind["steady_turning_diameter_m"]
    assert rel < 0.03, f"kinematic vs fitted diameter differ by {100 * rel:.2f}%"


def test_speed_loss_and_drift_signs_in_starboard_turn():
    m = _model()
    trace = run_constant_rudder(m, math.radians(35.0), 1800.0, 0.5)
    ind = turning_circle_indicators(m, trace)
    assert ind["speed_loss_pct"] > 0.0                # thrust is constant
    assert ind["r_steady"] > 0.0                      # starboard rudder -> starboard yaw
    assert ind["drift_angle_deg"] > 0.0               # bow into the circle


def test_time_step_sensitivity_below_one_percent():
    m = _model()
    a = turning_circle_indicators(m, run_constant_rudder(m, math.radians(35.0), 900.0, 0.5))
    b = turning_circle_indicators(m, run_constant_rudder(m, math.radians(35.0), 900.0, 0.25))
    for key in ("advance_m", "transfer_m", "steady_turning_diameter_m"):
        assert abs(a[key] - b[key]) / b[key] < 0.01, key


def test_zigzag_overshoots_are_bounded_and_finite():
    m = _model()
    tr20, ov20 = zigzag(m, rudder_deg=20.0, heading_deg=10.0, dt=0.5, max_time=1800.0)
    tr10, ov10 = zigzag(m, rudder_deg=10.0, heading_deg=10.0, dt=0.5, max_time=1800.0)
    assert len(ov20) >= 2 and len(ov10) >= 2
    assert all(math.isfinite(o) and 0.0 < o < 90.0 for o in ov20 + ov10)
    assert tr20.t[-1] <= tr10.t[-1]          # larger rudder completes the reversals sooner


def test_response_time_shrinks_with_rudder_angle():
    m = _model()
    def t_to_heading(deg, psi_target):
        delta = math.radians(deg)
        s = m.initial_state()
        t = 0.0
        while s[5] < psi_target and t < 1200.0:
            s = m.step(s, delta, 0.5)
            t += 0.5
        return t
    assert t_to_heading(20.0, math.radians(5.0)) < t_to_heading(10.0, math.radians(5.0))


# ---------------------------------------------------------------------------
# IMO Res. MSC.137(76) evaluator
# ---------------------------------------------------------------------------

def test_imo_evaluator_pass_and_fail_paths():
    ok = evaluate_imo(320.0, turning={"advance_over_L": 3.0, "tactical_diameter_over_L": 4.0},
                      zigzag_10=[8.0, 20.0], zigzag_20=[20.0])
    assert ok["applicable"] is True and ok["verdict"] == "PASS"
    bad = evaluate_imo(320.0, turning={"advance_over_L": 6.0, "tactical_diameter_over_L": 7.0},
                       zigzag_10=[15.0, 30.0])
    assert bad["verdict"] == "FAIL" and bad["n_fail"] == 4
    assert IMO_LIMITS["tactical_diameter_over_L"] == 5.0


def test_imo_evaluator_flags_short_ship_as_advisory():
    short = evaluate_imo(60.0, turning={"advance_over_L": 3.0, "tactical_diameter_over_L": 4.0})
    assert short["applicable"] is False
    assert "advisory" in short["applicability_note"]


def test_imo_evaluator_reports_not_evaluated_when_values_missing():
    res = evaluate_imo(200.0, turning={"advance_over_L": 3.2})
    statuses = {c["criterion"]: c["status"] for c in res["criteria"]}
    assert "NOT_EVALUATED" in statuses.values()


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    import importlib.util
    import pathlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # load by path so the check does not pull the whole platform app / DB stack
    mod_path = pathlib.Path(__file__).resolve().parents[2] / "api" / "domain" / "manoeuvring.py"
    spec = importlib.util.spec_from_file_location("manoeuvring_api_under_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # register so pydantic can resolve annotations
    spec.loader.exec_module(mod)
    manoeuvring_router = mod.router

    app = FastAPI()
    app.include_router(manoeuvring_router, prefix="/api/v1")
    return TestClient(app)


def test_endpoint_turning_circle(client):
    r = client.post("/api/v1/calculations/manoeuvring", json={
        "length": 320.0, "beam": 58.0, "draft": 20.8, "cb": 0.8098,
        "design_speed": 7.0, "displacement_t": 320000.0,
        "manoeuvre": "turning_circle", "rudder_deg": 35.0, "dt": 0.5, "duration": 900.0,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accuracy_grade"] == "WITHHELD"
    assert body["indicators"]["tactical_diameter_over_L"] > 0
    assert body["imo"]["resolution"] == "IMO Res. MSC.137(76)"
    assert body["scenario"]["displacement_from_geometry_t"] == pytest.approx(320000.0, rel=0.01)


def test_endpoint_rejects_inconsistent_geometry(client):
    r = client.post("/api/v1/calculations/manoeuvring", json={
        "length": 320.0, "beam": 58.0, "draft": 20.8, "cb": 0.8098,
        "design_speed": 7.0, "displacement_t": 150000.0,
    })
    assert r.status_code == 400
    assert "geometry inconsistent" in r.json()["detail"]


def test_endpoint_limits_are_exposed(client):
    r = client.get("/api/v1/calculations/manoeuvring/limits")
    assert r.status_code == 200
    body = r.json()
    assert body["imo_limits"]["tactical_diameter_over_L"] == 5.0
    assert body["accuracy_grade"] == "WITHHELD"