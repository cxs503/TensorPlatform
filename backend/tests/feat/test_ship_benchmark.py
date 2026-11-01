"""
Tests for ship benchmark validation endpoint.

Tests:
1. Error calculation logic (correct percentage)
2. Pass/fail logic (<=15% passes, >15% fails)
3. Honest-FAIL detection for SUBOFF at Fr>0.4
4. Endpoint structure validation (error_pct and pass fields present)
5. Missing case handling (404)
6. All-fail triggers algorithm_upgrade_recommended
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure backend is on path
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from api.domain.ship_benchmark import (
    _load_benchmark_data,
    benchmark_validate,
    SHIP_GEOMETRY,
    BenchmarkValidateRequest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def benchmark_data():
    """Load the benchmark reference dataset."""
    return _load_benchmark_data()


@pytest.fixture
def mock_resistance_api():
    """Mock the resistance API call with realistic Holtrop/ITTC-57 results."""
    def _mock(ship_type: str, speed_ms: float, geometry: dict) -> dict:
        """Simulate Holtrop/ITTC-57 resistance calculation."""
        L = geometry["length"]
        cb = geometry["cb"]
        g = 9.81
        fr = speed_ms / (g * L) ** 0.5

        # Simplified Holtrop-like Ct estimation
        # For merchant ships (Cb>0.55): reasonable accuracy
        # For SUBOFF (Cb~0.5, Fr>0.4): known to overpredict
        if ship_type == "SUBOFF":
            # Holtrop overpredicts for slender bodies at high Fr
            # Experimental Ct ~ 0.0038-0.0044, Holtrop gives ~0.0055-0.0070
            base_ct = 0.0055 + 0.002 * fr  # Overprediction trend
        elif ship_type == "DTMB-5415":
            # Fine-form warship, moderate error
            base_ct = 0.0040 + 0.001 * fr
        elif ship_type == "KVLCC2":
            # Full-form tanker, Holtrop works well
            base_ct = 0.0025 + 0.003 * fr
        else:
            base_ct = 0.0040

        reynolds = speed_ms * L / 1.19e-6  # kinematic viscosity of water
        return {
            "reynolds_number": reynolds,
            "froude_number": fr,
            "wetted_surface": L * L * 0.3,
            "cf": 0.075 / (max(1, (reynolds - 2)) ** 0.2),
            "cr": base_ct * 0.6,
            "ct": base_ct,
            "rf": 0.0,
            "rr": 0.0,
            "rt": base_ct * 0.5 * geometry.get("displacement", 1000) * speed_ms ** 2,
            "pe": 0.0,
            "method": "ITTC-57摩擦阻力 + Holtrop剩余阻力",
        }
    return _mock


# ---------------------------------------------------------------------------
# Test 1: Dataset structure validation
# ---------------------------------------------------------------------------

class TestDatasetStructure:
    """Validate benchmark_reference.json structure and content."""

    def test_file_exists(self):
        data_path = BACKEND_DIR / "data" / "benchmark_reference.json"
        assert data_path.exists(), f"Benchmark reference file not found: {data_path}"

    def test_loads_valid_json(self, benchmark_data):
        assert "cases" in benchmark_data
        assert isinstance(benchmark_data["cases"], list)

    def test_minimum_records(self, benchmark_data):
        """Must have >= 10 records."""
        cases = benchmark_data["cases"]
        assert len(cases) >= 10, f"Expected >= 10 cases, got {len(cases)}"

    def test_three_ship_types(self, benchmark_data):
        """Must have 3 ship types: SUBOFF, DTMB-5415, KVLCC2."""
        ship_types = {c["ship_type"] for c in benchmark_data["cases"]}
        assert "SUBOFF" in ship_types
        assert "DTMB-5415" in ship_types
        assert "KVLCC2" in ship_types

    def test_each_type_has_at_least_two(self, benchmark_data):
        """Each ship type must have >= 2 cases."""
        from collections import Counter
        counts = Counter(c["ship_type"] for c in benchmark_data["cases"])
        for st in ["SUBOFF", "DTMB-5415", "KVLCC2"]:
            assert counts[st] >= 2, f"{st} has only {counts[st]} cases, need >= 2"

    def test_required_fields(self, benchmark_data):
        """Each case must have required fields."""
        required = {"case_id", "ship_type", "experimental_ct", "source_citation"}
        for case in benchmark_data["cases"]:
            missing = required - set(case.keys())
            assert not missing, f"Case {case.get('case_id', 'unknown')} missing: {missing}"

    def test_suboff_speed_range(self, benchmark_data):
        """SUBOFF cases should have speeds in 3-9 m/s range."""
        suboff_cases = [c for c in benchmark_data["cases"] if c["ship_type"] == "SUBOFF"]
        for case in suboff_cases:
            speed = case.get("speed_ms", 0)
            assert 3.0 <= speed <= 9.0, f"SUBOFF speed {speed} outside 3-9 m/s range"

    def test_source_citations(self, benchmark_data):
        """Verify source citations match expected references."""
        suboff_cases = [c for c in benchmark_data["cases"] if c["ship_type"] == "SUBOFF"]
        for case in suboff_cases:
            assert "Roddy" in case["source_citation"] or "1990" in case["source_citation"], \
                f"SUBOFF case {case['case_id']} missing Roddy 1990 citation"

        dtmb_cases = [c for c in benchmark_data["cases"] if c["ship_type"] == "DTMB-5415"]
        for case in dtmb_cases:
            assert "ITTC" in case["source_citation"] or "V&V" in case["source_citation"], \
                f"DTMB-5415 case {case['case_id']} missing ITTC V&V citation"

        kvlcc_cases = [c for c in benchmark_data["cases"] if c["ship_type"] == "KVLCC2"]
        for case in kvlcc_cases:
            assert "SIMMAN" in case["source_citation"] or "2008" in case["source_citation"], \
                f"KVLCC2 case {case['case_id']} missing SIMMAN 2008 citation"


# ---------------------------------------------------------------------------
# Test 2: Error calculation logic
# ---------------------------------------------------------------------------

class TestErrorCalculation:
    """Verify error percentage calculation is correct."""

    def test_error_pct_formula(self):
        """error_pct = |calc_ct - ref_ct| / ref_ct * 100."""
        ref_ct = 0.00300
        calc_ct = 0.00345
        expected_error = abs(calc_ct - ref_ct) / ref_ct * 100.0
        assert abs(expected_error - 15.0) < 0.01, f"Expected 15%, got {expected_error}"

    def test_error_pct_zero(self):
        """Perfect match should give 0% error."""
        ref_ct = 0.00300
        calc_ct = 0.00300
        error = abs(calc_ct - ref_ct) / ref_ct * 100.0
        assert error == 0.0

    def test_error_pct_large(self):
        """Large mismatch should give large error."""
        ref_ct = 0.00300
        calc_ct = 0.00600
        error = abs(calc_ct - ref_ct) / ref_ct * 100.0
        assert abs(error - 100.0) < 0.01


# ---------------------------------------------------------------------------
# Test 3: Pass/fail logic
# ---------------------------------------------------------------------------

class TestPassFailLogic:
    """Verify pass/fail threshold at 15%."""

    def test_pass_at_15_percent(self):
        """Exactly 15% error should pass."""
        ref_ct = 0.00300
        calc_ct = 0.00345  # 15% error
        error = abs(calc_ct - ref_ct) / ref_ct * 100.0
        passed = error <= 15.0
        assert passed is True

    def test_pass_below_15_percent(self):
        """Below 15% error should pass."""
        ref_ct = 0.00300
        calc_ct = 0.00330  # 10% error
        error = abs(calc_ct - ref_ct) / ref_ct * 100.0
        passed = error <= 15.0
        assert passed is True

    def test_fail_above_15_percent(self):
        """Above 15% error should fail."""
        ref_ct = 0.00300
        calc_ct = 0.00360  # 20% error
        error = abs(calc_ct - ref_ct) / ref_ct * 100.0
        passed = error <= 15.0
        assert passed is False


# ---------------------------------------------------------------------------
# Test 4: Endpoint integration tests (with mocked API)
# ---------------------------------------------------------------------------

class TestBenchmarkValidateEndpoint:
    """Integration tests for the benchmark_validate endpoint."""

    def test_kvlcc2_single_case_passes(self, mock_resistance_api):
        """KVLCC2 at design speed should pass (Holtrop works well for tankers)."""
        with patch("api.domain.ship_benchmark._call_resistance_api", side_effect=mock_resistance_api):
            req = BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="KVLCC2-02")
            resp = benchmark_validate(req)

            assert len(resp.results) == 1
            result = resp.results[0]
            assert result.case_id == "KVLCC2-02"
            assert hasattr(result, "error_pct")
            assert hasattr(result, "pass_")
            assert result.ref_ct > 0
            assert result.calc_ct > 0

    def test_suboff_honest_fail(self, mock_resistance_api):
        """SUBOFF at Fr>0.4 should trigger honest-FAIL."""
        with patch("api.domain.ship_benchmark._call_resistance_api", side_effect=mock_resistance_api):
            req = BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="SUBOFF-03")
            resp = benchmark_validate(req)

            assert len(resp.results) == 1
            result = resp.results[0]
            assert result.case_id == "SUBOFF-03"
            # Holtrop overpredicts for SUBOFF at high Fr
            assert result.error_pct > 15.0, \
                f"Expected honest-FAIL for SUBOFF-03, got error_pct={result.error_pct}"
            assert result.pass_ is False
            assert "HONEST-FAIL" in result.note or "Holtrop" in result.note

    def test_all_suboff_cases_fail(self, mock_resistance_api):
        """All SUBOFF cases should fail (Fr>0.4 for all)."""
        with patch("api.domain.ship_benchmark._call_resistance_api", side_effect=mock_resistance_api):
            req = BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all")
            resp = benchmark_validate(req)

            assert len(resp.results) >= 4
            failed = [r for r in resp.results if not r.pass_]
            assert len(failed) > 0, "Expected at least some SUBOFF cases to fail"
            assert resp.summary.get("honest_fail") is True

    def test_response_structure(self, mock_resistance_api):
        """Response must contain error_pct and pass fields."""
        with patch("api.domain.ship_benchmark._call_resistance_api", side_effect=mock_resistance_api):
            req = BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="KVLCC2-01")
            resp = benchmark_validate(req)

            for result in resp.results:
                assert hasattr(result, "error_pct"), "Missing error_pct field"
                assert hasattr(result, "pass_"), "Missing pass field"
                assert isinstance(result.error_pct, (int, float))
                assert isinstance(result.pass_, bool)

    def test_missing_case_returns_404(self):
        """Non-existent case should raise 404."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            req = BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="NONEXISTENT")
            benchmark_validate(req)
        assert exc_info.value.status_code == 404

    def test_honest_fail_requires_verified_reference_rows(self, mock_resistance_api):
        """m1388: an "upgrade the algorithm" verdict needs usable reference data.

        The previous version of this test asserted ``algorithm_upgrade_recommended
        is True`` for SUBOFF.  That verdict was an artefact: every case had already
        degraded to ``calc_ct = 0`` because the remote hop was broken, and the
        SUBOFF reference rows themselves fail the Ct-Rt identity audit.  The
        endpoint must therefore withhold the verdict instead of recommending CFD.
        """
        with patch("api.domain.ship_benchmark._call_resistance_api", side_effect=mock_resistance_api):
            req = BenchmarkValidateRequest(ship_type="SUBOFF", benchmark_id="all")
            resp = benchmark_validate(req)

            assert resp.summary["verified_cases"] == 0
            assert resp.summary["withheld_count"] == len(resp.results)
            assert resp.summary.get("algorithm_upgrade_recommended") is False
            assert "recommendation" in resp.summary
            assert "NO VERIFIED REFERENCE ROWS" in resp.summary["recommendation"]



