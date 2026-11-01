"""
Platform verification audit script (m1671)

Programmatic verification of the claims in PLATFORM_VERIFICATION_STATUS_M1671.md.
Runs all core tests and produces a machine-readable summary.

Usage:
    python3 tools/verify_platform_capabilities.py
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

# Ensure backend is importable
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def run_pytest(path: str) -> dict:
    """Run a pytest file and return pass/fail counts."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", path, "-v", "--tb=no", "-q"],
        capture_output=True, text=True, cwd=str(BACKEND_DIR.parent)
    )
    lines = result.stdout.strip().split("\n")
    summary = lines[-1] if lines else ""
    passed = failed = skipped = 0
    for part in summary.split():
        if part.isdigit():
            num = int(part)
        elif part == "passed":
            passed = num
        elif part == "failed":
            failed = num
        elif part == "skipped":
            skipped = num
    return {"passed": passed, "failed": failed, "skipped": skipped, "summary": summary}


def verify_ittc57_friction_line():
    """Verify ITTC-1957 friction line against standard values."""
    results = []
    test_points = [(1e6, 0.00469), (5e6, 0.00340), (1e7, 0.00300)]
    for re, expected_cf in test_points:
        cf = 0.075 / (math.log10(re) - 2.0) ** 2
        error_pct = abs(cf - expected_cf) / expected_cf * 100.0
        results.append({
            "Re": re, "expected_cf": expected_cf, "calc_cf": cf,
            "error_pct": error_pct, "pass": error_pct < 2.0
        })
    return results


def verify_actuator_disk_analytic():
    """Verify actuator disk against Lanchester-Glauert closed form."""
    from services.propulsion import actuator_disk, disk_area, DEFAULT_RHO

    results = []
    test_cases = [(3.0, 2.0, 25_000.0), (6.0, 4.0, 120_000.0), (7.2, 6.0, 350_000.0), (12.0, 1.0, 4_500.0)]
    for V, D, T in test_cases:
        r = actuator_disk(V, T, D)
        eta_match = abs(r.ideal_efficiency - r.ideal_efficiency_analytic) < 1e-12
        momentum_ok = abs(r.momentum_residual_n) <= 1e-9 * max(T, 1.0)
        results.append({
            "V": V, "D": D, "T": T, "eta_i": r.ideal_efficiency,
            "eta_i_analytic": r.ideal_efficiency_analytic,
            "eta_match": eta_match, "momentum_residual_N": r.momentum_residual_n,
            "momentum_ok": momentum_ok, "pass": eta_match and momentum_ok,
        })

    # Anchor test: C_T = 3 -> eta_i = 2/3
    V, D = 5.0, 2.0
    area = disk_area(D)
    thrust = 3.0 * 0.5 * DEFAULT_RHO * area * V ** 2
    r = actuator_disk(V, thrust, D)
    anchor_pass = abs(r.thrust_coefficient - 3.0) < 1e-12 and abs(r.ideal_efficiency - 2.0 / 3.0) < 1e-12
    results.append({"test": "anchor_Ct_3", "Ct": r.thrust_coefficient, "eta_i": r.ideal_efficiency, "expected_eta": 2.0 / 3.0, "pass": anchor_pass})
    return results


def verify_stability_wall_sided():
    """Verify wall-sided GZ against exact box barge solution."""
    from services.stability_reference import box_parameters, exact_gz_box, wall_sided_gz

    params = box_parameters(beam_m=10.0, draft_m=3.0, kg_m=3.5, depth_m=5.0)
    gm, bm = params["gm_m"], params["bm_m"]
    beam, draft, kg, depth = params["beam_m"], params["draft_m"], params["kg_m"], params["depth_m"]

    results = []
    for phi_deg in [1, 2, 5, 10, 15, 20]:
        phi_rad = math.radians(phi_deg)
        gz_ws = wall_sided_gz(gm, bm, phi_rad)  # Correct signature: (gm, bm, phi)
        gz_exact_dict = exact_gz_box(beam, draft, kg, phi_rad, depth)
        gz_exact = gz_exact_dict["gz_m"]
        error_m = abs(gz_ws - gz_exact)
        results.append({
            "phi_deg": phi_deg, "gz_wall_sided": gz_ws, "gz_exact_box": gz_exact,
            "error_m": error_m, "pass": error_m < 1e-6,
        })
    return results


