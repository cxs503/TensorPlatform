#!/usr/bin/env python3
"""worker 画像出图（只读，读 report.json 画 png）。

四张图（判据图，供报告与后续调度波引用）：
  fig1_worker_throughput.png   工单数 top-N + 成功率
  fig2_pickup_wall_dist.png    领取延迟（log 轴）与在岗时长分布 + 7200s 预算线
  fig3_completion_7200.png     各 worker 7200s 内完成率（排序）与平台 pooled 线
  fig4_repo_distribution.png   活跃 worker 的仓分布（top-N 堆叠）

用法：python3 -m tools.worker_portrait.figures --report artifacts/worker_portraits/report.json \
        --out-dir artifacts/worker_portraits/figs [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

WALL_BUDGET_S = 7200.0
TOP_N = 20


def _save(fig, path: str, dry_run: bool) -> None:
    if dry_run:
        plt.close(fig)
        print(f"[dry-run] skip {path}")
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"[write] {path}")


def fig_throughput(rep: Dict[str, Any], path: str, dry_run: bool) -> None:
    workers = rep["workers"][:TOP_N]
    names = [w["worker"] for w in workers][::-1]
    counts = [w["n_missions"] for w in workers][::-1]
    rates = [w["success_rate"] for w in workers][::-1]
    fig, ax = plt.subplots(figsize=(8.6, 6.4))
    bars = ax.barh(names, counts, color="#3b6ea5")
    for bar, rate in zip(bars, rates):
        label = f"{rate * 100:.0f}%" if rate is not None else "n/a"
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{int(bar.get_width())} / succ {label}", va="center", fontsize=8)
    ax.set_xlabel("claimed missions (n_missions)")
    ax.set_title(f"Worker throughput top-{len(names)} (snapshot {rep['generated_at'][:19]}, read-only)")
    ax.set_xlim(0, max(counts) * 1.32)
    ax.grid(axis="x", alpha=0.3)
    _save(fig, path, dry_run)


def fig_dist(rep: Dict[str, Any], path: str, dry_run: bool) -> None:
    pickups = [m["pickup_s"] for w in rep["workers"] for m in w["per_mission"]
               if m["pickup_s"] is not None]
    walls = [m["wall_s"] for w in rep["workers"] for m in w["per_mission"]
             if m["wall_s"] is not None]
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4))
    p = [max(1e-3, x) for x in pickups]
    axes[0].hist(p, bins=60, color="#c0504d", log=True)
    axes[0].set_xscale("log")
    axes[0].set_xlabel("pickup latency claim_at - created_at [s] (log)")
    axes[0].set_ylabel("missions (log)")
    axes[0].set_title(f"Pickup latency, n={len(p)} (median {rep['pooled']['pickup_s']['median']:.1f}s)")
    axes[0].grid(alpha=0.3)

    axes[1].hist(walls, bins=80, color="#4f81bd")
    axes[1].axvline(WALL_BUDGET_S, color="k", ls="--", lw=1.2, label="budget 7200s")
    axes[1].set_xlabel("on-duty duration finished_at - claim_at [s]")
    axes[1].set_ylabel("missions")
    rate = rep["pooled"]["completion_7200"]["rate"] or 0.0
    axes[1].set_title(f"On-duty duration, n={len(walls)}; within-7200s rate {rate * 100:.1f}%")
    axes[1].legend(loc="upper right", fontsize=8)
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    _save(fig, path, dry_run)


def fig_completion(rep: Dict[str, Any], path: str, dry_run: bool) -> None:
    rows: List[tuple] = [(w["worker"], w["completion_7200"]["rate"], w["n_missions"])
                         for w in rep["workers"] if w["completion_7200"]["rate"] is not None]
    rows.sort(key=lambda r: r[1])
    names = [r[0] for r in rows]
    vals = [r[1] * 100 for r in rows]
    fig, ax = plt.subplots(figsize=(8.6, max(4.6, 0.17 * len(rows) + 1.4)))
    ax.barh(names, vals, color="#7f9f68")
    pooled = (rep["pooled"]["completion_7200"]["rate"] or 0) * 100
    ax.axvline(pooled, color="#c0504d", ls="--", lw=1.2, label=f"platform pooled {pooled:.1f}%")
    ax.set_xlim(60, 100)
    ax.set_xlabel("share finished within 7200s [%] (finished missions)")
    ax.set_title("Per-worker completion within 7200s budget (ascending)")
    ax.legend(loc="lower right", fontsize=8)
    ax.tick_params(axis="y", labelsize=6.5)
    ax.grid(axis="x", alpha=0.3)
    _save(fig, path, dry_run)


def fig_repo(rep: Dict[str, Any], path: str, dry_run: bool) -> None:
    workers = rep["workers"][:TOP_N]
    repos: List[str] = []
    for w in workers:
        for repo in w["repo_share"]:
            if repo not in repos:
                repos.append(repo)
    repos = repos[:8]
    names = [w["worker"] for w in workers][::-1]
    fig, ax = plt.subplots(figsize=(9.2, 6.4))
    left = [0.0] * len(names)
    cmap = plt.get_cmap("tab10")
    for idx, repo in enumerate(repos):
        vals = [w["repo_share"].get(repo, 0) * 100 for w in workers][::-1]
        ax.barh(names, vals, left=left, label=repo, color=cmap(idx % 10))
        left = [a + b for a, b in zip(left, vals)]
    ax.set_xlabel("mission share [%]")
    ax.set_title(f"Repo distribution of active workers (top-{len(names)})")
    ax.legend(fontsize=7, ncol=2, loc="lower right")
    ax.tick_params(axis="y", labelsize=7)
    _save(fig, path, dry_run)


def build_all(report_path: str, out_dir: str, dry_run: bool = False) -> List[str]:
    with open(report_path, "r", encoding="utf-8") as fh:
        rep = json.load(fh)
    names = ["fig1_worker_throughput.png", "fig2_pickup_wall_dist.png",
             "fig3_completion_7200.png", "fig4_repo_distribution.png"]
    funcs = [fig_throughput, fig_dist, fig_completion, fig_repo]
    for name, fn in zip(names, funcs):
        fn(rep, os.path.join(out_dir, name), dry_run)
    return names


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="worker_portrait.figures", description="worker 画像出图")
    ap.add_argument("--report", default="artifacts/worker_portraits/report.json")
    ap.add_argument("--out-dir", default="artifacts/worker_portraits/figs")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    build_all(args.report, args.out_dir, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())