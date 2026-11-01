#!/usr/bin/env python3
"""
SUBOFF 阻力计算全面验证 — m1775

验证平台 resistance 计算端点对 DARPA SUBOFF 模型的预测精度，
对标 Roddy (1990) 实验数据（经 m1561 基准裁决后的 DERIVED 行）。

本验证覆盖：
1. ITTC-1957 摩擦线闭式验证（Re = 1e5 … 1e10）
2. SUBOFF 四工况 Ct/Rt 计算 vs 实验基准
3. 湿表面积经验式偏差量化
4. Cr 经验式在不同 Fn 段的误差分析
5. 几何输入通道（wetted_surface_m2）有效性验证

所有数字程序化产出，零手抄。
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
# Configuration
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts" / "verif" / "m1775"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# SUBOFF geometry (from benchmark_reference.json, m1561 adjudicated)
SUBOFF = {
    "length": 4.356,
    "beam": 0.508,
    "draft": 0.476,
    "cb": 0.542,
    "wetted_surface_validated": 5.42,  # m2, from Roddy 1990
}

# Fluid properties (fresh water 20C, per m1561 canon)
RHO = 998.0
NU = 1.004e-6
G = 9.81

# SUBOFF benchmark cases (DERIVED status, m1561 adjudicated)
SUBOFF_CASES = [
    {"case_id": "SUBOFF-01", "speed_ms": 3.0, "ct_exp": 0.00380, "rt_exp_n": 92.52},
    {"case_id": "SUBOFF-02", "speed_ms": 4.0, "ct_exp": 0.00400, "rt_exp_n": 173.13},
    {"case_id": "SUBOFF-03", "speed_ms": 5.0, "ct_exp": 0.00420, "rt_exp_n": 284.04},
    {"case_id": "SUBOFF-04", "speed_ms": 6.0, "ct_exp": 0.00440, "rt_exp_n": 428.49},
]

# ---------------------------------------------------------------------------
# Resistance calculation (replicates backend/api/calculations.py logic)
# ---------------------------------------------------------------------------

def empirical_wetted_surface(length, beam, cb):
    """Legacy wetted-surface estimate: S = pi * B * L * (0.5 + 0.25 * Cb)"""
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def calc_resistance(speed_ms, length, beam, draft, cb, rho, nu,
                    wetted_surface_m2=None, form_factor=1.0):
    """Compute resistance using the platform's ITTC-57 + empirical Cr method."""
    L, B, T, Cb, V = length, beam, draft, cb, speed_ms

    if wetted_surface_m2 is not None:
        S = wetted_surface_m2
        S_source = "input_validated"
    else:
        S = empirical_wetted_surface(L, B, Cb)
        S_source = "empirical_formula"

    Re = V * L / nu
    Cf = 0.075 / (math.log10(Re) - 2.0) ** 2
    Rf = 0.5 * rho * S * Cf * V ** 2

    k = form_factor - 1.0
    Rform = k * Rf

    Fn = V / math.sqrt(G * L)
    Cr = 0.002 * (1 + 2.0 * Cb) * Fn ** 2.5 if Fn > 0.05 else 0.0
    Rr = 0.5 * rho * S * Cr * V ** 2

    Rt = Rf + Rform + Rr
    Ct = Rt / (0.5 * rho * S * V ** 2) if V > 0 else 0.0

    return {
        "S": S, "S_source": S_source,
        "Re": Re, "Cf": Cf, "Fn": Fn, "Cr": Cr,
        "Rf": Rf, "Rform": Rform, "Rr": Rr, "Rt": Rt, "Ct": Ct,
    }


def ittc_57_cf(reynolds):
    """ITTC-1957 friction line closed form."""
    return 0.075 / (math.log10(reynolds) - 2.0) ** 2


# ---------------------------------------------------------------------------
# Verification 1: ITTC-57 friction line
# ---------------------------------------------------------------------------

def verify_ittc57():
    """Verify ITTC-57 friction line against closed-form values."""
    re_values = [1e5, 1e6, 1e7, 1e8, 1e9, 1e10]
    # Reference values from ITTC-1957 standard
    cf_ref = [0.00833333, 0.00468750, 0.00300000, 0.00208333, 0.00153061, 0.00117188]

    results = []
    for re_val, ref in zip(re_values, cf_ref):
        cf_calc = ittc_57_cf(re_val)
        err = abs(cf_calc - ref) / ref * 100 if ref > 0 else 999
        results.append({
            "Re": re_val, "Cf_calc": cf_calc, "Cf_ref": ref,
            "error_pct": err, "passed": err < 0.001,
        })
    return results


# ---------------------------------------------------------------------------
# Verification 2: SUBOFF resistance
# ---------------------------------------------------------------------------

