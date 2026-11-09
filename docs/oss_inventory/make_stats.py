#!/usr/bin/env python3
"""Measure lbmpy source stats (real numbers, zero hand-copying) and emit JSON + figures.

Input:  a checkout of github.com/lssfau/lbmpy (path via LBMPY_SRC, default /tmp/lbmpy_src)
Output: docs/oss_inventory/lbmpy_stats.json
        docs/oss_inventory/fig1_surface.png   implementer-surface counts per capability axis
        docs/oss_inventory/fig2_modules.png   module LOC distribution
        docs/oss_inventory/fig3_multiphase.png multiphase family coverage matrix
"""
import json
import os
import re
import subprocess
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC = os.environ.get("LBMPY_SRC", "/tmp/lbmpy_src")
OUT = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(SRC, "src", "lbmpy")


def loc(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def grep(pattern, path, flags=""):
    """Return list of (lineno, line) matches, like grep -n."""
    rx = re.compile(pattern, re.M | (re.I if "i" in flags else 0))
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if rx.search(line):
                out.append((i, line.rstrip("\n")))
    return out


def py_files(root):
    acc = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if n.endswith(".py"):
                acc.append(os.path.join(dirpath, n))
    return sorted(acc)


def head_commit():
    try:
        return subprocess.run(["git", "-C", SRC, "log", "-1", "--format=%H|%ad|%s"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception as e:  # pragma: no cover
        return f"unknown ({e})"


stats = {}
stats["head"] = head_commit()

files = py_files(PKG)
stats["n_py_src"] = len(files)
stats["src_loc_total"] = sum(loc(f) for f in files)
stats["n_py_all"] = len([f for f in py_files(SRC) if "/.git/" not in f])
stats["all_py_loc"] = sum(loc(f) for f in py_files(SRC))

# ---- module LOC ranking -------------------------------------------------------
mod = []
for f in files:
    rel = os.path.relpath(f, PKG)
    mod.append((rel, loc(f)))
mod.sort(key=lambda t: -t[1])
stats["top_modules"] = mod[:14]

# ---- capability surface counts (class/def definitions per family) --------------
fam = {}
fam["stencils"] = [f"{os.path.relpath(p, PKG)}:{n}" for p in [os.path.join(PKG, "velocityspace/standardstencils.py")]
                   for n, l in grep(r"^class ", p)]
fam["collision_factories"] = [(os.path.relpath(p, PKG) + ":" + str(n), l.split("(")[0][4:])
                               for p in [os.path.join(PKG, "methods/creationfunctions.py")]
                               for n, l in grep(r"^def create_", p)]
fam["force_models"] = [(os.path.relpath(p, PKG) + ":" + str(n), l.split("(")[0][6:].split(":")[0])
                       for p in [os.path.join(PKG, "forcemodels.py")]
                       for n, l in grep(r"^class ", p)]
fam["boundaries"] = [(os.path.relpath(p, PKG) + ":" + str(n), l.split("(")[0][6:].split(":")[0])
                     for p in [os.path.join(PKG, "boundaries/boundaryconditions.py")]
                     for n, l in grep(r"^class ", p)
                     if not l.startswith("class LbBoundary")]
stats["families"] = fam

# ---- multiphase capability matrix (evidence per family) -----------------------
def has(pat, paths):
    hits = []
    for p in paths:
        full = os.path.join(PKG, p)
        if os.path.exists(full):
            hits += [(p, n) for n, _ in grep(pat, full)]
    return hits


mp_paths = ["phasefield/phasefieldstep.py", "phasefield/kerneleqs.py",
            "phasefield/nphase_nestler.py", "phasefield/n_phase_boyer.py",
            "phasefield/high_density_ratio_model.py", "phasefield/eos.py",
            "phasefield_allen_cahn/kernel_equations.py",
            "phasefield_allen_cahn/parameter_calculation.py", "forcemodels.py"]

mp = {
    "SCMP (single-component two-phase)": has(r"^class ShanChen|def psi", ["forcemodels.py"]),
    "Cahn-Hilliard (conserved, n-phase)": has(r"^def free_energy_functional_n_phases|^class PhaseFieldStep",
                                              ["phasefield/analytical.py", "phasefield/phasefieldstep.py"]),
    "Allen-Cahn (non-conserved, contact angle)": has(r"^def chemical_potential_symbolic|^def surface_tension_force",
                                                     ["phasefield_allen_cahn/kernel_equations.py"]),
    "EOS-driven free energy (C-S / vdW)": has(r"^def carnahan_starling_eos|^def van_der_walls_eos|^def maxwell_construction",
                                              ["phasefield/eos.py"]),
    "high density ratio free energy": has(r"^def free_energy_high_density_ratio",
                                          ["phasefield/high_density_ratio_model.py"]),
    "pressure-tensor forcing": has(r"^def pressure_tensor_kernel|^def pressure_force",
                                   ["phasefield/kerneleqs.py", "phasefield_allen_cahn/kernel_equations.py"]),
    "free surface (fill fraction / VOF)": has(r"fill.?fraction|volume.?of.?fluid|free.?surface", [])
    + has(r"free.surface", ["fieldaccess.py"]),
    "spurious-current suppression": has(r"well.?balanced|spurious", [p for p in mp_paths]),
}
stats["multiphase_hits"] = {k: len(v) for k, v in mp.items()}
stats["multiphase_detail"] = {k: [f"{p}:{n}" for p, n in v][:6] for k, v in mp.items()}

# ---- tests / CI surface -------------------------------------------------------
tests = py_files(os.path.join(SRC, "tests"))
stats["n_test_files"] = len(tests)
def nb_files(root):
    acc = []
    for dirpath, _dirs, names in os.walk(root):
        acc += [n for n in names if n.endswith(".ipynb")]
    return acc


stats["n_test_notebooks"] = len(nb_files(os.path.join(SRC, "tests")))
stats["n_notebooks_doc"] = len(nb_files(os.path.join(SRC, "doc", "notebooks")))
stats["has_gitlab_ci"] = os.path.exists(os.path.join(SRC, ".gitlab-ci.yml"))
stats["license"] = [l.strip() for l in open(os.path.join(SRC, "LICENSE"), encoding="utf-8")][:2]

with open(os.path.join(OUT, "lbmpy_stats.json"), "w", encoding="utf-8") as f:
    json.dump(stats, f, indent=2, ensure_ascii=False)

# =============================== figures =======================================
plt.rcParams.update({"font.size": 9})

# Fig 1: implementer surface per capability axis
axes_label = ["lattice stencils", "collision factories", "force models", "boundary conditions"]
counts = [len(fam["stencils"]), len(fam["collision_factories"]), len(fam["force_models"]),
          len(fam["boundaries"])]
fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=160)
bars = ax.bar(axes_label, counts, color=["#4C72B0", "#55A868", "#C44E52", "#8172B2"])
for b, c in zip(bars, counts):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.15, str(c), ha="center", fontsize=10)
ax.set_ylabel("number of public classes / factory functions")
ax.set_title("lbmpy method surface measured from source (src/lbmpy, HEAD %s)" % stats["head"].split("|")[0][:8],
             fontsize=9)
ax.set_ylim(0, max(counts) + 2)
ax.grid(axis="y", alpha=.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_surface.png"))
plt.close(fig)

# Fig 2: module LOC distribution
top = stats["top_modules"][:14][::-1]
names = [t[0] for t in top]
vals = [t[1] for t in top]
fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=160)
ax.barh(names, vals, color="#4C72B0")
ax.set_xlabel("source lines of code")
ax.set_title(f"lbmpy module size (top 14 of {stats['n_py_src']} files, "
             f"{stats['src_loc_total']} LOC total)", fontsize=9)
for i, v in enumerate(vals):
    ax.text(v + 8, i, str(v), va="center", fontsize=7)
ax.grid(axis="x", alpha=.3)
ax.tick_params(labelsize=7)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_modules.png"))
plt.close(fig)

# Fig 3: multiphase coverage matrix (evidence-count heat map)
labels = list(mp.keys())
hitn = [len(mp[k]) for k in labels]
fig, ax = plt.subplots(figsize=(7.6, 3.8), dpi=160)
colors = ["#55A868" if h else "#C44E52" for h in hitn]
bars = ax.barh(labels, [max(h, 0) for h in hitn], color=colors)
for b, h in zip(bars, hitn):
    ax.text(max(h, 0) + 0.2, b.get_y() + b.get_height() / 2,
            str(h) if h else "ABSENT (0)", va="center", fontsize=8)
ax.set_xlabel("number of code anchors (def/class matches) found")
ax.set_title("lbmpy multiphase / free-surface capability coverage (0 = verified absent)", fontsize=9)
ax.set_xlim(0, max(hitn) + 4)
ax.tick_params(labelsize=7.5)
ax.grid(axis="x", alpha=.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig3_multiphase.png"))
plt.close(fig)

print(json.dumps({k: stats[k] for k in ["head", "n_py_src", "src_loc_total", "n_test_files",
                                        "n_test_notebooks", "n_notebooks_doc",
                                        "multiphase_hits", "license"]}, indent=2, ensure_ascii=False))
print("FAMILIES:", {k: len(v) for k, v in fam.items()})