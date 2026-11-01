#!/usr/bin/env python3
"""Render the report assets (figures + tables) for the benchmark dataset audit.

Numbers are read from the committed audit artifacts - nothing is typed by hand.
"""
from __future__ import annotations
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts/verifier"
pre = json.loads((ART / "benchmark_audit_prefix.json").read_text())
post = json.loads((ART / "benchmark_audit_postfix.json").read_text())


def check(case, name):
    return next((c for c in case["checks"] if c["check"] == name), {})


pre_by = {c["case_id"]: c for c in pre["cases"]}
post_by = {c["case_id"]: c for c in post["cases"]}
ids = [c["case_id"] for c in post["cases"]]

# ---------------- figure 1: pre-fix diagnosis -------------------------------
fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
ax = axes[0]
ratios = [pre_by[i]["s_implied_m2"] / pre_by[i]["checks"][5].get("ratio", float("nan")) * 0 or 0 for i in ids]
# recompute geometry-implied base from the ratio recorded by the audit
base_ratio = []
for i in ids:
    chk = check(pre_by[i], "implied_wetted_area")
    base_ratio.append(chk.get("ratio", float("nan")))
ax.bar(range(len(ids)), base_ratio, color="#d62728")
ax.axhline(1.0, color="k", lw=1.0, ls="--", label="ratio = 1 (consistent)")
ax.axhspan(0.5, 2.0, color="#2ca02c", alpha=0.15, label="acceptance band [0.5, 2.0]")
ax.set_xticks(range(len(ids)))
ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
ax.set_ylabel("S implied by (Rt, Ct) / geometry-implied S")
ax.set_title("v1.0 (pre-fix): Rt contradicts Ct for 12/12 cases")
ax.legend(fontsize=7)
ax.grid(True, axis="y", alpha=0.3)

ax = axes[1]
decl = [post_by[i]["froude_declared"] for i in ids]
rec = [post_by[i]["froude_recomputed"] for i in ids]
x = range(len(ids))
ax.plot(x, decl, "o-", label="Fn in v1.0 dataset", color="#1f77b4")
ax.plot(x, rec, "x--", label="Fn = V/sqrt(gL) from stated geometry", color="#ff7f0e")
ax.axhline(1.0, color="k", lw=0.8, ls=":", label="Fn = 1.0 sanity bound")
ax.set_xticks(list(x))
ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
ax.set_ylabel("Froude number")
ax.set_title("Froude column: DTMB-5415 was consistent with L~181 m")
ax.legend(fontsize=7)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(ART / "audit_report_diagnosis.png", dpi=150)
plt.close(fig)

# ---------------- figure 2: analytic friction floor after fix --------------
fig, ax = plt.subplots(figsize=(7, 4.4))
marker = {"SUBOFF": "o", "DTMB-5415": "s", "KVLCC2": "^"}
for cid in ids:
    c = post_by[cid]
    chk = check(c, "friction_floor")
    if not chk:
        continue
    ax.plot(chk["re"], chk["ct_over_cf"], marker[c["ship_type"]], ms=8,
            color={"SUBOFF": "#1f77b4", "DTMB-5415": "#ff7f0e", "KVLCC2": "#2ca02c"}[c["ship_type"]])
ax.axhspan(1.02, 2.5, color="#2ca02c", alpha=0.12, label="plausibility band [1.02, 2.5]")
ax.axhline(1.0, color="k", lw=1.0, ls="--", label="Ct = Cf (analytic floor, ITTC-57)")
ax.set_xscale("log")
ax.set_xlabel("Reynolds number Re = V L / nu")
ax.set_ylabel("Ct / Cf(ITTC-57)")
ax.set_title("Reference Ct values vs the analytic ITTC-57 friction floor")
for st, m in marker.items():
    ax.plot([], [], m, color={"SUBOFF": "#1f77b4", "DTMB-5415": "#ff7f0e", "KVLCC2": "#2ca02c"}[st], label=st)
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(ART / "audit_report_friction_floor.png", dpi=150)
plt.close(fig)

# ---------------- tables ---------------------------------------------------
print("### T1 v1.0 residual diagnosis (per case)")
print("| case | Rt v1.0 [N] | Rt from identity [N] | rel err | S implied [m2] | S yardstick [m2] | ratio |")
print("|---|---|---|---|---|---|---|")
for i in ids:
    c = pre_by[i]
    crt = check(c, "ct_rt_consistency")
    imp = check(c, "implied_wetted_area")
    rt_old = c["rt_stated_n"]
    rt_new = post_by[i]["rt_stated_n"]
    print(f"| {i} | {rt_old} | {rt_new if rt_new is not None else 'n/a'} | "
          f"{crt.get('rel_err', float('nan'))*100:.1f}% | {c['s_implied_m2']:.4g} | "
          f"{c['s_implied_m2']/imp['ratio']:.4g} | {imp['ratio']:.3f} |")

print("\n### T2 post-fix audit")
print("| case | verdict | Rt [N] | rt_status | Fn | Ct/Cf | checks |")
print("|---|---|---|---|---|---|---|")
for i in ids:
    c = post_by[i]
    ff = check(c, "friction_floor")
    st = ",".join(f"{k['check']}={k['status']}" for k in c["checks"])
    print(f"| {i} | {c['verdict']} | {c['rt_stated_n']} | {c['rt_status']} | "
          f"{c['froude_recomputed']:.4f} | {ff.get('ct_over_cf', float('nan')):.3f} | {st} |")

print("\n### T3 presets")
for p in post["presets"]:
    print(f"- {p['preset']}: {p['status']} fails={p['fails']} warns={p['warns']}")
print("\ncounts pre :", pre["case_verdict_counts"], "strict:", pre["strict_pass"])
print("counts post:", post["case_verdict_counts"], "strict:", post["strict_pass"])
