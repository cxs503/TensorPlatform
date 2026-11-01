"""Analytic anchors for the manoeuvring chain (mission m1657).

These tests pin the closed-form linear path (:mod:`physics.ship_env_maneuvering.
linear_analysis`) against the shipped 3-DOF solver.  They are *verification* tests:
the numbers they assert are the ones measured by
``python3 -m tools.verify.maneuvering_linear.audit`` and are re-derived here from
first principles, never hard-coded from a previous run.

Run with::

    python3 -m pytest tests/test_ship_env_maneuvering_linear.py -q

Documented findings (asserted, so a silent "fix" of the coefficient set would fail
this file and force a re-adjudication):

* ``K' < 0`` for the shipped ``kvlcc2_reference`` set: the *linear* limit predicts a
  turn opposite to the rudder, and the course-stability discriminant is negative
  (directionally unstable).  The physical branch only exists through the quadratic
  derivatives, so the linear indices must not be used as a design gain.
* First-order Nomoto reduction under-predicts the 10/10 zigzag overshoot by a wide
  margin (two widely separated modes, one of them unstable).
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from physics.ship_env_maneuvering import data as D
from physics.ship_env_maneuvering import linear_analysis as LA
from physics.ship_env_maneuvering import mmg as M

V, COEF, U = D.VESSEL_VLCC, M.KVLCC2_REFERENCE, 7.717


# ------------------------------------------------------------- derivatives ---
def test_finite_difference_derivatives_recover_the_coefficient_table():
    d = LA.linear_derivatives(V, COEF, U)
    # the quadratic derivatives contribute a small amount at a finite step, so the
    # FD slope is *close to* but not identical with the table value
    assert d.Y_v_nd == pytest.approx(COEF.Y_v, rel=0.10)
    assert d.N_v_nd == pytest.approx(COEF.N_v, rel=0.10)
    assert d.Y_r_nd == pytest.approx(COEF.Y_r, rel=0.15)
    assert d.N_r_nd == pytest.approx(COEF.N_r, rel=0.15)
    # a starboard rudder gives a port sway force and a starboard yaw moment
    assert d.Y_delta < 0.0 < d.N_delta


def test_linearisation_is_defined_for_calm_water_only():
    env = M.Environment(wind_speed=10.0)
    with pytest.raises(ValueError):
        LA.linear_derivatives(V, COEF, U, env=env)


# ----------------------------------------------------------------- indices ---
def test_nomoto_gain_is_invariant_of_yaw_inertia():
    base = LA.linearised_indices(V, COEF, U)
    for jz in (0.1, 0.2, 0.3):
        other = LA.linearised_indices(V, COEF, U, jz_ratio=jz)
        assert other.K == pytest.approx(base.K, rel=1e-12)
        assert other.K_nd == pytest.approx(base.K_nd, rel=1e-12)


def test_yaw_time_constant_is_only_weakly_sensitive_to_missing_added_inertia():
    base = LA.linearised_indices(V, COEF, U)
    with_jz = LA.linearised_indices(V, COEF, U, jz_ratio=0.1)
    assert abs(with_jz.T - base.T) / abs(base.T) < 0.02


def test_shipped_coefficient_set_is_directionally_unstable_and_gain_negative():
    idx = LA.linearised_indices(V, COEF, U)
    assert idx.stability_C_nd < 0.0          # Y_v N_r - N_v (Y_r - (m+m_x))
    assert idx.routh_hurwitz_stable is False
    assert max(idx.lam1, idx.lam2) > 0.0     # one divergent mode
    assert idx.K_nd < 0.0                    # documented: linear limit turns the wrong way
    assert 0.15 < idx.T1_nd < 0.30           # fast (sway) mode, O(L/U)
    assert idx.T2_nd < -10.0                 # slow divergent mode


def test_discriminant_is_a_near_cancellation_not_a_robust_number():
    idx = LA.linearised_indices(V, COEF, U)
    d = LA.linear_derivatives(V, COEF, U)
    m = V.mass
    m_x = COEF.m_x * 0.5 * D.RHO_WATER * V.L ** 2 * V.T
    first = d.Y_v * d.N_r
    second = (d.Y_r - (m + m_x) * U) * d.N_v
    # the two competing terms are each ~1e18 while their difference is ~1e17
    assert abs(first) / abs(idx.stability_C) > 5.0
    assert abs(second) / abs(idx.stability_C) > 5.0


# ------------------------------------------------- linear vs time domain -----
def test_exact_two_mode_solution_reproduces_the_nonlinear_solver():
    """In the small-rudder limit the RK4 solver must follow the closed form."""
    sim = M.simulate_turning_circle(V, COEF, U, rudder_deg=0.5, dt=0.25, t_max=400.0)
    lin = LA.linear_response(V, COEF, U, rudder_deg=0.5, times=sim.history["t"])
    scale = max(abs(x) for x in lin["r"])
    err = max(abs(a - b) / scale for a, b in zip(lin["r"], sim.history["r"]))
    # residual is the quadratic (Y_vv, N_vv, ...) contribution omitted by the
    # linear path; it is measured, not assumed
    assert err < 0.15
    assert abs(sim.history["r"][0]) < 1e-2 * scale   # starts from rest (one RK4 step)
    # both share the same sign and the same order of magnitude late in the run
    assert math.copysign(1.0, lin["r"][-1]) == math.copysign(1.0, sim.history["r"][-1])


def test_linear_response_initial_slope_matches_the_yaw_equation():
    d = LA.linear_derivatives(V, COEF, U)
    delta = math.radians(1.0)
    h = 1.0e-3
    lin = LA.linear_response(V, COEF, U, rudder_deg=1.0, times=[0.0, h])
    slope = lin["r"][1] / h
    assert slope == pytest.approx(d.N_delta * delta / V.I_z, rel=1e-3)


# --------------------------------------------------------------- zigzag -----
def test_zigzag_first_order_overshoot_closed_form():
    zz = LA.nomoto_zigzag_overshoot(K=0.02, T=40.0, rudder_deg=10.0, heading_deg=10.0)
    assert zz["first_overshoot_deg"] > 0.0
    # a slower ship (larger T) overshoots more
    slow = LA.nomoto_zigzag_overshoot(K=0.02, T=80.0, rudder_deg=10.0, heading_deg=10.0)
    assert slow["first_overshoot_deg"] > zz["first_overshoot_deg"]
    # the analytic time to reach the switching heading matches the closed form
    kd, psi_s = zz["K"] * math.radians(10.0), math.radians(10.0)
    t1 = zz["t_first_reach_s"]
    assert kd * (t1 - zz["T"] * (1.0 - math.exp(-t1 / zz["T"]))) == pytest.approx(psi_s, rel=1e-4)


def test_simulated_zigzag_overshoot_exceeds_first_order_prediction():
    """Two modes, one divergent: the 1st-order reduction is not usable for overshoot."""
    sim = M.simulate_zigzag(V, COEF, U, rudder_deg=10.0, heading_deg=10.0,
                            dt=0.25, t_max=1500.0)
    m = sim.metrics
    assert m["completed"] is True
    assert 5.0 < m["first_overshoot_deg"] < 25.0
    t1 = m["time_to_first_heading"]
    r_rev = next(r for t, r in zip(sim.history["t"], sim.history["r"]) if t >= t1)
    delta = math.radians(10.0)
    t63 = next(t for t, r in zip(sim.history["t"], sim.history["r"])
               if r >= 0.632 * r_rev)
    k_op = r_rev / (delta * (1.0 - math.exp(-t1 / t63)))
    pred = LA.nomoto_zigzag_overshoot(k_op, t63, 10.0, 10.0)["first_overshoot_deg"]
    assert pred < m["first_overshoot_deg"]
    assert (m["first_overshoot_deg"] - pred) / m["first_overshoot_deg"] > 0.5


def test_turning_circle_metrics_are_in_the_engineering_band():
    sim = M.simulate_turning_circle(V, COEF, U, rudder_deg=35.0, dt=0.5, t_max=1500.0)
    m = sim.metrics
    # VLCC at 35 deg rudder: steady diameter of a few ship lengths, speed drops
    assert 2.0 < m["steady_turning_diameter_over_L"] < 4.5
    assert 0.25 < m["steady_speed_ratio"] < 0.95
    assert 0.1 < abs(m["steady_drift_angle_deg"]) < 20.0