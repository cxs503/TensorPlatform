#!/usr/bin/env python3
"""
Tests for ship resistance benchmark validation.

Tests the standalone validation script logic:
1. Resistance coefficient calculation matches expected formulas
2. Benchmark comparison produces correct error percentages
3. Pass/fail logic at 15% threshold
4. Honest-FAIL detection for SUBOFF at Fr>0.4
5. KVLCC2 systematic underprediction (known limitation)

Usage:
    pytest backend/tests/test_resistance_benchmark.py -v
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Add tools to path
TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from validate_resistance_benchmark import (
    calc_resistance_coefficient,
    run_validation,
    BENCHMARK_CASES,
)


# ---------------------------------------------------------------------------
# Test 1: Resistance coefficient calculation
# ---------------------------------------------------------------------------

class TestResistanceCoefficientCalculation:
    """Verify the resistance coefficient calculation matches ITTC-57 + Holtrop formulas."""

    def test_ittc57_friction_line(self):
        """ITTC-1957 friction line: Cf = 0.075/(log10(Re)-2)^2."""
        # At Re=1e7, Cf should be ~0.0029
        result = calc_resistance_coefficient(
            speed_ms=10.0, length=100.0, beam=15.0, draft=5.0, cb=0.6,
            nu=1.19e-6
        )
        Re = 10.0 * 100.0 / 1.19e-6  # ~8.4e8
        expected_cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        assert abs(result["Cf"] - expected_cf) < 1e-8

    def test_froude_number(self):
        """Fn = V / sqrt(g*L)."""
        result = calc_resistance_coefficient(
            speed_ms=3.0, length=4.356, beam=0.508, draft=0.508, cb=0.508
        )
        expected_fn = 3.0 / math.sqrt(9.81 * 4.356)
        assert abs(result["Fn"] - expected_fn) < 1e-6

    def test_wetted_surface_axisymmetric(self):
        """S = π·B·L·(0.5 + 0.25·Cb) for axisymmetric bodies."""
        result = calc_resistance_coefficient(
            speed_ms=3.0, length=4.356, beam=0.508, draft=0.508, cb=0.508
        )
        expected_S = math.pi * 0.508 * 4.356 * (0.5 + 0.25 * 0.508)
        assert abs(result["S"] - expected_S) < 1e-6

    def test_ct_equals_cf_plus_cr(self):
        """Ct = Cf + Cr by definition."""
        result = calc_resistance_coefficient(
            speed_ms=5.0, length=4.356, beam=0.508, draft=0.508, cb=0.508
        )
        assert abs(result["Ct"] - (result["Cf"] + result["Cr"])) < 1e-10

    def test_cr_zero_at_low_fn(self):
        """Cr should be zero when Fn <= 0.05."""
        result = calc_resistance_coefficient(
            speed_ms=0.1, length=100.0, beam=15.0, draft=5.0, cb=0.6
        )
        assert result["Fn"] <= 0.05
        assert result["Cr"] == 0.0


# ---------------------------------------------------------------------------
# Test 2: Benchmark validation results
# ---------------------------------------------------------------------------

class TestBenchmarkValidation:
    """Verify benchmark validation produces correct results."""

    def test_all_cases_processed(self):
        """All 12 benchmark cases should be processed."""
        results = run_validation()
        assert len(results) == 12

    def test_suboff_results(self):
        """SUBOFF cases should have specific error patterns."""
        results = run_validation([c for c in BENCHMARK_CASES if c["ship_type"] == "SUBOFF"])
        assert len(results) == 4
        # SUBOFF-02 should be closest (2.2% error)
        suboff_02 = [r for r in results if r["case_id"] == "SUBOFF-02"][0]
        assert suboff_02["error_pct"] < 5.0
        assert suboff_02["pass"] is True

    def test_kvlcc2_systematic_underprediction(self):
        """KVLCC2 should show systematic ~50% underprediction (known limitation)."""
        results = run_validation([c for c in BENCHMARK_CASES if c["ship_type"] == "KVLCC2"])
        assert len(results) == 4
        for r in results:
            assert r["error_pct"] > 40.0, f"KVLCC2 {r['case_id']} error too low: {r['error_pct']}"
            assert r["pass"] is False

    def test_dtmb5415_high_fn_blowup(self):
        """DTMB-5415 at high Fn should show massive error (Cr formula issue)."""
        results = run_validation([c for c in BENCHMARK_CASES if c["ship_type"] == "DTMB-5415"])
        dtmb_04 = [r for r in results if r["case_id"] == "DTMB5415-04"][0]
        assert dtmb_04["error_pct"] > 500.0, "DTMB-5415 at Fn=0.366 should have >500% error"


# ---------------------------------------------------------------------------
# Test 3: Pass/fail logic
# ---------------------------------------------------------------------------

class TestPassFailLogic:
    """Verify pass/fail threshold at 15%."""

    def test_pass_below_15(self):
        """Error < 15% should pass."""
        results = run_validation()
        passing = [r for r in results if r["pass"]]
        for r in passing:
            assert r["error_pct"] <= 15.0

    def test_fail_above_15(self):
        """Error > 15% should fail."""
        results = run_validation()
        failing = [r for r in results if not r["pass"]]
        for r in failing:
            assert r["error_pct"] > 15.0 or r["honest_fail"]

    def test_honest_fail_detection(self):
        """SUBOFF at Fr>0.4 should be flagged as honest-FAIL."""
        results = run_validation([c for c in BENCHMARK_CASES if c["ship_type"] == "SUBOFF"])
        for r in results:
            assert r["honest_fail"] is True, f"{r['case_id']} should be honest-FAIL"
            assert "HONEST-FAIL" in r["note"]


# ---------------------------------------------------------------------------
# Test 4: Summary statistics
# ---------------------------------------------------------------------------

class TestSummaryStatistics:
    """Verify summary statistics are correct."""

    def test_pass_rate(self):
        """Overall pass rate should be ~33%."""
        results = run_validation()
        passed = sum(1 for r in results if r["pass"])
        total = len(results)
        pass_rate = passed / total * 100
        assert 30.0 <= pass_rate <= 40.0, f"Pass rate {pass_rate:.1f}% outside expected range"

    def test_honest_fail_count(self):
        """Should have 4 honest-FAIL cases (all SUBOFF)."""
        results = run_validation()
        honest_fails = sum(1 for r in results if r["honest_fail"])
        assert honest_fails == 4
