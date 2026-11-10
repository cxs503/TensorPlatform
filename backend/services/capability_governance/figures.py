"""Figures for the capability-governance acceptance run (all numbers from artifacts)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def _load(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def figure_backtest(out_dir: Path) -> Optional[Dict[str, str]]:
    data = _load(out_dir / "backtest.json")
    if not data:
        return None
    metrics = data["metrics"]
    ablation = data["single_gate_ablation"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    labels = ["accuracy", "false_positive_rate"]
    xs = range(len(labels))
    width = 0.36
    pipeline_vals = [metrics["accuracy"], metrics["false_positive_rate"]]
    baseline_vals = [ablation["accuracy"], ablation["false_positive_rate"]]
    axes[0].bar([x - width / 2 for x in xs], pipeline_vals, width,
                label="four-gate mechanism", color="#2b6cb0")
    axes[0].bar([x + width / 2 for x in xs], baseline_vals, width,
                label="single numeric gate (ablation)", color="#c05621")
    axes[0].set_xticks(list(xs))
    axes[0].set_xticklabels(labels)
    axes[0].axhline(0.05, color="#2f855a", linestyle="--", linewidth=1)
    axes[0].text(len(labels) - 0.5, 0.07, "FPR ceiling 5%", fontsize=8, color="#2f855a",
                 ha="right")
    axes[0].set_ylim(0, 1.05)
    axes[0].set_ylabel("value")
    axes[0].set_title(f"Frozen set ({metrics['n_cases']} endpoints): "
                      f"acc={metrics['accuracy']:.3f}, FPR={metrics['false_positive_rate']:.3f}")
    axes[0].legend(fontsize=8, loc="center right")

    confusion = metrics["confusion"]
    expected = sorted(confusion)
    observed = sorted({k for row in confusion.values() for k in row})
    grid = [[confusion.get(e, {}).get(o, 0) for o in observed] for e in expected]
    im = axes[1].imshow(grid, cmap="Blues")
    axes[1].set_xticks(range(len(observed)))
    axes[1].set_xticklabels(observed, rotation=30, ha="right", fontsize=8)
    axes[1].set_yticks(range(len(expected)))
    axes[1].set_yticklabels(expected, fontsize=8)
    axes[1].set_xlabel("predicted (diagnostic sub-level)")
    axes[1].set_ylabel("labelled")
    axes[1].set_title("Confusion (decision is REAL / not-REAL)")
    for i, row in enumerate(grid):
        for j, val in enumerate(row):
            axes[1].text(j, i, str(val), ha="center", va="center",
                         color="white" if val > max(max(r) for r in grid) / 2 else "black",
                         fontsize=9)
    fig.colorbar(im, ax=axes[1], fraction=0.046, pad=0.04)
    fig.tight_layout()
    path = out_dir / "fig_backtest.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return {"name": "fig_backtest", "path": str(path),
            "caption": "Frozen-set replay: four-gate mechanism vs single numeric gate; "
                       "confusion of diagnostic sub-levels."}


def figure_attribution(out_dir: Path) -> Optional[Dict[str, str]]:
    data = _load(out_dir / "attribution.json")
    if not data:
        return None
    checks = data["checks"]
    dims = ["format", "output", "input_definition", "discretisation"]
    colours = {"format": "#2b6cb0", "output": "#2f855a", "input_definition": "#b7791f",
               "discretisation": "#6b46c1", "residual": "#c53030"}
    fig, ax = plt.subplots(figsize=(10, 4.6))
    left = [0.0] * len(checks)
    for dim in dims:
        vals = [c["contributions"].get(dim, 0.0) for c in checks]
        ax.barh([c["case_id"] for c in checks], vals, left=left, color=colours[dim], label=dim)
        left = [l + v for l, v in zip(left, vals)]
    residuals = [c["residual_share"] for c in checks]
    ax.barh([c["case_id"] for c in checks], residuals, left=left, color=colours["residual"],
            label="unexplained residual")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("share of the raw relative deviation (telescoping: shares + residual = 1)")
    ax.set_title("Four-dimension deviation attribution chain "
                 f"(completeness {data['attribution_completeness']:.3f})")
    ax.legend(fontsize=8, ncol=5, loc="lower center", bbox_to_anchor=(0.5, -0.32))
    fig.tight_layout()
    path = out_dir / "fig_attribution.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return {"name": "fig_attribution", "path": str(path),
            "caption": "Per-case attribution shares; the last case is an unmodelled 3% "
                       "difference and is refused attribution (residual = 1)."}


def figure_samples(out_dir: Path) -> Optional[Dict[str, str]]:
    data = _load(out_dir / "samples" / "sample_audit.json")
    if not data:
        return None
    fam_labels = data["family_labels"]
    cls_labels = data["class_labels"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    fam_items = sorted(fam_labels.items())
    cls_items = sorted(cls_labels.items())
    axes[0].bar([k for k, _ in fam_items], [data["families"][k] for k, _ in fam_items],
                color="#2b6cb0")
    axes[0].axhline(data["min_per_family"], color="#2f855a", linestyle="--", linewidth=1)
    axes[0].set_title("rows per family axis")
    axes[0].tick_params(axis="x", labelsize=8, rotation=15)
    axes[1].bar([k for k, _ in cls_items], [data["case_classes"][k] for k, _ in cls_items],
                color="#b7791f")
    axes[1].axhline(data["min_per_class"], color="#2f855a", linestyle="--", linewidth=1)
    axes[1].set_title("rows per class axis")
    axes[1].tick_params(axis="x", labelsize=8, rotation=15)
    ratios = {
        "rows/min_rows": data["n_rows"] / max(1, 10000),
        "provenance": data["provenance_completeness"],
        "unique digests": data["dedupe_unique_rate"],
        "non-empty output": data["output_non_empty_ratio"],
    }
    axes[2].barh(list(ratios), list(ratios.values()), color="#2f855a")
    axes[2].set_xlim(0, 1.15)
    axes[2].axvline(0.95, color="#c53030", linestyle="--", linewidth=1)
    axes[2].set_title(f"corpus quality (n_rows={data['n_rows']})")
    for i, (k, v) in enumerate(ratios.items()):
        axes[2].text(min(v, 1.1), i, f"{v:.3f}", va="center", fontsize=8)
    fig.tight_layout()
    path = out_dir / "fig_samples.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return {"name": "fig_samples", "path": str(path),
            "caption": "Structured sample corpus coverage per axis and corpus-quality ratios."}


def render_all(out_dir: Path) -> List[Dict[str, str]]:
    figures = []
    for fn in (figure_backtest, figure_attribution, figure_samples):
        fig = fn(out_dir)
        if fig:
            figures.append(fig)
    return figures