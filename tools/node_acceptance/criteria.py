"""Criterion parser: text -> machine-judgeable :class:`Criterion` objects.

Three text shapes are handled, all domain-neutral:

1. **Identifier form** - ``near_singular_ratio > 0.05``, ``max_diff <= 1e-12``.
   The identifier is the strongest possible anchor: it names the metric that
   must be looked up in the artifact.
2. **Hint form** - ``耗时≤30s``, ``一致率≥4/5``, ``误差<3%``, ``≥12项全绿``.
   No identifier is available, so a lexicon hint (error / duration / ...) is
   attached and the aggregator resolves the value from derived evidence.
3. **Presence form** - ``报告包含六要素`` / ``includes elements``.

Whatever cannot be pinned to a comparator plus a number is returned with
``comparator=NONE`` or ``APPROX`` so that it is *not* silently judged; the
aggregator reports it as UNKNOWN and the report names it as a caveat.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Sequence

from .model import Comparator, Criterion

LEXICON_PATH = Path(__file__).resolve().parent / "lexicon.json"

_OP_MAP = {
    "≥": Comparator.GE, ">=": Comparator.GE, "不低于": Comparator.GE, "至少": Comparator.GE,
    "不低於": Comparator.GE, "以上": Comparator.GE,
    "≤": Comparator.LE, "<=": Comparator.LE, "不超过": Comparator.LE, "不高于": Comparator.LE, "至多": Comparator.LE, "以下": Comparator.LE,
    ">": Comparator.GT, "超过": Comparator.GT, "大于": Comparator.GT,
    "<": Comparator.LT, "小于": Comparator.LT,
    "==": Comparator.EQ, "=": Comparator.EQ, "等于": Comparator.EQ,
    "≈": Comparator.APPROX, "~=": Comparator.APPROX, "约": Comparator.APPROX,
}

_OP_ALT = sorted(_OP_MAP, key=len, reverse=True)
_OP_RE = "|".join(re.escape(o) for o in _OP_ALT)
_NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_UNIT = r"(ms|s|sec|分钟|秒|小时|min|h|%|项|个|条|cases?|tests?|倍)"

_IDENT_THRESH = re.compile(
    rf"(?P<name>[A-Za-z_][A-Za-z0-9_]{{2,}}(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\[\d+\])?)"
    rf"\s*(?P<op>{_OP_RE})\s*(?P<num>{_NUM})\s*(?P<unit>{_UNIT})?"
)
_GENERIC_THRESH = re.compile(
    rf"(?P<op>{_OP_RE})\s*(?P<num>{_NUM})\s*(?P<unit>{_UNIT})?"
)
_RATIO = re.compile(r"(?P<a>\d+)\s*/\s*(?P<b>\d+)")
_PERCENT_PLAIN = re.compile(rf"(?P<num>{_NUM})\s*%")

_CACHE: dict[str, dict] = {}


def load_lexicon(path: str | Path | None = None) -> dict:
    """Load (and memoise) the metric-naming lexicon."""
    key = str(path or LEXICON_PATH)
    if key not in _CACHE:
        _CACHE[key] = json.loads(Path(key).read_text(encoding="utf-8"))
    return _CACHE[key]


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _normalise_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip().lower()
    mapping = {"sec": "s", "seconds": "s", "second": "s", "秒": "s", "分钟": "min", "min": "min",
               "hour": "h", "hours": "h", "小时": "h", "%": "%", "项": "count", "个": "count",
               "条": "count", "case": "count", "cases": "count", "test": "count", "tests": "count",
               "倍": "x"}
    return mapping.get(u, u)


def detect_hint(text: str, lexicon: dict | None = None) -> str | None:
    """Return the lexicon hint whose vocabulary best matches ``text``."""
    lex = lexicon or load_lexicon()
    low = text.lower()
    best: tuple[int, str] | None = None
    for hint, words in lex["metric_hints"].items():
        for w in words:
            wl = w.lower()
            if wl and wl in low:
                if best is None or len(wl) > best[0]:
                    best = (len(wl), hint)
    return best[1] if best else None


def error_leaf_rank(leaf: str, lexicon: dict | None = None) -> int:
    """Rank an error-like leaf name; lower is better (more specific)."""
    lex = lexicon or load_lexicon()
    low = leaf.lower()
    for i, term in enumerate(lex.get("error_precedence", [])):
        if term in low:
            return i
    return 999


def split_segments(text: str, lexicon: dict | None = None) -> list[str]:
    """Split a compound criterion on configured connectors (keeps order)."""
    lex = lexicon or load_lexicon()
    parts = [text]
    for sep in lex.get("connectors", []):
        nxt: list[str] = []
        for p in parts:
            nxt.extend(p.split(sep))
        parts = nxt
    return [p.strip() for p in parts if p.strip()]


def parse_criterion(
    text: str,
    scope: str = "",
    source: str = "",
    source_pointer: str = "",
    lexicon: dict | None = None,
) -> list[Criterion]:
    """Parse one criterion string into zero or more :class:`Criterion` rows."""
    lex = lexicon or load_lexicon()
    if not isinstance(text, str) or not text.strip():
        return []
    out: list[Criterion] = []
    segments = split_segments(text, lex) or [text]
    for seg in segments:
        out.extend(_parse_segment(seg, text, scope, source, source_pointer, lex))
    if not out:
        out.append(Criterion(raw=text.strip(), comparator=Comparator.NONE, scope=scope, source=source,
                             source_pointer=source_pointer, hint=detect_hint(text, lex),
                             parse_confidence=0.0, notes=("no_comparator_found",)))
    return out


def _ordered(crits: list[Criterion]) -> list[Criterion]:
    """Numeric assertions first, boolean/presence assertions second."""
    return sorted(crits, key=lambda c: 0 if c.comparator not in (Comparator.ALL_TRUE, Comparator.PRESENT) else 1)


def _parse_segment(seg: str, full: str, scope: str, source: str, pointer: str, lex: dict) -> list[Criterion]:
    made: list[Criterion] = []
    low = seg.lower()
    base = dict(raw=seg.strip(), scope=scope, source=source, source_pointer=pointer)

    # --- boolean forms ---------------------------------------------------
    if any(w.lower() in low for w in lex["metric_hints"]["all_true"]):
        made.append(Criterion(comparator=Comparator.ALL_TRUE, hint="all_true",
                              parse_confidence=0.9, **base))
    # --- presence forms --------------------------------------------------
    if any(w.lower() in low for w in lex["metric_hints"]["element"]):
        made.append(Criterion(comparator=Comparator.PRESENT, hint="element",
                              metric_name="elements", parse_confidence=0.8, **base))

    # --- ratio form "≥4/5" ----------------------------------------------
    ratio_match = _RATIO.search(seg)
    has_op = re.search(_OP_RE, seg) is not None
    if ratio_match:
        a, b = int(ratio_match.group("a")), int(ratio_match.group("b"))
        if b > 0 and a <= b:
            made.append(Criterion(comparator=Comparator.RATIO_GE, ratio=(a, b), hint=detect_hint(seg, lex),
                                  parse_confidence=0.85, notes=("ratio_form",), **base))
            return _ordered(made)  # ratio consumes the numbers

    # --- identifier form -------------------------------------------------
    ident = _IDENT_THRESH.search(seg)
    if ident and _to_float(ident.group("num")) is not None:
        op = _OP_MAP.get(ident.group("op"), Comparator.NONE)
        made.append(Criterion(
            comparator=op,
            threshold=_to_float(ident.group("num")),
            unit=_normalise_unit(ident.group("unit")),
            metric_name=ident.group("name"),
            hint=detect_hint(seg, lex),
            parse_confidence=0.95 if op is not Comparator.APPROX else 0.5,
            notes=("identifier_form",) if op is not Comparator.APPROX else ("approx_without_tolerance",),
            **base))
        return _ordered(made)

    # --- generic operator + number --------------------------------------
    gen = _GENERIC_THRESH.search(seg)
    if gen and _to_float(gen.group("num")) is not None:
        op = _OP_MAP.get(gen.group("op"), Comparator.NONE)
        made.append(Criterion(
            comparator=op,
            threshold=_to_float(gen.group("num")),
            unit=_normalise_unit(gen.group("unit")),
            hint=detect_hint(seg, lex),
            parse_confidence=0.85 if gen.group("unit") else 0.8,
            notes=("generic_form",) if any(x.comparator is not Comparator.ALL_TRUE for x in made) else (),
            **base))
        return _ordered(made)

    # --- bare percentage "误差 3%" --------------------------------------
    pct = _PERCENT_PLAIN.search(seg)
    if pct and _to_float(pct.group("num")) is not None and any(
            w in low for w in lex["metric_hints"]["error"]):
        made.append(Criterion(comparator=Comparator.LE, threshold=_to_float(pct.group("num")), unit="%",
                              hint="error", parse_confidence=0.5,
                              notes=("bare_percent_interpreted_as_upper_bound",), **base))
        return _ordered(made)

    if made:
        # numeric assertions are the primary content of a criterion; boolean
        # ("all green") parts come after so that callers see the number first
        return _ordered(made)

    # nothing numeric: keep an unjudgeable row with the hint attached
    fallback_hint = detect_hint(full, lex)
    if fallback_hint == "all_true":
        made.append(Criterion(comparator=Comparator.ALL_TRUE, hint=fallback_hint, parse_confidence=0.7,
                              notes=("boolean_form_without_number",), **base))
        return made
    made.append(Criterion(comparator=Comparator.NONE, hint=fallback_hint, parse_confidence=0.0,
                          notes=("no_comparator_found",), **base))
    return made


def parse_criteria(items: Iterable, scope: str = "", source: str = "", pointer: str = "",
                   lexicon: dict | None = None) -> list[Criterion]:
    """Parse a heterogeneous list of criterion descriptors.

    ``items`` may contain plain strings, or mapping/dict-shaped descriptors with
    a text-bearing field (``criterion``/``description``/``target``/``text``/
    ``criterion_text``).  Dictionaries additionally act as *scope anchors*: the
    declaration point is remembered so that observations can be resolved inside
    the same record.
    """
    lex = lexicon or load_lexicon()
    out: list[Criterion] = []
    text_fields = ("criterion", "criterion_text", "target", "description", "text", "requirement")
    for idx, item in enumerate(items):
        if isinstance(item, str):
            out.extend(parse_criterion(item, scope=scope, source=source,
                                       source_pointer=f"{pointer}[{idx}]", lexicon=lex))
            continue
        if isinstance(item, dict):
            item_scope = f"{scope}[{idx}]" if scope else f"[{idx}]"
            text = None
            for f in text_fields:
                v = item.get(f)
                if isinstance(v, str) and re.search(r"[0-9]", v):
                    text = v
                    break
            if text is None:
                for f in text_fields:
                    v = item.get(f)
                    if isinstance(v, str) and v.strip():
                        text = v
                        break
            if text is None:
                continue
            # "target" carries the numeric assertion while "description" carries
            # the prose; prefer target, but keep the description as a hint source.
            hint_text = text + " " + " ".join(str(item.get(f, "")) for f in text_fields)
            out.extend(parse_criterion(text, scope=item_scope, source=source,
                                       source_pointer=f"{pointer}[{idx}]", lexicon=lex))
            if out and out[-1].hint is None:
                h = detect_hint(hint_text, lex)
                if h:
                    out[-1] = _replace_hint(out[-1], h)
            continue
    return out


def _replace_hint(c: Criterion, hint: str) -> Criterion:
    from dataclasses import replace

    return replace(c, hint=hint)


def parse_criteria_text(text: str, source: str = "", lexicon: dict | None = None) -> list[Criterion]:
    """Parse multi-line criterion text (one criterion per non-empty line)."""
    lex = lexicon or load_lexicon()
    out: list[Criterion] = []
    for ln, line in enumerate(text.splitlines()):
        line = line.strip().lstrip("-*0123456789.)、 ").strip()
        if not line:
            continue
        out.extend(parse_criterion(line, source=source, source_pointer=f"line[{ln}]", lexicon=lex))
    return out