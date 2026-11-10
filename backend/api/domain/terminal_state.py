"""Semantic terminal-state taxonomy + delivery label (P93W3-N10).

Purpose
-------
The storage status of a mission is binary where it matters (``succeeded`` vs the
non-success states), so a mission that was *forced* to close — budget spent,
wall clock spent, no progress — was stored as ``succeeded`` and was therefore
indistinguishable from a mission that actually finished its work. Every
autonomous mechanism downstream (refill, merge gate, grading, KPI) reads that
flag, so a forced closure silently inflates the success rate.

This module derives a **semantic** terminal state on top of the stored facts::

    succeeded      normal completion, artifact facts complete, closure clean
    forced_finish  closure was forced (budget/wall/progress exhausted)
    degraded       closed without a forced marker but artifacts are incomplete
    failed         non-success storage status (or unclassified -> fail-closed)

and attaches a machine-checkable **delivery label**::

    {completeness: 0..1, quality_gate: pass|fail, budget_status: normal|warning|exhausted}

Design rules
------------
* **Derived, never stored.** The DB status CHECK constraint and the cross-repo
  ``mission_status_vocab`` contract are untouched (a storage enum change would
  require a migration plus a coordinated TensorPhysics bump). The semantic layer
  is a deterministic function of stored facts, so it is backward compatible by
  construction; ``migrate_legacy_succeeded`` back-fills it onto legacy rows and
  keeps the original status verbatim under ``legacy_status``.
* **Fact-based, never self-reported.** Classification reads only structural
  facts: the storage status, ``fail_stage`` closure markers, budget plan vs
  actual usage, and artifact presence via the shared normaliser in
  :mod:`api.domain.delivery_state`. No prose interpretation.
* **Zero domain semantics.** Generic mission/budget vocabulary only.
* **Fail-closed.** An unknown/unparseable record classifies as ``failed`` and a
  non-terminal storage status raises :class:`NonTerminalRecord` instead of
  inventing a verdict.
* **One threshold source.** ``0.70`` is read from the contract file and is the
  same number the 70% MVD checkpoint uses
  (:data:`api.domain.mvd_checkpoint.BUDGET_THRESHOLD`) — the test suite pins the
  two together so they can never drift.

Audit chain
-----------
Consumed by:
- ``api/domain/mvd_checkpoint.py`` (70% checkpoint shares the budget thresholds)
- ``backend/api/kpi_delivery.py`` style KPI views (four-state denominators)
- ``tools/p93w3_n10/evaluate.py`` + ``docs/p93w3-n10/REPORT.md`` (measured corpus)

Machine-verifiable acceptance criteria (see ``tools/p93w3_n10/acceptance.py``):
- (a) four-state classification accuracy 1.00 on the 20-scenario matrix
- (b) 70% MVD checkpoint trigger latency p100 <= 1 s
- (c) backward-compatibility migration of legacy ``succeeded`` rows loses no data
- (d) delivery-label schema validation pass rate 1.00
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from api.domain.delivery_state import (
    assess_attested_delivery,
    normalize_artifacts,
)
from api.domain import mvd_checkpoint as _mvd

TerminalState = Literal["succeeded", "forced_finish", "degraded", "failed"]
BudgetStatus = Literal["normal", "warning", "exhausted"]
QualityGate = Literal["pass", "fail"]

_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "terminal_state_vocab.v1.json"
_LABEL_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "delivery_label.v1.json"

#: Four semantic terminal states, in contract precedence order (first match wins).
TERMINAL_STATES: tuple[str, ...] = ("failed", "forced_finish", "degraded", "succeeded")

_CONTRACT_CACHE: dict[str, Any] | None = None


class NonTerminalRecord(ValueError):
    """Raised when a record is not in a terminal storage status.

    A live mission has no terminal semantics yet; guessing one would be a
    fabrication, so the caller must decide (``allow_non_terminal=True`` is only
    for what-if analysis and marks the result ``failed``-independent, see
    :func:`classify_terminal_state`).
    """


def contract() -> dict[str, Any]:
    """Load (and cache) the terminal-state vocabulary contract."""
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is None:
        data = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        if data.get("contract") != "terminal_state_vocab":
            raise ValueError(f"{_CONTRACT_PATH.name}: unexpected contract field")
        _CONTRACT_CACHE = data
    return _CONTRACT_CACHE


def delivery_label_schema() -> dict[str, Any]:
    """Load the delivery-label JSON Schema (contract file, single source)."""
    return json.loads(_LABEL_SCHEMA_PATH.read_text(encoding="utf-8"))


def _failed_statuses() -> tuple[str, ...]:
    return tuple(contract()["failed_statuses"])


def _marker_token() -> str:
    return str(contract()["forced_marker_token"])


def budget_thresholds() -> dict[str, float]:
    """``{"warning", "exhausted", "mvd_checkpoint"}`` from the contract."""
    return {k: float(v) for k, v in contract()["budget_thresholds"].items()}


def _weights() -> dict[str, float]:
    return {k: float(v) for k, v in contract()["completeness_weights"].items()}


# --------------------------------------------------------------------------- #
# Budget facts
# --------------------------------------------------------------------------- #
def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_seconds(value: Any) -> float | None:
    """Parse an ISO-8601 timestamp into epoch seconds (None when unparseable)."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def budget_utilization(record: dict[str, Any] | None) -> float | None:
    """Fraction of the planned budget already spent (max of the two dimensions).

    Only stored facts are used: ``record["budget"]`` (the plan) versus
    ``result["tool_calls"]`` (the actual) and ``created_at`` -> ``finished_at``
    (the wall span). A closure marker raises the value to at least ``1.0`` so the
    arithmetic and the marker agree. This is a *reporting* number; exhaustion is
    decided by :func:`budget_status` from evidence (see
    :func:`budget_dimensions`).

    Examples
    --------
    >>> budget_utilization({"budget": {"max_tool_calls": 100},
    ...                     "result": {"tool_calls": 70}})
    0.7
    >>> budget_utilization({"budget": {}, "result": {}}) is None
    True
    """
    return budget_dimensions(record)["utilization"]


