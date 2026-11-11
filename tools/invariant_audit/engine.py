#!/usr/bin/env python3
"""Plan execution: declarative targets -> per-pair ledger.

The engine owns *only* orchestration: resolve the callable, call it once for the
base configuration, hand the observables to each invariant evaluator, fold the
per-pair verdicts into a per-target state and serialise deterministically.  All
physics lives in the injected plan (``config/invariant_audit/plans/*.json``) and
in the solver implementations it points at.

Determinism: the ledger is built from rounded floats and sorted keys, so two
runs in the same environment produce byte-identical ``ledger.json`` (asserted by
``tests/invariant_audit/test_invariant_audit_engine.py``).
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tools.verification_harness.evidence import canonical_json
from tools.verification_harness.registry import resolve_callable

from .checks import EvalContext, evaluate
from .model import (
    AUDIT_SCHEMA_VERSION,
    DIMENSIONS_KEY,
    RESERVED_OBSERVABLE_PREFIX,
    AuditReport,
    InvariantResult,
    InvariantState,
    TargetResult,
    TargetSpec,
    aggregate_state,
    round_floats,
)


class PlanError(ValueError):
    """The plan is not a valid ``invariant-audit/1.0`` declaration (fail-closed)."""


@dataclass(frozen=True)
class AuditPlan:
    plan_id: str
    base_dimensions: Tuple[str, ...]
    targets: Tuple[TargetSpec, ...]
    schema_version: str = AUDIT_SCHEMA_VERSION
    description: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AuditPlan":
        if not isinstance(payload, Mapping):
            raise PlanError(f"plan must be a mapping, got {type(payload).__name__}")
        version = str(payload.get("schema_version", AUDIT_SCHEMA_VERSION))
        if version != AUDIT_SCHEMA_VERSION:
            raise PlanError(f"unsupported plan schema {version!r}; "
                            f"expected {AUDIT_SCHEMA_VERSION!r}")
        plan_id = payload.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise PlanError("plan needs a non-empty string 'plan_id'")
        base = payload.get("base_dimensions") or []
        if not isinstance(base, list) or not all(isinstance(b, str) and b for b in base):
            raise PlanError("'base_dimensions' must be a list of non-empty strings")
        if len(set(base)) != len(base):
            raise PlanError("'base_dimensions' contains duplicates")
        raw_targets = payload.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise PlanError("plan needs a non-empty 'targets' list")
        targets = []
        seen = set()
        for item in raw_targets:
            try:
                target = TargetSpec.from_mapping(item)
            except ValueError as exc:
                raise PlanError(str(exc)) from exc
            if target.target_id in seen:
                raise PlanError(f"duplicate target id {target.target_id!r}")
            seen.add(target.target_id)
            for inv in target.invariants:
                if inv.kind.value not in {k.value for k in _known_kinds()}:
                    raise PlanError(f"target {target.target_id!r}: unknown kind {inv.kind}")
            targets.append(target)
        return cls(plan_id=plan_id, base_dimensions=tuple(base), targets=tuple(targets),
                   schema_version=version,
                   description=str(payload.get("description", "")))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "description": self.description,
            "base_dimensions": list(self.base_dimensions),
            "targets": [t.to_dict() for t in self.targets],
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_json(round_floats(self.to_dict())).encode("utf-8")
                              ).hexdigest()


def _known_kinds():
    from .checks import KIND_HANDLERS
    return set(KIND_HANDLERS)


def load_plan(payload: Mapping[str, Any]) -> AuditPlan:
    return AuditPlan.from_mapping(payload)


def load_plan_file(path: str | Path) -> AuditPlan:
    text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path}: invalid JSON: {exc}") from exc
    return AuditPlan.from_mapping(payload)


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
def _split_observables(payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    observables = {str(k): v for k, v in payload.items()
                   if not str(k).startswith(RESERVED_OBSERVABLE_PREFIX)}
    raw_dims = payload.get(DIMENSIONS_KEY) or {}
    dimensions = dict(raw_dims) if isinstance(raw_dims, Mapping) else {}
    return observables, dimensions


def _run_target(target: TargetSpec, plan: AuditPlan) -> TargetResult:
    try:
        call = resolve_callable(target.callable_path)
    except Exception as exc:
        return TargetResult(target_id=target.target_id, state=InvariantState.ERROR,
                            label=target.label,
                            error=f"callable_unresolved:{type(exc).__name__}:{exc}")
    base_config = copy.deepcopy(dict(target.config))
    try:
        raw = call(copy.deepcopy(base_config))
    except Exception as exc:
        return TargetResult(target_id=target.target_id, state=InvariantState.ERROR,
                            label=target.label,
                            error=f"callable_raised:{type(exc).__name__}:{exc}")
    if not isinstance(raw, Mapping):
        return TargetResult(target_id=target.target_id, state=InvariantState.ERROR,
                            label=target.label,
                            error=f"callable_returned:{type(raw).__name__}")
    observables, dimensions = _split_observables(raw)
    ctx = EvalContext(call=call, config=base_config, observables=observables,
                      dimensions=dimensions, base_dimensions=plan.base_dimensions)
    results: List[InvariantResult] = []
    for spec in target.invariants:
        outcome = evaluate(spec, ctx)
        results.append(InvariantResult(
            invariant_id=spec.invariant_id,
            kind=spec.kind,
            state=outcome.state,
            residual=outcome.residual,
            scale=outcome.scale,
            tolerance=spec.tolerance,
            reason=outcome.reason,
            details=outcome.details,
        ))
    return TargetResult(
        target_id=target.target_id,
        state=aggregate_state([r.state for r in results]),
        invariants=tuple(results),
        label=target.label,
        observable_keys=tuple(sorted(observables)),
    )


def run_audit(plan: AuditPlan) -> AuditReport:
    """Execute every declared target and fold the verdicts."""
    targets = tuple(_run_target(target, plan) for target in plan.targets)
    return AuditReport(plan_id=plan.plan_id, targets=targets,
                       base_dimensions=plan.base_dimensions,
                       schema_version=plan.schema_version,
                       plan_digest=plan.digest())


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------
def ledger_markdown(report: AuditReport) -> str:
    counts = report.counts()
    pairs = report.pair_counts()
    lines = [
        f"# Invariant audit ledger — `{report.plan_id}`",
        "",
        f"- schema: `{report.schema_version}`",
        f"- plan digest: `{report.plan_digest}`",
        "- base dimensions (injected): " + (", ".join(f"`{b}`" for b in report.base_dimensions)
                                             or "_none_"),
        "",
        "## Target states",
        "",
        "| state | targets | pairs |",
        "|---|---|---|",
    ]
    for state in InvariantState:
        lines.append(f"| {state.value} | {counts[state.value]} | {pairs[state.value]} |")
    lines += [
        "",
        "## Per pair",
        "",
        "| target | invariant | kind | state | residual | scale | reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for target in report.targets:
        for inv in target.invariants:
            residual = "" if inv.residual is None else f"{inv.residual:.6g}"
            scale = "" if inv.scale is None else f"{inv.scale:.6g}"
            lines.append(f"| `{target.target_id}` | `{inv.invariant_id}` | "
                         f"{inv.kind.value} | **{inv.state.value}** | {residual} | "
                         f"{scale} | {inv.reason or '-'} |")
    lines += ["", "## Target summary", "",
              "| target | state | label |", "|---|---|---|"]
    for target in report.targets:
        lines.append(f"| `{target.target_id}` | **{target.state.value}** | {target.label or '-'} |")
    if any(t.error for t in report.targets):
        lines += ["", "## Errors", ""]
        for target in report.targets:
            if target.error:
                lines.append(f"- `{target.target_id}`: {target.error}")
    return "\n".join(lines) + "\n"