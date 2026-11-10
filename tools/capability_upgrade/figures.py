"""Figures for the P218-N01 acceptance report.

Three figures, all generated from run artifacts (no hand-typed numbers):

1. ``fig1_ingest_latency.png`` — measured ingest→adjudication latency per event,
   on a log scale, against the 1 s deadline (C2).
2. ``fig2_level_transition.png`` — claimed vs adjudicated evidence levels for the
   real capability ledger (self-healing view, C4/C5 at platform scale).
3. ``fig3_gate_matrix.png`` — which acceptance gate blocked each slot, so every
   remaining WITHHELD is explainable rather than silent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

GATES = [
    "min_matching",
    "min_sources",
    "provenance_sha256",
    "contradiction_gate",
    "below_target_assertion",
    "metric:error_pct",
    "metric:n_samples",
]


def fig_latency(records: List[Dict[str, Any]], out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.2))
    labels = [r["label"] for r in records]
    values = [max(r["latency_ms"], 1e-3) for r in records]
    ax.bar(range(len(values)), values, color="#2a7fb8")
    ax.set_yscale("log")
    ax.axhline(1000.0, color="#c0392b", linestyle="--", linewidth=1.4,
               label="deadline 1000 ms")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=75, fontsize=6)
    ax.set_ylabel("ingest → re-adjudication latency [ms, log]")
    ax.set_title("Evidence-ingest latency (auto re-judge completed inside the call)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_levels(rows: List[Dict[str, Any]], out: Path) -> Path:
    levels = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
    claimed = {lv: 0 for lv in levels}
    adjudicated = {lv: 0 for lv in levels}
    for row in rows:
        c = (row.get("claimed_level") or "WITHHELD").upper()
        a = row["level"].upper()
        claimed[c] = claimed.get(c, 0) + 1
        adjudicated[a] = adjudicated.get(a, 0) + 1
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    width = 0.38
    xs = range(len(levels))
    ax.bar([x - width / 2 for x in xs], [claimed[lv] for lv in levels], width,
           label="ledger claim", color="#95a5a6")
    ax.bar([x + width / 2 for x in xs], [adjudicated[lv] for lv in levels], width,
           label="pipeline adjudicated", color="#e67e22")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(levels, rotation=20)
    ax.set_ylabel("capability count")
    ax.set_title("Real ledger: claimed vs evidence-adjudicated level")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def fig_gate_matrix(rows: List[Dict[str, Any]], out: Path) -> Path:
    matrix = []
    labels = []
    for row in rows:
        hits = {g: 0 for g in GATES}
        for failure in row.get("failures", []):
            check = failure.get("check", "")
            if check in hits:
                hits[check] += 1
        matrix.append([hits[g] for g in GATES])
        labels.append(row["capability_id"])
    fig, ax = plt.subplots(figsize=(8.6, max(4.0, 0.32 * len(rows) + 1.6)))
    im = ax.imshow(matrix, aspect="auto", cmap="Reds", vmin=0)
    ax.set_xticks(range(len(GATES)))
    ax.set_xticklabels(GATES, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=6)
    ax.set_title("Which acceptance gate blocked the upgrade (failure counts)")
    for i in range(len(labels)):
        for j in range(len(GATES)):
            if matrix[i][j]:
                ax.text(j, i, str(matrix[i][j]), ha="center", va="center", fontsize=6)
    fig.colorbar(im, ax=ax, shrink=0.7, label="failed evaluations")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def build_all(results: Dict[str, Any], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    recs = results["demo"]["events"] + results["ledger"]["events"]
    paths = [
        fig_latency(recs, out_dir / "fig1_ingest_latency.png"),
        fig_levels(results["ledger"]["slots"], out_dir / "fig2_level_transition.png"),
        fig_gate_matrix(results["ledger"]["slots"], out_dir / "fig3_gate_matrix.png"),
    ]
    (out_dir / "figures_manifest.json").write_text(
        json.dumps([p.name for p in paths], indent=2), encoding="utf-8"
    )
    return paths