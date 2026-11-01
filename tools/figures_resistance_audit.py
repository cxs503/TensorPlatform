#!/usr/bin/env python3
"""Figures for the resistance validation-debt audit (mission 1452).

Reads artifacts/resistance_validation_audit/{baseline,after_fix}.json and writes
PNGs to artifacts/resistance_validation_audit/figures/. All numbers come from the
audit artifacts, never typed in by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path("artifacts/resistance_validation_audit")
OUT = ROOT / "figures"
OUT.mkdir(parents=True, exist_ok=True)

base = json.loads((ROOT / "baseline.json").read_text())
after = json.loads((ROOT / "after_fix.json").read_text())


def _find(result, case_id, invariant):
    for case in result["cases"]:
        if case["case_id"] == case_id:
            for check in case["checks"]:
                if check["invariant"] == invariant:
                    return check
    return None


# ---------------------------------------------------------------- figure 1
# Reported Rt vs the Rt implied by the record's own dimensionless coefficient.
labels, reported, implied = [], [], []
for case in after["cases"]:
    cid = case["case_id"]
    b = _find(base, cid, "I2_force") or _find(base, cid, "I3_implied_wetted_surface")
    a = _find(after, cid, "I2_force")
    if b is None:
        continue
    if "reported_rt_n" in b:
        rep, imp = b["reported_rt_n"], b["rt_from_ct_n"]
    else:
        imp = b["implied_s_m2"] * 0.0  # placeholder, handled below
        continue
    labels.append(cid)
    reported.append(rep)
    implied.append(imp)

fig, ax = plt.subplots(figsize=(9, 4.6))
x = range(len(labels))
ax.bar([i - 0.2 for i in x], reported, width=0.4, label="Rt as tabulated (before repair)", color="#c0392b")
ax.bar([i + 0.2 for i in x], implied, width=0.4, label="Rt implied by the same Ct (I2)", color="#27ae60")
ax.set_yscale("log")
ax.set_xticks(list(x))
ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
ax.set_ylabel("Total resistance (N, log scale)")
ax.set_title("Reference dataset: tabulated R$_t$ vs R$_t$ implied by its own C$_t$")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3, which="both")
fig.tight_layout()
fig.savefig(OUT / "fig1_reference_rt_consistency.png", dpi=160)
plt.close(fig)

# ---------------------------------------------------------------- figure 2
# Ct/Cf plausibility band per case (I4) — the dimensionless column survived.
ratios, ids = [], []
for case in after["cases"]:
    c = _find(after, case["case_id"], "I4_ct_over_cf")
    if c:
        ids.append(case["case_id"])
        ratios.append(c["ratio"])
fig, ax = plt.subplots(figsize=(9, 3.8))
ax.axhspan(1.1, 3.0, color="#27ae60", alpha=0.12, label="acceptance band 1.1–3.0")
ax.plot(ids, ratios, "o-", color="#2c3e50", markersize=5)
ax.set_ylabel("C$_t$ / C$_f$(ITTC-57)")
ax.set_title("Dimensionless coefficient plausibility (retained, cited column)")
ax.tick_params(axis="x", rotation=45, labelsize=8)
ax.legend(fontsize=8)
ax.grid(alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "fig2_ct_over_cf.png", dpi=160)
plt.close(fig)

# ---------------------------------------------------------------- figure 3
# Live service deviations from closed-form invariants.
live = base.get("live") or {}
fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
if live.get("reachable"):
    re_c = next(c for c in live["checks"] if c["invariant"] == "L1_reynolds")
    fn_c = next(c for c in live["checks"] if c["invariant"] == "L2_froude")
    rt_c = next(c for c in live["checks"] if c["invariant"] == "L3_force_closure")

    axes[0].bar(["service", "closed form"], [re_c["got"], re_c["expected"]], color=["#c0392b", "#2c3e50"])
    axes[0].set_title(f"Re  (ratio {re_c['ratio']:.2f})", fontsize=9)
    axes[1].bar(["service", "closed form"], [fn_c["got"], fn_c["expected"]], color=["#c0392b", "#2c3e50"])
    axes[1].set_title(f"F$_n$  (Δ {fn_c['delta']:.3f})", fontsize=9)
    axes[2].bar(["service", "from own C$_t$"],
                [rt_c["rt_reported_n"], rt_c["rt_from_ct_n"]], color=["#c0392b", "#2c3e50"])
    axes[2].set_yscale("log")
    axes[2].set_title(f"R$_t$ [N] (reported = {rt_c['reported_over_expected']:.1e} of expected)", fontsize=8)
    for ax, lbl in zip(axes, ["Re at 3 m/s, L=4.356 m", "Froude number", "Total resistance"]):
        ax.set_ylabel(lbl)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("Deployed resistance service (:8001) vs closed-form invariants", fontsize=10)
else:
    axes[0].text(0.1, 0.5, "live service not reachable in this run")
fig.tight_layout()
fig.savefig(OUT / "fig3_live_service_invariants.png", dpi=160)
plt.close(fig)

print("figures:", *sorted(p.name for p in OUT.glob("*.png")), sep="\n  ")