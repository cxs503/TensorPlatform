"""Benchmark the seeding near-duplicate gate on the labelled pair set.

Measurement protocol
--------------------
Every candidate is adjudicated against the **full ledger** of the pair file
(``N_LEDGER`` nodes, half of them in-flight) with one IDF model built over
ledger + candidates — the production situation, and the only statistically sound
one, because the similarity features are IDF-weighted and a two-document corpus
has degenerate IDF.

Positive class = ``duplicate`` ("must block").  Negatives = ``novel`` (same
wording family **plus** a fresh Δ identifier) and ``unique`` (different topic,
different skeleton, no Δ).

Reported:

``gate``                    similarity + residual-Δ (as shipped)
``gate_dev`` / ``gate_holdout``  the same rule on the fixed generator split
``ablation_similarity_only``     block iff ``score >= theta`` (no Δ check)
``ablation_delta_only``          block iff no Δ declared (ignores score)
``theta_sweep``                  P/R over a threshold grid, dev and holdout
                                 columns, so the shipped theta can be checked to
                                 sit on a plateau instead of on a peak
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ops.taskplane.governance.proposal_dedup import DedupConfig, IdfModel, build_idf  # noqa: E402
from ops.taskplane.governance.seeding_dedup import (  # noqa: E402
    ALLOW,
    BLOCK,
    Candidate,
    Node,
    SeedingConfig,
    adjudicate,
    ledger_seal,
)

PAIRS_SCHEMA = "seeding-pairs/v1"
DEFAULT_PAIRS = "data/seeding_dedup/pairs_100.json"
THETA_GRID: Tuple[float, ...] = (0.20, 0.24, 0.28, 0.30, 0.32, 0.35, 0.40, 0.45, 0.50, 0.60)


def load_pairs(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        payload = json.load(fh)
    if payload.get("schema") != PAIRS_SCHEMA:
        raise ValueError(f"pairs schema {payload.get('schema')!r} != {PAIRS_SCHEMA!r}")
    return payload


def _node(entry: Dict[str, Any]) -> Node:
    return Node(
        entry["node_id"],
        entry["title"],
        entry["goal"],
        entry.get("status", "done"),
        entry.get("deltas") or {},
    )


def _confusion(tp: int, fp: int, tn: int, fn: int) -> Dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def _idf_for(pairs: Sequence[Dict[str, Any]], ledger: Sequence[Node]) -> IdfModel:
    texts = [n.text for n in ledger]
    texts += [f"{p['candidate']['title']}\n{p['candidate']['goal']}" for p in pairs]
    return build_idf(texts)


def _rows(
    pairs: Sequence[Dict[str, Any]],
    ledger: Sequence[Node],
    *,
    cfg: Optional[DedupConfig] = None,
    seeding: Optional[SeedingConfig] = None,
    theta: Optional[float] = None,
    idf: Optional[IdfModel] = None,
) -> List[Dict[str, Any]]:
    cfg = cfg or DedupConfig()
    sc = seeding or SeedingConfig()
    th = sc.theta if theta is None else float(theta)
    model = idf or _idf_for(pairs, ledger)
    out: List[Dict[str, Any]] = []
    for pair in pairs:
        env = adjudicate(pair["candidate"], list(ledger), cfg=cfg, seeding=sc, theta=th, idf=model)
        blocked = env["decision"] == BLOCK
        nearest = env["neighbours"][0] if env.get("neighbours") else None
        out.append(
            {
                "pair_id": pair["pair_id"],
                "label": pair["label"],
                "paraphrase": pair.get("paraphrase", ""),
                "split": pair.get("split", ""),
                "score": nearest["score"] if nearest else 0.0,
                "nearest": nearest["node_id"] if nearest else "",
                "nearest_is_source": bool(nearest and nearest["node_id"] == pair["neighbour"]["node_id"]),
                "theta": round(th, 6),
                "decision": env["decision"],
                "code": env["code"],
                "blocked": blocked,
                "has_delta": any((pair["candidate"].get("deltas") or {}).values()),
                "correct": blocked == (pair["label"] == "duplicate"),
            }
        )
    return out


def _score_rows(rows: Iterable[Dict[str, Any]], theta: float) -> Dict[str, Any]:
    tp = fp = tn = fn = 0
    for r in rows:
        positive = r["label"] == "duplicate"
        pred = r["score"] >= theta
        if positive and pred:
            tp += 1
        elif positive:
            fn += 1
        elif pred:
            fp += 1
        else:
            tn += 1
    return _confusion(tp, fp, tn, fn)


def evaluate(
    pairs_payload: Dict[str, Any],
    *,
    cfg: Optional[DedupConfig] = None,
    seeding: Optional[SeedingConfig] = None,
    theta: Optional[float] = None,
    sweep: Sequence[float] = THETA_GRID,
) -> Dict[str, Any]:
    sc = seeding or SeedingConfig()
    th = sc.theta if theta is None else float(theta)
    pairs = pairs_payload["pairs"]
    ledger = [_node(n) for n in pairs_payload.get("ledger") or []]
    model = _idf_for(pairs, ledger)
    rows = _rows(pairs, ledger, cfg=cfg, seeding=sc, theta=th, idf=model)

    gate = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    sim_only = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    delta_only = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for r in rows:
        positive = r["label"] == "duplicate"
        preds = {
            "gate": r["blocked"],
            "sim": r["score"] >= th,
            "delta": not r["has_delta"],
        }
        for name, pred in preds.items():
            bucket = gate if name == "gate" else sim_only if name == "sim" else delta_only
            if positive and pred:
                bucket["tp"] += 1
            elif positive:
                bucket["fn"] += 1
            elif pred:
                bucket["fp"] += 1
            else:
                bucket["tn"] += 1

    result: Dict[str, Any] = {
        "criteria": {
            "positive_class": "duplicate (must block)",
            "theta": round(th, 6),
            "theta_source": "SeedingConfig.theta (dev-split sweep; see theta_sweep)",
            "n_pairs": len(rows),
            "n_ledger": len(ledger),
            "ledger_seal": ledger_seal(ledger),
            "fail_closed_codes_hit": sorted({r["code"] for r in rows}),
        },
        "n_pairs": len(rows),
        "gate": _confusion(**gate),
        "ablation_similarity_only": _confusion(**sim_only),
        "ablation_delta_only": _confusion(**delta_only),
        "similarity_theta_sweep": [
            {"theta": t,
             "dev": _score_rows([r for r in rows if r["split"] == "dev"], t),
             "holdout": _score_rows([r for r in rows if r["split"] == "holdout"], t),
             "all": _score_rows(rows, t)}
            for t in sweep
        ],
        "rows": rows,
        "errors": [r for r in rows if not r["correct"]],
    }
    # per-split confusion of the *shipped* rule (not the score sweep)
    for split in ("dev", "holdout"):
        sub = [r for r in rows if r["split"] == split]
        if not sub:
            continue
        tp = sum(1 for r in sub if r["label"] == "duplicate" and r["blocked"])
        fn = sum(1 for r in sub if r["label"] == "duplicate" and not r["blocked"])
        fp = sum(1 for r in sub if r["label"] != "duplicate" and r["blocked"])
        tn = sum(1 for r in sub if r["label"] != "duplicate" and not r["blocked"])
        result[f"gate_{split}"] = _confusion(tp, fp, tn, fn)
    # per-class score spread: shows the separation margin the decision rests on
    spread: Dict[str, Any] = {}
    for label in ("duplicate", "novel", "unique"):
        vals = sorted(r["score"] for r in rows if r["label"] == label)
        if vals:
            spread[label] = {"n": len(vals), "min": vals[0], "median": vals[len(vals) // 2], "max": vals[-1]}
    result["score_spread"] = spread
    result["similarity_theta_sweep_note"] = (
        "the sweep scores the *similarity-only* rule (score >= theta, no residual-Δ "
        "check) so that the plateau and the cost of dropping the Δ channel are visible "
        "side by side with the shipped rule's confusion matrix"
    )
    result["generator"] = pairs_payload.get("generator", {})
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="seeding dedup gate benchmark")
    ap.add_argument("--pairs", default=DEFAULT_PAIRS)
    ap.add_argument("--out", default="")
    ap.add_argument("--theta", type=float, default=None)
    args = ap.parse_args(argv)
    path = args.pairs if os.path.isabs(args.pairs) else os.path.join(REPO_ROOT, args.pairs)
    payload = load_pairs(path)
    report = evaluate(payload, theta=args.theta)
    report["pairs_path"] = args.pairs
    if args.out:
        out = args.out if os.path.isabs(args.out) else os.path.join(REPO_ROOT, args.out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
    g = report["gate"]
    print(
        f"[seeding-dedup] gate P={g['precision']:.3f} R={g['recall']:.3f} F1={g['f1']:.3f} "
        f"(tp={g['tp']} fp={g['fp']} tn={g['tn']} fn={g['fn']}) theta={report['criteria']['theta']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())