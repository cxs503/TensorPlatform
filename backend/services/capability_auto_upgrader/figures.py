"""Figures for the capability auto-upgrader acceptance run (matplotlib, Agg)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402

LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
COLORS = {"promote": "#1b7f3b", "hold": "#8c8c8c", "demote": "#b3261e"}


def _save(fig, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return str(path)


def fig_gate_timeline(timeline: Sequence[Dict[str, Any]], policy: Dict[str, Any], path: Path) -> str:
    """Per-run pass rate / benchmark error against the two policy gates."""
    seqs = [row["seq"] for row in timeline]
    pass_rate = [row["pass_rate"] * 100.0 for row in timeline]
    error = [row["error_pct"] for row in timeline]

    fig, ax1 = plt.subplots(figsize=(9.0, 4.6))
    ax1.plot(seqs, pass_rate, "o-", color="#1f4e9c", linewidth=2, label="pytest pass rate [%]")
    ax1.axhline(policy["min_pass_rate"] * 100.0, color="#1f4e9c", linestyle="--", linewidth=1.2,
                label=f"gate: pass rate >= {policy['min_pass_rate'] * 100:.0f}%")
    ax1.set_xlabel("run sequence")
    ax1.set_ylabel("pytest pass rate [%]", color="#1f4e9c")
    ax1.set_ylim(60, 105)
    ax1.tick_params(axis="y", labelcolor="#1f4e9c")

    ax2 = ax1.twinx()
    ax2.plot(seqs, error, "s-", color="#b3261e", linewidth=2, label="benchmark error [%]")
    ax2.axhline(policy["max_error_pct"], color="#b3261e", linestyle="--", linewidth=1.2,
                label=f"gate: error <= {policy['max_error_pct']:g}%")
    ax2.set_ylabel("benchmark error vs reference [%]", color="#b3261e")
    ax2.set_ylim(0, max(max(error) * 1.3, policy["max_error_pct"] * 2))
    ax2.tick_params(axis="y", labelcolor="#b3261e")

    window = int(policy["promotion_window"])
    if len(seqs) >= window:
        ax1.axvspan(seqs[0] - 0.4, seqs[window - 1] + 0.4, color="#1b7f3b", alpha=0.10,
                    label=f"promotion window ({window} runs)")
    for row in timeline:
        if row.get("action") != "hold":
            ax1.annotate(
                f"{row['action']}\n{row['level_before']}→{row['level_after']}",
                xy=(row["seq"], pass_rate[seqs.index(row["seq"])]),
                xytext=(0, 26),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color=COLORS.get(row["action"], "black"),
                arrowprops={"arrowstyle": "->", "color": COLORS.get(row["action"], "black"), "lw": 1.0},
            )
    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, fontsize=7.5, loc="lower left")
    ax1.set_title("Promotion gate: both conditions must hold for N consecutive runs")
    ax1.grid(alpha=0.25)
    return _save(fig, path)


def fig_level_trajectory(events: Sequence[Dict[str, Any]], path: Path) -> str:
    """Derived level after each ledger event (append-only history)."""
    xs, ys, labels = [], [], []
    level = None
    for idx, event in enumerate(events, 1):
        if event["direction"] in ("register", "upgrade", "downgrade") and event.get("level_after"):
            level = event["level_after"]
        xs.append(idx)
        ys.append(LEVELS.index(level) if level else -0.5)
        labels.append(event["direction"])

    fig, ax = plt.subplots(figsize=(9.0, 4.2))
    ax.step(xs, ys, where="post", color="#1f4e9c", linewidth=2)
    ax.plot(xs, ys, "o", color="#1f4e9c", markersize=5)
    for x, y, label in zip(xs, ys, labels):
        if label in ("upgrade", "downgrade"):
            ax.annotate(label, xy=(x, y), xytext=(0, -18), textcoords="offset points",
                        ha="center", fontsize=8, color=COLORS["promote" if label == "upgrade" else "demote"])
    ax.set_yticks(range(len(LEVELS)))
    ax.set_yticklabels(LEVELS)
    ax.set_xticks(xs)
    ax.set_xlabel("ledger event (append-only order)")
    ax.set_ylabel("derived evidence level")
    ax.set_title("Level trajectory replayed from the feature ledger")
    ax.grid(alpha=0.25, axis="y")
    return _save(fig, path)


def fig_decision_grid(grid: Dict[str, Any], policy: Dict[str, Any], path: Path) -> str:
    """Decision map over (pass rate, benchmark error) — the gate is a rectangle."""
    actions = {"hold": 0, "promote": 1, "demote": 2}
    matrix = [[actions[a] for a in row] for row in grid["actions"]]
    cmap = ListedColormap(["#d9d9d9", "#1b7f3b", "#b3261e"])
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], cmap.N)

    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    im = ax.imshow(matrix, origin="lower", cmap=cmap, norm=norm, aspect="auto",
                   extent=[grid["pass_rate"][0], grid["pass_rate"][-1],
                           grid["error_pct"][0], grid["error_pct"][-1]])
    ax.axvline(policy["min_pass_rate"], color="black", linestyle="--", linewidth=1.2)
    ax.axhline(policy["max_error_pct"], color="black", linestyle="--", linewidth=1.2)
    ax.set_xlabel("pytest pass rate")
    ax.set_ylabel("benchmark error vs reference [%]")
    ax.set_title("Promotion region (green) for a MOCK capability, window = %d" % int(policy["promotion_window"]))
    cbar = fig.colorbar(im, ticks=[0, 1, 2])
    cbar.ax.set_yticklabels(["hold", "promote", "demote"])
    return _save(fig, path)


def fig_provenance(checks: Dict[str, Any], path: Path) -> str:
    """What verification found: chain integrity and both tamper probes."""
    labels = ["events\nchained", "JSONL tamper\ndetected", "SQLite tamper\ndetected",
              "evaluations\nverified"]
    values = [
        1 if checks["chain_ok"] else 0,
        1 if checks["tamper_jsonl_detected"] else 0,
        1 if checks["tamper_sqlite_detected"] else 0,
        1 if checks["evaluations_ok"] else 0,
    ]
    colors = ["#1b7f3b" if v else "#b3261e" for v in values]
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    bars = ax.bar(labels, values, color=colors, width=0.55)
    for bar, value, n in zip(bars, values, [checks["n_events"], checks["tamper_jsonl_problems"],
                                            checks["tamper_sqlite_problems"], checks["n_evaluations"]]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.03, f"{value} ({n})",
                ha="center", fontsize=9)
    ax.set_ylim(0, 1.25)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["fail", "pass"])
    ax.set_title("Provenance audit: hash chain intact, deliberate edits detected")
    return _save(fig, path)