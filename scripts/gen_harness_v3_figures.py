#!/usr/bin/env python3
"""从 gate_report.json 生成 Harness v3 报告用图（数字全部程序化读取，零手抄）。

输出:
  docs/harness_v3/figs/harness_v3_gate.png            门禁总览（6 场景：恢复步数 / 调用消耗）
  docs/harness_v3/figs/harness_v3_resume_timeline.png 任务时间线重建（attempt1 被杀 → attempt2 续跑）

用法: python3 scripts/gen_harness_v3_figures.py [--report docs/harness_v3/gate_report.json]
"""
from __future__ import annotations

import argparse
import json
import pathlib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="docs/harness_v3/gate_report.json")
    ap.add_argument("--outdir", default="docs/harness_v3/figs")
    a = ap.parse_args(argv)
    rep = json.loads(pathlib.Path(a.report).read_text())
    out = pathlib.Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sc = {s["scenario"]: s for s in rep["scenarios"]}
    names = list(sc)
    short = [n.split("_")[0] for n in names]
    resumed = [sc[n]["observed"].get("resumed_step_count", 0) for n in names]
    ok = [sc[n]["pass"] for n in names]

    # 图 1：恢复步数 + 预算纪律
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
    colors = ["#2b7bba" if p else "#c0504d" for p in ok]
    ax = axes[0]
    b = ax.bar(short, resumed, color=colors)
    ax.axhline(1, ls="--", c="k", lw=0.9)
    ax.set_title("resumed steps after kill -9\n(dashed = criterion >=1, scenario A)")
    ax.set_ylabel("steps recovered from journal")
    for bar, v in zip(b, resumed):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.06, str(v), ha="center", fontsize=9)
    ax = axes[1]
    calls = [sc[n]["observed"].get("calls_used", 0) for n in names]
    b2 = ax.bar(short, calls, color="#4f81bd")
    cap = sc.get("D_budget_cap", {}).get("observed", {}).get("budget")
    if cap:
        ax.axhline(cap, ls="--", c="r", lw=1.0, label=f"budget cap = {cap} (scenario D)")
        ax.legend(fontsize=8)
    ax.set_ylabel("tool calls consumed (cross-attempt)")
    ax.set_title("budget is cumulative across resumes")
    for bar, v in zip(b2, calls):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.06, str(v), ha="center", fontsize=9)
    fig.suptitle(f"Harness v3 acceptance gate — {rep['verdict']} "
                 f"({rep['scenario_pass']}/{rep['scenario_total']} scenarios)", fontsize=10)
    fig.tight_layout()
    p1 = out / "harness_v3_gate.png"
    fig.savefig(p1, dpi=150)
    plt.close(fig)

    # 图 2：由观测重建的任务时间线（A 场景）
    obs = sc["A_crash_resume"]["observed"]
    done = obs.get("done", 0) or 0
    resumed = obs.get("resumed", []) or []
    total = done if done else 6
    fig, ax = plt.subplots(figsize=(9.5, 2.9))
    for i in range(total):
        sid = f"s{i + 1}"
        if sid in resumed:
            ax.barh(0.6, 0.92, left=i, color="#2b7bba", edgecolor="k", lw=0.4)
            ax.text(i + 0.46, 0.6, sid, ha="center", va="center", color="w", fontsize=8)
        else:
            ax.barh(-0.6, 0.92, left=i, color="#4f81bd", edgecolor="k", lw=0.4)
            ax.text(i + 0.46, -0.6, sid, ha="center", va="center", color="w", fontsize=8)
    for sid in resumed:
        ax.barh(0.6, 0.92, left=int(sid[1:]) - 1, color="#2b7bba", alpha=0.0)
    kill_x = len(resumed)
    ax.axvline(kill_x, ls="--", c="#c0504d", lw=1.2)
    ax.text(kill_x + 0.05, 1.15, f"SIGKILL at progress {obs.get('killed_at_progress')}"
            f"\n(attempt 1, calls={len(resumed)})", color="#c0504d", fontsize=8)
    ax.text(-0.15, 0.6, "attempt 1", ha="right", va="center", fontsize=9)
    ax.text(-0.15, -0.6, f"attempt 2 (resumed {len(resumed)})", ha="right", va="center",
            fontsize=9)
    ax.set_xlim(-1.6, total + 1.4)
    ax.set_ylim(-1.2, 1.5)
    ax.set_yticks([])
    ax.set_xlabel("step index in mission")
    ax.set_title("Reconstruction of journal-driven resume (scenario A): "
                 "filled = reused from journal, not redone", fontsize=9)
    fig.tight_layout()
    p2 = out / "harness_v3_resume_timeline.png"
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    print(json.dumps({"figure1": str(p1), "figure2": str(p2),
                      "resumed": len(resumed), "done": total}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())