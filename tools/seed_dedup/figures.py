#!/usr/bin/env python3
"""Figures for the seeding-dedup / specificity-gate maturity report.

Every number plotted comes from a sealed artifact (never typed by hand):

* ``artifacts/seed_dedup/corpus_maturity.json`` — corpus redundancy census +
  threshold sweeps on the provenance-labelled and synthetic pair sets;
* ``artifacts/whitespace_radar/gate_replay.json`` — the 6 adversarial
  "false covered" probes with their witness counts.

    python tools/seed_dedup/figures.py --out-dir docs/figures/p33w3_n12
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MATURITY = os.path.join(REPO_ROOT, "artifacts/seed_dedup/corpus_maturity.json")
GATE = os.path.join(REPO_ROOT, "artifacts/whitespace_radar/gate_replay.json")


def _load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fig_score_distribution(maturity: Dict[str, Any], out: str) -> str:
    rep = maturity["report"] if "report" in maturity else maturity
    rows = rep["baseline"]["top_pairs"]
    quant = rep["baseline"]["baseline"]["score_quantiles_over_blocked_pairs"]
    theta = rep["policy"]["theta"]
    scores = [r["score"] for r in rows]

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    if scores:
        ax.hist(scores, bins=min(30, max(6, len(scores))), color="#4c72b0", alpha=0.85)
    ax.axvline(theta, color="#c44e52", linewidth=2, label=f"gate threshold theta={theta}")
    ax.set_xlabel("dual-channel similarity (BM25 0.55 + char-3gram Jaccard 0.45)")
    ax.set_ylabel("pairs (top-ranked census rows)")
    ax.set_title(
        "Corpus redundancy census\n"
        f"N={rep['corpus']['n_nodes']} nodes, quantiles p50={quant['p50']:.3f} "
        f"p90={quant['p90']:.3f} max={quant['max']:.3f}"
    )
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_threshold_sweep(maturity: Dict[str, Any], out: str) -> str:
    rep = maturity["report"] if "report" in maturity else maturity
    prov = rep["provenance_replay"]["sweep"]
    synth = rep["synthetic_replay"]["sweep"]
    theta = rep["policy"]["theta"]

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    xs = [r["theta"] for r in prov]
    ax.plot(xs, [r["precision"] for r in prov], "o-", color="#4c72b0", label="provenance-labelled precision")
    ax.plot(xs, [r["recall"] for r in prov], "s-", color="#55a868", label="provenance-labelled recall")
    ax.plot([r["theta"] for r in synth], [r["precision"] for r in synth], "o--", color="#8172b2",
            label="synthetic graded precision")
    ax.plot([r["theta"] for r in synth], [r["recall"] for r in synth], "s--", color="#937860",
            label="synthetic graded recall")
    ax.axhline(0.85, color="#c44e52", linestyle=":", linewidth=1.5, label="acceptance 0.85")
    ax.axvline(theta, color="#c44e52", linewidth=1.2, alpha=0.6, label=f"shipped theta={theta}")
    ax.set_xlabel("near-duplicate threshold theta")
    ax.set_ylabel("metric value")
    ax.set_ylim(0.0, 1.05)
    ax.set_title(
        "Seeding gate discrimination vs threshold\n"
        f"provenance pairs n={rep['provenance_replay']['n_pairs']}, "
        f"synthetic pairs n={rep['synthetic_replay']['n_pairs']}"
    )
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def fig_gate_teeth(gate: Dict[str, Any], out: str) -> str:
    rep = gate["report"] if "report" in gate else gate
    rows = [r for r in rep["gate_rows"] if r["set"] == "false_covered"]
    ids = [r["id"] for r in rows]
    hits = [r["specific_hits"] or 0 for r in rows]
    witness = [r["witness_probes"] or 0 for r in rows]
    required = rep["thresholds"]["min_existing_probes"]

    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    xs = range(len(ids))
    ax.bar([x - 0.2 for x in xs], hits, width=0.4, color="#4c72b0", label="existing specific probes")
    ax.bar([x + 0.2 for x in xs], witness, width=0.4, color="#dd8452",
           label="probes carried by one witness document")
    ax.axhline(required, color="#c44e52", linestyle="--", linewidth=1.5,
               label=f"covered needs >= {required} from one witness")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(ids)
    ax.set_ylabel("probe count")
    ax.set_title(
        "Specificity gate on adversarial 'false covered' probes\n"
        f"{rep['false_covered']['n_detected']}/{rep['false_covered']['n_probes']} capped to generic_only"
    )
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25, axis="y")
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default="docs/figures/p33w3_n12")
    args = ap.parse_args(argv)
    out_dir = os.path.join(REPO_ROOT, args.out_dir) if not os.path.isabs(args.out_dir) else args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    made: List[str] = []
    if os.path.isfile(MATURITY):
        maturity = _load(MATURITY)
        made.append(fig_score_distribution(maturity, os.path.join(out_dir, "corpus_redundancy.png")))
        made.append(fig_threshold_sweep(maturity, os.path.join(out_dir, "threshold_sweep.png")))
    if os.path.isfile(GATE):
        made.append(fig_gate_teeth(_load(GATE), os.path.join(out_dir, "gate_false_covered.png")))
    print("\n".join(made))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())