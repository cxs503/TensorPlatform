#!/usr/bin/env python3
"""Figures for node N10 (multi-fidelity error chain + credible-domain guard).

Reads ``evidence/fidelity_chain_evidence.json`` (numbers) and re-derives the pointwise
scatter / curve series with the same primitives, so no value is transcribed by hand.

    python docs/p470w2_n10/make_figures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIG = HERE / "figures"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

from run_fidelity_chain import (  # noqa: E402
    DOMAIN_HI,
    DOMAIN_LO,
    WIDTH,
    build_chain,
    quantity,
)

EVIDENCE = json.loads((HERE / "evidence" / "fidelity_chain_evidence.json").read_text())
CRIT = {c["criterion"]: c for c in EVIDENCE["criteria"]}


def _style() -> None:
    plt.rcParams.update({
        "figure.dpi": 150, "savefig.dpi": 150, "font.size": 9,
        "axes.grid": True, "grid.alpha": 0.3, "axes.spines.top": False,
        "axes.spines.right": False, "legend.frameon": False,
    })


def fig_bound_vs_actual(chain) -> Path:
    X_in = np.linspace(DOMAIN_LO, DOMAIN_HI, 401).reshape(-1, 1)
    X_out = np.concatenate([
        (DOMAIN_HI + d * WIDTH + 0.01 * WIDTH * np.arange(20)).reshape(-1, 1)
        for d in (0.05, 0.1, 0.2, 0.4)])
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    for X, label, colour, marker in ((X_in, "inside the calibration box", "#1f77b4", "o"),
                                     (X_out, "outside the box (extrapolated)",
                                      "#d62728", "^")):
        actual = np.abs(chain.composed_prediction(X) - quantity(X))
        bound = chain.composed_bound(X, mode="guard")
        ax.scatter(actual, bound, s=14, alpha=0.6, label=label, color=colour,
                   marker=marker, edgecolors="none")
    lo = 1e-5
    hi = 10.0
    ax.plot([lo, hi], [lo, hi], "k--", lw=1.0, label="bound = actual")
    ax.plot([lo, hi], [100 * lo, 100 * hi], "k:", lw=1.0, label="bound = 100 x actual")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("realised error |prediction - reference|")
    ax.set_ylabel("propagated guard bound")
    audit = CRIT["C1"]["in_and_out_of_domain"]
    ax.set_title("C1 error-propagation conservatism: "
                 f"{audit['violations']}/{audit['n']} violations, "
                 f"median bound/actual = {audit['ratio_median']:.1f}", fontsize=9)
    ax.legend(loc="upper left", fontsize=8)
    path = FIG / "fig1_bound_vs_actual.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_coverage_control() -> Path:
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    colours = {"0": "#1f77b4", "1": "#2ca02c", "2": "#9467bd"}
    for rank, colour in colours.items():
        curve = CRIT["C2"]["per_tier"][rank]["curve"]
        scales = [c["halfwidth_scale"] for c in curve]
        cov = [c["coverage"] for c in curve]
        ax.plot(scales, cov, "o-", color=colour,
                label=f"tier {rank} (95 % level: "
                      f"{CRIT['C2']['per_tier'][rank]['coverage_at_95']:.3f})")
    ax.axhline(0.90, color="k", ls="--", lw=1.0, label="C2 requirement 90 %")
    ax.axhline(0.80, color="0.45", ls=":", lw=1.0, label="negative-control ceiling 80 %")
    ax.axvline(0.5, color="0.75", lw=0.8)
    ax.annotate("halved interval\n(negative control)",
                xy=(0.5, CRIT["C2"]["negative_control"]["coverage"]),
                xytext=(0.62, 0.70), fontsize=8,
                arrowprops=dict(arrowstyle="->", lw=0.8, color="0.4"))
    ax.set_xlabel("credible-interval half-width scale")
    ax.set_ylabel("empirical coverage of the reference")
    ax.set_ylim(0.0, 1.02)
    ax.set_title("C2 credible-interval calibration and shrunk-interval control", fontsize=9)
    ax.legend(loc="lower right", fontsize=8)
    path = FIG / "fig2_coverage_control.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_extrapolation(chain) -> Path:
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    colours = {0: "#1f77b4", 1: "#2ca02c", 2: "#9467bd"}
    grid = np.linspace(0.05, 0.7, 120)
    for row in CRIT["C4"]["tiers"]:
        rank = row["rank"]
        d = np.array(row["study_distances"])
        ax.plot(d, row["actual_error"], "o", color=colours[rank], ms=5,
                label=f"tier {rank} measured")
        ax.plot(grid, row["C"] * grid ** row["p"], "-", color=colours[rank], lw=1.1,
                label=f"tier {rank} law: C={row['C']:.3g}, p={row['p']:.2f}")
        o = row["out_of_sample"]
        ax.plot([o["distance"]], [o["actual_error"]], "s", mfc="none",
                mec=colours[rank], ms=7)
    ax.axvline(max(CRIT["C4"]["tiers"][0]["study_distances"]), color="0.6", ls="--", lw=1.0)
    ax.annotate("study range end", xy=(0.4, 2e-5), xytext=(0.44, 1.1e-5), fontsize=8,
                color="0.35")
    ax.annotate("out-of-sample at 1.5x study range\n(hollow square; tier 2 is 7 % "
                "short -> honest finding)",
                xy=(0.6, 1.77e-3), xytext=(0.30, 3.0e-3), fontsize=7.5,
                arrowprops=dict(arrowstyle="->", lw=0.8, color="0.4"))
    ax.set_yscale("log")
    ax.set_xscale("log")
    ax.set_xlabel("normalised extrapolation distance d (box widths)")
    ax.set_ylabel("error / risk bound")
    ax.set_title("C4 power-law extrapolation risk eps(d) = C d^p (p >= 1)", fontsize=9)
    ax.legend(fontsize=7, loc="lower right", ncol=2)
    path = FIG / "fig3_extrapolation_law.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_gate() -> Path:
    sweep = CRIT["C3"]["sweep"]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.6, 3.9),
                                  gridspec_kw={"width_ratios": [1.6, 1.0]})
    labels = ["inside"] + [f"{s['min_overshoot']:.0%}" for s in sweep[1:]]
    rates = [s["trigger_rate"] for s in sweep]
    bars = ax.bar(labels, rates, color=["#2ca02c"] + ["#d62728"] * (len(rates) - 1))
    for bar, rate in zip(bars, rates):
        ax.text(bar.get_x() + bar.get_width() / 2, rate + 0.02, f"{rate:.2f}",
                ha="center", fontsize=8)
    ax.axhline(0.05, color="k", ls="--", lw=1.0)
    ax.text(0.02, 0.09, "false-alarm budget 5 %", fontsize=7.5)
    ax.set_ylim(0, 1.15)
    ax.set_xlabel("minimum overshoot beyond the calibrated box")
    ax.set_ylabel("escalation rate")
    ax.set_title("C3 switch-gate trigger rate", fontsize=9)

    fp = CRIT["C3"]["false_alarm"]
    rows = CRIT["C1"]["margin_sensitivity_2d"]
    ax2.bar(["in-domain\nfalse alarms"], [fp["rate"]], color="#1f77b4",
            label=f"n={fp['n']}")
    ax2.axhline(0.05, color="k", ls="--", lw=1.0)
    ax2.set_ylim(0, 1.15)
    ax2.set_ylabel("rate")
    ax2.set_title("C3 in-domain false alarms", fontsize=9)
    ax2.text(0.02, 0.5, f"guard defaults: violations "
                        f"{rows[-2]['violations']}/{rows[-2]['n']} (2-D stress)",
             fontsize=7, rotation=0)
    path = FIG / "fig4_gate_behaviour.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> int:
    _style()
    FIG.mkdir(parents=True, exist_ok=True)
    chain = build_chain()
    paths = [fig_bound_vs_actual(chain), fig_coverage_control(),
             fig_extrapolation(chain), fig_gate()]
    for p in paths:
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
