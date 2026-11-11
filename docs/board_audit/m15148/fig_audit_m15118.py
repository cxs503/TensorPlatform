#!/usr/bin/env python3
"""m15148 board-audit 图：m15118(KPI-SWEEP-1000) 申报值 vs 审计独立复算值。

左：计数口径（log 轴）申报 vs 复算 + A 档污染量；右：L1 恒等式合格率 申报(seed=?) vs 审计(seed=20261111 重抽)。
全部数字取自 docs/board_audit/m15148/verify_output.json 与 evidence/artifacts/*.json（零手抄）。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = Path(__file__).resolve().parent
V = json.loads((BASE / "verify_output.json").read_text())
CLAIM = json.loads((BASE / "evidence/artifacts/kpi_sweep_1000_result.json").read_text())
SAMP = json.loads((BASE / "evidence/artifacts/samples_census.json").read_text())

kpi = CLAIM.get("kpi", CLAIM)
claim = {
    "A": kpi["current_by_scope"]["A_strict_solver_batch"],
    "B": kpi["current_by_scope"]["B_formula_chain_batch(headline)"],
    "C": kpi["current_by_scope"]["C_wide_all_case_records"],
}
c1 = V["C1_counts"]
recomp = {
    "A": sum(a["cases"] for a in json.loads(
        (BASE / "evidence/artifacts/artifact_census.json").read_text())["artifacts"]),
    "B": c1["B_headline_recompute"],
    "C": c1["C_wide_recompute"],
}

# 申报合格率（原单 400 行抽样）
def claimed_rate(ct, key):
    d = SAMP["l1_identity_checks"].get(ct, {}).get("details", {}).get(key)
    return None if not d else d["pass"] / max(d["n"], 1)


ident = V["C3_identities"]
pairs = [
    ("resistance\nCf=ITTC57", claimed_rate("kb_resistance", "Cf=ITTC57(Re)"),
     ident["resistance.Cf=ITTC57(Re)"]["rate"]),
    ("resistance\nRt=Rf+Rw+Ra", claimed_rate("kb_resistance", "Rt=Rf+Rw+Ra"),
     ident["resistance.Rt=sum(1e-3)"]["rate"]),
    ("stability\nGM=KB+BM-KG", claimed_rate("kb_stability", "GM=KB+BM-KG"),
     ident["stability.GM=KB+BM-KG"]["rate"]),
    ("propulsion\neta0=J*KT/2piKQ", claimed_rate("kb_propulsion", "eta0=J*KT/(2pi*KQ)"),
     ident["propulsion.eta0=J*KT/(2pi*KQ)"]["rate"]),
    ("ice\nR_ice=sum", claimed_rate("kb_ice_navigation", "R_ice=sum(components)"),
     ident["ice.R_ice=sum(components)"]["rate"]),
]

fig, ax = plt.subplots(1, 2, figsize=(13, 5.4))
labels = ["A strict solver\nbatch", "B headline\n(formula chain)", "C wide\n(all records)"]
x = range(3)
ax[0].bar([i - 0.2 for i in x], [claim["A"], claim["B"], claim["C"]], 0.4,
          label="claimed (m15118)", color="#4C72B0")
ax[0].bar([i + 0.2 for i in x], [recomp["A"], recomp["B"], recomp["C"]], 0.4,
          label="audit recompute (m15148)", color="#DD8452")
ax[0].axhline(1000, ls="--", c="crimson", lw=1.4)
ax[0].text(2.42, 1150, "KPI target 1000", color="crimson", fontsize=9, ha="right")
ax[0].set_yscale("log")
ax[0].set_xticks(list(x))
ax[0].set_xticklabels(labels, fontsize=9)
ax[0].set_ylabel("cases (log scale)")
ax[0].set_title("Counting: claimed vs audit recompute (B delta=0, C delta=-255 def., A holds 14 non-case rows)", fontsize=10)
for i, v in enumerate([claim["A"], claim["B"], claim["C"]]):
    ax[0].text(i - 0.2, v * 1.15, f"{v:,}", ha="center", fontsize=8)
for i, v in enumerate([recomp["A"], recomp["B"], recomp["C"]]):
    ax[0].text(i + 0.2, v * 1.15, f"{v:,}", ha="center", fontsize=8)
ax[0].legend(fontsize=8, loc="upper left")
ax[0].grid(axis="y", alpha=0.25)

w = 0.38
ax[1].bar([i - w / 2 for i in range(len(pairs))], [p[1] for p in pairs], w,
          label="claimed sample n=400", color="#4C72B0")
ax[1].bar([i + w / 2 for i in range(len(pairs))], [p[2] for p in pairs], w,
          label="audit resample n=300 (seed 20261111)", color="#DD8452")
ax[1].set_xticks(range(len(pairs)))
ax[1].set_xticklabels([p[0] for p in pairs], fontsize=8)
ax[1].set_ylim(0, 1.12)
ax[1].set_ylabel("L1 identity recompute pass rate")
ax[1].set_title("Recomputability: identity pass rate, claimed vs audit (same direction, sampling gap <=3pt)", fontsize=10)
ax[1].axhline(0.95, ls=":", c="green", lw=1.2)
ax[1].text(4.45, 0.96, "95% gate", color="green", fontsize=8, ha="right")
ax[1].legend(fontsize=8, loc="lower left")
ax[1].grid(axis="y", alpha=0.25)

fig.suptitle("m15118 KPI-SWEEP-1000 board audit: counts reproducible (delta=0); no benchmark judge / no integration / no tests -> L2",
             fontsize=11)
fig.tight_layout()
out = BASE / "fig_audit_m15118.png"
fig.savefig(out, dpi=130)
print("wrote", out, "|", claim, recomp, [(p[0], round(p[1] or -1, 3), p[2]) for p in pairs])