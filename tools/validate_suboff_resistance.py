#!/usr/bin/env python3
"""
SUBOFF 阻力验证：生成对比图 + 报告数据

对标 Roddy (1990) DARPA SUBOFF 实验数据，
验证平台 ITTC-57 + Holtrop 简化方法的预测精度。

输出:
  - outputs/suboff_validation/ct_comparison.png  (Ct vs Fn 对比图)
  - outputs/suboff_validation/rt_comparison.png  (Rt vs V 对比图)
  - outputs/suboff_validation/validation_results.json
  - outputs/suboff_validation/report_data.json (for report publishing)
"""
import base64
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "suboff_validation"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# m1709 修复：参考侧改为正典 v3.0 单一真源（原字面量表用了正典已作废的 legacy Rt
# 12.50/21.00/31.50/45.00 N 与错误 Froude 数列 0.236/0.315/0.393/0.472，
# 且 displacement 误抄 beam=0.508；详见 tools/validate_suboff_resistance_canon.py 与
# docs/m1709/suboff_reference_provenance.md）。
_CANON_PATH = Path(__file__).resolve().parent.parent / "backend" / "data" / "benchmark_reference.json"
_CANON = json.loads(_CANON_PATH.read_text(encoding="utf-8"))


def _canon_suboff_cases() -> list:
    out = []
    for case in _CANON["cases"]:
        if case["ship_type"] != "SUBOFF":
            continue
        geom = case["geometry"]
        out.append({
            "case_id": case["case_id"],
            "speed_ms": case["speed_ms"],
            "fn": case["froude_number"],
            "ct_exp": case["experimental_ct"],
            "rt_exp_n": case["experimental_rt_n"],
            "s_wet_m2": geom["wetted_surface_m2"],
        })
    return sorted(out, key=lambda c: c["speed_ms"])


SUBOFF_CASES = _canon_suboff_cases()

_first_geom = min(_CANON["cases"], key=lambda c: 0 if c["ship_type"] == "SUBOFF" else 1)["geometry"]
SUBOFF_GEOM = {
    "length": _first_geom["length_m"],
    "beam": _first_geom["diameter_m"],
    "draft": _first_geom["diameter_m"],
    "cb": 0.542,
    "displacement": 0.542 * _first_geom["length_m"] * _first_geom["diameter_m"] ** 2,
}

