"""Criterion parser: acceptance-criteria text -> :class:`model.Criterion`.

Covered criterion modes (>=5 required by the node contract)
------------------------------------------------------------
1. ``error_threshold``      ``误差≤3%`` / ``relative error <= 0.03``
2. ``convergence_order``    ``收敛阶≥2`` / ``observed order >= 1.8``
3. ``conservation``         ``守恒量偏差≤1e-12`` / ``mass conservation <= 0.1%``
4. ``statistical_pvalue``   ``p值≥0.05`` / ``p-value < 0.01``
5. ``coverage``             ``覆盖率≥80%``
6. ``count``                ``pytest≥15例`` / ``test count >= 15``
7. ``threshold``            ``R²≥0.999`` / ``|σ-1|≤0.05``
8. ``presence``             ``报告包含六要素`` (no number -> not silently dropped)

Design rules
------------
* **No invention**: if a text carries no comparator and no range it is returned
  as ``presence`` (``value=None``); the auditor then reports it as unjudgeable
  instead of pretending a threshold exists.
* **Canonical units**: percentages/permille are divided into ratios at parse
  time, so ``3%`` and ``0.03`` are the same criterion (no unit-induced false
  drift).  The written number is preserved in ``display_value``.
* **One assertion per criterion**: real texts contain context numbers
  (``B(ω→0)``, ``d/L>2 时 …``).  The parser keeps every numeric match in
  ``alternatives`` for auditability but selects the *asserted* bound by a
  documented score: explicit unit > later position > carries a comparison
  glyph (``→`` is not a comparison).

Vocabulary (kind keywords, operator phrases, unit scales) comes from
``lexicon.json``; nothing domain-specific is hard-coded here.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import model

_HERE = Path(__file__).resolve().parent
LEXICON_PATH = _HERE / "lexicon.json"

NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_FRACTION = re.compile(r"(?P<a>\d+)\s*/\s*(?P<b>\d+)")
_UNIT_AFTER = re.compile(
    r"^\s*(?P<unit>%|％|‰|percent|per\s+mille|个百分点|倍|例|项|个|条|阶|ms|s\b)?",
    re.IGNORECASE,
)
_TOL = re.compile(r"(?:±|\+/-)\s*(?P<num>" + NUM + r")\s*(?P<unit>%|％|‰|percent)?")
_TOL_TRAIL = re.compile(r"^\s*(?:±|\+/-)\s*(?P<num>" + NUM + r")\s*(?P<unit>%|％|‰|percent)?")
_RANGE = re.compile(
    r"(?P<lo>" + NUM + r")\s*(?P<u1>%|％|‰)?\s*(?:~|～|–|—|至|到|to|\.\.\.)\s*(?P<hi>" + NUM + r")\s*(?P<u2>%|％|‰)?"
)
_RANGE_BRACKET = re.compile(r"[\[\(]\s*(?P<lo>" + NUM + r")\s*,\s*(?P<hi>" + NUM + r")\s*[\]\)]")

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_LEAD_ENUM = re.compile(r"^\s*(?:[\(（]\s*\d+\s*[\)）]|\d+[\.\)、]|[①-⑳]|[-*•])\s*")


@lru_cache(maxsize=1)
def load_lexicon(path: str | None = None) -> Dict[str, Any]:
    p = Path(path) if path else LEXICON_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def _nfkc(text: str) -> str:
    """Full-width -> half-width (％→%, ０→0, ≤ stays), keeps CJK intact."""
    return unicodedata.normalize("NFKC", text or "")


def _op_regex(lex: Dict[str, Any]) -> re.Pattern:
    phrases: List[str] = []
    for _op, items in lex.get("op_phrases", {}).items():
        for s in items:
            phrases.append(s)
    # longest first so that "<=" wins over "<"
    phrases.sort(key=len, reverse=True)
    alt = "|".join(re.escape(p) for p in phrases)
    return re.compile(r"(?P<op>" + alt + r")", re.IGNORECASE)


@lru_cache(maxsize=1)
def _op_pattern() -> re.Pattern:
    return _op_regex(load_lexicon())


@lru_cache(maxsize=1)
def _canonical_op_map() -> Dict[str, str]:
    lex = load_lexicon()
    out: Dict[str, str] = {}
    for op, items in lex.get("op_phrases", {}).items():
        for s in items:
            out[s.lower()] = op
    return out


def _canon_op(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _canonical_op_map().get(key, model.canonical_operator(key))


def _unit_scale(unit: str, lex: Dict[str, Any]) -> float:
    return float(lex.get("unit_scale", {}).get(unit, 1.0))


def _kind_of(text: str, lex: Dict[str, Any]) -> str:
    low = _nfkc(text).lower()
    best_kind, best_hits, best_len = model.KIND_THRESHOLD, 0, 0
    for item in lex.get("kinds", []):
        for kw in item.get("keywords", []):
            k = kw.lower()
            if k and k in low:
                hits = low.count(k)
                # prefer the longest keyword (p-value over value; 守恒量偏差 over 偏差)
                if hits and (len(k) > best_len or (len(k) == best_len and hits > best_hits)):
                    best_kind, best_hits, best_len = item["kind"], hits, len(k)
    return best_kind


def _kind_from_subject_or_text(subject: str, whole: str, lex: Dict[str, Any]) -> str:
    """Kind is decided on the subject first; if the subject is too thin (``≤3%``)
    the whole criterion text is used.  A subject hit always wins because the
    tail may contain a different noun (``… 覆盖率 R²``)."""
    if len(_nfkc(subject).strip()) >= 2:
        k = _kind_of(subject, lex)
        if k != model.KIND_THRESHOLD:
            return k
        # subject exists but carries no keyword: fall back to the whole text only
        # when the whole text adds a keyword outside the subject.
    return _kind_of(whole, lex)


def _unit_class(kind: str, unit_token: str, lex: Dict[str, Any]) -> Tuple[str, float]:
    u = (unit_token or "").strip().lower()
    if kind == model.KIND_ORDER:
        return model.UNIT_ORDER, 1.0
    if kind == model.KIND_PVALUE:
        return model.UNIT_PVALUE, 1.0
    if kind == model.KIND_COUNT:
        return model.UNIT_COUNT, 1.0
    if u in lex.get("unit_scale", {}):
        return model.UNIT_FRACTION, _unit_scale(u, lex)
    if kind in (model.KIND_ERROR, model.KIND_COVERAGE, model.KIND_CONSERVATION):
        return model.UNIT_FRACTION, 1.0
    return model.UNIT_DIMENSIONLESS, 1.0


def _strip_subject(text: str) -> str:
    s = _LEAD_ENUM.sub("", _nfkc(text)).strip()
    s = re.sub(r"[\s,;:，；：、]+$", "", s)
    return s


def _select_candidate(cands: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the asserted bound.

    Score: explicit unit (+3) > textually flagged as the assertion (+1: preceded
    by 时/when/,) > later occurrence (+0.1 per index).  Ties resolved by taking
    the later one, deterministically.
    """
    if not cands:
        return None
    def score(c: Dict[str, Any]) -> float:
        s = 0.0
        if c.get("unit_token"):
            s += 3.0
        if c.get("assertive"):
            s += 1.0
        if c.get("depth", 0) > 0:
            # "变化<=2%(GCI<=3%)": the parenthetical is a companion bound, and the
            # asserted one is outside the parentheses.  Penalty, not exclusion, so a
            # lone parenthetical number is still usable.
            s -= 1.5
        s += 0.1 * c["index"]
        return s
    return max(cands, key=score)


