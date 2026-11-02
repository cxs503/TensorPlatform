#!/usr/bin/env python3
"""
Comprehensive verification of TensorPlatform ship performance calculations.

Verifies:
1. Resistance calculation (ITTC-57 + empirical Cr) against SUBOFF experimental data
2. Stability calculation (wall-sided formula) against analytical solution
3. Benchmark data self-consistency

Author: TensorPlatform Agent (m1604)
Date: 2026-11-01
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure matplotlib uses non-interactive backend
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WORK_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_PATH = WORK_DIR / "backend" / "data" / "benchmark_reference.json"
OUTPUT_DIR = WORK_DIR / "docs" / "verification_m1604"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Platform calculation (replica of backend/api/calculations.py logic)
# ---------------------------------------------------------------------------
def calc_resistance_platform(
    speed_ms: float,
    length: float = 4.356,
    beam: float = 0.508,
    draft: float = 0.476,
    cb: float = 0.542,
    rho: float = 998.0,
    nu: float = 1.004e-6,
) -> Dict[str, float]:
    """Replicate the platform's resistance calculation."""
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

    return {
        "S": S, "Re": Re, "Cf": Cf, "Rf": Rf,
        "Fn": Fn, "Cr": Cr, "Rr": Rr, "Rt": Rt, "Ct": Ct,
    }


def calc_stability_wall_sided(
    gm: float, bm: float, heel_deg: float, displacement_t: float = 1000.0
) -> Dict[str, float]:
    """Wall-sided formula for GZ."""
    phi = math.radians(heel_deg)
    GZ = gm * math.sin(phi) + 0.5 * bm * (math.tan(phi) ** 2) * math.sin(phi)
    RM = displacement_t * 1000 * 9.81 * GZ / 1000  # kN·m
    return {"GZ": GZ, "RM": RM, "phi_rad": phi}


