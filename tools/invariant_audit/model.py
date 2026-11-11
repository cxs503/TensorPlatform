#!/usr/bin/env python3
"""Domain-neutral data model of the invariant audit (``invariant-audit/1.0``).

A *target* is an opaque identifier plus the dotted path of the callable under
test and a JSON parameter block.  An *invariant* is an opaque identifier plus a
``kind`` (which evaluator to run) and the evaluator's parameter block.  Nothing
here knows what the numbers physically mean: every such statement is
configuration.

States
------
=========================  ==================================================
``PASS``                    the invariant was evaluated and held inside its band
``FAIL``                    it was evaluated and was violated
``WITHHELD``                the evidence needed to judge it is absent (an
                            honest "cannot tell", never silently converted
                            into PASS)
``ERROR``                   the target violated the observable *contract*
                            (raised, wrong shape) - a bug in the harness interface,
                            not a physics verdict
=========================  ==================================================

Pair and aggregate states are kept separate: a target is ``FAIL`` if any of its
invariants failed, ``ERROR`` if any errored (and none failed), ``WITHHELD`` if
anything was withheld (and nothing failed/errored), else ``PASS``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tools.verification_harness.model import FLOAT_DIGITS, Tolerance

AUDIT_SCHEMA_VERSION = "invariant-audit/1.0"

# Reserved keys in the observable mapping returned by a target callable.
RESERVED_OBSERVABLE_PREFIX = "_"
DIMENSIONS_KEY = "_dimensions"


class InvariantState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WITHHELD = "WITHHELD"
    ERROR = "ERROR"


class InvariantKind(str, Enum):
    CONSERVATION = "conservation"
    COVARIANCE = "covariance"
    DIMENSION = "dimension_consistency"
    RELATION = "relation"


def aggregate_state(states: Sequence[InvariantState]) -> InvariantState:
    """Fold per-invariant states into one target state (conservative order)."""
    states = list(states)
    if not states:
        return InvariantState.WITHHELD
    if InvariantState.FAIL in states:
        return InvariantState.FAIL
    if InvariantState.ERROR in states:
        return InvariantState.ERROR
    if InvariantState.WITHHELD in states:
        return InvariantState.WITHHELD
    return InvariantState.PASS


def round_floats(obj: Any, digits: int = FLOAT_DIGITS) -> Any:
    """Round floats recursively so two runs serialise to identical bytes."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return str(obj)
        return float(f"{obj:.{digits}g}")
    if isinstance(obj, Mapping):
        return {str(k): round_floats(v, digits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [round_floats(v, digits) for v in obj]
    return obj


@dataclass(frozen=True)
class InvariantSpec:
    """One declarative invariant attached to a target."""

    invariant_id: str
    kind: InvariantKind
    params: Mapping[str, Any] = field(default_factory=dict)
    tolerance: Tolerance = field(default_factory=Tolerance)
    note: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InvariantSpec":
        if not isinstance(payload, Mapping):
            raise ValueError(f"invariant must be a mapping, got {type(payload).__name__}")
        try:
            invariant_id = str(payload["id"])
        except KeyError as exc:
            raise ValueError("invariant is missing required key 'id'") from exc
        raw_kind = payload.get("kind")
        try:
            kind = InvariantKind(raw_kind)
        except ValueError as exc:
            raise ValueError(
                f"invariant {invariant_id!r}: unknown kind {raw_kind!r}; "
                f"known kinds: {[k.value for k in InvariantKind]}") from exc
        tol = payload.get("tolerance") or {}
        if not isinstance(tol, Mapping):
            raise ValueError(f"invariant {invariant_id!r}: tolerance must be a mapping")
        tolerance = Tolerance(rtol=float(tol.get("rtol", 1e-9)),
                              atol=float(tol.get("atol", 0.0)))
        params = payload.get("params") or {}
        if not isinstance(params, Mapping):
            raise ValueError(f"invariant {invariant_id!r}: params must be a mapping")
        return cls(invariant_id=invariant_id, kind=kind, params=dict(params),
                   tolerance=tolerance, note=str(payload.get("note", "")))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.invariant_id,
            "kind": self.kind.value,
            "params": round_floats(dict(self.params)),
            "tolerance": self.tolerance.to_dict(),
            "note": self.note,
        }


@dataclass(frozen=True)
class TargetSpec:
    """A solver/case pair under audit (opaque identity + callable + parameters)."""

    target_id: str
    callable_path: str
    config: Mapping[str, Any] = field(default_factory=dict)
    invariants: Tuple[InvariantSpec, ...] = ()
    label: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TargetSpec":
        if not isinstance(payload, Mapping):
            raise ValueError(f"target must be a mapping, got {type(payload).__name__}")
        try:
            target_id = str(payload["id"])
        except KeyError as exc:
            raise ValueError("target is missing required key 'id'") from exc
        callable_path = payload.get("callable")
        if not isinstance(callable_path, str) or ":" not in callable_path:
            raise ValueError(
                f"target {target_id!r}: 'callable' must be 'module:function', "
                f"got {callable_path!r}")
        config = payload.get("config") or {}
        if not isinstance(config, Mapping):
            raise ValueError(f"target {target_id!r}: config must be a mapping")
        raw_invariants = payload.get("invariants") or []
        if not isinstance(raw_invariants, list) or not raw_invariants:
            raise ValueError(f"target {target_id!r}: at least one invariant is required")
        invariants = tuple(InvariantSpec.from_mapping(item) for item in raw_invariants)
        seen = set()
        for inv in invariants:
            if inv.invariant_id in seen:
                raise ValueError(f"target {target_id!r}: duplicate invariant id "
                                 f"{inv.invariant_id!r}")
            seen.add(inv.invariant_id)
        return cls(target_id=target_id, callable_path=callable_path, config=dict(config),
                   invariants=invariants, label=str(payload.get("label", "")))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.target_id,
            "callable": self.callable_path,
            "config": round_floats(dict(self.config)),
            "invariants": [inv.to_dict() for inv in self.invariants],
            "label": self.label,
        }


