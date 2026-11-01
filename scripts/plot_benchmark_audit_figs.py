#!/usr/bin/env python3
"""Figures for the benchmark-dataset integrity audit (v1.0 -> v1.1).

Reads the repaired dataset, the archived v1.0 copy and the audit JSON, and
writes three two-panel figures into ``artifacts/benchmark_dataset_audit/``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
AUD = ROOT / "artifacts" / "benchmark_dataset_audit"
CUR = json.loads((ROOT / "backend/data/benchmark_reference.json").read_text())
OLD = json.loads((AUD / "benchmark_reference.orig.json").read_text())
AUDIT = json.loads((AUD / "audit.json").read_text())

G = 9.80665
COL = {"SUBOFF": "#1f77b4", "DTMB-5415": "#d62728", "KVLCC2": "#2ca02c"}


def cf57(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


# --- Fig 1: Froude closure, v1.0 vs v1.1 -----------------------------------
fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.8))
for ax, data, title in (
    (axes[0], OLD, "v1.0 (as stored)"),
    (axes[1], CUR, "v1.1 (repaired)"),
):
    for c in data["cases"]:
        L = c["geometry"]["length_m"]
        fr_calc = c["speed_ms"] / math.sqrt(G * L)
        ax.scatter(fr_calc, c["froude_number"], s=70,
                   color=COL.get(c["ship_type"], "k"), label=c["ship_type"], zorder=3)
    ax.plot([0, 2.3], [0, 2.3], "k--", lw=1)
    ax.axhspan(0, 0.5, color="green", alpha=0.06)
    ax.set_xlim(0, 2.3); ax.set_ylim(0, 2.3)
    ax.set_xlabel(r"$V/\sqrt{gL}$")
    ax.set_ylabel("stored Froude number")
    ax.set_title(f"{title}: Froude closure")
    ax.grid(alpha=0.3)
h, l = axes[0].get_legend_handles_labels()
u = dict(zip(l, h))
axes[1].legend(u.values(), u.keys(), fontsize=8, loc="upper left")
axes[1].text(0.9, 0.25, "physical\ndisplacement-hull\nband (Fr<0.5)", fontsize=7,
             color="green")
fig.suptitle("K1: stored vs recomputed Froude number (DTMB-5415 block was 82 % off)")
fig.tight_layout()
fig.savefig(AUD / "fig1_froude_closure.png", dpi=150)
plt.close(fig)

# --- Fig 2: Ct against the ITTC-57 friction bound --------------------------
fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.8))
ax = axes[0]
rek = [10 ** (6 + 0.02 * i) for i in range(0, 200)]
ax.plot(rek, [cf57(r) for r in rek], "k-", lw=1.3, label=r"ITTC-57 $C_F$ (floor)")
ax.plot(rek, [1.4 * cf57(r) for r in rek], "k:", lw=1.0, label=r"$1.4\,C_F$")
for c in CUR["cases"]:
    L = c["geometry"]["length_m"]
    re = c["speed_ms"] * L / c["kinematic_viscosity_m2_s"]
    ax.scatter(re, c["experimental_ct"], s=70, color=COL.get(c["ship_type"], "k"),
               zorder=3)
ax.set_xscale("log"); ax.set_yscale("log")
ax.set_xlabel(r"$Re = VL/\nu$ (working frame)")
ax.set_ylabel(r"experimental $C_T$")
ax.set_title("K4: $C_T$ stays above flat-plate friction")
ax.grid(alpha=0.3, which="both"); ax.legend(fontsize=8)

ax = axes[1]
for row in AUDIT["cases"]:
    k4 = row["checks"]["K4_friction_bound"]
    ax.scatter(k4["Re"], k4["Ct_over_Cf"], s=70, zorder=3,
               color=COL.get(row["ship_type"], "k"))
ax.axhline(1.0, color="red", lw=1.2, ls="--", label="physical floor (1.0)")
ax.axhline(2.5, color="orange", lw=1.0, ls=":", label="advisory ceiling (2.5)")
ax.set_xscale("log")
ax.set_xlabel("Reynolds number")
ax.set_ylabel(r"$C_T/C_F$")
ax.set_title(r"K4: form + wave allowance ratio $C_T/C_F$")
ax.grid(alpha=0.3, which="both"); ax.legend(fontsize=8)
fig.suptitle("K4: experimental $C_T$ versus the ITTC-57 friction bound (all 12 rows pass)")
fig.tight_layout()
fig.savefig(AUD / "fig2_ct_vs_friction.png", dpi=150)
plt.close(fig)

# --- Fig 3: v1.0 force/coefficient inconsistency ---------------------------
fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.8))
ax = axes[0]
ids, ratios = [], []
for c in OLD["cases"]:
    rho = 1025.0 if c["ship_type"] == "KVLCC2" else 998.2
    rt = c["experimental_rt_n"]
    if rt is None:
        continue
    implied_s = rt / (0.5 * rho * c["speed_ms"] ** 2 * c["experimental_ct"])
    ids.append(c["case_id"]); ratios.append(implied_s)
ax.barh(ids, ratios, color="#8c564b")
ax.set_xscale("log")
ax.axvline(5.42, color="#1f77b4", lw=1.4, ls="--",
           label="SUBOFF wetted surface stored = 5.42 m$^2$")
ax.set_xlabel("implied wetted surface  $R_T/(0.5\\rho V^2 C_T)$  [m$^2$]")
ax.set_title("v1.0: stored $R_T$ implies unphysical wetted area")
ax.grid(alpha=0.3, axis="x"); ax.legend(fontsize=8)
ax.invert_yaxis()

ax = axes[1]
sub = [c for c in CUR["cases"] if c["ship_type"] == "SUBOFF"]
V = [c["speed_ms"] for c in sub]
rt_new = [c["experimental_rt_n"] for c in sub]
rt_old = [c["experimental_rt_n"] for c in OLD["cases"] if c["ship_type"] == "SUBOFF"]
ax.plot(V, rt_new, "o-", color="#1f77b4", label=r"v1.1 $0.5\rho S V^2 C_T$")
ax.plot(V, rt_old, "s--", color="#d62728", label="v1.0 stored")
ax.set_xlabel("speed [m/s]")
ax.set_ylabel("$R_T$ [N]")
ax.set_title("K5: SUBOFF force closure (7.4x low in v1.0)")
ax.grid(alpha=0.3); ax.legend(fontsize=8)
fig.suptitle("K5/K7: force-coefficient closure of the reference rows")
fig.tight_layout()
fig.savefig(AUD / "fig3_rt_closure.png", dpi=150)
plt.close(fig)

print("wrote fig1_froude_closure.png, fig2_ct_vs_friction.png, fig3_rt_closure.png")