def verify_suboff():
    """Verify SUBOFF resistance calculation against experimental data."""
    results_validated = []
    results_empirical = []

    for case in SUBOFF_CASES:
        # With geometry-validated S
        res_v = calc_resistance(
            speed_ms=case["speed_ms"],
            length=SUBOFF["length"], beam=SUBOFF["beam"],
            draft=SUBOFF["draft"], cb=SUBOFF["cb"],
            rho=RHO, nu=NU,
            wetted_surface_m2=SUBOFF["wetted_surface_validated"],
        )
        ct_err_v = (res_v["Ct"] - case["ct_exp"]) / case["ct_exp"] * 100
        rt_err_v = (res_v["Rt"] - case["rt_exp_n"]) / case["rt_exp_n"] * 100

        results_validated.append({
            "case_id": case["case_id"],
            "speed_ms": case["speed_ms"],
            "Fn": res_v["Fn"],
            "Ct_calc": res_v["Ct"],
            "Ct_exp": case["ct_exp"],
            "Ct_error_pct": ct_err_v,
            "Rt_calc": res_v["Rt"],
            "Rt_exp": case["rt_exp_n"],
            "Rt_error_pct": rt_err_v,
            "S": res_v["S"],
            "S_source": res_v["S_source"],
            "Cf": res_v["Cf"],
            "Cr": res_v["Cr"],
            "passed_15": abs(ct_err_v) <= 15.0,
            "passed_3": abs(ct_err_v) <= 3.0,
        })

        # With empirical S
        res_e = calc_resistance(
            speed_ms=case["speed_ms"],
            length=SUBOFF["length"], beam=SUBOFF["beam"],
            draft=SUBOFF["draft"], cb=SUBOFF["cb"],
            rho=RHO, nu=NU,
            wetted_surface_m2=None,
        )
        ct_err_e = (res_e["Ct"] - case["ct_exp"]) / case["ct_exp"] * 100
        rt_err_e = (res_e["Rt"] - case["rt_exp_n"]) / case["rt_exp_n"] * 100

        results_empirical.append({
            "case_id": case["case_id"],
            "speed_ms": case["speed_ms"],
            "Fn": res_e["Fn"],
            "Ct_calc": res_e["Ct"],
            "Ct_exp": case["ct_exp"],
            "Ct_error_pct": ct_err_e,
            "Rt_calc": res_e["Rt"],
            "Rt_exp": case["rt_exp_n"],
            "Rt_error_pct": rt_err_e,
            "S": res_e["S"],
            "S_source": res_e["S_source"],
        })

    return results_validated, results_empirical


# ---------------------------------------------------------------------------
# Verification 3: Wetted surface bias
# ---------------------------------------------------------------------------

