#!/usr/bin/env python3
"""m1386 verification analysis: wall-sided GZ vs exact box-barge hydrostatics.

Produces (all numbers in the report are generated here, never hand-copied):

    artifacts/m1386/results.json          machine-readable numbers
    artifacts/m1386/results_tables.md     markdown tables injected into the doc
    artifacts/m1386/fig1_gz_curves.png
    artifacts/m1386/fig2_error_envelope.png
    artifacts/m1386/fig3_immersed_sections.png

Run:  python3 artifacts/m1386/analysis.py
"""
from __future__ import annotations

import json
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "backend"))

from services.stability_reference import (  # noqa: E402
    box_parameters,
    exact_gz_box,
    gz_error_envelope,
    max_true_gz,
    wall_sided_gz,
    wall_sided_limit_deg,
)

CASES = [
    {"id": "A", "label": "full-form barge", "B": 20.0, "T": 8.0, "D": 12.0, "KG": 6.5},
    {"id": "B", "label": "slender hull", "B": 32.0, "T": 12.0, "D": 20.0, "KG": 8.0},
    {"id": "C", "label": "shallow-draft box", "B": 10.0, "T": 2.0, "D": 3.0, "KG": 2.0},
]
HEELS = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0,
         60.0, 70.0, 80.0, 85.0, 89.0]


def analyse_case(c: dict) -> dict:
    par = box_parameters(c["B"], c["T"], c["KG"], c["D"])
    limit = wall_sided_limit_deg(c["B"], c["T"], c["D"])
    rows = gz_error_envelope(c["B"], c["T"], c["KG"], HEELS, c["D"])
    peak = max_true_gz(c["B"], c["T"], c["KG"], c["D"], step_deg=0.25)

    def first_breach(threshold_pct: float):
        for r in rows:
            if abs(r["rel_err_pct"]) > threshold_pct:
                return r["heel_deg"]
        return None

    return {
        "case": c["id"],
        "label": c["label"],
        "beam_m": c["B"], "draft_m": c["T"], "depth_m": c["D"], "kg_m": c["KG"],
        "kb_m": par["kb_m"], "bm_m": par["bm_m"], "gm_m": par["gm_m"],
        "wall_sided_limit_deg": limit,
        "gz_max_exact_m": peak["gz_max_m"],
        "heel_at_gz_max_deg": peak["heel_at_gz_max_deg"],
        "first_breach_3pct_deg": first_breach(3.0),
        "first_breach_15pct_deg": first_breach(15.0),
        "envelope": rows,
    }


def imo_indicators(c: dict) -> dict:
    """IS-Code style indicators from both curves (trapezoid integration, 0.05 deg step)."""
    par = box_parameters(c["B"], c["T"], c["KG"], c["D"])
    heels = [i * 0.05 for i in range(0, 1801)]  # 0..90 deg
    exact = [exact_gz_box(c["B"], c["T"], c["KG"], math.radians(h), c["D"])["gz_m"] for h in heels]
    wall = [wall_sided_gz(par["gm_m"], par["bm_m"], math.radians(h)) for h in heels]

    def area(curve, lo, hi):
        acc = 0.0
        for i in range(len(heels) - 1):
            if lo <= heels[i] < hi:
                acc += 0.5 * (curve[i] + curve[i + 1]) * math.radians(heels[i + 1] - heels[i])
        return acc

    def vanishing(curve):
        for i in range(1, len(heels)):
            if curve[i] <= 0.0 < curve[i - 1]:
                return heels[i]
        return None

    def at(curve, deg):
        return curve[int(round(deg / 0.05))]

    return {
        "case": c["id"],
        "gz_30_exact_m": at(exact, 30.0), "gz_30_wall_m": at(wall, 30.0),
        "gz_40_exact_m": at(exact, 40.0), "gz_40_wall_m": at(wall, 40.0),
        "a30_exact_mrad": area(exact, 0.0, 30.0), "a30_wall_mrad": area(wall, 0.0, 30.0),
        "a40_exact_mrad": area(exact, 30.0, 40.0), "a40_wall_mrad": area(wall, 30.0, 40.0),
        "gzmax_exact_m": max(exact), "gzmax_wall_m": max(wall),
        "phi_vanishing_exact_deg": vanishing(exact),
        "phi_vanishing_wall_deg": vanishing(wall),
    }


