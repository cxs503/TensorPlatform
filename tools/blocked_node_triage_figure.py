#!/usr/bin/env python3
"""blocked_node_triage 证据图：从 dry-run JSON 程序化生成 PNG（零手抄）。

左图：五类分诊 × 三决策的堆叠柱（数量直接取自 JSON 的 ``by_class`` / ``decisions``）；
右图：原始 ``block_reason`` 频次 Top-10（含 MANUAL_GATE 原文，用于证明「不可自动处理」
的那 8 条长什么样）。

用法::

    python3 tools/blocked_node_triage_figure.py \
        --json docs/blocked_node_triage_live_dry_run.json \
        --out docs/blocked_node_triage_20261112.png
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def build(json_path: str, out_path: str, title_suffix: str = "") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    decisions = data["decisions"]

    classes = list(data["by_class"].keys())
    per_class_decision = {c: Counter() for c in classes}
    for d in decisions:
        per_class_decision[d["class"]][d["decision"]] += 1

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.4))
    ax = axes[0]
    order = ["requeue", "stay_blocked", "never"]
    colors = {"requeue": "#2e7d32", "stay_blocked": "#b0bec5", "never": "#c62828"}
    bottoms = [0] * len(classes)
    for dec in order:
        vals = [per_class_decision[c][dec] for c in classes]
        ax.bar(classes, vals, bottom=bottoms, color=colors[dec], label=dec)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    for i, c in enumerate(classes):
        ax.text(i, bottoms[i] + max(bottoms) * 0.01, str(bottoms[i]),
                ha="center", va="bottom", fontsize=10)
    ax.set_ylabel("blocked nodes")
    ax.set_title("Triage of blocked campaign nodes by class and decision")
    ax.legend(title="decision", fontsize=9)
    ax.tick_params(axis="x", labelsize=9)
    ax.grid(axis="y", alpha=0.3)
    ax.set_ylim(0, max(bottoms) * 1.18 if max(bottoms) else 1)

    ax2 = axes[1]
    raw = Counter((d.get("block_reason") or "<null>") for d in decisions)
    top = raw.most_common(10)[::-1]
    # 字体无 CJK 字形：非 ASCII 原文一律折成占位标签（原文与计数仍完整保存在 JSON/文档表里）
    def _label(text: str, idx: int, decision: str) -> str:
        if all(ord(ch) < 128 for ch in text):
            return text if len(text) <= 46 else text[:43] + "..."
        return f"<non-ASCII reason #{idx} | {decision}>"

    labels, values, shade = [], [], []
    for idx, (reason, count) in enumerate(top):
        hit = next((d for d in decisions if (d.get("block_reason") or "<null>") == reason), None)
        decision = hit["decision"] if hit else "?"
        labels.append(_label(reason, idx, decision))
        values.append(count)
        shade.append(colors["never"] if decision == "never"
                     else ("#2e7d32" if decision == "requeue" else "#78909c"))
    ax2.barh(labels, values, color=shade)
    for i, v in enumerate(values):
        ax2.text(v + max(values) * 0.01, i, str(v), va="center", fontsize=9)
    ax2.set_xlabel("blocked nodes")
    ax2.set_title("Raw block_reason frequency (top 10; non-ASCII folded to placeholders)")
    ax2.tick_params(axis="y", labelsize=8)
    ax2.grid(axis="x", alpha=0.3)

    fig.suptitle(
        f"blocked_node_triage dry-run @ {data['generated_at']} | "
        f"blocked_total={data['blocked_total']} classified={data['classified_total']} "
        f"coverage_ok={data['coverage_ok']} {title_suffix}".strip(), fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description="blocked_node_triage 证据图（PNG）")
    p.add_argument("--json", default="docs/blocked_node_triage_live_dry_run.json")
    p.add_argument("--out", default="docs/blocked_node_triage_20261112.png")
    p.add_argument("--note", default="")
    a = p.parse_args()
    print(build(a.json, a.out, a.note))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())