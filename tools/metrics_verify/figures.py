#!/usr/bin/env python3
"""Figures for the metrics-tool verification chain (matplotlib, Agg backend).

Four panels, one per verified facet, each carrying the injected truth and the
measured value so a reader can see *both* without the numbers being transcribed
by hand: the PNG is regenerated from the same report payload that is committed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Sequence

import matplotlib
matplotlib.use("Agg")  # noqa: E402  (must precede pyplot import)
import matplotlib.pyplot as plt  # noqa: E402

from .stats import lognormal_quantile  # noqa: E402

DPI = 140


def _save(fig, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return str(path)


def fig_pulse_latency(latencies: Sequence[float], summary: Dict[str, Any],
                      cfg: Dict[str, Any], out: Path) -> str:
    """ECDF of measured heartbeat latency vs the analytic lognormal + ceilings."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ordered = sorted(latencies)
    n = len(ordered)
    xs = ordered
    ys = [(i + 1) / n for i in range(n)]
    axes[0].plot(xs, ys, lw=1.6, label=f"measured (n={n})")
    # analytic reference in the same axes
    grid = [cfg["median_latency_s"] * math.exp(cfg["sigma"] * z)
            for z in (-2.0 + 0.05 * k for k in range(100))]
    ref = []
    for x in grid:
        ref.append(0.5 * (1.0 + math.erf((math.log(x / cfg["median_latency_s"]) / cfg["sigma"]) / math.sqrt(2))))
    axes[0].plot(grid, ref, "--", lw=1.2, label="analytic lognormal (injected)")
    axes[0].axvline(summary["mean"], color="tab:green", lw=1.0,
                    label=f"measured mean = {summary['mean']:.3f} s (limit 1 s)")
    axes[0].axvline(summary["p99"], color="tab:red", lw=1.0,
                    label=f"measured P99 = {summary['p99']:.3f} s (limit 5 s)")
    axes[0].set_xlabel("heartbeat one-way latency [s]")
    axes[0].set_ylabel("ECDF")
    axes[0].set_title("pulse: latency recovery vs injected distribution")
    axes[0].legend(fontsize=7, loc="lower right")
    axes[0].grid(alpha=0.3)

    axes[1].hist(latencies, bins=120, color="tab:blue", alpha=0.75, log=True)
    axes[1].axvline(summary["mean"], color="tab:green", lw=1.0)
    axes[1].axvline(summary["p99"], color="tab:red", lw=1.0)
    axes[1].set_xlabel("heartbeat one-way latency [s]")
    axes[1].set_ylabel("count (log)")
    axes[1].set_title("pulse: latency distribution, mean / P99 marked")
    axes[1].grid(alpha=0.3)
    return _save(fig, out)


