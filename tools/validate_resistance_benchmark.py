#!/usr/bin/env python3
"""
Ship resistance benchmark validation — standalone script.

Computes resistance coefficients using the Holtrop/ITTC-57 method from
calculations.py and compares against experimental reference data.

Usage:
    python3 validate_resistance_benchmark.py [--plot] [--json]

Outputs:
    - Console table with case-by-case comparison
    - Optional PNG plot (matplotlib)
    - Optional JSON report

v1.0 (2026-11-01): Initial validation script for m1602 selfstart task.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Benchmark reference data (embedded from benchmark_reference.json)
# ---------------------------------------------------------------------------

BENCHMARK_CASES = [
    # SUBOFF (DARPA SUBOFF, Roddy 1990)
    {"case_id": "SUBOFF-01", "ship_type": "SUBOFF", "speed_ms": 3.0, "fn": 0.459, "exp_ct": 0.0038,
     "L": 4.356, "B": 0.508, "T": 0.508, "Cb": 0.508, "source": "Roddy 1990 (DTRC-90/012)"},
    {"case_id": "SUBOFF-02", "ship_type": "SUBOFF", "speed_ms": 4.0, "fn": 0.612, "exp_ct": 0.0040,
     "L": 4.356, "B": 0.508, "T": 0.508, "Cb": 0.508, "source": "Roddy 1990 (DTRC-90/012)"},
    {"case_id": "SUBOFF-03", "ship_type": "SUBOFF", "speed_ms": 5.0, "fn": 0.765, "exp_ct": 0.0042,
     "L": 4.356, "B": 0.508, "T": 0.508, "Cb": 0.508, "source": "Roddy 1990 (DTRC-90/012)"},
    {"case_id": "SUBOFF-04", "ship_type": "SUBOFF", "speed_ms": 6.0, "fn": 0.918, "exp_ct": 0.0044,
     "L": 4.356, "B": 0.508, "T": 0.508, "Cb": 0.508, "source": "Roddy 1990 (DTRC-90/012)"},
    # DTMB-5415 (ITTC V&V Workshop)
    {"case_id": "DTMB5415-01", "ship_type": "DTMB-5415", "speed_ms": 2.572, "fn": 0.061, "exp_ct": 0.0045,
     "L": 5.72, "B": 0.759, "T": 0.246, "Cb": 0.508, "source": "ITTC V&V Workshop 2010"},
    {"case_id": "DTMB5415-02", "ship_type": "DTMB-5415", "speed_ms": 5.144, "fn": 0.122, "exp_ct": 0.0038,
     "L": 5.72, "B": 0.759, "T": 0.246, "Cb": 0.508, "source": "ITTC V&V Workshop 2010"},
    {"case_id": "DTMB5415-03", "ship_type": "DTMB-5415", "speed_ms": 10.289, "fn": 0.244, "exp_ct": 0.0035,
     "L": 5.72, "B": 0.759, "T": 0.246, "Cb": 0.508, "source": "ITTC V&V Workshop 2010"},
    {"case_id": "DTMB5415-04", "ship_type": "DTMB-5415", "speed_ms": 15.433, "fn": 0.366, "exp_ct": 0.0038,
     "L": 5.72, "B": 0.759, "T": 0.246, "Cb": 0.508, "source": "ITTC V&V Workshop 2010"},
    # KVLCC2 (SIMMAN 2008)
    {"case_id": "KVLCC2-01", "ship_type": "KVLCC2", "speed_ms": 7.202, "fn": 0.041, "exp_ct": 0.0028,
     "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "source": "SIMMAN 2008"},
    {"case_id": "KVLCC2-02", "ship_type": "KVLCC2", "speed_ms": 7.717, "fn": 0.044, "exp_ct": 0.0030,
     "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "source": "SIMMAN 2008"},
    {"case_id": "KVLCC2-03", "ship_type": "KVLCC2", "speed_ms": 7.974, "fn": 0.045, "exp_ct": 0.0031,
     "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "source": "SIMMAN 2008"},
    {"case_id": "KVLCC2-04", "ship_type": "KVLCC2", "speed_ms": 8.231, "fn": 0.047, "exp_ct": 0.0032,
     "L": 320.0, "B": 58.0, "T": 20.8, "Cb": 0.8098, "source": "SIMMAN 2008"},
]

# ---------------------------------------------------------------------------
# Resistance calculation (matches calculations.py logic)
# ---------------------------------------------------------------------------

def calc_resistance_coefficient(
    speed_ms: float, length: float, beam: float, draft: float,
    cb: float, rho: float = 998.0, nu: float = 1.004e-6
) -> Dict[str, float]:
    """
    Compute resistance coefficients using ITTC-57 + simplified Holtrop.

    Returns dict with Cf, Cr, Ct, Re, Fn, S.
    """
    L, B, T, Cb, V = length, beam, draft, cb, speed_ms

    # Wetted surface (axisymmetric approximation)
    S = math.pi * B * L * (0.5 + 0.25 * Cb)

    # Reynolds number
    Re = V * L / nu

    # ITTC-1957 friction line
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    # Froude number
    g = 9.81
    Fn = V / math.sqrt(g * L)

    # Residual resistance coefficient (simplified Holtrop)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0

    # Total resistance coefficient
    Ct = Cf + Cr

    return {
        "S": S, "Re": Re, "Fn": Fn,
        "Cf": Cf, "Cr": Cr, "Ct": Ct,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def run_validation(cases: List[Dict] = None) -> List[Dict[str, Any]]:
    """Run validation for all cases and return results."""
    if cases is None:
        cases = BENCHMARK_CASES

    results = []
    for case in cases:
        calc = calc_resistance_coefficient(
            speed_ms=case["speed_ms"],
            length=case["L"],
            beam=case["B"],
            draft=case["T"],
            cb=case["Cb"],
        )
        exp_ct = case["exp_ct"]
        calc_ct = calc["Ct"]
        error_pct = abs(calc_ct - exp_ct) / exp_ct * 100.0 if exp_ct > 0 else 999.0
        passed = error_pct <= 15.0

        # Honest-FAIL detection
        honest_fail = False
        note = ""
        if case["ship_type"] == "SUBOFF" and case["fn"] > 0.4:
            honest_fail = True
            note = "HONEST-FAIL: Holtrop not valid for SUBOFF at Fr>0.4"

        results.append({
            "case_id": case["case_id"],
            "ship_type": case["ship_type"],
            "speed_ms": case["speed_ms"],
            "fn": case["fn"],
            "exp_ct": exp_ct,
            "calc_ct": round(calc_ct, 6),
            "cf": round(calc["Cf"], 6),
            "cr": round(calc["Cr"], 6),
            "error_pct": round(error_pct, 2),
            "pass": passed,
            "honest_fail": honest_fail,
            "note": note,
            "source": case["source"],
        })

    return results


def print_table(results: List[Dict]) -> None:
    """Print results as a formatted table."""
    header = f"{'Case':<14} {'Type':<12} {'V(m/s)':<8} {'Fn':<7} {'Ct_exp':<9} {'Ct_calc':<9} {'Error%':<8} {'Pass':<6}"
    print(header)
    print("-" * len(header))
    for r in results:
        status = "PASS" if r["pass"] else ("H-FAIL" if r["honest_fail"] else "FAIL")
        print(f"{r['case_id']:<14} {r['ship_type']:<12} {r['speed_ms']:<8.2f} {r['fn']:<7.3f} "
              f"{r['exp_ct']:<9.4f} {r['calc_ct']:<9.6f} {r['error_pct']:<8.1f} {status:<6}")

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    honest_fail = sum(1 for r in results if r["honest_fail"])
    print("-" * len(header))
    print(f"Total: {total} | Passed: {passed} | Failed: {total - passed} | Honest-FAIL: {honest_fail}")
    print(f"Pass rate: {passed/total*100:.1f}%")


def generate_plot(results: List[Dict], output_path: str = "resistance_validation.png") -> str:
    """Generate comparison plot: Ct_exp vs Ct_calc by ship type."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    ship_types = ["SUBOFF", "DTMB-5415", "KVLCC2"]
    colors = {"SUBOFF": "#e74c3c", "DTMB-5415": "#3498db", "KVLCC2": "#2ecc71"}

    for idx, st in enumerate(ship_types):
        ax = axes[idx]
        cases = [r for r in results if r["ship_type"] == st]
        speeds = [c["speed_ms"] for c in cases]
        exp_cts = [c["exp_ct"] for c in cases]
        calc_cts = [c["calc_ct"] for c in cases]

        x = np.arange(len(speeds))
        width = 0.35

        bars1 = ax.bar(x - width/2, exp_cts, width, label="Experimental", color=colors[st], alpha=0.7)
        bars2 = ax.bar(x + width/2, calc_cts, width, label="Holtrop/ITTC-57", color="gray", alpha=0.7)

        ax.set_xticks(x)
        ax.set_xticklabels([f"{s:.1f}" for s in speeds])
        ax.set_xlabel("Speed (m/s)")
        ax.set_ylabel("Ct")
        ax.set_title(f"{st}")
        ax.legend(fontsize=8)

        # Add error labels
        for i, c in enumerate(cases):
            ax.text(i + width/2, max(exp_cts[i], calc_cts[i]) * 1.05,
                    f"{c['error_pct']:.0f}%", ha='center', fontsize=7,
                    color='red' if not c['pass'] else 'green')

    plt.suptitle("Ship Resistance Validation: Experimental vs Holtrop/ITTC-57", fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    return output_path


def generate_json_report(results: List[Dict]) -> Dict:
    """Generate JSON report."""
    total = len(results)
    passed = sum(1 for r in results if r["pass"])

    return {
        "title": "Ship Resistance Benchmark Validation Report",
        "method": "ITTC-57 friction line + simplified Holtrop residual resistance",
        "pass_criterion": "error_pct <= 15%",
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate_pct": round(passed / total * 100, 1) if total > 0 else 0,
        "results": results,
        "honest_fail_note": (
            "SUBOFF cases at Fr>0.4 are outside Holtrop method validity range (Cb>0.55). "
            "These are documented as honest-FAIL, not algorithm bugs. "
            "Recommended upgrade: CFD (RANS) or slender-body theory for submarine hulls."
        ),
    }



# ===========================================================================
# m1608 段：简化 Holtrop 全案例验证（对标 backend/data/benchmark_reference.json）
# 与上段 m1602 系数法并存；CLI 经 --holtrop 分发
# ===========================================================================
BENCHMARK_PATH = Path(__file__).resolve().parent.parent / "backend" / "data" / "benchmark_reference.json"

# Physical constants
G = 9.81
RHO = 998.0       # water density kg/m³
NU = 1.004e-6     # kinematic viscosity m²/s

# Ship geometry presets (matching ship_benchmark.py)
SHIP_GEOMETRY = {
    "SUBOFF": {"length": 4.356, "beam": 0.508, "draft": 0.476, "cb": 0.542},
    "DTMB-5415": {"length": 5.720, "beam": 0.759, "draft": 0.246, "cb": 0.508},
    "KVLCC2": {"length": 320.0, "beam": 58.0, "draft": 20.8, "cb": 0.8098},
}

# ---------------------------------------------------------------------------
# Resistance calculation (mirrors backend/api/calculations.py)
# ---------------------------------------------------------------------------

def calc_resistance_holtrop_simplified(ship_type: str, speed_ms: float) -> Dict[str, float]:
    """
    Simplified Holtrop-Mennen resistance calculation.
    Matches the implementation in backend/api/calculations.py calc_resistance().
    """
    geo = SHIP_GEOMETRY[ship_type]
    L = geo["length"]
    B = geo["beam"]
    Cb = geo["cb"]

    # Wetted surface area (approximation for axisymmetric body)
    S = math.pi * B * L * (0.5 + 0.25 * Cb)

    # Reynolds number
    Re = speed_ms * L / NU

    # ITTC-1957 friction line
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2

    # Froude number
    Fn = speed_ms / math.sqrt(G * L)

    # Residual resistance coefficient (simplified)
    Cr = 0.002 * (1 + 2 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0

    # Total resistance coefficient
    Ct = Cf + Cr

    # Total resistance (N)
    Rt = 0.5 * RHO * S * Ct * speed_ms ** 2

    return {
        "S": S,
        "Re": Re,
        "Cf": Cf,
        "Fn": Fn,
        "Cr": Cr,
        "Ct": Ct,
        "Rt": Rt,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_all_cases() -> List[Dict[str, Any]]:
    """Run validation against all benchmark cases."""
    with open(BENCHMARK_PATH) as f:
        data = json.load(f)

    results = []
    for case in data["cases"]:
        cid = case["case_id"]
        st = case["ship_type"]
        speed = case.get("speed_ms", 0.0)
        Ct_exp = case["experimental_ct"]
        rt_status = case.get("rt_status", "")
        Rt_exp = case.get("experimental_rt_n")

        calc = calc_resistance_holtrop_simplified(st, speed)
        Ct_calc = calc["Ct"]
        Rt_calc = calc["Rt"]

        error_ct = abs(Ct_calc - Ct_exp) / Ct_exp * 100 if Ct_exp > 0 else 999.0
        if Rt_exp is None:
            # canon v3.0：experimental_rt_n=null（rt_status=WITHHELD）→ RT 维度不判
            error_rt = None
            pass_rt = None
        else:
            error_rt = abs(Rt_calc - Rt_exp) / Rt_exp * 100 if Rt_exp > 0 else 999.0
            pass_rt = error_rt <= 15.0

        results.append({
            "case_id": cid,
            "ship_type": st,
            "speed_ms": speed,
            "Fn": calc["Fn"],
            "Re": calc["Re"],
            "Cf": calc["Cf"],
            "Cr": calc["Cr"],
            "Ct_calc": Ct_calc,
            "Ct_exp": Ct_exp,
            "error_ct_pct": round(error_ct, 2),
            "Rt_calc": round(Rt_calc, 4),
            "Rt_exp": Rt_exp,
            "error_rt_pct": None if error_rt is None else round(error_rt, 2),
            "pass_ct": error_ct <= 15.0,
            "pass_rt": pass_rt,
            "rt_status": rt_status,
        })

    return results


# ---------------------------------------------------------------------------
# Figure generation
# ---------------------------------------------------------------------------

def generate_figures(results: List[Dict], output_dir: Path) -> List[Dict[str, str]]:
    """Generate Ct vs Fr comparison plots for each ship type."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import base64
    from io import BytesIO

    output_dir.mkdir(parents=True, exist_ok=True)
    figures = []

    ship_types = ["SUBOFF", "DTMB-5415", "KVLCC2"]
    titles = {
        "SUBOFF": "DARPA SUBOFF (L=4.356m, Cb=0.542)",
        "DTMB-5415": "DTMB Model 5415 (L=5.72m, Cb=0.508)",
        "KVLCC2": "KVLCC2 Tanker (L=320m, Cb=0.810)",
    }

    for st in ship_types:
        st_results = [r for r in results if r["ship_type"] == st]
        if not st_results:
            continue

        fig, ax = plt.subplots(figsize=(8, 5))

        fn_vals = [r["Fn"] for r in st_results]
        ct_calc = [r["Ct_calc"] for r in st_results]
        ct_exp = [r["Ct_exp"] for r in st_results]

        ax.plot(fn_vals, ct_exp, "ko-", label="Experimental (Roddy/ITTC/SIMMAN)", markersize=8, linewidth=2)
        ax.plot(fn_vals, ct_calc, "rs--", label="Holtrop-Mennen (simplified)", markersize=8, linewidth=2)

        # Add error annotations
        for r in st_results:
            color = "green" if r["pass_ct"] else "red"
            ax.annotate(
                f"{r['error_ct_pct']:.1f}%",
                (r["Fn"], r["Ct_calc"]),
                textcoords="offset points",
                xytext=(8, 8),
                fontsize=8,
                color=color,
                fontweight="bold",
            )

        ax.set_xlabel("Froude Number (Fr)", fontsize=12)
        ax.set_ylabel("Total Resistance Coefficient (Ct)", fontsize=12)
        ax.set_title(f"Ct vs Fr — {titles[st]}", fontsize=13, fontweight="bold")
        ax.legend(loc="best", fontsize=10)
        ax.grid(True, alpha=0.3)

        buf = BytesIO()
        plt.tight_layout()
        fig.savefig(buf, format="png", dpi=150)
        buf.seek(0)
        png_base64 = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)

        fname = f"ct_vs_fr_{st.lower().replace('-', '_')}.png"
        fpath = output_dir / fname
        with open(fpath, "wb") as f:
            f.write(base64.b64decode(png_base64))

        figures.append({
            "name": f"ct_vs_fr_{st}",
            "caption": f"Ct vs Froude Number for {titles[st]}. Red = calculated, Black = experimental. Annotations show % error.",
            "png_base64": png_base64,
            "filename": fname,
        })

    # Summary bar chart: error percentages
    fig, ax = plt.subplots(figsize=(10, 5))
    case_ids = [r["case_id"] for r in results]
    errors = [r["error_ct_pct"] for r in results]
    colors = ["green" if r["pass_ct"] else "red" for r in results]

    bars = ax.bar(range(len(case_ids)), errors, color=colors, edgecolor="black", linewidth=0.5)
    ax.axhline(y=15, color="orange", linestyle="--", linewidth=2, label="15% threshold")
    ax.set_xticks(range(len(case_ids)))
    ax.set_xticklabels(case_ids, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Error in Ct (%)", fontsize=12)
    ax.set_title("Resistance Calculation Error vs 15% Threshold", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis="y")

    buf = BytesIO()
    plt.tight_layout()
    fig.savefig(buf, format="png", dpi=150)
    buf.seek(0)
    png_base64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    fname = "error_summary_bar.png"
    fpath = output_dir / fname
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(png_base64))

    figures.append({
        "name": "error_summary",
        "caption": "Error percentage for each benchmark case. Green = pass (<15%), Red = fail (>15%).",
        "png_base64": png_base64,
        "filename": fname,
    })

    return figures


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def build_summary(results: List[Dict]) -> Dict[str, Any]:
    """Build summary statistics."""
    total = len(results)
    passed = sum(1 for r in results if r["pass_ct"])
    failed = total - passed

    by_type: Dict[str, Dict] = {}
    for st in ["SUBOFF", "DTMB-5415", "KVLCC2"]:
        st_results = [r for r in results if r["ship_type"] == st]
        if st_results:
            errors = [r["error_ct_pct"] for r in st_results]
            by_type[st] = {
                "total": len(st_results),
                "passed": sum(1 for r in st_results if r["pass_ct"]),
                "failed": sum(1 for r in st_results if not r["pass_ct"]),
                "mean_error_pct": round(sum(errors) / len(errors), 2),
                "max_error_pct": round(max(errors), 2),
                "min_error_pct": round(min(errors), 2),
            }

    return {
        "total_cases": total,
        "passed": passed,
        "failed": failed,
        "pass_rate_pct": round(passed / total * 100, 1) if total > 0 else 0,
        "by_ship_type": by_type,
        "overall_mean_error_pct": round(sum(r["error_ct_pct"] for r in results) / total, 2),
        "overall_max_error_pct": round(max(r["error_ct_pct"] for r in results), 2),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def holtrop_summary_cli():
    output_dir = Path("outputs/resistance_validation")
    if "--output-dir" in sys.argv:
        idx = sys.argv.index("--output-dir")
        output_dir = Path(sys.argv[idx + 1])

    print("=" * 60)
    print("Resistance Calculation Benchmark Validation")
    print("=" * 60)

    results = validate_all_cases()
    summary = build_summary(results)
    figures = generate_figures(results, output_dir)

    # Print table
    print(f"\n{'Case':>14} {'Ship':>10} {'Fr':>6} {'Ct_calc':>9} {'Ct_exp':>8} {'Err%':>7} {'Pass':>5}")
    print("-" * 65)
    for r in results:
        status = "PASS" if r["pass_ct"] else "FAIL"
        print(f"{r['case_id']:>14} {r['ship_type']:>10} {r['Fn']:6.3f} {r['Ct_calc']:9.6f} {r['Ct_exp']:8.4f} {r['error_ct_pct']:6.1f}% {status:>5}")

    print(f"\n--- Summary ---")
    print(f"Total: {summary['total_cases']}, Passed: {summary['passed']}, Failed: {summary['failed']}")
    print(f"Pass rate: {summary['pass_rate_pct']}%")
    print(f"Overall mean error: {summary['overall_mean_error_pct']}%")
    print(f"Overall max error: {summary['overall_max_error_pct']}%")

    for st, stats in summary["by_ship_type"].items():
        print(f"\n  {st}: {stats['passed']}/{stats['total']} pass, mean err={stats['mean_error_pct']}%, max err={stats['max_error_pct']}%")

    # Save JSON
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "validation_results.json"
    with open(json_path, "w") as f:
        json.dump({"results": results, "summary": summary}, f, indent=2)
    print(f"\nResults saved to: {json_path}")

    fig_paths = [output_dir / fig["filename"] for fig in figures]
    print(f"Figures saved to: {output_dir}/")
    for fp in fig_paths:
        print(f"  {fp}")

    return results, summary, figures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ship resistance benchmark validation")
    parser.add_argument("--holtrop", action="store_true",
                        help="Run the m1608 simplified-Holtrop full-case validation "
                             "against backend/data/benchmark_reference.json (canon)")
    parser.add_argument("--plot", action="store_true", help="Generate comparison plot")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--ship-type", choices=["SUBOFF", "DTMB-5415", "KVLCC2", "all"],
                        default="all", help="Filter by ship type")
    args = parser.parse_args()

    if args.holtrop:
        holtrop_summary_cli()
        raise SystemExit(0)

    # Filter cases
    if args.ship_type != "all":
        cases = [c for c in BENCHMARK_CASES if c["ship_type"] == args.ship_type]
    else:
        cases = BENCHMARK_CASES

    # Run validation
    results = run_validation(cases)

    # Print table
    print_table(results)

    # Generate plot
    if args.plot:
        plot_path = generate_plot(results)
        print(f"\nPlot saved to: {plot_path}")

    # Generate JSON
    if args.json:
        report = generate_json_report(results)
        print(json.dumps(report, indent=2))
