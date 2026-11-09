"""BM25 inverted index over the pool, with an auditable query trail.

The index is intentionally simple and stdlib-only so that the evidence it
produces can be recomputed by anyone with ``python3``:

* postings: ``term -> {doc_id: tf}``
* ``df`` = number of docs containing the term
* ``dl`` = indexed token count per doc, ``avgdl`` = mean
* BM25 with ``k1 = 1.2``, ``b = 0.75`` (Robertson/Sparck-Jones defaults)

Two engineering properties matter more than speed here, and both are tested:

1. **Determinism.** No hash-order dependence anywhere: postings are sorted by
   ``doc_id`` when read back, and score ties break on ``doc_id``.
2. **Multi-repo scalability.** The index stores only integer counters and doc
   ids; adding repositories is purely a matter of adding docs.  ``build`` is
   O(total tokens) and a 100k-doc pool costs a few hundred MB at most, which is
   why the pool adapter streams one source at a time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .tokenize import tokenize

K1 = 1.2
B = 0.75


@dataclass
class TermStat:
    term: str
    df: int
    tf_total: int
    postings: Dict[str, int]

    def as_evidence(self, limit: int = 5) -> Dict[str, object]:
        docs = sorted(self.postings.items(), key=lambda kv: (-kv[1], kv[0]))
        return {
            "term": self.term,
            "df": self.df,
            "tf_total": self.tf_total,
            "top_docs": [{"doc_id": d, "tf": tf} for d, tf in docs[:limit]],
        }


class Bm25Index:
    def __init__(self, k1: float = K1, b: float = B):
        self.k1 = k1
        self.b = b
        self.postings: Dict[str, Dict[str, int]] = {}
        self.doc_len: Dict[str, int] = {}
        self.doc_meta: Dict[str, Dict[str, str]] = {}
        self.doc_text: Dict[str, str] = {}
        self._n_tokens = 0

    # -- construction ----------------------------------------------------
    def add(self, doc_id: str, text: str, meta: Optional[Dict[str, str]] = None) -> None:
        toks = tokenize(text)
        # Normalised text is kept for *phrase containment* probes: existence
        # evidence must be a literal match, not a bag-of-ngrams coincidence.
        self.doc_text[doc_id] = " ".join(text.lower().split())
        self.doc_len[doc_id] = len(toks)
        self.doc_meta[doc_id] = dict(meta or {})
        self._n_tokens += len(toks)
        for tok in toks:
            post = self.postings.get(tok)
            if post is None:
                post = self.postings[tok] = {}
            post[doc_id] = post.get(doc_id, 0) + 1

    @classmethod
    def build(cls, docs: Iterable, k1: float = K1, b: float = B) -> "Bm25Index":
        idx = cls(k1=k1, b=b)
        for d in docs:
            idx.add(
                d.doc_id,
                f"{d.title}\n{d.text}",
                {"kind": d.kind, "title": d.title, "ref": d.ref},
            )
        return idx

    # -- statistics ------------------------------------------------------
    @property
    def n_docs(self) -> int:
        return len(self.doc_len)

    @property
    def avgdl(self) -> float:
        if not self.doc_len:
            return 0.0
        return sum(self.doc_len.values()) / len(self.doc_len)

    def df(self, term: str) -> int:
        return len(self.postings.get(term, ()))

    def idf(self, term: str) -> float:
        df = self.df(term)
        n = self.n_docs
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    # -- retrieval -------------------------------------------------------
    def search(
        self, query_terms: Sequence[str], top_k: int = 5
    ) -> Tuple[Dict[str, float], List[Tuple[str, float]]]:
        """Return ``(scores, ranked)``; ``ranked`` is sorted and stable."""
        scores: Dict[str, float] = {}
        avgdl = self.avgdl or 1.0
        for term in query_terms:
            post = self.postings.get(term)
            if not post:
                continue
            idf = self.idf(term)
            for doc_id, tf in post.items():
                dl = self.doc_len.get(doc_id, 0) or 1
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (self.k1 + 1.0) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return scores, ranked[:top_k]

    def contains(self, phrase: str) -> List[str]:
        """Docs whose normalised text contains ``phrase`` (literal substring).

        Candidate generation uses the rarest 2-gram of the phrase, so this stays
        cheap on a 100k-doc pool without any auxiliary structure.
        """
        needle = " ".join(phrase.lower().split())
        if not needle:
            return []
        grams = [g for g in tokenize(needle)]
        if not grams:
            return []
        grams.sort(key=lambda g: (self.df(g), g))
        cand = self.postings.get(grams[0], {})
        if not cand:
            return []
        out = []
        for doc_id in cand:
            text = self.doc_text.get(doc_id, "")
            if needle in text:
                out.append(doc_id)
        return sorted(out)

    def coverage(self, query_terms: Sequence[str]) -> Tuple[int, int]:
        """(#terms with df>0, #terms)."""
        hit = sum(1 for t in query_terms if self.df(t) > 0)
        return hit, len(query_terms)

    def explain(self, query_terms: Sequence[str], top_k: int = 5) -> Dict[str, object]:
        """Per-term, per-doc BM25 contributions — the audit trail."""
        rows = []
        for term in query_terms:
            post = self.postings.get(term)
            if not post:
                rows.append({"term": term, "df": 0, "contrib": []})
                continue
            idf = self.idf(term)
            avgdl = self.avgdl or 1.0
            contrib = []
            for doc_id, tf in sorted(post.items()):
                dl = self.doc_len.get(doc_id, 0) or 1
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                contrib.append(
                    {
                        "doc_id": doc_id,
                        "tf": tf,
                        "bm25": round(idf * tf * (self.k1 + 1.0) / denom, 6),
                    }
                )
            contrib.sort(key=lambda r: (-r["bm25"], r["doc_id"]))
            rows.append(
                {
                    "term": term,
                    "df": len(post),
                    "idf": round(idf, 6),
                    "contrib": contrib[:top_k],
                }
            )
        return {"terms": rows}

    def as_manifest(self) -> Dict[str, object]:
        """Compact, hashable description of the index (for provenance)."""
        return {
            "n_docs": self.n_docs,
            "n_terms": len(self.postings),
            "n_tokens": self._n_tokens,
            "k1": self.k1,
            "b": self.b,
            "avgdl": round(self.avgdl, 6),
        }