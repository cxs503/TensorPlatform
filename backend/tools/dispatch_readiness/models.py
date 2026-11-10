"""Data structures shared by the readiness gate (pure, dependency free)."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

READY = "READY"
NOT_READY = "NOT_READY"
CONFLICT = "CONFLICT"

# requirement modes, weakest to strongest
MODE_DECLARED = "declared"            # upstream artefact merely announced/queued
MODE_DELIVERED = "delivered"          # upstream finished producing it
MODE_TERMINAL_SUCCESS = "terminal_success"  # upstream finished *successfully*


@dataclass(frozen=True)
class ArtifactRequirement:
    """One requirement of an input contract."""

    upstream: str = "*"          # node key of the producer, "*" = all declared upstreams
    artifact: str = "delivery"   # artefact class
    mode: str = MODE_DELIVERED
    fields: Tuple[str, ...] = ()
    units: Optional[str] = None
    version_min: Optional[str] = None
    required: bool = True

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ArtifactRequirement":
        mode = str(raw.get("mode", MODE_DELIVERED))
        return cls(
            upstream=str(raw.get("upstream", "*")),
            artifact=str(raw.get("artifact", raw.get("artifact_class", "delivery"))),
            mode=mode,
            fields=tuple(raw.get("fields") or ()),
            units=raw.get("units"),
            version_min=raw.get("version_min", raw.get("version")),
            required=bool(raw.get("required", True)),
        )


@dataclass(frozen=True)
class InputContract:
    """Contract a node declares about what it consumes."""

    node: str
    requirements: Tuple[ArtifactRequirement, ...] = ()
    version: str = "1.0"
    source: str = "inferred"   # "declared" | "inferred"

    @classmethod
    def from_dict(cls, node: str, raw: Dict[str, Any], source: str = "declared") -> "InputContract":
        reqs = raw.get("requires") or raw.get("requirements") or ()
        return cls(
            node=str(node),
            requirements=tuple(ArtifactRequirement.from_dict(r) for r in reqs),
            version=str(raw.get("version", "1.0")),
            source=str(raw.get("source", source)),
        )


@dataclass(frozen=True)
class OutputContract:
    """Contract a node publishes about what it produces."""

    node: str
    provides: Tuple[str, ...] = ("delivery",)
    fields: Tuple[str, ...] = ()
    units: Optional[str] = None
    version: str = "1.0"

    @classmethod
    def from_dict(cls, node: str, raw: Dict[str, Any]) -> "OutputContract":
        provides = raw.get("provides") or raw.get("artifacts") or ("delivery",)
        if isinstance(provides, str):
            provides = (provides,)
        names = []
        for item in provides:
            names.append(item if isinstance(item, str) else str(item.get("artifact", "delivery")))
        return cls(
            node=str(node),
            provides=tuple(names),
            fields=tuple(raw.get("fields") or ()),
            units=raw.get("units"),
            version=str(raw.get("version", "1.0")),
        )


@dataclass(frozen=True)
class ArtifactRecord:
    """A concrete artefact observed in the delivery registry."""

    node: str
    artifact: str = "delivery"
    fields: Tuple[str, ...] = ()
    units: Optional[str] = None
    version: Optional[str] = None
    produced_at: Optional[str] = None
    terminal_success: bool = True


@dataclass(frozen=True)
class UpstreamState:
    """What the gate is allowed to know about one upstream node at dispatch time."""

    node: str
    dispatched: bool = False     # an upstream task has been handed out
    delivered: bool = False      # that task has finished
    succeeded: bool = False      # ... and finished successfully
    record: Optional[ArtifactRecord] = None
    detail: str = ""


@dataclass(frozen=True)
class Attribution:
    """Human readable (and machine checkable) reason for a verdict."""

    requirement: str
    outcome: str          # "satisfied" | "missing" | "caliber"
    message: str

    def as_dict(self) -> Dict[str, str]:
        return {"requirement": self.requirement, "outcome": self.outcome, "message": self.message}


@dataclass
class ReadinessResult:
    """Verdict for one node."""

    node: str
    status: str
    attributions: List[Attribution] = field(default_factory=list)
    contract_source: str = "inferred"
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def dispatchable(self) -> bool:
        return self.status == READY

    @property
    def blocking_reasons(self) -> List[str]:
        return [a.message for a in self.attributions if a.outcome != "satisfied"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "status": self.status,
            "dispatchable": self.dispatchable,
            "contract_source": self.contract_source,
            "attributions": [a.as_dict() for a in self.attributions],
            "blocking_reasons": self.blocking_reasons,
            "details": self.details,
        }


def version_tuple(value: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Parse '1.2.3' -> (1, 2, 3); returns None when not parseable."""
    if value is None:
        return None
    parts: Sequence[str] = str(value).strip().lstrip("vV").split(".")
    out: List[int] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits == "":
            return None
        out.append(int(digits))
    return tuple(out) if out else None