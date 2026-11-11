"""Degradation drill: prove that a REAL->MOCK silent downgrade is caught, 100% of the time.

Method (no files are modified; the worktree stays clean)
-------------------------------------------------------
For each injection:

1. take one registry entry (its declared call chain is the *real* observable);
2. wrap it in one :data:`tools.evidence_regression.targets.MOCK_FAMILIES` family — constant
   freeze, unseeded random, uniform drift, zero fill, or *expected echo* (a stub written to
   return the mirror's expected values, i.e. a test-gaming MOCK);
3. run the entry's own probes against the degraded target;
4. the injection counts as **detected** iff at least one fatal probe fails.

The drill reports per-injection evidence (family, failed probe kinds, digest) plus a full
``entries x families`` matrix, so a family that a registry cannot catch is visible instead of
being averaged away.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .model import sha256_obj
from .registry import ProbeRegistry
from .targets import MOCK_FAMILIES, DegradedTarget


def expectations_of(entry) -> List[Any]:
    """Collect the expectation values declared by an entry (used by the echo MOCK family)."""
    values: List[Any] = []
    for probe in entry.probes:
        params = probe.params
        if probe.kind == "anchor":
            cases = params.get("cases")
            if isinstance(cases, list):
                for case in cases:
                    if "expect" in case:
                        values.append(case["expect"])
            elif "expect" in params:
                values.append(params["expect"])
        elif probe.kind == "bounded":
            if "lo" in params:
                values.append(params["lo"])
            if "hi" in params:
                values.append(params["hi"])
    return values


@dataclass
class Injection:
    index: int
    capability_id: str
    family: str
    detected: bool
    failed_kinds: List[str] = field(default_factory=list)
    probe_digest: str = ""
    elapsed_ms: float = 0.0
    degradation_visible: bool = False  # did the observed value actually change?

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "capability_id": self.capability_id,
            "family": self.family,
            "detected": self.detected,
            "failed_kinds": self.failed_kinds,
            "probe_digest": self.probe_digest,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "degradation_visible": self.degradation_visible,
        }


def inject_and_probe(registry: ProbeRegistry, capability_id: str, family: str) -> Injection:
    entry = registry.entries[capability_id]
    base = registry.target_for(capability_id)
    degraded = DegradedTarget(base, family, expectations=expectations_of(entry))
    started = time.perf_counter()
    report = registry.run(capability_id, target=degraded)
    elapsed = (time.perf_counter() - started) * 1000.0
    return Injection(index=-1, capability_id=capability_id, family=family, detected=not report.passed,
                     failed_kinds=report.failed_kinds, probe_digest=report.digest(),
                     elapsed_ms=elapsed, degradation_visible=not report.passed)


def drill(registry: ProbeRegistry, injections: int = 20) -> Dict[str, Any]:
    """Run ``injections`` REAL->MOCK degradations; return detection evidence."""
    ids: Sequence[str] = registry.registered_capabilities()
    if not ids:
        raise ValueError("registry has no entries to drill")
    rows: List[Injection] = []
    for i in range(injections):
        cid = ids[i % len(ids)]
        family = MOCK_FAMILIES[i % len(MOCK_FAMILIES)]
        inj = inject_and_probe(registry, cid, family)
        inj.index = i
        rows.append(inj)
    detected = sum(1 for r in rows if r.detected)
    return {
        "injections": injections,
        "detected": detected,
        "detection_rate": detected / injections if injections else 0.0,
        "families_used": sorted({r.family for r in rows}),
        "capabilities_used": sorted({r.capability_id for r in rows}),
        "rows": [r.to_dict() for r in rows],
        "digest": sha256_obj([r.to_dict() for r in rows]),
    }


def drill_matrix(registry: ProbeRegistry) -> Dict[str, Any]:
    """Full ``entries x families`` matrix (stricter supplementary evidence)."""
    rows: List[Dict[str, Any]] = []
    misses: List[Dict[str, Any]] = []
    for cid in registry.registered_capabilities():
        for family in MOCK_FAMILIES:
            inj = inject_and_probe(registry, cid, family)
            rows.append(inj.to_dict())
            if not inj.detected:
                misses.append({"capability_id": cid, "family": family})
    total = len(rows)
    return {
        "cases": total,
        "detected": total - len(misses),
        "detection_rate": (total - len(misses)) / total if total else 0.0,
        "misses": misses,
        "rows": rows,
        "digest": sha256_obj(rows),
    }


__all__ = ["drill", "drill_matrix", "inject_and_probe", "expectations_of", "MOCK_FAMILIES"]