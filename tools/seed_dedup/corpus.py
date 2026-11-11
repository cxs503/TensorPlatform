"""Corpus-level redundancy baseline + null-model ledger for the seeding gate.

The seeding dedup gate answers a *per-candidate* question ("is this seed already
worked on?").  This module answers the *corpus-level* question the gate cannot:
**how much near-duplicate redundancy does the node corpus itself carry, and are
the gate's labels distinguishable at the corpus scale?**

Three things are produced, all sealed with sha256:

1. ``blocked_pairs`` — candidate pairs by a character 3-gram inverted index
   (blocking, so the census stays linear-ish instead of quadratic);
2. ``baseline`` — the redundancy distribution over the corpus at a given
   ``theta``: how many pairs clear the gate, how the near-duplicate *families*
   (connected components) are shaped, and where the score quantiles sit;
3. ``provenance_pairs`` — a labelled pair set whose labels come from node
   *provenance* (same ``ext_id`` or byte-identical normalised text) rather than
   from the similarity metric under test, so precision / recall are not
   circular.

Zero subject-matter semantics: texts are opaque strings, labels come from ids.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from tools.whitespace_radar_core.evidence import dumps, seal, verify
from tools.whitespace_radar_core.sources import Doc
from tools.whitespace_radar_core.index import Bm25Index

from .core import (
    SeedConfig,
    _bm25_self_score,
    char_3gram_jaccard,
    char_3grams,
    dual_similarity,
)
from .ledger import ALLOW, BLOCK, Node, adjudicate

SCHEMA = "seed-corpus-baseline/v1"
PROVENANCE_PAIR_SCHEMA = "seed-corpus-pairs/v1"
#: how much of the goal counts as the seed's "head" when deciding whether two
#: entries are the same seed submitted twice
SEED_HEAD_CHARS = 200
DEFAULT_MIN_SHARED_GRAMS = 10


# ------------------------------------------------------------------ corpus io
def load_corpus(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        payload = json.load(fh)
    if payload.get("schema") != "seed-corpus/v1":
        raise ValueError(f"corpus schema {payload.get('schema')!r} != 'seed-corpus/v1'")
    nodes = payload.get("nodes") or []
    if not nodes:
        raise ValueError("corpus has no nodes")
    for node in nodes:
        for key in ("node_id", "title", "goal"):
            if key not in node:
                raise ValueError(f"node missing {key!r}: {node!r}")
    return payload


def node_text(node: Dict[str, Any]) -> str:
    return f"{node.get('title') or ''}\n{node.get('goal') or ''}".strip()


def to_ledger_nodes(nodes: Sequence[Dict[str, Any]]) -> List[Node]:
    return [
        Node(
            node_id=str(n["node_id"]),
            title=str(n.get("title") or ""),
            goal=str(n.get("goal") or ""),
            status=str(n.get("status") or "inflight"),
        )
        for n in nodes
    ]


def build_index(nodes: Sequence[Dict[str, Any]]) -> Bm25Index:
    docs = [
        Doc(
            doc_id=str(n["node_id"]),
            kind="node",
            title=str(n.get("title") or ""),
            text=str(n.get("goal") or ""),
            ref=str(n["node_id"]),
        )
        for n in nodes
    ]
    return Bm25Index.build(docs)


# ------------------------------------------------------------------ blocking
def gram_inverted(nodes: Sequence[Dict[str, Any]]) -> Dict[str, List[int]]:
    postings: Dict[str, List[int]] = {}
    for i, node in enumerate(nodes):
        for gram in char_3grams(node_text(node)):
            postings.setdefault(gram, []).append(i)
    return postings


def blocked_pairs(
    nodes: Sequence[Dict[str, Any]],
    *,
    min_shared: int = DEFAULT_MIN_SHARED_GRAMS,
    max_df_frac: float = 0.2,
    max_df_abs: int = 40,
) -> List[Tuple[int, int]]:
    """Candidate pairs sharing >= ``min_shared`` rare-ish 3-grams.

    Ultra-common grams (shared by > ``max_df_frac`` of the corpus) are skipped:
    they carry no discriminative power and would blow the pair set up.  On a
    tiny corpus the cap can become so small that shared boilerplate disappears;
    raise ``max_df_frac`` (or use :func:`brute_force_scores`) there.
    """
    postings = gram_inverted(nodes)
    n = len(nodes)
    # the absolute cap keeps the pair counter bounded: a gram carried by d
    # documents contributes C(d, 2) increments, so d must stay small
    cap = max(2, min(int(max_df_frac * n), max_df_abs))
    counts: Dict[Tuple[int, int], int] = {}
    for gram, docs in postings.items():
        if len(docs) < 2 or len(docs) > cap:
            continue
        for a in range(len(docs)):
            for b in range(a + 1, len(docs)):
                key = (docs[a], docs[b])
                counts[key] = counts.get(key, 0) + 1
    return sorted(k for k, c in counts.items() if c >= min_shared)


def score_pair(
    index: Bm25Index,
    nodes: Sequence[Dict[str, Any]],
    i: int,
    j: int,
    cfg: SeedConfig,
) -> Dict[str, Any]:
    a, b = nodes[i], nodes[j]
    sim = dual_similarity(index, node_text(a), str(b["node_id"]), node_text(b), cfg)
    return {
        "a": str(a["node_id"]),
        "b": str(b["node_id"]),
        "score": sim.score,
        "bm25_norm": sim.bm25_norm,
        "jaccard_3gram": sim.jaccard_3gram,
    }


# -------------------------------------------------------------- fast scoring
def candidate_sim_cache(
    index: Bm25Index,
    text: str,
    nodes: Sequence[Dict[str, Any]],
    *,
    cfg: Optional[SeedConfig] = None,
    gram_postings: Optional[Dict[str, List[int]]] = None,
    gram_sizes: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Score one candidate against every node, reusing one BM25/gram pass.

    ``dual_similarity`` scores one (candidate, document) pair at a time and
    recomputes the candidate's self-score per call, which is fine for one
    candidate but quadratic-expensive for a corpus census.  Here the BM25
    channel is one ``search`` over the query terms and the Jaccard channel is
    accumulated over the gram postings, so the result is identical (up to
    rounding) at a fraction of the cost.  ``test_seed_dedup_corpus`` pins that
    equivalence against the pairwise oracle.
    """
    cfg = cfg or SeedConfig()
    from tools.whitespace_radar_core.tokenize import tokenize

    query_terms = list(dict.fromkeys(tokenize(text)))
    raw_scores, _ranked = index.search(query_terms, top_k=index.n_docs or 1)
    self_score = _bm25_self_score(index, text)
    grams = char_3grams(text)
    if gram_postings is None:
        gram_postings = gram_inverted(nodes)
    if gram_sizes is None:
        gram_sizes = [len(char_3grams(node_text(n))) for n in nodes]
    shared: Dict[int, int] = {}
    for gram in grams:
        for i in gram_postings.get(gram, ()):
            shared[i] = shared.get(i, 0) + 1
    sims: Dict[str, Any] = {}
    for i, node in enumerate(nodes):
        inter = shared.get(i, 0)
        union = len(grams) + int(gram_sizes[i]) - inter
        jac = (inter / union) if union > 0 else 0.0
        raw = raw_scores.get(str(node["node_id"]), 0.0)
        norm = min(raw / self_score, 1.0) if self_score > 0 else 0.0
        score = cfg.w_bm25 * norm + cfg.w_jaccard * jac
        sims[str(node["node_id"])] = {
            "bm25_raw": round(raw, 6),
            "bm25_norm": round(norm, 6),
            "jaccard_3gram": round(jac, 6),
            "score": round(score, 6),
        }
    return {
        "schema": "seed-sim-cache/v1",
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "n_docs": len(nodes),
        "sims": sims,
    }


