"""m1828 图件：代理端点 vs Holtrop 真算基准；PINN 耐波 RAO 浪向不变性。

全部数值从 artifacts/m1828/surrogate_audit.json 读取，零手抄。
"""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(HERE, "artifacts", "m1828")
audit = json.load(open(os.path.join(ART, "surrogate_audit.json"), encoding="utf-8"))

pinn = audit["pinn_resistance"]
trf = audit["transformer_resistance"]
sea = audit["pinn_seakeeping"]

fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))

# (a) 阻力-航速：PINN / Transformer / Holtrop(REAL 基准)
ax = axes[0]
hol = pinn.get("holtrop_curve") or []
ax.plot([h["speed"] for h in hol], [h["total_resistance"] for h in hol],
        "k-o", lw=2, label="Holtrop (REAL baseline)")
ax.plot([p["speed"] for p in pinn.get("speed_curve", [])],
        [p["resistance"] for p in pinn.get("speed_curve", [])],
        "r--s", label="PINN surrogate")
tp = sorted({(p["speed"], p["resistance"]) for p in trf.get("predictions") or []})
if tp:
    ax.plot([t[0] for t in tp], [t[1] for t in tp], "b^:", label="Transformer surrogate")
for row in pinn.get("S4_vs_holtrop", []):
    ax.annotate(f"+{row['rel_err_pct']:.0f}%", (row["speed"], row["pinn"]),
                textcoords="offset points", xytext=(0, 6), fontsize=8, color="r")
ax.set_xlabel("Speed V [kn]")
ax.set_ylabel("Total resistance R [kN]")
ax.set_title("(a) Resistance surrogates vs physics baseline\nLpp=180 m, B=30 m, T=11 m, Cb=0.82")
ax.grid(alpha=0.3)
ax.legend(fontsize=8)

# (b) 相对误差柱状
ax = axes[1]
rows = pinn.get("S4_vs_holtrop", [])
labels = [f"{r['speed']:.0f}" for r in rows]
ax.bar([f"{l}\nPINN" for l in labels], [r["rel_err_pct"] for r in rows],
       color="indianred", label="PINN")
trf_rows = {round(r["speed"], 3): r["rel_err_pct"] for r in trf.get("S4_vs_holtrop", [])}
ax.bar([f"{l}\nTRF" for l in labels],
       [trf_rows.get(round(float(l), 3), 0.0) for l in labels],
       color="steelblue", label="Transformer")
ax.axhline(15.0, color="k", ls="--", lw=1, label="Platform gate 15%")
ax.set_ylabel("Rel. error vs Holtrop [%]")
ax.set_title("(b) Relative error to physics baseline")
ax.legend(fontsize=8)
ax.grid(alpha=0.3, axis="y")

# (c) PINN 耐波：浪向不变性
ax = axes[2]
hc = sea.get("heading_curve", [])
ax.plot([c["heading"] for c in hc], [c["heave_rao"] for c in hc], "r-o", label="heave RAO")
ax.plot([c["heading"] for c in hc], [c["pitch_rao"] for c in hc], "b-s", label="pitch RAO (rad/m)")
ax.axhline(3.0, color="k", ls="--", lw=1, label="heave magnitude bound 3.0")
ax.set_xlabel("Wave heading [deg]")
ax.set_ylabel("RAO")
ax.set_ylim(0, max(9.0, max((c["heave_rao"] for c in hc), default=1) * 1.1))
ax.set_title("(c) PINN RAO: heading-invariant")
ax.legend(fontsize=8)
ax.grid(alpha=0.3)

fig.suptitle("m1828 - AI/ML surrogate endpoints audit (read-only live probes)", fontsize=12)
fig.tight_layout()
out = os.path.join(ART, "fig_surrogate_audit.png")
fig.savefig(out, dpi=140)
print("figure:", out)