def budget_dimensions(record: dict[str, Any] | None) -> dict[str, Any]:
    """Per-dimension budget facts used as *evidence* (not just a ratio).

    ``tool_at_cap`` is accepted as exhaustion evidence: the tool-call counter is
    hard-enforced by the harness, so hitting the cap means work stopped at the
    cap. ``wall_at_cap`` is reported (and feeds the warning band) but is **not**
    evidence by itself: measured on the frozen corpus, wall arithmetic
    (``finished_at - created_at``) crosses 1.0 for a large share of rows whose
    missions were never force-closed (queue wait and soft wall budgets are
    inside that span), while every harness-set ``wall_exhausted`` marker is
    inside it — high recall, low precision. Only the marker proves forced
    closure. Numbers: ``runs/p93w3-n10/metrics.json`` -> ``budget_evidence``.

    Examples
    --------
    >>> d = budget_dimensions({"budget": {"max_tool_calls": 10, "max_wall_seconds": 100},
    ...                        "result": {"tool_calls": 10}})
    >>> d["tool_at_cap"], d["tool_ratio"]
    (True, 1.0)
    """
    if not isinstance(record, dict):
        return {"tool_ratio": None, "wall_ratio": None, "tool_at_cap": False,
                "wall_at_cap": False, "utilization": None, "marker_exhaustion": False,
                "closure_cause": None}
    budget = record.get("budget") if isinstance(record.get("budget"), dict) else {}
    result = record.get("result") if isinstance(record.get("result"), dict) else {}

    tool_ratio = None
    max_tools = _as_float(budget.get("max_tool_calls"))
    tools = _as_float(result.get("tool_calls"))
    if max_tools and tools is not None and max_tools > 0:
        tool_ratio = round(tools / max_tools, 4)

    wall_ratio = None
    max_wall = _as_float(budget.get("max_wall_seconds"))
    start = _as_seconds(record.get("created_at"))
    end = _as_seconds(record.get("finished_at")) or _as_seconds(record.get("heartbeat_at"))
    if max_wall and start is not None and end is not None and max_wall > 0:
        wall_ratio = round(max(0.0, end - start) / max_wall, 4)

    ratios = [r for r in (tool_ratio, wall_ratio) if r is not None]
    cause = closure_cause(record)
    utilization = max(ratios) if ratios else None
    if cause in ("budget", "wall"):        # a marker is proof; keep the ratio consistent
        utilization = max(utilization or 0.0, 1.0)
    return {
        "tool_ratio": tool_ratio,
        "wall_ratio": wall_ratio,
        "tool_at_cap": tool_ratio is not None and tool_ratio >= 1.0,
        "wall_at_cap": wall_ratio is not None and wall_ratio >= 1.0,
        "utilization": None if utilization is None else round(utilization, 4),
        "marker_exhaustion": cause in ("budget", "wall"),
        "closure_cause": cause,
    }