def verify_resistance_suboff():
    """Verify SUBOFF resistance calculation."""
    L, B, T, Cb = 4.356, 0.508, 0.476, 0.542
    rho, nu = 998.0, 1.004e-6
    suboff_cases = [
        {"case_id": "SUBOFF-01", "speed_ms": 3.0, "ct_exp": 0.00380},
        {"case_id": "SUBOFF-02", "speed_ms": 4.0, "ct_exp": 0.00400},
        {"case_id": "SUBOFF-03", "speed_ms": 5.0, "ct_exp": 0.00420},
        {"case_id": "SUBOFF-04", "speed_ms": 6.0, "ct_exp": 0.00440},
    ]

    results = []
    for case in suboff_cases:
        V = case["speed_ms"]
        Re = V * L / nu
        Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        Fn = V / math.sqrt(9.81 * L)
        Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
        Ct = Cf + Cr
        error_pct = abs(Ct - case["ct_exp"]) / case["ct_exp"] * 100.0
        results.append({
            "case_id": case["case_id"], "speed_ms": V, "Fn": Fn,
            "Ct_calc": Ct, "Ct_exp": case["ct_exp"],
            "error_pct": error_pct, "pass_15pct": error_pct <= 15.0,
        })
    return results


def main():
    print("=" * 80)
    print("TensorPlatform Capability Verification Audit (m1671)")
    print("=" * 80)

    audit = {
        "ittc57_friction_line": verify_ittc57_friction_line(),
        "actuator_disk_analytic": verify_actuator_disk_analytic(),
        "stability_wall_sided": verify_stability_wall_sided(),
        "resistance_suboff": verify_resistance_suboff(),
        "test_suites": {},
    }

    test_files = [
        ("propulsion", "backend/tests/test_propulsion.py"),
        ("stability_gz", "tests/test_m1386_stability_gz_validation.py"),
        ("resistance_benchmark", "backend/tests/test_resistance_benchmark_verification.py"),
    ]
    for name, path in test_files:
        print(f"\nRunning {name} tests...")
        result = run_pytest(path)
        audit["test_suites"][name] = result
        print(f"  {result['summary']}")

    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)

    all_pass = True
    ittc_pass = all(r["pass"] for r in audit["ittc57_friction_line"])
    print(f"ITTC-1957 friction line: {'✅ PASS' if ittc_pass else '❌ FAIL'}")
    all_pass = all_pass and ittc_pass

    ad_pass = all(r["pass"] for r in audit["actuator_disk_analytic"])
    print(f"Actuator disk analytic:  {'✅ PASS' if ad_pass else '❌ FAIL'}")
    all_pass = all_pass and ad_pass

    stab_pass = all(r["pass"] for r in audit["stability_wall_sided"])
    print(f"Stability wall-sided:    {'✅ PASS' if stab_pass else '❌ FAIL'}")
    all_pass = all_pass and stab_pass

    suboff_results = audit["resistance_suboff"]
    suboff_pass_count = sum(1 for r in suboff_results if r["pass_15pct"])
    print(f"SUBOFF resistance:       {suboff_pass_count}/4 cases within 15%")
    for r in suboff_results:
        status = "✅" if r["pass_15pct"] else "⚠️"
        print(f"  {status} {r['case_id']}: Fn={r['Fn']:.3f}, error={r['error_pct']:.1f}%")

    for name, result in audit["test_suites"].items():
        status = "✅" if result["failed"] == 0 else "❌"
        print(f"Test suite {name}: {status} {result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")

    print("\n" + "=" * 80)
    if all_pass:
        print("OVERALL: ✅ ALL VERIFICATIONS PASSED")
    else:
        print("OVERALL: ⚠️ SOME VERIFICATIONS FAILED (see details above)")
    print("=" * 80)

    report_path = Path(__file__).resolve().parent.parent / "artifacts" / "m1671_verification_audit.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(audit, f, indent=2, default=str)
    print(f"\nMachine-readable report: {report_path}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
