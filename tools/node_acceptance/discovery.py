"""Criterion discovery: find the acceptance criteria declared inside artifacts.

Real node artifact directories are heterogeneous.  Three declaration shapes are
recognised, all of them *declared by the artifact itself* (the engine never
invents a threshold):

* ``criteria: [...]``  - list of descriptors (``description``/``target`` text).
* ``anchors: [...]``   - list of check records whose ``description`` carries the
  numeric assertion (``... <= 1e-12``).
* ``acceptance_criteria`` / ``criteria.json`` at the top level.

Declarations that carry no comparator/number are returned as unjudgeable rows
so the report can name them instead of silently dropping them.
"""
from __future__ import annotations

from typing import Any, Iterable

from .criteria import error_leaf_rank, load_lexicon, parse_criteria, parse_criterion
from .model import Criterion, NodeEvidence

_LIST_KEYS = ("criteria", "anchors", "acceptance_criteria", "requirements")
_TEXT_FIELDS = ("target", "criterion", "criterion_text", "description", "requirement", "text")


def _text_of(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for f in _TEXT_FIELDS:
            v = item.get(f)
            if isinstance(v, str) and any(ch.isdigit() for ch in v):
                return v
        for f in _TEXT_FIELDS:
            v = item.get(f)
            if isinstance(v, str) and v.strip():
                return v
    return None


def discover_criteria(evidence: NodeEvidence, lex: dict | None = None) -> list[Criterion]:
    """Return the criteria declared by this node's own artifacts."""
    lex = lex or load_lexicon()
    found: list[Criterion] = []
    seen: set[tuple[str, str]] = set()

    def _push(crits: Iterable[Criterion]) -> None:
        for crit in crits:
            sig = (crit.raw, crit.scope)
            if sig in seen:
                continue
            seen.add(sig)
            found.append(crit)

    from .scanner import role_of

    allow = tuple(lex.get("criteria_role_allowlist", ("validation_report", "criteria")))
    for source, doc in evidence.documents:
        if allow and role_of(evidence, source) not in allow:
            continue
        if isinstance(doc, dict):
            for key in _LIST_KEYS:
                items = doc.get(key)
                if isinstance(items, list):
                    _push(parse_criteria(items, scope=f"{source}::{key}", source=source,
                                         pointer=key, lexicon=lex))
            # a single flat declaration: {"criterion": "...", "tolerance": 1e-3}
            if not any(isinstance(doc.get(k), list) for k in _LIST_KEYS):
                flat_text = _text_of(doc)
                if flat_text:
                    _push(parse_criterion(flat_text, scope=f"{source}::", source=source,
                                          source_pointer="", lexicon=lex))
        elif isinstance(doc, list):
            _push(parse_criteria(doc, scope=f"{source}::", source=source, pointer="items", lexicon=lex))

    return found


_PROSE_FIELDS = ("note", "detail", "reason", "comment", "convention", "explanation", "expected", "claim", "description")
_DEVIATION_FLAGS = ("passed", "holds", "ok", "pass", "inside_target_band", "within", "verified", "accepted")
_UP_WORDS = ("more than", "greater than", "exceed", "larger than", "at least", "above", "大于", "超过", "不少于", "不低于")
_DOWN_WORDS = ("not exceed", "no more than", "less than", "below", "within", "at most",
               "不超过", "以内", "小于", "不高于")


def records_by_scope(evidence: NodeEvidence) -> dict[str, dict]:
    """Map ``source::dotted-pointer`` -> record dict for every dict in the artifacts."""
    out: dict[str, dict] = {}

    def walk(node: Any, path: str, source: str) -> None:
        if isinstance(node, dict):
            if path:
                out[f"{source}::{path}"] = node
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k), source)
        elif isinstance(node, list):
            for i, v in enumerate(node[:200]):
                walk(v, f"{path}[{i}]", source)

    for source, doc in evidence.documents:
        walk(doc, "", source)
    return out


_DIRECTION_FIELDS = ("expected", "claim", "requirement", "target", "criterion", "description")


def _direction_from_prose(record: dict, lex: dict) -> str:
    """Read the comparison direction the record itself *asserts* ('more than' ...).

    Only assertion-bearing fields are read.  Explanatory prose (``note`` /
    ``detail`` / ``reason``) frequently quotes other numbers, so reading it here
    used to invert verdicts; it is now excluded.
    """
    text = " ".join(str(record.get(k, "")) for k in _DIRECTION_FIELDS).lower()
    up = any(w in text for w in _UP_WORDS)
    down = any(w in text for w in _DOWN_WORDS)
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    return "undeclared"


