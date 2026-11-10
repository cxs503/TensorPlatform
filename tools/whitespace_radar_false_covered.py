"""Build the adversarial "*false covered*" probe set for the specificity discipline.

The failure mode
----------------
The radar's existence test is a **union over documents**: a topic is "known" if
each of its discriminative probes occurs *somewhere* in the pool.  A topic that
glues together two rare probes from unrelated documents therefore satisfies the
legacy ``covered`` gate (top BM25 score, probe count and coverage all clear)
even though **no single document is about the combination** — the pool's match is
generic wording plus two lone mentions.  That false ``covered`` is an over-claim:
it blocks a genuinely new combination as if it were prior art.

What this script does
---------------------
It *constructs* that class of topic mechanically and writes it as data, so the
discipline can be measured instead of asserted:

1. harvest 4-character CJK windows from pool **document titles** (title windows
   look like terms, not sentence fragments) whose document frequency sits in
   ``[min_df, rare_frac * N]`` — i.e. rare enough to count as discriminative;
2. pair two windows with **disjoint document support** (no document contains
   both) and append a connector from a fixed generic list;
3. keep the topic only if the **legacy** gate (the pre-discipline rule, spelled
   out in :func:`legacy_covered`) says ``covered``;
4. take the first ``--n`` topics in deterministic order.

Nothing here consults the specificity discipline: the probe set is defined by the
*legacy* rule plus the structural property "no co-occurrence", which is what
makes the measured downgrade rate meaningful.

Everything is domain-neutral code: vocabulary comes from the pool, the connector
list is generic project language, and the thresholds come from ``Thresholds``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from itertools import combinations
from typing import Any, Dict, List, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.whitespace_radar_core import (  # noqa: E402
    Bm25Index,
    GENERIC_ONLY,
    Thresholds,
    analyze,
    apply_pin,
    load_pin,
    load_pool,
    pool_digest,
)
from tools.whitespace_radar_core.bench import FALSE_COVERED_SCHEMA  # noqa: E402
from tools.whitespace_radar_core.tokenize import _CJK_RE  # noqa: E402

POOL_MANIFEST = "data/whitespace_radar/pool/manifest.json"
POOL_PIN = "data/whitespace_radar/pool/pin.json"
#: generic project connectors (no subject-matter vocabulary)
CONNECTORS: Tuple[str, ...] = (
    "协同机制",
    "联动口径",
    "贯通流程",
    "闭环台账",
    "交接排期",
    "复核清单",
)

CONSTRUCTION_RULE = (
    "topic = <rare 4-char window A> <rare 4-char window B> <generic connector>; "
    "A and B are title-derived windows with df/N <= rare_frac and df >= min_df; "
    "A and B have disjoint document support (no pool document contains both); "
    "the topic is kept only if the legacy union-of-probes gate calls it covered"
)


def legacy_covered(
    top_score: float, n_existing_specific: int, specific_coverage: float, th: Thresholds
) -> bool:
    """The pre-discipline ``covered`` gate, spelled out (union over documents)."""
    return (
        top_score >= th.s_covered
        and n_existing_specific >= th.min_existing_probes
        and specific_coverage >= th.c_covered
    )


def harvest_windows(
    index: Bm25Index, th: Thresholds, min_df: int = 5
) -> List[Tuple[str, List[str]]]:
    """Title-derived 4-char windows with rare-but-nonzero document frequency."""
    limit = int(th.rare_frac * max(index.n_docs, 1))
    candidates: Dict[str, set] = {}
    for doc_id in sorted(index.doc_meta):
        title = str(index.doc_meta[doc_id].get("title") or "").lower()
        for run in _CJK_RE.findall(title):
            for i in range(len(run) - 3):
                candidates.setdefault(run[i:i + 4], set()).add(doc_id)
    out: List[Tuple[str, List[str]]] = []
    for window in sorted(candidates):
        docs = index.contains(window)
        if min_df <= len(docs) <= limit:
            out.append((window, docs))
    return out


def build_probe_set(
    index: Bm25Index,
    *,
    th: Thresholds | None = None,
    n: int = 6,
    digest: str = "",
    pin_source: str = "",
) -> Dict[str, Any]:
    th = th or Thresholds()
    windows = harvest_windows(index, th)
    probes: List[Dict[str, Any]] = []
    used: set = set()
    for connector in CONNECTORS:
        if len(probes) >= n:
            break
        for (wa, da), (wb, db) in combinations(windows, 2):
            if wa in used or wb in used or set(da) & set(db):
                continue
            topic = f"{wa} {wb} {connector}"
            env = analyze(index, topic, thresholds=th, pool_digest=digest)
            q = env["query"]
            if not legacy_covered(env["top"]["score"], q["n_existing_specific"], q["specific_coverage"], th):
                continue
            probes.append(
                {
                    "id": f"FC{len(probes) + 1:02d}",
                    "topic": topic,
                    "expected_verdict": GENERIC_ONLY,
                    "connector": connector,
                    "probe_a": {"window": wa, "df": len(da), "docs": da[:3]},
                    "probe_b": {"window": wb, "df": len(db), "docs": db[:3]},
                    "co_occur_docs": 0,
                    "legacy_gate": {
                        "top_score": env["top"]["score"],
                        "n_existing_specific": q["n_existing_specific"],
                        "specific_coverage": q["specific_coverage"],
                    },
                }
            )
            used.update({wa, wb})
            break
    return {
        "schema": FALSE_COVERED_SCHEMA,
        "purpose": (
            "Adversarial probe set for the specificity discipline: topics whose rare "
            "probes all exist in the pool but never in the same document, so the legacy "
            "union-of-probes gate over-claims covered."
        ),
        "construction_rule": CONSTRUCTION_RULE,
        "expected_property": (
            "every probe must be downgraded to generic_only and carry the generic ledger"
        ),
        "pool_digest": digest,
        "pool_pin_source": pin_source,
        "n_probes": len(probes),
        "probes": probes,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pool", default=POOL_MANIFEST)
    ap.add_argument("--pin", default=POOL_PIN)
    ap.add_argument("--out", default="data/whitespace_radar/false_covered_probes.json")
    ap.add_argument("--n", type=int, default=6)
    args = ap.parse_args(argv)

    pin_path = os.path.join(REPO_ROOT, args.pin)
    pin = load_pin(pin_path) if os.path.isfile(pin_path) else None
    docs = load_pool(REPO_ROOT, os.path.join(REPO_ROOT, args.pool), pin=pin)
    index = Bm25Index.build(docs)
    payload = build_probe_set(
        index,
        n=args.n,
        digest=pool_digest(docs),
        pin_source=(pin or {}).get("source", ""),
    )
    out = os.path.join(REPO_ROOT, args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"[false-covered] wrote {payload['n_probes']} probes -> {args.out}", file=sys.stderr)
    for p in payload["probes"]:
        print(f"  {p['id']}: {p['topic']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())