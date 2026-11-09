#!/usr/bin/env python3
"""Evidence figure for the LB-t (2b-t/LB-t) OSS inventory.

Panels:
  (a) method-family coverage: implemented in tag v2.4 / scaffold-only at devel HEAD / absent
  (b) LOC per module for both revisions, measured with `cat <globs> | wc -l`

No simulation is run on purpose: at devel HEAD there is no collision operator
(include/lbt/populations/collision/bgk.hpp:56 "TODO: Implement collide-stream"),
so no convergence/field plot can be produced honestly. The deliverable is a
code-evidence figure.
"""
import json
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np

OUT = pathlib.Path(__file__).with_name("fig_lbt_coverage.png")

# 2 = implemented (tag v2.4 physics), 1 = scaffold/type-only (devel HEAD), 0 = absent
FAMILIES = [
    ("lattice D2Q9", 2), ("lattice D3Q15", 2), ("lattice D3Q19", 2), ("lattice D3Q27", 2),
    ("collision BGK", 2), ("collision TRT", 2), ("collision Smagorinsky-LES", 2),
    ("collision AVX2 intrinsics", 2), ("collision AVX512 intrinsics", 2),
    ("collision MRT", 0), ("collision KBC/entropic", 0), ("collision regularized (RR)", 0),
    ("forcing Guo", 0), ("forcing shifted-velocity", 0),
    ("BC half-way bounce-back", 2), ("BC Guo interpolation (u/p)", 2),
    ("BC Zou-He", 0), ("BC curvature-corrected BB", 0), ("BC immersed boundary", 0),
    ("MP SCMP (Shan-Chen)", 0), ("MP MCMP", 0), ("MP free-energy", 0),
    ("MP phase-field/Allen-Cahn", 0), ("MP color-gradient", 0), ("MP free-surface", 0),
    ("parallel OpenMP (shared)", 2), ("parallel MPI (distributed)", 0), ("GPU CUDA/HIP", 0),
    ("A-A pattern indexing", 2), ("A-B double-buffer pattern", 1),
    ("units + material library", 1), ("VTK geometry import", 1),
    ("gtest unit-test surface", 1), ("GitHub Actions CI", 1),
]
STATUS_TXT = {2: "impl", 1: "scaffold", 0: "absent"}

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(15.5, 9.2),
                               gridspec_kw={"width_ratios": [2.05, 1]})

codes = np.array([[c] for _, c in FAMILIES], dtype=float)
cmap = ListedColormap(["#d9534f", "#f0ad4e", "#5cb85c"])
ax0.imshow(codes, cmap=cmap, aspect="auto", vmin=0, vmax=2)
ax0.set_xticks([0])
ax0.set_xticklabels(["status"], fontsize=10)
ax0.set_yticks(range(len(FAMILIES)))
ax0.set_yticklabels([n for n, _ in FAMILIES], fontsize=8.5)
for i, (_, c) in enumerate(FAMILIES):
    ax0.text(0, i, STATUS_TXT[c], ha="center", va="center", fontsize=8,
             color="white" if c != 1 else "black", fontweight="bold")
ax0.set_title("(a) LB-t method families: impl in tag v2.4 / scaffold only at devel / absent",
              fontsize=10.5)
ax0.set_xticks(np.arange(-0.5, 1, 1), minor=True)
ax0.set_yticks(np.arange(-0.5, len(FAMILIES), 1), minor=True)
ax0.grid(which="minor", color="white", linewidth=0.8)

# panel (b): measured LOC (cat <globs> | wc -l on each revision)
mods = ["lattice", "collision", "boundary", "indexing\n+population",
        "continuum\n+geometry", "infra\n(omp/timer/vtk)", "units\n+materials", "gtest\ntests"]
v24 = [476, 1409, 732, 497, 671, 757, 0, 0]
devel = [489, 139, 297, 618, 1224, 1360, 2382, 5518]
x = np.arange(len(mods))
ax1.bar(x - 0.2, v24, 0.4, label="tag v2.4 (2020, physics)", color="#357ebd")
ax1.bar(x + 0.2, devel, 0.4, label="devel HEAD (2025, scaffolding+tests)", color="#f0ad4e")
for xi, (a, b) in enumerate(zip(v24, devel)):
    ax1.text(xi - 0.2, a + 70, str(a), ha="center", fontsize=7)
    ax1.text(xi + 0.2, b + 70, str(b), ha="center", fontsize=7)
ax1.set_xticks(x)
ax1.set_xticklabels(mods, fontsize=8, rotation=25, ha="right")
ax1.set_ylabel("LOC (with comments, wc -l)")
ax1.set_title("(b) LOC split: physics (v2.4) vs engineering scaffolding (devel)", fontsize=10.5)
ax1.legend(fontsize=8)
ax1.grid(axis="y", alpha=.3)

fig.suptitle("LB-t (2b-t/LB-t) code-evidence inventory - devel 162092a (2025-01-18) / tag v2.4 42d377a (2020-09-21)",
             fontsize=11.5)
fig.tight_layout(rect=[0, 0, 1, 0.96])
fig.savefig(OUT, dpi=140)
print(json.dumps({"figure": str(OUT), "bytes": OUT.stat().st_size,
                  "families": len(FAMILIES),
                  "implemented": sum(1 for _, c in FAMILIES if c == 2),
                  "scaffold": sum(1 for _, c in FAMILIES if c == 1),
                  "absent": sum(1 for _, c in FAMILIES if c == 0)}))