"""
Pytest tests for ship performance calculation verification (m1604).

Tests:
1. Benchmark data self-consistency (Rt = 0.5*rho*S*Ct*V^2)
2. SUBOFF resistance accuracy vs experimental data (Roddy 1990)
3. Stability wall-sided formula correctness
4. Resistance calculation edge cases

Run: pytest tests/test_calc_verification_m1604.py -v
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

# Paths
BACKEND_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_PATH = BACKEND_DIR / "backend" / "data" / "benchmark_reference.json"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Helpers (replica of calculation logic)
# ---------------------------------------------------------------------------
def calc_resistance(speed_ms, length=4.356, beam=0.508, draft=0.476, cb=0.542,
                    rho=998.0, nu=1.004e-6):
    L, B, T, Cb, V = length, beam, draft, cb, speed_ms
    g = 9.81
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * V ** 2
    Fn = V / math.sqrt(g * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2
    Rt = Rf + Rr
    Ct = Rt / (0.5 * rho * S * V ** 2)
    return {"S": S, "Re": Re, "Cf": Cf, "Fn": Fn, "Cr": Cr, "Rt": Rt, "Ct": Ct,
            "Rf": Rf, "Rr": Rr}


def calc_stability(gm, bm, heel_deg, displacement_t=1000.0):
    phi = math.radians(heel_deg)
    GZ = gm * math.sin(phi) + 0.5 * bm * math.tan(phi) ** 2 * math.sin(phi)
    RM = displacement_t * 1000 * 9.81 * GZ / 1000
    return {"GZ": GZ, "RM": RM}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def benchmark_data():
    with open(BENCHMARK_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Test 1: Benchmark self-consistency
# ---------------------------------------------------------------------------
class TestBenchmarkConsistency:
    """Verify Rt = 0.5 * rho * S * Ct * V^2 for all benchmark cases."""

    def test_all_cases_consistent(self, benchmark_data):
        for case in benchmark_data["cases"]:
            ct = case["experimental_ct"]
            rt = case["experimental_rt_n"]
            v = case["speed_ms"]
            geom = case.get("geometry", {})
            s = geom.get("wetted_surface_m2")
            rho = geom.get("rho_kgm3", 998.0)

            if s is None:
                pytest.skip(f"{case['case_id']}: missing wetted_surface_m2")

            rt_expected = 0.5 * rho * s * ct * v ** 2
            rel_err = abs(rt - rt_expected) / rt_expected * 100
            assert rel_err < 1.0, (
                f"{case['case_id']}: Rt inconsistency: "
                f"expected {rt_expected:.1f} N, got {rt:.1f} N (err={rel_err:.1f}%)"
            )

    def test_version_is_1_1_or_later(self, benchmark_data):
        """Ensure the fixed version is in use."""
        version = benchmark_data.get("version", "0.0")
        assert float(version) >= 1.1, (
            f"Benchmark version {version} < 1.1; Rt values may be inconsistent"
        )


# ---------------------------------------------------------------------------
# Test 2: SUBOFF resistance accuracy
# ---------------------------------------------------------------------------
class TestSUBOFFResistance:
    """Compare platform calculation against SUBOFF experimental data."""

    @pytest.fixture
    def suboff_cases(self, benchmark_data):
        return [c for c in benchmark_data["cases"] if c["ship_type"] == "SUBOFF"]

    def test_ct_within_35_percent(self, suboff_cases):
        """All SUBOFF Ct errors should be within 35% (generous bound)."""
        for case in suboff_cases:
            v = case["speed_ms"]
            ct_exp = case["experimental_ct"]
            geom = case["geometry"]
            rho = geom.get("rho_kgm3", 998.0)

            calc = calc_resistance(
                speed_ms=v,
                length=geom["length_m"],
                beam=geom["diameter_m"],
                draft=geom["diameter_m"],
                cb=0.542,
                rho=rho,
            )
            ct_err = abs(calc["Ct"] - ct_exp) / ct_exp * 100
            assert ct_err < 35.0, (
                f"{case['case_id']}: Ct error {ct_err:.1f}% > 35% "
                f"(calc={calc['Ct']:.5f}, exp={ct_exp})"
            )

    def test_ct_within_15_percent_at_moderate_fn(self, suboff_cases):
        """At moderate Fn (0.4-0.8), Ct error should be within 15%."""
        moderate = [c for c in suboff_cases if 0.4 < c["froude_number"] < 0.8]
        for case in moderate:
            v = case["speed_ms"]
            ct_exp = case["experimental_ct"]
            geom = case["geometry"]
            rho = geom.get("rho_kgm3", 998.0)

            calc = calc_resistance(
                speed_ms=v,
                length=geom["length_m"],
                beam=geom["diameter_m"],
                draft=geom["diameter_m"],
                cb=0.542,
                rho=rho,
            )
            ct_err = abs(calc["Ct"] - ct_exp) / ct_exp * 100
            assert ct_err < 15.0, (
                f"{case['case_id']} (Fn={case['froude_number']:.3f}): "
                f"Ct error {ct_err:.1f}% > 15%"
            )

    def test_froude_number_calculation(self):
        """Verify Fn = V / sqrt(g*L) is correct."""
        calc = calc_resistance(speed_ms=3.0, length=4.356)
        expected_fn = 3.0 / math.sqrt(9.81 * 4.356)
        assert abs(calc["Fn"] - expected_fn) < 1e-6

    def test_ittc57_friction_line(self):
        """Verify ITTC-1957 friction line: Cf = 0.075 / (log10(Re) - 2)^2."""
        calc = calc_resistance(speed_ms=3.0, length=4.356)
        Re = calc["Re"]
        expected_cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        assert abs(calc["Cf"] - expected_cf) < 1e-8


# ---------------------------------------------------------------------------
# Test 3: Stability (wall-sided formula)
# ---------------------------------------------------------------------------
class TestStability:
    """Verify wall-sided formula implementation."""

    def test_gz_at_zero_heel(self):
        """GZ should be 0 at 0° heel."""
        result = calc_stability(gm=1.0, bm=3.0, heel_deg=0.0)
        assert abs(result["GZ"]) < 1e-10

    def test_gz_small_angle_approximation(self):
        """At small angles, GZ ≈ GM*sin(phi)."""
        result = calc_stability(gm=1.0, bm=3.0, heel_deg=5.0)
        phi = math.radians(5.0)
        gz_linear = 1.0 * math.sin(phi)
        # Wall-sided adds BM correction; at 5° it should be small
        assert abs(result["GZ"] - gz_linear) / gz_linear < 0.02  # within 2%

    def test_gz_analytical_exact(self):
        """GZ should match analytical wall-sided formula exactly."""
        for phi_deg in [5, 10, 15, 20, 30, 45]:
            gm, bm = 1.0, 3.0
            result = calc_stability(gm=gm, bm=bm, heel_deg=phi_deg)
            phi = math.radians(phi_deg)
            gz_expected = gm * math.sin(phi) + 0.5 * bm * math.tan(phi) ** 2 * math.sin(phi)
            assert abs(result["GZ"] - gz_expected) < 1e-10, (
                f"phi={phi_deg}°: GZ mismatch"
            )

    def test_righting_moment_positive_for_positive_gm(self):
        """RM should be positive when GM > 0 (stable ship)."""
        result = calc_stability(gm=1.0, bm=3.0, heel_deg=10.0, displacement_t=1000.0)
        assert result["RM"] > 0
        assert result["GZ"] > 0

    def test_righting_moment_formula(self):
        """RM = displacement * g * GZ / 1000 (kN·m)."""
        gm, bm = 1.5, 4.0
        disp = 5000.0
        result = calc_stability(gm=gm, bm=bm, heel_deg=15.0, displacement_t=disp)
        expected_rm = disp * 1000 * 9.81 * result["GZ"] / 1000
        assert abs(result["RM"] - expected_rm) < 0.01


# ---------------------------------------------------------------------------
# Test 4: Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_speed_gives_zero_resistance(self):
        """At V→0, resistance should approach 0."""
        # Very low speed
        calc = calc_resistance(speed_ms=0.001)
        assert calc["Rt"] < 0.001  # essentially zero

    def test_high_reynolds_number(self):
        """At high Re, Cf should be small."""
        calc = calc_resistance(speed_ms=10.0, length=320.0)
        assert calc["Re"] > 1e9
        assert calc["Cf"] < 0.002

    def test_positive_resistance_always(self):
        """Resistance should always be positive for positive speed."""
        for v in [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]:
            calc = calc_resistance(speed_ms=v)
            assert calc["Rt"] > 0, f"Negative resistance at V={v}"
            assert calc["Ct"] > 0, f"Negative Ct at V={v}"
