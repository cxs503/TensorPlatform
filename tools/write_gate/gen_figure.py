#!/usr/bin/env python3
"""验收图：三合一写入边界门的判据达成面 + 判决流水。

输入：``artifacts/write_gate/acceptance.json`` + ``verdict_sample.json``（验收跑批产物）
输出：``artifacts/write_gate/fig_criteria.png``、``fig_pipeline.png``

图内文字用英文（避免缺字字体导致的方框），图注在报告里用中文。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "artifacts" / "write_gate"


def load() -> dict:
    return json.loads((OUT / "acceptance.json").read_text(encoding="utf-8"))


def fig_criteria(data: dict) -> Path:
    c = data["criteria"]
    fig, axes = plt.subplots(1, 4, figsize=(19.5, 4.4))

    names = ["whitelist\n(out-of-bounds)", "cross-repo\n(pollution)", "secret\n(leakage)"]
    detected = [c["whitelist"]["detected"], c["cross_repo"]["detected"], c["secrets"]["detected"]]
    required = [c["whitelist"]["required"], c["cross_repo"]["required"], c["secrets"]["required"]]
    rates = [d / r * 100 for d, r in zip(detected, required)]
    bars = axes[0].bar(names, rates, color=["#1f77b4", "#2ca02c", "#d62728"], width=0.6)
    axes[0].axhline(100, color="black", linestyle="--", linewidth=0.8)
    axes[0].set_ylim(0, 118)
    axes[0].set_ylabel("detection rate [%]")
    axes[0].set_title("(a) Pre-registered adversarial scenarios")
    for bar, det, req in zip(bars, detected, required):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                     f"{det}/{req}", ha="center", fontsize=11, fontweight="bold")

    fp = c["false_positive"]
    labels = ["normal writes\n(pass)", "false positives\n(block)"]
    values = [fp["samples"] - fp["false_positives"], fp["false_positives"]]
    bars = axes[1].bar(labels, values, color=["#2ca02c", "#d62728"], width=0.55)
    axes[1].set_ylabel("samples")
    axes[1].set_title(f"(b) False-positive budget: {fp['rate'] * 100:.2f}% "
                      f"(budget {fp['budget'] * 100:.0f}%, n={fp['samples']})")
    for bar, value in zip(bars, values):
        axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                     str(value), ha="center", fontsize=11, fontweight="bold")

    verdict = json.loads((OUT / "verdict_sample.json").read_text(encoding="utf-8"))
    by_cat = verdict["totals"]["by_category"]
    cats = list(by_cat.keys())
    vals = [by_cat[k] for k in cats]
    bars = axes[2].bar(cats, vals, color=["#ff7f0e", "#9467bd", "#d62728", "#8c564b"], width=0.6)
    axes[2].set_ylabel("violations")
    axes[2].set_title(f"(c) Sample verdict: {verdict['verdict']} "
                      f"(digest={verdict['digest'][:10]}…)")
    for bar, value in zip(bars, vals):
        axes[2].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                     str(value), ha="center", fontsize=11, fontweight="bold")

    # (d) 凭证判据的两档内容范围（full vs added）
    scope = c["content_scope"]
    labels = [f"{r['scenario']}\n[{r['scope']}]" for r in scope["rows"]]
    blocked = [1 if r["verdict"] == "FAIL" else 0 for r in scope["rows"]]
    colors = ["#d62728" if b else "#2ca02c" for b in blocked]
    bars = axes[3].bar(labels, blocked, color=colors, width=0.6)
    axes[3].set_ylim(0, 1.35)
    axes[3].set_yticks([0, 1])
    axes[3].set_yticklabels(["pass", "block"])
    axes[3].set_title(f"(d) Content scope: {scope['detected']}/{scope['required']} scenarios")
    for bar, row in zip(bars, scope["rows"]):
        note = row["verdict"]
        if row["scope"] == "added" and row["verdict"] == "PASS":
            note = f"{row['verdict']}\n+{row.get('pre_existing_advisories', 0)} advisory"
        axes[3].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.06,
                     note, ha="center", fontsize=9, fontweight="bold")
    axes[3].tick_params(axis="x", labelsize=7.5)

    fig.suptitle("Worktree write-gate: whitelist x cross-repo x secret-leak (three-in-one, pre-commit)",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = OUT / "fig_criteria.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def fig_pipeline(data: dict) -> Path:
    c = data["criteria"]
    fig, ax = plt.subplots(figsize=(13, 4.2))
    ax.axis("off")
    stages = [
        ("change\n(staged / paths / json)", "#eeeeee"),
        ("canonicalize\nNFKC · %-decode ×2\nNUL · '..' fold\nsegments", "#cfe2f3"),
        ("cross-repo judge\nroot escape · absolute\nnested .git · symlink\n"
         f"{c['cross_repo']['detected']}/{c['cross_repo']['required']} (+{c['cross_repo']['extra_detected']} extra)", "#d9ead3"),
        ("whitelist judge\ndeny > read-only > allow\n"
         f"{c['whitelist']['detected']}/{c['whitelist']['required']}", "#fff2cc"),
        ("secret judge\npattern + entropy + allow\nredacted excerpt\n"
         f"{c['secrets']['detected']}/{c['secrets']['required']} (+{c['secrets']['extra_detected']} extra)", "#f4cccc"),
        ("verdict JSON\nPASS / FAIL(unknown)\nschema tp.write_gate.verdict/v1\ncontent-addressed digest", "#d0e0e3"),
    ]
    n = len(stages)
    width = 1.0 / n
    for i, (text, color) in enumerate(stages):
        x = i * width + 0.01
        ax.add_patch(plt.Rectangle((x, 0.28), width - 0.02, 0.44, facecolor=color,
                                   edgecolor="#444444", linewidth=1.0))
        ax.text(x + (width - 0.02) / 2, 0.5, text, ha="center", va="center", fontsize=8.6)
        if i < n - 1:
            ax.annotate("", xy=((i + 1) * width + 0.005, 0.5), xytext=(x + width - 0.02, 0.5),
                        arrowprops=dict(arrowstyle="->", linewidth=1.2, color="#444444"))
    ax.text(0.01, 0.12, "fail-closed: unreadable root / bad policy / illegal path -> decision=unknown & blocked",
            fontsize=9, color="#b00020")
    ax.text(0.01, 0.80, f"false-positive rate {c['false_positive']['rate'] * 100:.2f}% "
                        f"({c['false_positive']['false_positives']}/{c['false_positive']['samples']} normal writes) · "
                        f"{c['performance']['microseconds_per_change']} µs/change",
            fontsize=9)
    ax.set_title("Write-gate decision pipeline (numbers = acceptance artifacts)", fontsize=11)
    path = OUT / "fig_pipeline.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> int:
    if not (OUT / "acceptance.json").is_file():
        print("run acceptance.py first", file=sys.stderr)
        return 1
    data = load()
    for path in (fig_criteria(data), fig_pipeline(data)):
        print(path.relative_to(REPO_ROOT), path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())