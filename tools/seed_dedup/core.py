"""Dual-channel similarity: BM25 + character 3-gram Jaccard.

Design contract
---------------
Two independent signals, both domain-neutral:

1. **BM25 channel** — token-level (2-gram CJK + latin tokens) BM25 score,
   normalised to [0, 1] by dividing by the self-score of the query.
   Captures *term overlap with IDF weighting*.

2. **Char 3-gram Jaccard** — character-level trigram Jaccard similarity.
   Captures *surface form overlap* even when token boundaries differ
   (e.g. CJK text where tokenisation is ambiguous).

Composite score = ``w_bm25 * bm25_norm + w_jaccard * jaccard``.
Weights and thresholds live in :class:`SeedConfig`.

No external dependencies (stdlib + whitespace_radar_core only).
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from tools.whitespace_radar_core.index import Bm25Index
from tools.whitespace_radar_core.tokenize import tokenize


@dataclass(frozen=True)
class SeedConfig:
    """Policy for the dual-channel seed dedup gate."""

    w_bm25: float = 0.55          # weight for normalised BM25
    w_jaccard: float = 0.45       # weight for char 3-gram Jaccard
    theta: float = 0.30           # near-duplicate threshold
    k: int = 5                    # neighbours reported
    min_title_chars: int = 4
    min_goal_chars: int = 20
    min_terms: int = 6

    def to_dict(self) -> Dict[str, object]:
        return {
            "w_bm25": self.w_bm25,
            "w_jaccard": self.w_jaccard,
            "theta": self.theta,
            "k": self.k,
            "min_title_chars": self.min_title_chars,
            "min_goal_chars": self.min_goal_chars,
            "min_terms": self.min_terms,
        }


@dataclass(frozen=True)
class DualSim:
    """Result of dual-channel similarity between two texts."""

    bm25_raw: float
    bm25_norm: float       # raw / self_score of query (clipped to [0, 1])
    jaccard_3gram: float
    score: float           # composite

    def to_dict(self) -> Dict[str, object]:
        return {
            "bm25_raw": round(self.bm25_raw, 6),
            "bm25_norm": round(self.bm25_norm, 6),
            "jaccard_3gram": round(self.jaccard_3gram, 6),
            "score": round(self.score, 6),
        }


def _normalize(text: str) -> str:
    """NFKC + casefold + collapse whitespace."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def char_3grams(text: str) -> Set[str]:
    """Character 3-grams of normalised text."""
    norm = _normalize(text)
    if len(norm) < 3:
        return {norm} if norm else set()
    return {norm[i:i + 3] for i in range(len(norm) - 2)}


def char_3gram_jaccard(a: str, b: str) -> float:
    """Jaccard similarity of character 3-gram sets."""
    sa = char_3grams(a)
    sb = char_3grams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _bm25_self_score(index: Bm25Index, text: str) -> float:
    """BM25 score of a text against itself (for normalisation)."""
    terms_list = tokenize(text)
    if not terms_list:
        return 0.0
    unique_terms = list(dict.fromkeys(terms_list))  # preserve order, dedup
    scores, _ = index.search(unique_terms, top_k=1)
    # The text itself may not be in the index; compute manually
    avgdl = index.avgdl or 1.0
    score = 0.0
    for term in unique_terms:
        post = index.postings.get(term)
        if not post:
            continue
        idf = index.idf(term)
        # Use the doc with highest tf for this term as proxy
        best_tf = max(post.values())
        dl = index.doc_len.get(max(post, key=post.get), 1) or 1
        denom = best_tf + index.k1 * (1.0 - index.b + index.b * dl / avgdl)
        score += idf * best_tf * (index.k1 + 1.0) / denom
    return score


def bm25_similarity(index: Bm25Index, query_text: str, doc_id: str) -> Tuple[float, float]:
    """BM25 raw score and normalised score for a query against a document.

    Returns (raw_score, normalised_score) where normalised = raw / self_score.
    """
    terms_list = tokenize(query_text)
    if not terms_list:
        return 0.0, 0.0
    unique_terms = list(dict.fromkeys(terms_list))
    scores, ranked = index.search(unique_terms, top_k=index.n_docs or 1)
    raw = scores.get(doc_id, 0.0)

    # Normalise by self-score of the query
    self_score = _bm25_self_score(index, query_text)
    norm = raw / self_score if self_score > 0 else 0.0
    norm = min(norm, 1.0)  # clip
    return raw, norm


def dual_similarity(
    index: Bm25Index,
    query_text: str,
    doc_id: str,
    doc_text: str,
    cfg: Optional[SeedConfig] = None,
) -> DualSim:
    """Compute dual-channel similarity between query and a document.

    Channel 1: BM25 (normalised)
    Channel 2: character 3-gram Jaccard
    """
    cfg = cfg or SeedConfig()
    bm25_raw, bm25_norm = bm25_similarity(index, query_text, doc_id)
    jaccard = char_3gram_jaccard(query_text, doc_text)
    score = cfg.w_bm25 * bm25_norm + cfg.w_jaccard * jaccard
    return DualSim(
        bm25_raw=round(bm25_raw, 6),
        bm25_norm=round(bm25_norm, 6),
        jaccard_3gram=round(jaccard, 6),
        score=round(score, 6),
    )
