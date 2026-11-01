"""冰区域验证图件（全部数字来自 artifacts/m1517/ice_probe.json 与 ice_criteria.json）。"""
from __future__ import annotations

import json
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tools.verif.ice.ice_ref import (KNOT, IACS_PC_PRESSURE_MPA, evaluate,
                                     iacs_plating_thickness_mm, lindqvist_band)

PROBE = "artifacts/m1517/ice_probe.json"
OUT = "artifacts/m1517/figs"


def rows(probe):
    return {r["variant"]: r["response"] for r in probe["rows"]}


def sel(rw, prefix):
    pts = []
    for k, v in rw.items():
        if k.startswith(prefix + "="):
            pts.append((float(k.split("=")[1]), v["ice_resistance"]))
    return sorted(pts)


def fig1(probe, summary):
    rw = rows(probe)
    bc = probe["base_case"]
    hs = sel(rw, "h")
    hx = [h for h, _ in hs]
    px = [r / 1e3 for _, r in hs]
    lo, hi = [], []
    for h in hx:
        b = lindqvist_band(bc["length"], bc["beam"], bc["draft"], h,
                           bc["speed"] * KNOT)
        lo.append(b["R_lo"] / 1e3)
        hi.append(b["R_hi"] / 1e3)
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    ax.fill_between(hx, lo, hi, color="#9ecae1", alpha=0.55,
                    label="Lindqvist (1989) parameter band")
    ax.plot(hx, px, "o-", color="#c00000", lw=2,
            label="platform /extended-calculations/ice")
    ax.set_xlabel("Ice thickness h [m]")
    ax.set_ylabel("Level-ice resistance R [kN]")
    ax.set_yscale("log")
    ax.set_title("R vs h: platform endpoint vs independent Lindqvist band (base case)")
    ax.grid(alpha=0.3, which="both")
    ax.legend()
    for h, p in zip(hx, px):
        ax.annotate(f"{p:.1f}", (h, p), textcoords="offset points",
                    xytext=(4, -10), fontsize=7, color="#c00000")
    fig.tight_layout()
    fig.savefig(f"{OUT}/fig1_R_vs_h.png", dpi=130)
    plt.close(fig)


def fig2(probe):
    rw = rows(probe)
    bc = probe["base_case"]
    vs = sel(rw, "V")
    vx = [v for v, _ in vs]
    py = [r / 1e3 for _, r in vs]
    lq = []
    for v in vx:
        b = lindqvist_band(bc["length"], bc["beam"], bc["draft"],
                           bc["ice_thickness"], v * KNOT)
        lq.append(b["R_lo"] / 1e3)
    caps = {}
    for c in IACS_PC_PRESSURE_MPA:
        caps[c] = rw[f"class={c}"]["continuous_speed"]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.4))
    ax = axes[0]
    ax.plot(vx, py, "o-", color="#c00000", lw=2, label="platform endpoint")
    ax.plot(vx, lq, "s--", color="#1f4e79", lw=2, label="Lindqvist lower bound")
    ax.set_xlabel("Ship speed V [kn]")
    ax.set_ylabel("Level-ice resistance R [kN]")
    ax.set_title("Speed response: platform dR/dV = 0 (A2/A3 FAIL)")
    ax.grid(alpha=0.3)
    ax.legend()

    ax = axes[1]
    cls = list(IACS_PC_PRESSURE_MPA)
    vals = [caps[c] for c in cls]
    ax.bar(cls, vals, color="#70ad47", alpha=0.85, label="class capability cap")
    vmax = max(v for v, _ in vs)
    ax.axhline(rw["V=10.0"]["continuous_speed"], color="#c00000", ls="--", lw=2,
               label=f"input V={vmax:.0f} kn -> {rw['V=10.0']['continuous_speed']:.1f} kn")
    ax.set_ylabel("continuous_speed [kn]")
    ax.set_title("Cap saturation broken: PC7 cap 2.0 kn exceeded by 4.0 kn (A7 FAIL)")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{OUT}/fig2_velocity_and_cap.png", dpi=130)
    plt.close(fig)


def fig3(summary, probe):
    rw = rows(probe)
    crit = summary["criteria"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4),
                             gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    ids = [c["id"] for c in crit]
    ok = [1 if c["verdict"] == "PASS" else 0 for c in crit]
    ax.bar(ids, ok, color=["#2e7d32" if v else "#c00000" for v in ok])
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["FAIL", "PASS"])
    ax.set_title(f"Closed-form criteria verdicts ({summary['n_fail']}/{summary['n_criteria']} FAIL -> {summary['verdict']})")
    ax.grid(alpha=0.3, axis="y")
    for c, v in zip(crit, ok):
        ax.text(c["id"], v + 0.03, c["class"], ha="center", fontsize=7)

    ax = axes[1]
    cls = list(IACS_PC_PRESSURE_MPA)
    plat = [rw[f"class={c}"]["shell_plating"] for c in cls]
    iacs = [iacs_plating_thickness_mm(c, rw[f"class={c}"]["frame_spacing"] / 1000.0)
            for c in cls]
    x = range(len(cls))
    ax.bar([i - 0.2 for i in x], plat, width=0.4, label="platform shell_plating",
           color="#c00000", alpha=0.85)
    ax.bar([i + 0.2 for i in x], iacs, width=0.4, label="IACS UR I2 estimate",
           color="#1f4e79", alpha=0.85)
    ax.set_xticks(list(x))
    ax.set_xticklabels(cls)
    ax.set_ylabel("Shell plating thickness [mm]")
    ax.set_title("Plating thickness: constant 25 mm across PC classes (A9 FAIL)")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{OUT}/fig3_verdicts_and_plating.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    probe = json.load(open(PROBE))
    summary = evaluate(probe)
    fig1(probe, summary)
    fig2(probe)
    fig3(summary, probe)
    print("figures written to", OUT)
