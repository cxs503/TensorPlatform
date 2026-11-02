#!/usr/bin/env python3
"""Render the G1-G3 boundary-gate baseline figure from the JSON baseline.

Reads ``artifacts/boundary_gates/baseline.json`` (produced by
``tools/audit/boundary_gates.py``) and draws a two-panel figure:

  left  : per-gate violation inventory (counts + status colour)
  right : G3 context triage (executable code / string literal / comment)

Every number comes from the JSON artefact — nothing is transcribed by hand, so
the figure cannot drift from the baseline.  Missing matplotlib degrades to a
clear message instead of a stack trace.

Usage:
    python tools/audit/boundary_gates_figure.py \
        --json artifacts/boundary_gates/baseline.json \
        --out artifacts/boundary_gates/baseline_figure.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _short_name(gate_id: str) -> str:
    return gate_id.split("_", 1)[0]


def render(baseline: Dict[str, Any], out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    gates: Dict[str, Any] = baseline["gates"]
    ids = list(gates)
    counts = [
        0 if gates[g]["count"] is None else int(gates[g]["count"]) for g in ids
    ]
    colours = [
        {"red": "#c0392b", "green": "#27ae60"}.get(gates[g]["status"], "#7f8c8d")
        for g in ids
    ]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), gridspec_kw={"width_ratios": [1.05, 1]})

    ax = axes[0]
    bars = ax.bar([_short_name(g) for g in ids], counts, color=colours, width=0.55)
    for bar, gate_id in zip(bars, ids):
        value = gates[gate_id]["count"]
        label = "WITHHELD" if value is None else str(value)
        ax.annotate(
            label,
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            textcoords="offset points",
            xytext=(0, 4),
            ha="center",
            fontsize=11,
            fontweight="bold",
        )
        ax.annotate(
            gates[gate_id]["status"].upper(),
            (bar.get_x() + bar.get_width() / 2, 0),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            fontsize=8,
            color="white",
            rotation=90,
        )
    ax.set_ylabel("violations (count)")
    ax.set_title(
        "G1-G3 boundary gate inventory\n"
        f"total violations = {baseline['totals']['violations']} "
        f"({baseline['totals']['gates_red']} red / {baseline['totals']['gates_green']} green)",
        fontsize=10,
    )
    ax.set_ylim(0, max(counts + [1]) * 1.25)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    g3 = gates.get("G3_constant_hoisting", {})
    by_context = g3.get("by_context") or {}
    order = ["code", "string", "comment"]
    labels = [k for k in order if k in by_context] + [
        k for k in by_context if k not in order
    ]
    values = [by_context[k] for k in labels]
    palette = {"code": "#c0392b", "string": "#f39c12", "comment": "#7f8c8d"}
    bars = ax.barh(
        labels[::-1],
        values[::-1],
        color=[palette.get(k, "#95a5a6") for k in labels[::-1]],
    )
    for bar, value in zip(bars, values[::-1]):
        ax.annotate(
            str(value),
            (bar.get_width(), bar.get_y() + bar.get_height() / 2),
            textcoords="offset points",
            xytext=(4, 0),
            va="center",
            fontsize=10,
            fontweight="bold",
        )
    ax.set_xlabel("hits")
    ax.set_title(
        "G3 context triage\n"
        f"total = {0 if g3.get('count') is None else g3.get('count')} hits, "
        f"only 'code' is an executable constant leak",
        fontsize=10,
    )
    ax.set_xlim(0, max(values + [1]) * 1.2)
    ax.grid(axis="x", alpha=0.3)

    fig.suptitle(
        "TensorPlatform domain-boundary gates (baseline snapshot, non-blocking)",
        fontsize=12,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="artifacts/boundary_gates/baseline.json")
    parser.add_argument("--out", default="artifacts/boundary_gates/baseline_figure.png")
    args = parser.parse_args(argv)
    baseline = json.loads(Path(args.json).read_text(encoding="utf-8"))
    out = render(baseline, Path(args.out))
    print(f"[boundary_gates_figure] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())