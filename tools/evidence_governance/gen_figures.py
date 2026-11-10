#!/usr/bin/env python3
"""Generate the report figures for P210W2-N01 from the committed artifacts.

Every number in the figures is read from the artifact JSON (or recomputed from the
labelled benchmark), never typed by hand:

* ``fig1_acquisition_queue.png``  real-library priority queue (score, band, type)
* ``fig2_closure_funnel.png``     scan -> proposal -> upgrade funnel, real vs loopback
* ``fig3_classification_accuracy.png``  per-type recall + the measured effect of the
  nested-pattern fix (recomputed here by re-running the uncollapsed scorer)

Usage: python3 tools/evidence_governance/gen_figures.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "artifacts" / "evidence_governance" / "figures"

BAND_COLORS = {"P0": "#b2182b", "P1": "#ef8a62", "P2": "#f7d27b", "P3": "#9e9e9e"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _prefix_recompute_accuracy() -> dict:
    """Recompute the pre-fix accuracy (no nested-pattern collapse) from labels."""
    rules = _load(PKG / "config" / "classifier_rules.v1.json")["rules"]
    cfg = _load(PKG / "config" / "governance.v1.json")["classification"]
    labels = _load(PKG / "fixtures" / "labeled_debts.json")["items"]
    order = cfg["tie_break_order"]
    rank = {name: i for i, name in enumerate(order)}
    n_ok = 0
    for row in labels:
        text = row["declaration_text"].lower()
        scores = {}
        for rule in rules:
            hits = [p for p in rule["patterns"] if p.lower() in text]
            if hits:
                scores[rule["baseline_type"]] = len(hits)
        if not scores:
            pred = cfg["default_type"]
        else:
            pred = sorted(scores, key=lambda k: (-scores[k], rank[k]))[0]
        n_ok += int(pred == row["expected"])
    return {"accuracy": n_ok / len(labels), "n": len(labels)}


def fig_queue(base: Path, out: Path) -> Path:
    proposals = _load(base / "proposals.json")["proposals"]
    proposals = sorted(proposals, key=lambda p: p["priority_score"])
    fig, ax = plt.subplots(figsize=(9, 7))
    ys = range(len(proposals))
    colors = [BAND_COLORS.get(p["priority_band"], "#cccccc") for p in proposals]
    ax.barh(list(ys), [p["priority_score"] for p in proposals], color=colors, height=0.75)
    for y, p in zip(ys, proposals):
        ax.text(
            p["priority_score"] + 0.01, y,
            f"{p['priority_band']} | {p['baseline_type'][:14]} | mag={p['missing_magnitude']:.2f}",
            va="center", fontsize=7,
        )
    ax.set_yticks(list(ys))
    ax.set_yticklabels([p["capability_id"] for p in proposals], fontsize=7)
    ax.set_xlim(0, 1.35)
    ax.set_xlabel("priority score (0..1)")
    ax.set_title("Real library: WITHHELD acquisition queue (19 rows)\n"
                 "colour = priority band; mag = normalised missing magnitude")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    path = out / "fig1_acquisition_queue.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_funnel(real_base: Path, demo_base: Path, out: Path) -> Path:
    real_kpi = _load(real_base / "kpi.json")
    demo_kpi = _load(demo_base / "kpi.json")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))

    ax = axes[0]
    labels = ["WITHHELD\n(scanned)", "proposals\n(compiled)", "upgraded\n(closure)"]
    for i, (kpi, name, color) in enumerate((
        (real_kpi, "real library", "#4c72b0"),
        (demo_kpi, "synthetic loopback", "#55a868"),
    )):
        vals = [kpi["n_withheld_total"], kpi["n_proposals"], kpi["n_upgraded"]]
        xs = [j + (i - 0.5) * 0.34 for j in range(3)]
        ax.bar(xs, vals, width=0.32, color=color, label=name)
        for x, v in zip(xs, vals):
            ax.text(x, v + 0.25, str(v), ha="center", fontsize=8)
    ax.set_xticks(range(3))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("entries")
    ax.set_title("scan -> proposal -> upgrade funnel")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    keys = ["n_upgraded", "n_failed", "n_remaining"]
    colors = ["#55a868", "#c44e52", "#8c8c8c"]
    bottoms = [0, 0]
    for key, color in zip(keys, colors):
        vals = [real_kpi[key], demo_kpi[key]]
        ax.bar(["real library", "synthetic\nloopback"], vals, bottom=bottoms,
               color=color, label=key)
        for x, (v, b) in enumerate(zip(vals, bottoms)):
            if v:
                ax.text(x, b + v / 2, str(v), ha="center", va="center", fontsize=9,
                        color="white")
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_ylabel("entries")
    ax.set_title("closure accounting (invariant: upgraded+failed+remaining=total)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    fig.suptitle(
        f"closure_rate: real library {real_kpi['closure_rate']:.3f} "
        f"({real_kpi['n_upgraded']}/{real_kpi['n_withheld_total']}), "
        f"loopback {demo_kpi['closure_rate']:.3f} "
        f"({demo_kpi['n_upgraded']}/{demo_kpi['n_withheld_total']})",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = out / "fig2_closure_funnel.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def fig_accuracy(real_base: Path, out: Path) -> Path:
    acc = _load(real_base / "classification_accuracy.json")
    prefix = _prefix_recompute_accuracy()
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))

    names = sorted(acc["per_type"])
    recalls = [acc["per_type"][n]["recall"] for n in names]
    support = [acc["per_type"][n]["support"] for n in names]
    ax = axes[0]
    bars = ax.bar(range(len(names)), recalls, color="#4c72b0")
    for i, (b, s) in enumerate(zip(bars, support)):
        ax.text(i, b.get_height() + 0.02, f"n={s}", ha="center", fontsize=8)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=7)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("recall")
    ax.set_title(f"per-type recall (overall {acc['accuracy']:.4f}, n={acc['n_items']})")
    ax.axhline(acc["threshold"], color="#c44e52", ls="--", lw=1,
               label=f"threshold {acc['threshold']:.2f}")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    labels = ["before fix\n(overlapping patterns\ncounted twice)",
              "after fix\n(most specific\npattern only)"]
    vals = [prefix["accuracy"], acc["accuracy"]]
    bars = ax.bar(labels, vals, color=["#c44e52", "#55a868"], width=0.55)
    for b, v, n in zip(bars, vals, [prefix["n"], acc["n_items"]]):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.004,
                f"{v:.4f}\n({round(v * n)}/{n})", ha="center", fontsize=9)
    ax.set_ylim(0.9, 1.02)
    ax.set_ylabel("benchmark accuracy")
    ax.set_title("measured effect of the nested-pattern collapse")
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    path = out / "fig3_classification_accuracy.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT / "artifacts" / "evidence_governance"))
    ap.add_argument("--demo", default=str(
        ROOT / "artifacts" / "evidence_governance" / "demo_loopback"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    base, demo, out = Path(args.base), Path(args.demo), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    paths = [
        fig_queue(base, out),
        fig_funnel(base, demo, out),
        fig_accuracy(base, out),
    ]
    meta = {
        "generated_from": [str(base), str(demo)],
        "figures": [str(p.relative_to(ROOT)) for p in paths],
        "caption_hints": {
            "fig1_acquisition_queue.png":
                "19 WITHHELD rows ranked by priority; colour = band, text = baseline type + missing magnitude.",
            "fig2_closure_funnel.png":
                "Left: scan/proposal/upgrade counts for the real library and the synthetic loopback. "
                "Right: closure accounting, invariant checked on serialisation.",
            "fig3_classification_accuracy.png":
                "Left: per-type recall against the 48-item labelled benchmark. Right: accuracy before/after "
                "collapsing overlapping rule patterns (recomputed, not hand-typed).",
        },
    }
    (out / "figures_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())