#!/usr/bin/env python3
"""Figures for docs/oss_inventory_positionbaseddynamics/REPORT.md.
All curves come from the CSV files written by
artifacts/oss-inv-positionbaseddynamics/harness/pbd_fluid_harness.cpp (no hand-typed numbers).
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "..", "..", "artifacts", "oss-inv-positionbaseddynamics", "runs")
OUT = os.path.join(HERE, "figures")
os.makedirs(OUT, exist_ok=True)


def load(name):
    rows = []
    with open(os.path.join(RUNS, name)) as fh:
        rd = csv.DictReader(l for l in fh if not l.startswith("#"))
        for r in rd:
            rows.append({k: float(v) for k, v in r.items()})
    return rows


dam = load("dam_full.csv")     # harness step (serial, instrumented)
lib = load("libdam_full.csv")  # unmodified upstream TimeStepFluidModel::step
hyd = load("hydro.csv")
it = load("iter.csv")

# ---------------- fig 1: dam-break kinematics + incompressibility error -------------
fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.2))

t = np.array([r["t"] for r in dam]); fx = np.array([r["front_x"] for r in dam])
h = np.array([r["height"] for r in dam])
tl = np.array([r["t"] for r in lib]); fxl = np.array([r["front_x"] for r in lib])
hl = np.array([r["height"] for r in lib])
ax[0].plot(t, fx, "-", lw=2, label="front x (harness step)")
ax[0].plot(tl, fxl, "--", lw=1.5, label="front x (upstream step)")
ax[0].plot(t, h, "-", lw=2, label="max height (harness)")
ax[0].plot(tl, hl, "--", lw=1.5, label="max height (upstream)")
ax[0].axhline(2.0, color="k", ls=":", lw=1)
ax[0].text(0.05, 2.05, "right wall x = 2.0 m", fontsize=8)
ax[0].set_xlabel("t [s]"); ax[0].set_ylabel("position [m]")
ax[0].set_title("PBF dam-break (4500 fluid + 18630 boundary particles)")
ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3)

ax[1].plot(t, 100 * np.array([r["max_relerr"] for r in dam]), lw=1.8, label="max (rho/rho0-1)")
ax[1].plot(t, 100 * np.array([r["mean_relerr"] for r in dam]), lw=1.8, label="mean")
ax[1].axhline(3.0, color="r", ls="--", lw=1.2, label="3% acceptance bar")
ax[1].set_xlabel("t [s]"); ax[1].set_ylabel("relative density error [%]")
ax[1].set_title("Incompressibility residual, 5 density-solver iterations (upstream default)")
ax[1].legend(fontsize=8); ax[1].grid(alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_dambreak.png"), dpi=140)
plt.close(fig)

# ---------------- fig 2: iteration convergence + capability coverage ----------------
fig, ax = plt.subplots(1, 2, figsize=(11.5, 4.2))
mi = np.array([r["max_iter"] for r in it])
ax[0].plot(mi, 100 * np.array([r["max_relerr"] for r in it]), "o-", label="max")
ax[0].plot(mi, 100 * np.array([r["mean_relerr"] for r in it]), "s-", label="mean")
ax[0].axvline(5, color="k", ls=":", lw=1)
ax[0].text(5.1, 100 * float(it[-1]["max_relerr"]), "upstream maxIter = 5", fontsize=8,
           rotation=90, va="bottom")
ax[0].axhline(3.0, color="r", ls="--", lw=1.2, label="3% bar")
ax[0].set_xlabel("density-solver iterations (maxIter)")
ax[0].set_ylabel("relative density error after the step [%]")
ax[0].set_title("Jacobian-free projection converges sub-linearly")
ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3)

feats = [
    ("PBD/XPBD solids (rigid, cloth, rods)", 1),
    ("PBF density-constraint fluid (Macklin 2013)", 1),
    ("Akinci 2012 boundary particles (psi-volume)", 1),
    ("XSPH viscosity", 1),
    ("cubic-spline kernel (CubicKernel)", 1),
    ("spatial-hashing neighbour search (OpenMP)", 1),
    ("WCSPH / Tait EOS (explicit pressure)", 0),
    ("PCISPH / DFSPH / IISPH (divergence-free)", 0),
    ("ISPH (pressure Poisson)", 0),
    ("delta-SPH (density diffusion)", 0),
    ("artificial pressure / surface tension", 0),
    ("pressure field export (loads)", 0),
    ("multiphase / density ratio", 0),
    ("GPU (CUDA/HIP) backend", 0),
    ("MPI / distributed", 0),
    ("unit tests / quantitative validation data", 0),
]
names = [f[0] for f in feats][::-1]
vals = [f[1] for f in feats][::-1]
colors = ["#2a7f3f" if v else "#b03030" for v in vals]
ax[1].barh(range(len(vals)), [1] * len(vals), color=colors, alpha=0.85)
ax[1].set_yticks(range(len(vals)))
ax[1].set_yticklabels(names, fontsize=8)
ax[1].set_xticks([])
ax[1].set_xlim(0, 1)
for i, v in enumerate(vals):
    ax[1].text(0.5, i, "present" if v else "absent", ha="center", va="center",
               fontsize=7, color="white")
ax[1].set_title("Capability coverage, source-confirmed (grep-verified)")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_convergence_coverage.png"), dpi=140)
plt.close(fig)

# ---------------- hydrostatic numbers printed for the report ----------------
hy_t = np.array([r["t"] for r in hyd])
hy_h = np.array([r["height"] for r in hyd])
n = 9480
vp = 0.8 * (0.05 ** 3)
area = (79 * 0.05) * (15 * 0.05)
print("hydro: N=%d V=%.4f m3 area=%.4f m2 h_analytic=%.4f m" % (n, n * vp, area, n * vp / area))
print("hydro: h_measured_top_center_first=%.4f last=%.4f drift=%.2f%%" % (
    hy_h[0], hy_h[-1], 100 * (hy_h[-1] - hy_h[0]) / hy_h[0]))
print("hydro: max_relerr_last=%.4f%% mean_relerr_last=%.4f%% max_vel_last=%.4f" % (
    100 * hyd[-1]["max_relerr"], 100 * hyd[-1]["mean_relerr"], hyd[-1]["max_vel"]))
print("dam: peak max_relerr=%.3f%% mean_max=%.3f%%" % (
    100 * max(r["max_relerr"] for r in dam), 100 * max(r["mean_relerr"] for r in dam)))
print("dam: front@0.5s harness=%.4f lib=%.4f" % (
    np.interp(0.5, t, fx), np.interp(0.5, tl, fxl)))
print("iter: 1=>%.3f%% 5=>%.3f%% 10=>%.3f%% (max)" % (
    100 * it[0]["max_relerr"], 100 * it[4]["max_relerr"], 100 * it[-1]["max_relerr"]))
print("figs written to", OUT)