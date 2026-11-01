"""Verification tests for the linearised manoeuvring references (mission m1656).

Closes the verification debt of ``backend/physics/ship_env_maneuvering``:

* the time integrator had no analytical reference,
* the zig-zag overshoot angle was only checked for "plausible" magnitude.

Run with::

    python -m pytest tests/test_linear_manoeuvring.py -q

Every test compares against an *independent* reference (closed-form matrix
exponential, closed-form eigen solution, closed-form Nomoto zig-zag, or the
observed order of accuracy), not against a second copy of the same code.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from physics.ship_env_maneuvering import data as D
from physics.ship_env_maneuvering import linear_manoeuvring as LM
from physics.ship_env_maneuvering import mmg as M

VESSEL = D.VESSEL_VLCC
SPEED = 7.717          # m/s (15 kn service speed of the reference hull)


# ------------------------------------------------------------ linear algebra --
def test_expm_matches_closed_form_two_state_solution():
    """expm(A t) against the eigen-decomposition form, A = P diag(lam) P^{-1}."""
    lam1, lam2 = -0.25, -1.0
    p = [[1.0, 0.25], [1.0, -1.0]]
    p_inv = LM.inverse(p)
    diag_lam = [[lam1, 0.0], [0.0, lam2]]
    a = LM._mat_mul(LM._mat_mul(p, diag_lam), p_inv)
    for t in (0.5, 3.0, 50.0):
        e = LM.expm(a, t)
        diag = [[math.exp(lam1 * t), 0.0], [0.0, math.exp(lam2 * t)]]
        ref = LM._mat_mul(LM._mat_mul(p, diag), p_inv)
        err = max(abs(e[i][j] - ref[i][j]) for i in range(2) for j in range(2))
        assert err < 1e-10, (t, err)


def test_expm_semigroup_property():
    """expm(A, t1 + t2) == expm(A, t1) expm(A, t2) (squaring consistency)."""
    a = [[-0.121, 0.0031], [-0.02, 0.00114]]
    left = LM.expm(a, 7.0)
    right = LM._mat_mul(LM.expm(a, 3.0), LM.expm(a, 4.0))
    assert max(abs(left[i][j] - right[i][j])
               for i in range(2) for j in range(2)) < 1e-12


def test_solve_and_inverse_are_consistent():
    a = [[3.0, -1.0, 2.0], [1.0, 5.0, -3.0], [2.0, 1.0, 4.0]]
    b = [7.0, -2.0, 9.0]
    x = LM.solve(a, b)
    assert max(abs(sum(aij * xj for aij, xj in zip(row, x)) - bi)
               for row, bi in zip(a, b)) < 1e-12
    inv = LM.inverse(a)
    prod = LM._mat_mul(a, inv)
    for i in range(3):
        for j in range(3):
            assert abs(prod[i][j] - (1.0 if i == j else 0.0)) < 1e-12


# ---------------------------------------------------------------- linearisation-
@pytest.fixture(scope="module")
def model() -> LM.LinearModel:
    return LM.linear_system(VESSEL, M.KVLCC2_REFERENCE, SPEED)


@pytest.fixture(scope="module")
def nomoto(model: LM.LinearModel) -> LM.NomotoIndices:
    return LM.nomoto_coefficients(model, VESSEL)


def test_linear_system_reproduces_the_initial_slope_of_the_parent_model(model):
    """dr/dt and dv/dt of one RK4 step must equal ``b2 * delta`` to O(dt)."""
    slope = LM.initial_slope_check(rudder_deg=1.0, dt=0.005, vessel=VESSEL,
                                   coef=M.KVLCC2_REFERENCE, speed=SPEED)
    assert slope["drdt_rel_err_pct"] < 0.1
    assert slope["dvdt_rel_err_pct"] < 0.1
    # sign convention: positive rudder -> positive yaw acceleration
    assert slope["drdt_model"] > 0.0
    assert model.b2[1] > 0.0


def test_linear_steady_state_is_an_equilibrium_of_the_linear_system(model):
    v_ss, r_ss = model.steady_state(2.0)
    a = model.a2_list()
    b = list(model.b2)
    delta = math.radians(2.0)
    residual = [a[i][0] * v_ss + a[i][1] * r_ss + b[i] * delta for i in range(2)]
    assert max(abs(x) for x in residual) < 1e-12


def test_nomoto_gain_equals_the_linear_steady_yaw_rate(model, nomoto):
    """``K delta`` must be the steady yaw rate of the linear system."""
    for rudder in (0.5, 1.0, 5.0):
        _, r_ss = model.steady_state(rudder)
        assert abs(nomoto.k * math.radians(rudder) - r_ss) < 1e-12


def test_nomoto_transfer_function_factorisation_is_exact(model, nomoto):
    """K, T1, T2, T3 must reproduce r/delta(s) obtained directly from the state space."""
    (a11, a12), (a21, a22) = model.a2
    b1, b2 = model.b2
    num_const = a21 * b1 - a11 * b2
    for s in (0.5, 2.0, 1j * 0.02, 1j * 0.3):
        direct = (b2 * s + num_const) / ((s - a11) * (s - a22) - a12 * a21)
        factored = (nomoto.k * (1.0 + nomoto.t3 * s)
                    / ((1.0 + nomoto.t1 * s) * (1.0 + nomoto.t2 * s)))
        assert abs(direct - factored) / abs(direct) < 1e-10


def test_second_order_time_constants_match_eigenvalues(model, nomoto):
    """``1/T_i`` must be the negated eigenvalues of A2 (both stable branches)."""
    l1, l2 = model.eigenvalues2()
    assert not nomoto.oscillatory
    got = sorted([nomoto.t1, nomoto.t2])
    ref = sorted([-1.0 / l1.real, -1.0 / l2.real])
    for g, r in zip(got, ref):
        assert abs(g - r) <= 1e-9 * max(1.0, abs(r))


def test_stability_discriminant_equivalent_to_eigenvalue_criterion(model, nomoto):
    """``C_nomoto`` and ``det A2`` share sign; stability needs det > 0 and tr < 0."""
    assert (nomoto.c_nomoto > 0.0) == (nomoto.det > 0.0)
    assert model.is_course_stable() == (nomoto.det > 0.0 and nomoto.trace < 0.0)
    assert nomoto.stable == model.is_course_stable()


def test_reference_hull_is_directionally_unstable(model, nomoto):
    """KVLCC2-form VLCC hull with these derivatives is a spiral-unstable ship.

    Documented finding (not a requirement of the platform): the full-form tanker is
    directionally unstable, i.e. ``det A2 < 0`` and one positive eigenvalue.
    """
    assert nomoto.det < 0.0
    assert not nomoto.stable
    assert nomoto.eigenvalue_real > 0.0
    # the slow (unstable) mode has a long time constant, the fast mode ~8 s
    assert 300.0 < 1.0 / nomoto.eigenvalue_real < 5000.0


# ------------------------------------------------------------ exact responses --
def test_linear_step_response_matches_independent_rk4_integration(model):
    """Matrix-exponential solution against RK4 integration of the same system."""
    dt = 0.01
    t_end = 200.0
    resp = LM.linear_step_response(model, 1.0, t_end, dt)
    a = model.a2_list()
    b = list(model.b2)
    delta = math.radians(1.0)
    x = [0.0, 0.0]
    psi = 0.0

    def rhs(xx):
        return [a[i][0] * xx[0] + a[i][1] * xx[1] + b[i] * delta for i in range(2)]

    worst_r = worst_psi = 0.0
    for i in range(1, len(resp["t"])):
        k1 = rhs(x)
        k2 = rhs([x[j] + 0.5 * dt * k1[j] for j in range(2)])
        k3 = rhs([x[j] + 0.5 * dt * k2[j] for j in range(2)])
        k4 = rhs([x[j] + dt * k3[j] for j in range(2)])
        x = [x[j] + dt / 6.0 * (k1[j] + 2 * k2[j] + 2 * k3[j] + k4[j])
             for j in range(2)]
        psi += dt * x[1]
        worst_r = max(worst_r, abs(x[1] - resp["r"][i]))
        worst_psi = max(worst_psi, abs(psi - math.radians(resp["psi_deg"][i])))
    assert worst_r < 1e-6, worst_r
    assert worst_psi < 1e-5, worst_psi


# ------------------------------------------------ closed-form Nomoto zig-zag --
@pytest.mark.parametrize("k,t_const,rudder,heading", [
    (0.10, 20.0, 10.0, 10.0),
    (0.05, 40.0, 20.0, 20.0),
    (0.30, 8.0, 5.0, 5.0),
])
def test_nomoto_zigzag_closed_form_matches_ode_integration(k, t_const, rudder, heading):
    """The analytical δ/ψ zig-zag must reproduce direct integration of ``T r' + r = K δ``."""
    analytic = LM.nomoto_zigzag_analytic(k, t_const, rudder, heading)
    numeric = LM.nomoto_zigzag_numeric(k, t_const, rudder, heading, dt=0.002)
    assert LM.relative_error(analytic["first_overshoot_deg"],
                             numeric["first_overshoot_deg"]) < 0.05
    assert LM.relative_error(analytic["second_overshoot_deg"],
                             numeric["second_overshoot_deg"]) < 0.05
    assert LM.relative_error(analytic["time_to_first_heading"],
                             numeric["time_to_first_heading"]) < 0.05


