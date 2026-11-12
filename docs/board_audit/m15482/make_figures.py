#!/usr/bin/env python3
"""m15482 board audit — 报告配图（全部读工件，零手抄数字）。

fig_tau_recovery.png
  (a) m15444 convergence_tau.csv 的 loss 收敛曲线（真实运行工件）
  (b) τ 恢复三方对照：ref=0.9 / 交付态 0.89971 / TensorLBM 干净 HEAD 0.55
      （后者来自本单 runs/m15482_audit/repro_check.json，独立复跑）
  (c) 墙钟复现性：m15444 记录 33.50s vs 本单复跑 41.99s
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

M = Path("/root/tp-agent/work/m15444")
OUT = Path("/root/tp-agent/work/m15482/runs/m15482_audit")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conv = list(csv.DictReader(open(M / "runs/gap_pp5_cpu_baseline/rep_0/convergence_tau.csv")))
    it = [int(r["iter"]) for r in conv]
    loss = [float(r["loss"]) for r in conv]

    repro = json.loads((OUT / "repro_check.json").read_text())
    rec = json.loads((M / "runs/gap_pp5_cpu_baseline/results.json").read_text())["results"][0]
    delivered = next(r for r in repro["runs"] if r["tag"] == "delivered_worktree")
    head = next(r for r in repro["runs"] if r["tag"] == "clean_head")

    fig, ax = plt.subplots(1, 3, figsize=(16, 4.6))

    ax[0].semilogy(it, loss, "o-", ms=3, lw=1.2, color="#1f77b4")
    ax[0].set_xlabel("SGD iteration")
    ax[0].set_ylabel("loss (misfit vs target field)")
    ax[0].set_title("(a) tau-mode convergence\nm15444 rep_0/convergence_tau.csv (grid=64, steps=50)")
    ax[0].grid(alpha=0.3, which="both")

    labels = ["ref (analytic\nby construction)", "as-delivered\n(worktree patch)",
              "clean TensorLBM HEAD\n(no patch)"]
    vals = [rec["tau_star"], delivered["tau_got"], head["tau_got"]]
    errs = [0.0, delivered["error_pct"], head["error_pct"]]
    colors = ["#2ca02c", "#1f77b4", "#d62728"]
    bars = ax[1].bar(labels, vals, color=colors, width=0.6)
    for b, v, e in zip(bars, vals, errs):
        ax[1].text(b.get_x() + b.get_width() / 2, v + 0.012, f"{v:.5f}\nerr {e:.2f}%",
                   ha="center", va="bottom", fontsize=9)
    ax[1].axhline(rec["tau_star"], ls="--", lw=1, color="k", alpha=0.6)
    ax[1].set_ylim(0.5, 1.02)
    ax[1].set_ylabel("recovered tau")
    ax[1].set_title("(b) tau recovery: recorded vs independent re-run\n(15% tolerance band)")

    ax[2].bar(["m15444 recorded", "m15482 re-run"], [rec["wall_s"], delivered["wall_s"]],
              color=["#7f7f7f", "#ff7f0e"], width=0.55)
    for x, v in enumerate([rec["wall_s"], delivered["wall_s"]]):
        ax[2].text(x, v + 0.6, f"{v:.2f}s", ha="center", fontsize=10)
    ax[2].set_ylabel("wall clock [s]")
    ax[2].set_title(f"(c) wall-clock reproducibility\nratio = {delivered['wall_s']/rec['wall_s']:.2f}x (1 rep each)")

    fig.tight_layout()
    p = OUT / "fig_tau_recovery.png"
    fig.savefig(p, dpi=150, bbox_inches="tight")
    print(f"-> {p} ({p.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())