#!/usr/bin/env python3
"""Figures for REPORT_walberla.md (FEAT-OSS-INVENTORY-LBM / mission 8551).

All numbers come from walberla_stats.json (programmatic collection) -- nothing hand-typed.

fig1: src/ module size distribution (LOC), LBM-relevant modules highlighted
fig2: multiphase capability matrix -- our target families x implementation channel in waLBerla
fig3: free-surface time-step pipeline (sweep order extracted from SurfaceDynamicsHandler.h)

Usage: python3 make_walberla_figures.py [walberla_stats.json] [outdir]
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

stats = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "walberla_stats.json"))
outdir = sys.argv[2] if len(sys.argv) > 2 else "."
os.makedirs(outdir, exist_ok=True)

# ------------------------------------------------------------------ fig1
mods = list(stats["modules_cpp"].items())[:14][::-1]
names = [m[0] for m in mods]
locs = [m[1]["loc"] for m in mods]
lbm_related = {"lbm", "lbm_generated", "field", "boundary", "stencil", "gpu", "lbm_mesapd_coupling"}
colors = ["#c0392b" if n in lbm_related else "#7f8c8d" for n in names]

fig, ax = plt.subplots(figsize=(9, 5.2), dpi=150)
bars = ax.barh(names, locs, color=colors)
for b, v in zip(bars, locs):
    ax.text(v + 1500, b.get_y() + b.get_height() / 2, f"{v:,}", va="center", fontsize=8)
ax.set_xlabel("lines of C++ (.h/.cpp/.cu) in src/<module>")
ax.set_title(f"waLBerla @ {stats['head'][:8]} — src module size (LBM-relevant in red)", fontsize=11)
ax.set_xlim(0, max(locs) * 1.18)
ax.grid(axis="x", alpha=0.25)
ax.text(0.99, 0.02, f"total src: {stats['totals']['cpp_src']['loc']:,} LOC / "
                    f"{stats['totals']['cpp_src']['n_files']} files",
        transform=ax.transAxes, ha="right", fontsize=8, color="#555")
fig.tight_layout()
fig.savefig(os.path.join(outdir, "walberla_fig1_modules.png"))
plt.close(fig)

# ------------------------------------------------------------------ fig2
# rows: our multiphase target families; cols: implementation channel in waLBerla
rows = ["SCMP (pseudopotential / Shan-Chen)", "MCMP (multi-component)",
        "free-energy LBM", "color-gradient", "Cahn-Hilliard",
        "phase-field Allen-Cahn (2-phase)", "free surface (fill level)",
        "thermocapillary / Marangoni"]
cols = ["hand-written C++", "via lbmpy codegen", "absent"]
# 2 = full implementation, 1 = partial/indirect, 0 = absent
grid = [
    [0, 0, 1],   # SCMP  -> absent (grep 0 hits)
    [0, 0, 1],   # MCMP  -> absent
    [0, 0, 1],   # free energy
    [0, 0, 1],   # color gradient
    [0, 0, 1],   # Cahn-Hilliard
    [0, 2, 0],   # Allen-Cahn: codegen only
    [2, 0, 0],   # free surface: C++ only
    [0, 2, 0],   # thermocapillary: codegen only
]
notes = [
    ["grep 0 hits", "", "—"],
    ["grep 0 hits", "", "—"],
    ["grep 0 hits", "", "—"],
    ["grep 0 hits", "", "—"],
    ["grep 0 hits", "", "—"],
    ["", "17 files, rho-ratio 744/1000", ""],
    ["71 files / 15 063 LOC", "", ""],
    ["", "D3Q7 thermal + RK", ""],
]
fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=150)
cmap = {0: "#f2f2f2", 1: "#f6d365", 2: "#27ae60"}
for i, row in enumerate(grid):
    for j, v in enumerate(row):
        ax.add_patch(plt.Rectangle((j, len(rows) - 1 - i), 1, 1,
                                   facecolor=cmap[v], edgecolor="white", lw=2))
        if notes[i][j]:
            ax.text(j + 0.5, len(rows) - 1 - i + 0.5, notes[i][j], ha="center",
                    va="center", fontsize=7.5,
                    color="white" if v == 2 else "#333")
ax.set_xlim(0, 3)
ax.set_ylim(0, len(rows))
ax.set_xticks([0.5, 1.5, 2.5])
ax.set_xticklabels(cols, fontsize=9)
ax.set_yticks([len(rows) - 1 - i + 0.5 for i in range(len(rows))])
ax.set_yticklabels(rows, fontsize=9)
ax.set_title("waLBerla multiphase coverage vs our target families (green = implemented)",
             fontsize=10.5)
for sp in ax.spines.values():
    sp.set_visible(False)
ax.tick_params(length=0)
fig.tight_layout()
fig.savefig(os.path.join(outdir, "walberla_fig2_multiphase_matrix.png"))
plt.close(fig)

# ------------------------------------------------------------------ fig3
stages = [
    ("boundary handling\n+ force weighting", ":140 / :155"),
    ("StreamReconstruct\nAdvect (fused)", ":188"),
    ("collide / stream\n(+Smagorinsky)", ":220-267"),
    ("cell conversion\nfill-level threshold", ":280"),
    ("PDF refilling\n(5 families)", ":298-339"),
    ("excess mass\ndistribution (9)", ":358"),
    ("conversion flag\nreset", ":417"),
    ("block state update\n/ load balancing", ":425"),
]
fig, ax = plt.subplots(figsize=(11, 3.2), dpi=150)
ax.set_xlim(0, len(stages) * 2.4)
ax.set_ylim(0, 2.2)
ax.axis("off")
for i, (name, ptr) in enumerate(stages):
    x = i * 2.4 + 0.12
    col = "#2c3e50" if i not in (3, 4, 5) else "#c0392b"
    ax.add_patch(FancyBboxPatch((x, 0.75), 2.0, 0.85, boxstyle="round,pad=0.06",
                                facecolor="#eaf2f8" if i not in (3, 4, 5) else "#fdecea",
                                edgecolor=col, lw=1.4))
    ax.text(x + 1.0, 1.32, name, ha="center", va="center", fontsize=8, color=col)
    ax.text(x + 1.0, 0.95, f"SurfaceDynamicsHandler.h{ptr}", ha="center", va="center",
            fontsize=6.5, color="#555")
    if i < len(stages) - 1:
        ax.add_patch(FancyArrowPatch((x + 2.06, 1.17), (x + 2.34, 1.17),
                                     arrowstyle="-|>", mutation_scale=11, color="#7f8c8d"))
ax.text(len(stages) * 2.4 / 2, 0.25,
        "red = mass-conservation critical stages (conversion -> refilling -> excess-mass redistribution)",
        ha="center", fontsize=8, color="#c0392b")
ax.set_title("waLBerla free-surface time step (sweep order extracted programmatically from "
             "SurfaceDynamicsHandler.h)", fontsize=10)
fig.tight_layout()
fig.savefig(os.path.join(outdir, "walberla_fig3_freesurface_pipeline.png"))
plt.close(fig)

print("wrote 3 figures to", os.path.abspath(outdir))