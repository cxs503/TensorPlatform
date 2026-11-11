#!/usr/bin/env python3
"""KPI-QC-RECHECK 出图：只读 kpi_result.json，不重算任何数字。

图 1 口径阶梯（D0→D3）｜图 2 分仓覆盖｜图 3 逐日合并 vs 独立复核通过｜图 4 复核供给侧逐日状态
环境无 CJK 字体 → 图内文字用英文，中文解释写在报告图注里（杜绝豆腐块）。
"""
from __future__ import annotations

import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="artifacts/kpi_qc_recheck/kpi_result.json")
    ap.add_argument("--outdir", default="artifacts/kpi_qc_recheck")
    args = ap.parse_args()

    with open(args.result, encoding="utf-8") as fh:
        d = json.load(fh)
    os.makedirs(args.outdir, exist_ok=True)

    # 图 1：口径阶梯 + 目标线
    lad = d["ladder"]
    keys = ["D0_any_record", "D1_any_confirmed", "D2_independent_confirmed", "D3_independent_cross_model"]
    labels = ["D0 any record", "D1 any confirmed\n(incl. auto_scan)", "D2 independent\nconfirmed (KPI)", "D3 D2 + cross-model"]
    vals = [lad[k]["pct"] for k in keys]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar(labels, vals, color=["#9aa5b1", "#f0a35e", "#1f6feb", "#2ea043"])
    ax.set_ylabel("share of merged agent PRs (%)")
    ax.set_title("Definition ladder: what counts as 'independently reviewed and passed'")
    ax.axhline(100, color="crimson", ls="--", lw=1.2, label="target 100%")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.2f}%", ha="center", fontsize=9)
    ax.set_ylim(0, 110)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(args.outdir, "fig1_definition_ladder.png"), dpi=150)
    plt.close(fig)

    # 图 2：分仓（合并量 vs 独立复核通过）
    rows = d["per_repo"][:12]
    names = [r["repo"] for r in rows][::-1]
    merged = [r["merged_prs"] for r in rows][::-1]
    ind = [r["ind_reviewed"] for r in rows][::-1]
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    y = range(len(names))
    ax.barh(list(y), merged, color="#c9d1d9", label="merged agent PRs")
    ax.barh(list(y), ind, color="#f0a35e", label="independently reviewed & passed")
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("PR count (last 30 days)")
    ax.set_title("Per-repo coverage: merged vs independently reviewed")
    for i, (m, k) in enumerate(zip(merged, ind)):
        ax.text(m + 8, i, f"{k}/{m}", va="center", fontsize=7)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(args.outdir, "fig2_per_repo.png"), dpi=150)
    plt.close(fig)

    # 图 3：逐日
    daily = d["daily"]
    days = [r["day"] for r in daily]
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    ax.bar([x - 0.2 for x in range(len(days))], [r["merged_prs"] for r in daily], width=0.4,
           color="#c9d1d9", label="merged agent PRs")
    ax.bar([x + 0.2 for x in range(len(days))], [r["ind_reviewed"] for r in daily], width=0.4,
           color="#f0a35e", label="independently reviewed & passed")
    ax.set_xticks(range(len(days)))
    ax.set_xticklabels([x[5:] for x in days], fontsize=8, rotation=0)
    ax.set_ylabel("PR count")
    ax.set_title("Daily: merged volume vs independent review throughput")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(args.outdir, "fig3_daily.png"), dpi=150)
    plt.close(fig)

    # 图 4：复核供给侧（逐日按 status 堆叠）
    sup = d.get("review_supply", [])
    if sup:
        sdays = sorted({r["day"] for r in sup if r.get("day")})
        statuses = sorted({r["status"] for r in sup})
        fig, ax = plt.subplots(figsize=(7.6, 4.2))
        bottom = [0] * len(sdays)
        colors = {"confirmed": "#2ea043", "candidate": "#1f6feb", "rejected": "#cf222e", "pending": "#9aa5b1"}
        for st in statuses:
            vals = [sum(r["rows"] for r in sup if r["day"] == dy and r["status"] == st) for dy in sdays]
            ax.bar(sdays, vals, bottom=bottom, label=st, color=colors.get(st, "#8250df"))
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax.set_ylabel("review rows added")
        ax.set_title("Review supply side: rows by status (pending = built but never adjudicated)")
        ax.tick_params(axis="x", labelsize=8)
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        fig.savefig(os.path.join(args.outdir, "fig4_review_supply.png"), dpi=150)
        plt.close(fig)

    print(json.dumps({"outdir": args.outdir, "figures": sorted(
        f for f in os.listdir(args.outdir) if f.endswith(".png"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())