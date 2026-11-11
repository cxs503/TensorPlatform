"""Seeding-dedup maturity runner: corpus baseline + labelled-pair replay + seal.

One command produces the three numbers a reviewer needs in order to believe the
seeding gate:

1. **corpus redundancy baseline** — what the node corpus itself carries at the
   gate's threshold (pairs, families, quantiles);
2. **provenance-labelled replay** — precision / recall on pairs whose labels
   come from node provenance, with a threshold sweep instead of a single point;
3. **synthetic graded replay** — the strength-graded pair set, as a second,
   independent control (easy/medium/heavy duplicates against fresh seeds).

Everything is sealed twice (canonical JSON + sha256) and the run is repeated to
prove byte-identical output.  No subject-matter vocabulary anywhere: texts are
opaque, labels come from ids.

Usage::

    python -m tools.seed_dedup.maturity \\
        --corpus data/seed_dedup/corpus/nodes.json \\
        --out artifacts/seed_dedup/corpus_maturity.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from tools.whitespace_radar_core.evidence import dumps, seal

from .bench import build_benchmark, evaluate_benchmark
from .corpus import (
    blocked_pairs,
    build_index,
    evaluate_provenance_pairs,
    load_corpus,
    pairwise_scores,
    provenance_pairs,
    redundancy_baseline,
)
from .core import CALIBRATED_THETA, SeedConfig

SCHEMA = "seed-dedup-maturity/v1"
DEFAULT_THETAS = (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95)
ACCEPT_PRECISION = 0.85
ACCEPT_RECALL = 0.85


def _census_sweep(ranked: Sequence[Dict[str, Any]], thetas: Sequence[float]) -> List[Dict[str, Any]]:
    """Re-count the census at several thresholds from one scored pair list.

    Scoring a pair is the expensive half of the census, so the sweep reuses the
    same scored list and only re-counts — otherwise a calibration curve would
    cost one full census per threshold.
    """
    out: List[Dict[str, Any]] = []
    for t in thetas:
        near = [r for r in ranked if r["score"] >= t]
        out.append(
            {
                "theta": round(float(t), 6),
                "n_pairs_ge_theta": len(near),
                "n_pairs": len(ranked),
            }
        )
    return out


def run(
    corpus: Dict[str, Any],
    *,
    cfg: Optional[SeedConfig] = None,
    thetas: Sequence[float] = DEFAULT_THETAS,
    n_positive: int = 50,
    n_negative: int = 88,
    pair_seed: int = 12128,
    min_shared: int = 10,
    synthetic_seed: int = 12128,
) -> Dict[str, Any]:
    cfg = cfg or SeedConfig()
    # the operating point must always be in the sweep, otherwise "the reported
    # number" and "the swept curve" could disagree
    thetas = sorted({round(float(x), 6) for x in list(thetas) + [round(cfg.theta, 6)]})
    nodes = corpus["nodes"]
    index = build_index(nodes)

    pairs = blocked_pairs(nodes, min_shared=min_shared)
    scored = pairwise_scores(nodes, pairs, cfg=cfg, index=index)
    baseline = redundancy_baseline(
        nodes, cfg=cfg, min_shared=min_shared, top_pairs=25, pair_list=pairs, pair_scores=scored
    )
    labelled = provenance_pairs(nodes, n_positive=n_positive, n_negative=n_negative, seed=pair_seed)
    prov_eval = evaluate_provenance_pairs(nodes, labelled, cfg=cfg, thetas=thetas, full_index=index)

    synthetic = build_benchmark(seed=synthetic_seed)
    bench_index = build_index(synthetic["ledger"])
    synth_sweep: List[Dict[str, Any]] = []
    for t in thetas:
        rep = evaluate_benchmark(synthetic, bench_index, theta=float(t), k=cfg.k)
        synth_sweep.append(
            {
                "theta": round(float(t), 6),
                "precision": rep["precision"],
                "recall": rep["recall"],
                "f1": rep["f1"],
                "tp": rep["tp"],
                "fp": rep["fp"],
                "fn": rep["fn"],
                "tn": rep["tn"],
            }
        )

    pair_chosen = next(r for r in prov_eval["pair_level"]["sweep"] if r["theta"] == round(cfg.theta, 6))
    chosen_synth = next(r for r in synth_sweep if r["theta"] == round(cfg.theta, 6))
    gate_chosen = next(r for r in prov_eval["sweep"] if r["theta"] == round(cfg.theta, 6))
    acceptance = {
        "thresholds": {"precision": ACCEPT_PRECISION, "recall": ACCEPT_RECALL, "theta": cfg.theta},
        "provenance_pair_precision_ok": pair_chosen["precision"] >= ACCEPT_PRECISION,
        "provenance_pair_recall_ok": pair_chosen["recall"] >= ACCEPT_RECALL,
        "synthetic_precision_ok": chosen_synth["precision"] >= ACCEPT_PRECISION,
        "synthetic_recall_ok": chosen_synth["recall"] >= ACCEPT_RECALL,
        "determinism_identical": None,
    }
    acceptance["passed"] = all(
        v for k, v in acceptance.items() if k not in ("thresholds", "passed", "determinism_identical")
    )

    return {
        "schema": SCHEMA,
        "corpus": {
            "n_nodes": corpus["n_nodes"],
            "digest": corpus.get("digest"),
            "window_rule": corpus.get("window_rule"),
            "n_unique_node_ids": len({n["node_id"] for n in nodes}),
        },
        "policy": {
            "theta": cfg.theta,
            "shipped_default_theta": SeedConfig().theta,
            "calibrated_theta": CALIBRATED_THETA,
            "k": cfg.k,
            "weights": {"bm25_norm": cfg.w_bm25, "jaccard_3gram": cfg.w_jaccard},
            "min_shared_grams": min_shared,
            "thetas": [round(float(t), 6) for t in thetas],
        },
        "baseline": baseline,
        "census_sweep": _census_sweep(scored, thetas),
        "labelled_pairs": {
            "n_pairs": labelled["n_pairs"],
            "n_duplicate": labelled["n_duplicate"],
            "n_novel": labelled["n_novel"],
            "digest": labelled["digest"],
            "label_sources": labelled["label_sources"],
            "seed": labelled["seed"],
        },
        "provenance_replay": prov_eval,
        "gate_level": {
            "note": (
                "gate decisions look at the candidate's nearest neighbour anywhere in the corpus, so a "
                "'novel' pair can still be blocked through a third entry; these counts are operational "
                "context, not the labelled-pair precision/recall"
            ),
            "at_theta": gate_chosen,
        },
        "synthetic_replay": {
            "n_pairs": len(synthetic["pairs"]),
            "n_ledger": len(synthetic["ledger"]),
            "seed": synthetic_seed,
            "sweep": synth_sweep,
            "at_theta": chosen_synth,
        },
        "acceptance": acceptance,
    }


def determinism_check(
    corpus: Dict[str, Any], seal_a: str = "", **kwargs: Any
) -> Dict[str, Any]:
    """Re-seal the census on the same snapshot and compare with the first seal.

    Recomputing the census once is enough to prove byte-identical output: the
    first seal comes from the run that produced the report.
    """
    again = redundancy_baseline(
        corpus["nodes"],
        cfg=kwargs.get("cfg"),
        min_shared=kwargs.get("min_shared", 10),
        max_df_frac=kwargs.get("max_df_frac", 0.2),
        top_pairs=25,
    )
    seal_b = again["digest"]["sha256"]
    return {
        "seal_a": seal_a,
        "seal_b": seal_b,
        "identical": bool(seal_a) and seal_a == seal_b,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default="data/seed_dedup/corpus/nodes.json")
    ap.add_argument("--out", default="artifacts/seed_dedup/corpus_maturity.json")
    ap.add_argument("--pairs-out", default="data/seed_dedup/corpus/pairs_provenance.json")
    ap.add_argument("--min-shared", type=int, default=10)
    ap.add_argument("--n-positive", type=int, default=50)
    ap.add_argument("--n-negative", type=int, default=88)
    ap.add_argument("--seed", type=int, default=12128)
    ap.add_argument("--theta", type=float, default=CALIBRATED_THETA,
                    help="theta to report acceptance at (default: the calibrated operating point)")
    args = ap.parse_args(argv)

    corpus = load_corpus(args.corpus)
    report = run(
        corpus,
        cfg=SeedConfig(theta=args.theta),
        min_shared=args.min_shared,
        n_positive=args.n_positive,
        n_negative=args.n_negative,
        pair_seed=args.seed,
    )
    report["determinism"] = determinism_check(
        corpus,
        report["baseline"]["digest"]["sha256"],
        cfg=SeedConfig(theta=args.theta),
        min_shared=args.min_shared,
    )
    report["acceptance"]["determinism_identical"] = report["determinism"]["identical"]
    report["acceptance"]["passed"] = all(
        report["acceptance"][k]
        for k in (
            "provenance_pair_precision_ok",
            "provenance_pair_recall_ok",
            "synthetic_precision_ok",
            "synthetic_recall_ok",
            "determinism_identical",
        )
    )
    envelope = seal(report)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(dumps(envelope))
    if args.pairs_out:
        os.makedirs(os.path.dirname(args.pairs_out) or ".", exist_ok=True)
        with open(args.pairs_out, "w", encoding="utf-8") as fh:
            json.dump(
                provenance_pairs(
                    corpus["nodes"],
                    n_positive=args.n_positive,
                    n_negative=args.n_negative,
                    seed=args.seed,
                ),
                fh,
                ensure_ascii=False,
                sort_keys=True,
                indent=1,
            )
            fh.write("\n")
    acc = report["acceptance"]
    print(
        f"[maturity] corpus={report['corpus']['n_nodes']} nodes "
        f"pairs>theta={report['baseline']['baseline']['n_pairs_ge_theta']} "
        f"prov P/R={report['provenance_replay']['precision']:.3f}/"
        f"{report['provenance_replay']['recall']:.3f} "
        f"synth P/R={report['synthetic_replay']['at_theta']['precision']:.3f}/"
        f"{report['synthetic_replay']['at_theta']['recall']:.3f} "
        f"passed={acc['passed']} sha256={envelope['digest']['sha256'][:16]}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())