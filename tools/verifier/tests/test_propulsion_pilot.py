"""Tests for the propulsion open-water verifier battery (offline, no API needed)."""
from __future__ import annotations

import math
import os
import sys

import pytest

_TOOLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _TOOLS)

from verifier.pilots import propulsion_openwater as ow          # noqa: E402
from verifier.pilots.propulsion_control import control_sampler  # noqa: E402


# ---------------------------------------------------------------------------
# analytic references
# ---------------------------------------------------------------------------
def test_actuator_disk_ct_derivation():
    """C_T = 8KT/(pi J^2) must reproduce the textbook thrust loading."""
    KT, J = 0.23, 0.5
    assert ow.thrust_loading(KT, J) == pytest.approx(8 * 0.23 / (math.pi * 0.25), rel=1e-12)


def test_ideal_efficiency_known_values():
    """eta_ideal = 2/(1+sqrt(1+C_T)); hand-checked at C_T = 3 and C_T = 15."""
    # C_T = 3 -> eta = 2/3 = 0.6667
    J = math.sqrt(8 * 0.75 / (math.pi * 3.0))   # KT chosen so that C_T == 3
    assert ow.thrust_loading(0.75, J) == pytest.approx(3.0, rel=1e-12)
    assert ow.ideal_efficiency(0.75, J) == pytest.approx(2.0 / 3.0, rel=1e-9)
    # C_T = 15 -> eta = 2/5 = 0.4
    J2 = math.sqrt(8 * 0.75 / (math.pi * 15.0))
    assert ow.ideal_efficiency(0.75, J2) == pytest.approx(0.4, rel=1e-9)


def test_ideal_efficiency_monotone_in_loading():
    prev = None
    for kt in (0.05, 0.1, 0.2, 0.3, 0.4):
        eta = ow.ideal_efficiency(kt, 0.7)
        if prev is not None:
            assert eta < prev        # heavier loading -> lower ideal efficiency
        prev = eta


def test_eta0_definition_roundtrip():
    assert ow.eta0_from_coefficients(0.5, 0.23, 0.023) == pytest.approx(
        0.5 * 0.23 / (2 * math.pi * 0.023), rel=1e-12)
    assert ow.eta0_from_coefficients(0.0, 0.3, 0.03) == 0.0


# ---------------------------------------------------------------------------
# battery behaviour: admissible control passes, MOCK-style surrogate fails
# ---------------------------------------------------------------------------
def test_control_candidate_passes_every_invariant():
    report = ow.evaluate_candidate(control_sampler, "control-admissible")
    failed = [i["name"] for i in report["invariants"] if not i["passed"]]
    assert failed == [], f"battery too strict, control failed: {failed}"
    assert report["verdict"] == "pass"


def _placeholder_sampler(J, P_D, Z, Ae_A0):
    """Replica of the platform's linear placeholder (structure only, for the test)."""
    kt = 0.3 * P_D - 0.2 * J
    kq = 0.03 * P_D - 0.02 * J
    if Z == 3:
        kt *= 0.95; kq *= 0.95
    elif Z == 5:
        kt *= 1.02; kq *= 1.02
    elif Z == 6:
        kt *= 1.04; kq *= 1.04
    kt *= (0.5 + Ae_A0); kq *= (0.5 + Ae_A0)
    kt = max(0.05, min(0.5, kt)); kq = max(0.005, min(0.05, kq))
    eta = (J * kt) / (2 * math.pi * kq) if (J > 0 and kq > 0) else 0.5
    return {"KT": kt, "KQ": kq, "eta0": min(eta, 0.85)}


def test_linear_placeholder_fails_all_invariants():
    report = ow.evaluate_candidate(_placeholder_sampler, "linear-placeholder")
    assert report["verdict"] == "fail"
    assert report["passed"] == 0


def test_pitch_invariance_detected_analytically():
    """For the placeholder, eta0 is a function of J alone: 1.5915*J (clamped)."""
    for J in (0.1, 0.3, 0.5):
        etas = [_placeholder_sampler(J, p, 4, 0.65)["eta0"] for p in (0.6, 1.0, 1.4)]
        assert max(etas) - min(etas) == pytest.approx(0.0, abs=1e-12)
        assert etas[0] == pytest.approx(min(1.5915 * J, 0.85), rel=2e-3)


def test_actuator_disk_violation_reported_for_placeholder():
    """At J=0.5, P/D=1.4 the placeholder exceeds eta_ideal by ~26%."""
    r = _placeholder_sampler(0.5, 1.4, 4, 0.65)
    eta_ideal = ow.ideal_efficiency(r["KT"], 0.5)
    excess = r["eta0"] / eta_ideal - 1.0
    assert excess == pytest.approx(0.265, abs=0.01)


def test_plateau_detected():
    inv = ow.inv_kt_monotone_no_plateau(_placeholder_sampler)
    assert not inv.passed and inv.measured >= 1


def test_blade_count_collision_detected():
    inv = ow.inv_blade_count_resolution(_placeholder_sampler)
    assert not inv.passed           # Z=7 falls through to the uncorrected branch


def test_bollard_branch_detected():
    inv = ow.inv_bollard_efficiency(_placeholder_sampler)
    assert not inv.passed
    assert inv.measured == pytest.approx(0.5)


def test_report_schema_shape():
    report = ow.evaluate_candidate(control_sampler, "control-admissible")
    for key in ("candidate_id", "domain", "invariants", "passed", "total", "verdict", "samples"):
        assert key in report
    assert len(report["samples"]) == len(ow.J_GRID) * len(ow.P_GRID)
    for inv in report["invariants"]:
        for key in ("name", "passed", "measured", "reference", "tolerance", "provenance"):
            assert key in inv