def verify_wetted_surface():
    """Quantify wetted surface empirical formula bias."""
    shapes = [
        {"name": "cylinder", "L": 4.356, "B": 0.508, "Cb": math.pi / 4,
         "S_closed": math.pi * 0.508 * 4.356},
        {"name": "prolate_spheroid_b/a=0.25", "L": 4.356, "B": 0.508, "Cb": 0.5236,
         "S_closed": None},  # computed below
        {"name": "prolate_spheroid_b/a=0.15", "L": 4.356, "B": 0.3048, "Cb": 0.5236,
         "S_closed": None},
    ]

    # Prolate spheroid surface area: S = 2*pi*b^2 + 2*pi*a*b*asin(e)/e
    # where a = L/2, b = B/2, e = sqrt(1 - (b/a)^2)
    for s in shapes:
        if "spheroid" in s["name"]:
            a = s["L"] / 2
            b = s["B"] / 2
            e = math.sqrt(1 - (b / a) ** 2)
            if e > 0:
                s["S_closed"] = 2 * math.pi * b ** 2 + 2 * math.pi * a * b * math.asin(e) / e
            else:
                s["S_closed"] = 4 * math.pi * a ** 2  # sphere limit

        s["S_empirical"] = empirical_wetted_surface(s["L"], s["B"], s["Cb"])
        s["error_pct"] = (s["S_empirical"] - s["S_closed"]) / s["S_closed"] * 100

    return shapes


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def generate_figures(suboff_validated, suboff_empirical, ittc_results, ws_results):
    """Generate verification figures."""
    figures = []

    # Figure 1: Ct vs Fn comparison
    fig1, ax1 = plt.subplots(figsize=(10, 6))

    fn_exp = [c["Fn"] for c in suboff_validated]
    ct_exp = [c["Ct_exp"] for c in suboff_validated]
    ct_calc = [c["Ct_calc"] for c in suboff_validated]
    errors = [c["Ct_error_pct"] for c in suboff_validated]

    ax1.plot(fn_exp, ct_exp, "o-", color="#e74c3c", linewidth=2, markersize=10,
             label="Experimental (Roddy 1990)", zorder=3)
    ax1.plot(fn_exp, ct_calc, "s--", color="#3498db", linewidth=2, markersize=8,
             label="Platform (ITTC-57 + empirical Cr)", zorder=2)

    for i, (fn, ct, err) in enumerate(zip(fn_exp, ct_calc, errors)):
        color = "#27ae60" if abs(err) <= 15 else "#e74c3c"
        ax1.annotate(f"{err:+.1f}%", (fn, ct), textcoords="offset points",
                     xytext=(8, 8), fontsize=9, color=color, fontweight="bold")

    ax1.set_xlabel("Froude Number (Fn)", fontsize=12)
    ax1.set_ylabel("Total Resistance Coefficient (Ct)", fontsize=12)
    ax1.set_title("SUBOFF: Ct vs Fn — Platform vs Experiment", fontsize=14)
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)

    # ±15% band
    for fn, ct in zip(fn_exp, ct_exp):
        ax1.fill_between([fn * 0.95, fn * 1.05], ct * 0.85, ct * 1.15,
                         alpha=0.1, color="gray")

    plt.tight_layout()
    png1 = OUTPUT_DIR / "m1775_ct_vs_fn.png"
    plt.savefig(png1, dpi=150, bbox_inches="tight")
    plt.close()
    figures.append({"name": "ct_vs_fn", "png": str(png1),
                    "caption": "SUBOFF Ct vs Fn: 实验值与平台计算值对比，标注逐点误差"})

    # Figure 2: Error bar chart
    fig2, ax2 = plt.subplots(figsize=(10, 5))

    labels = [c["case_id"] for c in suboff_validated]
    x = np.arange(len(labels))
    bar_colors = ["#27ae60" if c["passed_15"] else "#e74c3c" for c in suboff_validated]

    bars = ax2.bar(x, [abs(c["Ct_error_pct"]) for c in suboff_validated],
                   color=bar_colors, edgecolor="white", linewidth=0.5)
    ax2.axhline(y=15.0, color="red", linestyle="--", linewidth=2, label="15% threshold")
    ax2.axhline(y=3.0, color="orange", linestyle="--", linewidth=1.5, label="3% threshold")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, fontsize=10)
    ax2.set_ylabel("Absolute Error (%)", fontsize=12)
    ax2.set_title("SUBOFF Resistance: Ct Error vs Thresholds", fontsize=14)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    png2 = OUTPUT_DIR / "m1775_error_bars.png"
    plt.savefig(png2, dpi=150, bbox_inches="tight")
    plt.close()
    figures.append({"name": "error_bars", "png": str(png2),
                    "caption": "逐点误差与 3%/15% 阈值对比"})

    # Figure 3: Wetted surface bias
    fig3, ax3 = plt.subplots(figsize=(8, 5))

    names = [s["name"].replace("_", "\n") for s in ws_results]
    errors_ws = [s["error_pct"] for s in ws_results]
    bar_colors_ws = ["#e74c3c" if abs(e) > 3 else "#27ae60" for e in errors_ws]

    x3 = np.arange(len(names))
    ax3.bar(x3, errors_ws, color=bar_colors_ws, edgecolor="white", linewidth=0.5)
    ax3.axhline(y=0, color="black", linewidth=0.5)
    ax3.axhline(y=3, color="orange", linestyle="--", linewidth=1, label="±3%")
    ax3.axhline(y=-3, color="orange", linestyle="--", linewidth=1)
    ax3.set_xticks(x3)
    ax3.set_xticklabels(names, fontsize=9)
    ax3.set_ylabel("Error (%)", fontsize=12)
    ax3.set_title("Wetted Surface: Empirical Formula vs Closed-Form", fontsize=13)
    ax3.legend(fontsize=10)
    ax3.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    png3 = OUTPUT_DIR / "m1775_wetted_surface_bias.png"
    plt.savefig(png3, dpi=150, bbox_inches="tight")
    plt.close()
    figures.append({"name": "wetted_surface_bias", "png": str(png3),
                    "caption": "湿表面积经验式对三种闭式形状的偏差"})

    # Figure 4: Cr vs Fn (empirical formula behavior)
    fig4, ax4 = plt.subplots(figsize=(10, 5))

    fn_range = np.linspace(0.0, 1.2, 200)
    cb = SUBOFF["cb"]
    cr_values = [0.002 * (1 + 2.0 * cb) * fn ** 2.5 if fn > 0.05 else 0.0 for fn in fn_range]

    ax4.plot(fn_range, cr_values, "-", color="#9b59b6", linewidth=2,
             label=f"Cr = 0.002(1+2Cb)Fn^2.5, Cb={cb}")

    # Mark SUBOFF operating points
    for case in suboff_validated:
        ax4.plot(case["Fn"], case["Cr"], "o", color="#e74c3c", markersize=10,
                 zorder=3)
        ax4.annotate(f"{case['case_id']}", (case["Fn"], case["Cr"]),
                     textcoords="offset points", xytext=(8, 5), fontsize=8)

    ax4.set_xlabel("Froude Number (Fn)", fontsize=12)
    ax4.set_ylabel("Residual Resistance Coefficient (Cr)", fontsize=12)
    ax4.set_title("Empirical Cr Formula Behavior vs SUBOFF Operating Points", fontsize=13)
    ax4.legend(fontsize=10)
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    png4 = OUTPUT_DIR / "m1775_cr_vs_fn.png"
    plt.savefig(png4, dpi=150, bbox_inches="tight")
    plt.close()
    figures.append({"name": "cr_vs_fn", "png": str(png4),
                    "caption": "Cr 经验式随 Fn 的变化及 SUBOFF 工况点"})

    return figures


