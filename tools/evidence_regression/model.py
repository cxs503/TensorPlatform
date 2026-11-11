"""Data model for the probe registry, probe checks and gate verdicts.

Everything here is plain data so that a verdict can be replayed from JSON alone
(no hand-transcribed numbers in reports).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_REGISTRY = "evidence_probe_registry.v1"
SCHEMA_REPORT = "evidence_regression.report.v1"

#: Probe kinds that are sensitive to *variation* of the output across inputs. A registry
#: entry without at least one of them is satisfied by a constant MOCK (the drill proves it),
#: so registry validation rejects such entries.
VARIATION_SENSITIVE = ("nontriviality", "scale_law")
#: Kinds whose presence is also mandatory: determinism kills random stubs, and an
#: expectation-bearing probe (anchor/bounded) is the only thing that kills a *uniform drift*
#: (which preserves ratios, so neither non-triviality nor homogeneity notices it).
REQUIRED_KINDS = ("determinism",)
EXPECTATION_KINDS = ("anchor", "bounded")

PROBE_KINDS = ("anchor", "scale_law", "monotonic", "nontriviality", "determinism", "bounded")


def sha256_obj(obj: Any) -> str:
    """Stable sha256 over a JSON-able object (used as probe-evidence digest)."""
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TargetSpec:
    """How to obtain the observable of a capability.

    ``steps`` is a call chain: step 0 takes ``kwargs``; every later step receives the
    previous result as its single positional argument. ``field`` selects a scalar (or any
    JSON-able) leaf from the final result using a dotted path; numeric segments index into
    sequences, e.g. ``"derived.volume_m3"`` or ``"0"`` for the first tuple element.
    """

    steps: Tuple[Dict[str, Any], ...]
    field: Optional[str] = None

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "TargetSpec":
        steps = raw.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("target.steps must be a non-empty list")
        norm: List[Dict[str, Any]] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("ref"), str) or ":" not in step["ref"]:
                raise ValueError(f"target.steps[{i}] must be {{'ref': 'module:attr', 'kwargs': {{...}}}}")
            kwargs = step.get("kwargs") or {}
            if not isinstance(kwargs, dict):
                raise ValueError(f"target.steps[{i}].kwargs must be an object")
            norm.append({"ref": step["ref"], "kwargs": dict(kwargs)})
        field_path = raw.get("field")
        if field_path is not None and not isinstance(field_path, str):
            raise ValueError("target.field must be a string")
        return TargetSpec(steps=tuple(norm), field=field_path)

    def to_dict(self) -> Dict[str, Any]:
        return {"steps": [dict(s) for s in self.steps], "field": self.field}


@dataclass(frozen=True)
class ProbeSpec:
    kind: str
    params: Dict[str, Any] = field(default_factory=dict)
    fatal: bool = True
    label: str = ""

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "ProbeSpec":
        kind = raw.get("kind")
        if kind not in PROBE_KINDS:
            raise ValueError(f"unknown probe kind {kind!r}; expected one of {PROBE_KINDS}")
        params = {k: v for k, v in raw.items() if k not in ("kind", "fatal", "label")}
        return ProbeSpec(kind=kind, params=params, fatal=bool(raw.get("fatal", True)),
                         label=str(raw.get("label") or ""))

    def to_dict(self) -> Dict[str, Any]:
        out = {"kind": self.kind, **self.params, "fatal": self.fatal}
        if self.label:
            out["label"] = self.label
        return out


@dataclass(frozen=True)
class Entry:
    """One capability entry of the probe registry."""

    capability_id: str
    target: TargetSpec
    probes: Tuple[ProbeSpec, ...]
    sys_path: Tuple[str, ...] = ()
    note: str = ""

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "Entry":
        cid = raw.get("capability_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError("entry.capability_id must be a non-empty string")
        probes_raw = raw.get("probes")
        if not isinstance(probes_raw, list) or not probes_raw:
            raise ValueError(f"entry {cid!r} has no probes")
        sys_path = raw.get("sys_path") or []
        if not isinstance(sys_path, list) or not all(isinstance(p, str) for p in sys_path):
            raise ValueError(f"entry {cid!r} sys_path must be a list of strings")
        return Entry(
            capability_id=cid,
            target=TargetSpec.from_dict(raw.get("target") or {}),
            probes=tuple(ProbeSpec.from_dict(p) for p in probes_raw),
            sys_path=tuple(sys_path),
            note=str(raw.get("note") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "sys_path": list(self.sys_path),
            "target": self.target.to_dict(),
            "probes": [p.to_dict() for p in self.probes],
            "note": self.note,
        }

    @property
    def variation_sensitive(self) -> Tuple[str, ...]:
        return tuple(p.kind for p in self.probes if p.kind in VARIATION_SENSITIVE)

    @property
    def kinds(self) -> Tuple[str, ...]:
        return tuple(p.kind for p in self.probes)


@dataclass
class Check:
    """One probe execution record. ``passed`` is None when the observation was unusable."""

    capability_id: str
    kind: str
    passed: Optional[bool]
    observed: Any = None
    expected: Any = None
    rel_error: Optional[float] = None
    tol: Optional[float] = None
    detail: str = ""
    fatal: bool = True
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeReport:
    """Result of running every probe of one registry entry."""

    capability_id: str
    checks: List[Check] = field(default_factory=list)
    error: str = ""
    elapsed_ms: float = 0.0

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        fatal = [c for c in self.checks if c.fatal]
        return bool(fatal) and all(c.passed is True for c in fatal)

    @property
    def failed_kinds(self) -> List[str]:
        return [c.kind for c in self.checks if c.fatal and c.passed is not True]

    def digest(self) -> str:
        """Digest of the evidence that produced this verdict (binds acks to a probe run).

        Timing (``elapsed_ms``) is deliberately excluded: an acknowledgement must bind to the
        *evidence*, not to the machine speed of the run that produced it.
        """
        checks = []
        for check in self.checks:
            row = check.to_dict()
            row.pop("elapsed_ms", None)
            checks.append(row)
        return sha256_obj(
            {
                "capability_id": self.capability_id,
                "passed": self.passed,
                "checks": checks,
                "error": self.error,
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "passed": self.passed,
            "failed_kinds": self.failed_kinds,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "digest": self.digest(),
            "checks": [c.to_dict() for c in self.checks],
        }