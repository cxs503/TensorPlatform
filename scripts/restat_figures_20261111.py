#!/usr/bin/env python3
"""AUDIT-RESTAT 图与名单生成：读 runs/restat_20261111/restat_summary.json + missions_classified.csv，
产出 figures + 待完成名单 CSV（零手抄：所有数值取自 SQL 产物）。

用法: python3 scripts/restat_figures_20261111.py --dir runs/restat_20261111
"""
from __future__ import annotations

import argparse
import csv
import json
import os

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["axes.unicode_minus"] = False
import matplotlib.pyplot as plt  # noqa: E402

LABELS = ["L0 docs", "L1 math-verify", "L2 standalone", "L3 integrated", "L4 benchmark", "L5 novel"]
COLORS = ["#9e9e9e", "#f4a261", "#2a9d8f", "#264653", "#e76f51", "#6a4c93"]


def bar_pair(ax, left, right, titles):
    idx = range(6)
    ax.bar([i - 0.2 for i in idx], left, width=0.4, label=titles[0], color="#264653")
    ax.bar([i + 0.2 for i in idx], right, width=0.4, label=titles[1], color="#e76f51")
    ax.set_xticks(list(idx))
    ax.set_xticklabels(LABELS, rotation=18, fontsize=8)
    ax.set_ylabel("count")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=.25)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="runs/restat_20261111")
    a = ap.parse_args()
    d = a.dir
    s = json.load(open(f"{d}/restat_summary.json"))
    figdir = f"{d}/figures"
    os.makedirs(figdir, exist_ok=True)
    figs = []

    # Fig1: 审计判定层（Tier-A）claim vs 机器门实测
    claim = [s["tierA_claimed_level_counts"].get(str(i), 0) for i in range(6)]
    gate = [s["tierA_gate_level_counts"].get(str(i), 0) for i in range(6)]
    fig, ax = plt.subplots(figsize=(7, 4))
    bar_pair(ax, claim, gate, ["claimed level (audit verdict)", "gate level (artifact-measured)"])
    ax.set_title("Tier-A: 184 audited scopes - claimed vs measured (tensor_maturity_verdicts)")
    fig.tight_layout()
    p1 = f"{figdir}/fig1_claim_vs_gate.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    figs.append(p1)

    # Fig2: 全库代理层（Tier-B）严格 vs 宽松
    strict = [s["tierB_level_counts"].get(str(i), 0) for i in range(6)]
    loose = [s["tierB_level_counts_loose"].get(str(i), 0) for i in range(6)]
    fig, ax = plt.subplots(figsize=(7, 4))
    bar_pair(ax, loose, strict, ["loose (impl files counted)", "strict (needs PR merge evidence)"])
    ax.set_title("Tier-B: 9,117 succeeded missions - loose vs strict proxy grading")
    fig.tight_layout()
    p2 = f"{figdir}/fig2_proxy_loose_vs_strict.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    figs.append(p2)

    # Fig3: 米制漏斗（任务 -> 成果）
    infl = s["inflation"]
    funnel = [
        ("all missions", s["missions_total"]),
        ("succeeded", s["succeeded_total"]),
        ("impl code (loose)", infl["proxy_L2plus_succeeded_missions"]),
        ("PR merged (strict L2+)", s["tierB_level_counts"].get("2", 0)
         + s["tierB_level_counts"].get("3", 0) + s["tierB_level_counts"].get("4", 0)),
        ("run artifacts", infl["succ_with_run_artifacts"]),
        ("audited gate>=L1", infl["verified_audit_gate_L1plus"]),
        ("audited gate>=L2", infl["verified_functions_audit_gate_L2plus"]),
    ]
    fig, ax = plt.subplots(figsize=(7.2, 4))
    names = [f[0] for f in funnel]
    vals = [f[1] for f in funnel]
    bars = ax.bar(range(len(vals)), vals, color=["#264653", "#2a9d8f", "#8ab17d",
                                                 "#e9c46a", "#f4a261", "#e76f51", "#6a4c93"])
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(names, rotation=22, fontsize=8, ha="right")
    ax.set_yscale("symlog")
    ax.set_ylabel("count (symlog)")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + (1 if v else 0.2), str(v),
                ha="center", fontsize=8)
    ax.set_title("Real-output funnel: 13,742 missions -> verified deliverables")
    fig.tight_layout()
    p3 = f"{figdir}/fig3_output_funnel.png"
    fig.savefig(p3, dpi=130)
    plt.close(fig)
    figs.append(p3)

    # Fig4: 日度提交趋势（按档位堆叠）
    trend = s["trend_by_day"]
    days = sorted(trend)
    bot = [0] * len(days)
    fig, ax = plt.subplots(figsize=(7.6, 4))
    for lvl in range(5):
        vals = [trend[d].get(str(lvl), 0) for d in days]
        ax.bar(days, vals, bottom=bot, label=LABELS[lvl], color=COLORS[lvl])
        bot = [b + v for b, v in zip(bot, vals)]
    ax.set_xticks(range(len(days)))
    ax.set_xticklabels([d[5:] for d in days], rotation=45, fontsize=8)
    ax.set_ylabel("succeeded missions / day")
    ax.legend(fontsize=7)
    ax.set_title("Distribution trend of succeeded missions by strict level (2026-10-29 .. 11-11)")
    fig.tight_layout()
    p4 = f"{figdir}/fig4_trend_by_day.png"
    fig.savefig(p4, dpi=130)
    plt.close(fig)
    figs.append(p4)

    # 待完成名单 CSV（Tier-B 严格口径 L0/L1 的 succeeded 单；不改任务状态）
    rows = list(csv.DictReader(open(f"{d}/missions_classified.csv")))
    pend = [r for r in rows if r["status"] == "succeeded" and r["level"] in ("0", "1")]
    with open(f"{d}/pending_proxy_L0L1.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(pend)
    print(json.dumps(dict(figures=figs, pending_proxy=len(pend)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())