def test_nomoto_zigzag_overshoot_grows_with_rudder_angle():
    """Physical trend of the closed form: larger rudder -> larger overshoot."""
    prev = 0.0
    for rudder in (5.0, 10.0, 20.0):
        row = LM.nomoto_zigzag_analytic(0.1, 20.0, rudder, rudder)
        assert row["first_overshoot_deg"] > prev
        assert row["second_overshoot_deg"] > 0.0
        prev = row["first_overshoot_deg"]


def test_nomoto_zigzag_rejects_non_positive_inputs():
    with pytest.raises(ValueError):
        LM.nomoto_zigzag_analytic(0.1, 20.0, -10.0, 10.0)


# -------------------------------------------- contraction onto the linear limit-
def test_transient_contracts_onto_the_linear_solution(model):
    """Nonlinear vs exact linear transient: error must shrink with the amplitude."""
    study = LM.transient_contraction([4.0, 2.0, 1.0], [120.0],
                                     vessel=VESSEL, coef=M.KVLCC2_REFERENCE,
                                     speed=SPEED, dt=0.2)
    errs = [row["samples"][0]["yaw_rate_rel_err_pct"] for row in study["rows"]]
    assert errs[0] > errs[1] > errs[2]
    assert errs[-1] < 1.5, errs                      # 1 deg rudder at t = 120 s
    order = study["orders"]["t=120s"]["fitted_order_yaw_rate"]
    assert 0.6 < order < 1.6, order                   # quadratic terms -> O(delta)