# ---------------------------------------------------------------------------
# Call resistance API
# ---------------------------------------------------------------------------
def call_resistance_api(speed: float) -> dict:
    """Call the ship platform resistance API."""
    try:
        import httpx
        base_url = os.getenv("SHIP_PLATFORM_URL", "http://localhost:8001")
        payload = {
            "ship_type": "SUBOFF",
            "speed": speed,
            "length": SUBOFF_GEOM["length"],
            "beam": SUBOFF_GEOM["beam"],
            "draft": SUBOFF_GEOM["draft"],
            "displacement": SUBOFF_GEOM["displacement"],
            "cb": SUBOFF_GEOM["cb"],
        }
        resp = httpx.post(
            f"{base_url}/api/v1/calculations/resistance",
            json=payload, timeout=30.0
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"API call failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Run validation
# ---------------------------------------------------------------------------
def run_validation():
    results = []
    for case in SUBOFF_CASES:
        api_result = call_resistance_api(case["speed_ms"])
        if api_result is None:
            print(f"SKIP {case['case_id']}: API unavailable")
            continue

        ct_calc = api_result["ct"]
        ct_exp = case["ct_exp"]
        rt_calc = api_result["rt"]
        rt_exp = case["rt_exp_n"]
        error_ct = abs(ct_calc - ct_exp) / ct_exp * 100.0
        error_rt = abs(rt_calc - rt_exp) / rt_exp * 100.0

        results.append({
            "case_id": case["case_id"],
            "speed_ms": case["speed_ms"],
            "fn": case["fn"],
            "ct_calc": round(ct_calc, 6),
            "ct_exp": ct_exp,
            "error_ct_pct": round(error_ct, 1),
            "rt_calc_n": round(rt_calc, 4),
            "rt_exp_n": rt_exp,
            "error_rt_pct": round(error_rt, 1),
            "reynolds": round(api_result.get("reynolds_number", 0), 0),
            "cf": api_result.get("cf", 0),
            "cr": api_result.get("cr", 0),
            "method": api_result.get("method", "Unknown"),
        })
        print(f"{case['case_id']}: V={case['speed_ms']} m/s, "
              f"Ct_calc={ct_calc:.6f}, Ct_exp={ct_exp:.6f}, "
              f"Error={error_ct:.1f}%")

    return results


# ---------------------------------------------------------------------------
# Generate figures
# ---------------------------------------------------------------------------
def fig_to_base64(fig) -> str:
    """Convert matplotlib figure to base64 PNG string."""
    from io import BytesIO
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def plot_ct_comparison(results):
    """Plot Ct vs Froude number: calculated vs experimental."""
    fig, ax = plt.subplots(figsize=(8, 5))

    fn_vals = [r["fn"] for r in results]
    ct_calc = [r["ct_calc"] for r in results]
    ct_exp = [r["ct_exp"] for r in results]

    ax.plot(fn_vals, ct_exp, "ko-", markersize=8, linewidth=2, label="Experimental (Roddy 1990)")
    ax.plot(fn_vals, ct_calc, "rs--", markersize=8, linewidth=2, label="ITTC-57 + Holtrop (Platform)")

    ax.set_xlabel("Froude Number (Fn)", fontsize=12)
    ax.set_ylabel("Total Resistance Coefficient (Ct)", fontsize=12)
    ax.set_title("DARPA SUBOFF: Ct vs Fn — Platform vs Experiment", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11, loc="upper left")
    ax.grid(True, alpha=0.3)

    # Annotate error percentages
    for i, r in enumerate(results):
        ax.annotate(
            f"+{r['error_ct_pct']:.0f}%",
            (fn_vals[i], ct_calc[i]),
            textcoords="offset points",
            xytext=(8, 5),
            fontsize=9,
            color="red",
            fontweight="bold",
        )

    plt.tight_layout()
    return fig


def plot_rt_comparison(results):
    """Plot Rt vs speed: calculated vs experimental."""
    fig, ax = plt.subplots(figsize=(8, 5))

    speeds = [r["speed_ms"] for r in results]
    rt_calc = [r["rt_calc_n"] for r in results]
    rt_exp = [r["rt_exp_n"] for r in results]

    ax.plot(speeds, rt_exp, "ko-", markersize=8, linewidth=2, label="Experimental (Roddy 1990)")
    ax.plot(speeds, rt_calc, "rs--", markersize=8, linewidth=2, label="ITTC-57 + Holtrop (Platform)")

    ax.set_xlabel("Speed (m/s)", fontsize=12)
    ax.set_ylabel("Total Resistance Rt (N)", fontsize=12)
    ax.set_title("DARPA SUBOFF: Rt vs Speed — Platform vs Experiment", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11, loc="upper left")
    ax.grid(True, alpha=0.3)

    for i, r in enumerate(results):
        ax.annotate(
            f"+{r['error_rt_pct']:.0f}%",
            (speeds[i], rt_calc[i]),
            textcoords="offset points",
            xytext=(8, 5),
            fontsize=9,
            color="red",
            fontweight="bold",
        )

    plt.tight_layout()
    return fig


def plot_error_bar(results):
    """Plot error percentage bar chart."""
    fig, ax = plt.subplots(figsize=(8, 5))

    case_ids = [r["case_id"] for r in results]
    errors = [r["error_ct_pct"] for r in results]

    colors = ["red" if e > 15 else "green" for e in errors]
    bars = ax.bar(case_ids, errors, color=colors, alpha=0.7, edgecolor="black")

    ax.axhline(y=15, color="orange", linestyle="--", linewidth=2, label="15% threshold")
    ax.set_xlabel("Case ID", fontsize=12)
    ax.set_ylabel("Relative Error (%)", fontsize=12)
    ax.set_title("SUBOFF Resistance: Ct Error vs 15% Threshold", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3, axis="y")

    for bar, err in zip(bars, errors):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                f"{err:.0f}%", ha="center", fontsize=10, fontweight="bold")

    plt.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("SUBOFF Resistance Validation")
    print("=" * 60)

    results = run_validation()
    if not results:
        print("No results — API unavailable. Exiting.")
        sys.exit(1)

    # Save results JSON
    results_path = OUTPUT_DIR / "validation_results.json"
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {results_path}")

    # Generate figures
    fig1 = plot_ct_comparison(results)
    fig1_path = OUTPUT_DIR / "ct_comparison.png"
    fig1.savefig(fig1_path, dpi=150, bbox_inches="tight")
    plt.close(fig1)
    print(f"Ct comparison saved to {fig1_path}")

    fig2 = plot_rt_comparison(results)
    fig2_path = OUTPUT_DIR / "rt_comparison.png"
    fig2.savefig(fig2_path, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    print(f"Rt comparison saved to {fig2_path}")

    fig3 = plot_error_bar(results)
    fig3_path = OUTPUT_DIR / "error_bar.png"
    fig3.savefig(fig3_path, dpi=150, bbox_inches="tight")
    plt.close(fig3)
    print(f"Error bar saved to {fig3_path}")

    # Prepare report data
    report_data = {
        "title": "DARPA SUBOFF 阻力计算验证报告",
        "summary": "平台 ITTC-57 + Holtrop 简化方法对 SUBOFF 回转体的阻力预测精度验证。对标 Roddy (1990) 实验数据，4 个航速工况。",
        "scenario_key": "suboff_resistance_validation",
        "mission_id": 1444,
        "tags": ["resistance", "SUBOFF", "validation", "honest-fail", "ITTC-57", "Holtrop"],
        "results": results,
        "figures": [],
    }

    # Encode figures as base64
    for fig, name, caption in [
        (fig1, "ct_vs_fn", "Ct vs Fn: 平台计算值与 Roddy (1990) 实验值对比"),
        (fig2, "rt_vs_speed", "Rt vs Speed: 平台计算值与实验值对比"),
        (fig3, "error_bar", "各工况 Ct 相对误差与 15% 阈值对比"),
    ]:
        b64 = fig_to_base64(fig)
        report_data["figures"].append({
            "name": name,
            "caption": caption,
            "png_base64": b64,
        })
        plt.close(fig)

    report_path = OUTPUT_DIR / "report_data.json"
    report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
    print(f"Report data saved to {report_path}")

    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    for r in results:
        status = "PASS" if r["error_ct_pct"] <= 15 else "FAIL"
        print(f"  {r['case_id']}: Ct error = {r['error_ct_pct']:.1f}% [{status}]")

    all_pass = all(r["error_ct_pct"] <= 15 for r in results)
    print(f"\nOverall: {'ALL PASS' if all_pass else 'HONEST-FAIL — all cases exceed 15%'}")
    print("Reason: Holtrop method calibrated for Cb>0.55 merchant ships;")
    print("        SUBOFF (Cb=0.542, Fr>0.2) is outside validity range.")
    print("=" * 60)


if __name__ == "__main__":
    main()
