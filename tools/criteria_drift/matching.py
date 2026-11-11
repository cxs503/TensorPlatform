"""Criterion matching: align proposal criteria with report criteria.

Why matching is the hard part
-----------------------------
Drift can only be measured on *the same* criterion.  Reports are written by a
different author, in a different language mix, in a different order.  So the
matcher must be robust to paraphrase while never pairing two unrelated
criteria - an unrelated pairing would manufacture a huge fake relaxation.

Mechanism (all parameters in ``config.json``)
---------------------------------------------
1. **Synonym canonicalisation** - the lexicon's ``synonyms`` groups are folded
   into one token each (``误差``/``error``/``偏差`` -> ``error``; CJK bigrams of a
   multi-character member are members too), so cross-language paraphrase still
   shares tokens.
2. **Score** = w_subject·Jaccard + w_kind·[same kind family] + w_value·[equal
   canonical value].
3. **Acceptance gate** - a pair is only a candidate when it shares subject
   tokens (>= accept_subject_similarity) *or* it has the same kind family and
   exactly the same canonical value.  This is what keeps a *dropped* criterion
   from being silently paired with some other criterion of the same kind.
4. **Assignment** - deterministic greedy on score descending, tie-break by
   (proposal index, report index).  Leftovers are reported as dropped (in the
   proposal) or added (in the report).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import model
from .parser import load_lexicon

POLICY_PATH = Path(__file__).resolve().parent / "config.json"


@lru_cache(maxsize=1)
def load_policy(path: str | None = None) -> Dict[str, Any]:
    p = Path(path) if path else POLICY_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def _bigrams(s: str) -> List[str]:
    if len(s) <= 1:
        return [s]
    return [s[i:i + 2] for i in range(len(s) - 1)]


@lru_cache(maxsize=1)
def synonym_map() -> Dict[str, str]:
    lex = load_lexicon()
    groups = list(lex.get("synonyms", []))
    aliases = lex.get("metric_aliases", {}).get("groups", [])
    groups.extend(aliases)
    out: Dict[str, str] = {}
    for gi, members in enumerate(groups):
        canonical = f"syn{gi}"
        for m in members:
            ml = m.lower().strip()
            if not ml:
                continue
            out[ml] = canonical
            for bg in _bigrams(ml):
                out[bg] = canonical
    return out


def canonical_tokens(tokens: Sequence[str]) -> frozenset:
    sm = synonym_map()
    return frozenset(sm.get(t, t) for t in tokens)


def subject_similarity(a: model.Criterion, b: model.Criterion, use_synonyms: bool = True) -> float:
    ta = canonical_tokens(a.tokens) if use_synonyms else frozenset(a.tokens)
    tb = canonical_tokens(b.tokens) if use_synonyms else frozenset(b.tokens)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def kind_family(c: model.Criterion) -> str:
    """Coarse family: two criteria in the same family measure the same thing."""
    if c.kind in (model.KIND_ORDER, model.KIND_PVALUE, model.KIND_COUNT, model.KIND_PRESENCE):
        return c.kind
    # all scalar bounds live on the same numeric scale after unit
    # canonicalisation (3% == 0.03), so fraction vs dimensionless is NOT a
    # family change - only measurement *type* (order/p-value/count/presence) is.
    return "numeric"


def same_value(a: model.Criterion, b: model.Criterion, rel: float = 1e-9) -> bool:
    if a.op == model.RANGE_OP and b.op == model.RANGE_OP:
        return (_close(a.lo, b.lo, rel) and _close(a.hi, b.hi, rel))
    if a.value is None or b.value is None:
        return False
    if a.unit == model.UNIT_COUNT and b.unit == model.UNIT_COUNT:
        return abs(a.value - b.value) < 1e-12
    return _close(a.value, b.value, rel)


def _close(x: Optional[float], y: Optional[float], rel: float) -> bool:
    if x is None or y is None:
        return x is y
    return abs(x - y) <= rel * max(1.0, abs(x), abs(y))


def pair_score(a: model.Criterion, b: model.Criterion, policy: Optional[Dict[str, Any]] = None) -> float:
    pol = policy or load_policy()
    m = pol.get("match", {})
    sim = subject_similarity(a, b, bool(m.get("use_synonyms", True)))
    same_kind = 1.0 if kind_family(a) == kind_family(b) else 0.0
    same_val = 1.0 if same_value(a, b) else 0.0
    return (m.get("subject_weight", 0.6) * sim
            + m.get("kind_weight", 0.3) * same_kind
            + m.get("value_weight", 0.1) * same_val)


def is_candidate(a: model.Criterion, b: model.Criterion, policy: Optional[Dict[str, Any]] = None) -> bool:
    pol = policy or load_policy()
    m = pol.get("match", {})
    sim = subject_similarity(a, b, bool(m.get("use_synonyms", True)))
    if sim >= float(m.get("accept_subject_similarity", 0.20)):
        return True
    if m.get("accept_equal_value_with_same_kind", True):
        if kind_family(a) == kind_family(b) and same_value(a, b):
            return True
    return False


def _optimal_assignment(scores: List[List[Optional[float]]]) -> List[Tuple[int, int, float]]:
    """Exact one-to-one assignment (bitmask DP).

    Greedy matching lets one criterion "steal" the partner of another when two
    criteria share a kind and a synonym collapses the distinguishing word; the
    robbed criterion then looks *dropped* (a false positive) and a fake
    relaxation appears on the other.  n is small (<= 10 criteria per document),
    so the exact optimum is cheap and removes tie-break arbitrariness.
    """
    n_p = len(scores)
    n_r = len(scores[0]) if n_p else 0
    if n_p == 0 or n_r == 0:
        return []
    NEG = float("-inf")
    # dp[mask] = best total score having assigned the first `i` proposals
    dp = {0: 0.0}
    choice: List[Dict[int, Tuple[int, int]]] = [dict() for _ in range(n_p + 1)]
    for i in range(n_p):
        nxt: Dict[int, float] = {}
        for mask, total in dp.items():
            # option: leave proposal i unmatched
            if total > nxt.get(mask, NEG):
                nxt[mask] = total
                choice[i + 1][mask] = (i, -1)
            for j in range(n_r):
                if mask & (1 << j):
                    continue
                sc = scores[i][j]
                if sc is None:
                    continue
                nm = mask | (1 << j)
                cand = total + sc
                if cand > nxt.get(nm, NEG):
                    nxt[nm] = cand
                    choice[i + 1][nm] = (i, j)
        dp = nxt
    best_mask, best_total = 0, NEG
    for mask, total in dp.items():
        if total > best_total + 1e-12 or (abs(total - best_total) <= 1e-12 and mask < best_mask):
            best_mask, best_total = mask, total
    pairs: List[Tuple[int, int, float]] = []
    mask = best_mask
    for i in range(n_p, 0, -1):
        pi, rj = choice[i][mask]
        if rj >= 0:
            pairs.append((pi, rj, round(scores[pi][rj] or 0.0, 4)))
            mask ^= (1 << rj)
    pairs.sort(key=lambda t: t[0])
    return pairs


def _family_fallback(proposal: Sequence[model.Criterion], report: Sequence[model.Criterion],
                     unmatched_p: List[int], unmatched_r: List[int],
                     policy: Dict[str, Any]) -> List[Tuple[int, int, float]]:
    """Pair leftovers only when the pairing is *unambiguous*: exactly one
    unmatched proposal and one unmatched report in the same kind family, with
    relative distance <= max_rel_distance.  This rescues paraphrases that share
    no tokens (``p值至少0.05`` vs ``p值不低于0.0463``) without ever pairing a
    dropped criterion with a sibling of the same kind."""
    m = policy.get("match", {})
    max_rel = float(m.get("family_fallback_max_rel_distance", 0.5))
    out: List[Tuple[int, int, float]] = []
    used_p, used_r = set(), set()
    for i in list(unmatched_p):
        if i in used_p:
            continue
        fam = kind_family(proposal[i])
        same_p = [k for k in unmatched_p if k not in used_p and kind_family(proposal[k]) == fam]
        same_r = [j for j in unmatched_r if j not in used_r and kind_family(report[j]) == fam]
        if len(same_p) != 1 or len(same_r) != 1:
            continue
        a, b = proposal[same_p[0]], report[same_r[0]]
        if model.operator_direction(a.op) != model.operator_direction(b.op):
            continue
        if not _comparable(a, b, max_rel):
            continue
        used_p.add(same_p[0]); used_r.add(same_r[0])
        out.append((same_p[0], same_r[0], round(0.25 * subject_similarity(a, b), 4)))
    return out


def _comparable(a: model.Criterion, b: model.Criterion, max_rel: float) -> bool:
    if a.value is None or b.value is None:
        return False
    if a.unit == model.UNIT_COUNT or b.unit == model.UNIT_COUNT:
        if a.unit != b.unit:
            return False
        return abs(a.value - b.value) <= max(1.0, max_rel * max(a.value, b.value))
    denom = max(abs(a.value), abs(b.value), 1e-30)
    return abs(a.value - b.value) / denom <= max_rel


def match(proposal: Sequence[model.Criterion], report: Sequence[model.Criterion],
          policy: Optional[Dict[str, Any]] = None) -> Tuple[List[Tuple[int, int, float]], List[int], List[int]]:
    """Return ``(pairs, unmatched_proposal_idx, unmatched_report_idx)``."""
    pol = policy or load_policy()
    scores: List[List[Optional[float]]] = []
    for a in proposal:
        row: List[Optional[float]] = []
        for b in report:
            row.append(pair_score(a, b, pol) if is_candidate(a, b, pol) else None)
        scores.append(row)
    pairs = _optimal_assignment(scores)
    used_p = {p for p, _, _ in pairs}
    used_r = {j for _, j, _ in pairs}
    unmatched_p = [i for i in range(len(proposal)) if i not in used_p]
    unmatched_r = [j for j in range(len(report)) if j not in used_r]
    if unmatched_p and unmatched_r and pol.get("match", {}).get("enable_family_fallback", True):
        extra = _family_fallback(proposal, report, unmatched_p, unmatched_r, pol)
        pairs.extend(extra)
        used_p |= {p for p, _, _ in extra}
        used_r |= {j for _, j, _ in extra}
        unmatched_p = [i for i in unmatched_p if i not in used_p]
        unmatched_r = [j for j in unmatched_r if j not in used_r]
    pairs.sort(key=lambda t: t[0])
    return pairs, unmatched_p, unmatched_r


def match_waiver(proposal: model.Criterion, waivers: Sequence[model.Waiver]) -> Optional[model.Waiver]:
    """A waiver covers a criterion when its target text names it (token overlap
    >= 0.5) or contains the criterion text verbatim."""
    best, best_sim = None, 0.0
    for w in waivers:
        t = (w.target or "").strip()
        if not t:
            continue
        raw_n = "".join((proposal.raw or "").split())
        t_n = "".join(t.split())
        if raw_n and (raw_n in t_n or (len(t_n) >= 4 and t_n in raw_n)):
            return w
        subj_n = "".join((proposal.subject or "").split())
        if subj_n and len(subj_n) >= 4 and subj_n in t_n:
            return w
        stops = load_lexicon().get("stop_tokens", ())
        wt = canonical_tokens(model.tokens_from_text(t, stops))
        pt = canonical_tokens(proposal.tokens)
        st = canonical_tokens(model.tokens_from_text(proposal.subject, stops))
        if not wt:
            continue
        sim = len(wt & (st or pt)) / len(wt | (st or pt)) if (st or pt) else 0.0
        if sim > best_sim:
            best, best_sim = w, sim
    return best if best_sim >= 0.5 else None