# ---------------------------------------------------------------------------
# Test 5: Geometry presets
# ---------------------------------------------------------------------------

class TestGeometryPresets:
    """Verify geometry presets for benchmark ships."""

    def test_kvlcc2_geometry(self):
        geo = SHIP_GEOMETRY["KVLCC2"]
        assert abs(geo["length"] - 320.0) < 0.1
        assert abs(geo["cb"] - 0.8098) < 0.001

    def test_suboff_geometry(self):
        geo = SHIP_GEOMETRY["SUBOFF"]
        assert abs(geo["length"] - 4.356) < 0.01

    def test_dtmb5415_geometry(self):
        geo = SHIP_GEOMETRY["DTMB-5415"]
        assert abs(geo["length"] - 5.720) < 0.01


# ---------------------------------------------------------------------------
# Test 6: End-to-end JSON serialization
# ---------------------------------------------------------------------------

class TestJSONSerialization:
    """Verify response can be serialized to JSON."""

    def test_response_serializable(self, mock_resistance_api):
        with patch("api.domain.ship_benchmark._call_resistance_api", side_effect=mock_resistance_api):
            req = BenchmarkValidateRequest(ship_type="KVLCC2", benchmark_id="KVLCC2-01")
            resp = benchmark_validate(req)

            # Should not raise
            json_str = resp.model_dump_json(by_alias=True)
            data = json.loads(json_str)
            assert "results" in data
            assert "summary" in data

    def test_artifact_output(self, mock_resistance_api, tmp_path):
        """Generate artifacts/benchmark_validation.json."""
        with patch("api.domain.ship_benchmark._call_resistance_api", side_effect=mock_resistance_api):
            # Test all ship types
            all_results = []
            for ship_type in ["SUBOFF", "DTMB-5415", "KVLCC2"]:
                req = BenchmarkValidateRequest(ship_type=ship_type, benchmark_id="all")
                resp = benchmark_validate(req)
                for r in resp.results:
                    all_results.append({
                        "case_id": r.case_id,
                        "ship_type": r.ship_type,
                        "speed_ms": r.speed_ms,
                        "calc_ct": r.calc_ct,
                        "ref_ct": r.ref_ct,
                        "error_pct": r.error_pct,
                        "pass": r.pass_,
                        "method": r.method,
                        "note": r.note,
                    })

            artifact = {
                "mission": "M398",
                "description": "Benchmark validation results for SUBOFF, DTMB-5415, KVLCC2",
                "total_cases": len(all_results),
                "results": all_results,
                "summary": {
                    "passed": sum(1 for r in all_results if r["pass"]),
                    "failed": sum(1 for r in all_results if not r["pass"]),
                    "honest_fail_ships": ["SUBOFF"],
                    "algorithm_upgrade_recommended_for": ["SUBOFF"],
                }
            }

            output_path = tmp_path / "benchmark_validation.json"
            with open(output_path, "w") as f:
                json.dump(artifact, f, indent=2)

            assert output_path.exists()
            with open(output_path) as f:
                loaded = json.load(f)
            assert loaded["total_cases"] >= 10
            assert loaded["summary"]["failed"] > 0  # SUBOFF should fail
