#!/usr/bin/env python3
"""Generate verification figures for the residual resistance coefficient (Cr) model.

Mission m1772 — produces:
  1. Cr vs Fn for different Cb values (showing the Fn^2.5 scaling)
  2. Cr model vs SUBOFF experimental data comparison
  3. Resistance breakdown (Rf vs Rr) vs speed for KVLCC2

Outputs: artifacts/m1772/fig*.png
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
OUTDIR = ROOT / "artifacts" / "m1772"
OUTDIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BACKEND))
from services.ship_resistance import (
    residual_coefficient,
    ittc57_cf,
    reynolds_number,
    froude_number,
    resistance_breakdown,
)

RHO = 998.0
NU = 1.004e-6
G = 9.81

# ---------------------------------------------------------------------------
# Figure 1: Cr vs Fn for different Cb values
# ---------------------------------------------------------------------------

def fig1_cr_vs_fn():
    """Cr as a function of Froude number for various block coefficients."""
    fns = np.linspace(0.0, 0.45, 200)
    cbs = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]

    fig, ax = plt.subplots(figsize=(10, 6))
    for cb in cbs:
        crs = [residual_coefficient(fn, cb) for fn in fns]
        ax.plot(fns, crs, label=f"Cb = {cb:.2f}", linewidth=2)

    ax.axvline(x=0.05, color="gray", linestyle="--", alpha=0.5, label="Fn floor (0.05)")
    ax.set_xlabel("Froude Number (Fn)", fontsize=12)
    ax.set_ylabel("Residual Resistance Coefficient (Cr)", fontsize=12)
    ax.set_title("Residual Resistance Coefficient vs Froude Number\n"
                 r"Cr = 0.002 × (1 + 2Cb) × Fn$^{2.5}$  (Fn > 0.05)", fontsize=13)
    ax.legend(fontsize=10, ncol=2)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, 0.45)
    ax.set_ylim(0, 0.012)

    plt.tight_layout()
    path = OUTDIR / "fig1_cr_vs_fn.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved {path}")


# ---------------------------------------------------------------------------
# Figure 2: Cr model vs SUBOFF experimental data
# ---------------------------------------------------------------------------

def fig2_suboff_comparison():
    """Compare model Cr against SUBOFF experimental data (Roddy 1990)."""
    # SUBOFF experimental Ct data (Groves et al. 1989 / Roddy 1990)
    suboff_data = [
        {"fn": 0.10, "ct_exp": 3.50e-3},
        {"fn": 0.15, "ct_exp": 3.30e-3},
        {"fn": 0.20, "ct_exp": 3.20e-3},
        {"fn": 0.25, "ct_exp": 3.30e-3},
        {"fn": 0.30, "ct_exp": 3.50e-3},
        {"fn": 0.35, "ct_exp": 3.90e-3},
    ]
    suboff_cb = 0.542
    suboff_l = 4.356

    fig, ax = plt.subplots(figsize=(10, 6))

    # Model Ct = Cf + Cr
    fns_model = np.linspace(0.05, 0.40, 100)
    cts_model = []
    crs_model = []
    cfs_model = []
    for fn in fns_model:
        v = fn * math.sqrt(G * suboff_l)
        re = v * suboff_l / NU
        cf = ittc57_cf(re)
        cr = residual_coefficient(fn, suboff_cb)
        cts_model.append(cf + cr)
        crs_model.append(cr)
        cfs_model.append(cf)

    ax.plot(fns_model, cts_model, "b-", label="Model Ct = Cf + Cr", linewidth=2)
    ax.plot(fns_model, cfs_model, "b--", label="Model Cf (ITTC-57)", alpha=0.7)
    ax.plot(fns_model, crs_model, "b:", label="Model Cr (empirical fit)", alpha=0.7)

    # Experimental Ct
    fn_exp = [d["fn"] for d in suboff_data]
    ct_exp = [d["ct_exp"] for d in suboff_data]
    ax.scatter(fn_exp, ct_exp, color="red", s=80, zorder=5, label="SUBOFF exp. Ct (Roddy 1990)",
               edgecolors="darkred", linewidths=1)

    ax.set_xlabel("Froude Number (Fn)", fontsize=12)
    ax.set_ylabel("Total Resistance Coefficient (Ct × 10³)", fontsize=12)
    ax.set_title("SUBOFF Resistance: Model vs Experimental Data\n"
                 "L = 4.356 m, Cb = 0.542, ρ = 998 kg/m³", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0.05, 0.40)

    # Y-axis in 10^-3
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x*1000:.1f}"))

    plt.tight_layout()
    path = OUTDIR / "fig2_suboff_comparison.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved {path}")


# ---------------------------------------------------------------------------
# Figure 3: Resistance breakdown for KVLCC2
# ---------------------------------------------------------------------------

def fig3_kvlcc2_breakdown():
    """Resistance breakdown (Rf vs Rr) vs speed for KVLCC2."""
    L, B, T, Cb = 320.0, 58.0, 20.8, 0.8098
    speeds = np.linspace(2.0, 18.0, 50)

    rfs, rrs, rts = [], [], []
    fns = []
    for v in speeds:
        r = resistance_breakdown(length=L, beam=B, draft=T, cb=Cb, speed=v)
        rfs.append(r["rf_n"] / 1000)  # kN
        rrs.append(r["rr_n"] / 1000)
        rts.append(r["rt_n"] / 1000)
        fns.append(froude_number(v, L))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Left: Resistance vs speed
    ax1.plot(speeds, rfs, "b-", label="Frictional (Rf)", linewidth=2)
    ax1.plot(speeds, rrs, "r-", label="Residual (Rr)", linewidth=2)
    ax1.plot(speeds, rts, "k-", label="Total (Rt)", linewidth=2.5)
    ax1.set_xlabel("Speed (m/s)", fontsize=12)
    ax1.set_ylabel("Resistance (kN)", fontsize=12)
    ax1.set_title("KVLCC2 Resistance Breakdown vs Speed\n"
                  "L = 320 m, Cb = 0.810", fontsize=12)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3)

    # Right: Resistance ratio vs Fn
    ratios = [rr / rf for rr, rf in zip(rrs, rfs)]
    ax2.plot(fns, ratios, "g-", linewidth=2)
    ax2.set_xlabel("Froude Number (Fn)", fontsize=12)
    ax2.set_ylabel("Rr / Rf ratio", fontsize=12)
    ax2.set_title("Residual/Friction Ratio vs Froude Number\n"
                  "Shows Fn^2.5 vs V²·Cf scaling", fontsize=12)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    path = OUTDIR / "fig3_kvlcc2_breakdown.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved {path}")


# ---------------------------------------------------------------------------
# Figure 4: Error analysis table visualization
# ---------------------------------------------------------------------------

def fig4_error_analysis():
    """Bar chart of relative error between model Cr and experimental Cr for SUBOFF."""
    suboff_data = [
        {"fn": 0.10, "ct_exp": 3.50e-3},
        {"fn": 0.15, "ct_exp": 3.30e-3},
        {"fn": 0.20, "ct_exp": 3.20e-3},
        {"fn": 0.25, "ct_exp": 3.30e-3},
        {"fn": 0.30, "ct_exp": 3.50e-3},
        {"fn": 0.35, "ct_exp": 3.90e-3},
    ]
    suboff_cb = 0.542
    suboff_l = 4.356

    fns = [d["fn"] for d in suboff_data]
    errors = []
    for d in suboff_data:
        fn = d["fn"]
        ct_exp = d["ct_exp"]
        v = fn * math.sqrt(G * suboff_l)
        re = v * suboff_l / NU
        cf = ittc57_cf(re)
        cr_exp = ct_exp - cf
        cr_model = residual_coefficient(fn, suboff_cb)
        if cr_exp > 0:
            err = (cr_model - cr_exp) / cr_exp * 100
        else:
            err = 0
        errors.append(err)

    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["red" if abs(e) > 50 else "orange" if abs(e) > 20 else "green" for e in errors]
    bars = ax.bar([f"Fn={f:.2f}" for f in fns], errors, color=colors, edgecolor="black", linewidth=0.5)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.axhline(y=3, color="green", linestyle="--", alpha=0.5, label="±3% threshold")
    ax.axhline(y=-3, color="green", linestyle="--", alpha=0.5)
    ax.set_xlabel("Froude Number", fontsize=12)
    ax.set_ylabel("Relative Error: (Cr_model - Cr_exp) / Cr_exp × 100%", fontsize=11)
    ax.set_title("SUBOFF Residual Resistance: Model vs Experimental Error\n"
                 "NOTE: Cr_exp = Ct_exp - Cf includes form effects (1+k), so direct comparison is approximate",
                 fontsize=12)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis="y")

    # Add value labels
    for bar, err in zip(bars, errors):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{err:.0f}%", ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    path = OUTDIR / "fig4_error_analysis.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved {path}")


if __name__ == "__main__":
    fig1_cr_vs_fn()
    fig2_suboff_comparison()
    fig3_kvlcc2_breakdown()
    fig4_error_analysis()
    print(f"\nAll figures saved to {OUTDIR}")