def _human_flag(record: dict, lex: dict) -> bool | None:
    """Any human-authored success flag in the record: True / False / None."""
    flags: list[bool] = []
    for k in _DEVIATION_FLAGS:
        v = record.get(k)
        if isinstance(v, bool):
            flags.append(v)
        elif isinstance(v, dict):
            flags.extend(x for x in v.values() if isinstance(x, bool))
    if "verdict" in record or "status" in record:
        from .model import Verdict as _V, classify_human_token

        token = record.get("verdict", record.get("status"))
        v = classify_human_token(token)
        if v is _V.PASS:
            flags.append(True)
        elif v is _V.FAIL:
            flags.append(False)
    if not flags:
        return None
    return any(flags)


def _deviation_documented(record: dict, lex: dict | None = None) -> bool:
    """True when the record flags success *and* explains a deviation in prose."""
    if _human_flag(record, lex or load_lexicon()) is not True:
        return False
    prose = [record.get(k) for k in _PROSE_FIELDS
             if isinstance(record.get(k), str) and len(record.get(k, "")) > 40]
    return bool(prose)


def discover_declared_checks(
    evidence: NodeEvidence,
    lex: dict | None = None,
    covered_scopes: Iterable[str] = (),
) -> list[Criterion]:
    """Synthesise criteria from record-local (tolerance, value) pairs.

    Some artifacts never spell the criterion out in prose; instead each record
    carries its own ``tolerance`` next to its value (``*_error``, ``*_residual``,
    ``*_diff``).  That pair *is* the declared criterion, so it is lifted
    verbatim - the engine still invents no threshold, it copies the artifact's
    own number.  Two extra facts are read from the same record:

    * **direction**: prose such as "differ by *more than* 1e-3" declares a
      lower bound, not an upper bound; ignoring it inverts the verdict.
    * **documented deviation**: a true flag *plus* explanatory prose means the
      author knowingly accepted a number outside the band.  Such a criterion is
      judged ``MECHANICAL_CONFLICT`` (curation required), never silently PASS.
    """
    from .model import Comparator

    lex = lex or load_lexicon()
    tol_keys = tuple(k.lower() for k in lex.get("tolerance_keys", []))
    err_words = tuple(w.lower() for w in lex["metric_hints"]["error"])
    covered = set(covered_scopes)

    out: list[Criterion] = []
    for scope, record in records_by_scope(evidence).items():
        if not scope or scope in covered:
            continue
        tol = None
        errs: list[tuple[str, float]] = []
        for k, v in record.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            kl = str(k).lower()
            if kl in tol_keys:
                tol = abs(float(v))
            elif any(w in kl for w in err_words):
                errs.append((str(k), float(v)))
        if tol is None or not errs:
            continue
        best_rank = min(error_leaf_rank(k, lex) for k, _ in errs)
        cand = [(k, v) for k, v in errs if error_leaf_rank(k, lex) == best_rank]
        leaf, _ = max(cand, key=lambda kv: abs(kv[1]))
        source, _, record_ptr = scope.partition("::")
        record_ptr = record_ptr or scope
        value_ptr = f"{record_ptr}.{leaf}" if record_ptr else leaf
        notes = ["declared_tolerance_check"]
        direction = _direction_from_prose(record, lex)
        notes.append(f"direction:{direction}")
        flag = _human_flag(record, lex)
        if flag is None:
            # the tolerance/value association is inferred by the engine, not
            # declared: a violation of it may never be issued as FAIL
            notes.append("association_inferred")
        if _deviation_documented(record, lex):
            notes.append("deviation_documented")
        out.append(Criterion(
            raw=(f"declared check: {leaf} {'>=' if direction == 'up' else ('<=' if direction == 'down' else '?')} "
                 f"{tol:g} (record-local tolerance{', direction undeclared' if direction == 'undeclared' else ''})"),
            comparator=Comparator.GE if direction == "up" else Comparator.LE,
            threshold=tol,
            metric_name=value_ptr,
            hint="error",
            scope=scope,
            source=source,
            source_pointer=value_ptr,
            parse_confidence=0.8,
            notes=tuple(notes),
        ))
    return out


def discover_all(evidence: NodeEvidence, lex: dict | None = None) -> list[Criterion]:
    """Text-declared criteria first, then record-local declared tolerances."""
    lex = lex or load_lexicon()
    crits = discover_criteria(evidence, lex)
    covered = {c.scope for c in crits if c.judgeable}
    declared = discover_declared_checks(evidence, lex, covered_scopes=covered)
    if declared:
        # a prose row that only repeats a record which already declares its own
        # tolerance adds noise, not evidence: keep the numeric declaration
        declared_scopes = {c.scope for c in declared}
        crits = [c for c in crits if c.judgeable or c.scope not in declared_scopes]
    crits.extend(declared)
    return crits


def discover_from_items(items: Iterable[Any], source: str = "cli", lex: dict | None = None) -> list[Criterion]:
    lex = lex or load_lexicon()
    return parse_criteria(items, source=source, lexicon=lex)