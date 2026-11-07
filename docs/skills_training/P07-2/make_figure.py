#!/usr/bin/env python3
"""P07-2 结果图：由 probe_results.json 程序化生成（零手抄数字）。
运行：python3 docs/skills_training/P07-2/make_figure.py
输出：p07_2_probe_summary.png
"""
import json
from collections import Counter, OrderedDict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
d = json.loads((HERE / "probe_results.json").read_text(encoding="utf-8"))
recs = [r for r in d["records"] if r["verdict"] != "INFO"]

# 图内用英文标签：绘图层无 CJK 字体（fc-list :lang=zh 为空），中文会渲染成方块
DIM_EN = {"尺寸上限": "Size limit", "类型白名单": "Type allowlist", "目录围栏": "Path fence",
          "授权": "AuthN/AuthZ", "中断续传": "Interrupt/resume", "数据完整性": "Integrity",
          "错误语义": "Error semantics"}
DIM_ORDER = list(DIM_EN)
V_ORDER = ["CONFIRMED_DEFECT", "ACCEPTED_RISK", "OK", "UNTESTED"]
V_EN = {"CONFIRMED_DEFECT": "defect (confirmed)", "ACCEPTED_RISK": "accepted risk",
        "OK": "pass", "UNTESTED": "untested"}
COLORS = {"CONFIRMED_DEFECT": "#c0392b", "ACCEPTED_RISK": "#e67e22", "OK": "#27ae60",
          "UNTESTED": "#7f8c8d"}

fig, axes = plt.subplots(1, 3, figsize=(12.4, 3.55))

# (a) 维度 × 判定
ax = axes[0]
mat = OrderedDict((dim, Counter()) for dim in DIM_ORDER)
for r in recs:
    dim = r["dimension"] if r["dimension"] in mat else "数据完整性"
    mat[dim][r["verdict"]] += 1
left = [0] * len(DIM_ORDER)
for v in V_ORDER:
    vals = [mat[dim].get(v, 0) for dim in DIM_ORDER]
    ax.barh([DIM_EN[d] for d in DIM_ORDER], vals, left=left, color=COLORS[v],
            label=V_EN[v], height=0.62)
    left = [a + b for a, b in zip(left, vals)]
ax.set_xlabel("probe count")
ax.set_title(f"(a) Probe verdicts by dimension (n={len(recs)})", fontsize=10)
ax.legend(fontsize=7, loc="lower right", framealpha=0.9)
ax.invert_yaxis()
ax.tick_params(labelsize=8)

# (b) 已确认缺陷严重度
ax = axes[1]
sev = Counter(r["severity"] for r in recs if r["verdict"] == "CONFIRMED_DEFECT")
order = ["critical", "high", "medium", "low"]
vals = [sev.get(s, 0) for s in order]
bars = ax.bar(order, vals, color=["#8e1b0f", "#c0392b", "#e67e22", "#f1c40f"])
for b, v in zip(bars, vals):
    ax.text(b.get_x() + b.get_width() / 2, v + 0.08, str(v), ha="center", fontsize=9)
ax.set_ylabel("defects")
ax.set_title(f"(b) Confirmed defects by severity (n={sum(vals)})", fontsize=10)
ax.tick_params(labelsize=8)

# (c) 尺寸上限：上限 vs 实际读入（对数轴，证明「先整包缓冲再校验」）
ax = axes[2]
s03 = next(r for r in d["records"] if r["id"] == "s03")
ev = s03["evidence"]
labels = ["check limit\n(scaled in test)", "request body", "bytes read\nbefore reject"]
vals = [ev["limit"], ev["body"], ev["bytes_read"]]
bars = ax.bar(labels, vals, color=["#95a5a6", "#2980b9", "#c0392b"], width=0.6)
ax.set_yscale("log")
for b, v in zip(bars, vals):
    ax.text(b.get_x() + b.get_width() / 2, v * 1.15, f"{v/1024:.0f} KiB", ha="center", fontsize=8.5)
ax.set_ylabel("bytes (log)")
ax.set_title("(c) Oversize reject happens AFTER full buffering\n"
             f"(bytes read / limit = {ev['ratio_buffered_vs_limit']:.0f}x)", fontsize=10)
ax.tick_params(labelsize=8)

fig.suptitle("P07-2 file-upload endpoint failure-mode probes: evidence summary "
             f"(git {d['env']['git_head']}, sqlite {d['env']['sqlite']})", fontsize=11)
fig.tight_layout(rect=(0, 0, 1, 0.94))
out = HERE / "p07_2_probe_summary.png"
fig.savefig(out, dpi=100)
print("wrote", out, out.stat().st_size, "bytes")