def fig_chronicle(result: Dict[str, Any], out: Path) -> str:
    """Detector scoreboard: honest stream vs injected faults."""
    honest = result["honest"]
    disorder = result["disorder"]
    drop = result["drop_control"]
    dup = result["duplicate_control"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    labels = ["completeness", "order correct", "disorder detect", "drop detect", "duplicate detect"]
    values = [
        honest["completeness"] if honest["completeness"] is not None else 0.0,
        honest["order_correct_rate"],
        disorder["detection_rate"],
        1.0 if drop["detected"] else 0.0,
        1.0 if dup["detected"] else 0.0,
    ]
    colors = ["tab:blue", "tab:blue", "tab:orange", "tab:orange", "tab:orange"]
    bars = axes[0].bar(labels, values, color=colors)
    axes[0].axhline(0.99, ls="--", color="tab:red", lw=1.0, label="acceptance 0.99")
    axes[0].set_ylim(0, 1.05)
    axes[0].set_ylabel("rate")
    axes[0].set_title(f"chronicle: {honest['n_events']} events, fault detection")
    axes[0].legend(fontsize=8)
    axes[0].tick_params(axis="x", rotation=25, labelsize=8)
    for bar, val in zip(bars, values):
        axes[0].annotate(f"{val:.4f}", (bar.get_x() + bar.get_width() / 2, val),
                         ha="center", va="bottom", fontsize=7)
    axes[0].grid(alpha=0.3, axis="y")

    counts = {
        "injected disorder": disorder["n_injected_events"],
        "detected disorder": disorder["n_injected_detected"],
        "missing vs truth (1% drop)": drop["n_missing_vs_truth"],
        "duplicates (1% dup)": dup["n_duplicates"],
        "false alarms (honest)": honest["n_seq_regressions"] + honest["n_ts_regressions"],
    }
    axes[1].barh(list(counts), list(counts.values()),
                 color=["tab:orange", "tab:green", "tab:orange", "tab:orange", "tab:blue"])
    axes[1].set_xscale("symlog")
    axes[1].set_xlabel("event count (symlog)")
    axes[1].set_title("chronicle: injected vs recovered fault counts")
    axes[1].grid(alpha=0.3, axis="x")
    for i, (k, v) in enumerate(counts.items()):
        axes[1].annotate(str(v), (v, i), va="center", ha="left", fontsize=8)
    return _save(fig, out)


def fig_portrait(result: Dict[str, Any], workers: List[Dict[str, Any]], out: Path) -> str:
    """Measured vs truth per worker + error budget."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    truth = [w["success_rate_truth"] for w in workers]
    meas = [w["success_rate_measured"] for w in workers]
    axes[0].scatter(truth, meas, s=40, color="tab:blue", label="success rate")
    lo = min(truth + meas) - 0.05
    hi = max(truth + meas) + 0.05
    axes[0].plot([lo, hi], [lo, hi], "--", color="gray", lw=1.0, label="y = x")
    axes[0].fill_between([lo, hi], [lo - 0.05, hi - 0.05], [lo + 0.05, hi + 0.05],
                         color="tab:green", alpha=0.12, label="±5 % tolerance band")
    axes[0].set_xlim(lo, hi)
    axes[0].set_ylim(lo, hi)
    axes[0].set_xlabel("simulator truth success rate")
    axes[0].set_ylabel("portrait.aggregate success rate")
    axes[0].set_title("portrait: profile vs ground truth")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)

    names = ["success rate\n(abs, pp)", "mean wall time\n(rel)", "domain share\n(max abs)"]
    vals = [result["max_success_rate_abs_error"], result["max_mean_wall_rel_error"],
            result["max_domain_share_abs_error"]]
    bars = axes[1].bar(names, vals, color="tab:blue")
    axes[1].axhline(result["tolerance"], ls="--", color="tab:red", lw=1.0, label="5 % acceptance")
    axes[1].set_ylabel("max error over workers")
    axes[1].set_title(f"portrait: error budget ({result['n_workers']} workers, "
                      f"{result['n_missions']} missions)")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3, axis="y")
    for bar, val in zip(bars, vals):
        axes[1].annotate(f"{val:.2e}", (bar.get_x() + bar.get_width() / 2, val),
                         ha="center", va="bottom", fontsize=8)
    return _save(fig, out)


def fig_drift(drift: Dict[str, Any], example: Dict[str, Any], out: Path) -> str:
    """Alarm trajectory for one seed + latency distribution over seeds."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    traj = example["trajectory"]
    idx = [t[0] for t in traj]
    stat = [t[1] for t in traj]
    axes[0].plot(idx, stat, lw=1.4, label="CUSUM statistic")
    axes[0].axhline(example["threshold_h"], ls="--", color="tab:red", lw=1.0,
                    label=f"decision interval h = {example['threshold_h']}")
    if example.get("change_index") is not None:
        axes[0].axvline(example["change_index"], color="black", lw=1.0, label="true change")
    if example.get("alarm_index") is not None:
        axes[0].axvline(example["alarm_index"], color="tab:orange", lw=1.4, label="alarm")
    axes[0].set_xlabel("event index")
    axes[0].set_ylabel("log-likelihood ratio (nats)")
    axes[0].set_title("portrait drift: CUSUM trajectory")
    axes[0].legend(fontsize=7)
    axes[0].grid(alpha=0.3)

    axes[1].bar(["latency mean", "latency max", "ceiling"],
                [drift["latency_mean"] or 0.0, drift["latency_max"] or 0.0,
                 drift["latency_threshold"]],
                color=["tab:blue", "tab:orange", "tab:red"])
    axes[1].set_ylabel("events")
    axes[1].set_title(f"drift detection latency over {drift['n_trials']} seeds "
                      f"(misses={drift['n_missed']}, false alarms={drift['n_false_alarms_stable']})")
    axes[1].grid(alpha=0.3, axis="y")
    for i, v in enumerate([drift["latency_mean"] or 0.0, drift["latency_max"] or 0.0,
                           drift["latency_threshold"]]):
        axes[1].annotate(f"{v:.2f}", (i, v), ha="center", va="bottom", fontsize=8)
    return _save(fig, out)