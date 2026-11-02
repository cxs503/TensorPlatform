"""A-class gate for the digital-twin EKF: exact algebra + chi-square consistency.

Context
-------
``backend/services/digital_twin/state_estimator.py`` publishes three estimated
correction factors **and their 1-sigma band**.  Its only evidence used to be
behavioural (``backend/tests/test_digital_twin_estimator_predictor.py``:
"converges in 50 steps", "rejects a spike") -- a filter with a 30x wrong
covariance passes both.

Because the state is a static parameter vector, the Kalman recursion has an
exact closed form in information form (weighted least squares with the R the
filter claims to use), and the normalised error squared over a Monte-Carlo
ensemble is exactly chi-square.  Those are the anchors below; see
``tools/verification/digital_twin/ekf_consistency_vv.py`` for the derivations,
the stub registry and the measurement synthesis.

What the suite caught (m1867)
-----------------------------
Channels 1 and 2 built ``R = (r_rel * z)**2`` while ``r_rel`` is a relative
*variance* (0.03^2, 0.032^2, 0.1^2 as documented) -- channel 0 used the correct
``r_rel * x**2``.  The resulting R was 1/r_rel = 1000x too small, so the filter
trusted the resistance/damage sensors ~30x more than documented: sigma collapsed
(E9 NEES = 1300 against dof = 2) and the 5-sigma innovation gate then locked the
resistance channel out after two steps, freezing the estimate silently (E11).
Both anchors are now regression gates.

Evidence grade: A (closed-form / chi-square ground truth, no literature point
data).  The *physical* accuracy against a real hull stays WITHHELD: the
measurement models are surrogate outputs.

Run: ``python3 -m pytest tests/test_ekf_consistency_vv.py -q``
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util  # noqa: E402

_TOOL = os.path.join(_ROOT, "tools", "verification", "digital_twin", "ekf_consistency_vv.py")
_spec = importlib.util.spec_from_file_location("ekf_consistency_vv", _TOOL)
vv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vv)  # noqa: E402


@pytest.fixture(scope="module")
def res():
    return vv.run()


def test_all_anchors_pass(res):
    v = res["verdict_counts"]
    assert v.get("FAIL", 0) == 0 and v.get("ERROR", 0) == 0, {
        k: a for k, a in res["anchors"].items() if a.get("verdict") != "PASS"}


def test_e1_static_kf_equals_weighted_least_squares(res):
    a = res["anchors"]["E1_wls_equivalence"]
    assert a["rel_err_x"] < 1e-8
    assert a["rel_err_P_diag"] < 1e-8
    assert a["max_clip_gap"] == 0.0          # bounds inactive: anchor is unclipped


def test_e2_process_noise_is_prior_inflation(res):
    a = res["anchors"]["E2_prior_inflation"]
    assert a["rel_err_x"] < 1e-5
    assert a["rel_err_P_diag"] < 1e-3


def test_e3_order_invariance(res):
    a = res["anchors"]["E3_order_invariance"]
    assert a["rel_diff_x"] < 1e-10
    assert a["rel_diff_P_diag"] < 1e-10


def test_e4_predict_adds_exactly_q2_dt(res):
    a = res["anchors"]["E4_predict_exactness"]
    assert a["rel_gap_P"] < 1e-14
    assert a["abs_gap_x"] == 0.0


def test_e5_zero_noise_pins_state_to_measurement(res):
    a = res["anchors"]["E5_zero_noise_pin"]
    assert a["rel_err_x1"] < 1e-6 and a["rel_err_x2"] < 1e-6


def test_e6_covariance_is_symmetric_and_psd(res):
    a = res["anchors"]["E6_psd_joseph"]
    assert a["min_eig"] >= 0.0
    assert a["asymmetry"] <= 1e-12 * max(a["P_scale"], 1.0)


def test_e7_unmeasured_channels_are_untouched(res):
    a = res["anchors"]["E7_channel_isolation"]
    assert a["other_channels_moved"] == 0.0
    assert a["gm_moved"] > 0.0


def test_e8_gate_drops_outlier_keeps_valid_channel(res):
    a = res["anchors"]["E8_gate_semantics"]
    assert a["accepted"] is True
    assert a["resistance_bias_moved"] < 1e-3


def test_e9_nees_matches_chi_square(res):
    a = res["anchors"]["E9_nees_consistency"]
    dof = a["dof"]
    assert 0.8 * dof <= a["mean_nees"] <= 1.25 * dof, a["mean_nees"]
    # the published 1-sigma band must match the empirical spread within 15 %
    for r in a["sigma_ratio_emp_over_rep"]:
        assert 0.85 <= r <= 1.15, a["sigma_ratio_emp_over_rep"]


def test_e10_gm_channel_uses_variance_semantics(res):
    a = res["anchors"]["E10_gm_channel_closed_form"]
    assert a["rel_err_x"] < 1e-12
    assert a["rel_err_P"] < 1e-12


def test_e11_no_gate_lockout_on_valid_drift(res):
    """Regression: a 2.7 % measurement drift must never be gated out (m1867)."""
    a = res["anchors"]["E11_gate_lockout"]
    assert all(a["accepted"]), a["accepted"]
    assert a["rejected_counter_final"] == 0


def test_r_semantics_are_consistent_across_channels():
    """All three channels must consume r_rel as a relative variance (R = r_rel*v^2).

    This is the direct unit-level statement of the m1867 defect: channel 0 used
    ``r_rel * x**2`` while channels 1/2 used ``(r_rel * z)**2`` = ``r_rel**2 * z**2``.
    """
    ekf = vv.make_ekf()
    frame = vv.wls_frame(vv.meas({"R_kN": 1600.0, "D_per_day": 4.0e-5}))
    H, z, R = frame
    # reference: relative-variance contract
    assert np.isclose(R[0, 0], vv.R_REL[1] * z[0] ** 2, rtol=1e-12)
    # the filter must reproduce that R: compare its posterior with the closed form
    ekf.step(0.0, vv.meas({"R_kN": 1600.0, "D_per_day": 4.0e-5}))
    x_ref, P_ref = vv.wls_posterior([frame], inflation=np.zeros(3))
    assert np.max(np.abs(ekf.x - x_ref) / np.abs(x_ref)) < 1e-8
    # and the implied sigma on resistance_bias must be ~3.2 %, not ~0.1 %
    sigma_x1 = float(np.sqrt(ekf.P[1, 1]))
    assert 0.02 < sigma_x1 < 0.06, sigma_x1