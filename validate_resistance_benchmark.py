#!/usr/bin/env python3
"""
Ship Resistance Benchmark Validation — m1479

Validates the Holtrop/ITTC-57 resistance calculation in TensorPlatform
against experimental reference data for three benchmark hulls:
- DARPA SUBOFF (axisymmetric body, Roddy 1990)
- DTMB-5415 (surface combatant, ITTC V&V)
- KVLCC2 (VLCC tanker, SIMMAN 2008)

Produces:
1. Error analysis table (calc Ct vs exp Ct, error %)
2. Ct vs Froude number comparison plots
3. Pass/fail summary per hull type
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Benchmark data
# ---------------------------------------------------------------------------
BENCHMARK_PATH = BACKEND_DIR / "data" / "benchmark_reference.json"

def load_benchmark():
    with open(BENCHMARK_PATH) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Ship geometry presets
# ---------------------------------------------------------------------------
SHIP_GEOMETRY = {
    "SUBOFF": {
        "length": 4.356,
        "beam": 0.508,
        "draft": 0.508,
        "cb": 0.508,
        "wetted_surface": 5.42,  # from benchmark data
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
    },
}

# ---------------------------------------------------------------------------
# Resistance calculation (replicates api/calculations.py logic)
# ---------------------------------------------------------------------------
def calc_resistance(ship_type: str, speed_ms: float, geometry: dict) -> dict:
    """Compute resistance using Holtrop/ITTC-57 simplified method."""
    L = geometry["length"]
    B = geometry["beam"]
    T = geometry["draft"]
    Cb = geometry["cb"]
    V = speed_ms
    rho = 998.0  # fresh water
    nu = 1.004e-6

    # Wetted surface area
    if "wetted_surface" in geometry and geometry["wetted_surface"] > 0:
        S = geometry["wetted_surface"]
    else:
        S = math.pi * B * L * (0.5 + 0.25 * Cb)

    # Reynolds number
    Re = V * L / nu

    # ITTC-1957 friction line
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    # Frictional resistance
    Rf = 0.5 * rho * S * Cf * V ** 2

    # Froude number
    g = 9.81
    Fn = V / math.sqrt(g * L)

    # Residual resistance coefficient (simplified Holtrop-like)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2

    Rt = Rf + Rr

    # Total resistance coefficient: Ct = Rt / (0.5 * rho * S * V^2)
    dynamic_pressure = 0.5 * rho * S * V ** 2
    Ct = Rt / dynamic_pressure if dynamic_pressure > 0 else 0.0

    return {
        "Re": Re,
        "Fn": Fn,
        "Cf": Cf,
        "Cr": Cr,
        "Ct": Ct,
        "Rf": Rf,
        "Rr": Rr,
        "Rt": Rt,
        "S": S,
    }

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def run_validation():
    data = load_benchmark()
    cases = data["cases"]

    results = []
    by_type = {}

    for case in cases:
        ship_type = case["ship_type"]
        case_id = case["case_id"]
        speed_ms = case.get("speed_ms", 0.0)
        ref_ct = case["experimental_ct"]
        fn = case.get("froude_number", 0.0)

        geo = SHIP_GEOMETRY.get(ship_type, {})
        if not geo:
            continue

        calc = calc_resistance(ship_type, speed_ms, geo)
        calc_ct = calc["Ct"]

        if ref_ct > 0:
            error_pct = abs(calc_ct - ref_ct) / ref_ct * 100.0
        else:
            error_pct = 999.0

        passed = error_pct <= 15.0

        result = {
            "case_id": case_id,
            "ship_type": ship_type,
            "speed_ms": speed_ms,
            "Fn": fn,
            "ref_ct": ref_ct,
            "calc_ct": round(calc_ct, 6),
            "error_pct": round(error_pct, 2),
            "passed": passed,
            "note": "",
        }

        # Honest-FAIL annotation
        if ship_type == "SUBOFF" and fn > 0.4:
            result["note"] = "HONEST-FAIL: Holtrop calibrated for Cb>0.55; SUBOFF at Fr>0.4 outside validity"

        results.append(result)
        by_type.setdefault(ship_type, []).append(result)

    return results, by_type

# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def generate_figures(by_type, output_dir):
    """Generate Ct vs Fn comparison plots."""
    os.makedirs(output_dir, exist_ok=True)

    colors = {"SUBOFF": "#e74c3c", "DTMB-5415": "#3498db", "KVLCC2": "#2ecc71"}
    markers = {"SUBOFF": "o", "DTMB-5415": "s", "KVLCC2": "^"}

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    for idx, (ship_type, cases) in enumerate(by_type.items()):
        ax = axes[idx]
        fn_exp = [c["Fn"] for c in cases]
        ct_exp = [c["ref_ct"] for c in cases]
        fn_calc = [c["Fn"] for c in cases]
        ct_calc = [c["calc_ct"] for c in cases]

        color = colors.get(ship_type, "#333")
        marker = markers.get(ship_type, "o")

        ax.plot(fn_exp, ct_exp, marker=marker, color=color, linewidth=2,
                markersize=8, label="Experimental", zorder=3)
        ax.plot(fn_calc, ct_calc, marker="x", color=color, linewidth=1,
                markersize=8, label="Holtrop/ITTC-57", linestyle="--", zorder=2)

        # Error bars annotation
        for c in cases:
            ax.annotate(f"{c['error_pct']:.0f}%",
                       (c["Fn"], c["calc_ct"]),
                       textcoords="offset points", xytext=(5, 5),
                       fontsize=7, color=color, alpha=0.8)

        ax.set_xlabel("Froude Number (Fn)")
        ax.set_ylabel("Total Resistance Coefficient (Ct)")
        ax.set_title(f"{ship_type}")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    png_path = os.path.join(output_dir, "resistance_benchmark_ct_vs_fn.png")
    plt.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close()

    # Error bar chart
    fig2, ax2 = plt.subplots(figsize=(10, 5))
    all_cases = []
    for cases in by_type.values():
        all_cases.extend(cases)

    labels = [c["case_id"] for c in all_cases]
    errors = [c["error_pct"] for c in all_cases]
    bar_colors = ["#27ae60" if c["passed"] else "#e74c3c" for c in all_cases]

    x = np.arange(len(labels))
    bars = ax2.bar(x, errors, color=bar_colors, edgecolor="white", linewidth=0.5)
    ax2.axhline(y=15.0, color="red", linestyle="--", linewidth=1.5, label="15% threshold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax2.set_ylabel("Error (%)")
    ax2.set_title("Resistance Calculation Error vs 15% Threshold")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    png_path2 = os.path.join(output_dir, "resistance_benchmark_errors.png")
    plt.savefig(png_path2, dpi=150, bbox_inches="tight")
    plt.close()

    return png_path, png_path2

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------
def summary_table(results, by_type):
    lines = []
    lines.append("| Case ID | Ship Type | Fn | Ct_exp | Ct_calc | Error% | Pass |")
    lines.append("|---------|-----------|-----|--------|---------|--------|------|")
    for r in results:
        status = "✅" if r["passed"] else "❌"
        lines.append(f"| {r['case_id']} | {r['ship_type']} | {r['Fn']:.3f} | {r['ref_ct']:.4f} | {r['calc_ct']:.4f} | {r['error_pct']:.1f}% | {status} |")

    lines.append("")
    lines.append("## Summary by Hull Type")
    lines.append("")
    lines.append("| Ship Type | Cases | Passed | Failed | Pass Rate | Avg Error% |")
    lines.append("|-----------|-------|--------|--------|-----------|------------|")

    for ship_type, cases in by_type.items():
        n = len(cases)
        passed = sum(1 for c in cases if c["passed"])
        failed = n - passed
        rate = passed / n * 100 if n > 0 else 0
        avg_err = sum(c["error_pct"] for c in cases) / n if n > 0 else 0
        lines.append(f"| {ship_type} | {n} | {passed} | {failed} | {rate:.0f}% | {avg_err:.1f}% |")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    output_dir = os.path.join(os.path.dirname(__file__), "outputs", "m1479")
    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print("Ship Resistance Benchmark Validation — m1479")
    print("=" * 60)

    results, by_type = run_validation()

    # Generate figures
    fig1, fig2 = generate_figures(by_type, output_dir)
    print(f"\nFigures saved to: {output_dir}")

    # Print summary table
    table = summary_table(results, by_type)
    print(table)

    # Overall stats
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    avg_error = sum(r["error_pct"] for r in results) / total if total > 0 else 0

    print(f"\nOverall: {passed}/{total} passed ({passed/total*100:.0f}%), avg error = {avg_error:.1f}%")

    # Save results JSON
    output_json = os.path.join(output_dir, "benchmark_results.json")
    with open(output_json, "w") as f:
        json.dump({
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
            "avg_error_pct": round(avg_error, 2),
            "results": results,
            "summary_by_type": {
                st: {
                    "cases": len(cs),
                    "passed": sum(1 for c in cs if c["passed"]),
                    "failed": sum(1 for c in cs if not c["passed"]),
                    "avg_error_pct": round(sum(c["error_pct"] for c in cs) / len(cs), 2),
                }
                for st, cs in by_type.items()
            }
        }, f, indent=2)
    print(f"\nResults saved to: {output_json}")