def test_zigzag_overshoot_contracts_onto_the_linear_reference():
    """The previously unverified overshoot angle now has a small-amplitude reference."""
    study = LM.zigzag_contraction([2.0, 1.0], vessel=VESSEL,
                                  coef=M.KVLCC2_REFERENCE, speed=SPEED, dt=0.05)
    errors = [row["first_overshoot_rel_err_pct"] for row in study["rows"]]
    assert errors[1] < errors[0]
    assert errors[1] < 2.5, errors                    # 1 deg / 1 deg zig-zag
    assert study["rows"][1]["first_overshoot_ref_deg"] > 1.0


def test_rk4_integrator_shows_its_designed_order():
    """Observed Richardson order of the parent RK4 integrator (expected 4)."""
    study = LM.rk4_order_study((2.0, 1.0, 0.5, 0.25), t_end=600.0,
                               vessel=VESSEL, coef=M.KVLCC2_REFERENCE, speed=SPEED)
    assert study["min_observed_order"] > 3.5, study["observed_orders"]
    assert len(study["observed_orders"]) == 2


def test_relative_error_helper_handles_degenerate_references():
    assert LM.relative_error(1.5, 1.0) == pytest.approx(50.0)
    assert math.isnan(LM.relative_error(1.0, 0.0))
    assert math.isinf(LM.relative_error(float("inf"), 1.0))