def comparator_directions(text: str) -> List[str]:
    """Distinct comparison directions present in the text (public helper).

    Two opposed directions joined by 且/and mean the criterion is a *compound*
    assertion ("占比>30% 且 merge<50%"); the model keeps the asserted bound and
    flags ``compound`` so the comparator can detect losing the other half.
    """
    dirs = []
    for c in _candidates(_nfkc(text)):
        d = model.operator_direction(c["op"])
        if d in ("upper", "lower") and d not in dirs:
            dirs.append(d)
    return dirs


_CONJUNCTION = re.compile(r"(且|并且|而且|同时|以及|\band\b|&)")


def is_compound(text: str) -> bool:
    """Opposed bounds joined by a conjunction (``且``/``and``).

    A context number (``d/L>2 时 ...``) is *not* a compound criterion: two
    opposed comparators only mean "two constraints" when they are conjoined.
    """
    norm = _nfkc(text)
    if not _CONJUNCTION.search(norm):
        return False
    return len(set(comparator_directions(norm)) & {"upper", "lower"}) > 1


def _candidates(text: str) -> List[Dict[str, Any]]:
    cands: List[Dict[str, Any]] = []
    for m in _op_pattern().finditer(text):
        op_raw = m.group("op")
        rest = text[m.end():m.end() + 40]
        num_m = re.match(r"\s*(" + NUM + r")", rest)
        if not num_m:
            # operator word such as "within" may be followed by a space and number
            continue
        num_txt = num_m.group(1)
        tail = rest[num_m.end():]
        unit_m = _UNIT_AFTER.match(tail)
        unit_token = (unit_m.group("unit") or "") if unit_m else ""
        before = text[max(0, m.start() - 12):m.start()]
        depth = text[:m.start()].count("(") - text[:m.start()].count(")")
        cands.append({
            "depth": max(0, depth),
            "index": len(cands),
            "op_raw": op_raw,
            "op": _canon_op(op_raw),
            "num": float(num_txt),
            "num_txt": num_txt,
            "unit_token": unit_token,
            "tail": tail[:24],
            "before": before,
            "assertive": bool(re.search(r"(时|when|，|,|则|要求|应)", before)),
            "start": m.start(),
        })
    return cands


