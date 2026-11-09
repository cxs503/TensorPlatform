"""Replay benchmark: fixed topics + fixed labels + a machine-checkable score.

The benchmark file is *data* (``data/whitespace_radar/replay_bench.json``); this
module only knows how to score it.  Labels are frozen in the file with the
evidence that justified them (which pool document covers a ``covered`` topic,
which terms were searched for a ``blank`` one) so that a later reviewer can
audit the label rather than trust it.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from .adjudicate import BLANK, COVERED, LABELS, PARTIAL, Thresholds, analyze
from .index import Bm25Index

BENCH_SCHEMA = "radar-bench/v1"


def load_bench(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        payload = json.load(fh)
    if payload.get("schema") != BENCH_SCHEMA:
        raise ValueError(f"bench schema {payload.get('schema')!r} != {BENCH_SCHEMA!r}")
    return payload


def replay(
    index: Bm25Index,
    bench: Dict[str, Any],
    *,
    thresholds: Optional[Thresholds] = None,
    pool_digest: str = "",
    as_of: str = "",
) -> Dict[str, Any]:
    """Run every benchmark topic through the adjudicator and score it."""
    rows: List[Dict[str, Any]] = []
    confusion = {a: {b: 0 for b in LABELS} for a in LABELS}
    correct = 0
    blank_false_positive = 0
    blank_total = 0
    for item in bench.get("topics") or []:
        env = analyze(
            index,
            item["topic"],
            thresholds=thresholds,
            pool_digest=pool_digest,
            as_of=as_of,
        )
        got = env["verdict"]
        want = item["label"]
        ok = got == want
        correct += int(ok)
        confusion.setdefault(want, {b: 0 for b in LABELS})[got] += 1
        if want == BLANK:
            blank_total += 1
            if got != BLANK:
                blank_false_positive += 1
        rows.append(
            {
                "id": item["id"],
                "topic": item["topic"],
                "label": want,
                "verdict": got,
                "correct": ok,
                "top_score": env["top"]["score"],
                "specific_coverage": env["query"]["specific_coverage"],
                "n_existing_specific": env["query"]["n_existing_specific"],
                "hits": env["hits"]["count"],
                "top_ref": (env["hits"]["docs"][0]["ref"] if env["hits"]["docs"] else None),
                "provenance": item.get("provenance", ""),
                "note": item.get("note", ""),
                "specific_zero_hit_terms": env["zero_hit_proof"]["n_specific"] - env["zero_hit_proof"]["n_existing_specific"],
            }
        )
    total = len(rows)
    return {
        "n_topics": total,
        "n_correct": correct,
        "accuracy": round(correct / total, 6) if total else 0.0,
        "blank_total": blank_total,
        "blank_false_positive": blank_false_positive,
        "confusion": confusion,
        "thresholds": (thresholds or Thresholds()).as_dict(),
        "rows": rows,
        "mismatches": [
            {k: r[k] for k in ("id", "topic", "label", "verdict", "top_score", "specific_coverage")}
            for r in rows
            if not r["correct"]
        ],
    }


def passes_acceptance(report: Dict[str, Any], *, min_correct: int = 16) -> bool:
    """Acceptance: >=16/18 correct *and* zero blank false positives."""
    return (
        report["n_topics"] >= 18
        and report["n_correct"] >= min_correct
        and report["blank_false_positive"] == 0
    )


def calibration_curve(
    index: Bm25Index,
    bench: Dict[str, Any],
    *,
    covered_scores: Sequence[float] = (2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 30.0),
    rare_fracs: Sequence[float] = (0.005, 0.01, 0.02, 0.05),
    pool_digest: str = "",
) -> List[Dict[str, Any]]:
    """Grid over the two thresholds that actually matter; documents the plateau.

    Emitting the whole grid (not just the shipped point) is what turns "we chose
    10.0" into "10.0 sits on a plateau of width W".  A single-point calibration
    would be indistinguishable from overfitting.
    """
    out: List[Dict[str, Any]] = []
    for s in covered_scores:
        for rf in rare_fracs:
            th = Thresholds(s_covered=s, rare_frac=rf)
            rep = replay(index, bench, thresholds=th, pool_digest=pool_digest)
            out.append(
                {
                    "s_covered": s,
                    "rare_frac": rf,
                    "n_correct": rep["n_correct"],
                    "accuracy": rep["accuracy"],
                    "blank_false_positive": rep["blank_false_positive"],
                }
            )
    return out