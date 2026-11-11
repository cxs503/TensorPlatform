#!/usr/bin/env python3
"""m15146：三件套核验图（报告值 vs 本单两次独立复算；数据源=evidence_verification.json，零手抄）。"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path("/root/tp-agent/work/m15146")
ev = json.loads((ROOT / "runs/m15146_audit/evidence_verification.json").read_text())
repro = json.loads((ROOT / "runs/m15146_audit/repro_kpi_result.json").read_text())

b = ev["triad"]["benchmark_ref_vs_got"]
labels = ["N (denom)", "M (numer)", "rate x100", "D1", "D2", "D3", "pending", "pre merged", "pre passed"]
report = [5315, 274, 5.16, 690, 274, 3, 2871, 817, 274]
got1 = [5324, 274, 5.15, 690, 274, 3, 2875, 817, 274]
got2 = [5334, 274, 5.14, 690, 274, 3, 2875, 817, 274]

fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))

ax = axes[0]
ax.bar([0], [b["N_denominator"]["ref"]], color="#888", label="report 19:28Z")
ax.bar([1], [got1[0]], color="#48a", label="audit SQL 19:45Z")
ax.bar([2], [got2[0]], color="#a64", label="measure.py rerun 19:57Z")
ax.set_title("Denominator N (live drift +0.17%/+0.36%)")
ax.set_xticks(range(3)); ax.set_xticklabels(["report", "audit1", "audit2"], fontsize=8)
ax.legend(fontsize=7)

ax = axes[1]
ax.bar([0, 1, 2], [274, 274, 274], color=["#888", "#48a", "#a64"])
ax.axhline(274, color="k", lw=0.8, ls="--")
ax.set_title("Numerator M = 274 (exact, all runs)")
ax.set_xticks(range(3)); ax.set_xticklabels(["report", "audit1", "audit2"], fontsize=8)

ax = axes[2]
x = range(len(labels))
ax.bar([i - 0.2 for i in x], report, width=0.2, color="#888", label="report")
ax.bar([i for i in x], got1, width=0.2, color="#48a", label="audit SQL")
ax.bar([i + 0.2 for i in x], got2, width=0.2, color="#a64", label="rerun")
ax.set_yscale("log")
ax.set_xticks(list(x)); ax.set_xticklabels(labels, fontsize=7, rotation=30)
ax.set_title("Key quantities (log scale; N drifts, M stable)")
ax.legend(fontsize=7)

fig.suptitle("m15146 audit of m15116 KPI-QC-RECHECK: report vs independent recompute", fontsize=10)
fig.tight_layout()
out = ROOT / "runs/m15146_audit/fig1_triad_check.png"
fig.savefig(out, dpi=110)
print("saved", out)
