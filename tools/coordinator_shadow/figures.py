#!/usr/bin/env python3
"""coordinator_shadow v1 —— 报告图（IN27-H1）。

四张图，各自回答一个问题：
1. ``fig_soak_timeline``  72h 演练里，器官怎么坏、影子在第几拍立单、什么被挡住；
2. ``fig_gate_decision``  每个越界拍的去向（立单/持久性挡/器官去重挡/冷却挡/LLM 否决）；
3. ``fig_fpr_modes``      两种判读模式的立单数与误报率 vs 30% 门槛（LLM 判读的价值）；
4. ``fig_live_pulse``     生产库真实一拍的器官读数 vs 阈值（含被修掉的声明率探针口径）。

全英文标注：避免容器内缺 CJK 字体导致图里豆腐块。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

COLORS = {"credit_backlog": "#c0392b", "oldest_queued_minutes": "#8e44ad",
          "error_total": "#d35400", "warn_total": "#f39c12", "declaration_rate": "#16a085"}
TRUTH_COLORS = {"real_fault": "#fdecea", "noise": "#eaf2fb", "benign": "#fef9e7"}


def _bands(timeline: Sequence[Mapping[str, Any]]) -> list[tuple[float, float, str]]:
    """把 timeline 里连续同 truth 的区间折叠成带（用于背景着色）。"""
    out: list[tuple[float, float, str]] = []
    start = None
    cur = None
    prev_t = None
    for row in timeline:
        truth = row.get("truth")
        if truth != cur:
            if cur in TRUTH_COLORS and start is not None and prev_t is not None:
                out.append((start, prev_t + 0.5, cur))
            start = row["t_hours"] if truth in TRUTH_COLORS else None
            cur = truth
        prev_t = row["t_hours"]
    if cur in TRUTH_COLORS and start is not None and prev_t is not None:
        out.append((start, prev_t + 0.5, cur))
    return out


def fig_soak_timeline(soak: Mapping[str, Any], path: str) -> str:
    tl = soak["timeline"]
    t = [r["t_hours"] for r in tl]
    fig, axes = plt.subplots(2, 2, figsize=(13, 7.2), sharex=True)
    panels = [(axes[0][0], "credit_backlog", "Credit backlog (settled evals unbooked)", [20, 50]),
              (axes[0][1], "oldest_queued_minutes", "Oldest unclaimed mission (min)", [120, 240]),
              (axes[1][0], "error_total", "daemon ERROR lines / hour", [3, 10]),
              (axes[1][1], "warn_total", "daemon WARN lines / hour", [12, 30])]
    filed_at = {f["created_at"]: f for f in soak["filings"]}
    filed_hours = []
    for row in tl:
        if row["filings"]:
            filed_hours.append(row["t_hours"])
    for ax, key, label, (warn, crit) in panels:
        ys = [r[key] for r in tl]
        ax.plot(t, ys, color=COLORS[key], lw=1.6, label=label)
        ax.axhline(warn, color="#7f8c8d", ls="--", lw=1.0, label=f"warn={warn:g}")
        ax.axhline(crit, color="#2c3e50", ls=":", lw=1.0, label=f"crit={crit:g}")
        for hs, he, truth in _bands(tl):
            ax.axvspan(hs, he, color=TRUTH_COLORS[truth], alpha=0.55, lw=0)
        for h in filed_hours:
            ax.axvline(h, color="#27ae60", lw=0.9, alpha=0.8)
        ax.set_title(label, fontsize=10)
        ax.set_ylabel("value")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=7, loc="upper left")
    for ax in axes[1]:
        ax.set_xlabel("hours since t0 (30 min per beat, 144 beats = 72 h)")
    handles = [Patch(color=TRUTH_COLORS["real_fault"], label="injected real fault"),
               Patch(color=TRUTH_COLORS["noise"], label="single-beat noise (persistence gate)"),
               Patch(color=TRUTH_COLORS["benign"], label="benign burst (LLM veto)"),
               plt.Line2D([], [], color="#27ae60", lw=1.0, label="diagnosis mission filed")]
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle("coordinator_shadow v1 — 72 h accelerated soak (synthetic platform state, 144 beats)", fontsize=11)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def fig_gate_decision(soak: Mapping[str, Any], path: str) -> str:
    beats = soak.get("beat_rows") or []
    filed = sum(len(b.get("filings") or []) for b in beats)
    reasons = Counter()
    for b in beats:
        for s in b.get("suppressed") or []:
            reasons[s.get("reason")] += 1
    vetoed = 0
    for b in beats:
        for f in (b.get("verdict") or {}).get("findings") or []:
            if isinstance(f, dict) and f.get("vetoed"):
                vetoed += 1
    labels = ["filed\n(mission)", "persistence\ngate", "organ\ndedupe", "signature\ncooldown",
              "LLM veto", "beat cap"]
    values = [filed, reasons.get("persistence", 0) + _count_short_consecutive(beats),
              reasons.get("organ-taken-this-beat", 0), reasons.get("cooldown", 0) + reasons.get("organ-cooldown", 0),
              vetoed, reasons.get("beat-cap", 0)]
    colors = ["#27ae60", "#95a5a6", "#2980b9", "#8e44ad", "#e67e22", "#bdc3c7"]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    bars = ax.bar(labels, values, color=colors)
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.02,
                str(val), ha="center", fontsize=9)
    ax.set_ylabel("beat-level decisions (count)")
    ax.set_title("Where each crossing goes: 1 organ-deduped filing per real fault, noise suppressed", fontsize=10)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _count_short_consecutive(beats: Sequence[Mapping[str, Any]]) -> int:
    """规则层看到越界但连击不足（consecutive < require_persistence）的 finding 数。"""
    n = 0
    for b in beats:
        for f in (b.get("verdict") or {}).get("findings") or []:
            if isinstance(f, dict) and not f.get("advisory") and int(f.get("consecutive") or 0) < 2:
                n += 1
    return n


def fig_fpr_modes(modes: Mapping[str, Mapping[str, Any]], path: str) -> str:
    names = list(modes)
    filed = [modes[m]["filings"] for m in names]
    fps = [modes[m]["false_positive_filings"] for m in names]
    fpr = [(fp / f if f else 0.0) for fp, f in zip(fps, filed)]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    x = range(len(names))
    axes[0].bar([i - 0.2 for i in x], filed, width=0.4, label="missions filed", color="#2980b9")
    axes[0].bar([i + 0.2 for i in x], fps, width=0.4, label="false positives", color="#c0392b")
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(names)
    axes[0].set_ylabel("count")
    axes[0].set_title("Filings and false positives by judge mode", fontsize=10)
    axes[0].legend(fontsize=8)
    axes[0].grid(axis="y", alpha=0.25)
    axes[1].bar(names, [v * 100 for v in fpr], color=["#27ae60", "#e67e22"][:len(names)])
    axes[1].axhline(30, color="#c0392b", ls="--", lw=1.2, label="maturity threshold 30%")
    for i, v in enumerate(fpr):
        axes[1].text(i, v * 100 + 0.8, f"{v * 100:.1f}%", ha="center", fontsize=9)
    axes[1].set_ylabel("false-positive rate (%)")
    axes[1].set_ylim(0, max(35, max(fpr) * 100 + 8))
    axes[1].set_title("FP rate vs threshold (adjudicated filings only)", fontsize=10)
    axes[1].legend(fontsize=8)
    axes[1].grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def fig_live_pulse(beat: Mapping[str, Any], path: str) -> str:
    organs = beat["pulse"]["organs"]
    spec = {"credit_backlog": (20, 50, "count"),
            "oldest_queued_minutes": (120, 240, "min"),
            "queued_stuck_2h": (5, 15, "missions"),
            "campaign_blocked": (5, 12, "nodes"),
            "queued_open": (25, 60, "missions"),
            "awaiting_pr_merge": (15, 40, "missions")}
    rows = [(k, organs[k]["value"], w, c) for k, (w, c, _u) in spec.items() if organs.get(k, {}).get("value") is not None]
    fig, axes = plt.subplots(1, len(rows), figsize=(2.6 * len(rows), 3.8))
    if len(rows) == 1:
        axes = [axes]
    for ax, (key, value, warn, crit) in zip(axes, rows):
        color = "#c0392b" if value >= crit else ("#e67e22" if value >= warn else "#27ae60")
        ax.bar(["live"], [value], color=color, width=0.5)
        ax.axhline(warn, color="#7f8c8d", ls="--", lw=1.0)
        ax.axhline(crit, color="#2c3e50", ls=":", lw=1.0)
        ax.set_title(key, fontsize=8)
        ax.text(0, value, f"{value:g}", ha="center", va="bottom", fontsize=9)
        ax.set_ylim(0, max(crit, value) * 1.25)
        ax.tick_params(labelsize=7)
        ax.grid(axis="y", alpha=0.2)
    decl = organs.get("declaration_rate", {}).get("value")
    fig.suptitle(f"Live production pulse (read-only) — declaration_rate={decl} (warn 0.60) / "
                 "dashed=warn, dotted=crit", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def render_all(soak_path: str, live_path: str, fpr_json: str, out_dir: str) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    soak = json.loads(Path(soak_path).read_text())
    live = json.loads(Path(live_path).read_text())
    modes = json.loads(Path(fpr_json).read_text())
    return {
        "soak_timeline": fig_soak_timeline(soak["soak"], str(out / "fig_soak_timeline.png")),
        "gate_decision": fig_gate_decision(soak["soak"], str(out / "fig_gate_decision.png")),
        "fpr_modes": fig_fpr_modes(modes, str(out / "fig_fpr_modes.png")),
        "live_pulse": fig_live_pulse(live, str(out / "fig_live_pulse.png")),
    }


if __name__ == "__main__":  # pragma: no cover
    import sys

    print(render_all(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "artifacts/coordinator_shadow"))