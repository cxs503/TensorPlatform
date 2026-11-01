"""Regression tests for the structural-domain verification debt audit (mission 1590).

Two layers:

1. ``TestReference*`` -- unit tests of the *independent reference* formulas.
   These must hold unconditionally: they pin the closed-form baselines
   (Timoshenko plate buckling, Faulkner ultimate strength, BS 7608 / DNV-RP-C203
   S-N curves, Palmgren-Miner) so the audit cannot drift.

2. ``TestServedConformance`` -- conformance of the *recorded* platform response
   (``artifacts/probe_*.json``) against those baselines.  These encode the
   deviation found on 2026-11-01 together with its magnitude, so that

   * a future fix makes them fail loudly (the audit ledger has to be updated), and
   * nothing silently regresses in the other direction.

The second layer is offline: it never touches the network, so it runs in CI.
"""

from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "validation"))

import structure_reference as ref  # noqa: E402

ART = os.path.join(ROOT, "artifacts")


# ---------------------------------------------------------------------------
# 1. reference formulas
# ---------------------------------------------------------------------------
class TestReferencePlateBuckling:
    def test_rigidity(self):
        D = ref.plate_rigidity(206e9, 0.015, 0.3)
        assert D == pytest.approx(206e9 * 0.015 ** 3 / (12 * (1 - 0.09)), rel=1e-12)

    def test_square_simply_supported_k_is_four(self):
        assert ref.k_uniaxial_x(1.0) == pytest.approx(4.0, rel=1e-12)

    def test_long_plate_k_tends_to_four(self):
        for alpha in (2.0, 3.0, 5.0, 10.0):
            assert ref.k_uniaxial_x(alpha) == pytest.approx(4.0, rel=1e-9)

    def test_short_plate_k_grows(self):
        # classic Timoshenko curve: (alpha + 1/alpha)^2 for a/b < 1
        assert ref.k_uniaxial_x(0.5) == pytest.approx(6.25, rel=1e-9)
        assert ref.k_uniaxial_x(1.0 / 3.0) == pytest.approx((3 + 1 / 3) ** 2, rel=1e-9)

    def test_sigma_cr_matches_bryan_closed_form(self):
        # sigma_cr = k pi^2 E / (12 (1-nu^2)) (t/b)^2
        E, t, b, nu, k = 206e9, 0.015, 2.4, 0.3, 4.0
        closed = k * math.pi ** 2 * E / (12 * (1 - nu ** 2)) * (t / b) ** 2 / 1e6
        assert ref.sigma_cr(k, E, t, b, nu) == pytest.approx(closed, rel=1e-9)
        assert ref.sigma_cr(k, E, t, b, nu) == pytest.approx(29.09, rel=2e-3)

    def test_transverse_coefficient_bound(self):
        # k_b = [1 + (b/a)^2]^2 and it can never drop below 1
        assert ref.k_uniaxial_y(1.0) == pytest.approx(4.0, rel=1e-12)
        assert ref.k_uniaxial_y(2.0) == pytest.approx(1.5625, rel=1e-12)
        for alpha in (0.5, 1.0, 2.0, 3.0, 5.0, 10.0):
            assert ref.k_uniaxial_y(alpha) >= 1.0

    def test_shear_coefficient(self):
        assert ref.k_shear(1.0) == pytest.approx(9.34, rel=1e-9)
        assert ref.k_shear(2.0) == pytest.approx(6.34, rel=1e-9)
        assert ref.k_shear(0.5) == pytest.approx(6.34, rel=1e-9)

    def test_slenderness_and_faulkner(self):
        beta = ref.plate_slenderness(2.4, 0.015, 235.0, 206000.0)
        assert beta == pytest.approx(5.4037, rel=1e-4)
        su = ref.faulkner_ultimate(235.0, beta)
        assert su == pytest.approx(235.0 * (2 / beta - 1 / beta ** 2), rel=1e-12)
        assert su == pytest.approx(78.9, rel=2e-3)
        # stocky plate: full yield
        assert ref.faulkner_ultimate(235.0, 0.8) == 235.0
        # ultimate must exceed the elastic critical stress for a slender panel
        assert su > ref.sigma_cr(4.0, 206e9, 0.015, 2.4, 0.3)


