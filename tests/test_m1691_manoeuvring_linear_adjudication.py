"""Exact-theory regression tests for the MMG manoeuvring integrators (mission m1691).

These tests do not re-assert the module's own formulas: every reference is an
exact mathematical object -- the matrix exponential of the Jacobian
linearisation, the known order of RK4, the exact odd symmetry of the model, or
the documented kinematics -- so a regression in the time integration, in the
steady-state solver or in the metric extraction is caught even though the
production model stays self-consistent.

Run with::

    python -m pytest tests/test_m1691_manoeuvring_linear_adjudication.py -q
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "verification"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import manoeuvring_linear_adjudication as ADJ          # noqa: E402
from physics.ship_env_maneuvering import data as D     # noqa: E402
from physics.ship_env_maneuvering import mmg as M      # noqa: E402

VESSEL = D.VESSEL_VLCC
U0 = ADJ.U0


@pytest.fixture(scope="module")
def model():
    m, _info = ADJ.build_linear_model()
    return m


# ----------------------------------------------------------- linearisation ---
def test_equilibrium_residual_is_negligible():
    """The simulators start on (numerically) a straight-course equilibrium."""
    du = float(ADJ.f3(U0, 0.0, 0.0, 0.0)[0])
    assert abs(du) < 1.0e-8, du


def test_one_positive_eigenvalue_full_form_hull(model):
    """A full-form tanker is directionally unstable: exactly one positive root."""
    ev = model.eigenvalues()
    pos = [e for e in ev if abs(e.imag) < 1e-9 and e.real > 0.0]
    assert len(pos) == 1
    assert 1.0e-4 < pos[0].real < 1.0e-2


# ------------------------------------------------------- linear limit (R1) ---
def test_simulator_converges_to_exact_linear_solution(model, delta_deg=0.05):
    delta = math.radians(delta_deg)
    res = ADJ.run_turning(delta_deg, 0.5, ADJ.T_LINEAR_HORIZON)
    lin = model.history(delta, [ADJ.T_LINEAR_HORIZON])
    r_sim = ADJ._interp(res.history, "r", ADJ.T_LINEAR_HORIZON)
    rel = abs(r_sim - float(lin[-1][2])) / abs(float(lin[-1][2]))
    assert rel < 1.0e-3, rel


def test_linear_limit_error_grows_with_rudder_angle(model):
    errs = []
    for d_deg in (0.05, 1.6):
        delta = math.radians(d_deg)
        res = ADJ.run_turning(d_deg, 0.5, ADJ.T_LINEAR_HORIZON)
        lin = model.history(delta, [ADJ.T_LINEAR_HORIZON])
        errs.append(abs(ADJ._interp(res.history, "r", ADJ.T_LINEAR_HORIZON)
                        - float(lin[-1][2])) / abs(float(lin[-1][2])))
    assert errs[0] < errs[1]
    assert errs[1] / errs[0] > 10.0      # roughly the delta ratio (32x)


# -------------------------------------------------------- RK4 order (R2) -----
def test_rk4_observed_order_is_four():
    states = []
    for dt in (1.0, 0.5, 0.25):
        states.append(ADJ.run_turning(35.0, dt, 100.0).history["r"][-1])
    p = math.log2(abs(states[1] - states[0]) / abs(states[2] - states[1]))
    assert 3.7 < p < 4.5, p


# --------------------------------------------------------- symmetry (R3) -----
def test_port_starboard_antisymmetry_is_exact():
    pos = ADJ.run_turning(35.0, 0.5, 1800.0)
    neg = ADJ.run_turning(-35.0, 0.5, 1800.0)
    n = min(len(pos.history["t"]), len(neg.history["t"]))
    for key, add in (("r", True), ("v", True), ("u", False)):
        a = np.asarray(pos.history[key][:n])
        b = np.asarray(neg.history[key][:n])
        diff = np.max(np.abs(a + b if add else a - b))
        scale = max(np.max(np.abs(a)), 1e-30)
        assert diff / scale < 1e-12, (key, diff / scale)


def test_port_turn_metrics_are_finite_and_mirrored():
    """Regression: negative-rudder trials used to return NaN advance/diameter."""
    pos = ADJ.run_turning(35.0, 0.5, 1800.0).metrics
    neg = ADJ.run_turning(-35.0, 0.5, 1800.0).metrics
    for key in ("advance", "transfer", "tactical_diameter",
                "steady_turning_diameter_over_L"):
        assert math.isfinite(neg[key]), key
        assert abs(neg[key] - pos[key]) / abs(pos[key]) < 1e-9, key
    assert abs(neg["steady_drift_angle_deg"] + pos["steady_drift_angle_deg"]) < 1e-9


# ----------------------------------------- steady state / surge balance (R4) -
def test_steady_solver_agrees_with_time_march():
    """Regression: the Newton solver must return an equilibrium of the ODE.

    Before the m1691 fix the surge balance omitted ``(m+m_y) v r``, which put the
    steady speed ~1.94x too high (turning diameter unchanged).
    """
    ss = M.steady_turning_solution(VESSEL, rudder_deg=35.0)
    march = ADJ._march(35.0, 0.5, 5000.0)
    u, v, r = march["u"][-1], march["v"][-1], march["r"][-1]
    assert abs(ss["u"] - u) / u < 1e-4
    assert abs(ss["v"] - v) / abs(v) < 1e-4
    assert abs(ss["r"] - r) / r < 1e-4


def test_steady_solver_surge_balance_includes_centripetal_term():
    ss = M.steady_turning_solution(VESSEL, rudder_deg=35.0)
    f = M.manoeuvring_forces((0.0, 0.0, 0.0, ss["u"], ss["v"], ss["r"]),
                             math.radians(35.0), ADJ.N_RPS, VESSEL,
                             M.KVLCC2_REFERENCE, M.Environment())
    m = VESSEL.mass
    m_y = M.KVLCC2_REFERENCE.m_y * 0.5 * D.RHO_WATER * VESSEL.L ** 2 * VESSEL.T
    cent = (m + m_y) * ss["v"] * ss["r"]
    thrust_scale = abs((1.0 - M.KVLCC2_REFERENCE.t_P) * f.parts["X_P"])
    # the integrated ODE requires X + (m+m_y) v r = 0 ...
    assert abs(f.X + cent) / thrust_scale < 1e-3
    # ... and the term is not negligible, so dropping it cannot be benign
    assert abs(cent) / thrust_scale > 0.3


# ------------------------------------------------ kinematics / metrics (R5) --
def test_trajectory_reintegration_matches_recorded_path():
    res = ADJ.run_turning(35.0, 0.5, 1800.0)
    h = res.history
    t = np.asarray(h["t"])
    psi = np.radians(np.asarray(h["psi_deg"]))
    u = np.asarray(h["u"])
    v = np.asarray(h["v"])
    dx = u * np.cos(psi) - v * np.sin(psi)
    dy = u * np.sin(psi) + v * np.cos(psi)
    x_re = np.concatenate([[0.0], np.cumsum(0.5 * (dx[1:] + dx[:-1]) * np.diff(t))])
    span = float(np.max(np.hypot(h["x_water"], h["y_water"])))
    assert np.max(np.abs(x_re - np.asarray(h["x_water"]))) / span < 1e-2


def test_trial_metrics_reextracted_independently():
    res = ADJ.run_turning(35.0, 0.5, 1800.0)
    h = res.history
    psi = np.abs(np.asarray(h["psi_deg"]))
    idx = int(np.argmax(psi >= 180.0))
    y = abs(np.asarray(h["y_water"])[idx])
    rel = abs(y - res.metrics["tactical_diameter"]) / res.metrics["tactical_diameter"]
    assert rel < 0.02, rel


# ------------------------------------------------------------ zig-zag (R6) ---
def test_zigzag_overshoot_converges_to_linear_reference(model):
    sigma = 0.25
    lin = model.zigzag(math.radians(sigma), 2.0 * sigma)
    sim = ADJ.run_zigzag(sigma, 2.0 * sigma, 0.25, 4000.0).metrics
    assert sim["completed"] is True
    rel = abs(sim["first_overshoot_deg"] - lin["first_overshoot_deg"]) \
        / lin["first_overshoot_deg"]
    assert rel < 0.02, rel