def closure_cause(record: dict[str, Any] | None) -> str | None:
    """Closure cause token from ``fail_stage``: ``budget``/``wall``/``progress``.

    Returns ``None`` when the record carries no forced-closure marker at all.
    ``"unknown"`` means the marker exists but names no known cause.
    """
    if not isinstance(record, dict):
        return None
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    stage = str(result.get("fail_stage") or "").strip().lower()
    if not stage:
        return None
    causes = contract()["closure_causes"]
    for cause, tokens in causes.items():
        if cause == "unknown":
            continue
        for token in tokens:
            if token and token in stage:
                return cause
    return "unknown" if _marker_token() in stage else None


def has_forced_marker(record: dict[str, Any] | None) -> bool:
    """True iff the record carries a forced-closure marker (any cause)."""
    return closure_cause(record) is not None


def budget_status(record: dict[str, Any] | None) -> tuple[BudgetStatus, float | None]:
    """``(budget_status, utilization)`` for a record.

    ``normal`` < warning(0.70) <= ``warning`` < exhausted(1.00) <= ``exhausted``.
    Exhaustion requires *evidence*: a forced-closure marker, or the hard-enforced
    tool-call counter at its cap. Wall arithmetic alone reaches no further than
    ``warning`` — measured on the frozen corpus it fires on far more rows than
    were ever force-closed (see :func:`budget_dimensions`).

    Examples
    --------
    >>> budget_status({"budget": {"max_tool_calls": 100}, "result": {"tool_calls": 10}})[0]
    'normal'
    >>> budget_status({"budget": {"max_tool_calls": 100}, "result": {"tool_calls": 75}})[0]
    'warning'
    >>> budget_status({"budget": {"max_tool_calls": 100}, "result": {"tool_calls": 100}})[0]
    'exhausted'
    >>> budget_status({"budget": {"max_tool_calls": 100}, "result":
    ...     {"tool_calls": 20, "fail_stage": "wall_exhausted+forced_finish"}})[0]
    'exhausted'
    """
    thresholds = budget_thresholds()
    dimensions = budget_dimensions(record)
    utilization = dimensions["utilization"]
    if dimensions["marker_exhaustion"] or dimensions["tool_at_cap"]:
        return "exhausted", utilization
    if utilization is None:
        return "normal", None
    if utilization >= thresholds["warning"]:
        return "warning", utilization
    return "normal", utilization


