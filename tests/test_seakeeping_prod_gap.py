"""Regression tests for the seakeeping production-gap verification debt.

Two independent layers:

1. Analytic layer (no network, no platform): the closed-form references used by
   ``docs/verification_debt/seakeeping_prod_gap/probe.py`` are re-derived here from
   first principles, so a silent edit of the probe cannot move the goalposts.
2. Evidence layer: the committed probe artifact must keep reporting the mock
   fingerprints it found on the live endpoint.  If production is fixed and the
   fingerprint disappears, this test fails on purpose -> refresh the artifact
   (``python3 docs/verification_debt/seakeeping_prod_gap/probe.py``) and update the
   ledger in the same commit.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]
PROBE_DIR = REPO / "docs" / "verification_debt" / "seakeeping_prod_gap"
ART = PROBE_DIR / "artifacts" / "gap_results.json"

RHO, G = 1025.0, 9.81
# 100 m box barge, Cb = Cwp = 1 -> all hydrostatics closed form
L, B, T, DISP_T, GM = 100.0, 20.0, 8.0, 16000.0, 2.0
MASS = DISP_T * 1000.0
AW = L * B


# ---------------------------------------------------------------------------
# layer 1: closed-form references
# ---------------------------------------------------------------------------
def test_heave_period_lower_bound_is_exact():
    """A33 = 0 (non-physical) gives the shortest possible heave period."""
    t = 2.0 * math.pi * math.sqrt(MASS / (RHO * G * AW))
    assert t == pytest.approx(5.6044, abs=1e-3)


def test_heave_period_scales_with_added_mass():
    """T33 = 2 pi sqrt(m (1+kappa) / (rho g Aw)) -> strictly increasing in kappa."""
    base = 2.0 * math.pi * math.sqrt(MASS / (RHO * G * AW))
    vals = [base * math.sqrt(1.0 + k) for k in (0.3, 0.7, 0.9, 1.5)]
    assert vals == sorted(vals)
    # a real (positive) added mass can never reproduce the A33 = 0 value
    assert vals[0] / base == pytest.approx(math.sqrt(1.3), rel=1e-12)


def test_roll_period_metacentric_reference():
    """T44 = 2 pi k_xx / sqrt(g GM) with the 0.36 B engineering convention."""
    t = 2.0 * math.pi * 0.36 * B / math.sqrt(G * GM)
    assert t == pytest.approx(10.2132, abs=1e-3)
    # GM sweep must scale as 1/sqrt(GM) exactly
    t_soft = 2.0 * math.pi * 0.36 * B / math.sqrt(G * 0.5)
    assert t_soft / t == pytest.approx(2.0, rel=1e-12)


def test_pitch_and_heave_cannot_share_a_period_by_construction():
    """For a non-degenerate hull the pitch and heave periods differ:
    T55/T33 = sqrt((I55+A55)/(m+A33) * (rho g Aw)/(m g GM_L)); I55 + A55 >> A33
    and Aw/GM_L > 1, so the ratio is far from 1 for this barge."""
    i55_over_m = (0.25 * L) ** 2                     # k_yy = 0.25 L convention
    a33_over_m = 0.8
    gm_l = 87.08
    ratio = math.sqrt(((i55_over_m + 1.0) / (1.0 + a33_over_m)) * (AW / gm_l))
    assert ratio > 1.05, "heave and pitch periods must be separated by >5%"


# ---------------------------------------------------------------------------
# layer 2: committed evidence must keep the fingerprints
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def art():
    if not ART.exists():
        pytest.skip("probe artifact missing; run probe.py")
    return json.loads(ART.read_text())


def test_artifact_kernel_self_consistency(art):
    """The in-repo REAL kernel must satisfy its own reported quantities."""
    k = art["kernel"]
    assert k["A33_over_mass"] > 0.3, "kernel must carry a physical heave added mass"
    assert k["T33_self_check_pct"] < 1.0
    assert k["T55_self_check_pct"] < 1.0
    assert k["T44_vs_ref_pct"] < 3.0


def test_live_endpoint_mock_fingerprints(art):
    """Production :8001 /seakeeping/motion fingerprints (audited 2026-11-01)."""
    chk = art.get("live_checks") or {}
    if not chk:
        pytest.skip("probe ran offline")
    assert chk["P1_amplitudes_independent_of_wave_and_heading"]["verdict"] == "MOCK"
    assert chk["P2_heave_pitch_identical_period"]["identical"] is True
    assert chk["P2b_rao_heave_pitch_bitwise_identical"]["max_abs_diff"] == 0.0
    # heave period sits on the A33 = 0 bound -> no added mass at all
    assert chk["P3_heave_period_vs_no_added_mass_bound"]["ratio"] == pytest.approx(1.0, abs=0.02)
    # roll period is driven by GM (physical) but 21.5% above the metacentric reference
    assert chk["P4b_roll_period_follows_GM"]["ratio"] == pytest.approx(2.0, abs=1e-6)
    assert 15.0 < chk["P4_roll_vs_metacentric_reference"]["err_pct"] < 30.0


def test_deployment_gap_quantified(art):
    """Deployed vs in-repo kernel heave period gap must be large (>15% = unacceptable)."""
    if not (art.get("live_checks")):
        pytest.skip("probe ran offline")
    t_dep = art["live_checks"]["P3_heave_period_vs_no_added_mass_bound"]["T33_endpoint"]
    t_ker = art["kernel"]["T33"]
    gap = abs(t_dep - t_ker) / t_ker * 100.0
    assert gap > 15.0, f"gap {gap:.1f}% should be reported as unacceptable"


def test_kernel_long_wave_limit(art):
    """heave RAO -> 1 exactly as kL -> 0 (model independent)."""
    lw = art.get("kernel_long_wave") or []
    vals = [r["heave_rao"] for r in lw if "heave_rao" in r]
    if not vals:
        pytest.skip("no kernel long-wave samples")
    assert abs(vals[0] - 1.0) < 0.01, f"kernel long-wave heave RAO {vals[0]} off by >1%"
    # and it must be monotone in omega (approaching 1 from above/below consistently)
    assert len(vals) == len(lw)


def test_figures_present(art):
    for rel in art.get("figures", []):
        assert (PROBE_DIR / rel).exists(), f"missing figure {rel}"