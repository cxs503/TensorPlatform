#!/usr/bin/env python3
"""m1692 — figures + report tables for the fatigue load-path verification.

Reads artifacts/m1692/fatigue_load_path_{before,after}.json (produced by
scripts/verify_fatigue_load_path.py --tag before|after) and writes
  artifacts/m1692/fig1_wave_parameter_C.png
  artifacts/m1692/fig2_stress_transfer.png
  artifacts/m1692/fig3_life_transfer.png
  artifacts/m1692/fig4_damage_closure.png
  docs/m1692/TABLES.md   (markdown tables, numbers injected from JSON)
"""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "artifacts", "m1692")
DOCS = os.path.join(ROOT, "docs", "m1692")
os.makedirs(OUT, exist_ok=True)
os.makedirs(DOCS, exist_ok=True)

before = json.load(open(os.path.join(OUT, "fatigue_load_path_before.json"), encoding="utf-8"))
after = json.load(open(os.path.join(OUT, "fatigue_load_path_after.json"), encoding="utf-8"))

HS_REF = before["V2_hs_transfer"]["Hs_ref_m"]
SIG_DESIGN = before["V2_hs_transfer"]["sigma_design_MPa"]

# legacy (pre-fix) formulae, documented from the pre-fix source
def legacy_C(L):
    L = np.asarray(L, dtype=float)
    return np.where(L <= 300.0, 10.75 - ((300.0 - L) / 100.0) ** 1.5, 10.75)

def rule_C(L):
    L = np.asarray(L, dtype=float)
    return np.where(L <= 90.0, 0.0412 * L + 4.0,
                    np.where(L <= 300.0, 10.75 - ((300.0 - L) / 100.0) ** 1.5, 10.75))

# ---------------------------------------------------------------- fig 1 ----
L = np.linspace(40.0, 420.0, 761)
fig, ax = plt.subplots(figsize=(7.6, 4.6))
ax.plot(L, rule_C(L), "-", lw=2.4, color="#1f4e79", label="IACS UR S11 Rev.7 3.2.1 (baseline)")
ax.plot(L, legacy_C(L), "--", lw=1.8, color="#c00000", label="module, pre-fix (one formula everywhere)")
b = after["V1_wave_parameter"]["rows"]
ax.plot([r["L_m"] for r in b], [r["C_module"] for r in b], "o", ms=5.5,
        mfc="none", mec="#2e7d32", mew=1.6, label="module, post-fix (verified points)")
ax.axvline(90.0, color="grey", lw=0.8, ls=":")
ax.text(92, 7.0, "L = 90 m\nrule branch switch", fontsize=8, color="grey")
ax.set_xlabel("Length L [m]")
ax.set_ylabel("wave parameter C(L) [-]")
ax.set_title("V1  IACS UR S11 wave parameter: module vs rule")
ax.grid(alpha=0.3)
ax.legend(fontsize=8, loc="lower right")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_wave_parameter_C.png"), dpi=150)

# ---------------------------------------------------------------- fig 2 ----
rows_b = before["V2_hs_transfer"]["rows"]
rows_a = after["V2_hs_transfer"]["rows"]
hs = np.linspace(0.0, 14.0, 141)
fig, ax = plt.subplots(figsize=(7.6, 4.6))
ax.plot(hs, SIG_DESIGN * hs / HS_REF, "-", lw=2.4, color="#1f4e79",
        label=r"linear wave theory  $\sigma_d=\sigma_{11}\,H_s/11$ (baseline)")
ax.plot(hs, SIG_DESIGN * (hs / HS_REF) ** 2, "--", lw=1.8, color="#c00000",
        label=r"module, pre-fix  $\sigma_d=\sigma_{11}(H_s/11)^2$")
ax.plot([r["Hs_m"] for r in rows_a], [r["sigma_amp_module_MPa"] for r in rows_a], "o",
        ms=5.5, mfc="none", mec="#2e7d32", mew=1.6, label="module, post-fix")
ax.plot([HS_REF], [SIG_DESIGN], "*", ms=13, color="black",
        label="UR S11 design anchor (Hs = 11 m)")
ax.set_xlabel(r"significant wave height $H_s$ [m]")
ax.set_ylabel(r"stress amplitude $\sigma_a$ [MPa]")
ax.set_title(r"V2  Sea-state transfer: $\sigma_a(H_s)$ vs linear wave theory")
ax.grid(alpha=0.3)
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_stress_transfer.png"), dpi=150)

# ---------------------------------------------------------------- fig 3 ----
fig, ax = plt.subplots(figsize=(7.6, 4.6))
hb = [r["Hs_m"] for r in rows_b]
ax.semilogy(hb, [r["life_years_module"] for r in rows_b], "s--", color="#c00000",
            label="life, module pre-fix (quadratic law)")
ax.semilogy(hb, [r["life_years_linear"] for r in rows_b], "o-", color="#1f4e79",
            label="life, linear wave theory (baseline)")
ax2 = ax.twinx()
ax2.bar(hb, [r["life_ratio_module_over_linear"] for r in rows_b], width=0.55,
        color="#f2b3b3", alpha=0.75, zorder=0)
ax2.set_ylabel("life over-prediction factor  (module / linear)")
ax2.set_ylim(0, 14)
for x, r in zip(hb, rows_b):
    ax2.text(x, r["life_ratio_module_over_linear"] + 0.25,
             "%.1f" % r["life_ratio_module_over_linear"], ha="center", fontsize=7)