def _with_kind_token(tokens: Sequence[str], kind: str) -> Tuple[str, ...]:
    """Add a ``kind:<family>`` token.

    Rationale: the *kind word* is often written in the other language
    (``p-value`` vs ``p值``, ``observed order`` vs ``观测阶``).  Without a shared
    kind token two paraphrases of the same criterion look unrelated and the
    matcher wrongly reports a dropped criterion (a false positive).  The token is
    a mechanism-level label, not a domain word.
    """
    fam = "numeric" if kind in (model.KIND_ERROR, model.KIND_CONSERVATION,
                                model.KIND_COVERAGE, model.KIND_THRESHOLD) else kind
    return tuple(sorted(set(tokens) | {f"kind:{fam}"}))


def parse_criterion(text: str, locator: str = "", lexicon: Optional[Dict[str, Any]] = None) -> model.Criterion:
    """Parse one criterion string.  Never raises on odd input."""
    lex = lexicon or load_lexicon()
    raw = text or ""
    norm = _nfkc(raw)
    subject_all = _strip_subject(norm)

    # ── 1) tolerance band (±0.5% / 3%±0.5%) ────────────────────────────
    tolerance = None
    tolerance_display = None
    tol_m = _TOL.search(norm)
    if tol_m:
        try:
            tol_num = float(tol_m.group("num"))
            tol_unit = (tol_m.group("unit") or "").strip().lower()
            tol_kind = _kind_of(norm, lex)
            unit_cls, scale = _unit_class(tol_kind, tol_unit, lex)
            tolerance = tol_num * scale if unit_cls == model.UNIT_FRACTION else tol_num
            tolerance_display = tol_num
        except (TypeError, ValueError):
            tolerance = None

    # ── 2) explicit ranges (0.9~1.1, [0.9, 1.1]) ───────────────────────
    rng = _RANGE_BRACKET.search(norm) or _RANGE.search(norm)
    if rng:
        try:
            lo = float(rng.group("lo"))
            hi = float(rng.group("hi"))
            if rng.re is _RANGE:
                u1 = (rng.group("u1") or "").strip().lower()
                u2 = (rng.group("u2") or "").strip().lower()
            else:
                u1 = u2 = ""
            kind = _kind_from_subject_or_text(subject_all, norm, lex)
            unit_cls, scale = _unit_class(kind, u1 or u2, lex)
            if unit_cls == model.UNIT_FRACTION:
                lo, hi = lo * scale, hi * scale
            if hi < lo:
                lo, hi = hi, lo
            subject = _strip_subject(norm[:rng.start()])
            return model.Criterion(
                raw=raw, kind=kind, op=model.RANGE_OP, value=None,
                unit=unit_cls, subject=subject,
                tokens=_with_kind_token(model.tokens_from_text(subject_all, lex.get("stop_tokens", ())), kind),
                display_value=None, display_unit=(u1 or u2),
                tolerance=tolerance, tolerance_display=tolerance_display,
                lo=lo, hi=hi, locator=locator,
            )
        except (TypeError, ValueError):
            pass

    # ── 3) fraction-style agreement (≥4/5) ─────────────────────────────
    frac_m = _FRACTION.search(norm)
    # only treat "4/5" as a ratio when it is not part of a unit like "1/2 h"
    frac_candidate = None
    if frac_m and "/" in norm and not re.search(r"\d+\s*/\s*\d+\s*(h|s|m)\b", norm):
        a, b = int(frac_m.group("a")), int(frac_m.group("b"))
        if b > 0 and 0 < a <= b:
            frac_candidate = a / b

    # ── 4) comparison candidates ───────────────────────────────────────
    cands = _candidates(norm)
    chosen = _select_candidate(cands)

    if frac_candidate is not None and (chosen is None or not chosen.get("unit_token")):
        # "agreement >= 4/5" -> ratio lower bound
        subject = _strip_subject(norm[:frac_m.start()])
        op = chosen["op"] if chosen else "<="
        return model.Criterion(
            raw=raw, kind=model.KIND_THRESHOLD, op=op, value=frac_candidate,
            unit=model.UNIT_FRACTION, subject=subject,
            tokens=_with_kind_token(model.tokens_from_text(subject_all, lex.get("stop_tokens", ())),
                                    model.KIND_THRESHOLD),
            display_value=frac_candidate, display_unit="ratio",
            tolerance=tolerance, tolerance_display=tolerance_display,
            locator=locator,
        )

    if chosen is None:
        # presence criterion: no comparator, no range.  Do NOT invent a value.
        return model.Criterion(
            raw=raw, kind=model.KIND_PRESENCE, op=model.PRESENCE_OP, value=None,
            unit="none", subject=subject_all,
            tokens=_with_kind_token(model.tokens_from_text(subject_all, lex.get("stop_tokens", ())),
                                    model.KIND_PRESENCE),
            tolerance=None, locator=locator,
        )

    op = chosen["op"]
    unit_token = chosen["unit_token"]
    subject = _strip_subject(norm[:chosen["start"]])
    if len(subject) < 2:
        subject = _strip_subject(re.sub(r"(" + NUM + r")\s*(%|％|‰)?", " ", norm))
        subject = re.sub(_op_pattern(), " ", subject).strip()
    kind = _kind_from_subject_or_text(subject, norm, lex)
    # a count unit forces the count kind even if the subject keyword is absent
    if unit_token in ("例", "项", "个", "条"):
        kind = model.KIND_COUNT
    unit_cls, scale = _unit_class(kind, unit_token, lex)
    value = chosen["num"] * scale if unit_cls == model.UNIT_FRACTION else chosen["num"]

    # range written as "3%~5%" -> store as range for width comparison
    tail = chosen["tail"]
    r2 = re.match(r"\s*(?:~|～|–|—|至|到|-)\s*(" + NUM + r")\s*(%|％|‰)?", tail)
    lo = hi = None
    if r2 and unit_cls == model.UNIT_FRACTION:
        scale2 = _unit_scale((r2.group(2) or unit_token).strip().lower(), lex)
        a, b = value, float(r2.group(1)) * scale2
        lo, hi = min(a, b), max(a, b)
        op = model.RANGE_OP
        kind = kind if kind != model.KIND_THRESHOLD else model.KIND_ERROR

    alts = tuple(round(c["num"] * (_unit_scale(c["unit_token"], lex)
                                    if _unit_class(kind, c["unit_token"], lex)[0] == model.UNIT_FRACTION else 1.0), 12)
                 for c in cands if c is not chosen)

    return model.Criterion(
        raw=raw, kind=kind, op=op, value=value, unit=unit_cls, subject=subject,
        tokens=_with_kind_token(model.tokens_from_text(subject_all, lex.get("stop_tokens", ())), kind),
        display_value=chosen["num"], display_unit=unit_token or "",
        tolerance=tolerance, tolerance_display=tolerance_display,
        lo=lo, hi=hi, locator=locator, alternatives=alts,
        extras=tuple(f"{c['op']}{c['num']}{c['unit_token']}" for c in cands if c is not chosen),
        compound=is_compound(norm),
    )