# --------------------------------------------------------------------------- #
# Artifact facts -> completeness
# --------------------------------------------------------------------------- #
def completeness_components(result: dict[str, Any] | None) -> dict[str, float]:
    """The three graded completeness components, each in ``[0, 1]``.

    ``implementation`` (0.40 weight) is 1/0 on artifact presence,
    ``verification`` (0.40) is 1.0 machine-checkable / 0.5 present-but-vacuous /
    0 absent, ``claim`` (0.20) is 1/0 on a claim artifact.
    """
    facts = normalize_artifacts(result if isinstance(result, dict) and result else None)
    implementation = 1.0 if facts.implementation_present else 0.0
    if facts.verification_concrete:
        verification = 1.0
    elif facts.verification_present:
        verification = 0.5
    else:
        verification = 0.0
    claim = 1.0 if facts.claim_present else 0.0
    return {"implementation": implementation, "verification": verification, "claim": claim}


def completeness_score(result: dict[str, Any] | None) -> float:
    """Weighted completeness in ``[0, 1]`` (weights from the contract).

    Examples
    --------
    >>> completeness_score({"changed_files": ["a.py"], "checks": "12 passed", "summary": "ok"})
    1.0
    >>> completeness_score({"changed_files": ["a.py"], "checks": "", "summary": "ok"})
    0.6
    >>> completeness_score(None)
    0.0
    """
    weights = _weights()
    components = completeness_components(result)
    return round(sum(weights[name] * components[name] for name in weights), 4)


# --------------------------------------------------------------------------- #
# Terminal state
# --------------------------------------------------------------------------- #
@dataclass
class TerminalAssessment:
    """Semantic terminal state + delivery label of one mission record."""

    state: TerminalState
    source_status: str | None
    completeness: float
    quality_gate: QualityGate
    budget_status: BudgetStatus
    budget_utilization: float | None
    components: dict[str, float] = field(default_factory=dict)
    closure_cause: str | None = None
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    budget_evidence: dict[str, Any] = field(default_factory=dict)

    def label(self) -> dict[str, Any]:
        """The delivery label (validates against ``delivery_label.v1.json``)."""
        label: dict[str, Any] = {
            "schema_version": int(contract()["version"]),
            "terminal_state": self.state,
            "completeness": round(float(self.completeness), 4),
            "quality_gate": self.quality_gate,
            "budget_status": self.budget_status,
            "budget_utilization": self.budget_utilization,
            "components": {k: round(float(v), 4) for k, v in self.components.items()},
            "missing": list(self.missing),
        }
        if self.state == "forced_finish":
            label["closure_cause"] = self.closure_cause or "unknown"
        return label

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "source_status": self.source_status,
            "reasons": self.reasons,
            "budget_evidence": self.budget_evidence,
            **self.label(),
        }


def _fail_closed(
    source_status: str | None,
    reason: str,
    *,
    result: dict[str, Any] | None = None,
) -> TerminalAssessment:
    """A fail-closed assessment that still carries the graded artifact facts.

    The label must stay schema-complete even when the record is unusable: an
    unusable record scores completeness 0 and still names what is missing.
    """
    components = completeness_components(result)
    return TerminalAssessment(
        state="failed",
        source_status=source_status,
        completeness=completeness_score(result),
        quality_gate="fail",
        budget_status="normal",
        budget_utilization=None,
        components=components,
        missing=list(assess_attested_delivery(result).missing),
        reasons=[reason],
    )