ax.set_xlabel(r"significant wave height $H_s$ [m]")
ax.set_ylabel("remaining life [years]  (log)")
ax.set_title("V2  Non-conservative life over-prediction of the pre-fix transfer law")
ax.grid(alpha=0.3, which="both")
h1, l1 = ax.get_legend_handles_labels()
ax.legend(h1, l1, fontsize=8, loc="lower right")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig3_life_transfer.png"), dpi=150)

# ---------------------------------------------------------------- fig 4 ----
v3 = after["V3_time_domain_closure"]
sk = after["V2_platform_linearity"]
fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.3))
axes[0].bar(["rain-flow\n(ASTM E1049-85)", "narrow-band\nRayleigh"],
            [v3["damage_rainflow"], v3["damage_narrowband"]],
            color=["#1f4e79", "#2e7d32"], width=0.55)
for i, v in enumerate([v3["damage_rainflow"], v3["damage_narrowband"]]):
    axes[0].text(i, v * 1.02, "%.4e" % v, ha="center", fontsize=8)
axes[0].set_ylabel("damage in 24 000 s of record [-]")
axes[0].set_title("V3  Time-domain closure (err = %.2f %%)" % v3["err_pct"])
axes[1].plot([r["Hs_m"] for r in sk["rows"]], [r["heave_rms_over_Hs"] for r in sk["rows"]],
             "o-", color="#1f4e79")
axes[1].set_xlabel(r"$H_s$ [m]")
axes[1].set_ylabel(r"heave rms / $H_s$  [m/m]")
axes[1].set_title("Platform linearity cross-check (spread %.2f %%)"
                  % sk["spread_heave_rms_over_Hs_pct"])
axes[1].grid(alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig4_damage_closure.png"), dpi=150)

# ---------------------------------------------------------------- tables ---
def fmt(x, n=3):
    return ("%%.%df" % n) % x

lines = []
lines.append("### T1  UR S11 wave parameter and moment (B = 30 m, Cb = 0.82)\n")
lines.append("| L [m] | C rule | C module post-fix | C err % | C module pre-fix | C err % (pre) | M_hog err % |")
lines.append("|---|---|---|---|---|---|---|")
for rb, ra in zip(before["V1_wave_parameter"]["rows"], after["V1_wave_parameter"]["rows"]):
    lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
        fmt(rb["L_m"], 0), fmt(ra["C_rule"], 4), fmt(ra["C_module"], 4), fmt(ra["C_err_pct"], 3),
        fmt(rb["C_module"], 4), fmt(rb["C_err_pct"], 3), fmt(ra["Mhog_err_pct"], 3)))

lines.append("\n### T2  Sea-state transfer of the stress amplitude (L=180, B=30, T=18, Cb=0.82, Tp=9 s)\n")
lines.append("| Hs [m] | sigma_a module pre-fix [MPa] | sigma_a post-fix [MPa] | sigma_a linear [MPa] | err pre % | err post % |")
lines.append("|---|---|---|---|---|---|")
for rb, ra in zip(before["V2_hs_transfer"]["rows"], after["V2_hs_transfer"]["rows"]):
    lines.append("| %s | %s | %s | %s | %s | %s |" % (
        fmt(rb["Hs_m"], 1), fmt(rb["sigma_amp_module_MPa"], 4), fmt(ra["sigma_amp_module_MPa"], 4),
        fmt(ra["sigma_amp_linear_MPa"], 4), fmt(rb["sigma_err_pct"], 2), fmt(ra["sigma_err_pct"], 2)))

lines.append("\n### T3  Damage / remaining life consequence of the transfer law\n")
lines.append("| Hs [m] | D/day pre-fix | D/day linear (baseline) | life pre-fix [a] | life baseline [a] | life over-prediction |")
lines.append("|---|---|---|---|---|---|")
for rb in before["V2_hs_transfer"]["rows"]:
    lines.append("| %s | %s | %s | %s | %s | x%s |" % (
        fmt(rb["Hs_m"], 1), "%.4e" % rb["damage_per_day_module"], "%.4e" % rb["damage_per_day_linear"],
        fmt(rb["life_years_module"], 1), fmt(rb["life_years_linear"], 1),
        fmt(rb["life_ratio_module_over_linear"], 2)))

lines.append("\n### T4  Independent anchors\n")
lines.append("| check | baseline | module post-fix | error |")
lines.append("|---|---|---|---|")
lines.append("| C(L), full range incl. L<=90 and L>350 | IACS UR S11 Rev.7 3.2.1 | `wave_parameter_C` | %.4f %% (max) |"
             % after["summary"]["V1_max_C_err_pct"])
lines.append("| sigma_a(2Hs)/sigma_a(Hs) | 2.0 (linear wave theory) | %s | %.4f %% |" % (
    fmt(after["V2_self_similarity"]["ratio_sigma_8_over_4"], 6),
    after["summary"]["V2_self_similarity_err_pct"]))
lines.append("| heave rms / Hs | constant (linear RAO) | %s | spread %.4f %% |" % (
    fmt(after["V2_platform_linearity"]["rows"][0]["heave_rms_over_Hs"], 6),
    after["V2_platform_linearity"]["spread_heave_rms_over_Hs_pct"]))
lines.append("| narrow-band damage vs rain-flow | ASTM E1049-85 count on 120 000-pt record | %.6e vs %.6e | %s %% |" % (
    after["V3_time_domain_closure"]["damage_narrowband"], after["V3_time_domain_closure"]["damage_rainflow"],
    fmt(after["V3_time_domain_closure"]["err_pct"], 3)))

with open(os.path.join(DOCS, "TABLES.md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print("figures + TABLES.md written")
print("summary post-fix:", json.dumps(after["summary"], ensure_ascii=False))
print("summary pre-fix :", json.dumps(before["summary"], ensure_ascii=False))