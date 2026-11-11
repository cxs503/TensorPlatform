#!/usr/bin/env python3
"""KPI-TRACE-100 结果图（脚本化生成，禁止手抄数字）。

输入：artifacts/kpi_trace_100/result.json（算路 A 产物）
输出：artifacts/kpi_trace_100/fig_*.png
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path("artifacts/kpi_trace_100")
res = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
plt.rcParams.update({"figure.dpi": 130, "font.size": 9, "axes.grid": True,
                     "grid.alpha": 0.3, "axes.axisbelow": True})


def fig_gap() -> Path:
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    cur, tgt = res["current"], res["target"]
    bars = ax.bar(["current\n(measured)", "target"], [cur, tgt],
                  color=["#c0504d", "#4f81bd"], width=0.5)
    ax.axhline(tgt, ls="--", lw=1, color="#4f81bd")
    for b, v in zip(bars, [cur, tgt]):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.2f}%", ha="center")
    ax.annotate(f"gap = {res['gap']:.2f} pp",
                xy=(0.5, (cur + tgt) / 2), xytext=(0.62, cur + 12),
                arrowprops=dict(arrowstyle="->", color="#333"), ha="center")
    ax.set_ylim(0, 115)
    ax.set_ylabel("traceable report artifacts (%)")
    ax.set_title(f"KPI-TRACE-100  n={res['numerator']}/{res['denominator']} terminal missions")
    p = OUT / "fig_1_gap.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    return p


def fig_status() -> Path:
    bs = res["by_status"]
    keys = [k for k in ("succeeded", "failed", "stopped", "revoked") if k in bs]
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    vals = [bs[k]["pct"] or 0 for k in keys]
    bars = ax.bar([f"{k}\nn={bs[k]['terminal']}" for k in keys], vals,
                  color=["#4f81bd", "#c0504d", "#9bbb59", "#f79646"])
    for b, k in zip(bars, keys):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5,
                f"{bs[k]['pct']:.2f}%\n({bs[k]['traceable']})", ha="center", fontsize=8)
    ax.set_ylim(0, 100)
    ax.set_ylabel("traceable (%)")
    ax.set_title("Traceability by terminal status (denominator channel split)")
    p = OUT / "fig_2_by_status.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    return p


def fig_weekly() -> Path:
    wk = res["weekly_trend"]
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    xs = [w["week_start"] for w in wk]
    ys = [w["pct"] for w in wk]
    ax.plot(xs, ys, "o-", color="#c0504d", lw=2)
    for x, w in zip(xs, wk):
        ax.annotate(f"{w['pct']:.2f}%\nn={w['terminal']}", (x, w["pct"]),
                    textcoords="offset points", xytext=(0, 9), ha="center", fontsize=8)
    ax.axhline(res["current"], ls=":", color="#555", lw=1)
    ax.text(0.02, res["current"] + 2, f"all-time {res['current']:.2f}%",
            transform=ax.get_yaxis_transform(), color="#555", fontsize=8)
    ax.set_ylim(0, 100)
    ax.set_ylabel("traceable (%)")
    ax.set_title("Weekly traceability trend (by finished_at, ISO week)")
    p = OUT / "fig_3_weekly.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    return p


def fig_repo() -> Path:
    rows = res["by_repo_last7d"][:10]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    ys = [r["repo"] for r in rows][::-1]
    xs = [r["missing"] for r in rows][::-1]
    ax.barh(ys, xs, color="#c0504d")
    for y, r in zip(ys, rows[::-1]):
        ax.text(r["missing"] + 20, y, f"{r['missing']} ({r['missing_pct']:.1f}% of {r['terminal']})",
                va="center", fontsize=8)
    ax.set_xlabel("untraceable terminal missions (last 7 d)")
    ax.set_title("Untraceable missions by repo — top 10 (last 7 days)")
    ax.set_xlim(0, max(xs) * 1.35)
    p = OUT / "fig_4_repo_7d.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    return p


def fig_debt() -> Path:
    acc = res["accountability"]
    m_owed = acc["report_owed_and_missing"]
    m_dark = acc["not_flagged_but_missing"]
    with_rep = res["numerator"]
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    labels = ["has report artifact", "owed & missing", "not flagged & missing"]
    vals = [with_rep, m_owed, m_dark]
    colors = ["#4f81bd", "#c0504d", "#f79646"]
    bars = ax.bar(labels, vals, color=colors)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 80, str(v), ha="center", fontsize=8)
    ax.set_ylabel("terminal missions")
    ax.set_title("Accountability split of terminal missions\n"
                 f"(backfillable pool with evidence in result: {acc['missing_with_evidence_in_mission_result']})")
    ax.tick_params(axis="x", labelsize=7)
    p = OUT / "fig_5_debt.png"
    fig.tight_layout(); fig.savefig(p); plt.close(fig)
    return p


if __name__ == "__main__":
    for f in (fig_gap(), fig_status(), fig_weekly(), fig_repo(), fig_debt()):
        print(f)