@dataclass(frozen=True)
class InvariantResult:
    invariant_id: str
    kind: InvariantKind
    state: InvariantState
    residual: Optional[float] = None
    scale: Optional[float] = None
    tolerance: Optional[Tolerance] = None
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "invariant": self.invariant_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "reason": self.reason,
            "details": round_floats(dict(self.details)),
        }
        if self.residual is not None:
            payload["residual"] = round_floats(float(self.residual))
        if self.scale is not None:
            payload["scale"] = round_floats(float(self.scale))
        if self.tolerance is not None:
            payload["tolerance"] = self.tolerance.to_dict()
        return payload


@dataclass(frozen=True)
class TargetResult:
    target_id: str
    state: InvariantState
    invariants: Tuple[InvariantResult, ...] = ()
    label: str = ""
    observable_keys: Tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "target": self.target_id,
            "state": self.state.value,
            "label": self.label,
            "observables": list(self.observable_keys),
            "invariants": [inv.to_dict() for inv in self.invariants],
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class AuditReport:
    plan_id: str
    targets: Tuple[TargetResult, ...]
    base_dimensions: Tuple[str, ...] = ()
    schema_version: str = AUDIT_SCHEMA_VERSION
    plan_digest: str = ""

    def counts(self) -> Dict[str, int]:
        out = {state.value: 0 for state in InvariantState}
        for target in self.targets:
            out[target.state.value] += 1
        return out

    def pair_counts(self) -> Dict[str, int]:
        out = {state.value: 0 for state in InvariantState}
        for target in self.targets:
            for inv in target.invariants:
                out[inv.state.value] += 1
        return out

    def pairs(self) -> List[Tuple[str, InvariantResult]]:
        return [(t.target_id, inv) for t in self.targets for inv in t.invariants]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "base_dimensions": list(self.base_dimensions),
            "counts": self.counts(),
            "pair_counts": self.pair_counts(),
            "targets": [t.to_dict() for t in self.targets],
        }