# ── Chinese "二阶收敛" has no comparator glyph ────────────────────────────
_CN_ORDER = re.compile(r"([一二两三四五六七八九十])\s*阶(?:收敛|精度)|([一二两三四五六七八九十])\s*阶\s*精度")


def parse_with_cn_order(text: str, locator: str = "") -> model.Criterion:
    """Wrapper: translate ``二阶收敛`` into ``收敛阶>=2`` before the generic parse."""
    if _CN_ORDER.search(text or ""):
        m = _CN_ORDER.search(text)
        n = _CN_NUM.get(m.group(1) or m.group(2), 2)
        c = parse_criterion(text, locator=locator)
        if c.kind == model.KIND_PRESENCE or c.value is None:
            return model.Criterion(
                raw=text, kind=model.KIND_ORDER, op=">=", value=float(n), unit=model.UNIT_ORDER,
                subject=c.subject or text, tokens=c.tokens, display_value=float(n),
                display_unit="阶", locator=locator,
            )
    return parse_criterion(text, locator=locator)


# ── document level ───────────────────────────────────────────────────────
_CRITERIA_KEYS = ("acceptance_criteria", "criteria", "acceptance", "judgement_criteria")


def parse_document(doc: Any, ref: str = "") -> Tuple[List[model.Criterion], List[model.Waiver]]:
    """Extract criteria + waivers from a proposal / report JSON document.

    Accepted shapes (all declared by the document itself, nothing inferred):

    * a list of strings
    * ``{"acceptance_criteria": [...]}`` / ``{"criteria": [...]}``
    * ``{"goal": {"criteria": [...]}}`` (proposal v2 shape)
    * ``{"node": {"acceptance_criteria": [...]}}`` (campaign node export)
    * criteria entries that are dicts carrying ``text``/``description``/``statement``

    Waivers: ``{"waivers": [{"criterion"/"target", "reason", "approved_by"}]}``.
    """
    crits: List[model.Criterion] = []
    waivers: List[model.Waiver] = []
    if doc is None:
        return crits, waivers

    if isinstance(doc, str):
        return [parse_with_cn_order(doc, locator=f"{ref}")], waivers

    def push(items: Sequence[Any], base: str) -> None:
        for i, item in enumerate(items):
            loc = f"{base}[{i}]"
            if isinstance(item, str):
                crits.append(parse_with_cn_order(item, locator=loc))
            elif isinstance(item, dict):
                text = (item.get("text") or item.get("description") or item.get("statement")
                        or item.get("criterion") or item.get("title") or "")
                if text:
                    c = parse_with_cn_order(str(text), locator=loc)
                    if item.get("id"):
                        c = model.Criterion(**{**c.as_dict(), "locator": f"{loc}#{item['id']}",
                                               "tokens": tuple(c.tokens), "alternatives": tuple(c.alternatives),
                                               "extras": tuple(c.extras)})
                    crits.append(c)

    def walk(node: Any, base: str) -> None:
        if isinstance(node, list):
            push(node, base)
            return
        if not isinstance(node, dict):
            return
        for key in _CRITERIA_KEYS:
            if key in node:
                push(node[key] if isinstance(node[key], list) else [node[key]], f"{base}.{key}")
                return
        for key in ("goal", "node", "mission", "spec", "report"):
            if isinstance(node.get(key), (dict, list)):
                walk(node[key], f"{base}.{key}")
                return
        ws = node.get("waivers")
        if isinstance(ws, list):
            for i, w in enumerate(ws):
                if isinstance(w, dict):
                    waivers.append(model.Waiver(
                        target=str(w.get("criterion") or w.get("target") or w.get("criterion_id") or ""),
                        reason=str(w.get("reason") or ""),
                        approved_by=str(w.get("approved_by") or w.get("approver") or ""),
                        locator=f"{base}.waivers[{i}]",
                    ))

    walk(doc, ref)
    # waivers may live at top level alongside criteria
    if isinstance(doc, dict) and isinstance(doc.get("waivers"), list):
        for i, w in enumerate(doc["waivers"]):
            if isinstance(w, dict):
                waivers.append(model.Waiver(
                    target=str(w.get("criterion") or w.get("target") or w.get("criterion_id") or ""),
                    reason=str(w.get("reason") or ""),
                    approved_by=str(w.get("approved_by") or w.get("approver") or ""),
                    locator=f"{ref}.waivers[{i}]",
                ))
    return crits, waivers