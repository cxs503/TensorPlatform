#!/usr/bin/env python3
"""Figures for the FEM OSS inventory report (mission m8692).

This is a documentation/inventory task: there is no physical field quantity, so
contour plots are impossible by construction (stated in the report). The figures
below are the inventory's own quantitative surface:

  fig1  activity-vs-popularity map (stars vs last commit date, coloured by tier)
  fig2  tier composition + language distribution
  fig3  capability coverage per tier (the registry's "solution spectrum")
  fig4  license distribution by tier (reuse/compliance risk surface)

All numbers come from oss_fem_registry.json (built from live API captures).
"""
from __future__ import annotations

import json
import os
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "figures")
os.makedirs(FIG, exist_ok=True)

reg = json.load(open(os.path.join(HERE, "oss_fem_registry.json"), encoding="utf-8"))
repos = reg["repositories"]
TCOL = {1: "#1f77b4", 2: "#ff7f0e", 3: "#2ca02c"}
TLAB = {1: "tier1 complete FEM solver", 2: "tier2 differentiable/framework", 3: "tier3 specialist"}


def d(s: str) -> date:
    y, m, dd = (int(x) for x in s.split("-"))
    return date(y, m, dd)


# ---------------------------------------------------------------- fig1
fig, ax = plt.subplots(figsize=(11, 6))
for r in repos:
    ax.scatter(d(r["last_commit"]), r["stars"], s=40 + 8 * r["tier"],
               color=TCOL[r["tier"]], alpha=0.8, edgecolor="k", linewidth=0.4)
    short = r["repo"].split("/")[-1][:22]
    ax.annotate(short, (d(r["last_commit"]), r["stars"]), fontsize=6,
                xytext=(3, 3), textcoords="offset points")
ax.set_yscale("log")
ax.set_xlabel("last commit date (GitHub API /commits?per_page=1)")
ax.set_ylabel("GitHub stars (log)")
ax.set_title("FEM OSS inventory m8692: activity vs popularity, 40 API-verified repos")
ax.grid(alpha=0.3, which="both")
for t in (1, 2, 3):
    ax.scatter([], [], color=TCOL[t], label=TLAB[t], edgecolor="k", linewidth=0.4)
ax.legend(loc="upper left", fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig1_activity_vs_popularity.png"), dpi=150)
plt.close(fig)

# ---------------------------------------------------------------- fig2
langs, tiers = {}, {1: 0, 2: 0, 3: 0}
for r in repos:
    langs[r["lang"] or "n/a"] = langs.get(r["lang"] or "n/a", 0) + 1
    tiers[r["tier"]] += 1
fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
ax = axes[0]
ks = sorted(tiers)
ax.bar([TLAB[k] for k in ks], [tiers[k] for k in ks], color=[TCOL[k] for k in ks])
for k in ks:
    ax.text(TLAB[k], tiers[k] + 0.2, str(tiers[k]), ha="center", fontsize=10)
ax.set_ylabel("repos")
ax.set_title("Tier composition (n=40)")
ax.tick_params(axis="x", labelsize=7, rotation=12)
ax = axes[1]
lk = sorted(langs, key=lambda s: -langs[s])
ax.barh(lk[::-1], [langs[k] for k in lk][::-1], color="#444")
ax.set_xlabel("repos")
ax.set_title("Language distribution")
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig2_tier_language.png"), dpi=150)
plt.close(fig)

# ---------------------------------------------------------------- fig3
caps = ["geometry_elements", "assembly", "linear_elasticity", "thermal", "dynamics",
        "nonlinear_contact_fracture", "parallel_gpu", "differentiable"]
den = {1: sum(1 for r in repos if r["tier"] == 1),
       2: sum(1 for r in repos if r["tier"] == 2),
       3: sum(1 for r in repos if r["tier"] == 3)}
cov = {t: [100.0 * sum(1 for r in repos if r["tier"] == t and r["completeness"][c]) / den[t] for c in caps]
       for t in (1, 2, 3)}
fig, ax = plt.subplots(figsize=(11, 4.8))
w = 0.26
for i, t in enumerate((1, 2, 3)):
    ax.bar([x + (i - 1) * w for x in range(len(caps))], cov[t], width=w, color=TCOL[t],
           label=f"{TLAB[t]} (n={den[t]})")
ax.set_xticks(range(len(caps)))
ax.set_xticklabels([c.replace("_", "\n") for c in caps], fontsize=8)
ax.set_ylabel("% of tier with capability (README/description evidence)")
ax.set_title("Capability coverage by tier — where TensorFEM's own axes stand relative to OSS")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig3_capability_coverage.png"), dpi=150)
plt.close(fig)

# ---------------------------------------------------------------- fig4
lic_by_tier: dict[str, dict[int, int]] = {}
for r in repos:
    lic = r["license"] or "NOASSERTION"
    lic_by_tier.setdefault(lic, {1: 0, 2: 0, 3: 0})[r["tier"]] += 1
lics = sorted(lic_by_tier, key=lambda k: -sum(lic_by_tier[k].values()))
fig, ax = plt.subplots(figsize=(10, 4.6))
bottom = [0] * len(lics)
for t in (1, 2, 3):
    vals = [lic_by_tier[k][t] for k in lics]
    ax.bar(lics, vals, bottom=bottom, color=TCOL[t], label=TLAB[t])
    bottom = [b + v for b, v in zip(bottom, vals)]
for i, k in enumerate(lics):
    ax.text(i, bottom[i] + 0.15, str(bottom[i]), ha="center", fontsize=9)
ax.set_ylabel("repos")
ax.set_title("License distribution (GPL/LGPL decide reuse mode — see GAP-FEM-LICENSE-DEPENDENCY-MATRIX)")
ax.tick_params(axis="x", labelsize=8)
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig4_license_by_tier.png"), dpi=150)
plt.close(fig)

print("wrote", *sorted(os.listdir(FIG)), sep="\n  ")
print("tier den:", den)
print("coverage%:", {t: [round(v) for v in cov[t]] for t in cov})