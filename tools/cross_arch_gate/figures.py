"""Figures for the cross-architecture gate report.

Three panels, each answering a question the numeric tables cannot:

1. **band plot** — every observable field of every arm pair on one log axis, with
   the declared relative-tolerance line drawn in.  It shows at a glance that the
   measured spread is meters below the gate, and how far away the gate actually
   is from the noise floor (that distance is the real content of a tolerance).
2. **mutation confusion matrix** — injected class vs judged class.  A judge that
   is only "usually" right shows up immediately as off-diagonal mass.
3. **criteria dashboard** — the six machine-checkable criteria as bars against
   their thresholds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                        # noqa: E402

from . import classify, fixtures         # noqa: E402

FIGSIZE = (7.6, 4.4)
DPI = 150


def _finish(fig, path: Path) -> Path:
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


def band_plot(pairs: Sequence[Mapping[str, Any]], policy_rel: float, path: Path) -> Path:
    labels: List[str] = []
    series: List[List[float]] = []
    for report in pairs:
        rels = [max(r["rel"], 1e-18) for r in report["rows"] if r.get("kind") == fixtures.FP]
        labels.append(report["candidate"]["arm"])
        series.append(rels)
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for label, rels in zip(labels, series):
        ax.plot(np.arange(len(rels)) + 1, rels, marker=".", linestyle="none", label=label, alpha=0.85)
    ax.axhline(policy_rel, color="crimson", linestyle="--", linewidth=1.4,
               label=f"relative tolerance {policy_rel:g}")
    ax.set_yscale("log")
    ax.set_xlabel("observable field index (sorted by name)")
    ax.set_ylabel("relative difference vs reference arm")
    ax.set_title("Cross-architecture field differences against the tolerance band")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=7, ncol=2)
    return _finish(fig, path)


def mutation_matrix(mutation: Mapping[str, Any], path: Path) -> Path:
    expected = [classify.BIT_IDENTICAL, classify.WITHIN_TOLERANCE,
                classify.OVER_TOLERANCE, classify.NAN, "any"]
    judged = list(classify.LABELS) + ["missed"]
    grid = np.zeros((len(expected), len(judged)))
    for case in mutation["cases"]:
        counts = case["class_counts"]
        row = expected.index(case["expected_class"]) if case["expected_class"] in expected else expected.index("any")
        for col, label in enumerate(classify.LABELS):
            grid[row, col] += counts.get(label, 0)
        missed = sum(counts.values()) - sum(counts.get(lbl, 0) for lbl in classify.LABELS)
        grid[row, judged.index("missed")] += missed
    row_sums = grid.sum(axis=1, keepdims=True)
    normalised = np.divide(grid, row_sums, out=np.zeros_like(grid), where=row_sums > 0)
    fig, ax = plt.subplots(figsize=FIGSIZE)
    image = ax.imshow(normalised, cmap="Blues", vmin=0.0, vmax=1.0)
    ax.set_xticks(range(len(judged)), judged, rotation=20, fontsize=7)
    ax.set_yticks(range(len(expected)), [f"injected: {e}" for e in expected], fontsize=7)
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            if grid[i, j]:
                ax.text(j, i, f"{int(grid[i, j])}", ha="center", va="center", fontsize=7,
                        color="black" if normalised[i, j] < 0.6 else "white")
    ax.set_title("Judge confusion matrix (counts inside cells, row-normalised colour)")
    fig.colorbar(image, ax=ax, label="fraction of row")
    return _finish(fig, path)


def criteria_dashboard(criteria_report: Mapping[str, Any], path: Path) -> Path:
    items = criteria_report["criteria"]
    names = list(items)
    values = [items[n]["value"] for n in names]
    thresholds = [items[n]["threshold"] for n in names]
    colours = ["#2a7f62" if items[n]["pass"] else "#b5342b" for n in names]
    fig, ax = plt.subplots(figsize=(7.6, 4.8))
    positions = np.arange(len(names))
    ax.bar(positions, [1.0] * len(names), color=colours, alpha=0.85)
    for pos, name in enumerate(names):
        value = values[pos]
        threshold = thresholds[pos]
        ax.text(pos, 0.5, f"value\n{value:g}\nthreshold {threshold:g}", ha="center", va="center",
                fontsize=6.5, wrap=True)
    ax.set_xticks(positions, [n.replace("_", "\n", 1) for n in names], fontsize=6.5)
    ax.set_yticks([])
    ax.set_title(f"Machine-checkable criteria — all_pass = {criteria_report['all_pass']}")
    return _finish(fig, path)


def make_all(outdir: Path, records: Mapping[str, Any], pairs: Sequence[Mapping[str, Any]],
             mutation: Mapping[str, Any], criteria_report: Mapping[str, Any]) -> List[Path]:
    outdir = Path(outdir) / "figures"
    rel_tol = float(criteria_report["policy"].get("fp64_rel_tol", 1e-10))
    paths = [
        band_plot(pairs, rel_tol, outdir / "fig1_band.png"),
        mutation_matrix(mutation, outdir / "fig2_confusion.png"),
        criteria_dashboard(criteria_report, outdir / "fig3_criteria.png"),
    ]
    (Path(outdir) / "index.json").write_text(json.dumps([str(p) for p in paths], indent=2) + "\n",
                                             encoding="utf-8")
    return paths