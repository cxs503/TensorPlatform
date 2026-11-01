"""Offline unit tests for the resistance validation-debt baselines.

These tests are deliberately independent of the platform server: they pin the
*reference mathematics* used to judge the platform, so a regression in the audit
harness itself is caught before it can produce a false verdict.

Run:
    python3 -m pytest tools/validation_debt/tests -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
VD = HERE.parent
ROOT = VD.parents[1]
sys.path.insert(0, str(VD))

import baselines as B  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Kinematics / coefficient baselines (closed-form ground truth)
# ---------------------------------------------------------------------------
class TestBaselines:
    def test_knot_conversion_roundtrip(self):
        assert B.knots_to_ms(1.0) == pytest.approx(0.514444)
        assert B.ms_to_knots(B.knots_to_ms(7.2)) == pytest.approx(7.2, rel=1e-12)

    def test_froude_and_reynolds_closed_form(self):
        # SUBOFF: U = V_kn * 0.514444 with V_kn = 3.0 -> U = 1.5433 m/s, L = 4.356 m
        u = B.knots_to_ms(3.0)
        assert B.froude(u, 4.356) == pytest.approx(u / (9.81 * 4.356) ** 0.5, rel=1e-12)
        assert B.froude(u, 4.356) == pytest.approx(0.23609, abs=5e-5)
        # platform default viscosity 1.19e-6 -> Re must reproduce the deployed value
        assert B.reynolds(u, 4.356, 1.19e-6) == pytest.approx(5.6489e6, rel=1e-4)

    @pytest.mark.parametrize("re_,cf", [
        (1e6, 0.075 / 16.0),      # (log10 - 2)^2 = 16  -> 4.6875e-3
        (1e7, 0.075 / 25.0),      # -> 3.0e-3
        (1e8, 0.075 / 36.0),      # -> 2.0833e-3
    ])
    def test_ittc57_cf_closed_form(self, re_, cf):
        assert B.ittc57_cf(re_) == pytest.approx(cf, rel=1e-12)

    def test_ittc57_rejects_nonpositive_re(self):
        with pytest.raises(ValueError):
            B.ittc57_cf(0.0)

    def test_holtrop_wetted_surface_reproduces_platform_implementation(self):
        # The deployed endpoint reports 4.72 m^2 for L=4.356, B=T=0.508, Cb=0.508.
        s = B.holtrop_wetted_surface(4.356, 0.508, 0.508, 0.508)
        assert s == pytest.approx(4.72, abs=0.01)
        # and it is 12.9% below the cited SUBOFF reference area
        assert abs(s - 5.42) / 5.42 == pytest.approx(0.1299, abs=5e-4)

    def test_submerged_baseline_scales_linearly_with_form_factor(self):
        cf = 0.003
        assert B.submerged_ct_baseline(cf, 0.0) == pytest.approx(cf)
        assert B.submerged_ct_baseline(cf, 0.10) == pytest.approx(1.10 * cf)


# ---------------------------------------------------------------------------
# 2. Verdict logic (must be able to say "pass" as well as "fail")
# ---------------------------------------------------------------------------
class TestChecks:
    def test_arithmetic_identity_accepts_consistent_output(self):
        out = {"cf": 0.0030, "cr": 0.0010, "ct": 0.0044}
        assert B.check_arithmetic_identity(out)["passed"] is True

    def test_arithmetic_identity_rejects_inconsistent_output(self):
        out = {"cf": 0.0030, "cr": 0.0010, "ct": 0.0050}
        r = B.check_arithmetic_identity(out)
        assert r["passed"] is False and r["rel_error"] > 0.1

    def test_surface_response_tolerates_quantisation_at_small_magnitude(self):
        # 0.0556 kN reported as 0.06 kN must NOT be flagged (rounding, not a bug)
        out = {"ct": 0.010211, "rt": 0.06, "pe": 0.08}
        r = B.check_surface_response(out, 1.5, B.RHO_DEFAULT, 4.72,
                                     tol_rt=0.01 / 0.06, tol_pe=0.01 / 0.08)
        assert r["passed"] is True

    def test_surface_response_detects_plumbing_error(self):
        out = {"ct": 0.010211, "rt": 0.60, "pe": 0.90}   # 10x too large
        r = B.check_surface_response(out, 1.5, B.RHO_DEFAULT, 4.72, 0.02, 0.02)
        assert r["passed"] is False

    def test_submerged_wave_term_ceiling(self):
        ok = B.check_submerged_wave_resistance("SUBOFF", {"cf": 0.003, "cr": 0.0003})
        bad = B.check_submerged_wave_resistance("SUBOFF", {"cf": 0.003, "cr": 0.014})
        assert ok["wave_component_plausible"] is True
        assert bad["wave_component_plausible"] is False
        assert "CATEGORY ERROR" in bad["verdict"]
        assert bad["cr_cf_ratio"] == pytest.approx(4.667, abs=1e-3)

    def test_wetted_surface_criterion(self):
        assert B.check_wetted_surface(4.72, 4.72)["passed"] is True
        r = B.check_wetted_surface(4.72, 5.42)
        assert r["passed"] is False and r["rel_error"] == pytest.approx(0.1292, abs=5e-4)

    def test_validity_domain_flags_fine_form(self):
        v = B.check_inputs_physically_valid("SUBOFF", 0.508, 0.236)
        assert v["cb_in_holtrop_domain"] is False
        assert v["fn_in_holtrop_domain"] is True
        assert v["holtrop_applicable"] is False
        ok = B.check_inputs_physically_valid("KVLCC2", 0.8098, 0.18)
        assert ok["holtrop_applicable"] is True


# ---------------------------------------------------------------------------
# 3. Benchmark-table integrity (the repo's own reference data)
# ---------------------------------------------------------------------------
class TestBenchmarkIntegrity:
    @pytest.fixture(scope="class")
    def cases(self):
        path = ROOT / "backend" / "data" / "benchmark_reference.json"
        return json.loads(path.read_text())["cases"]

    def test_consistent_synthetic_case_passes(self):
        case = {"case_id": "SYN-01", "ship_type": "SYN", "speed_ms": 2.0,
                "experimental_ct": 0.004, "experimental_rt_n": 43.43,
                "geometry": {"wetted_surface_m2": 5.42}}
        # Rt implied = 0.5*1025*5.42*4*0.004 = 44.44 N -> 2.3% from 43.43 N
        assert B.case_internal_consistency(case)["consistent"] is True

    def test_contradictory_synthetic_case_fails(self):
        case = {"case_id": "SYN-02", "ship_type": "SYN", "speed_ms": 2.0,
                "experimental_ct": 0.004, "experimental_rt_n": 250.0,
                "geometry": {"wetted_surface_m2": 5.42}}
        r = B.case_internal_consistency(case)
        assert r["consistent"] is False and r["rel_error"] > 0.5

    def test_case_without_wetted_area_is_declared_unchecked(self):
        case = {"case_id": "SYN-03", "ship_type": "SYN", "speed_ms": 2.0,
                "experimental_ct": 0.004, "experimental_rt_n": 10.0, "geometry": {}}
        assert B.case_internal_consistency(case)["checked"] is False

    def test_known_defect_suboff_reference_is_self_contradictory(self, cases):
        """KNOWN DEFECT (audit finding F7, 2026-11-01).

        SUBOFF-01 states Ct = 0.0038 and Rt = 12.5 N at 3 m/s with S = 5.42 m^2;
        the two are inconsistent by ~7x.  If the reference table is ever repaired
        this test must be updated together with docs/validation_debt/.
        """
        suboff01 = next(c for c in cases if c["case_id"] == "SUBOFF-01")
        r = B.case_internal_consistency(suboff01)
        assert r["checked"] is True
        assert r["consistent"] is False
        assert r["ratio_reported_over_implied"] == pytest.approx(0.134, abs=0.01)

    def test_known_defect_suboff_ct_rises_with_speed(self, cases):
        """Ct must fall with speed for a friction-dominated submerged body."""
        mono = B.dataset_monotonicity(cases, "SUBOFF")
        assert mono["monotonically_decreasing"] is False
        assert mono["ct"] == sorted(mono["ct"])

    def test_datmtb_series_is_monotonic_with_a_design_speed_bump(self, cases):
        """Surface hull: Ct falls with speed up to the wave hump, then rises -
        the dataset reproduces the expected non-monotone surface-ship trend."""
        mono = B.dataset_monotonicity(cases, "DTMB-5415")
        assert mono["monotonically_decreasing"] is False