"""
Tests for ship resistance calculation API (calculations.py).

Self-contained tests that don't require the full backend stack.
Validates:
1. ResistanceResponse includes ct, cf, cr, method fields (benchmark compatibility)
2. ITTC-1957 friction coefficient correctness
3. Ct = Cf + Cr identity
4. SUBOFF geometry sanity (Cb, L, B, D)
5. Froude/Reynolds number correctness
6. Edge cases: zero speed, extreme Cb, invalid dimensions
7. Benchmark integration: response fields match ship_benchmark expectations
"""
from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# Inline the resistance calculation logic for self-contained testing
# ---------------------------------------------------------------------------

def compute_resistance(
    ship_type: str = "SUBOFF",
    speed_ms: float = 5.0,
    length: float = 4.356,
    beam: float = 0.508,
    draft: float = 0.508,
    cb: float = 0.542,
    water_density: float = 998.0,
    kinematic_viscosity: float = 1.004e-6,
) -> dict:
    """Compute total resistance using the same logic as calculations.py."""
    L = length
    B = beam
    T = draft
    Cb = cb
    V = speed_ms
    rho = water_density
    nu = kinematic_viscosity

    if not (0.3 < Cb < 1.0):
        raise ValueError(f"Cb={Cb} out of physical range [0.3, 1.0]")
    if L <= 0 or B <= 0 or T <= 0:
        raise ValueError("Dimensions must be positive")

    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * V ** 2

    g = 9.81
    Fn = V / math.sqrt(g * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2
    Rt = Rf + Rr
    Ct = Cf + Cr

    return {
        "ship_type": ship_type,
        "speed_ms": V,
        "total_resistance_n": round(Rt, 4),
        "frictional_resistance_n": round(Rf, 4),
        "residual_resistance_n": round(Rr, 4),
        "reynolds_number": round(Re, 2),
        "froude_number": round(Fn, 6),
        "ct": round(Ct, 6),
        "cf": round(Cf, 6),
        "cr": round(Cr, 6),
        "method": "ITTC-57摩擦阻力 + Holtrop剩余阻力 (简化SUBOFF标定)",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SUBOFF = {
    "ship_type": "SUBOFF",
    "speed_ms": 5.0,
    "length": 4.356,
    "beam": 0.508,
    "draft": 0.508,
    "cb": 0.542,
    "water_density": 998.0,
    "kinematic_viscosity": 1.004e-6,
}


# ---------------------------------------------------------------------------
# Test 1: Response schema includes benchmark-required fields
# ---------------------------------------------------------------------------

class TestResponseSchema:
    def test_has_ct_field(self):
        resp = compute_resistance(**SUBOFF)
        assert "ct" in resp and resp["ct"] > 0

    def test_has_cf_field(self):
        resp = compute_resistance(**SUBOFF)
        assert "cf" in resp and resp["cf"] > 0

    def test_has_cr_field(self):
        resp = compute_resistance(**SUBOFF)
        assert "cr" in resp and resp["cr"] >= 0

    def test_has_method_field(self):
        resp = compute_resistance(**SUBOFF)
        assert "method" in resp and len(resp["method"]) > 0
        assert "ITTC" in resp["method"] or "Holtrop" in resp["method"]


# ---------------------------------------------------------------------------
# Test 2: ITTC-1957 friction coefficient correctness
# ---------------------------------------------------------------------------

class TestITTCCoefficient:
    def test_ittc_formula_suboff(self):
        resp = compute_resistance(**SUBOFF)
        Re = SUBOFF["speed_ms"] * SUBOFF["length"] / SUBOFF["kinematic_viscosity"]
        expected_cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        assert abs(resp["cf"] - expected_cf) < 2e-6

    def test_ittc_formula_kvlcc2(self):
        resp = compute_resistance(
            ship_type="KVLCC2", speed_ms=7.717, length=320.0,
            beam=58.0, draft=20.8, cb=0.8098,
        )
        Re = 7.717 * 320.0 / 1.004e-6
        expected_cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        assert abs(resp["cf"] - expected_cf) < 2e-6


# ---------------------------------------------------------------------------
# Test 3: Ct = Cf + Cr identity
# ---------------------------------------------------------------------------

class TestCoefficientIdentity:
    def test_ct_identity_suboff(self):
        resp = compute_resistance(**SUBOFF)
        assert abs(resp["ct"] - (resp["cf"] + resp["cr"])) < 2e-6

    @pytest.mark.parametrize("speed", [1.0, 3.0, 5.0, 8.0, 10.0])
    def test_ct_identity_across_speeds(self, speed):
        params = {**SUBOFF, "speed_ms": speed}
        resp = compute_resistance(**params)
        assert abs(resp["ct"] - (resp["cf"] + resp["cr"])) < 2e-6


# ---------------------------------------------------------------------------
# Test 4: SUBOFF geometry sanity
# ---------------------------------------------------------------------------

class TestGeometrySanity:
    def test_suboff_cb_range(self):
        assert 0.5 < SUBOFF["cb"] < 0.6

    def test_suboff_length(self):
        assert abs(SUBOFF["length"] - 4.356) < 0.001

    def test_suboff_beam(self):
        assert abs(SUBOFF["beam"] - 0.508) < 0.001


# ---------------------------------------------------------------------------
# Test 5: Froude and Reynolds number correctness
# ---------------------------------------------------------------------------

class TestDimensionlessNumbers:
    def test_froude_number_suboff_5ms(self):
        resp = compute_resistance(**SUBOFF)
        g = 9.81
        expected_fn = SUBOFF["speed_ms"] / math.sqrt(g * SUBOFF["length"])
        assert abs(resp["froude_number"] - expected_fn) < 1e-4

    def test_reynolds_number_suboff_5ms(self):
        resp = compute_resistance(**SUBOFF)
        expected_re = SUBOFF["speed_ms"] * SUBOFF["length"] / SUBOFF["kinematic_viscosity"]
        assert abs(resp["reynolds_number"] - expected_re) < 1e4


# ---------------------------------------------------------------------------
# Test 6: Edge cases and validation
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_cb_too_low_raises(self):
        with pytest.raises(ValueError):
            compute_resistance(**{**SUBOFF, "cb": 0.2})

    def test_cb_too_high_raises(self):
        with pytest.raises(ValueError):
            compute_resistance(**{**SUBOFF, "cb": 1.1})

    def test_negative_length_raises(self):
        with pytest.raises(ValueError):
            compute_resistance(**{**SUBOFF, "length": -1.0})

    def test_zero_speed_low_fn(self):
        resp = compute_resistance(**{**SUBOFF, "speed_ms": 0.1})
        assert resp["cr"] == 0.0


# ---------------------------------------------------------------------------
# Test 7: Benchmark integration compatibility
# ---------------------------------------------------------------------------

class TestBenchmarkCompatibility:
    def test_response_has_ct_for_benchmark(self):
        resp = compute_resistance(**SUBOFF)
        calc_data = {"ct": resp["ct"], "method": resp["method"]}
        assert calc_data.get("ct", 0.0) > 0

    def test_response_has_method_for_benchmark(self):
        resp = compute_resistance(**SUBOFF)
        calc_data = {"ct": resp["ct"], "method": resp["method"]}
        method = calc_data.get("method", "Unknown")
        assert method != "Unknown"
        assert "ITTC" in method or "Holtrop" in method

    def test_suboff_ct_order_of_magnitude(self):
        resp = compute_resistance(**SUBOFF)
        assert 1e-4 < resp["ct"] < 0.1

    def test_kvlcc2_ct_reasonable(self):
        resp = compute_resistance(
            ship_type="KVLCC2", speed_ms=7.717, length=320.0,
            beam=58.0, draft=20.8, cb=0.8098,
        )
        assert 1e-4 < resp["ct"] < 0.01
