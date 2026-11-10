"""报告图（matplotlib，无交互后端；每图落 png + sha256 入报告）。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .ladder import LADDER  # noqa: E402

SHORT = {
    "not_supported": "L0 none",
    "implemented": "L1 impl",
    "tested": "L2 tested",
    "benchmark_verified": "L3 bench",
    "coupled_prototype": "L4 coupled",
    "engineering_qualified": "L5 qual",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(fig, path: Path) -> Dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return {"name": path.name, "path": str(path), "sha256": _sha(path)}


def fig_golden_confusion(golden_report: Mapping[str, Any], out_dir: Path) -> Dict[str, str]:
    labels = list(LADDER)
    mat = np.zeros((len(labels), len(labels)), dtype=int)
    conf = golden_report["confusion"]
    for i, exp in enumerate(labels):
        for j, pred in enumerate(labels):
            mat[i, j] = int((conf.get(exp) or {}).get(pred, 0))
    fig, ax = plt.subplots(figsize=(7.2, 5.6))
    im = ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels([SHORT[l] for l in labels], rotation=35, ha="right", fontsize=8)
    ax.set_yticklabels([SHORT[l] for l in labels], fontsize=8)
    ax.set_xlabel("inferred level (engine)")
    ax.set_ylabel("frozen human label")
    for i in range(len(labels)):
        for j in range(len(labels)):
            if mat[i, j]:
                ax.text(j, i, str(mat[i, j]), ha="center", va="center", fontsize=9,
                        color="white" if mat[i, j] > mat.max() / 2 else "black")
    naive = golden_report.get("naive_baseline") or {}
    ax.set_title(
        f"Golden set n={golden_report['total']}  acc={golden_report['accuracy']:.3f}  "
        f"macro-F1={golden_report['macro_f1']:.3f}\n"
        f"naive max-rung baseline acc={naive.get('accuracy', float('nan')):.3f} "
        f"(fail-closed gain {golden_report['accuracy'] - naive.get('accuracy', 0):+.3f})",
        fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    return _save(fig, out_dir / "fig1_golden_confusion.png")


def fig_level_distribution(real_levels: Mapping[str, Any], out_dir: Path) -> Dict[str, str]:
    rows = real_levels["rows"]
    labels = list(LADDER)
    claimed_rank = {"NOT_SUPPORTED": 0, "STUB": 0, "WITHHELD": 1, "MOCK": 1, "REAL": 3}
    claimed_counts = [0] * len(labels)
    inferred_counts = [0] * len(labels)
    for r in rows:
        inferred_counts[r["inferred_rank"]] += 1
        claimed_counts[claimed_rank.get((r.get("claimed_level") or "").upper(), 0)] += 1
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    ax.bar(x - 0.2, claimed_counts, width=0.4, label="ledger claim (equivalised)", color="#90a4ae")
    ax.bar(x + 0.2, inferred_counts, width=0.4, label="inferred (fail-closed)", color="#1565c0")
    for i, (a, b) in enumerate(zip(claimed_counts, inferred_counts)):
        ax.text(i - 0.2, a + 0.15, str(a), ha="center", fontsize=8)
        ax.text(i + 0.2, b + 0.15, str(b), ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([SHORT[l] for l in labels], fontsize=9)
    ax.set_ylabel("capabilities")
    ax.set_title(f"Real ledger n={len(rows)}: claim vs inference "
                 f"(claimed REAL {real_levels['summary']['claimed_real']} → "
                 f"kept REAL {real_levels['summary']['kept_real']})", fontsize=9)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, out_dir / "fig2_level_distribution.png")


def fig_rung_matrix(real_levels: Mapping[str, Any], out_dir: Path) -> Dict[str, str]:
    rows = real_levels["rows"]
    rungs = list(LADDER[1:])
    mat = np.zeros((len(rows), len(rungs)))
    for i, r in enumerate(rows):
        support = {s["rung"]: (1 if s["supported"] else 0) for s in r["rung_support"]}
        for j, rung in enumerate(rungs):
            mat[i, j] = support.get(rung, 0)
    fig, ax = plt.subplots(figsize=(6.4, max(4.0, 0.28 * len(rows) + 1.4)))
    ax.imshow(mat, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(rungs)))
    ax.set_xticklabels([SHORT[r] for r in rungs], rotation=25, ha="right", fontsize=8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["capability_key"] for r in rows], fontsize=6)
    ax.set_title("Rung support matrix (green = evidence passes, red = fail-closed break)",
                 fontsize=9)
    for i in range(len(rows)):
        for j in range(len(rungs)):
            ax.text(j, i, "1" if mat[i, j] else "0", ha="center", va="center", fontsize=6)
    return _save(fig, out_dir / "fig3_rung_support_matrix.png")


def fig_migration(migration_plan: Mapping[str, Any], migrated: Mapping[str, Any],
                  negatives: Mapping[str, Any], out_dir: Path) -> Dict[str, str]:
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.8))
    ax = axes[0]
    before = int(negatives["b"]["live_node_rows"])
    rejected = int(negatives["b"]["rejected_count"])
    after = int(migrated.get("node_prefix_remaining", 0))
    ax.bar(["node.* rows\nbefore gate", "rejected by\nnamespace gate", "node.* rows\nafter migration"],
           [before, rejected, after], color=["#607d8b", "#c62828", "#2e7d32"])
    for i, v in enumerate([before, rejected, after]):
        ax.text(i, v + 0.05, str(v), ha="center", fontsize=9)
    ax.set_ylabel("rows")
    ax.set_title("node.* journal rows: gate + migration", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    keys = [e["old_key"] for e in migration_plan.get("entries", [])]
    targets = [f"{e['target_store']}\n→ {e['target_key']}" for e in migration_plan.get("entries", [])]
    ax.barh(range(len(keys)), [1] * len(keys), color="#1565c0")
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels(keys, fontsize=7)
    for i, t in enumerate(targets):
        ax.text(0.02, i, t, va="center", fontsize=6, color="white")
    ax.set_xticks([])
    ax.set_title("migration plan: capability ledger → campaign node registry", fontsize=9)
    return _save(fig, out_dir / "fig4_node_row_migration.png")


def build_all(golden_report: Mapping[str, Any], real_levels: Mapping[str, Any],
              migration_plan: Mapping[str, Any], migrated: Mapping[str, Any],
              negative_controls: Mapping[str, Any], out_dir: Path) -> List[Dict[str, str]]:
    out_dir = Path(out_dir)
    figs = [
        fig_golden_confusion(golden_report, out_dir),
        fig_level_distribution(real_levels, out_dir),
        fig_rung_matrix(real_levels, out_dir),
        fig_migration(migration_plan, migrated, negative_controls, out_dir),
    ]
    return figs