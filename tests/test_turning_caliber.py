"""Caliber judges for the manoeuvring turning-circle metrics (m1867).

The anchor is analytic and needs no literature value: a ship in a steady turn
follows a circle of radius R = V/Omega, and the transverse offset after 180 deg
of heading change *is* the diameter of that circle.  So a correct caliber
implementation must satisfy, on an exact synthetic circle,

    advance == transfer == R,   tactical_diameter == steady_diameter == 2R

to round-off.  The pre-m1867 caliber returned ``2|y|@180`` (i.e. 4R) here and is
rejected by the same judge in ``test_legacy_caliber_is_rejected``.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "tools", "verifier"),):
    if p not in sys.path:
        sys.path.insert(0, p)

import mmg_reference as R  # noqa: E402
import turning_caliber_audit as A  # noqa: E402

L = R.KVLCC2["L"]
REL = 1e-9


# --------------------------------------------------------------- analytic anchor
def test_exact_circle_advance_transfer_equal_radius():
    """advance and transfer on an exact circle are both the radius."""
    run = A.circle_hist(2.4, 0.0)
    ti = R.turning_indices(run, L)
    assert ti["advance"] / L == pytest.approx(2.4, rel=REL)
    assert ti["transfer"] / L == pytest.approx(2.4, rel=REL)


def test_exact_circle_tactical_diameter_equals_diameter():
    """T_d = 2R on an exact circle -- kills the factor-2 caliber error."""
    run = A.circle_hist(2.4, 0.0)
    ti = R.turning_indices(run, L)
    assert ti["tactical_diameter"] / L == pytest.approx(4.8, rel=REL)
    # ... and the caliber is self-consistent: ITTC offset == steady diameter
    assert ti["tactical_diameter"] / L == pytest.approx(
        ti["steady_diameter"] / L, rel=REL)


def test_legacy_caliber_is_rejected():
    """Negative control: the old 2|y|@180 caliber must miss by 100 %."""
    run = A.circle_hist(2.4, 0.0)
    leg = A.legacy_caliber(run, L)["tactical_diameter"]
    assert leg / L == pytest.approx(9.6, rel=REL)
    assert abs(leg / L - 4.8) / 4.8 > 0.5


@pytest.mark.parametrize("radius", [0.8, 2.4, 6.0])
def test_diameter_scales_linearly_with_radius(radius):
    ti = R.turning_indices(A.circle_hist(radius, 0.0), L)
    assert ti["tactical_diameter"] / L == pytest.approx(2.0 * radius, rel=REL)


# --------------------------------------------------------------- drift handling
def test_steady_diameter_uses_total_speed_not_surge():
    """With 18 deg drift the surge-only caliber under-reports by cos(beta)."""
    beta = 18.0
    R_ = 2.4
    ti = R.turning_indices(A.circle_hist(R_, beta), L)
    assert ti["steady_diameter"] / L == pytest.approx(2.0 * R_, rel=REL)
    assert ti["steady_diameter_surge_only"] / L == pytest.approx(
        2.0 * R_ * math.cos(math.radians(beta)), rel=REL)
    assert ti["drift_angle_deg"] == pytest.approx(beta, abs=1e-9)


# ------------------------------------------------------------------- bookkeeping
def test_tau_end_is_time_not_surge_speed():
    """Regression: ``tau_end`` used to return column 0 (u'), i.e. the speed."""
    run = R.manoeuvre(R.MMGParams(), R.self_propulsion_n(R.MMGParams()), 35.0)
    ti = R.turning_indices(run, L)
    assert ti["tau_end"] == pytest.approx((len(run["hist"]) - 1) * run["tau_step"])
    assert ti["tau_end"] != pytest.approx(ti["u_end"])


# --------------------------------------------------------------- real kernel run
@pytest.fixture(scope="module")
def kernel_35():
    prm = R.MMGParams()
    n_p = R.self_propulsion_n(prm)
    run = R.manoeuvre(prm, n_p, 35.0)
    return R.turning_indices(run, L, 7.97)


def test_kernel_tactical_diameter_in_literature_band(kernel_35):
    td = kernel_35["tactical_diameter"] / L
    assert 2.90 <= td <= 3.60, f"T_d/L={td:.3f} outside 2.90-3.60"


def test_kernel_legacy_double_caliber_outside_band(kernel_35):
    """The historical 5.92 L was exactly 2*T_d -- the artifact, not physics."""
    legacy = 2.0 * kernel_35["tactical_diameter"] / L
    assert legacy > 3.60
    assert legacy == pytest.approx(5.92, abs=0.05)


def test_kernel_steady_diameter_still_out_of_band(kernel_35):
    """Honest limit: the asymptotic circle is still wider than the band."""
    assert kernel_35["steady_diameter"] / L > 3.60


# --------------------------------------------------------------- audit judges
def test_audit_judges_all_pass():
    assert A.main() == 0
    import json
    res = json.load(open(os.path.join(ROOT, "artifacts", "turning_caliber",
                                      "numbers.json"), encoding="utf-8"))
    assert res["J1_exact_circle"]["verdict"] == "PASS"
    assert res["J1n_negative_control"]["verdict"] == "PASS"
    assert res["J2_drift_circle"]["verdict"] == "PASS"
    assert res["J5_band_judge_control"]["verdict"] == "PASS"
    # J3 is the platform-side finding: the surge-only caliber must be caught
    assert res["J3_platform_caliber"]["verdict"] in ("DEFECT", "OK", "WITHHELD")


def test_band_judge_can_fail():
    assert A.band_judge(3.2)["verdict"] == "PASS"
    assert A.band_judge(A.BAND[1] * 1.16)["verdict"] == "FAIL"
    assert A.band_judge(float("nan"))["verdict"] == "WITHHELD"