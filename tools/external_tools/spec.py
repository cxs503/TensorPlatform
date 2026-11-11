"""Declarative specification of *external tool classes* (platform mechanism).

Why a spec file instead of Python subclasses: the platform must be able to answer
"which external tool classes are onboarded **and usable**" without importing every
tool, and a new class must be addable by configuration — no runner change.  This
mirrors the two mature patterns for third-party integration:

* **Registry / Plugin pattern** (Fowler, *PoEAA* 2002, ch. "Plugin" and
  "Registry"): implementations are looked up by a stable id, the host never
  imports them statically.  Python's own instance of it is the
  ``entry point`` mechanism (https://packaging.python.org/en/latest/specifications/entry-points/,
  ``importlib.metadata.entry_points()``), which is what pytest / MLflow flavours /
  DVC stages use to discover plugins.
* **Capability negotiation before use** (Model Context Protocol, ``initialize`` +
  ``tools/list``, https://modelcontextprotocol.io/specification/2025-06-18): a
  tool is only advertised after the host has probed what the peer can actually
  do.  Same idea as Kubernetes liveness/readiness probes
  (https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/):
  *declared* availability is not *observed* availability.

So the spec declares, per tool class: the adapter that drives it, the probe that
decides whether the backing tool is present, the numeric invariant the smoke call
must satisfy, and which evidence level the class may claim.  Nothing here knows
what any tool is *for*; meaning arrives from the spec file (data, not code).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

EVIDENCE_LEVELS: Tuple[str, ...] = ("REAL", "MOCK", "WITHHELD", "FAIL")
#: ordered from strongest to weakest; a roll-up takes the weakest level seen.
LEVEL_RANK: Dict[str, int] = {lv: i for i, lv in enumerate(EVIDENCE_LEVELS)}

SPEC_VERSION = "external-tools/1"


class SpecError(ValueError):
    """Raised when a tool-class spec is malformed (fail closed, never guess)."""


@dataclass(frozen=True)
class Invariant:
    """The machine-checkable acceptance of a smoke call.

    ``metric`` is the name the adapter reports; ``limit`` is the numeric bound;
    ``rel`` selects ``abs(x - target) <= limit`` (``limit`` doubles as target when
    ``target`` is omitted) or, with ``op="le"``/``"ge"``, a one-sided bound.
    """

    metric: str
    limit: float
    op: str = "le"
    target: Optional[float] = None
    unit: str = ""
    #: independent reference value the observed number is scored against.
    #: ``None`` means "no declared reference" -> the benchmark marks the case
    #: ``unscored`` and makes no accuracy claim (no reference, no percentage).
    ref: Optional[float] = None
    #: where the reference comes from (closed form / analytic identity / stdlib)
    ref_source: str = ""

    def __post_init__(self) -> None:
        if self.op not in ("le", "ge", "abs"):
            raise SpecError(f"invariant {self.metric}: unknown op {self.op!r}")
        if self.limit < 0:
            raise SpecError(f"invariant {self.metric}: negative limit")

    def check(self, observed: float) -> bool:
        ref = 0.0 if self.target is None else float(self.target)
        if self.op == "le":
            return float(observed) <= self.limit
        if self.op == "ge":
            return float(observed) >= self.limit
        return abs(float(observed) - ref) <= self.limit

    def to_dict(self) -> Dict[str, Any]:
        out = {"metric": self.metric, "limit": self.limit, "op": self.op,
               "target": self.target, "unit": self.unit}
        if self.ref is not None:
            out["ref"] = self.ref
        if self.ref_source:
            out["ref_source"] = self.ref_source
        return out


@dataclass(frozen=True)
class ToolClass:
    """One external tool class, exactly as declared."""

    tool_id: str
    category: str
    adapter: str
    required: bool = True
    probe: Mapping[str, Any] = field(default_factory=dict)
    invariants: Tuple[Invariant, ...] = ()
    fallback: Optional[str] = None
    notes: str = ""
    domains: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tool_id:
            raise SpecError("tool class without id")
        if not set(self.tool_id) <= set("abcdefghijklmnopqrstuvwxyz0123456789_-"):
            raise SpecError(f"tool id {self.tool_id!r}: use [a-z0-9_-] only")
        if not self.invariants:
            raise SpecError(f"{self.tool_id}: a class without an invariant could only "
                            "claim execution, not usability")

    def to_dict(self) -> Dict[str, Any]:
        return {"tool_id": self.tool_id, "category": self.category,
                "adapter": self.adapter, "required": self.required,
                "probe": dict(self.probe),
                "invariants": [inv.to_dict() for inv in self.invariants],
                "fallback": self.fallback, "notes": self.notes,
                "domains": list(self.domains)}


@dataclass(frozen=True)
class Suite:
    """A whole spec file: classes + the coverage target that has to be met."""

    suite_id: str
    target_classes: int
    classes: Tuple[ToolClass, ...]
    source_path: str = ""

    def by_id(self) -> Dict[str, ToolClass]:
        return {c.tool_id: c for c in self.classes}


def _invariants(raw: Iterable[Mapping[str, Any]]) -> Tuple[Invariant, ...]:
    out: List[Invariant] = []
    for item in raw:
        if "metric" not in item:
            raise SpecError(f"invariant without metric: {item!r}")
        out.append(Invariant(
            metric=str(item["metric"]),
            limit=float(item["limit"]),
            op=str(item.get("op", "le")),
            target=None if item.get("target") is None else float(item["target"]),
            unit=str(item.get("unit", "")),
            ref=None if item.get("ref") is None else float(item["ref"]),
            ref_source=str(item.get("ref_source", "")),
        ))
    return tuple(out)


def load_suite(path: str | Path) -> Suite:
    """Load + validate a suite spec file.  Every failure is loud."""

    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - configuration error
        raise SpecError(f"spec not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"spec is not JSON: {p}: {exc}") from exc

    for key in ("suite_id", "target_classes", "classes"):
        if key not in raw:
            raise SpecError(f"{p}: missing key {key!r}")
    if not isinstance(raw["classes"], list) or not raw["classes"]:
        raise SpecError(f"{p}: 'classes' must be a non-empty list")

    classes: List[ToolClass] = []
    seen: set[str] = set()
    for item in raw["classes"]:
        if "tool_id" not in item or "adapter" not in item:
            raise SpecError(f"{p}: class entry needs tool_id+adapter: {item!r}")
        if item["tool_id"] in seen:
            raise SpecError(f"{p}: duplicate tool_id {item['tool_id']!r}")
        seen.add(item["tool_id"])
        classes.append(ToolClass(
            tool_id=str(item["tool_id"]),
            category=str(item.get("category", "uncategorised")),
            adapter=str(item["adapter"]),
            required=bool(item.get("required", True)),
            probe=dict(item.get("probe") or {}),
            invariants=_invariants(item.get("invariants") or []),
            fallback=item.get("fallback"),
            notes=str(item.get("notes", "")),
            domains=tuple(item.get("domains") or ()),
        ))

    target = int(raw["target_classes"])
    if target < 1:
        raise SpecError(f"{p}: target_classes must be >= 1")
    return Suite(suite_id=str(raw["suite_id"]), target_classes=target,
                 classes=tuple(classes), source_path=str(p))


def weakest(levels: Sequence[str]) -> str:
    """Fail-closed roll-up: the weakest observed level wins."""

    if not levels:
        return "WITHHELD"
    unknown = [lv for lv in levels if lv not in LEVEL_RANK]
    if unknown:
        raise SpecError(f"unknown evidence levels: {unknown}")
    return max(levels, key=lambda lv: LEVEL_RANK[lv])