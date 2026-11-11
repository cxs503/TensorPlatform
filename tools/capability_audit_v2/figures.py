"""审计图（matplotlib Agg，无交互后端）。

图只画台账里已有的数字（零手抄）：等级分布、一致率 vs 多数类基线、证据面矩阵。
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LEVEL_ORDER = ["REAL", "WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED"]
COLORS = {"REAL": "#2a9d8f", "WITHHELD": "#8d99ae", "MOCK": "#e76f51",
          "STUB": "#e9c46a", "NOT_SUPPORTED": "#6c757d"}


def level_tally(ledger: dict, static_tally: dict, out: Path) -> Path:
    v2 = ledger["summary"]["level_tally"]
    levels = [lv for lv in LEVEL_ORDER if v2.get(lv) or static_tally.get(lv)]
    xs = range(len(levels))
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=150)
    w = 0.38
    ax.bar([x - w / 2 for x in xs], [static_tally.get(lv, 0) for lv in levels],
           width=w, label="static only (v1 cascade)", color="#adb5bd")
    ax.bar([x + w / 2 for x in xs], [v2.get(lv, 0) for lv in levels],
           width=w, label="with executable evidence (v2)", color=[COLORS[lv] for lv in levels])
    ax.set_xticks(list(xs), levels)
    ax.set_ylabel("capabilities")
    ax.set_title("evidence level tally: static vs executable-evidence cascade")
    for x, lv in zip(xs, levels):
        ax.text(x - w / 2, static_tally.get(lv, 0) + 0.15, str(static_tally.get(lv, 0)),
                ha="center", fontsize=8)
        ax.text(x + w / 2, v2.get(lv, 0) + 0.15, str(v2.get(lv, 0)), ha="center", fontsize=8)
    ax.legend(fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def agreement(agreement_block: dict, out: Path, static_block: dict | None = None) -> Path:
    val = agreement_block.get("agreement")
    base = agreement_block.get("majority_baseline") or 0.0
    n = agreement_block.get("n") or 0
    static_val = (static_block or {}).get("agreement")
    fig, ax = plt.subplots(figsize=(6.2, 3.4), dpi=150)
    labels = ["static only\n(v1 cascade)", "with executable\nevidence (v2)",
              "majority-class\nbaseline"]
    values = [static_val or 0.0, val or 0.0, base]
    bars = ax.bar(labels, values, color=["#adb5bd", "#264653", "#ced4da"], width=0.55)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v*100:.1f}%", ha="center", fontsize=9)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("agreement with manual review")
    ax.set_title(f"agreement vs majority-class baseline (n={n} capabilities)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def evidence_planes(ledger: dict, out: Path) -> Path:
    caps = ledger["capabilities"]
    cols = ["impl", "tests", "benchmark", "pytest exec", "probe exec", "contradiction"]
    rows, matrix = [], []
    for c in caps:
        ev = c.get("evidence") or {}
        ex = c.get("exec_evidence") or {}
        rows.append(c["id"])
        matrix.append([
            1 if ev.get("impl_files") else 0,
            1 if ev.get("test_files") else 0,
            1 if (ev.get("benchmark") or {}).get("rows") else 0,
            1 if (ex.get("pytest") or {}).get("bound") else 0,
            1 if (ex.get("probe") or {}).get("bound") else 0,
            1 if c.get("contradictions") else 0,
        ])
    fig, ax = plt.subplots(figsize=(6.4, max(4.0, 0.28 * len(rows) + 1.4)), dpi=150)
    ax.imshow(matrix, cmap="Blues", vmin=0, vmax=1.4, aspect="auto")
    ax.set_xticks(range(len(cols)), cols, rotation=30, ha="right", fontsize=8)
    ax.set_yticks(range(len(rows)), rows, fontsize=7)
    ax.set_title("evidence planes per capability (filled = present)")
    ax.set_xticks([x - 0.5 for x in range(1, len(cols))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(rows))], minor=True)
    ax.grid(which="minor", color="white", linewidth=0.6)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out