# ------------------------------------------------------------------ census
def _quantiles(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0}
    ordered = sorted(values)

    def q(p: float) -> float:
        k = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
        return round(ordered[k], 6)

    return {"p50": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": round(ordered[-1], 6)}


def families(pairs: Iterable[Tuple[str, str]]) -> List[List[str]]:
    """Connected components over near-duplicate pairs (union-find)."""
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for a, b in pairs:
        union(a, b)
    groups: Dict[str, List[str]] = {}
    for member in sorted(parent):
        groups.setdefault(find(member), []).append(member)
    return sorted((sorted(g) for g in groups.values() if len(g) > 1), key=lambda g: (-len(g), g[0]))


def pairwise_scores(
    nodes: Sequence[Dict[str, Any]],
    pairs: Sequence[Tuple[int, int]],
    *,
    cfg: Optional[SeedConfig] = None,
    index: Optional[Bm25Index] = None,
) -> List[Dict[str, Any]]:
    """Score a bounded pair list with one streaming pass per node.

    ``pairs`` are interpreted as ``(a, b)`` with ``a < b``; the BM25 channel is
    normalised by the self-score of ``a`` (the same convention
    ``dual_similarity`` uses for a query), so the census and the gate agree.
    """
    cfg = cfg or SeedConfig()
    index = index if index is not None else build_index(nodes)
    grams = [char_3grams(node_text(n)) for n in nodes]
    sizes = [len(g) for g in grams]
    postings: Dict[str, List[int]] = {}
    for i, gset in enumerate(grams):
        for gram in gset:
            postings.setdefault(gram, []).append(i)
    by_a: Dict[int, List[Tuple[int, int]]] = {}
    for k, (a, b) in enumerate(pairs):
        by_a.setdefault(a, []).append((k, b))
    out: List[Optional[Dict[str, Any]]] = [None] * len(pairs)
    for a, waits in sorted(by_a.items()):
        cache = candidate_sim_cache(
            index, node_text(nodes[a]), nodes, cfg=cfg, gram_postings=postings, gram_sizes=sizes
        )["sims"]
        for k, b in waits:
            sim = cache[str(nodes[b]["node_id"])]
            out[k] = {
                "a": str(nodes[a]["node_id"]),
                "b": str(nodes[b]["node_id"]),
                "a_index": a,
                "b_index": b,
                "score": sim["score"],
                "bm25_norm": sim["bm25_norm"],
                "jaccard_3gram": sim["jaccard_3gram"],
            }
    return [r for r in out if r is not None]


def redundancy_baseline(
    nodes: Sequence[Dict[str, Any]],
    *,
    cfg: Optional[SeedConfig] = None,
    theta: Optional[float] = None,
    min_shared: int = DEFAULT_MIN_SHARED_GRAMS,
    top_pairs: int = 20,
    pair_list: Optional[Sequence[Tuple[int, int]]] = None,
    pair_scores: Optional[Sequence[Dict[str, Any]]] = None,
    max_df_frac: float = 0.2,
) -> Dict[str, Any]:
    """Redundancy census of a node corpus at ``theta`` (default from the gate)."""
    cfg = cfg or SeedConfig()
    th = cfg.theta if theta is None else float(theta)
    index = build_index(nodes)
    pairs = list(
        pair_list
        if pair_list is not None
        else blocked_pairs(nodes, min_shared=min_shared, max_df_frac=max_df_frac)
    )
    scored = list(pair_scores) if pair_scores is not None else pairwise_scores(nodes, pairs, cfg=cfg, index=index)
    scored.sort(key=lambda r: (-r["score"], r["a"], r["b"]))
    near = [r for r in scored if r["score"] >= th]
    fams = families([(r["a"], r["b"]) for r in near])
    in_fams = sum(len(f) for f in fams)
    payload = {
        "schema": SCHEMA,
        "policy": {
            "theta": round(th, 6),
            "min_shared_grams": min_shared,
            "max_df_frac": max_df_frac,
            "seed_config": cfg.to_dict(),
            "weights": {"bm25_norm": cfg.w_bm25, "jaccard_3gram": cfg.w_jaccard},
        },
        "corpus": {
            "n_nodes": len(nodes),
            "n_pairs_blocked": len(scored),
            "n_pairs_total_possible": len(nodes) * (len(nodes) - 1) // 2,
            "blocking_recall_note": (
                "blocking keeps pairs sharing >= min_shared rare 3-grams; the census is a "
                "lower bound on corpus redundancy"
            ),
        },
        "baseline": {
            "n_pairs_ge_theta": len(near),
            "pair_rate_per_mille": round(1000.0 * len(near) / max(1, len(scored)), 4),
            "n_families": len(fams),
            "n_nodes_in_families": in_fams,
            "redundant_node_frac": round(in_fams / max(1, len(nodes)), 6),
            "family_size_hist": {
                str(size): sum(1 for f in fams if len(f) == size)
                for size in sorted({len(f) for f in fams})
            },
            "largest_family_size": len(fams[0]) if fams else 0,
            "score_quantiles_over_blocked_pairs": _quantiles([r["score"] for r in scored]),
            "score_quantiles_over_kept_pairs": _quantiles([r["score"] for r in near]),
        },
        "families": [{"members": f, "size": len(f)} for f in fams[:top_pairs]],
        "top_pairs": scored[:top_pairs],
        "index": {"n_docs": index.n_docs, "n_terms": len(index.postings)},
    }
    return seal(payload)


# ------------------------------------------------------------------ labels
_PUNCT = re.compile(r"[\s\u3000]+")


def normalized_title(node: Dict[str, Any]) -> str:
    """Case/punctuation-insensitive title used for *label* construction only."""
    raw = unicodedata.normalize("NFKC", str(node.get("title") or "")).casefold()
    raw = re.sub(r"[^\w\u4e00-\u9fff]+", " ", raw)
    return _PUNCT.sub(" ", raw).strip()


def _title_terms(node: Dict[str, Any]) -> Set[str]:
    return {t for t in normalized_title(node).split() if t}


def provenance_pairs(
    nodes: Sequence[Dict[str, Any]],
    *,
    n_positive: int = 50,
    n_negative: int = 50,
    seed: int = 12128,
    max_title_jaccard_for_negative: float = 0.2,
) -> Dict[str, Any]:
    """Labelled pairs whose labels come from provenance, not from the metric.

    Positives (``duplicate``) — two entries whose normalised title *and* goal
    head are byte-identical: the same seed text submitted twice.  Identity
    labels (``ext_id``) are deliberately **not** used: a reseed may keep the id
    but completely rewrite the text, and the gate only ever sees text, so
    judging it on ids would manufacture misses.  ``same_ext_id`` is reported per
    row as context, never as a label.

    Negatives (``novel``) — different ``ext_id`` *and* a title word-set Jaccard
    below ``max_title_jaccard_for_negative`` (an independent, unweighted signal),
    so "clearly not the same seed".

    The candidate for a pair is node ``b``; the ledger is the corpus minus ``b``,
    which is exactly the situation the gate faces at seeding time.
    """
    rng = random.Random(seed)
    by_ext: Dict[str, List[int]] = {}
    by_seed: Dict[str, List[int]] = {}
    for i, node in enumerate(nodes):
        ext = str(node.get("ext_id") or "").strip()
        if ext:
            by_ext.setdefault(ext, []).append(i)
        # ``same seed text`` = identical normalised title *and* identical goal
        # head: the same seed submitted twice.  Title-only equality is not
        # enough — task books share headings while describing different work.
        head = unicodedata.normalize("NFKC", str(node.get("goal") or "")[:SEED_HEAD_CHARS]).casefold()
        head = _PUNCT.sub(" ", head).strip()
        title = normalized_title(node)
        if len(title) >= 8 and head:
            by_seed.setdefault(f"{title}\u0000{head}", []).append(i)

    positive_pool: List[Tuple[int, int, str]] = []
    for _key, idxs in sorted(by_seed.items()):
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                positive_pool.append((idxs[a], idxs[b], "same_seed_head"))

    seen: Set[Tuple[int, int]] = set()
    positives: List[Dict[str, Any]] = []
    for i, j, why in positive_pool:
        key = (min(i, j), max(i, j))
        if key in seen:
            continue
        seen.add(key)
        positives.append({"a_index": key[0], "b_index": key[1], "label": "duplicate", "why": why})
    positives.sort(key=lambda r: (r["a_index"], r["b_index"]))
    if len(positives) > n_positive:
        positives = sorted(rng.sample(positives, n_positive), key=lambda r: (r["a_index"], r["b_index"]))

    # A negative (pair with an unrelated entry) only measures the gate fairly if
    # the candidate itself has no twin in the ledger: otherwise the gate is
    # *right* to block it and the "novel" label would be wrong.  Twinness is
    # decided from provenance (the same seed-head key), never from the metric.
    seed_keys: Dict[int, str] = {}
    for i, node in enumerate(nodes):
        head = unicodedata.normalize("NFKC", str(node.get("goal") or "")[:SEED_HEAD_CHARS]).casefold()
        head = _PUNCT.sub(" ", head).strip()
        title = normalized_title(node)
        if title and head:
            seed_keys[i] = f"{title}\u0000{head}"
    key_counts: Dict[str, int] = {}
    for key in seed_keys.values():
        key_counts[key] = key_counts.get(key, 0) + 1

    negative_pool: List[Dict[str, Any]] = []
    n = len(nodes)
    for i in range(n):
        ti = _title_terms(nodes[i])
        if not ti:
            continue
        if key_counts.get(seed_keys.get(i, ""), 0) > 1:
            continue
        for j in range(i + 1, n):
            tj = _title_terms(nodes[j])
            if not tj:
                continue
            if str(nodes[i].get("ext_id") or "") == str(nodes[j].get("ext_id") or ""):
                continue
            if key_counts.get(seed_keys.get(j, ""), 0) > 1:
                continue
            union = ti | tj
            if not union:
                continue
            if len(ti & tj) / len(union) > max_title_jaccard_for_negative:
                continue
            negative_pool.append({"a_index": i, "b_index": j, "label": "novel", "why": "disjoint_ext_low_title_overlap"})
            if len(negative_pool) >= 40 * max(1, n_negative):
                break
        if len(negative_pool) >= 40 * max(1, n_negative):
            break
    negatives = sorted(rng.sample(negative_pool, min(n_negative, len(negative_pool))), key=lambda r: (r["a_index"], r["b_index"]))

    pairs = sorted(positives + negatives, key=lambda r: (r["label"], r["a_index"], r["b_index"]))
    return {
        "schema": PROVENANCE_PAIR_SCHEMA,
        "label_sources": {
            "duplicate": ["same_seed_head"],
            "novel": ["different_ext_id", f"title_word_jaccard<={max_title_jaccard_for_negative}"],
        },
        "seed": seed,
        "n_pairs": len(pairs),
        "n_duplicate": sum(1 for p in pairs if p["label"] == "duplicate"),
        "n_novel": sum(1 for p in pairs if p["label"] == "novel"),
        "digest": hashlib.sha256(dumps(pairs).encode("utf-8")).hexdigest(),
        "pairs": pairs,
    }


def evaluate_provenance_pairs(
    nodes: Sequence[Dict[str, Any]],
    labelled: Dict[str, Any],
    *,
    cfg: Optional[SeedConfig] = None,
    thetas: Optional[Sequence[float]] = None,
    full_index: Optional[Bm25Index] = None,
) -> Dict[str, Any]:
    """Precision/recall of the gate on provenance-labelled pairs (fail-closed).

    ``thetas`` sweeps the threshold: one similarity pass per candidate, then a
    cheap re-decision per threshold (that is what makes the calibration curve
    honest rather than a single cherry-picked point).  The candidate's own entry
    is removed from the ledger, so the gate never scores a node against itself.
    """
    cfg = cfg or SeedConfig()
    ths = [round(float(t), 6) for t in (thetas or (cfg.theta,))]
    index = full_index if full_index is not None else build_index(nodes)
    ledger_nodes = to_ledger_nodes(nodes)
    # gram postings/sizes are corpus-level: build them once, not once per pair
    grams = [char_3grams(node_text(n)) for n in nodes]
    gram_sizes = [len(g) for g in grams]
    gram_postings: Dict[str, List[int]] = {}
    for i, gset in enumerate(grams):
        for gram in gset:
            gram_postings.setdefault(gram, []).append(i)
    per_theta: Dict[str, Dict[str, int]] = {str(t): {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for t in ths}
    rows: List[Dict[str, Any]] = []
    for pair in labelled.get("pairs") or []:
        b_idx, a_idx = pair["b_index"], pair["a_index"]
        cand_node = nodes[b_idx]
        ledger = [n for k, n in enumerate(ledger_nodes) if k != b_idx]
        # the cache key must be *exactly* the text ``adjudicate`` will hash:
        # Candidate.text is ``f"{title}\n{goal}"`` with no whitespace trimming
        cache = candidate_sim_cache(
            index,
            f"{cand_node.get('title') or ''}\n{cand_node.get('goal') or ''}",
            nodes,
            cfg=cfg,
            gram_postings=gram_postings,
            gram_sizes=gram_sizes,
        )
        should_block = pair["label"] == "duplicate"
        same_ext = bool(
            str(nodes[a_idx].get("ext_id") or "")
        ) and str(nodes[a_idx].get("ext_id") or "") == str(cand_node.get("ext_id") or "")
        decisions: Dict[str, Dict[str, Any]] = {}
        for t in ths:
            env = adjudicate(
                {"title": cand_node.get("title"), "goal": cand_node.get("goal")},
                ledger,
                index,
                cfg=cfg,
                theta=t,
                sim_cache=cache,
            )
            blocked = env["decision"] == BLOCK
            tally = per_theta[str(t)]
            if should_block and blocked:
                tally["tp"] += 1
            elif should_block:
                tally["fn"] += 1
            elif blocked:
                tally["fp"] += 1
            else:
                tally["tn"] += 1
            nearest = (env.get("neighbours") or [{}])[0]
            decisions[str(t)] = {
                "decision": env["decision"],
                "code": env.get("code"),
                "nearest": nearest.get("node_id"),
                "score": nearest.get("score"),
                "reason": (env.get("reasons") or [""])[0],
            }
        base = decisions[str(ths[0])]
        rows.append(
            {
                "a": nodes[a_idx]["node_id"],
                "b": nodes[b_idx]["node_id"],
                "label": pair["label"],
                "why": pair["why"],
                "same_ext_id": same_ext,
                "decision": base["decision"],
                "code": base["code"],
                "nearest": base["nearest"],
                "score": base["score"],
                "correct": should_block == (base["decision"] == BLOCK),
                "reason": base["reason"],
                "decisions": decisions,
            }
        )

    # ---- pair-level classification (no third-node confound) --------------
    # A gate decision looks at the candidate's *nearest neighbour anywhere in the
    # corpus*, so a "novel" pair can still be blocked because of a third entry.
    # That is a labelled-pair confound, not a gate error.  The pair-level view
    # below scores exactly the labelled pair, which is what precision/recall of
    # the similarity channel must be measured on.
    idx_pairs = [
        (min(p_["a_index"], p_["b_index"]), max(p_["a_index"], p_["b_index"]))
        for p_ in (labelled.get("pairs") or [])
    ]
    pair_scores = {
        (r["a_index"], r["b_index"]): r["score"]
        for r in pairwise_scores(nodes, idx_pairs, cfg=cfg, index=index)
    }
    pair_sweep: List[Dict[str, Any]] = []
    for t in ths:
        tp = fp = fn = tn = 0
        for p_ in labelled.get("pairs") or []:
            key = (min(p_["a_index"], p_["b_index"]), max(p_["a_index"], p_["b_index"]))
            hit = pair_scores.get(key, 0.0) >= t
            dup = p_["label"] == "duplicate"
            if dup and hit:
                tp += 1
            elif dup:
                fn += 1
            elif hit:
                fp += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        pair_sweep.append(
            {
                "theta": round(t, 6),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(precision, 6),
                "recall": round(recall, 6),
                "f1": round(2 * precision * recall / (precision + recall), 6) if (precision + recall) else 0.0,
            }
        )

    sweep: List[Dict[str, Any]] = []
    for t in ths:
        tally = per_theta[str(t)]
        precision = tally["tp"] / (tally["tp"] + tally["fp"]) if (tally["tp"] + tally["fp"]) else 0.0
        recall = tally["tp"] / (tally["tp"] + tally["fn"]) if (tally["tp"] + tally["fn"]) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        sweep.append(
            {
                "theta": round(t, 6),
                **tally,
                "precision": round(precision, 6),
                "recall": round(recall, 6),
                "f1": round(f1, 6),
            }
        )
    chosen = str(ths[0])
    return {
        "n_pairs": len(rows),
        "n_duplicate": sum(1 for r in rows if r["label"] == "duplicate"),
        "n_novel": sum(1 for r in rows if r["label"] == "novel"),
        "theta": round(ths[0], 6),
        "metrics": {k: v for k, v in per_theta[chosen].items()},
        "precision": next(r["precision"] for r in sweep if r["theta"] == round(ths[0], 6)),
        "recall": next(r["recall"] for r in sweep if r["theta"] == round(ths[0], 6)),
        "f1": next(r["f1"] for r in sweep if r["theta"] == round(ths[0], 6)),
        "sweep": sweep,
        "pair_level": {
            "note": (
                "pair-level classification of the dual-channel score on the labelled pair itself; "
                "the gate-level sweep above answers a different question (nearest neighbour anywhere "
                "in the corpus), so it can block a 'novel' pair through a third entry"
            ),
            "sweep": pair_sweep,
            "at_theta": next(r for r in pair_sweep if r["theta"] == round(ths[0], 6)),
        },
        "pairs_digest": labelled.get("digest"),
        "rows": rows,
        "false_blocks": [r for r in rows if r["label"] == "novel" and r["decision"] == BLOCK],
        "missed_duplicates": [r for r in rows if r["label"] == "duplicate" and r["decision"] != BLOCK],
    }


def brute_force_scores(
    nodes: Sequence[Dict[str, Any]], *, cfg: Optional[SeedConfig] = None
) -> List[Dict[str, Any]]:
    """O(N^2) reference score list (small corpora only; test oracle)."""
    cfg = cfg or SeedConfig()
    index = build_index(nodes)
    out: List[Dict[str, Any]] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            out.append(score_pair(index, nodes, i, j, cfg))
    out.sort(key=lambda r: (-r["score"], r["a"], r["b"]))
    return out