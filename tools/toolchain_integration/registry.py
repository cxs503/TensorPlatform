"""Adapter registry: the only place where a stage implementation is named.

An *adapter* is a uniform port contract over whatever actually computes a stage
(a library in this repo, or an external callable resolved at run time).  The
DAG runner never imports a stage module directly, so:

* adding a stage = registering an adapter (no runner change),
* a missing/incompatible backing module degrades to ``WITHHELD`` instead of
  silently producing numbers,
* every adapter declares the evidence level it can actually support, and the
  pipeline reports the weakest level of the run (fail-closed roll-up).

Port tags form a tiny type system: a producer emits a tagged value and a
consumer can only bind a port whose tag it accepts.  Tag mismatch is a spec
error, caught before execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .schema import EVIDENCE_LEVELS, SpecError

#: Port value tags.  Deliberately coarse: the harness checks *kind compatibility*
#: (a report cannot be fed where a scalar is expected), not physics.
PORT_TAGS: Tuple[str, ...] = ("scalar", "series", "callable", "field", "report", "flag")


class AdapterError(RuntimeError):
    """Raised when an adapter cannot honour its declared contract."""


class AdapterUnavailable(AdapterError):
    """Raised when the module behind an adapter is absent or unusable."""


@dataclass(frozen=True)
class Port:
    """A declared adapter port."""

    name: str
    tag: str
    required: bool = True
    doc: str = ""

    def __post_init__(self) -> None:
        if self.tag not in PORT_TAGS:
            raise SpecError(f"port {self.name!r}: unknown tag {self.tag!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "tag": self.tag,
                "required": self.required, "doc": self.doc}


@dataclass(frozen=True)
class PortValue:
    """A tagged value travelling along an edge."""

    tag: str
    value: Any

    def __post_init__(self) -> None:
        if self.tag not in PORT_TAGS:
            raise SpecError(f"unknown port tag {self.tag!r}")


@dataclass(frozen=True)
class Adapter:
    """A stage implementation behind a fixed port contract."""

    kind: str
    summary: str
    inputs: Tuple[Port, ...]
    outputs: Tuple[Port, ...]
    run: Callable[..., Dict[str, PortValue]]
    evidence: str = "REAL"
    implementation: str = ""
    #: audit-style stages accept arbitrary input ports (names injected by the spec)
    accepts_any_inputs: bool = False

    def __post_init__(self) -> None:
        if self.evidence not in EVIDENCE_LEVELS:
            raise SpecError(f"adapter {self.kind!r}: unknown evidence {self.evidence!r}")
        names = [p.name for p in self.inputs] + [p.name for p in self.outputs]
        if len(names) != len(set(names)):
            raise SpecError(f"adapter {self.kind!r}: duplicate port name")

    # ------------------------------------------------------------------ lookup
    def input_port(self, name: str) -> Port:
        for p in self.inputs:
            if p.name == name:
                return p
        raise SpecError(f"adapter {self.kind!r} has no input port {name!r}")

    def output_port(self, name: str) -> Port:
        for p in self.outputs:
            if p.name == name:
                return p
        raise SpecError(f"adapter {self.kind!r} has no output port {name!r}")

    def contract(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "evidence": self.evidence,
            "implementation": self.implementation,
            "accepts_any_inputs": self.accepts_any_inputs,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
        }


_REGISTRY: Dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    if adapter.kind in _REGISTRY:
        raise SpecError(f"adapter {adapter.kind!r} registered twice")
    _REGISTRY[adapter.kind] = adapter
    return adapter


def get(kind: str) -> Adapter:
    try:
        return _REGISTRY[kind]
    except KeyError as exc:
        raise SpecError(
            f"unknown adapter {kind!r}; known: {sorted(_REGISTRY)}"
        ) from exc


def known() -> Sequence[str]:
    return tuple(sorted(_REGISTRY))


def catalogue() -> Dict[str, Dict[str, Any]]:
    return {k: a.contract() for k, a in sorted(_REGISTRY.items())}


@dataclass
class ExecutionContext:
    """Per-run knobs an adapter may consult (never timing-critical semantics)."""

    seed: int = 0
    warm: bool = False
    options: Dict[str, Any] = field(default_factory=dict)


def require(params: Mapping[str, Any], key: str, kind: Optional[type] = None) -> Any:
    if key not in params:
        raise AdapterError(f"missing parameter {key!r}")
    value = params[key]
    if kind is not None and not isinstance(value, kind):
        raise AdapterError(
            f"parameter {key!r} must be {kind.__name__}, got {type(value).__name__}"
        )
    return value