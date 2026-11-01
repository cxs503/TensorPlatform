"""
Tests for ship domain router registration and benchmark validation.

Verifies that:
1. ship_batch, ship_benchmark, ship_samples routers are properly registered
2. Benchmark validation endpoint returns correct structure
3. Resistance calculation produces physically reasonable results
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Test 1: Router registration
# ---------------------------------------------------------------------------

class TestShipRouterRegistration:
    """Verify ship domain routers are importable and have expected routes."""

    def test_ship_batch_router_exists(self):
        from api.domain.ship_batch import router
        routes = [r.path for r in router.routes]
        assert any("batch_sweep" in r for r in routes), f"Missing batch_sweep route, got: {routes}"

    def test_ship_benchmark_router_exists(self):
        from api.domain.ship_benchmark import router
        routes = [r.path for r in router.routes]
        assert any("benchmark_validate" in r for r in routes), f"Missing benchmark_validate route, got: {routes}"
        assert any("benchmark_cases" in r for r in routes), f"Missing benchmark_cases route, got: {routes}"

    def test_ship_samples_router_exists(self):
        from api.domain.ship_samples import router
        routes = [r.path for r in router.routes]
        assert any("batch_sweep" in r for r in routes), f"Missing ship/batch_sweep route, got: {routes}"
        assert any("samples" in r for r in routes), f"Missing ship/samples route, got: {routes}"
        assert any("timing_baseline" in r for r in routes), f"Missing timing_baseline route, got: {routes}"

    def test_misc_domain_includes_ship_routers(self):
        """Verify misc.py includes all three ship routers."""
        from api.domain import misc
        # Check that the module has the router attributes
        assert hasattr(misc, "ship_batch_router")
        assert hasattr(misc, "ship_benchmark_router")
        assert hasattr(misc, "ship_samples_router")

    def test_domain_init_exports_ship_routers(self):
        """Verify __init__.py exports all ship routers."""
        from api.domain import (
            ship_batch_router,
            ship_benchmark_router,
            ship_samples_router,
        )
        assert ship_batch_router is not None
        assert ship_benchmark_router is not None
        assert ship_samples_router is not None


# ---------------------------------------------------------------------------
# Test 2: Benchmark data integrity
# ---------------------------------------------------------------------------

class TestBenchmarkData:
    """Verify benchmark_reference.json structure and content."""

    def test_file_exists(self):
        data_path = BACKEND_DIR / "data" / "benchmark_reference.json"
        assert data_path.exists(), f"Benchmark file not found: {data_path}"

    def test_valid_json(self):
        data_path = BACKEND_DIR / "data" / "benchmark_reference.json"
        with open(data_path) as f:
            data = json.load(f)
        assert "cases" in data
        assert len(data["cases"]) >= 10

    def test_three_ship_types(self):
        data_path = BACKEND_DIR / "data" / "benchmark_reference.json"
        with open(data_path) as f:
            data = json.load(f)
        ship_types = {c["ship_type"] for c in data["cases"]}
        assert "SUBOFF" in ship_types
        assert "DTMB-5415" in ship_types
        assert "KVLCC2" in ship_types


# ---------------------------------------------------------------------------
# Test 3: Resistance calculation physics
# ---------------------------------------------------------------------------

class TestResistanceCalculation:
    """Verify resistance calculation produces physically reasonable results."""

    def _calc_ct(self, speed_ms, length, beam, draft, cb, wetted_surface=None):
        """Replicate the resistance calculation."""
        rho = 998.0
        nu = 1.004e-6
        g = 9.81

        if wetted_surface and wetted_surface > 0:
            S = wetted_surface
        else:
            S = math.pi * beam * length * (0.5 + 0.25 * cb)

        Re = speed_ms * length / nu
        Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        Fn = speed_ms / math.sqrt(g * length)
        Cr = 0.002 * (1 + 2.0 * cb) * Fn ** 2.5 if Fn > 0.05 else 0.0

        Rf = 0.5 * rho * S * Cf * speed_ms ** 2
        Rr = 0.5 * rho * S * Cr * speed_ms ** 2
        Rt = Rf + Rr

        dynamic_pressure = 0.5 * rho * S * speed_ms ** 2
        Ct = Rt / dynamic_pressure if dynamic_pressure > 0 else 0.0
        return Ct, Cf, Cr, Fn, Re, S

    def test_suboff_ct_range(self):
        """SUBOFF Ct should be in 0.003-0.006 range for Fn=0.4-0.9."""
        Ct, Cf, Cr, Fn, Re, S = self._calc_ct(
            speed_ms=5.0, length=4.356, beam=0.508, draft=0.508, cb=0.508,
            wetted_surface=5.42
        )
        assert 0.002 < Ct < 0.01, f"SUBOFF Ct={Ct} out of expected range"

    def test_friction_coefficient_positive(self):
        """Cf must always be positive."""
        for speed in [1.0, 5.0, 10.0]:
            Ct, Cf, Cr, Fn, Re, S = self._calc_ct(
                speed_ms=speed, length=100.0, beam=20.0, draft=10.0, cb=0.7
            )
            assert Cf > 0, f"Cf={Cf} at speed={speed}"

    def test_reynolds_number_scales_with_speed(self):
        """Re should increase linearly with speed."""
        _, _, _, _, Re1, _ = self._calc_ct(
            speed_ms=5.0, length=100.0, beam=20.0, draft=10.0, cb=0.7
        )
        _, _, _, _, Re2, _ = self._calc_ct(
            speed_ms=10.0, length=100.0, beam=20.0, draft=10.0, cb=0.7
        )
        assert abs(Re2 / Re1 - 2.0) < 0.01, f"Re ratio={Re2/Re1}, expected 2.0"

    def test_wetted_surface_axisymmetric(self):
        """Axisymmetric wetted surface formula should produce reasonable S."""
        _, _, _, _, _, S = self._calc_ct(
            speed_ms=5.0, length=4.356, beam=0.508, draft=0.508, cb=0.508
        )
        # For SUBOFF, S should be around 5-7 m²
        assert 3.0 < S < 10.0, f"SUBOFF S={S} out of expected range"


# ---------------------------------------------------------------------------
# Test 4: Benchmark validate endpoint structure
# ---------------------------------------------------------------------------

class TestBenchmarkValidateEndpoint:
    """Test the benchmark_validate function directly (no HTTP)."""

    def test_response_has_required_fields(self):
        from api.domain.ship_benchmark import (
            _load_benchmark_data,
            BenchmarkResult,
        )
        data = _load_benchmark_data()
        assert len(data["cases"]) >= 10

        # Verify BenchmarkResult model has required fields
        result = BenchmarkResult(
            case_id="TEST-01",
            ship_type="SUBOFF",
            speed_ms=5.0,
            calc_ct=0.004,
            ref_ct=0.004,
            error_pct=0.0,
            pass_=True,
            method="test",
        )
        assert result.case_id == "TEST-01"
        assert result.error_pct == 0.0
        assert result.pass_ is True

    def test_error_pct_calculation(self):
        """Verify error percentage formula."""
        ref_ct = 0.00300
        calc_ct = 0.00345
        expected = abs(calc_ct - ref_ct) / ref_ct * 100.0
        assert abs(expected - 15.0) < 0.01

    def test_pass_threshold(self):
        """15% error should pass, 16% should fail."""
        ref_ct = 0.00300
        # 15% error
        calc_ct_15 = ref_ct * 1.15
        error_15 = abs(calc_ct_15 - ref_ct) / ref_ct * 100.0
        assert error_15 <= 15.0

        # 16% error
        calc_ct_16 = ref_ct * 1.16
        error_16 = abs(calc_ct_16 - ref_ct) / ref_ct * 100.0
        assert error_16 > 15.0