def md_tables(results: list[dict]) -> str:
    lines = ["### T1. 基准几何与静水力参数（箱形驳船解析值）", "",
             "| 案例 | 描述 | B (m) | T (m) | D (m) | KG (m) | KB=T/2 | BM=B²/12T | GM | 壁式公式有效上限 φ_lim (°) | 精确 GZ_max (m) | φ(GZ_max) (°) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r['case']} | {r['label']} | {r['beam_m']:.0f} | {r['draft_m']:.0f} | "
            f"{r['depth_m']:.0f} | {r['kg_m']:.2f} | {r['kb_m']:.3f} | {r['bm_m']:.3f} | "
            f"{r['gm_m']:.3f} | {r['wall_sided_limit_deg']:.2f} | {r['gz_max_exact_m']:.3f} | "
            f"{r['heel_at_gz_max_deg']:.2f} |")

    lines += ["", "### T2. GZ 复核对表（精确积分解 vs 平台壁式闭式解，案例 A：B=20/T=8/D=12/KG=6.5）", "",
              "| φ (°) | GZ_精确 (m) | GZ_壁式 (m) | 绝对差 (m) | 相对误差 (%) | 域内/域外 |",
              "|---|---|---|---|---|---|"]
    a = results[0]
    for r in a["envelope"]:
        if r["heel_deg"] not in (0.0, 5.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 89.0):
            continue
        tag = "域内" if r["heel_deg"] <= a["wall_sided_limit_deg"] else "域外"
        rel = "—" if r["heel_deg"] == 0.0 else f"{r['rel_err_pct']:+.3f}"
        lines.append(f"| {r['heel_deg']:.0f} | {r['gz_exact_m']:.4f} | {r['gz_wall_sided_m']:.4f} | "
                     f"{r['abs_err_m']:+.4f} | {rel} | {tag} |")

    lines += ["", "### T3. 误差越界角（相对误差首次超过阈值的横倾角）", "",
              "| 案例 | φ_lim (°) | 首次 >3% | 首次 >15% | 域内最大相对误差 |",
              "|---|---|---|---|---|"]
    for r in results:
        inside = [abs(x["rel_err_pct"]) for x in r["envelope"]
                  if x["heel_deg"] <= r["wall_sided_limit_deg"] and x["heel_deg"] > 0]
        lines.append(f"| {r['case']} | {r['wall_sided_limit_deg']:.2f} | "
                     f"{r['first_breach_3pct_deg']:.0f}° | {r['first_breach_15pct_deg']:.0f}° | "
                     f"{max(inside):.2e}% |")
    lines += ["", "### T4. IS-Code 完整稳性指标：平台壁式解 vs 精确解（IMO IS Code 2008 第 2.2 条口径）", "",
              "| 案例 | GZ(30°) 精确/壁式 (m) | GZ(40°) 精确/壁式 (m) | A30 精确/壁式 (m·rad) | A40 精确/壁式 (m·rad) | GZmax 精确/壁式 (m) | 消失角 精确/壁式 (°) |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        im = imo_indicators({"id": r["case"], "B": r["beam_m"], "T": r["draft_m"],
                             "D": r["depth_m"], "KG": r["kg_m"]})
        vanish_wall = "无（单调增）" if im["phi_vanishing_wall_deg"] is None else f"{im['phi_vanishing_wall_deg']:.1f}"
        vanish_exact = "—" if im["phi_vanishing_exact_deg"] is None else f"{im['phi_vanishing_exact_deg']:.1f}"
        lines.append(
            f"| {r['case']} | {im['gz_30_exact_m']:.3f} / {im['gz_30_wall_m']:.3f} | "
            f"{im['gz_40_exact_m']:.3f} / {im['gz_40_wall_m']:.3f} | "
            f"{im['a30_exact_mrad']:.4f} / {im['a30_wall_mrad']:.4f} | "
            f"{im['a40_exact_mrad']:.4f} / {im['a40_wall_mrad']:.4f} | "
            f"{im['gzmax_exact_m']:.3f} / {im['gzmax_wall_m']:.2e} | "
            f"{vanish_exact} / {vanish_wall} |")
    return "\n".join(lines)


