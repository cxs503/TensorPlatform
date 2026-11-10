"""cycle_baseline.figures —— 报告配图（纯 matplotlib，无交互后端）。"""
from __future__ import annotations

import pathlib
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import spec  # noqa: E402


def _finite(vals: Sequence[Any]) -> list[float]:
    return [float(v) for v in vals if isinstance(v, (int, float))]


def fig_segment_quantiles(ledger: dict, outdir: pathlib.Path) -> pathlib.Path:
    segs = list(spec.SEGMENTS)
    p50 = [_finite([ledger[s]["p50"]]) for s in segs]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    width = 0.26
    for i, q in enumerate(("p50", "p90", "p99")):
        vals = [max(1e-3, float(ledger[s][q])) if ledger[s][q] is not None else 0.0
                for s in segs]
        ax.bar([x + (i - 1) * width for x in range(len(segs))], vals, width, label=q)
    ax.set_yscale("log")
    ax.set_xticks(range(len(segs)))
    labels = [f"{s}\n(n={ledger[s]['n']}, cov={ledger[s]['coverage']:.2f})"
              if ledger[s]["coverage"] is not None else s for s in segs]
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("duration [s] (log)")
    ax.set_title("Cycle four-segment quantiles (cohort %d)" % ledger.get("cohort_n", 0))
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = outdir / "fig_segment_quantiles.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def fig_coverage_gate(gate: dict, outdir: pathlib.Path) -> pathlib.Path:
    segs = list(spec.SEGMENTS)
    cov = [gate["per_segment"][s]["coverage"] or 0.0 for s in segs]
    colors = ["#2b8a3e" if c >= spec.COVERAGE_MIN else "#c92a2a" for c in cov]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.bar(segs, cov, color=colors)
    ax.axhline(spec.COVERAGE_MIN, color="black", linestyle="--", linewidth=1,
               label=f"gate {spec.COVERAGE_MIN:.0%}")
    for i, c in enumerate(cov):
        ax.text(i, c + 0.02, f"{c:.1%}", ha="center", fontsize=8)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("segment coverage")
    ax.set_title("Segment evidence coverage vs preregistered gate (WITHHELD if below)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = outdir / "fig_coverage_gate.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def fig_policy_fairness(sim: dict, outdir: pathlib.Path) -> pathlib.Path:
    pols = list(sim["policies"])
    j_meas = [sim["policies"][p]["measured"]["jain_count"] or 0.0 for p in pols]
    j_theory = [sim["policies"][p]["theory"]["jain"] or 0.0 for p in pols]
    g_meas = [sim["policies"][p]["measured"]["gini_count"] or 0.0 for p in pols]
    g_theory = [sim["policies"][p]["theory"]["gini"] or 0.0 for p in pols]
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8))
    for ax, meas, theo, title in ((axes[0], j_meas, j_theory, "Jain index"),
                                  (axes[1], g_meas, g_theory, "Gini of allocation")):
        x = range(len(pols))
        ax.bar([i - 0.2 for i in x], meas, 0.4, label="simulated")
        ax.bar([i + 0.2 for i in x], theo, 0.4, label="theory")
        ax.set_xticks(list(x))
        ax.set_xticklabels(pols, fontsize=8)
        ax.set_title(title)
        ax.legend(fontsize=8)
    fig.suptitle("Three-policy fairness: simulator vs analytic prediction", fontsize=10)
    fig.tight_layout()
    path = outdir / "fig_policy_fairness.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def fig_cycle_distribution(records: Sequence[dict], ledger: dict,
                           outdir: pathlib.Path) -> pathlib.Path:
    vals = [float(r["cycle_s"]) for r in records
            if r.get("cycle_s") is not None and r["cycle_s"] > 0]
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    if vals:
        ax.hist(vals, bins=60, color="#4c6ef5", alpha=0.85)
        ax.set_xscale("log")
        p50 = ledger["cycle_s"]["p50"]
        if p50:
            ax.axvline(p50, color="black", linestyle="--",
                       label=f"p50 = {p50:,.0f} s")
            ax.legend(fontsize=8)
    ax.set_xlabel("cycle = finished_at − created_at [s] (log)")
    ax.set_ylabel("missions")
    ax.set_title(f"Mission cycle distribution (n={len(vals)})")
    fig.tight_layout()
    path = outdir / "fig_cycle_distribution.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def render_all(report: dict, records: Sequence[dict], outdir: pathlib.Path) -> list[dict]:
    outdir.mkdir(parents=True, exist_ok=True)
    made = [
        (fig_segment_quantiles(report["ledger"], outdir),
         "四段分位账本：p50/p90/p99（对数轴），标注各段样本数与覆盖率"),
        (fig_coverage_gate(report["coverage_gate"], outdir),
         "各段证据覆盖率 vs 预注册 80% 门限；低于门限者标红并降级 WITHHELD"),
        (fig_policy_fairness(report["policy_simulator"], outdir),
         "三策略模拟 vs 解析预测：左 Jain、右 Gini（份额口径）"),
        (fig_cycle_distribution(records, report["ledger"], outdir),
         "周期分布（尤尔-辛普森不适用/对数轴）+ p50 标记"),
    ]
    return [{"path": str(p), "name": p.name, "caption": c} for p, c in made]