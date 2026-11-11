"""Figures for the ledger-audit report (matplotlib, no domain content)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .classifier import RULE_IDS  # noqa: E402
from .spec import GATES, LEVELS  # noqa: E402

GATE_LABELS = {
    "test_pass_rate": "test pass rate",
    "benchmark_error_pct": "benchmark error",
    "independent_review": "independent review",
}


def fig_gate_matrix(report: Mapping[str, Any], path: str | Path) -> Path:
    """Capability x evidence-item gate matrix with the derived level printed."""
    results = sorted(report["results"], key=lambda r: r["capability_id"])
    n = len(results)
    grid = []
    for res in results:
        by_item = {item["item"]: item for item in res["chain"]["items"]}
        grid.append([1.0 if by_item[g]["passed"] else 0.0 for g in GATES])

    fig, ax = plt.subplots(figsize=(6.4, 0.34 * n + 1.9))
    ax.imshow(grid, cmap="RdYlGn", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(GATES)))
    ax.set_xticklabels([GATE_LABELS.get(g, g) for g in GATES], fontsize=8)
    ax.set_yticks(range(n))
    ax.set_yticklabels(
        [f"{r['capability_id']} → {r['level']} ({r['rule_id']})" for r in results],
        fontsize=7,
    )
    for i, row in enumerate(grid):
        for j, val in enumerate(row):
            ax.text(j, i, "PASS" if val else "FAIL", ha="center", va="center", fontsize=7)
    ax.set_title(
        "evidence-item gate matrix and derived level\n"
        f"thresholds sha256={report['thresholds_sha256'][:12]}…  "
        f"ledger sha256={report['ledger']['sha256'][:12]}…",
        fontsize=8,
    )
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_audit_profile(report: Mapping[str, Any], path: str | Path) -> Path:
    """Level distribution, rule usage and transition mix side by side."""
    results = report["results"]
    level_counts = [sum(1 for r in results if r["level"] == lvl) for lvl in LEVELS]
    rule_counts = [sum(1 for r in results if r["rule_id"] == rid) for rid in RULE_IDS]
    trans_keys = ["UPGRADE", "HOLD", "DOWNGRADE"]
    trans_counts = [sum(1 for r in results if r["transition"] == k) for k in trans_keys]

    fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.4))
    axes[0].bar(LEVELS, level_counts, color="#3b6ea5")
    axes[0].set_title("derived level distribution", fontsize=9)
    axes[0].tick_params(axis="x", rotation=35, labelsize=7)
    axes[0].set_ylabel("capabilities", fontsize=8)

    axes[1].bar(list(RULE_IDS), rule_counts, color="#a5563b")
    axes[1].set_title("rule that fired", fontsize=9)
    axes[1].set_xlabel("rule id", fontsize=8)

    axes[2].bar(trans_keys, trans_counts, color="#3b8f5a")
    axes[2].set_title("ledger transition", fontsize=9)
    axes[2].set_xlabel("transition", fontsize=8)

    score = report["known_label_score"]
    acc = score["accuracy"]
    acc_text = "n/a" if acc is None else f"{100.0 * acc:.1f}%"
    fig.suptitle(
        f"known-label accuracy {acc_text} ({score['matched']}/{score['judged']})   "
        f"report sha256={report['report_sha256'][:12]}…",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_idempotency(digest_runs: Sequence[Mapping[str, Any]], path: str | Path) -> Path:
    """Digest stability across repeated audits (bit identity as a picture)."""
    labels = [f"run {d['run']}" for d in digest_runs]
    values = [int(str(d["ledger_sha256"])[:8], 16) / float(16**8) for d in digest_runs]
    fig, ax = plt.subplots(figsize=(6.0, 2.6))
    ax.bar(labels, values, color="#4b4b8f")
    for i, d in enumerate(digest_runs):
        ax.text(i, values[i], str(d["ledger_sha256"])[:10] + "…", ha="center", va="bottom", fontsize=7)
    ax.set_ylim(0, 1.15)
    ax.set_yticks([])
    ax.set_title(
        "idempotency: identical ledger digest on every re-run\n"
        f"distinct digests = {len({d['ledger_sha256'] for d in digest_runs})}",
        fontsize=8,
    )
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out