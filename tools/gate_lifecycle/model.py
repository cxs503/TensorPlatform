"""Core data model for the campaign-gate lifecycle.

Domain-neutral by construction: nothing here knows about any scientific domain.
A *plan* is a DAG of *nodes*; each node carries machine-computable *criteria*
and (after execution) a set of *measurements* plus an optional worker
*declaration*.  All results are canonical JSON so that they can be hashed
(idempotency, C6) and diffed.

Canonicalisation rules (kept deliberately small so two runs of the same input
produce byte-identical payloads):
  * dict keys sorted, no runs of whitespace, floats normalised via ``repr`` of
    the rounded value (``FLOAT_ND`` decimals) and ``-0.0`` folded to ``0.0``;
  * lists keep their declared order;
  * no timestamps, no absolute paths, no random ids inside hashed payloads.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

FLOAT_ND = 12

# Verdict vocabulary -------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"
VERDICTS = (PASS, FAIL, WITHHELD)

# Node lifecycle status ----------------------------------------------------
STATUS_DONE = "done"          # executed, verdict PASS
STATUS_FAILED = "failed"      # executed, verdict FAIL (counts in failure rate)
STATUS_PROTECTED = "honest_fail"  # declared honest FAIL -> S-N track, not a failure
STATUS_WITHHELD = "withheld"   # not enough evidence to adjudicate
STATUS_BLOCKED = "blocked"     # never executed (dependency failed / withheld)
STATUS_PENDING = "pending"

FAILURE_COUNTED = (STATUS_FAILED,)
NEGATIVE_TRACK = (STATUS_PROTECTED,)


class PlanError(ValueError):
    """Raised for malformed plans (unknown dependency, negative duration, ...)."""


class CycleError(PlanError):
    """Raised when the node graph is not acyclic."""

    def __init__(self, cycle: Sequence[str]):
        self.cycle = list(cycle)
        super().__init__("dependency cycle: " + " -> ".join(self.cycle))


# ---------------------------------------------------------------------------
# criteria / measurements
# ---------------------------------------------------------------------------
OPS = ("le", "lt", "ge", "gt", "eq", "abs_le", "abs_lt")


@dataclass(frozen=True)
class Criterion:
    """A machine-computable acceptance criterion.

    ``metric`` is a dotted path into the measurement mapping, e.g.
    ``"residual.max_rel"``.
    """

    name: str
    metric: str
    op: str
    threshold: float
    tolerance: float = 0.0
    required: bool = True

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise PlanError(f"unknown op {self.op!r} for criterion {self.name!r}")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Criterion":
        return cls(
            name=str(raw["name"]),
            metric=str(raw["metric"]),
            op=str(raw["op"]),
            threshold=float(raw["threshold"]),
            tolerance=float(raw.get("tolerance", 0.0)),
            required=bool(raw.get("required", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "op": self.op,
            "threshold": self.threshold,
            "tolerance": self.tolerance,
            "required": self.required,
        }


@dataclass(frozen=True)
class Node:
    node_id: str
    deps: tuple[str, ...] = ()
    duration: float = 1.0
    criteria: tuple[Criterion, ...] = ()
    capability: str = ""
    category: str = "capability_extension"
    weight: float = 1.0
    meta: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Node":
        node_id = str(raw["id"])
        return cls(
            node_id=node_id,
            deps=tuple(str(d) for d in raw.get("deps", ())),
            duration=float(raw.get("duration", 1.0)),
            criteria=tuple(Criterion.from_dict(c) for c in raw.get("criteria", ())),
            capability=str(raw.get("capability", node_id)),
            category=str(raw.get("category", "capability_extension")),
            weight=float(raw.get("weight", 1.0)),
            meta=dict(raw.get("meta", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "deps": list(self.deps),
            "duration": self.duration,
            "criteria": [c.to_dict() for c in self.criteria],
            "capability": self.capability,
            "category": self.category,
            "weight": self.weight,
            "meta": _canon(self.meta),
        }


@dataclass(frozen=True)
class Plan:
    plan_id: str
    nodes: tuple[Node, ...]
    meta: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Plan":
        nodes = tuple(Node.from_dict(n) for n in raw["nodes"])
        seen: set[str] = set()
        for n in nodes:
            if n.node_id in seen:
                raise PlanError(f"duplicate node id {n.node_id!r}")
            seen.add(n.node_id)
            if n.duration < 0:
                raise PlanError(f"negative duration on node {n.node_id!r}")
        for n in nodes:
            for d in n.deps:
                if d not in seen:
                    raise PlanError(f"node {n.node_id!r} depends on unknown node {d!r}")
                if d == n.node_id:
                    raise CycleError([n.node_id, n.node_id])
        return cls(plan_id=str(raw.get("plan_id", "plan")), nodes=nodes,
                   meta=dict(raw.get("meta", {})))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "meta": _canon(self.meta),
        }

    def by_id(self) -> dict[str, Node]:
        return {n.node_id: n for n in self.nodes}

    def successors(self) -> dict[str, list[str]]:
        succ: dict[str, list[str]] = {n.node_id: [] for n in self.nodes}
        for n in self.nodes:
            for d in n.deps:
                succ[d].append(n.node_id)
        for v in succ.values():
            v.sort()
        return succ


# ---------------------------------------------------------------------------
# canonicalisation
# ---------------------------------------------------------------------------
def _num(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None  # non-finite never enters hashed payloads
        v = round(value, FLOAT_ND)
        return 0.0 if v == 0 else v
    return value


def _canon(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canon(v) for v in value)
    return _num(value)


def canonical_json(payload: Any) -> str:
    return json.dumps(_canon(payload), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_digest(path: Any) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CriterionResult:
    name: str
    metric: str
    op: str
    threshold: float
    required: bool
    value: float | None
    state: str            # PASS / FAIL / WITHHELD
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "op": self.op,
            "threshold": self.threshold,
            "required": self.required,
            "value": self.value,
            "state": self.state,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class NodeResult:
    node_id: str
    status: str
    verdict: str
    criteria: tuple[CriterionResult, ...]
    declared_honest_fail: bool = False
    flags: tuple[str, ...] = ()
    capability: str = ""
    category: str = "capability_extension"
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "verdict": self.verdict,
            "criteria": [c.to_dict() for c in self.criteria],
            "declared_honest_fail": self.declared_honest_fail,
            "flags": list(self.flags),
            "capability": self.capability,
            "category": self.category,
            "evidence": _canon(self.evidence),
        }

    @property
    def counted_as_failure(self) -> bool:
        return self.status in FAILURE_COUNTED

    @property
    def negative_track(self) -> bool:
        return self.status in NEGATIVE_TRACK


def get_metric(measurements: Mapping[str, Any], path: str) -> Any:
    cur: Any = measurements
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def iter_ids(nodes: Iterable[Node]) -> list[str]:
    return sorted(n.node_id for n in nodes)