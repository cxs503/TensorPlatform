#!/usr/bin/env python3
"""
SUBOFF Resistance Verification — m1764

Verifies the platform's calc.resistance (REAL) capability against
experimental reference data for DARPA SUBOFF (Roddy 1990).

Produces:
1. Error analysis table (Ct_calc vs Ct_exp, Rt_calc vs Rt_exp)
2. Ct vs Froude number comparison plot
3. Error vs Froude number plot
4. JSON results file for downstream consumption

All numbers are programmatically generated — zero hand-copying.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[2]  # work/m1764
BENCHMARK = REPO / "backend" / "data" / "benchmark_reference.json"
OUTPUT_DIR = REPO / "outputs" / "m1764"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Load benchmark data (only DERIVED cases)
# ---------------------------------------------------------------------------
def load_derived_cases():
    with open(BENCHMARK, encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for c in data["cases"]:
        if c.get("reference_status") == "DERIVED":
            cases.append(c)
    return cases

# ---------------------------------------------------------------------------
# Resistance calculation (replicates backend/api/calculations.py logic)
# ---------------------------------------------------------------------------
def calc_resistance(length, beam, draft, cb, speed_ms, rho, nu,
                    wetted_surface_m2=None, form_factor=1.0):
    """Compute resistance using the platform's ITTC-57 + empirical residual method."""
    L, B, T, Cb = length, beam, draft, cb
    V = speed_ms

    # Wetted surface
    if wetted_surface_m2 is not None:
        S = wetted_surface_m2
        S_source = "input_validated"
    else:
        S = math.pi * B * L * (0.5 + 0.25 * Cb)
        S_source = "empirical_formula"

    # Reynolds number
    Re = V * L / nu

    # ITTC-1957 friction line
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    # Frictional resistance
    Rf = 0.5 * rho * S * Cf * V ** 2

    # Form resistance
    k = form_factor - 1.0
    Rform = k * Rf

    # Froude number
    g = 9.81
    Fn = V / math.sqrt(g * L)

    # Residual resistance coefficient (empirical, SUBOFF-calibrated)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2

    # Total resistance
    Rt = Rf + Rform + Rr

    # Total resistance coefficient
    Ct = Rt / (0.5 * rho * S * V ** 2)

    return {
        "S": S, "S_source": S_source,
        "Re": Re, "Cf": Cf, "Fn": Fn, "Cr": Cr,
        "Rf": Rf, "Rform": Rform, "Rr": Rr, "Rt": Rt, "Ct": Ct,
    }