def classify_terminal_state(
    record: dict[str, Any] | None,
    *,
    allow_non_terminal: bool = False,
) -> TerminalAssessment:
    """Derive the semantic terminal state of a stored mission record.

    Precedence (first match wins, contract ``precedence``): storage failure ->
    forced closure -> partial delivery -> normal completion.

    Parameters
    ----------
    record : dict | None
        A stored mission row (``status``, ``budget``, ``created_at``,
        ``finished_at``, ``result``).
    allow_non_terminal : bool
        When False (default) a non-terminal storage status raises
        :class:`NonTerminalRecord`. When True the record is assumed closed
        (what-if analysis) and classified on facts alone.

    Examples
    --------
    >>> classify_terminal_state({"status": "succeeded", "result":
    ...     {"changed_files": ["a.py"], "checks": "12 passed", "summary": "ok"}}).state
    'succeeded'
    >>> classify_terminal_state({"status": "succeeded", "result":
    ...     {"changed_files": ["a.py"], "checks": "12 passed", "summary": "ok",
    ...      "fail_stage": "budget_exhausted+forced_finish"}}).state
    'forced_finish'
    >>> classify_terminal_state({"status": "succeeded", "result":
    ...     {"changed_files": ["a.py"], "checks": "", "summary": "ok"}}).state
    'degraded'
    >>> classify_terminal_state({"status": "failed", "result": {}}).state
    'failed'
    """
    if not isinstance(record, dict):
        return _fail_closed(None, "record is not a mapping -> fail-closed")

    source_status = record.get("status")
    source_status = str(source_status) if source_status is not None else None
    if source_status is None:
        return _fail_closed(None, "no storage status -> fail-closed")
    if source_status not in _failed_statuses() + ("succeeded",):
        if not allow_non_terminal:
            raise NonTerminalRecord(f"storage status {source_status!r} is not terminal")
        return _fail_closed(
            source_status,
            f"non-terminal status {source_status!r} classified as fail-closed",
            result=record.get("result") if isinstance(record.get("result"), dict) else None,
        )

    result = record.get("result") if isinstance(record.get("result"), dict) else None
    b_status, utilization = budget_status(record)
    components = completeness_components(result)
    completeness = completeness_score(result)
    cause = closure_cause(record)
    attested = assess_attested_delivery(result)
    missing = list(attested.missing)
    reasons: list[str] = []

    if source_status in _failed_statuses():
        state: TerminalState = "failed"
        reasons.append(f"storage status {source_status!r} is a non-success terminal")
    elif cause is not None:
        state = "forced_finish"
        reasons.append(f"forced-closure marker present (cause={cause})")
        if b_status == "exhausted":
            reasons.append(f"budget exhausted (utilization={utilization}, evidence accepted)")
        elif utilization is not None:
            reasons.append(f"wall arithmetic only reaches warning (utilization={utilization})")
    elif b_status == "exhausted":
        state = "forced_finish"
        reasons.append(
            f"tool-call counter at its cap (utilization={utilization}) without an explicit marker"
        )
    elif completeness < 1.0:
        state = "degraded"
        reasons.append(f"artifacts incomplete (completeness={completeness})")
        if missing:
            reasons.append("missing: " + ", ".join(missing))
    else:
        state = "succeeded"
        reasons.append("artifacts complete and closure clean")

    quality_gate: QualityGate = (
        "pass" if state == "succeeded" and completeness >= 1.0 else "fail"
    )
    return TerminalAssessment(
        state=state,
        source_status=source_status,
        budget_evidence=budget_dimensions(record),
        completeness=completeness,
        quality_gate=quality_gate,
        budget_status=b_status,
        budget_utilization=utilization,
        components=components,
        closure_cause=cause,
        missing=missing,
        reasons=reasons,
    )


def delivery_label(record: dict[str, Any] | None, *, allow_non_terminal: bool = False) -> dict[str, Any]:
    """Delivery label dict for one record (see :meth:`TerminalAssessment.label`)."""
    return classify_terminal_state(record, allow_non_terminal=allow_non_terminal).label()


# --------------------------------------------------------------------------- #
# Label schema validation
# --------------------------------------------------------------------------- #
def validate_delivery_label(label: dict[str, Any]) -> list[str]:
    """Validate a label against ``delivery_label.v1.json``; returns error strings.

    ``jsonschema`` is used when installed (full draft validation), otherwise a
    dependency-free structural fallback enforces the same required keys, enums
    and numeric ranges — so the contract is checked either way.

    Examples
    --------
    >>> validate_delivery_label(delivery_label({"status": "succeeded", "result":
    ...     {"changed_files": ["a.py"], "checks": "12 passed", "summary": "ok"}}))
    []
    >>> bool(validate_delivery_label({"terminal_state": "succeeded"}))
    True
    """
    schema = delivery_label_schema()
    try:
        import jsonschema
    except ImportError:
        return _validate_label_fallback(label, schema)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(label), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]