class TestReferenceFatigue:
    def test_dnv_d_strength_constant(self):
        assert 10 ** ref.DNV_D_LOGA_M3 == pytest.approx(1.4588e12, rel=1e-4)

    def test_dnv_d_knee(self):
        n_knee, s_knee = ref.knee_cycles(ref.DNV_D_LOGA_M3, ref.DNV_D_LOGA_M5)
        assert n_knee == pytest.approx(1e7, rel=5e-3)
        assert s_knee == pytest.approx(52.65, rel=1e-3)

    def test_dnv_curve_monotone_and_continuous(self):
        prev = math.inf
        for s in (30, 40, 45, 50, 52, 53, 60, 80, 120):
            n = ref.dnv_d_cycles(s)
            assert n < prev
            prev = n
        # continuity at the knee
        s_knee = ref.knee_cycles(ref.DNV_D_LOGA_M3, ref.DNV_D_LOGA_M5)[1]
        n_lo = 10 ** (ref.DNV_D_LOGA_M5 - 5 * math.log10(s_knee))
        n_hi = 10 ** (ref.DNV_D_LOGA_M3 - 3 * math.log10(s_knee))
        assert n_lo == pytest.approx(n_hi, rel=1e-3)

    def test_bs7608_class_d_anchor(self):
        # 53.4 MPa stress range at 2e6 cycles
        assert ref.bs_d_cycles(53.4) == pytest.approx(2e6, rel=1e-3)
        # knee at 1e7 cycles: 31.2 MPa
        assert ref.bs_d_cycles(31.2) == pytest.approx(1e7, rel=5e-3)

    def test_bs_stricter_than_dnv_for_class_d(self):
        for s in (40.0, 60.0, 80.0):
            assert ref.bs_d_cycles(s) < ref.dnv_d_cycles(s)

    def test_miner_superposition(self):
        d = ref.miner_damage([50.0, 80.0], [1e6, 5e5], ref.bs_d_cycles)
        expect = 1e6 / ref.bs_d_cycles(50.0) + 5e5 / ref.bs_d_cycles(80.0)
        assert d == pytest.approx(expect, rel=1e-12)

    def test_rel_err_guard(self):
        assert ref.rel_err(1.0, 0.0) == math.inf
        assert ref.rel_err(0.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# 2. conformance of the recorded platform response
# ---------------------------------------------------------------------------
def _load(name):
    path = os.path.join(ART, name)
    if not os.path.exists(path):
        pytest.skip(f"probe artifact missing: {name}")
    with open(path) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def probe():
    return _load("probe_struct_fatigue.json")


@pytest.fixture(scope="module")
def audit():
    return _load("structure_validation.json")


def _plate_row(probe, a_over_b, edge, t_mm=15.0):
    for r in probe["plate"]:
        i = r["input"]
        if (r.get("edge_condition") == edge and abs(i["a"] / i["b"] - a_over_b) < 1e-6
                and abs(i["t"] * 1000 - t_mm) < 1e-6 and r.get("critical_stress_a")):
            return r
    pytest.skip(f"no probe row for a/b={a_over_b} edge={edge}")


class TestServedPlateStability:
    """Documented state of /api/v1/plate-stability/plate on 2026-11-01."""

    def test_core_bryan_formula_is_correct(self, probe):
        r = _plate_row(probe, 1.0, "SSSS")
        k = r["critical_stress_a"] / ref.sigma_cr(1.0, 206e9, 0.015, 2.4, 0.3)
        assert k == pytest.approx(4.0, rel=5e-3)  # PASS: real formula

    def test_short_panel_coefficient_is_floored_and_non_conservative(self, probe):
        r = _plate_row(probe, 0.5, "SSSS")
        k = r["critical_stress_a"] / ref.sigma_cr(1.0, 206e9, 0.015, 2.4, 0.3)
        # exact value is 6.25; the platform floors k at 4.0 (-36%)
        assert k == pytest.approx(4.0, rel=5e-3)
        assert (k - 6.25) / 6.25 * 100 < -30

    def test_edge_condition_token_is_inconsistent(self, probe):
        a = _plate_row(probe, 0.5, "SSSS")
        b = _plate_row(probe, 0.5, "simply_supported")
        # identical physical condition, two different answers -> contract defect
        assert a["critical_stress_a"] != pytest.approx(b["critical_stress_a"], rel=0.01)

    def test_clamped_token_is_unrecognised(self, probe):
        cccc = _plate_row(probe, 1.0, "CCCC")
        ssss = _plate_row(probe, 1.0, "SSSS")
        # 'CCCC' silently degrades to the simply-supported result
        assert cccc["critical_stress_a"] == pytest.approx(ssss["critical_stress_a"], rel=1e-9)

    def test_clamped_square_underpredicts(self, probe):
        r = _plate_row(probe, 1.0, "clamped")
        k = r["critical_stress_a"] / ref.sigma_cr(1.0, 206e9, 0.015, 2.4, 0.3)
        assert k == pytest.approx(4.0, rel=5e-3)
        assert (k - ref.K_CLAMPED) / ref.K_CLAMPED * 100 < -40

    def test_transverse_coefficient_below_variational_bound(self, probe):
        r = _plate_row(probe, 3.0, "SSSS")
        kb = r["critical_stress_b"] / ref.sigma_cr(1.0, 206e9, 0.015, 2.4, 0.3)
        assert kb == pytest.approx(4.0 / 9.0, rel=5e-3)
        assert kb < 1.0  # impossible for a plate buckling coefficient

    def test_shear_coefficient_is_correct(self, probe):
        for ab in (1.0, 2.0, 3.0):
            r = _plate_row(probe, ab, "SSSS")
            ks = r["critical_shear"] / ref.sigma_cr(1.0, 206e9, 0.015, 2.4, 0.3)
            assert ks == pytest.approx(ref.k_shear(ab), rel=0.01)

    def test_ultimate_strength_collapses_to_critical_stress(self, probe):
        rows = [r for r in probe["plate"]
                if r.get("critical_stress_a") and r.get("ultimate_strength")]
        assert rows, "no probe rows"
        for r in rows:
            assert r["ultimate_strength"] == pytest.approx(r["critical_stress_a"], rel=1e-9)
        # and therefore under-predicts Faulkner by a wide margin for slender panels
        r = _plate_row(probe, 1.0, "SSSS")
        beta = ref.plate_slenderness(2.4, 0.015, 235.0, 206000.0)
        su = ref.faulkner_ultimate(235.0, beta)
        assert (r["ultimate_strength"] - su) / su * 100 < -40


class TestServedFatigue:
    """Documented state of /api/v1/fatigue/analyze on 2026-11-01."""

    @staticmethod
    def _served_lives(probe, curve):
        out = {}
        for r in probe["fatigue"]:
            if r.get("sn_curve") == curve and r.get("N"):
                out[r["S"]] = r["N"]
        return out

    def test_m3_branch_strength_constant(self, probe):
        assert self._served_lives(probe, "BS-D")[80.0] * 80.0 ** 3 == pytest.approx(
            1.4588e12, rel=1e-3)

    def test_served_curve_contradicts_its_own_reference_stress(self, probe):
        lives = self._served_lives(probe, "BS-D")
        c3 = lives[80.0] * 80.0 ** 3
        implied = (c3 / 2e6) ** (1 / 3)
        # the class is published as BS 7608 D (53.4 MPa @2e6) but the curve served is DNV D
        assert implied == pytest.approx(90.0, rel=0.01)
        assert (implied - 53.4) / 53.4 * 100 > 60

    def test_life_is_optimistic_against_bs7608(self, probe):
        lives = self._served_lives(probe, "BS-D")
        for s, n in ((70.0, lives[70.0]), (80.0, lives[80.0]), (100.0, lives[100.0])):
            assert n / ref.bs_d_cycles(s) == pytest.approx(4.79, rel=0.02)

    def test_life_is_optimistic_below_the_knee(self, probe):
        lives = self._served_lives(probe, "BS-D")
        assert lives[50.0] / ref.dnv_d_cycles(50.0) == pytest.approx(1.626, rel=2e-2)

    def test_sn_curve_is_discontinuous_at_the_knee(self, probe):
        lives = self._served_lives(probe, "BS-D")
        c3 = lives[80.0] * 80.0 ** 3
        c5 = lives[40.0] * 40.0 ** 5
        s_switch = (c3 / 1e7) ** (1 / 3)
        jump = (c5 / s_switch ** 5) / 1e7
        # a valid S-N curve is continuous; here the life jumps by >60% at the knee
        assert jump == pytest.approx(1.62, rel=0.02)

    def test_miner_summation_is_internally_consistent(self, probe):
        lives = self._served_lives(probe, "BS-D")
        d = sum(n / lives[s] for s, n in ((50.0, 1e6), (80.0, 5e5), (100.0, 1e5)))
        assert d == pytest.approx(0.291661, rel=1e-4)


class TestAuditLedger:
    """The audit output itself must stay well formed."""

    def test_all_checks_have_verdicts(self, audit):
        ids = [c["id"] for c in audit["checks"]]
        assert len(ids) == len(set(ids)), "duplicate check ids"
        for c in audit["checks"]:
            assert c["verdict"] in ("PASS", "FAIL")
            if c["verdict"] == "FAIL" and c["reference"] not in (None,):
                assert c["rel_err_pct"] is not None

    def test_failures_exceed_passes_only_because_debt_is_open(self, audit):
        s = audit["summary"]
        assert s["plate-stability"]["n"] == 9
        assert s["fatigue"]["n"] == 7
        assert sum(v["fail"] for v in s.values()) >= 15

    def test_correct_formulas_are_recorded_as_pass(self, audit):
        by_id = {c["id"]: c for c in audit["checks"]}
        # things that ARE right must be recorded as right (guard against a blanket FAIL)
        for cid in ("P1", "P6", "F6", "S1", "S3"):
            assert by_id[cid]["verdict"] == "PASS", cid

    def test_figures_exist(self, audit):
        for rel in audit["figures"]:
            assert os.path.exists(os.path.join(ROOT, rel)), rel