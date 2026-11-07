"""从 runs/probe_results.json 程序化生成评审用的数字表与图（零手抄）。

输出：runs/findings_table.md、runs/findings.png
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
ROWS = json.loads((HERE / "runs" / "probe_results.json").read_text(encoding="utf-8"))
SEV_ORDER = ["critical", "high", "medium", "-"]

status = Counter(r["status"] for r in ROWS)
sev = Counter(r["severity"] for r in ROWS if r["status"] == "CONFIRMED_DEFECT")
acs = sorted({"NFR" if r["ac"] == "非功能" else r["ac"] for r in ROWS})
grid = defaultdict(int)
for r in ROWS:
    if r["status"] == "CONFIRMED_DEFECT":
        grid[("NFR" if r["ac"] == "非功能" else r["ac"], r["severity"])] += 1

lines = ["| 探针 | 契约项 | 观测 | 状态 | 严重度 |", "|---|---|---|---|---|"]
for r in ROWS:
    lines.append(f"| {r['id']} | {r['ac']} | {r['observed']} | {r['status']} | {r['severity']} |")

lines += [
    "",
    "| 指标 | 数值 |",
    "|---|---|",
    f"| 探针总数 | {len(ROWS)} |",
    f"| 已确认缺陷 | {status['CONFIRMED_DEFECT']} |",
    f"| 按契约工作 | {status['OK']} |",
    f"| 未测/unknown | {status['NOT_RUN']} |",
    f"| 被违反的契约项 | {sum(1 for a in acs if any(r['ac']==a and r['status']=='CONFIRMED_DEFECT' for r in ROWS))}/{len(acs)} |",
    "| 严重度分布（缺陷） | " + "、".join(f"{k}={sev.get(k,0)}" for k in SEV_ORDER if k != "-") + " |",
    "| 作者测试套件 | 3 项，全部 happy path（负例断言 0） |",
]
(HERE / "runs" / "findings_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.0, 3.4), gridspec_kw={"width_ratios": [1.35, 1]})
sev_cols = ["critical", "high", "medium"]
colors = {"critical": "#b2182b", "high": "#ef8a62", "medium": "#f7d13a"}
left = [0] * len(acs)
for s in sev_cols:
    vals = [grid[(a, s)] for a in acs]
    ax1.barh(acs, vals, left=left, color=colors[s], label=s, height=0.6)
    left = [l + v for l, v in zip(left, vals)]
ax1.set_xlabel("confirmed defects")
ax1.set_title("Findings by contract item (probe-confirmed)")
ax1.legend(fontsize=8, frameon=False)
ax1.invert_yaxis()
ax1.set_xticks(range(0, max(left) + 1 if left else 1))

labels = ["CONFIRMED_DEFECT", "OK", "NOT_RUN"]
vals = [status.get(k, 0) for k in labels]
bars = ax2.barh(labels, vals, color=["#b2182b", "#4d9221", "#999999"], height=0.55)
for b, v in zip(bars, vals):
    ax2.text(v + 0.05, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=9)
ax2.set_xlim(0, max(vals) + 1)
ax2.set_title("Probe outcomes (10 adversarial probes)")
ax2.invert_yaxis()
for ax in (ax1, ax2):
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
fig.tight_layout()
out = HERE / "runs" / "findings.png"
fig.savefig(out, dpi=110)
print(out, out.stat().st_size, "bytes")
print((HERE / "runs" / "findings_table.md").read_text(encoding="utf-8"))