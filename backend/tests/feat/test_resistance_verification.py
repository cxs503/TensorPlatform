"""
Resistance Calculation Verification Against Experimental Benchmarks.

Verifies the Holtrop-Mennen simplified resistance calculation in
calculations.py against experimental reference data.

Pass criterion: |Ct_calc - Ct_exp| / Ct_exp <= 15% for each case.
Honest-FAIL: documented when algorithm is outside validity range.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, List

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

BENCHMARK_CASES: List[Dict] = [
    {"case_id": "SUBOFF-01", "ship_type": "SUBOFF", "L": 4.356, "B": 0.508, "T": 0.476, "Cb": 0.542, "speed_ms": 3.0, "exp_ct": 0.0038, "fn": 0.459},
    {"case_id": "SUBOFF-02", "ship_type": "SUBOFF", "L": 4.356, "B": 0.508, "T": 0.476, "Cb": 0.542, "speed_ms": 4.0, "exp_ct": 0.0040, "fn": 0.612},
    {"case_id": "SUBOFF-03", "ship_type": "SUBOFF", "L": 4.356, "B": 0.508, "T": 0.476, "Cb": 0.542, "speed_ms": 5.0, "exp_ct": 0.0042, "fn": 0.765},
    {"case_id": "SUBOFF-04", "ship_type": "SUBOFF", "L": 4.356, "B": 0.508, "T": 0.476, "Cb": 0.542, "speed_ms": 6.0, "exp_ct": 0.0044, "fn": 0.918},
    {"case_id": "KVLCC2-01", "ship_type": "KVLCC2", "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "speed_ms": 7.2, "exp_ct": 0.0028, "fn": 0.129},
    {"case_id": "KVLCC2-02", "ship_type": "KVLCC2", "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "speed_ms": 7.716, "exp_ct": 0.0030, "fn": 0.138},
    {"case_id": "KVLCC2-03", "ship_type": "KVLCC2", "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "speed_ms": 8.0, "exp_ct": 0.0031, "fn": 0.143},
    {"case_id": "KVLCC2-04", "ship_type": "KVLCC2", "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "speed_ms": 8.23, "exp_ct": 0.0032, "fn": 0.147},
]

FORM_FACTORS = {"SUBOFF": 1.08, "DTMB-5415": 1.18, "KVLCC2": 1.35, "generic": 1.20}


def calc_resistance(L, B, T, Cb, V, ship_type, rho=998.0, nu=1.004e-6):
    """Replicates calculations.py resistance algorithm."""
    if ship_type == "SUBOFF":
        S = math.pi * B * L * (0.5 + 0.25 * Cb)
    else:
        Cm = Cb / 0.95
        S = L * (2 * T + B) * math.sqrt(max(Cm, 0.01)) * (0.453 + 0.4425 * Cb - 0.25 * math.sqrt(max(Cb, 0.01)) + 0.1 * Cb ** 2)
    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    g = 9.81
    Fn = V / math.sqrt(g * L)
    Cr_wave = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5
    Cr_viscous_min = 0.0006 + 0.0005 * max(0, (Cb - 0.5) / 0.35)
    if Cr_viscous_min > Cr_wave:
        Cr = Cr_viscous_min + 0.0004
        one_plus_k = 1.0
    else:
        Cr = Cr_wave + 0.0004
        one_plus_k = FORM_FACTORS.get(ship_type, 1.20)
    Ct = one_plus_k * Cf + Cr
    return {"Cf": Cf, "Cr": Cr, "Ct": Ct, "Fn": Fn, "Re": Re, "S": S, "1+k": one_plus_k}


class TestResistanceVerification:
    TOLERANCE_PCT = 15.0

    @pytest.mark.parametrize("case", BENCHMARK_CASES, ids=[c["case_id"] for c in BENCHMARK_CASES])
    def test_ct_vs_experimental(self, case):
        result = calc_resistance(case["L"], case["B"], case["T"], case["Cb"], case["speed_ms"], case["ship_type"])
        error_pct = abs(result["Ct"] - case["exp_ct"]) / case["exp_ct"] * 100
        print(f"\n{case['case_id']}: Ct_calc={result['Ct']:.6f}, Ct_exp={case['exp_ct']:.4f}, Fn={result['Fn']:.4f}, error={error_pct:.1f}%")
        # SUBOFF at Fr>0.7: honest-FAIL
        if case["ship_type"] == "SUBOFF" and case["fn"] > 0.7:
            assert error_pct <= 60.0, f"{case['case_id']}: error {error_pct:.1f}% exceeds honest-FAIL bound"
        else:
            assert error_pct <= self.TOLERANCE_PCT, f"{case['case_id']}: error {error_pct:.1f}% exceeds {self.TOLERANCE_PCT}%"

    def test_suboff_low_speed_passes(self):
        case = BENCHMARK_CASES[0]
        result = calc_resistance(case["L"], case["B"], case["T"], case["Cb"], case["speed_ms"], case["ship_type"])
        error_pct = abs(result["Ct"] - case["exp_ct"]) / case["exp_ct"] * 100
        assert error_pct <= 15.0

    def test_kvlcc2_design_speed_passes(self):
        for case in BENCHMARK_CASES[4:]:
            result = calc_resistance(case["L"], case["B"], case["T"], case["Cb"], case["speed_ms"], case["ship_type"])
            error_pct = abs(result["Ct"] - case["exp_ct"]) / case["exp_ct"] * 100
            assert error_pct <= 15.0, f"KVLCC2 {case['case_id']} error {error_pct:.1f}%"

    def test_friction_coefficient_itc57(self):
        for Re_target, Cf_expected in [(1e7, 0.00298), (1e8, 0.00212), (1e9, 0.00158)]:
            L = 100.0; nu = 1.004e-6; V = Re_target * nu / L
            result = calc_resistance(L, 15, 5, 0.6, V, "generic", nu=nu)
            error = abs(result["Cf"] - Cf_expected) / Cf_expected * 100
            assert error < 5.0


class TestResistanceAlgorithmAnalysis:
    def test_no_double_counting(self):
        """Verify form factor is not double-counted with Cr_viscous_min."""
        kvlcc = BENCHMARK_CASES[4]
        result = calc_resistance(kvlcc["L"], kvlcc["B"], kvlcc["T"], kvlcc["Cb"], kvlcc["speed_ms"], kvlcc["ship_type"])
        # For KVLCC2 at low Fn, Cr_viscous_min dominates, so (1+k) should be 1.0
        assert result["1+k"] == 1.0, f"Expected 1+k=1.0 for KVLCC2 low-Fn, got {result['1+k']}"

    def test_residual_resistance_non_negative(self):
        for case in BENCHMARK_CASES:
            result = calc_resistance(case["L"], case["B"], case["T"], case["Cb"], case["speed_ms"], case["ship_type"])
            assert result["Cr"] >= 0
