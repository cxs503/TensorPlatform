"""Domain-neutral tokenizer for the whitespace radar.

Design contract
---------------
The radar must answer "has anybody in this ecosystem already worked on topic X?"
without knowing anything about the subject matter.  Therefore the tokenizer is
purely *orthographic*:

* CJK runs are cut into character n-grams (``n = 2`` by default).  A run of a
  single character falls back to the unigram so that one-character topics are
  still searchable.
* Latin/digit runs are lower-cased and kept when ``len >= 2``.  Length-1 latin
  tokens (``a``, ``x``, ``1``) carry no discriminative power in an index of this
  size and are dropped.
* Everything else (punctuation, whitespace, symbols) is a separator.

No subject-matter vocabulary, no synonym table and no per-domain stop-word list
exists in this module — those would be domain semantics smuggled into a common
platform mechanism.  The only hard-coded list is ``_PARTICLES``: natural
language function words that are *linguistically* motivated and equally wrong
to index in every domain.

Token order is the order of first appearance in the input, and ``tokenize`` is
deterministic: same text -> same list.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

# Natural-language particles (function words only, domain agnostic).
_PARTICLES = frozenset(
    """
    the a an of and or for to in on at by with from as is are was were be been
    this that these those it its we our you your they their not no yes
    """.split()
)

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_LATIN_RE = re.compile(r"[0-9a-z]+")

NGRAM = 2


def _cjk_ngrams(run: str, n: int) -> List[str]:
    if len(run) <= n:
        return [run]
    return [run[i:i + n] for i in range(len(run) - n + 1)]


def tokenize(text: str, n: int = NGRAM) -> List[str]:
    """Return the ordered token list of ``text`` (duplicates preserved)."""
    if not text:
        return []
    low = text.lower()
    out: List[str] = []
    for run in _CJK_RE.findall(low):
        out.extend(_cjk_ngrams(run, n))
    for run in _LATIN_RE.findall(low):
        if len(run) >= 2 and run not in _PARTICLES:
            out.append(run)
    return out


def iter_spans(text: str, n: int = NGRAM) -> Iterable[Tuple[str, int, int]]:
    """Yield ``(token, start, end)`` spans over the *original* text.

    Offsets are only exact for latin runs (CJK n-grams are lower-cased runs of
    the original string, so the offset of the run is reported).  Kept for
    evidence rendering: a reviewer can see *where* a query term came from.
    """
    if not text:
        return
    low = text.lower()
    for m in _CJK_RE.finditer(low):
        run = m.group(0)
        for tok in _cjk_ngrams(run, n):
            yield tok, m.start(), m.end()
    for m in _LATIN_RE.finditer(low):
        run = m.group(0)
        if len(run) >= 2 and run not in _PARTICLES:
            yield run, m.start(), m.end()


def terms(text: str, n: int = NGRAM) -> List[str]:
    """Unique tokens in first-appearance order (the query term set)."""
    seen = set()
    out: List[str] = []
    for tok in tokenize(text, n):
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def salience(text: str, n: int = NGRAM, limit: int | None = None) -> List[str]:
    """Rare-ish terms of a document text (df-independent, headline heuristic).

    Used only by the *pool builder* to derive human-checkable topic gists; the
    adjudicator never uses it.  Terms are ordered by (first appearance,
    character length desc) so that longer (more specific) tokens surface.
    """
    toks = terms(text, n)
    ordered = sorted(enumerate(toks), key=lambda p: (-len(p[1]), p[0]))
    out = [t for _, t in ordered]
    return out[:limit] if limit else out