def _validate_label_fallback(label: Any, schema: dict[str, Any]) -> list[str]:
    """Dependency-free subset of the schema (required / enum / range / const)."""
    if not isinstance(label, dict):
        return ["<root>: label must be an object"]
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in label:
            errors.append(f"{key}: required property missing")
    props = schema.get("properties", {})
    for key, value in label.items():
        spec = props.get(key)
        if spec is None:
            if schema.get("additionalProperties") is False:
                errors.append(f"{key}: additional property not allowed")
            continue
        if "const" in spec and value != spec["const"]:
            errors.append(f"{key}: must equal {spec['const']!r}")
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"{key}: {value!r} not in {spec['enum']}")
        if "minimum" in spec and isinstance(value, (int, float)) and value < spec["minimum"]:
            errors.append(f"{key}: below minimum {spec['minimum']}")
        if "maximum" in spec and isinstance(value, (int, float)) and value > spec["maximum"]:
            errors.append(f"{key}: above maximum {spec['maximum']}")
    components = label.get("components")
    if isinstance(components, dict):
        for key in ("implementation", "verification", "claim"):
            value = components.get(key)
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                errors.append(f"components/{key}: must be a number in [0, 1]")
    return errors


# --------------------------------------------------------------------------- #
# 70% budget MVD checkpoint (shares the threshold source)
# --------------------------------------------------------------------------- #
def mvd_checkpoint_for_record(record: dict[str, Any]) -> dict[str, Any]:
    """Run (and time) the 70% MVD checkpoint on one record's stored facts.

    The checkpoint itself lives in :mod:`api.domain.mvd_checkpoint`; this wrapper
    only feeds it the stored plan/actual budget and the artifact facts, and
    returns the evaluation plus its measured latency so the acceptance criterion
    (trigger latency <= 1 s) is measured on the production code path.

    Examples
    --------
    >>> out = mvd_checkpoint_for_record({"budget": {"max_tool_calls": 100},
    ...     "result": {"tool_calls": 70, "changed_files": ["a.py"],
    ...                "checks": "12 passed", "summary": "ok"}})
    >>> out["triggered"], out["passed"]
    (True, True)
    """
    budget = record.get("budget") if isinstance(record.get("budget"), dict) else {}
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    tools = _as_float(result.get("tool_calls")) or 0.0
    max_tools = _as_float(budget.get("max_tool_calls")) or 0.0
    start = _as_seconds(record.get("created_at"))
    end = _as_seconds(record.get("finished_at")) or _as_seconds(record.get("heartbeat_at"))
    wall = max(0.0, (end - start)) if start is not None and end is not None else 0.0
    max_wall = _as_float(budget.get("max_wall_seconds")) or 0.0

    t0 = time.perf_counter()
    evaluation = _mvd.evaluate_mvd_checkpoint(
        result if result else None,
        int(tools),
        int(max_tools),
        wall,
        max_wall,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    payload = evaluation.to_dict()
    payload["latency_ms"] = round(elapsed_ms, 4)
    return payload


# --------------------------------------------------------------------------- #
# Backward-compatible migration of legacy succeeded rows
# --------------------------------------------------------------------------- #
MIGRATION_RULE = "legacy_succeeded_to_terminal_state"


def migrate_legacy_succeeded(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Back-fill the semantic terminal state onto legacy stored rows.

    Every input row is copied (never mutated) and gains exactly three keys:
    ``terminal_state``, ``delivery_label`` and ``migration`` (which carries
    ``legacy_status``). The zero-loss invariant is checked, not assumed: the
    count must match, every original key must survive with an identical value,
    and the multiset of semantic states must cover every row.

    Parameters
    ----------
    records : list[dict]
        Stored mission rows (any storage status; non-succeeded rows are labelled
        from their own facts, not silently dropped).

    Returns
    -------
    dict
        ``{"records": [...], "report": {...}}`` where ``report`` holds
        ``records_in``, ``records_out``, ``state_counts``, ``migrated_in_state``,
        ``zero_data_loss`` (False only when an original key/value vanished),
        ``records_unmappable`` (copied verbatim, no semantics derivable) and the
        concrete ``violations`` list.
    """
    out: list[dict[str, Any]] = []
    violations: list[str] = []
    unmappable: list[int] = []
    state_counts: dict[str, int] = {state: 0 for state in TERMINAL_STATES}
    rule_counts: dict[str, int] = {}

    for index, record in enumerate(records or []):
        if not isinstance(record, dict):
            # Preserved verbatim: no semantics can be derived, but nothing is dropped.
            unmappable.append(index)
            out.append(record)  # type: ignore[arg-type]
            continue
        assessment = classify_terminal_state(record, allow_non_terminal=True)
        migrated = copy.deepcopy(record)
        migrated["terminal_state"] = assessment.state
        migrated["delivery_label"] = assessment.label()
        migrated["migration"] = {
            "rule": MIGRATION_RULE,
            "vocab_version": int(contract()["version"]),
            "legacy_status": record.get("status"),
            "from": record.get("status"),
            "to": assessment.state,
        }
        state_counts[assessment.state] = state_counts.get(assessment.state, 0) + 1
        rule_counts[assessment.state] = rule_counts.get(assessment.state, 0) + 1

        for key, value in record.items():           # zero-loss invariant
            if key not in migrated or migrated[key] != value:
                violations.append(f"record[{index}]: key {key!r} not preserved")
        out.append(migrated)

    status_changed = sum(
        1 for row in out
        if isinstance(row, dict) and row.get("migration", {}).get("from") != row.get("terminal_state")
    )
    covered = sum(state_counts.values())
    report = {
        "rule": MIGRATION_RULE,
        "vocab_version": int(contract()["version"]),
        "records_in": len(records or []),
        "records_out": len(out),
        "state_counts": state_counts,
        "migrated_in_state": rule_counts,
        "records_with_state_change": status_changed,
        "rows_covered": covered,
        "records_unmappable": len(unmappable),
        "zero_data_loss": (not violations)
        and len(out) == len(records or [])
        and covered + len(unmappable) == len(records or []),
        "violations": violations[:20],
    }
    return {"records": out, "report": report}


def summarize_states(assessments: list[TerminalAssessment]) -> dict[str, Any]:
    """Aggregate state counts / rates (explicit denominators, no rounding lies).

    The binary ``success_rate`` is deliberately absent: the point of this module
    is that ``succeeded`` alone is not a delivery rate.
    """
    total = len(assessments)
    counts = {state: 0 for state in TERMINAL_STATES}
    complete = 0
    gate_pass = 0
    for item in assessments:
        counts[item.state] = counts.get(item.state, 0) + 1
        complete += 1 if item.completeness >= 1.0 else 0
        gate_pass += 1 if item.quality_gate == "pass" else 0

    def rate(numerator: int) -> float:
        return round(numerator / total, 4) if total else 0.0

    return {
        "total": total,
        "counts": counts,
        "rates": {state: rate(counts[state]) for state in TERMINAL_STATES},
        "completeness_mean": round(
            sum(a.completeness for a in assessments) / total, 4
        ) if total else 0.0,
        "complete_rate": rate(complete),
        "quality_gate_pass_rate": rate(gate_pass),
        "budget_status_counts": {
            b: sum(1 for a in assessments if a.budget_status == b)
            for b in ("normal", "warning", "exhausted")
        },
    }