# ---------------------------------------------------------------------------
# Load benchmark data
# ---------------------------------------------------------------------------
def load_benchmark() -> Dict[str, Any]:
    with open(BENCHMARK_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Verification 1: Benchmark data self-consistency
# ---------------------------------------------------------------------------
def verify_benchmark_consistency(bench: Dict) -> List[Dict]:
    """Check that Rt = 0.5 * rho * S * Ct * V^2 for all cases."""
    results = []
    for c in bench["cases"]:
        ct = c["experimental_ct"]
        rt = c["experimental_rt_n"]
        v = c["speed_ms"]
        geom = c.get("geometry", {})
        s = geom.get("wetted_surface_m2")
        rho = geom.get("rho_kgm3", 998.0)

        if s is None:
            results.append({
                "case_id": c["case_id"],
                "status": "SKIP",
                "reason": "missing wetted_surface_m2",
            })
            continue

        rt_expected = 0.5 * rho * s * ct * v ** 2
        rel_err = abs(rt - rt_expected) / rt_expected * 100 if rt_expected > 0 else float('inf')
        results.append({
            "case_id": c["case_id"],
            "rt_exp": rt,
            "rt_consistent": round(rt_expected, 1),
            "rel_err_pct": round(rel_err, 2),
            "status": "PASS" if rel_err < 1.0 else "FAIL",
        })
    return results


# ---------------------------------------------------------------------------
# Verification 2: SUBOFF resistance vs platform calculation
# ---------------------------------------------------------------------------
def verify_suboff_resistance(bench: Dict) -> List[Dict]:
    """Compare platform calculation against SUBOFF experimental data."""
    suboff_cases = [c for c in bench["cases"] if c["ship_type"] == "SUBOFF"]
    results = []

    for c in suboff_cases:
        v = c["speed_ms"]
        ct_exp = c["experimental_ct"]
        rt_exp = c["experimental_rt_n"]
        geom = c["geometry"]
        s_exp = geom["wetted_surface_m2"]
        rho = geom.get("rho_kgm3", 998.0)

        # Platform calculation (uses default SUBOFF geometry)
        calc = calc_resistance_platform(
            speed_ms=v,
            length=geom["length_m"],
            beam=geom["diameter_m"],
            draft=geom["diameter_m"],  # axisymmetric: draft = diameter for surface case
            cb=0.542,  # SUBOFF Cb
            rho=rho,
        )

        ct_calc = calc["Ct"]
        rt_calc = calc["Rt"]
        s_calc = calc["S"]

        # Compare Ct (dimensionless, independent of S)
        ct_err = (ct_calc - ct_exp) / ct_exp * 100
        # Compare Rt (depends on S)
        rt_err = (rt_calc - rt_exp) / rt_exp * 100

        results.append({
            "case_id": c["case_id"],
            "speed_ms": v,
            "fn": calc["Fn"],
            "re": calc["Re"],
            "s_calc": round(s_calc, 4),
            "s_exp": s_exp,
            "ct_calc": round(ct_calc, 6),
            "ct_exp": ct_exp,
            "ct_err_pct": round(ct_err, 1),
            "rt_calc": round(rt_calc, 2),
            "rt_exp": rt_exp,
            "rt_err_pct": round(rt_err, 1),
            "cf": round(calc["Cf"], 6),
            "cr": round(calc["Cr"], 6),
        })

    return results


# ---------------------------------------------------------------------------
# Verification 3: Stability (wall-sided formula) analytical check
# ---------------------------------------------------------------------------
def verify_stability_analytical() -> List[Dict]:
    """Verify wall-sided formula against known analytical properties."""
    results = []
    gm, bm = 1.0, 3.0

    for phi_deg in [5, 10, 15, 20, 30, 45, 60]:
        calc = calc_stability_wall_sided(gm, bm, phi_deg)
        phi = calc["phi_rad"]

        # Analytical: GZ = GM*sin(phi) + 0.5*BM*tan^2(phi)*sin(phi)
        gz_expected = gm * math.sin(phi) + 0.5 * bm * math.tan(phi) ** 2 * math.sin(phi)
        gz_err = abs(calc["GZ"] - gz_expected) / gz_expected * 100 if gz_expected > 0 else 0

        # Small-angle approximation: GZ ≈ GM*sin(phi)
        gz_linear = gm * math.sin(phi)
        linear_err = (gz_linear - calc["GZ"]) / calc["GZ"] * 100

        results.append({
            "phi_deg": phi_deg,
            "gz_calc": round(calc["GZ"], 6),
            "gz_analytical": round(gz_expected, 6),
            "gz_err_pct": round(gz_err, 4),
            "gz_linear": round(gz_linear, 6),
            "linear_err_pct": round(linear_err, 1),
            "rm_knm": round(calc["RM"], 4),
        })

    return results


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
def plot_suboff_verification(suboff_results: List[Dict], output_dir: Path):
    """Generate SUBOFF verification plots."""
    speeds = [r["speed_ms"] for r in suboff_results]
    fn_vals = [r["fn"] for r in suboff_results]
    ct_calc = [r["ct_calc"] for r in suboff_results]
    ct_exp = [r["ct_exp"] for r in suboff_results]
    ct_err = [r["ct_err_pct"] for r in suboff_results]

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle("SUBOFF Resistance Verification: Platform vs Experiment (Roddy 1990)",
                 fontsize=14, fontweight='bold')

    # (a) Ct vs Fn
    ax = axes[0, 0]
    ax.plot(fn_vals, ct_exp, 'ro-', label='Experiment (Roddy 1990)', markersize=8)
    ax.plot(fn_vals, ct_calc, 'bs--', label='Platform (ITTC-57 + Cr model)', markersize=8)
    ax.set_xlabel('Froude Number $Fn$')
    ax.set_ylabel('Total Resistance Coefficient $C_T$')
    ax.set_title('(a) $C_T$ vs $Fn$')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # (b) Ct error vs Fn
    ax = axes[0, 1]
    colors = ['green' if abs(e) < 15 else 'orange' if abs(e) < 30 else 'red' for e in ct_err]
    ax.bar(range(len(fn_vals)), ct_err, color=colors, alpha=0.7, edgecolor='black')
    ax.set_xticks(range(len(fn_vals)))
    ax.set_xticklabels([f'{fn:.3f}' for fn in fn_vals])
    ax.set_xlabel('Froude Number $Fn$')
    ax.set_ylabel('$C_T$ Error (%)')
    ax.set_title('(b) $C_T$ Relative Error')
    ax.axhline(y=0, color='k', linewidth=0.5)
    ax.axhline(y=15, color='r', linewidth=0.5, linestyle='--', label='15% threshold')
    ax.axhline(y=-15, color='r', linewidth=0.5, linestyle='--')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # (c) Rt vs V
    ax = axes[1, 0]
    rt_calc = [r["rt_calc"] for r in suboff_results]
    rt_exp = [r["rt_exp"] for r in suboff_results]
    ax.plot(speeds, rt_exp, 'ro-', label='Experiment', markersize=8)
    ax.plot(speeds, rt_calc, 'bs--', label='Platform', markersize=8)
    ax.set_xlabel('Speed $V$ (m/s)')
    ax.set_ylabel('Total Resistance $R_T$ (N)')
    ax.set_title('(c) $R_T$ vs Speed')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # (d) Resistance components
    ax = axes[1, 1]
    # Recompute components
    rho = 998.0
    L, B, Cb = 4.356, 0.508, 0.542
    nu = 1.004e-6
    S = math.pi * B * L * (0.5 + 0.25 * Cb)
    g = 9.81

    rf_vals, rr_vals = [], []
    for v in speeds:
        Re = v * L / nu
        Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        Rf = 0.5 * rho * S * Cf * v ** 2
        Fn = v / math.sqrt(g * L)
        Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5
        Rr = 0.5 * rho * S * Cr * v ** 2
        rf_vals.append(Rf)
        rr_vals.append(Rr)

    ax.bar(np.array(speeds) - 0.1, rf_vals, 0.2, label='Frictional $R_F$ (ITTC-57)', color='steelblue')
    ax.bar(np.array(speeds) + 0.1, rr_vals, 0.2, label='Residual $R_R$ (empirical)', color='coral')
    ax.set_xlabel('Speed $V$ (m/s)')
    ax.set_ylabel('Resistance (N)')
    ax.set_title('(d) Resistance Components')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fig.savefig(output_dir / "suboff_verification.png", dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  Saved: {output_dir / 'suboff_verification.png'}")


def plot_stability_verification(stab_results: List[Dict], output_dir: Path):
    """Generate stability verification plots."""
    phi_deg = [r["phi_deg"] for r in stab_results]
    gz_calc = [r["gz_calc"] for r in stab_results]
    gz_linear = [r["gz_linear"] for r in stab_results]
    linear_err = [r["linear_err_pct"] for r in stab_results]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle("Stability Verification: Wall-Sided Formula (GM=1.0m, BM=3.0m)",
                 fontsize=14, fontweight='bold')

    # (a) GZ curve
    ax = axes[0]
    ax.plot(phi_deg, gz_calc, 'b-o', label='Wall-sided formula', markersize=8)
    ax.plot(phi_deg, gz_linear, 'r--s', label='Linear ($GM\\sin\\phi$)', markersize=6)
    ax.set_xlabel('Heel Angle $\\phi$ (deg)')
    ax.set_ylabel('Righting Arm $GZ$ (m)')
    ax.set_title('(a) GZ Curve')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # (b) Linear approximation error
    ax = axes[1]
    colors = ['green' if abs(e) < 5 else 'orange' if abs(e) < 15 else 'red' for e in linear_err]
    ax.bar(phi_deg, linear_err, color=colors, alpha=0.7, edgecolor='black')
    ax.set_xlabel('Heel Angle $\\phi$ (deg)')
    ax.set_ylabel('Linear Approx Error (%)')
    ax.set_title('(b) Error of Linear Approximation')
    ax.axhline(y=0, color='k', linewidth=0.5)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fig.savefig(output_dir / "stability_verification.png", dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  Saved: {output_dir / 'stability_verification.png'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("TensorPlatform Ship Performance Verification (m1604)")
    print("=" * 70)

    # Load benchmark
    bench = load_benchmark()
    print(f"\nBenchmark data version: {bench.get('version', '?')}")
    print(f"Total cases: {len(bench['cases'])}")

    # 1. Benchmark consistency
    print("\n" + "-" * 50)
    print("1. Benchmark Data Self-Consistency Check")
    print("-" * 50)
    consistency = verify_benchmark_consistency(bench)
    all_pass = True
    for r in consistency:
        if r["status"] == "SKIP":
            print(f"  {r['case_id']:13s}: SKIP ({r['reason']})")
        elif r["status"] == "PASS":
            print(f"  {r['case_id']:13s}: PASS (Rt err = {r['rel_err_pct']:.2f}%)")
        else:
            print(f"  {r['case_id']:13s}: FAIL (Rt err = {r['rel_err_pct']:.2f}%)")
            all_pass = False
    print(f"\n  Overall: {'ALL PASS' if all_pass else 'SOME FAILED'}")

    # 2. SUBOFF resistance verification
    print("\n" + "-" * 50)
    print("2. SUBOFF Resistance: Platform vs Experiment")
    print("-" * 50)
    suboff = verify_suboff_resistance(bench)
    print(f"  {'Case':10s} | {'V(m/s)':>6s} | {'Fn':>6s} | {'Ct_calc':>8s} | {'Ct_exp':>7s} | {'Ct_err%':>8s} | {'Rt_calc':>8s} | {'Rt_exp':>8s} | {'Rt_err%':>8s}")
    print("  " + "-" * 95)
    for r in suboff:
        ct_flag = "✓" if abs(r["ct_err_pct"]) < 15 else "✗"
        print(f"  {r['case_id']:10s} | {r['speed_ms']:6.1f} | {r['fn']:6.3f} | "
              f"{r['ct_calc']:8.5f} | {r['ct_exp']:7.4f} | {r['ct_err_pct']:+7.1f}{ct_flag} | "
              f"{r['rt_calc']:8.1f} | {r['rt_exp']:8.1f} | {r['rt_err_pct']:+7.1f}")

    # Summary
    ct_errors = [abs(r["ct_err_pct"]) for r in suboff]
    max_ct_err = max(ct_errors)
    avg_ct_err = sum(ct_errors) / len(ct_errors)
    within_15 = sum(1 for e in ct_errors if e < 15)
    print(f"\n  Max |Ct error|: {max_ct_err:.1f}%")
    print(f"  Avg |Ct error|: {avg_ct_err:.1f}%")
    print(f"  Cases within 15%: {within_15}/{len(suboff)}")

    # 3. Stability verification
    print("\n" + "-" * 50)
    print("3. Stability: Wall-Sided Formula Analytical Check")
    print("-" * 50)
    stability = verify_stability_analytical()
    print(f"  {'phi(deg)':>8s} | {'GZ_calc':>10s} | {'GZ_analytical':>13s} | {'err%':>8s} | {'GZ_linear':>10s} | {'linear_err%':>12s}")
    print("  " + "-" * 75)
    for r in stability:
        print(f"  {r['phi_deg']:8d} | {r['gz_calc']:10.6f} | {r['gz_analytical']:13.6f} | "
              f"{r['gz_err_pct']:8.4f} | {r['gz_linear']:10.6f} | {r['linear_err_pct']:+11.1f}")

    gz_max_err = max(r["gz_err_pct"] for r in stability)
    print(f"\n  Max GZ analytical error: {gz_max_err:.4f}% (should be ~0, formula is exact)")

    # 4. Generate plots
    print("\n" + "-" * 50)
    print("4. Generating Plots")
    print("-" * 50)
    plot_suboff_verification(suboff, OUTPUT_DIR)
    plot_stability_verification(stability, OUTPUT_DIR)

    # 5. Write JSON results
    results = {
        "verification_date": "2026-11-01",
        "benchmark_version": bench.get("version", "?"),
        "benchmark_consistency": consistency,
        "suboff_resistance": suboff,
        "stability_analytical": stability,
        "summary": {
            "benchmark_consistency_pass": all_pass,
            "suboff_max_ct_error_pct": round(max_ct_err, 1),
            "suboff_avg_ct_error_pct": round(avg_ct_err, 1),
            "suboff_cases_within_15pct": f"{within_15}/{len(suboff)}",
            "stability_max_gz_error_pct": round(gz_max_err, 4),
        }
    }

    results_path = OUTPUT_DIR / "verification_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved: {results_path}")

    # 6. Final verdict
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    print(f"  Benchmark consistency: {'PASS' if all_pass else 'FAIL'}")
    print(f"  SUBOFF Ct accuracy: max error = {max_ct_err:.1f}%, avg = {avg_ct_err:.1f}%")
    print(f"  SUBOFF within 15%: {within_15}/{len(suboff)} cases")
    print(f"  Stability (wall-sided): exact (err < {gz_max_err:.4f}%)")
    print()

    if max_ct_err > 15:
        print("  ⚠ WARNING: Platform resistance calculation exceeds 15% error at some speeds.")
        print("    Root cause: simplified Cr model (Cr ∝ Fn^2.5) not calibrated for high Fn.")
        print("    Recommendation: Implement Holtrop-Mennen full method or SUBOFF-specific Cr correlation.")
    else:
        print("  ✓ Platform resistance calculation within 15% for all SUBOFF cases.")

    print()
    return results


if __name__ == "__main__":
    main()
