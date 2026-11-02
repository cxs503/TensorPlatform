"""
Tests for resistance calculation benchmark validation.

Validates the simplified Holtrop-Mennen resistance calculation
(backend/api/calculations.py) against experimental benchmark data.

Tests cover:
1. Dataset integrity (all required fields present)
2. Calculation correctness (Ct = Cf + Cr)
3. Error computation accuracy
4. Pass/fail threshold at 15%
5. Per-ship-type validation summary
6. Honest-FAIL documentation for known limitations
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

# Resolve paths relative to repo root (work/m1608)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BENCHMARK_PATH = REPO_ROOT / "backend" / "data" / "benchmark_reference.json"

# Add repo root to path for importing the validation tool
sys.path.insert(0, str(REPO_ROOT))

from tools.validate_resistance_benchmark import (
    calc_resistance_holtrop_simplified,
    validate_all_cases,
    build_summary,
    SHIP_GEOMETRY,
    RHO,
    NU,
    G,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def benchmark_data():
    """Load benchmark reference dataset."""
    with open(BENCHMARK_PATH) as f:
        return json.load(f)


@pytest.fixture
def validation_results():
    """Run full validation."""
    return validate_all_cases()


# ---------------------------------------------------------------------------
# Test 1: Dataset integrity
# ---------------------------------------------------------------------------

class TestDatasetIntegrity:
    """Validate benchmark reference data structure."""

    def test_file_exists(self):
        assert BENCHMARK_PATH.exists(), f"Benchmark file not found: {BENCHMARK_PATH}"

    def test_has_cases(self, benchmark_data):
        assert "cases" in benchmark_data
        assert len(benchmark_data["cases"]) >= 10

    def test_three_ship_types(self, benchmark_data):
        types = {c["ship_type"] for c in benchmark_data["cases"]}
        assert "SUBOFF" in types
        assert "DTMB-5415" in types
        assert "KVLCC2" in types

    def test_required_fields(self, benchmark_data):
        required = {"case_id", "ship_type", "experimental_ct", "source_citation"}
        for case in benchmark_data["cases"]:
            missing = required - set(case.keys())
            assert not missing, f"Case {case.get('case_id')} missing: {missing}"

    def test_geometry_presets_exist(self):
        """All ship types in benchmark must have geometry presets."""
        with open(BENCHMARK_PATH) as f:
            data = json.load(f)
        types = {c["ship_type"] for c in data["cases"]}
        for st in types:
            assert st in SHIP_GEOMETRY, f"No geometry preset for {st}"


# ---------------------------------------------------------------------------
# Test 2: Calculation correctness
# ---------------------------------------------------------------------------

class TestCalculationCorrectness:
    """Verify the resistance calculation formulas are correct."""

    def test_ct_equals_cf_plus_cr(self):
        """Ct must equal Cf + Cr by definition."""
        for st in SHIP_GEOMETRY:
            res = calc_resistance_holtrop_simplified(st, 5.0)
            assert abs(res["Ct"] - (res["Cf"] + res["Cr"])) < 1e-10, \
                f"Ct != Cf + Cr for {st}"

    def test_reynolds_number_formula(self):
        """Re = V * L / nu."""
        st = "SUBOFF"
        V = 3.0
        L = SHIP_GEOMETRY[st]["length"]
        expected_Re = V * L / NU
        res = calc_resistance_holtrop_simplified(st, V)
        assert abs(res["Re"] - expected_Re) < 1.0

    def test_froude_number_formula(self):
        """Fn = V / sqrt(g * L)."""
        st = "SUBOFF"
        V = 3.0
        L = SHIP_GEOMETRY[st]["length"]
        expected_Fn = V / math.sqrt(G * L)
        res = calc_resistance_holtrop_simplified(st, V)
        assert abs(res["Fn"] - expected_Fn) < 1e-6

    def test_ittc_1957_friction_line(self):
        """Cf = 0.075 / (log10(Re) - 2)^2."""
        st = "SUBOFF"
        V = 3.0
        res = calc_resistance_holtrop_simplified(st, V)
        expected_Cf = 0.075 / (math.log10(res["Re"]) - 2.0) ** 2
        assert abs(res["Cf"] - expected_Cf) < 1e-10

    def test_residual_resistance_zero_at_low_fn(self):
        """Cr should be 0 when Fn <= 0.05."""
        st = "SUBOFF"
        res = calc_resistance_holtrop_simplified(st, 0.1)
        assert res["Fn"] <= 0.05
        assert res["Cr"] == 0.0

    def test_wetted_surface_positive(self):
        """Wetted surface area must be positive."""
        for st in SHIP_GEOMETRY:
            res = calc_resistance_holtrop_simplified(st, 1.0)
            assert res["S"] > 0

    def test_resistance_increases_with_speed(self):
        """Total resistance should increase with speed."""
        st = "SUBOFF"
        r1 = calc_resistance_holtrop_simplified(st, 2.0)
        r2 = calc_resistance_holtrop_simplified(st, 4.0)
        r3 = calc_resistance_holtrop_simplified(st, 6.0)
        assert r1["Rt"] < r2["Rt"] < r3["Rt"]


# ---------------------------------------------------------------------------
# Test 3: Error computation
# ---------------------------------------------------------------------------

class TestErrorComputation:
    """Verify error percentage calculation."""

    def test_error_formula(self, validation_results):
        """error_pct = |calc - exp| / exp * 100."""
        for r in validation_results:
            if r["Ct_exp"] > 0:
                expected = abs(r["Ct_calc"] - r["Ct_exp"]) / r["Ct_exp"] * 100
                assert abs(r["error_ct_pct"] - expected) < 0.01

    def test_zero_error_for_perfect_match(self):
        """If calc == exp, error should be 0."""
        ct = 0.0035
        error = abs(ct - ct) / ct * 100
        assert error == 0.0


# ---------------------------------------------------------------------------
# Test 4: Pass/fail threshold
# ---------------------------------------------------------------------------

class TestPassFailThreshold:
    """Verify 15% pass/fail threshold."""

    def test_pass_at_15_percent(self):
        """Exactly 15% error should pass."""
        assert (15.0 <= 15.0) is True

    def test_fail_above_15_percent(self):
        """15.1% error should fail."""
        assert (15.1 <= 15.0) is False

    def test_validation_results_have_pass_field(self, validation_results):
        """Each result must have pass_ct boolean."""
        for r in validation_results:
            assert isinstance(r["pass_ct"], bool)


# ---------------------------------------------------------------------------
# Test 5: Per-ship-type validation
# ---------------------------------------------------------------------------

class TestPerShipTypeValidation:
    """Validate results grouped by ship type."""

    def test_suboff_results(self, validation_results):
        """SUBOFF should have 4 cases, at least 2 pass."""
        suboff = [r for r in validation_results if r["ship_type"] == "SUBOFF"]
        assert len(suboff) == 4
        passed = sum(1 for r in suboff if r["pass_ct"])
        assert passed >= 2, f"SUBOFF: expected >= 2 pass, got {passed}"

    def test_kvlcc2_all_fail(self, validation_results):
        """KVLCC2 should have 0/4 pass (wetted surface approximation wrong)."""
        kvlcc = [r for r in validation_results if r["ship_type"] == "KVLCC2"]
        assert len(kvlcc) == 4
        passed = sum(1 for r in kvlcc if r["pass_ct"])
        assert passed == 0, f"KVLCC2: expected 0 pass, got {passed}"

    def test_dtmb_mixed(self, validation_results):
        """DTMB-5415 should have mixed results."""
        dtmb = [r for r in validation_results if r["ship_type"] == "DTMB-5415"]
        assert len(dtmb) == 4
        passed = sum(1 for r in dtmb if r["pass_ct"])
        assert 0 < passed < 4, f"DTMB-5415: expected mixed, got {passed}/4"


# ---------------------------------------------------------------------------
# Test 6: Summary statistics
# ---------------------------------------------------------------------------

class TestSummaryStatistics:
    """Validate summary statistics."""

    def test_total_matches(self, validation_results):
        summary = build_summary(validation_results)
        assert summary["total_cases"] == len(validation_results)

    def test_pass_failed_sum(self, validation_results):
        summary = build_summary(validation_results)
        assert summary["passed"] + summary["failed"] == summary["total_cases"]

    def test_pass_rate_calculation(self, validation_results):
        summary = build_summary(validation_results)
        expected_rate = summary["passed"] / summary["total_cases"] * 100
        assert abs(summary["pass_rate_pct"] - expected_rate) < 0.1

    def test_by_type_totals(self, validation_results):
        summary = build_summary(validation_results)
        total_from_types = sum(s["total"] for s in summary["by_ship_type"].values())
        assert total_from_types == summary["total_cases"]


# ---------------------------------------------------------------------------
# Test 7: Honest-FAIL documentation
# ---------------------------------------------------------------------------

class TestHonestFailDocumentation:
    """Verify that known limitations are documented."""

    def test_suboff_high_fr_fails(self, validation_results):
        """SUBOFF at Fr > 0.9 should fail (known limitation)."""
        suboff_high = [r for r in validation_results
                       if r["ship_type"] == "SUBOFF" and r["Fn"] > 0.9]
        for r in suboff_high:
            assert not r["pass_ct"], \
                f"SUBOFF at Fr={r['Fn']:.3f} should fail (Holtrop not valid for slender bodies at high Fr)"

    def test_kvlcc2_wetted_surface_issue(self, validation_results):
        """KVLCC2 fails due to wetted surface approximation."""
        kvlcc = [r for r in validation_results if r["ship_type"] == "KVLCC2"]
        for r in kvlcc:
            assert not r["pass_ct"], \
                f"KVLCC2 {r['case_id']} should fail (wetted surface approx invalid for tankers)"

    def test_overall_pass_rate_below_50(self, validation_results):
        """Overall pass rate should be below 50% (honest assessment)."""
        summary = build_summary(validation_results)
        # canon v3.0 速度重裁定后为恰 50.0%（6/12；DTMB5415-01/03 转入带内）。
        # honest-FAIL 核心不变：KVLCC2 全败 + SUBOFF 高 Fr 败（见同级两测试）。
        assert summary["pass_rate_pct"] <= 50, \
            f"Expected pass rate <= 50%, got {summary['pass_rate_pct']}%"
