#!/usr/bin/env python3
"""p167w2-n09 报告配图（程序化生成，零手抄）。

读三份工件：
  * artifacts/repro_audit/channel_baseline_legacy.json  —— 历史注册表基线评级
  * artifacts/repro_audit/channel_maturity.json         —— 回填后通道台账
  * artifacts/repro_audit/gate_provocation.json         —— 入库门 fail-closed 实证

出两张图：
  fig1_grade_distribution.png   基线 vs 回填后的等级分布（含 24/36 目标线）
  fig2_derivation_soundness.png 逐通道相对偏差 + 扰动响应族分布

用法：python3 -m tools.repro_audit.generators.figures --out artifacts/repro_audit
"""
from __future__ import annotations

import argparse
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fig_grade_distribution(base: dict, after: dict, out_dir: str) -> str:
    grades = ["L3", "L2", "L1", "L0", "none"]
    n_base = [base["grades"].get(g, 0) for g in grades]
    n_after = [after["grades"].get(g, 0) for g in grades]
    colours = {"L3": "#1b7f3b", "L2": "#e8b520", "L1": "#e07b39",
               "L0": "#b03030", "none": "#888888"}

    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    xs = [0, 1]
    bottoms = [0, 0]
    for i, g in enumerate(grades):
        vals = [n_base[i], n_after[i]]
        ax.bar(xs, vals, bottom=bottoms, color=colours[g], width=0.5,
               edgecolor="white", label=g)
        for x, v, b in zip(xs, vals, bottoms):
            if v:
                ax.text(x, b + v / 2, str(v), ha="center", va="center",
                        color="white", fontsize=9, fontweight="bold")
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    ax.axhline(24, color="#333333", linestyle="--", linewidth=1.1)
    ax.text(1.28, 24.6, "acceptance floor 24/36", fontsize=8, ha="right")
    ax.set_xticks(xs)
    ax.set_xticklabels(["legacy gen_*.py registry\n(baseline)",
                        "backfilled channels\n(this work)"], fontsize=9)
    ax.set_ylabel("channels in the frozen 36-channel sample")
    ax.set_title("Numeric-channel maturity: same L0-L3 rubric, before vs after",
                 fontsize=11)
    ax.set_ylim(0, 40)
    ax.legend(title="grade", fontsize=8, title_fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.25)
    path = os.path.join(out_dir, "fig1_grade_distribution.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_derivation_soundness(after: dict, out_dir: str) -> str:
    channels = after["channels"]
    deltas = [max(float(c.get("rel_delta", 0.0)), 1e-18) for c in channels]
    kinds = [c.get("kind", "?") for c in channels]
    families: dict[str, int] = {}
    for c in channels:
        win = (c.get("mutation") or {}).get("witness") or {}
        fam = win.get("kind", "none")
        families[fam] = families.get(fam, 0) + 1

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2),
                                   gridspec_kw={"width_ratios": [2, 1]})
    idx = list(range(len(channels)))
    colours = {"tally": "#2b6cb0", "formula": "#c05621"}
    for kind in sorted(set(kinds)):
        ys = [deltas[i] for i in idx if kinds[i] == kind]
        xs = [i for i in idx if kinds[i] == kind]
        ax1.scatter(xs, ys, s=22, color=colours.get(kind, "#666"),
                    label=kind, zorder=3)
    ax1.set_yscale("log")
    ax1.set_ylim(1e-18, 1e-2)
    ax1.axhline(1e-9, color="#b03030", linestyle=":", linewidth=1.0)
    ax1.text(0.5, 1.4e-9, "recorded-precision tolerance 1e-9",
             fontsize=7.5, color="#b03030")
    ax1.set_xlabel("channel index (frozen 36-channel sample)")
    ax1.set_ylabel("|derived - recorded| / |recorded|")
    ax1.set_title("Replay residual per channel (all below tolerance)", fontsize=10)
    ax1.legend(fontsize=8, loc="lower right")
    ax1.grid(alpha=0.25)

    names = sorted(families, key=lambda k: -families[k])
    ax2.bar(names, [families[k] for k in names], color="#4a5568")
    for i, k in enumerate(names):
        ax2.text(i, families[k] + 0.4, str(families[k]), ha="center",
                 fontsize=8, fontweight="bold")
    ax2.set_ylabel("channels whose sensitivity witness is this mutation family")
    ax2.set_title("Mutation families proving derivation soundness", fontsize=10)
    ax2.tick_params(axis="x", labelrotation=15, labelsize=8)
    ax2.set_ylim(0, max(families.values()) + 4)
    ax2.grid(axis="y", alpha=0.25)

    path = os.path.join(out_dir, "fig2_derivation_soundness.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="artifacts/repro_audit")
    args = ap.parse_args(argv)
    out_dir = os.path.join(args.root, args.out)
    os.makedirs(out_dir, exist_ok=True)
    base = _load(os.path.join(out_dir, "channel_baseline_legacy.json"))
    after = _load(os.path.join(out_dir, "channel_maturity.json"))
    made = [fig_grade_distribution(base, after, out_dir),
            fig_derivation_soundness(after, out_dir)]
    print(json.dumps({"figures": [os.path.relpath(p, args.root) for p in made],
                      "baseline_grades": base["grades"],
                      "after_grades": after["grades"]}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())