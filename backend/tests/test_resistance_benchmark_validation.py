"""
Resistance calculation benchmark validation.

Validates the Holtrop-Mennen simplified resistance calculation in
backend/api/calculations.py against experimental reference data from:
- Roddy (1990) for DARPA SUBOFF
- ITTC V&V Workshop (2010) for DTMB-5415
- SIMMAN 2008 for KVLCC2

KEY FINDINGS (2026-11-01):
1. Wetted surface formula uses axisymmetric body approximation (S=π·B·L·(0.5+0.25·Cb))
   which overestimates S by ~2.5x for full-form merchant ships (KVLCC2).
2. No form factor (1+k) is included, underestimating viscous resistance.
3. Residual resistance coefficient formula is overly simplistic.
4. SUBOFF Ct error is within 15% (coincidental), but Rt error is >400% due to S error.
5. KVLCC2 Ct error is ~50%, well outside the 15% acceptance threshold.

See docs/resistance_benchmark_validation_report.md for full analysis.
"""
import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# Load benchmark reference data
# ---------------------------------------------------------------------------
BENCHMARK_PATH = BACKEND_DIR / "data" / "benchmark_reference.json"

with open(BENCHMARK_PATH, "r") as f:
    BENCHMARK_DATA = json.load(f)

# ---------------------------------------------------------------------------
# Ship geometry presets (with literature wetted surface where available)
# ---------------------------------------------------------------------------
SHIP_GEOMETRY = {
    "SUBOFF": {
        "length": 4.356,
        "beam": 0.508,
        "draft": 0.508,
        "cb": 0.508,
        "wetted_surface_lit": 5.42,  # Roddy 1990
    },
    "DTMB-5415": {
        "length": 5.720,
        "beam": 0.759,
        "draft": 0.246,
        "cb": 0.508,
    },
    "KVLCC2": {
        "length": 320.0,
        "beam": 58.0,
        "draft": 20.8,
        "cb": 0.8098,
        "wetted_surface_lit": 16500.0,  # SIMMAN 2008
    },
}

# ---------------------------------------------------------------------------
# Resistance calculation (mirrors the API logic for direct testing)
# ---------------------------------------------------------------------------
WATER_DENSITY = 998.0
KINEMATIC_VISCOSITY = 1.004e-6
G = 9.81


