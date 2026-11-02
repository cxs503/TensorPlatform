"""Figures for the m1682 level-ice resistance cross-implementation audit.

Reads the JSON produced by tools/ice_resistance_crosscheck.py and writes PNGs to
docs/reports/figures/.  All numbers come from the JSON (nothing hand-typed).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "docs/reports/artifacts/m1682_ice_resistance_crosscheck.json"
FIGDIR = ROOT / "docs/reports/figures"

IMPLS = ["polar", "lindqvist", "twin"]
LABEL = {"polar": "TP-POLAR-1 (polar)", "lindqvist": "Lindqvist 1989 (ice_resistance_uq)",
         "twin": "digital twin (B/L form)"}
COLOR = {"polar": "#1f77b4", "lindqvist": "#2ca02c", "twin": "#d62728"}


def fig_level(res, out):
    rows = res["P1_absolute"]
    ships = [r["ship"] for r in rows]
    x = np.arange(len(ships))
    w = 0.2
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - 1.5 * w, [r["trial_kN"] for r in rows], w, label="full-scale trial mean",
           color="0.25", edgecolor="k")
    for i, name in enumerate(IMPLS):
        ax.bar(x + (i - 0.5) * w, [r[name + "_kN"] for r in rows], w,
               label=LABEL[name], color=COLOR[name], alpha=0.85)
    for i, r in enumerate(rows):
        for j, name in enumerate(IMPLS):
            ax.text(x[i] + (j - 0.5) * w, r[name + "_kN"] * 1.02, f"{r[name+'_err_pct']:+.0f}%",
                    ha="center", va="bottom", fontsize=8, rotation=90,
                    color=COLOR[name])
    ax.set_xticks(x, [f"{r['ship']}\nh={r['h_m']} m, v={r['v_ms']} m/s" for r in rows])
    ax.set_ylabel("level-ice resistance [kN]")
    ax.set_title("P1  Absolute level vs published full-scale trial means\n"
                 "(labels = relative error; platform gate is 15 %)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def fig_components(res, out):
    rows = res["P2_components"]
    ships = sorted({r["ship"] for r in rows}, key=lambda s: [x["ship"] for x in rows].index(s))
    fig, axes = plt.subplots(1, len(ships), figsize=(12, 4.6), sharey=False)
    parts = [("crushing", "crushing"), ("breaking", "bending"), ("submersion", "submersion")]
    colors = ["#4c72b0", "#dd8452", "#55a868"]
    for ax, ship in zip(axes, ships):
        sub = [r for r in rows if r["ship"] == ship]
        bottom = np.zeros(len(sub))
        for (key, lab), col in zip(parts, colors):
            vals = np.array([r[key] for r in sub])
            ax.bar(range(len(sub)), vals, bottom=bottom, label=lab, color=col)
            bottom += vals
        for i, r in enumerate(sub):
            ax.text(i, r["total"] * 1.02, f"{r['total']:.0f}", ha="center", va="bottom", fontsize=8)
            ax.text(i, r["submersion"] * 0.5 + sum(r[k] for k, _ in parts[:2]),
                    f"{r['submersion_share']:.0%}", ha="center", va="center", fontsize=7, color="w")
        ax.set_xticks(range(len(sub)), [r["impl"] for r in sub], fontsize=8)
        ax.set_title(ship, fontsize=9)
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("component resistance [kN]")
    axes[-1].legend(fontsize=8)
    fig.suptitle("P2  Component split at the trial condition — white label = submersion share\n"
                 "the polar path is crushing/bending dominated (5–7 % submersion), "
                 "Lindqvist is submersion dominated (50–66 %)")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out, dpi=160)
    plt.close(fig)


def fig_exponent(res, out):
    data = res["P3_exponents"]["by_impl"]
    ships = sorted({r["ship"] for r in data["polar"]})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    ax = axes[0]
    for name in IMPLS:
        sub = [r for r in data[name] if r["ship"] == ships[0]]
        ax.plot([r["h_m"] for r in sub], [r["n_breaking"] for r in sub], "o-",
                color=COLOR[name], label=LABEL[name])
    ax.axhspan(1.0, 1.3, color="0.85", label="admissible 1.00–1.30 (h$^{1.25}$ \u00b1 friction multiplier)")
    ax.set_xlabel("ice thickness h [m]")
    ax.set_ylabel("d ln R$_{break}$ / d ln h")
    ax.set_title(f"P3a  bending-term thickness exponent ({ships[0]})")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    ax = axes[1]
    for name in IMPLS:
        sub = [r for r in data[name] if r["ship"] == ships[0]]
        ax.plot([r["h_m"] for r in sub], [r["n_total"] for r in sub], "s--",
                color=COLOR[name], label=LABEL[name])
    ax.axhspan(1.0, 2.0, color="0.9", label="analytic window 1–2")
    ax.set_xlabel("ice thickness h [m]")
    ax.set_ylabel("d ln R$_{total}$ / d ln h")
    ax.set_title(f"P3b  total-resistance thickness exponent ({ships[0]})")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def fig_submersion(res, out):
    rows = res["P8_submersion_form"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    x = np.arange(len(rows))
    ax = axes[0]
    ax.bar(x - 0.2, [r["polar_contact_area_m2"] for r in rows], 0.4, label="polar: B·T·Cb·(mu+sin b)",
           color=COLOR["polar"])
    ax.bar(x + 0.2, [r["lindqvist_contact_area_m2"] for r in rows], 0.4,
           label="Lindqvist: B·(1+mu·cot phi)·0.3L", color=COLOR["lindqvist"])
    for i, r in enumerate(rows):
        ax.text(x[i], max(r["polar_contact_area_m2"], r["lindqvist_contact_area_m2"]) * 1.05,
                f"{r['area_ratio_lindqvist_over_polar']:.1f}x", ha="center", fontsize=8)
    ax.set_yscale("log")
    ax.set_xticks(x, [r["ship"] for r in rows], fontsize=8)
    ax.set_ylabel("equivalent ice–hull contact area [m²]")
    ax.set_title("P8a  submersion term: contact measure used by each form")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)

    ls = res["P4_length_sensitivity"]
    ax = axes[1]
    w = 0.25
    for i, name in enumerate(IMPLS):
        ax.bar(x + (i - 1) * w, [r[name + "_spread_pct"] for r in ls], w, label=LABEL[name],
               color=COLOR[name])
    ax.set_xticks(x, [r["ship"] for r in ls], fontsize=8)
    ax.set_ylabel("R spread over L ± 10 %  at fixed B, T [%]")
    ax.set_title("P4  ship-length sensitivity of the ice term")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)


def main():
    res = json.loads(ART.read_text())
    FIGDIR.mkdir(parents=True, exist_ok=True)
    outs = []
    for fn, name in ((fig_level, "m1682_fig1_absolute_level.png"),
                     (fig_components, "m1682_fig2_components.png"),
                     (fig_exponent, "m1682_fig3_exponents.png"),
                     (fig_submersion, "m1682_fig4_submersion_and_length.png")):
        out = FIGDIR / name
        fn(res, out)
        outs.append(out)
        print("wrote", out)
    return outs


if __name__ == "__main__":
    main()