def fig_gz_curves(results, path):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    heels_fine = [i * 0.5 for i in range(0, 179)]  # 0..89 deg
    for ax, r in zip(axes, results):
        exact = [exact_gz_box(r["beam_m"], r["draft_m"], r["kg_m"], math.radians(h),
                              r["depth_m"])["gz_m"] for h in heels_fine]
        wall = [wall_sided_gz(r["gm_m"], r["bm_m"], math.radians(h)) for h in heels_fine]
        ax.axvspan(r["wall_sided_limit_deg"], 89, color="0.88", alpha=0.7,
                   label="outside wall-sided domain")
        ax.axvline(r["wall_sided_limit_deg"], color="k", lw=1.0, ls=":")
        ax.plot(heels_fine, exact, color="#1b6ca8", lw=2.2, label="exact box hydrostatics")
        ax.plot(heels_fine, wall, color="#c0392b", lw=1.6, ls="--", label="shipped wall-sided form")
        ax.plot([r["heel_at_gz_max_deg"]], [r["gz_max_exact_m"]], "o", color="#1b6ca8", ms=5)
        ax.annotate(f"GZmax={r['gz_max_exact_m']:.2f} m\n@ {r['heel_at_gz_max_deg']:.1f}°",
                    (r["heel_at_gz_max_deg"], r["gz_max_exact_m"]),
                    textcoords="offset points", xytext=(-8, 10), fontsize=8)
        ax.set_title(f"Case {r['case']}  B={r['beam_m']:.0f}/T={r['draft_m']:.0f}/"
                     f"D={r['depth_m']:.0f}, GM={r['gm_m']:.2f} m", fontsize=10)
        ax.set_xlabel("heel φ (°)")
        ax.set_ylabel("GZ (m)")
        ax.set_xlim(0, 89)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("Righting arm: shipped wall-sided closed form vs exact box-barge hydrostatics", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_error_envelope(results, path):
    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    for r, color in zip(results, ["#1b6ca8", "#c0392b", "#27ae60"]):
        hs = [x["heel_deg"] for x in r["envelope"] if x["heel_deg"] > 0]
        rel = [abs(x["rel_err_pct"]) for x in r["envelope"] if x["heel_deg"] > 0]
        ax.semilogy(hs, rel, "o-", color=color, ms=4,
                    label=f"Case {r['case']} (φ_lim={r['wall_sided_limit_deg']:.1f}°)")
        ax.axvline(r["wall_sided_limit_deg"], color=color, lw=0.8, ls=":", alpha=0.8)
    ax.axhline(3.0, color="k", lw=1.0, ls="--")
    ax.axhline(15.0, color="k", lw=1.0, ls="-.")
    ax.text(2, 3.4, "3 % bar", fontsize=8)
    ax.text(2, 17, "15 % unacceptable bar", fontsize=8)
    ax.set_xlabel("heel φ (°)")
    ax.set_ylabel("|GZ_wall − GZ_exact| / GZ_exact  (%)")
    ax.set_title("Wall-sided GZ error envelope: exact inside φ_lim, unbounded growth outside")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_sections(results, path):
    """Immersed-section evidence of the reference integrator (case A)."""
    r = results[0]
    B, T, D, KG = r["beam_m"], r["draft_m"], r["depth_m"], r["kg_m"]
    heels = [10, 25, 45, 70]
    fig, axes = plt.subplots(1, len(heels), figsize=(15, 4.0))
    for ax, hd in zip(axes, heels):
        phi = math.radians(hd)
        s = exact_gz_box(B, T, KG, phi, D)
        zeta = s["waterline_z_m"]
        c_, sn = math.cos(phi), math.sin(phi)
        body = [(-B / 2, 0.0), (B / 2, 0.0), (B / 2, D), (-B / 2, D)]
        poly = [(y * c_ + z * sn, -y * sn + z * c_) for (y, z) in body]
        ax.add_patch(Polygon(poly, closed=True, fc="#d6e6f4", ec="#1b6ca8", lw=1.4))
        # immersed part (clip below waterline, z <= zeta)
        imm = []
        n = len(poly)
        for i in range(n):
            y0, z0 = poly[i]
            y1, z1 = poly[(i + 1) % n]
            if z0 <= zeta:
                imm.append((y0, z0))
            if (z0 <= zeta) != (z1 <= zeta):
                t = (zeta - z0) / (z1 - z0)
                imm.append((y0 + t * (y1 - y0), zeta))
        if len(imm) >= 3:
            ax.add_patch(Polygon(imm, closed=True, fc="#7fb2d6", ec="#12496e", lw=1.0, alpha=0.9))
        ax.axhline(zeta, color="#c0392b", lw=1.2, ls="--")
        ax.plot(s["yb_m"], s["zb_m"], "ko", ms=5)
        ax.plot(s["yg_m"], s["zg_m"], "r^", ms=6)
        ax.set_title(f"φ = {hd}°   GZ={s['gz_m']:.3f} m\nwall-sided={wall_sided_gz(r['gm_m'], r['bm_m'], phi):.3f} m",
                     fontsize=9)
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)
        ax.set_xlabel("y (m)")
        ax.set_ylabel("z (m)")
    axes[0].legend(["waterline (displacement-conserving)", "centre of buoyancy B", "centre of gravity G"],
                   fontsize=7, loc="lower left")
    fig.suptitle(f"Case {r['case']}: heeled section, immersed area = B·T = {B*T:.0f} m² at every heel", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    results = [analyse_case(c) for c in CASES]
    imo = [imo_indicators({"id": c["id"], "B": c["B"], "T": c["T"], "D": c["D"], "KG": c["KG"]})
           for c in CASES]
    out = {
        "mission": "m1386",
        "imo_indicators": imo,
        "subject": "calc.stability wall-sided GZ vs exact box-barge hydrostatics",
        "reference": "constant-displacement 2D hydrostatics, shoelace area/centroid + bisection sinkage",
        "cases": results,
    }
    with open(os.path.join(HERE, "results.json"), "w") as f:
        json.dump(out, f, indent=1)
    with open(os.path.join(HERE, "results_tables.md"), "w") as f:
        f.write(md_tables(results) + "\n")
    fig_gz_curves(results, os.path.join(HERE, "fig1_gz_curves.png"))
    fig_error_envelope(results, os.path.join(HERE, "fig2_error_envelope.png"))
    fig_sections(results, os.path.join(HERE, "fig3_immersed_sections.png"))
    print(md_tables(results))


if __name__ == "__main__":
    main()