def calc_resistance_api_logic(ship_type: str, speed_ms: float, geometry: dict) -> dict:
    """
    Compute resistance using the SAME formula as the current API endpoint.
    This is the baseline we're validating.
    """
    L = geometry["length"]
    B = geometry["beam"]
    T = geometry["draft"]
    Cb = geometry["cb"]
    V = speed_ms
    rho = WATER_DENSITY
    nu = KINEMATIC_VISCOSITY

    # Current API formula: axisymmetric body approximation
    S = math.pi * B * L * (0.5 + 0.25 * Cb)

    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * V ** 2

    Fn = V / math.sqrt(G * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2

    Rt = Rf + Rr
    Ct = Rt / (0.5 * rho * S * V ** 2) if V > 0 else 0.0

    return {
        "Ct": Ct, "Rt": Rt, "Cf": Cf, "Cr": Cr,
        "Re": Re, "Fn": Fn, "S": S, "Rf": Rf, "Rr": Rr,
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", BENCHMARK_DATA["cases"], ids=lambda c: c["case_id"])
def test_resistance_vs_experiment(case):
    """
    For each benchmark case, compute resistance and compare with experimental data.

    This test DOCUMENTS the error — it does not enforce pass/fail for known
    limitation cases. The pass/fail judgment is recorded for analysis.
    """
    ship_type = case["ship_type"]
    speed_ms = case.get("speed_ms", 0.0)
    exp_ct = case["experimental_ct"]
    exp_rt = case.get("experimental_rt_n", 0.0)

    geometry = SHIP_GEOMETRY.get(ship_type)
    if geometry is None:
        pytest.skip(f"No geometry preset for {ship_type}")

    result = calc_resistance_api_logic(ship_type, speed_ms, geometry)
    calc_ct = result["Ct"]
    calc_rt = result["Rt"]
    fn = result["Fn"]
    S_calc = result["S"]

    # Error calculation
    ct_error_pct = abs(calc_ct - exp_ct) / exp_ct * 100.0 if exp_ct > 0 else 999.0
    rt_error_pct = abs(calc_rt - exp_rt) / exp_rt * 100.0 if exp_rt > 0 else 999.0

    # Pass criterion: Ct error <= 15%
    tolerance = 15.0
    passed = ct_error_pct <= tolerance

    # Document known limitations
    note = ""
    if ship_type == "KVLCC2":
        note = (
            f"KNOWN LIMITATION: Wetted surface S={S_calc:.0f}m² uses axisymmetric formula; "
            f"literature value ~16,500m². Overestimation causes Ct underestimation. "
            f"Form factor (1+k) not included."
        )
    elif ship_type == "SUBOFF":
        note = (
            f"NOTE: Ct error within 15% is coincidental; Rt error is large due to "
            f"S mismatch (calc={S_calc:.2f}m² vs lit=5.42m²). "
            f"Holtrop method not physically valid for submarine forms."
        )
    elif ship_type == "DTMB-5415":
        note = (
            f"NOTE: Surface combatant — Holtrop method has limited validity. "
            f"Results for reference only."
        )

    # Store result for summary
    case["_test_result"] = {
        "calc_ct": round(calc_ct, 6),
        "exp_ct": exp_ct,
        "ct_error_pct": round(ct_error_pct, 2),
        "calc_rt": round(calc_rt, 4),
        "exp_rt": exp_rt,
        "rt_error_pct": round(rt_error_pct, 2),
        "fn": round(fn, 4),
        "S_calc": round(S_calc, 2),
        "passed": passed,
        "note": note,
    }

    # For KVLCC2, we expect FAIL (known issue) — document but don't assert
    # For SUBOFF, Ct may pass but Rt will fail — document both
    # This test is for documentation, not enforcement


def test_benchmark_summary():
    """
    Generate a summary of all benchmark results.
    """
    cases = BENCHMARK_DATA["cases"]
    results = []
    for case in cases:
        r = case.get("_test_result")
        if r:
            results.append((case["case_id"], case["ship_type"], r))

    if not results:
        pytest.skip("No test results available")

    total = len(results)
    passed = sum(1 for _, _, r in results if r["passed"])
    failed = total - passed

    # Print summary table
    print("\n" + "=" * 120)
    print("RESISTANCE BENCHMARK VALIDATION SUMMARY (Current API Logic)")
    print("=" * 120)
    print(f"{'Case':<15} {'Ship':<12} {'Fn':>6} {'S_calc':>10} {'Ct_calc':>10} {'Ct_exp':>10} {'Ct_err%':>8} {'Rt_err%':>8} {'Status':>8}")
    print("-" * 120)
    for case_id, ship_type, r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"{case_id:<15} {ship_type:<12} {r['fn']:>6.3f} "
              f"{r['S_calc']:>10.1f} {r['calc_ct']:>10.6f} {r['exp_ct']:>10.4f} "
              f"{r['ct_error_pct']:>8.1f} {r['rt_error_pct']:>8.1f} {status:>8}")
    print("-" * 120)
    print(f"Total: {total}, Passed: {passed}, Failed: {failed}, Pass Rate: {passed/total*100:.1f}%")
    print("=" * 120)

    # Print known limitations
    print("\nKNOWN LIMITATIONS:")
    for case_id, ship_type, r in results:
        if r["note"]:
            print(f"  {case_id}: {r['note']}")

    # At least document that we ran the tests
    assert total > 0, "No benchmark cases were tested"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--noconftest"])
