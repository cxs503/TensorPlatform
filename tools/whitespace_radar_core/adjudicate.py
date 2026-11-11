"""Tri-state adjudicator: ``covered | generic_only | partial | blank``.

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
   min_existing_probes`` and ``specific_coverage >= c_covered``
   **and the discriminative probes corroborate in one document**
   (>= ``min_existing_probes`` distinct specific probes inside a single document
   whose score also clears ``s_covered``) -> **covered**
4. 3 without corroboration          -> **generic_only** (specificity discipline:
   the pool's strongest match is carried by *scattered lone mentions*, i.e. by
   generic wording, so "covered" would be an over-claim; the envelope keeps the
   generic-probe ledger — every discriminative probe, its df, and the document
   it was found in — so a reviewer can see exactly which evidence was refused)
5. otherwise                       -> **partial** (adjacent work exists)

Specificity discipline (added on top of the existence probes)
-------------------------------------------------------------
"Probes exist somewhere" is a *union* over documents, and a union is trivially
satisfied by boilerplate: a query glued together from generic meta words can
have every 4-char window land in a different document while no single document
talks about the subject.  ``covered`` therefore demands a **witness document**:
one document that carries >= ``min_existing_probes`` distinct discriminative
probes.  Anything weaker is reported as ``generic_only`` with the generic
ledger attached, never silently rounded up to ``covered``.

Every threshold is embedded in the evidence file so a replay cannot drift, and
``drop_terms`` lets the killer test remove a probe from the query without
touching the index.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .evidence import SCHEMA, seal
from .index import Bm25Index
from .tokenize import _CJK_RE, _LATIN_RE, terms

COVERED = "covered"
GENERIC_ONLY = "generic_only"
PARTIAL = "partial"
BLANK = "blank"
LABELS = (COVERED, GENERIC_ONLY, PARTIAL, BLANK)

#: verdicts that mean "the pool already covers this topic" (block the proposal)
COVERING = (COVERED,)
#: verdicts that mean "adjacent work exists; the claim must be narrowed"
ADJACENT = (GENERIC_ONLY, PARTIAL)


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


def _probe_docs(index: Bm25Index, row: Dict[str, Any]) -> List[str]:
    """Full (untruncated) doc-id list for a probe — the corroboration input."""
    if row["type"] == "token":
        return sorted(index.postings.get(row["probe"], {}))
    return index.contains(row["probe"])


def query_plan(
    index: Bm25Index, topic: str, th: Thresholds, drop: Sequence[str] = ()
) -> Dict[str, Any]:
    """Probe classification for one topic — the part a reviewer can audit.

    ``probes[*]["docs"]`` is truncated to 5 ids for the evidence file; the
    corroboration step uses ``_probe_docs`` (full lists) so that a probe which
    exists in many documents is never mistaken for an absent one.
    """
    probes = build_probes(topic, th, drop)
    n = max(index.n_docs, 1)
    rows: List[Dict[str, Any]] = []
    for p in probes:
        docs = _probe_docs(index, p)
        df = len(docs)
        rows.append(
            {
                "probe": p["probe"],
                "type": p["type"],
                "df": df,
                "n_docs_hit": df,
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


def _doc_scores(index: Bm25Index, query_terms: Sequence[str]) -> Dict[str, float]:
    """BM25 score of *every* matching doc (no top-k truncation)."""
    scores, _ = index.search(query_terms, top_k=index.n_docs or 1)
    return scores


def corroboration(
    index: Bm25Index,
    plan: Dict[str, Any],
    doc_scores: Dict[str, float],
    th: Thresholds,
    limit: int = 3,
) -> Dict[str, Any]:
    """Find the witness document: the doc carrying the most discriminative probes.

    This is the specificity discipline's measurement step.  A topic is
    *corroborated* when a single document holds >= ``min_existing_probes``
    distinct specific probes **and** that document's own BM25 score clears
    ``s_covered`` — i.e. the document is both about the topic and about its
    discriminative core.
    """
    per_doc: Dict[str, List[str]] = {}
    for row in plan["probes"]:
        if not row["specific"] or row["df"] == 0:
            continue
        for doc_id in _probe_docs(index, row):
            per_doc.setdefault(doc_id, []).append(row["probe"])
    rolls: List[Dict[str, Any]] = []
    for doc_id in sorted(per_doc):
        probes = sorted(set(per_doc[doc_id]))
        score = doc_scores.get(doc_id, 0.0)
        rolls.append(
            {
                "doc_id": doc_id,
                "n_probes": len(probes),
                "probes": probes,
                "score": _round(score),
                "kind": index.doc_meta.get(doc_id, {}).get("kind", ""),
                "title": index.doc_meta.get(doc_id, {}).get("title", ""),
                "ref": index.doc_meta.get(doc_id, {}).get("ref", ""),
            }
        )
    rolls.sort(key=lambda r: (-r["n_probes"], -r["score"], r["doc_id"]))
    best = rolls[0] if rolls else None
    n_best = best["n_probes"] if best else 0
    best_score = best["score"] if best else 0.0
    return {
        "rule": (
            "covered requires one witness document holding >= min_existing_probes "
            "distinct discriminative probes and scoring >= s_covered"
        ),
        "min_probes": th.min_existing_probes,
        "score_floor": th.s_covered,
        "doc_id": best["doc_id"] if best else None,
        "n_probes": n_best,
        "score": best_score,
        "probes": best["probes"] if best else [],
        "corroborated": bool(n_best >= th.min_existing_probes and best_score >= th.s_covered),
        "witness_candidates": rolls[:limit],
        "n_docs_with_probes": len(rolls),
    }


def generic_ledger(
    index: Bm25Index,
    topic: str,
    plan: Dict[str, Any],
    corrob: Dict[str, Any],
    doc_scores: Dict[str, float],
    th: Thresholds,
) -> Dict[str, Any]:
    """The refused-evidence ledger attached to a ``generic_only`` verdict.

    Two lists, both literal and greppable:

    * **generic probes** — every discriminative probe with its df and the best
      document score it can point at, plus a machine reason why it was refused
      as corroboration (``lone_mention`` / ``below_floor`` / ``absent``);
    * **generic terms** — the topic's own 2-grams/tokens whose df/N exceeds
      ``rare_frac``: the *generic wording* the topic is built from.  This is the
      "泛词清单" a reviewer reads to see what the proposal would have to add.
    """
    witness = corrob.get("doc_id")
    probes: List[Dict[str, Any]] = []
    for row in plan["probes"]:
        if not row["specific"]:
            continue
        docs = _probe_docs(index, row)
        best_doc, best_score = None, 0.0
        for doc_id in docs:
            score = doc_scores.get(doc_id, 0.0)
            if score > best_score or (score == best_score and best_doc is None):
                best_doc, best_score = doc_id, score
        if not docs:
            why = "absent"
        elif witness in docs:
            why = "not_witness"  # present in the witness but not part of its core
        elif best_score < th.s_covered:
            why = "below_floor"
        else:
            why = "lone_mention"
        probes.append(
            {
                "probe": row["probe"],
                "type": row["type"],
                "df": row["df"],
                "df_frac": _round(row["df"] / max(index.n_docs, 1)),
                "best_doc": best_doc,
                "best_doc_score": _round(best_score),
                "why": why,
            }
        )
    generic_terms = []
    for term in terms(topic):
        df = index.df(term)
        frac = df / max(index.n_docs, 1)
        generic_terms.append(
            {"term": term, "df": df, "df_frac": _round(frac), "generic": frac > th.rare_frac}
        )
    generic_terms.sort(key=lambda r: (-r["df_frac"], r["term"]))
    return {
        "generic_probes": probes,
        "generic_terms": [r for r in generic_terms if r["generic"]],
        "all_terms": generic_terms,
        "n_generic_probes": sum(1 for p in probes if p["why"] != "absent"),
        "witness_doc": witness,
        "witness_probes": corrob.get("probes") or [],
    }


GATE_SCHEMA = "specificity-gate/v1"

#: The cap rule, as one auditable sentence.  ``covered`` is the only verdict
#: that claims "someone already did this"; it is therefore the only verdict the
#: gate may *downgrade*.  Everything weaker is left alone (the gate never
#: upgrades a verdict).
GATE_RULE = (
    "covered requires >= required_specific_hits existing discriminative (df/N<=rare_frac) "
    "literal probes AND a single witness document carrying >= min_existing_probes of them; "
    "otherwise the verdict is capped at generic_only and the generic wording is kept in "
    "generic_ledger"
)


def specificity_gate(
    plan: Dict[str, Any],
    corrob: Dict[str, Any],
    thresholds: Thresholds,
    proposed: str,
) -> Dict[str, Any]:
    """Verdict-cap rule for the specificity discipline (fail-closed).

    The gate is deliberately *separate* from the score thresholds: raising the
    BM25 floor can always be argued with ("the topic is just terse"), whereas
    this rule is binary and evidence-shaped — either at least one discriminative
    probe literally occurs in the pool and one document carries the
    discriminative core, or the verdict may not be ``covered``.

    Returns a record with the proposed verdict, the capped verdict, the rule
    that fired and the numbers a reviewer needs to re-derive the decision.
    """
    specific_hits = int(plan.get("n_existing_specific") or 0)
    required = int(thresholds.min_existing_probes)
    witness_probes = int((corrob or {}).get("n_probes") or 0)
    blocking: List[str] = []
    if proposed == COVERED:
        if specific_hits < 1:
            blocking.append("NO_SPECIFIC_HIT")
        if not (corrob or {}).get("corroborated"):
            if witness_probes < required:
                blocking.append("NO_WITNESS")
    capped = GENERIC_ONLY if blocking else proposed
    return {
        "schema": GATE_SCHEMA,
        "rule": GATE_RULE,
        "proposed": proposed,
        "verdict": capped,
        "cap_applied": capped != proposed,
        "blocking": blocking,
        "required_specific_hits": 1,
        "specific_hits": specific_hits,
        "n_specific": int(plan.get("n_specific") or 0),
        "witness_doc": (corrob or {}).get("doc_id"),
        "witness_probes": witness_probes,
        "min_witness_probes": required,
        "generic_list_kept": capped == GENERIC_ONLY,
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
    doc_scores = _doc_scores(index, plan["terms"])
    _, ranked = index.search(plan["terms"], top_k=top_k)
    top_score = _round(ranked[0][1]) if ranked else 0.0
    hits = [(d, s) for d, s in ranked if s >= th.s_floor]
    top_doc = ranked[0][0] if ranked else None
    top_existing = (
        [
            r["probe"]
            for r in plan["probes"]
            if r["specific"] and r["df"] > 0 and top_doc in _probe_docs(index, r)
        ]
        if top_doc
        else []
    )
    corrob = corroboration(index, plan, doc_scores, th)

    gate_ok = (
        top_score >= th.s_covered
        and plan["n_existing_specific"] >= th.min_existing_probes
        and plan["specific_coverage"] >= th.c_covered
    )
    if plan["n_specific"] == 0:
        verdict = PARTIAL
        reason = "no discriminative probe in the query; refusing to rule blank"
    elif plan["n_existing_specific"] == 0:
        verdict = BLANK
        reason = "every discriminative probe (literal window/token) is absent from the pool"
    elif gate_ok and corrob["corroborated"]:
        verdict = COVERED
        reason = (
            "literal probes exist, specific coverage and BM25 top score clear the "
            "covered thresholds, and one witness document carries the discriminative "
            "core"
        )
    elif gate_ok:
        verdict = GENERIC_ONLY
        reason = (
            "score/coverage clear the covered thresholds but no single document carries "
            f">= {th.min_existing_probes} distinct discriminative probes at score >= "
            f"{th.s_covered}: the match is generic wording only, refusing to rule covered"
        )
    else:
        verdict = PARTIAL
        reason = "adjacent work exists but the topic's discriminative core is only partly present"

    # ---- specificity gate: the only verdict the gate may touch is covered ----
    proposed = verdict
    gate = specificity_gate(plan, corrob, th, proposed)
    verdict = gate["verdict"]
    if gate["cap_applied"]:
        reason = (
            "specificity gate capped covered -> generic_only (blocking: "
            + ", ".join(gate["blocking"])
            + f"); specific hits {gate['specific_hits']}, witness probes "
            f"{gate['witness_probes']} < {gate['min_witness_probes']}"
        )
    elif verdict == COVERED:
        gate = dict(gate)
        gate["confirmed_by"] = "gate"

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
        "specificity": {
            "corroboration": corrob,
            "covered_requires_witness": True,
            "downgraded": verdict == GENERIC_ONLY,
            "gate": gate,
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
    if verdict == GENERIC_ONLY:
        payload["generic_ledger"] = generic_ledger(index, topic, plan, corrob, doc_scores, th)
    return seal(payload)


def triage(verdict: str) -> str:
    """Human-facing one-liner used by the CLI / arbitration bridge."""
    return {
        COVERED: "already worked on: cite the hits",
        GENERIC_ONLY: "match is generic wording only: cite the ledger, narrow the claim",
        PARTIAL: "adjacent work exists: narrow the claim",
        BLANK: "no evidence in pool: safe to propose (attach the zero-hit proof)",
    }.get(verdict, "unknown")