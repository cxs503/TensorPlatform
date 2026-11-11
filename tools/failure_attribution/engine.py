"""Failure-attribution engine (platform-generic, zero domain semantics).

The engine turns a *mission record* (status + free-text evidence fields) into a
machine-readable attribution ``(stage, kind, rule_id, evidence, confidence)``.

Design notes
------------
* **Config-driven.** The stage/kind vocabulary and every predicate live in
  ``config/failure_attribution.json``; this module contains no vocabulary of its
  own. That keeps the platform layer free of domain semantics and lets other
  repos re-point the table without touching code.
* **Declared beats inferred.** If the record already carries a structured
  stage (``fail_stage`` column or ``result.evidence.checks.fail_stage``) the
  attribution is ``source="declared"`` and no rule is applied. Rules only fill
  the gap (``source="inferred"``); a record that matches nothing is
  ``stage="unknown"``.
* **Deterministic.** Rules are sorted by ``priority`` then ``rule_id``; the
  first match wins. Same input → byte-identical output (no clocks, no RNG).
* **Leak masking.** ``mask_declared_tokens`` removes the literal stage token
  from text before classification, so an accuracy measurement cannot trivially
  re-read the answer out of a headline that embeds it. Used by the
  leak-masked arm of the accuracy experiment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"
UNKNOWN_STAGE = "unknown"
UNKNOWN_KIND = "unclassified"

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "failure_attribution.json"

#: Confidence for each provenance channel. Deliberately coarse and predeclared.
CONFIDENCE = {"declared": 1.0, "inferred": 0.6, "unknown": 0.0}


class ConfigError(ValueError):
    """The rule table itself is invalid (config error ≠ classification failure)."""


@dataclass(frozen=True)
class Mission:
    """Minimal mission view the engine needs (no ORM coupling)."""

    mission_id: Any
    status: str
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    declared_stage: Optional[str] = None
    texts: Mapping[str, str] = None  # field name -> text
    raw: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class Attribution:
    mission_id: Any
    stage: str
    kind: str
    rule_id: Optional[str]
    field: Optional[str]
    snippet: Optional[str]
    source: str
    confidence: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "stage": self.stage,
            "kind": self.kind,
            "rule_id": self.rule_id,
            "field": self.field,
            "snippet": self.snippet,
            "source": self.source,
            "confidence": self.confidence,
        }


FEATURE_OPS = ("eq", "ne", "le", "lt", "ge", "gt", "empty", "nonempty")


@dataclass(frozen=True)
class Rule:
    """One ordered attribution predicate: a text-regex bank *or* a structured
    feature test (never both — the loader rejects mixed entries)."""

    rule_id: str
    stage: str
    kind: str
    priority: int
    fields: Tuple[str, ...]
    patterns: Tuple[re.Pattern, ...]
    reason: str
    feature: Optional[str] = None
    op: Optional[str] = None
    value: Any = None


@dataclass(frozen=True)
class AttributionConfig:
    rules: Tuple[Rule, ...]
    taxonomy: Mapping[str, Sequence[str]]
    text_fields: Mapping[str, Sequence[str]]
    declared_paths: Sequence[str]
    targets: Mapping[str, float]
    window_days: int
    schema_version: str = SCHEMA_VERSION

    def kinds(self) -> Iterable[Tuple[str, str]]:
        for stage, kinds in self.taxonomy.items():
            for kind in kinds:
                yield stage, kind


def _as_float(mapping: Mapping[str, Any], key: str, default: float) -> float:
    val = mapping.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError) as exc:  # pragma: no cover - config guard
        raise ConfigError(f"target {key!r} is not numeric: {val!r}") from exc


def load_config(path: Optional[Path | str] = None) -> AttributionConfig:
    """Load + validate the rule table. Unknown keys are rejected loudly."""
    p = Path(path) if path else _DEFAULT_CONFIG
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"attribution config not found: {p}") from exc

    taxonomy = raw.get("taxonomy") or {}
    if UNKNOWN_STAGE not in taxonomy:
        raise ConfigError("taxonomy must declare the 'unknown' stage")

    declared_kinds = {k for kinds in taxonomy.values() for k in kinds}
    rules: List[Rule] = []
    seen = set()
    for entry in raw.get("rules", []):
        rid = entry.get("rule_id")
        if not rid:
            raise ConfigError("every rule needs a rule_id")
        if rid in seen:
            raise ConfigError(f"duplicate rule_id: {rid}")
        seen.add(rid)
        stage, kind = entry.get("stage"), entry.get("kind")
        if stage not in taxonomy:
            raise ConfigError(f"{rid}: unknown stage {stage!r}")
        if kind not in declared_kinds:
            raise ConfigError(f"{rid}: kind {kind!r} not declared in taxonomy")
        feature = entry.get("feature")
        if feature:
            if entry.get("patterns") or entry.get("fields"):
                raise ConfigError(f"{rid}: a rule is either text or feature, not both")
            op = entry.get("op")
            if op not in FEATURE_OPS:
                raise ConfigError(f"{rid}: feature rule needs op in {FEATURE_OPS}")
            rules.append(
                Rule(
                    rule_id=rid, stage=stage, kind=kind,
                    priority=int(entry.get("priority", 1000)),
                    fields=(), patterns=(), reason=str(entry.get("reason", "")),
                    feature=feature, op=op, value=entry.get("value"),
                )
            )
            continue
        fields = tuple(entry.get("fields") or ())
        if not fields:
            raise ConfigError(f"{rid}: no fields to scan")
        pats = entry.get("patterns") or []
        if not pats:
            raise ConfigError(f"{rid}: no patterns")
        try:
            compiled = tuple(re.compile(p, re.IGNORECASE) for p in pats)
        except re.error as exc:
            raise ConfigError(f"{rid}: bad regex {exc}") from exc
        rules.append(
            Rule(
                rule_id=rid,
                stage=stage,
                kind=kind,
                priority=int(entry.get("priority", 1000)),
                fields=fields,
                patterns=compiled,
                reason=str(entry.get("reason", "")),
            )
        )

    rules.sort(key=lambda r: (r.priority, r.rule_id))
    return AttributionConfig(
        rules=tuple(rules),
        taxonomy={k: tuple(v) for k, v in taxonomy.items()},
        text_fields={k: tuple(v) for k, v in (raw.get("text_fields") or {}).items()},
        declared_paths=tuple(raw.get("declared_paths") or ("fail_stage",)),
        targets={
            "success_rate_target": _as_float(raw.get("targets", {}), "success_rate_target", 0.90),
            "accuracy_target": _as_float(raw.get("targets", {}), "accuracy_target", 0.80),
            "coverage_target": _as_float(raw.get("targets", {}), "coverage_target", 0.95),
            "unknown_rate_max": _as_float(raw.get("targets", {}), "unknown_rate_max", 0.05),
            "min_rule_precision": _as_float(raw.get("targets", {}), "min_rule_precision", 0.80),
            "min_rule_support": _as_float(raw.get("targets", {}), "min_rule_support", 20),
        },
        window_days=int(raw.get("window_days", 7)),
    )


def get_path(payload: Any, dotted: str) -> Any:
    """Resolve a dotted path against nested mappings; ``None`` when absent."""
    cur = payload
    for part in dotted.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def mission_from_row(row: Mapping[str, Any], config: AttributionConfig) -> Mission:
    """Build a :class:`Mission` from a flat CSV row or a DB result mapping.

    ``row`` may carry ``result`` as a dict or a JSON string; the declared stage
    is resolved through ``config.declared_paths`` (first non-empty wins).
    """
    result = row.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result) if result.strip() else {}
        except json.JSONDecodeError:
            result = {}
    if result is None:
        result = {}

    merged: Dict[str, Any] = dict(row)
    merged["result"] = result

    declared = None
    for path in config.declared_paths:
        val = get_path(merged, path)
        if val not in (None, ""):
            declared = str(val).strip()
            break

    texts: Dict[str, str] = {}
    for field, candidates in config.text_fields.items():
        for cand in candidates:
            val = get_path(merged, cand)
            if isinstance(val, str) and val.strip():
                texts[field] = val
                break
    # allow callers/tests to pass pre-extracted text fields directly
    for field in config.text_fields:
        if field in row and isinstance(row[field], str) and row[field].strip():
            texts.setdefault(field, row[field])

    return Mission(
        mission_id=row.get("id", row.get("mission_id")),
        status=str(row.get("status", "")),
        created_at=str(row.get("created_at")) if row.get("created_at") else None,
        finished_at=str(row.get("finished_at")) if row.get("finished_at") else None,
        declared_stage=declared,
        texts=texts,
        raw=merged,
    )


def _snippet(text: str, match: re.Match, width: int = 60) -> str:
    start = max(0, match.start() - width)
    end = min(len(text), match.end() + width)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def classify(
    mission: Mission,
    config: AttributionConfig,
    *,
    mask_declared: bool = False,
    ignore_declared: bool = False,
) -> Attribution:
    """Attribute one mission.

    ``ignore_declared=True`` forces the inference path even when a structured
    stage exists — that is how the held-out accuracy arm is built.
    """
    if mission.declared_stage and not ignore_declared:
        stage, kind = _split_declared(mission.declared_stage, config)
        return Attribution(
            mission_id=mission.mission_id,
            stage=stage,
            kind=kind,
            rule_id=None,
            field="declared",
            snippet=mission.declared_stage,
            source="declared",
            confidence=CONFIDENCE["declared"],
        )

    texts = dict(mission.texts or {})
    if mask_declared:
        drop = {mission.declared_stage} if mission.declared_stage else set()
        texts = {k: mask_label_tokens(v, drop) for k, v in texts.items()}

    for rule in config.rules:
        if rule.feature:
            ok, rendered = _test_feature(mission.raw or {}, rule)
            if ok:
                return Attribution(
                    mission_id=mission.mission_id,
                    stage=rule.stage,
                    kind=rule.kind,
                    rule_id=rule.rule_id,
                    field=rule.feature,
                    snippet=rendered,
                    source="inferred",
                    confidence=CONFIDENCE["inferred"],
                )
            continue
        for field in rule.fields:
            text = texts.get(field)
            if not text:
                continue
            for pat in rule.patterns:
                m = pat.search(text)
                if m:
                    return Attribution(
                        mission_id=mission.mission_id,
                        stage=rule.stage,
                        kind=rule.kind,
                        rule_id=rule.rule_id,
                        field=field,
                        snippet=_snippet(text, m),
                        source="inferred",
                        confidence=CONFIDENCE["inferred"],
                    )
    return Attribution(
        mission_id=mission.mission_id,
        stage=UNKNOWN_STAGE,
        kind=UNKNOWN_KIND,
        rule_id=None,
        field=None,
        snippet=None,
        source="unknown",
        confidence=CONFIDENCE["unknown"],
    )


def _coerce(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return float(text) if "." in text or "e" in text.lower() else int(text)
    except ValueError:
        return text


def _test_feature(payload: Mapping[str, Any], rule: Rule) -> Tuple[bool, Optional[str]]:
    """Evaluate a structured predicate against the raw mission mapping."""
    val = get_path(payload, rule.feature or "")
    rendered = f"{rule.feature} = {val!r} {rule.op} {rule.value!r}"
    if val is None or val == "":
        return (True, rendered) if rule.op == "empty" else (False, rendered)
    if rule.op == "empty":
        return False, rendered
    if rule.op == "nonempty":
        return True, rendered
    left, right = _coerce(val), _coerce(rule.value)
    try:
        ops = {
            "eq": left == right,
            "ne": left != right,
            "le": left <= right,
            "lt": left < right,
            "ge": left >= right,
            "gt": left > right,
        }
    except TypeError:
        return False, rendered
    return bool(ops[rule.op]), rendered


def _split_declared(declared: str, config: AttributionConfig) -> Tuple[str, str]:
    """Map a declared stage token onto ``(stage, kind)``.

    Declared tokens may be compound (``no_progress+forced_finish``); the first
    known token wins, unknown tokens fall back to the ``unknown`` stage but keep
    the raw token as the kind so no information is dropped.
    """
    tokens = [t.strip() for t in re.split(r"[+,;|/]", declared) if t.strip()]
    for token in tokens:
        for stage, kinds in config.taxonomy.items():
            if token in kinds:
                return stage, token
    return UNKNOWN_STAGE, tokens[0] if tokens else UNKNOWN_KIND


def mask_label_tokens(text: str, tokens: Iterable[str]) -> str:
    """Remove declared stage tokens (and parenthesised ``终局: X``) from text."""
    out = text
    for tok in tokens:
        if not tok:
            continue
        out = out.replace(tok, "<masked>")
        out = re.sub(r"[（(]\s*(?:终局|terminal)\s*[:：]\s*" + re.escape(tok) + r"\s*[)）]", " ", out)
    return out


def evaluate(
    missions: Sequence[Mission],
    config: AttributionConfig,
    *,
    mask_declared: bool = True,
) -> Dict[str, Any]:
    """Run the engine over a cohort and report coverage + held-out accuracy.

    Accuracy arm: missions with a declared stage are re-classified **from text
    only** (declared field hidden, and by default its literal token masked out
    of the text). ``mask_declared=False`` gives the optimistic upper bound.
    """
    attributed: List[Attribution] = [classify(m, config) for m in missions]
    by_stage: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for a in attributed:
        by_stage[a.stage] = by_stage.get(a.stage, 0) + 1
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        by_source[a.source] = by_source.get(a.source, 0) + 1

    n = len(missions)
    attributed_n = sum(1 for a in attributed if a.source != "unknown")
    leaked = [m for m in missions if m.declared_stage]
    hits = 0
    exact_hits = 0
    confusion: Dict[str, Dict[str, int]] = {}
    for m in leaked:
        pred = classify(m, config, ignore_declared=True, mask_declared=mask_declared)
        truth_stage, truth_kind = _split_declared(m.declared_stage or "", config)
        row = confusion.setdefault(truth_stage, {})
        row[pred.stage] = row.get(pred.stage, 0) + 1
        hits += int(pred.stage == truth_stage)
        exact_hits += int(pred.kind == truth_kind)

    return {
        "cohort": n,
        "attributed": attributed_n,
        "coverage": _ratio(attributed_n, n),
        "unknown": n - attributed_n,
        "unknown_rate": _ratio(n - attributed_n, n),
        "by_stage": dict(sorted(by_stage.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_source": dict(sorted(by_source.items())),
        "heldout": {
            "n": len(leaked),
            "mask_declared": mask_declared,
            "stage_accuracy": _ratio(hits, len(leaked)),
            "kind_accuracy": _ratio(exact_hits, len(leaked)),
            "confusion": {k: dict(sorted(v.items())) for k, v in sorted(confusion.items())},
        },
        "rule_stats": rule_stats(missions, config, mask_declared=mask_declared),
        "attributions": [a.as_dict() for a in attributed],
    }


def _ratio(num: int, den: int) -> Optional[float]:
    return None if den == 0 else round(num / den, 6)


def rule_stats(
    missions: Sequence[Mission],
    config: AttributionConfig,
    *,
    mask_declared: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Per-rule precision measured on the labelled cohort (text-only arm).

    A rule that never fires on labelled data has ``support=0`` → precision is
    ``None`` ("unvalidated"), which callers must treat as *not trusted* rather
    than as passing.
    """
    stats: Dict[str, Dict[str, Any]] = {}
    for m in missions:
        if not m.declared_stage:
            continue
        pred = classify(m, config, ignore_declared=True, mask_declared=mask_declared)
        rid = pred.rule_id or "UNKNOWN"
        truth_stage, _ = _split_declared(m.declared_stage, config)
        row = stats.setdefault(rid, {"support": 0, "correct": 0, "pred_stage": pred.stage})
        row["support"] += 1
        row["correct"] += int(pred.stage == truth_stage)
    for row in stats.values():
        row["precision"] = round(row["correct"] / row["support"], 6) if row["support"] else None
    return {k: stats[k] for k in sorted(stats)}


def emit_backfill_sql(attributions: Sequence[Attribution]) -> str:
    """Render idempotent UPDATEs for missions still missing ``fail_stage``.

    The statement never overwrites a non-empty declared stage. Emitted, not
    executed: production writes are a governance decision, not an agent default.
    """
    lines = [
        "-- generated by tools.failure_attribution.emit_backfill_sql",
        "-- idempotent: only rows with an empty fail_stage are touched",
    ]
    for a in attributions:
        if a.source == "unknown":
            continue
        token = a.kind if a.stage != UNKNOWN_STAGE else None
        if not token:
            continue
        mid = str(a.mission_id).replace("'", "''")
        tok = token.replace("'", "''")
        lines.append(
            f"UPDATE tensor_dev_missions SET fail_stage = '{tok}' "
            f"WHERE id = {mid} AND (fail_stage IS NULL OR fail_stage = '');"
        )
    return "\n".join(lines) + "\n"