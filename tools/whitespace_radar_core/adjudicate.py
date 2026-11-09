"""Tri-state adjudicator: ``covered | partial | blank`` with a zero-hit proof.

Two independent pieces of evidence per topic
--------------------------------------------
1. **Existence probes (literal).**  A topic is cut into *probes*: CJK character
   windows (``window`` chars, ``stride`` step) and latin tokens (``len >= 3``).
   A probe "exists" if some pool document contains it *literally* (substring for
   CJK, token match for latin).  Literal containment is what makes a blank
   verdict greppable: "these 7 windows, listed verbatim, occur in 0 of 1877
   documents".
2. **Strength (BM25).**  The 2-gram BM25 score says *how much* of the topic the
   pool talks about and ranks the candidate documents a reviewer should read.

Why not n-gram document frequency as the existence test?
-------------------------------------------------------
Because incidental 2-grams ("播数", "法并") appear in unrelated documents and
make a genuinely new topic look "partly covered".  A 4-character literal window
is long enough that a hit is shared substance rather than noise.

Decision procedure
------------------
``specific probe`` = probe with ``df/N <= rare_frac`` (rare *or absent*).

1. no specific probe at all        -> **partial** ("query has no discriminative
   content in this pool: refusing to rule blank")
2. no specific probe exists        -> **blank** — the proof carries every probe,
   its df, and the executed window/stride/tokenisation parameters
3. ``top_score >= s_covered`` and ``# existing specific probes >=
   min_existing_probes`` and ``specific_coverage >= c_covered`` -> **covered**
4. otherwise                       -> **partial** (adjacent work exists)

Every threshold is embedded in the evidence file so a replay cannot drift, and
``drop_terms`` lets the killer test remove a probe from the query without
touching the index.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from .evidence import SCHEMA, seal
from .index import Bm25Index
from .tokenize import _CJK_RE, _LATIN_RE, terms

COVERED = "covered"
PARTIAL = "partial"
BLANK = "blank"
LABELS = (COVERED, PARTIAL, BLANK)


@dataclass(frozen=True)
class Thresholds:
    """Adjudication thresholds (calibrated on the replay benchmark)."""

    s_floor: float = 1.0          # a document must score this to count as a hit
    s_covered: float = 10.0       # top BM25 score required for "covered"
    c_covered: float = 0.5        # fraction of specific probes that must exist
    min_existing_probes: int = 2  # distinct existing specific probes required
    rare_frac: float = 0.02       # df/N at or below which a probe is "specific"
    window: int = 4               # CJK existence-probe length (characters)
    stride: int = 2               # step between consecutive windows
    min_probe_len: int = 2        # shortest CJK run we can probe
    min_latin_len: int = 3        # shortest latin token we treat as a probe

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _round(x: float, nd: int = 6) -> float:
    return round(float(x), nd) + 0.0


def _cjk_probes(run: str, th: Thresholds) -> List[str]:
    if len(run) < th.min_probe_len:
        return []
    if len(run) <= th.window:
        return [run]
    out = [run[i:i + th.window] for i in range(0, len(run) - th.window + 1, th.stride)]
    tail = run[-th.window:]
    if not out or out[-1] != tail:
        out.append(tail)
    return out


def build_probes(topic: str, th: Thresholds, drop: Sequence[str] = ()) -> List[Dict[str, str]]:
    """Ordered, de-duplicated probe list of a topic (window / token)."""
    dropped = {d.lower() for d in drop}
    seen: Dict[str, str] = {}
    out: List[Dict[str, str]] = []
    low = topic.lower()
    for run in _CJK_RE.findall(low):
        for p in _cjk_probes(run, th):
            if p in dropped or p in seen:
                continue
            seen[p] = "window"
            out.append({"probe": p, "type": "window"})
    for tok in _LATIN_RE.findall(low):
        if len(tok) < th.min_latin_len or tok in dropped or tok in seen:
            continue
        seen[tok] = "token"
        out.append({"probe": tok, "type": "token"})
    return out


def query_plan(
    index: Bm25Index, topic: str, th: Thresholds, drop: Sequence[str] = ()
) -> Dict[str, Any]:
    """Probe classification for one topic — the part a reviewer can audit."""
    probes = build_probes(topic, th, drop)
    n = max(index.n_docs, 1)
    rows: List[Dict[str, Any]] = []
    for p in probes:
        if p["type"] == "token":
            docs = sorted(index.postings.get(p["probe"], {}))
        else:
            docs = index.contains(p["probe"])
        df = len(docs)
        rows.append(
            {
                "probe": p["probe"],
                "type": p["type"],
                "df": df,
                "specific": (df / n) <= th.rare_frac,
                "docs": docs[:5],
            }
        )
    specific = [r for r in rows if r["specific"]]
    existing = [r for r in specific if r["df"] > 0]
    return {
        "probes": rows,
        "specific_probes": [r["probe"] for r in specific],
        "existing_specific_probes": [r["probe"] for r in existing],
        "missing_specific_probes": [r["probe"] for r in specific if r["df"] == 0],
        "n_probes": len(rows),
        "n_specific": len(specific),
        "n_existing_specific": len(existing),
        "specific_coverage": _round(len(existing) / len(specific)) if specific else 0.0,
        "terms": [t for t in terms(topic) if t not in set(drop)],
    }


def analyze(
    index: Bm25Index,
    topic: str,
    *,
    thresholds: Optional[Thresholds] = None,
    top_k: int = 5,
    pool_digest: str = "",
    as_of: str = "",
    drop_terms: Sequence[str] = (),
) -> Dict[str, Any]:
    """Adjudicate one free-text topic; returns a sealed ``radar/v1`` envelope."""
    th = thresholds or Thresholds()
    plan = query_plan(index, topic, th, drop_terms)
    _, ranked = index.search(plan["terms"], top_k=top_k)
    top_score = _round(ranked[0][1]) if ranked else 0.0
    hits = [(d, s) for d, s in ranked if s >= th.s_floor]
    top_doc = ranked[0][0] if ranked else None
    top_existing = (
        [r["probe"] for r in plan["probes"] if r["specific"] and r["df"] > 0 and top_doc in r["docs"]]
        if top_doc
        else []
    )

    if plan["n_specific"] == 0:
        verdict = PARTIAL
        reason = "no discriminative probe in the query; refusing to rule blank"
    elif plan["n_existing_specific"] == 0:
        verdict = BLANK
        reason = "every discriminative probe (literal window/token) is absent from the pool"
    elif (
        top_score >= th.s_covered
        and plan["n_existing_specific"] >= th.min_existing_probes
        and plan["specific_coverage"] >= th.c_covered
    ):
        verdict = COVERED
        reason = (
            "literal probes exist, specific coverage and BM25 top score clear the "
            "covered thresholds"
        )
    else:
        verdict = PARTIAL
        reason = "adjacent work exists but the topic's discriminative core is only partly present"

    payload: Dict[str, Any] = {
        "topic": topic,
        "verdict": verdict,
        "reason": reason,
        "as_of": as_of,
        "pool_digest": pool_digest,
        "thresholds": th.as_dict(),
        "query": {
            "raw": topic,
            "terms": plan["terms"],
            "probes": plan["probes"],
            "n_probes": plan["n_probes"],
            "specific_probes": plan["specific_probes"],
            "existing_specific_probes": plan["existing_specific_probes"],
            "n_specific": plan["n_specific"],
            "n_existing_specific": plan["n_existing_specific"],
            "specific_coverage": plan["specific_coverage"],
            "dropped_terms": sorted(set(drop_terms)),
            "term_trail": index.explain(plan["terms"], top_k=top_k)["terms"],
        },
        "hits": {
            "count": len(hits),
            "floor": th.s_floor,
            "docs": [
                {
                    "doc_id": d,
                    "score": _round(s),
                    "kind": index.doc_meta.get(d, {}).get("kind", ""),
                    "title": index.doc_meta.get(d, {}).get("title", ""),
                    "ref": index.doc_meta.get(d, {}).get("ref", ""),
                }
                for d, s in hits
            ],
        },
        "top": {
            "doc_id": top_doc,
            "score": top_score,
            "matched_terms": sorted(
                t for t in plan["terms"] if top_doc in index.postings.get(t, {})
            ) if top_doc else [],
            "matched_specific_probes": sorted(top_existing),
        },
        "zero_hit_proof": {
            "rule": "blank iff no discriminative (df/N<=rare_frac) literal probe occurs in the pool",
            "probe_kind": "cjk_char_window + latin_token",
            "window": th.window,
            "stride": th.stride,
            "relevance_floor": th.s_floor,
            "specific_probes": [
                {"probe": r["probe"], "type": r["type"], "df": r["df"]}
                for r in plan["probes"]
                if r["specific"]
            ],
            "n_specific": plan["n_specific"],
            "n_existing_specific": plan["n_existing_specific"],
            "existence_hits": plan["n_existing_specific"],
            "bm25_hits": len(hits),
            "max_raw_score": top_score,
            "statement": (
                f"executed {plan['n_probes']} literal probe(s) over {index.n_docs} docs; "
                f"{plan['n_specific']} discriminative; "
                f"{plan['n_existing_specific']} existing (existence hits); "
                f"BM25 top {top_score} (covered floor {th.s_covered}, "
                f"{len(hits)} doc(s) above relevance floor {th.s_floor})"
            ),
        },
        "index": index.as_manifest(),
        "schema_target": SCHEMA,
    }
    return seal(payload)


def triage(verdict: str) -> str:
    """Human-facing one-liner used by the CLI / arbitration bridge."""
    return {
        COVERED: "already worked on: cite the hits",
        PARTIAL: "adjacent work exists: narrow the claim",
        BLANK: "no evidence in pool: safe to propose (attach the zero-hit proof)",
    }.get(verdict, "unknown")