# ---------------------------------------------------------------------------
# Main verification
# ---------------------------------------------------------------------------
def main():
    cases = load_derived_cases()
    if not cases:
        print("ERROR: No DERIVED cases found in benchmark_reference.json")
        sys.exit(1)

    print(f"Loaded {len(cases)} DERIVED cases from benchmark_reference.json")

    # SUBOFF geometry from benchmark
    suboff_geom = cases[0]["geometry"]
    L = suboff_geom["length_m"]
    D = suboff_geom["diameter_m"]
    S_exp = suboff_geom["wetted_surface_m2"]

    # Fluid properties (fresh water 20°C, per benchmark v3.0)
    rho = 998.2
    nu = 1.004e-6

    results = []
    for case in cases:
        cid = case["case_id"]
        V = case["speed_ms"]
        Ct_exp = case["experimental_ct"]
        Rt_exp = case["experimental_rt_n"]

        # Calculate with validated wetted surface
        r = calc_resistance(L, D, D, 0.542, V, rho, nu,
                           wetted_surface_m2=S_exp, form_factor=1.0)

        # Calculate with empirical wetted surface
        r_emp = calc_resistance(L, D, D, 0.542, V, rho, nu,
                               wetted_surface_m2=None, form_factor=1.0)

        err_ct = abs(r["Ct"] - Ct_exp) / Ct_exp * 100.0
        err_rt = abs(r["Rt"] - Rt_exp) / Rt_exp * 100.0
        err_ct_emp = abs(r_emp["Ct"] - Ct_exp) / Ct_exp * 100.0

        pass_15 = err_ct < 15.0

        results.append({
            "case_id": cid,
            "speed_ms": V,
            "Fn": r["Fn"],
            "Ct_exp": Ct_exp,
            "Ct_calc": r["Ct"],
            "Ct_calc_empirical_S": r_emp["Ct"],
            "error_Ct_pct": round(err_ct, 2),
            "error_Ct_empirical_S_pct": round(err_ct_emp, 2),
            "Rt_exp": Rt_exp,
            "Rt_calc": r["Rt"],
            "error_Rt_pct": round(err_rt, 2),
            "S_validated": S_exp,
            "S_empirical": r_emp["S"],
            "pass_15pct": pass_15,
        })

        status = "PASS" if pass_15 else "FAIL"
        print(f"  {cid}: V={V} m/s, Fn={r['Fn']:.4f}, "
              f"Ct_calc={r['Ct']:.6f}, Ct_exp={Ct_exp}, "
              f"err={err_ct:.1f}% [{status}]")

    # Summary
    n_pass = sum(1 for r in results if r["pass_15pct"])
    n_total = len(results)
    avg_err = np.mean([r["error_Ct_pct"] for r in results])
    max_err = max(r["error_Ct_pct"] for r in results)

    print(f"\nSummary: {n_pass}/{n_total} passed (<15% error), "
          f"avg error = {avg_err:.1f}%, max error = {max_err:.1f}%")

    # Save JSON results
    results_path = OUTPUT_DIR / "suboff_resistance_results.json"
    with open(results_path, "w") as f:
        json.dump({
            "mission": "m1764",
            "title": "SUBOFF Resistance Verification against Experimental Data",
            "benchmark_version": "3.0",
            "fluid": {"rho": rho, "nu": nu, "source": "fresh_water_20c"},
            "geometry": {"L": L, "D": D, "S_validated": S_exp},
            "method": "ITTC-1957 friction + empirical residual (Cr = 0.002*(1+2*Cb)*Fn^2.5)",
            "results": results,
            "summary": {
                "n_cases": n_total,
                "n_pass": n_pass,
                "n_fail": n_total - n_pass,
                "pass_rate": round(n_pass / n_total * 100, 1),
                "avg_error_pct": round(avg_err, 2),
                "max_error_pct": round(max_err, 2),
            },
        }, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to: {results_path}")

    # -----------------------------------------------------------------------
    # Generate figures
    # -----------------------------------------------------------------------
    _generate_figures(results, L, D, S_exp, rho, nu)

    return results

def _generate_figures(results, L, D, S_exp, rho, nu):
    """Generate Ct vs Fn and error analysis plots."""
    fns = [r["Fn"] for r in results]
    ct_exp = [r["Ct_exp"] for r in results]
    ct_calc = [r["Ct_calc"] for r in results]
    ct_calc_emp = [r["Ct_calc_empirical_S"] for r in results]
    errors = [r["error_Ct_pct"] for r in results]
    errors_emp = [r["error_Ct_empirical_S_pct"] for r in results]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Left: Ct vs Fn
    ax1 = axes[0]
    ax1.plot(fns, ct_exp, 'ko-', markersize=8, label='Experimental (Roddy 1990)', linewidth=2)
    ax1.plot(fns, ct_calc, 'bs--', markersize=8, label='Platform (validated S)', linewidth=2)
    ax1.plot(fns, ct_calc_emp, 'r^:', markersize=8, label='Platform (empirical S)', linewidth=2)
    ax1.set_xlabel('Froude Number (Fn)', fontsize=12)
    ax1.set_ylabel('Total Resistance Coefficient (Ct)', fontsize=12)
    ax1.set_title('SUBOFF: Ct vs Froude Number', fontsize=13, fontweight='bold')
    ax1.legend(fontsize=10, loc='upper left')
    ax1.grid(True, alpha=0.3)
    for i, r in enumerate(results):
        ax1.annotate(r["case_id"].split("-")[1], (fns[i], ct_calc[i]),
                    textcoords="offset points", xytext=(8, 5), fontsize=8)

    # Right: Error vs Fn
    ax2 = axes[1]
    ax2.plot(fns, errors, 'bs-', markersize=8, label='Validated S', linewidth=2)
    ax2.plot(fns, errors_emp, 'r^--', markersize=8, label='Empirical S', linewidth=2)
    ax2.axhline(y=15.0, color='red', linestyle='--', alpha=0.7, label='15% threshold')
    ax2.set_xlabel('Froude Number (Fn)', fontsize=12)
    ax2.set_ylabel('Relative Error (%)', fontsize=12)
    ax2.set_title('SUBOFF: Ct Error vs Froude Number', fontsize=13, fontweight='bold')
    ax2.legend(fontsize=10, loc='upper left')
    ax2.grid(True, alpha=0.3)
    for i, r in enumerate(results):
        status = "PASS" if r["pass_15pct"] else "FAIL"
        ax2.annotate(status, (fns[i], errors[i]),
                    textcoords="offset points", xytext=(8, 5), fontsize=8,
                    color='green' if r["pass_15pct"] else 'red')

    plt.tight_layout()
    fig_path = OUTPUT_DIR / "suboff_resistance_comparison.png"
    fig.savefig(fig_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"Figure saved to: {fig_path}")

    # Second figure: Resistance breakdown
    fig2, ax = plt.subplots(figsize=(10, 6))
    speeds = [r["speed_ms"] for r in results]
    # Recalculate breakdown for validated S
    breakdown = []
    for r in results:
        V = r["speed_ms"]
        Re = V * L / nu
        Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
        Fn = V / math.sqrt(9.81 * L)
        Cr = 0.002 * (1 + 2.0 * 0.542) * Fn ** 2.5 if Fn > 0.05 else 0.0
        Rf = 0.5 * rho * S_exp * Cf * V ** 2
        Rr = 0.5 * rho * S_exp * Cr * V ** 2
        breakdown.append({"Rf": Rf, "Rr": Rr, "Rt": Rf + Rr})

    x = np.arange(len(speeds))
    width = 0.35
    bars1 = ax.bar(x - width/2, [b["Rf"] for b in breakdown], width, label='Frictional (Rf)', color='steelblue')
    bars2 = ax.bar(x + width/2, [b["Rr"] for b in breakdown], width, label='Residual (Rr)', color='coral')
    ax.plot(x, [b["Rt"] for b in breakdown], 'ko-', markersize=8, label='Total (Rt)', linewidth=2)
    ax.plot(x, [r["Rt_exp"] for r in results], 'g*-', markersize=10, label='Experimental', linewidth=2)
    ax.set_xlabel('Speed (m/s)', fontsize=12)
    ax.set_ylabel('Resistance (N)', fontsize=12)
    ax.set_title('SUBOFF: Resistance Breakdown vs Speed', fontsize=13, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s}" for s in speeds])
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    fig2_path = OUTPUT_DIR / "suboff_resistance_breakdown.png"
    fig2.savefig(fig2_path, dpi=150, bbox_inches='tight')
    plt.close(fig2)
    print(f"Figure saved to: {fig2_path}")


if __name__ == "__main__":
    main()
