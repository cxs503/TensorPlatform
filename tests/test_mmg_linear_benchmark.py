"""A-class gate for the MMG reference kernel: exact linear sway/yaw anchors.

Context
-------
``docs/verifier/MANEUVERING_REVIEW.md`` grades the reference manoeuvring kernel
WITHHELD on the strength of one number (tactical diameter ``5.92 L`` vs a
literature band of ``2.90-3.60 L``), and the kernel's own evidence was
structural only (straight run, mirror symmetry, time step).  There was no anchor
that does not depend on the kernel itself.

This suite supplies one and locks it: the exact solution of the *linearised*
sway/yaw system about a steady straight run, derived from the same force model
the kernel implements (see ``tools/verifier/mmg_linear_benchmark.py``).  The
linear ODE has a closed form (2x2 matrix exponential), so the following are
machine-decidable without any external data:

1. the analytic rudder derivatives match finite differences of the kernel's own
   force model, with the O(delta^2) signature of a correct truncation;
2. the RK4 kernel reproduces the closed-form linear response to <3% at small
   rudder, and the residual is *second order* in delta - so neither the
   integrator (dtau = 0.01) nor the non-dimensionalisation of the derivatives
   can be silently wrong;
3. the closed-form propagator used as the reference is itself verified against
   an independent high-resolution RK4 of the linear ODE;
4. the directional-stability verdict is pinned: the documented KVLCC2 set gives
   ``Re(lambda_max) = +0.584 > 0`` and Nomoto discriminant ``C < 0``, i.e. the
   hull is *directionally unstable with the rudder fixed*.  That is why a
   small-rudder "steady gain" is not a physical quantity and why the tactical
   diameter is definition-dependent (pinned below at 2.97 L ITTC-style vs
   5.00 L steady-circle).

Evidence grade: A (analytic ground truth, no literature point data required).
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.verifier import mmg_linear_benchmark as mb  # noqa: E402
from tools.verifier.mmg_reference import MMGParams, manoeuvre, self_propulsion_n  # noqa: E402

DELTAS = (0.25, 0.5, 1.0, 2.0)


@pytest.fixture(scope="module")
def res():
    return mb.run(deltas_deg=DELTAS)


# ---------------------------------------------------------------- 1. rudder
def test_rudder_derivatives_match_finite_differences(res):
    rows = res["rudder_linearity"]
    assert len(rows) == len(DELTAS)
    for r in rows:
        assert r["Y_rel_err"] < 1e-3, r
        assert r["N_rel_err"] < 1e-3, r


def test_rudder_truncation_error_is_second_order(res):
    """Err(delta) ~ O(delta^2): quadrupling the angle multiplies the error by ~4."""
    errs = [r["Y_rel_err"] for r in res["rudder_linearity"]]
    for e_lo, e_hi in zip(errs, errs[1:]):
        ratio = e_hi / e_lo
        assert 3.4 < ratio < 4.6, (errs, ratio)


def test_rudder_delta_sign_is_physically_correct(res):
    """Positive (starboard) rudder must give a positive yaw moment and a negative sway force."""
    d = res["stability"]["derivs"]
    assert d["N_delta"] > 0.0
    assert d["Y_delta"] < 0.0


# ------------------------------------------------------------ 2. integrator
def test_kernel_integrator_matches_closed_form_linear_solution(res):
    """RK4 kernel vs exact linear response at the smallest rudder: <3% (<1% typical)."""
    best = res["integrator"][0]
    assert best["delta_deg"] == DELTAS[0]
    assert best["max_rel_err"] < 0.03
    assert res["verdict"]["integrator_within_3pct"] is True


def test_integrator_residual_is_second_order_in_rudder(res):
    """The kernel-vs-linear gap is the *nonlinearity* of the model, hence O(delta^2)."""
    errs = [r["max_rel_err"] for r in res["integrator"]]
    for e_lo, e_hi in zip(errs, errs[1:]):
        ratio = e_hi / e_lo
        assert 3.2 < ratio < 4.8, (errs, ratio)


def test_closed_form_propagator_verified_against_independent_rk4():
    """The reference propagator itself must be right (not self-referential)."""
    prm = MMGParams()
    n_p = self_propulsion_n(prm)
    A, b, _d = mb.linear_system(prm, n_p)
    delta = math.radians(0.5)

    # independent fine-step RK4 of the linear ODE
    tau_end, n = 2.0, 200000
    h = tau_end / n
    x = np.zeros(2)
    for _ in range(n):
        k1 = A @ x + b * delta
        k2 = A @ (x + 0.5 * h * k1) + b * delta
        k3 = A @ (x + 0.5 * h * k2) + b * delta
        k4 = A @ (x + h * k3) + b * delta
        x = x + (h / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
    ref = mb.linear_time_solution(prm, n_p, delta, np.array([tau_end]))[0]
    assert abs(x[1] - ref) / max(abs(ref), 1e-12) < 1e-6


# ------------------------------------------------------------- 3. stability
def test_documented_derivative_set_is_pinned_and_directionally_unstable(res):
    st = res["stability"]
    assert st["stable"] is False
    assert st["max_real_part"] == pytest.approx(0.5842, abs=2e-3)
    assert st["discriminant_C"] == pytest.approx(-0.023353, abs=1e-4)
    assert st["discriminant_C"] < 0.0  # Nomoto course-stability discriminant


def test_unstable_root_implies_no_small_rudder_steady_gain(res):
    """Because Re(lambda)>0, d r_ss'/d delta at delta->0 has the wrong sign.

    Pinned so that a future 'fix' of the derivative set shows up as a change of
    this verdict rather than as a silent re-baselining.
    """
    prm = MMGParams()
    n_p = self_propulsion_n(prm)
    A, b, _d = mb.linear_system(prm, n_p)
    r_ss_per_rad = float((-np.linalg.solve(A, b))[1])
    assert r_ss_per_rad < 0.0  # starboard rudder -> negative steady rate: unphysical


# ------------------------------------------------- 4. turning-circle indices
def test_turning_circle_is_definition_dependent(res):
    ft = res["full_turn_35"]
    defs = res["verdict"]["definitions"]
    # ITTC-style (y at psi=180deg) sits inside the literature band
    assert defs["ittc_180deg"] == pytest.approx(ft["DT_over_L_measured"], abs=1e-9)
    assert 2.90 <= defs["ittc_180deg"] <= 3.60
    # steady circle is 2 * (u'/r') and exceeds it
    assert defs["steady_circle"] == pytest.approx(2.0 * ft["u_ss"] / ft["r_ss"], rel=1e-9)
    assert defs["steady_circle"] > 4.5
    # the two definitions must disagree strongly - that is the finding
    assert defs["steady_circle"] / defs["ittc_180deg"] > 1.5


def test_speed_collapse_in_the_turn_is_recorded(res):
    """u' collapsing to ~0.41 is why R/L must not be evaluated with u'=1."""
    ft = res["full_turn_35"]
    assert ft["u_ss"] < 0.6
    assert ft["R_over_L"] == pytest.approx(ft["u_ss"] / abs(ft["r_ss"]), rel=1e-9)