"""Adversarial (evasion-threshold) tests for the MOCK-fingerprint gate.

These tests do not re-check that the detectors *can* fire (that is
``tests/test_mock_fingerprint.py``).  They fix the *reliability* of the gate:
how much a synthesised response has to be perturbed before the gate calls it
REAL, and how much head-room a healthy response leaves above that line.

Recorded, machine-judged facts (see artifacts/mock_fingerprint/detector_evasion_sweep.json):
  * a healthy Wageningen B-series response never triggers any detector
    (no false positive) and its smallest detector spread is ~0.28;
  * every structural detector is an exact-equality test (``rel_tol = 1e-12``),
    so a clamped-eta MOCK that wobbles by 1e-12 evades the gate;
  * the ratio-lock family stays caught at every perturbation level because
    *other* detectors (parameter blindness, physical bound) carry it.

The last of these is asserted in the direction of the *current* behaviour on
purpose: if somebody relaxes ``rel_tol`` this test goes red and the change has
to be reviewed instead of slipping through as a silent gate weakening.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tools.mock_fingerprint.adversary_sweep import (  # noqa: E402
    EPS_GRID,
    fake_clamped,
    fake_exact_law,
    fake_power_law,
    healthy,
    run_detectors,
    sweep,
)


@pytest.fixture(scope="module")
def results():
    return sweep()


# --------------------------------------------------------------- false positives
def test_healthy_response_never_triggers():
    res = run_detectors(healthy())
    assert res["verdict"] == "REAL", res["fired"]


def test_healthy_headroom_is_at_least_eight_orders():
    """Head-room between a healthy spread and the trigger tolerance."""
    res = run_detectors(healthy())
    min_spread = res["min_spread"]
    assert min_spread is not None
    assert min_spread / 1e-12 >= 1e8, f"only {min_spread / 1e-12:.1e}x head-room"


# --------------------------------------------------------------- true positives
def test_ratio_family_is_caught_at_every_perturbation(results):
    fam = [r for r in results["rows"] if r["family"] == "F1_exact_ratio"]
    assert fam and all(r["verdict"] == "MOCK" for r in fam)


def test_power_law_blind_family_is_caught_at_every_perturbation(results):
    fam = [r for r in results["rows"] if r["family"] == "F3_power_law_blind"]
    assert fam and all(r["verdict"] == "MOCK" for r in fam)


def test_clamped_eta_is_caught_on_the_exact_response():
    res = run_detectors(fake_clamped(0.0))
    assert res["verdict"] == "MOCK"
    assert any(f.startswith("clamp_plateau") for f in res["fired"])


# ------------------------------------------------- documented weakness (guard)
def test_clamped_eta_evades_once_it_wobbles():
    """Exact-equality detector: 1e-12 relative wobble is enough to evade it.

    This is the audited weakness (docs/verification_debt/mock_fingerprint_evasion).
    It is asserted as current behaviour so that raising ``rel_tol`` during a fix
    makes this test pass differently and forces an explicit review of the gate.
    """
    assert run_detectors(fake_clamped(0.0))["verdict"] == "MOCK"
    for eps in (1e-12, 1e-9, 1e-4):
        assert run_detectors(fake_clamped(eps))["verdict"] == "REAL", eps


def test_ratio_lock_detector_is_exact_only():
    """``ratio_lock`` fires on the exact synthetic law and not on a wobbled one."""
    from tools.mock_fingerprint import ratio_lock

    def fire(eps):
        drag = [r for r in fake_exact_law(eps) if r["J"] > 0]
        return ratio_lock(drag, "KQ", "KT")["triggered"]

    assert fire(0.0) is True
    assert fire(1e-12) is False
    assert fire(1e-6) is False


# ------------------------------------------------------------ artefact contract
def test_evasion_thresholds_are_recorded(results):
    thr = results["evasion_threshold_eps"]
    assert thr["F1_exact_ratio"] is None          # never evades
    assert thr["F3_power_law_blind"] is None      # never evades
    assert thr["F4_clamped_eta"] <= 1e-12         # evades at the smallest wobble
    assert results["rows"], "sweep produced no rows"
    assert EPS_GRID[0] == 0.0


def test_parameter_sensitivity_is_far_above_the_tolerance(results):
    """Z=4 -> Z=5 changes K_T by O(10%), i.e. 11 orders above rel_tol = 1e-12."""
    assert results["z_sensitivity_max_rel"] > 0.05


def test_unknown_perturbation_level_does_not_crash():
    assert run_detectors(fake_power_law(1e-3))["verdict"] == "MOCK"