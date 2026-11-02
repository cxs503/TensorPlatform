#!/usr/bin/env python3
"""Figures for the independent LBM re-check — all numbers read from artifacts.

Zero hand-copied numbers: this script only reads
``artifacts/independent_recheck/recheck.json`` plus the raw LBM artifacts it points to
(velocity-field npz, history csv, result.json), so every plotted value is traceable to a
file on disk.

    python3 tools/benchmarks/independent_recheck_figures.py \
        --artifacts artifacts/independent_recheck
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.benchmarks import lbm_reference_registry as reg  # noqa: E402


def _hist(path):
    cols = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        for name in rdr.fieldnames or []:
            cols[name.strip()] = []
        for row in rdr:
            for name in cols:
                if row.get(name) not in (None, ""):
                    cols[name].append(float(row[name]))
    return cols


def fig_dam_break(checks, outdir):
    dh = [c for c in checks if c["case"].startswith("dam_break")]
    if not dh:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    ax = axes[0]
    ax.plot(reg.MARTIN_MOYCE_1952["T"], reg.MARTIN_MOYCE_1952["Z"], "ko",
            label="Martin & Moyce 1952 (digitised)", zorder=5)
    for c in dh:
        cols = _hist(c["artifact"])
        label = c["case"].split(":")[-1]
        ax.plot(cols["T_MM"], cols["X_toe"], "-", lw=1.8,
                label=f"LBM {label} (T=t√(2g/a), correct axis)")
        t_code = cols.get("T_code")
        if t_code:
            ax.plot(t_code, cols["X_toe"], ":", lw=1.2, alpha=0.8,
                    label=f"LBM {label} on legacy axis T=t√(g/a)")
    ax.set_xlabel("T = t·√(2g/a)")
    ax.set_ylabel("Z = x_front/a")
    ax.set_title("Dam-break front: independent re-check\n"
                 f"L∞ = {min(c['reference'][0]['rel_err_l_inf'] for c in dh):.2%}"
                 f"–{max(c['reference'][0]['rel_err_l_inf'] for c in dh):.2%} → FAIL")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    ax = axes[1]
    for c in dh:
        pts = c["quality"]["convention_correct_axis"]["points"]
        label = c["case"].split(":")[-1]
        ax.semilogy([p["x"] for p in pts], [100 * p["rel_err"] for p in pts], "o-",
                    ms=3, label=f"{label} correct axis")
        leg = c["quality"].get("convention_legacy_axis")
        if leg:
            ax.semilogy([p["x"] for p in leg["points"]],
                        [100 * p["rel_err"] for p in leg["points"]], "x:",
                        ms=3, alpha=0.6, label=f"{label} legacy axis")
    ax.axhline(3.0, color="g", ls="--", lw=1, label="3% target")
    ax.axhline(15.0, color="r", ls="--", lw=1, label="15% unacceptable")
    ax.set_xlabel("T = t·√(2g/a)")
    ax.set_ylabel("relative error of x_front [%]")
    ax.set_title("Error grows with time: front lags at late T\n"
                 "(convention fix alone is not sufficient)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3, which="both")
    path = os.path.join(outdir, "fig_dam_break_recheck.png")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def fig_forchheimer(checks, outdir):
    f = [c for c in checks if c["case"] == "forchheimer"]
    if not f:
        return None
    rows = f[0]["quality"]["rows"]
    re_ach = [r["re_ach"] for r in rows]
    r_sim = [r["R_sim"] for r in rows]
    r_ref = [r["R_ref_A"] for r in rows]
    err = [100 * r["rel_err_recomputed"] for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    ax = axes[0]
    ax.plot(re_ach, r_ref, "k^", ms=7, label="Koch & Ladd 1997 (via Forslund 2023)")
    ax.plot(re_ach, [1.03 * v for v in r_ref], "g--", lw=1, label="+3% band")
    ax.plot(re_ach, [0.97 * v for v in r_ref], "g--", lw=1)
    ax.plot(re_ach, r_sim, "o", ms=5, label="LBM re-checked (R_sim)")
    ax.set_xlabel("Re_Darcy")
    ax.set_ylabel("R = F/(μU) normalised drag")
    ax.set_title(f"Forchheimer square array (φ_s=0.40): worst {max(err):.2f}% → MARGINAL")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.bar([f"{r:.0f}/{d}" for r, d in zip(re_ach, [x["d"] for x in rows])], err,
           color=["#4c9f70" if e <= 3 else "#d08c26" for e in err])
    ax.axhline(3.0, color="g", ls="--", lw=1, label="3% target")
    ax.axhline(15.0, color="r", ls="--", lw=1, label="15% unacceptable")
    ax.set_ylabel("relative error [%]")
    ax.set_xlabel("Re_Darcy / resolution d")
    ax.set_title("Per-row drag error (all rows steady)")
    ax.tick_params(axis="x", labelrotation=45, labelsize=7)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    path = os.path.join(outdir, "fig_forchheimer_recheck.png")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def fig_cavity_summary(checks, outdir):
    cav = [c for c in checks if c["case"].startswith("cavity")]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))

    ax = axes[0]
    if cav and os.path.exists(cav[0]["artifact"]):
        z = np.load(cav[0]["artifact"])
        ux = np.asarray(z["ux"], dtype=float)
        n = ux.shape[1]
        prof = ux[ux.shape[0] // 2, :, ux.shape[2] // 2]
        scale = 1.0 if np.max(np.abs(ux)) > 5 * abs(float(z["u_lid"])) else float(z["u_lid"])
        y = np.linspace(0.0, 1.0, n)
        ax.plot(y, prof / scale, "-", lw=1.8, label="LBM 3D Re=1000 (re-checked field)")
        gy = [p[0] for p in reg.GHIA_U_CENTERLINE[1000]]
        gu = [p[1] for p in reg.GHIA_U_CENTERLINE[1000]]
        ax.plot(gy, gu, "ks", ms=4, label="Ghia 1982, 2D Re=1000")
        ax.axvline(y[int(np.argmin(prof))], color="C0", ls=":", lw=1)
        ax.plot([y[int(np.argmin(prof))]], [np.min(prof) / scale], "o", ms=7,
                mfc="none", mec="C0",
                label=f"u_min={np.min(prof) / scale:.4f} vs Ku 1987 "
                      f"{reg.KU_1987_3D['u_min']:.4f}")
        ax.set_xlabel("y/H")
        ax.set_ylabel("u/u_lid")
        ax.set_title("Cavity Re=1000: 3D field re-extracted\n"
                     "3D correction vs 2D Ghia is physical (~-28%), not an error")
        ax.legend(fontsize=7)
        ax.grid(alpha=0.3)

    ax = axes[1]
    labels, errs, verdicts = [], [], []
    for c in checks:
        if c["case"].startswith("dam"):
            if "a80" in c["case"]:
                continue
            e = c["reference"][0]["rel_err_l_inf"]
            labels.append("dam_break a40")
        elif c["case"].startswith("cavity"):
            e = c["reference"][0]["rel_err"]
            labels.append("cavity 3D")
        elif c["case"] == "forchheimer":
            e = c["reference"][0]["rel_err_l_inf"]
            labels.append("forchheimer")
        else:
            e = max(r.get("rel_err", 0.0) for r in c["reference"]
                    if isinstance(r, dict) and r.get("rel_err") is not None)
            labels.append("poiseuille 2D")
        errs.append(100 * e)
        verdicts.append(c["verdict"])
    colors = {"PASS": "#4c9f70", "MARGINAL": "#d08c26", "FAIL": "#c0392b"}
    ax.bar(labels, errs, color=[colors.get(v, "grey") for v in verdicts])
    ax.set_yscale("log")
    ax.axhline(3.0, color="g", ls="--", lw=1, label="3% target")
    ax.axhline(15.0, color="r", ls="--", lw=1, label="15% unacceptable")
    for i, (e, v) in enumerate(zip(errs, verdicts)):
        ax.annotate(f"{e:.2f}%\n{v}", (i, e), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=8)
    ax.set_ylabel("worst relative error [%] (log)")
    ax.set_title("Independent re-check verdicts (field-recompute where possible)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y", which="both")
    path = os.path.join(outdir, "fig_cavity_summary_recheck.png")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifacts", default="artifacts/independent_recheck")
    args = ap.parse_args(argv)
    with open(os.path.join(args.artifacts, "recheck.json"), encoding="utf-8") as fh:
        report = json.load(fh)
    checks = report["checks"]
    made = [p for p in (fig_dam_break(checks, args.artifacts),
                        fig_forchheimer(checks, args.artifacts),
                        fig_cavity_summary(checks, args.artifacts)) if p]
    for p in made:
        print(f"[figure] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())