# ---------------------------------------------------------------------------
# Summary table (markdown)
# ---------------------------------------------------------------------------

def generate_summary_table(suboff_validated, ittc_results, ws_results):
    lines = []
    lines.append("## 误差判定表（逐项，显式回答「是否 <3%」）")
    lines.append("")
    lines.append("| 关键量 | 基准 | 计算值 | 误差 | 是否 <3% |")
    lines.append("|--------|------|--------|------|----------|")

    # ITTC-57
    for r in ittc_results:
        status = "✅ 是" if r["passed"] else "❌ 否"
        lines.append(f"| Cf (Re={r['Re']:.0e}) | {r['Cf_ref']:.6e} | {r['Cf_calc']:.6e} | {r['error_pct']:.2e}% | {status} |")

    lines.append("")

    # Wetted surface
    for s in ws_results:
        status = "✅ 是" if abs(s["error_pct"]) <= 3 else "❌ 否"
        lines.append(f"| 湿表面积 ({s['name']}) | {s['S_closed']:.4f} m² | {s['S_empirical']:.4f} m² | {s['error_pct']:+.1f}% | {status} |")

    lines.append("")

    # SUBOFF Ct
    for c in suboff_validated:
        status_3 = "✅ 是" if c["passed_3"] else "❌ 否"
        status_15 = "✅ <15%" if c["passed_15"] else "❌ >15% 不可接受"
        lines.append(f"| Ct ({c['case_id']}, Fn={c['Fn']:.3f}) | {c['Ct_exp']:.4f} | {c['Ct_calc']:.4f} | {c['Ct_error_pct']:+.1f}% | {status_3} ({status_15}) |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("SUBOFF 阻力计算全面验证 — m1775")
    print("=" * 70)

    # Run verifications
    print("\n[1/4] ITTC-57 friction line verification...")
    ittc_results = verify_ittc57()
    ittc_passed = sum(1 for r in ittc_results if r["passed"])
    print(f"  {ittc_passed}/{len(ittc_results)} passed")

    print("\n[2/4] SUBOFF resistance verification...")
    suboff_validated, suboff_empirical = verify_suboff()
    suboff_passed_15 = sum(1 for c in suboff_validated if c["passed_15"])
    suboff_passed_3 = sum(1 for c in suboff_validated if c["passed_3"])
    print(f"  {suboff_passed_3}/4 passed <3%, {suboff_passed_15}/4 passed <15%")

    print("\n[3/4] Wetted surface bias quantification...")
    ws_results = verify_wetted_surface()
    for s in ws_results:
        print(f"  {s['name']}: {s['error_pct']:+.1f}%")

    print("\n[4/4] Generating figures...")
    figures = generate_figures(suboff_validated, suboff_empirical, ittc_results, ws_results)
    for f in figures:
        print(f"  {f['name']}: {f['png']}")

    # Generate summary table
    summary = generate_summary_table(suboff_validated, ittc_results, ws_results)

    # Save results JSON
    results_json = {
        "mission": "m1775",
        "title": "SUBOFF Resistance Comprehensive Verification",
        "ittc_57": ittc_results,
        "suboff_validated_S": suboff_validated,
        "suboff_empirical_S": suboff_empirical,
        "wetted_surface_bias": ws_results,
        "summary": {
            "ittc_passed": f"{ittc_passed}/{len(ittc_results)}",
            "suboff_passed_3": f"{suboff_passed_3}/4",
            "suboff_passed_15": f"{suboff_passed_15}/4",
        },
    }
    json_path = OUTPUT_DIR / "m1775_verification_results.json"
    with open(json_path, "w") as f:
        json.dump(results_json, f, indent=2)
    print(f"\nResults saved to: {json_path}")

    # Print summary table
    print("\n" + summary)

    return results_json, figures, summary


if __name__ == "__main__":
    main()
