"""The regression gate: turn a silent evidence downgrade into a red verdict.

Two evaluation modes
--------------------
``audit_claims``      current ledger claims vs freshly executed probes. A capability that
                     still claims REAL while its observable no longer satisfies the
                     contracts is a **REFUTED_CLAIM**: this is the silent-degradation
                     alarm (implementation rotted to MOCK, ledger untouched).
``evaluate_changes``  a *level table edit* (``before -> after``). Upgrades must be backed by
                     passing probes (fail-closed); downgrades must be backed by fresh probe
                     evidence **and** an acknowledgement bound to that probe run, otherwise
                     the edit is blocked.

Severity contract: ``RED`` blocks (exit code 3 at the CLI), ``ACK`` allows with a record,
``OK`` is a clean pass. Only RED counts as a detection / false positive, so the
detection-rate and false-positive-rate numbers in the artifact are directly comparable.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .model import ProbeReport, sha256_obj
from .registry import ProbeRegistry

#: Ordinal level order (higher = stronger evidence claim). Kept in sync with
#: ``tools.capability_audit_gate.taxonomy``; duplicated here only as a documented fallback
#: so this gate has no hard import dependency on a neighbouring tool.
LEVEL_ORDER = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")

RED, ACK, OK = "RED", "ACK", "OK"


def _rank() -> Dict[str, int]:
    try:  # single source of truth when available
        from tools.capability_audit_gate import taxonomy as _taxonomy

        levels = tuple(getattr(_taxonomy, "LEVELS", LEVEL_ORDER))
        return {level: i for i, level in enumerate(levels)}
    except Exception:  # noqa: BLE001 - fallback is intentionally identical
        return {level: i for i, level in enumerate(LEVEL_ORDER)}


RANK = _rank()


def rank(level: Optional[str]) -> int:
    return RANK.get(str(level or "").upper(), -1)


@dataclass
class Finding:
    capability_id: str
    code: str
    severity: str
    detail: str = ""
    before: Optional[str] = None
    after: Optional[str] = None
    probe_passed: Optional[bool] = None
    failed_kinds: List[str] = field(default_factory=list)
    probe_digest: str = ""
    ack: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    mode: str
    findings: List[Finding] = field(default_factory=list)
    probe_reports: List[ProbeReport] = field(default_factory=list)
    latency_ms: float = 0.0
    per_change_latency_ms: List[Dict[str, Any]] = field(default_factory=list)
    uncovered: List[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(f.severity == RED for f in self.findings):
            return RED
        if any(f.severity == ACK for f in self.findings):
            return ACK
        return OK

    @property
    def red_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == RED]

    def digest(self) -> str:
        return sha256_obj({
            "mode": self.mode,
            "verdict": self.verdict,
            "findings": [f.to_dict() for f in self.findings],
            "probes": [r.to_dict() for r in self.probe_reports],
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "verdict": self.verdict,
            "red_findings": len(self.red_findings),
            "latency_ms": round(self.latency_ms, 3),
            "max_change_latency_ms": round(max((c["elapsed_ms"] for c in self.per_change_latency_ms), default=0.0), 3),
            "uncovered": list(self.uncovered),
            "digest": self.digest(),
            "findings": [f.to_dict() for f in self.findings],
            "probe_reports": [r.to_dict() for r in self.probe_reports],
            "per_change_latency_ms": self.per_change_latency_ms,
        }


def _probe(registry: ProbeRegistry, capability_id: str) -> ProbeReport:
    return registry.run(capability_id)


def audit_claims(registry: ProbeRegistry, levels: Dict[str, str],
                 capabilities: Optional[Iterable[str]] = None) -> GateResult:
    """Compare current claims with live probe evidence (silent-degradation alarm)."""
    started = time.perf_counter()
    ids = list(capabilities) if capabilities is not None else list(registry.registered_capabilities())
    result = GateResult(mode="audit_claims")
    for cid in ids:
        level = str(levels.get(cid, "") or "").upper()
        if level != "REAL":
            continue
        report = _probe(registry, cid)
        result.probe_reports.append(report)
        if report.passed:
            result.findings.append(Finding(capability_id=cid, code="CLAIM_SUPPORTED", severity=OK,
                                           detail="REAL claim reproduced by probe contracts",
                                           after=level, probe_passed=True, probe_digest=report.digest()))
        else:
            result.findings.append(Finding(
                capability_id=cid, code="REFUTED_CLAIM", severity=RED,
                detail=f"ledger claims REAL but probes failed: {report.failed_kinds or [report.error]}",
                after=level, probe_passed=False, failed_kinds=report.failed_kinds,
                probe_digest=report.digest()))
    result.latency_ms = (time.perf_counter() - started) * 1000.0
    return result


def _ack_for(ack: Any, capability_id: str, digest: str) -> Optional[Dict[str, Any]]:
    if not ack:
        return None
    items = ack if isinstance(ack, list) else [ack]
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("capability_id") not in (capability_id, "*"):
            continue
        if str(item.get("probe_digest") or "") != digest:
            return {"_stale": True, **item}
        if not item.get("reason"):
            return {"_stale": True, **item}
        return dict(item)
    return None


def evaluate_changes(registry: ProbeRegistry, before: Dict[str, str], after: Dict[str, str],
                     ack: Any = None, probe_unchanged: bool = False) -> GateResult:
    """Guard a level-table edit: block upgrades without evidence and unbacked downgrades."""
    started = time.perf_counter()
    result = GateResult(mode="evaluate_changes")
    ids = sorted(set(before) | set(after))
    for cid in ids:
        old = str(before.get(cid, "") or "").upper() or None
        new = str(after.get(cid, "") or "").upper() or None
        if old == new and not probe_unchanged:
            continue
        if cid not in registry.entries:
            result.uncovered.append(cid)
            if old != new:
                result.findings.append(Finding(
                    capability_id=cid, code="UNCOVERED_CAPABILITY", severity=ACK,
                    detail="no probe registered: level edit neither verified nor blocked (coverage gap)",
                    before=old, after=new))
            continue
        _t0 = time.perf_counter()
        report = _probe(registry, cid)
        elapsed = (time.perf_counter() - _t0) * 1000.0
        result.probe_reports.append(report)
        result.per_change_latency_ms.append({"capability_id": cid, "elapsed_ms": elapsed,
                                            "before": old, "after": new})
        if old == new:
            result.findings.append(Finding(capability_id=cid, code="NO_CHANGE", severity=OK,
                                           before=old, after=new, probe_passed=report.passed,
                                           probe_digest=report.digest(),
                                           detail="level unchanged (probe requested)"))
            continue
        upgraded = rank(new) > rank(old)
        downgraded = rank(new) < rank(old)
        if upgraded:
            if report.passed:
                result.findings.append(Finding(capability_id=cid, code="UPGRADE_VERIFIED", severity=OK,
                                               before=old, after=new, probe_passed=True,
                                               probe_digest=report.digest(),
                                               detail="upgrade backed by passing probe run"))
            else:
                result.findings.append(Finding(
                    capability_id=cid, code="UNSUPPORTED_UPGRADE", severity=RED, before=old, after=new,
                    probe_passed=False, failed_kinds=report.failed_kinds, probe_digest=report.digest(),
                    detail=f"upgrade to {new} not reproducible: probes failed {report.failed_kinds}"))
            continue
        if downgraded:
            ack_item = _ack_for(ack, cid, report.digest())
            if not report.passed:
                if ack_item and not ack_item.get("_stale"):
                    result.findings.append(Finding(
                        capability_id=cid, code="ACKED_DEGRADATION", severity=ACK, before=old, after=new,
                        probe_passed=False, failed_kinds=report.failed_kinds, probe_digest=report.digest(),
                        ack=ack_item,
                        detail=f"degradation confirmed by probes {report.failed_kinds} and acknowledged"))
                else:
                    result.findings.append(Finding(
                        capability_id=cid, code="DEGRADATION_DETECTED", severity=RED, before=old, after=new,
                        probe_passed=False, failed_kinds=report.failed_kinds, probe_digest=report.digest(),
                        detail=f"probes confirm degradation {report.failed_kinds}: {old}->{new} needs ack"))
            else:
                if ack_item and not ack_item.get("_stale"):
                    result.findings.append(Finding(
                        capability_id=cid, code="ACKED_DOWNGRADE", severity=ACK, before=old, after=new,
                        probe_passed=True, probe_digest=report.digest(), ack=ack_item,
                        detail="observable still satisfies contracts; downgrade accepted with acknowledgement"))
                else:
                    result.findings.append(Finding(
                        capability_id=cid, code="UNSUBSTANTIATED_DOWNGRADE", severity=RED, before=old, after=new,
                        probe_passed=True, probe_digest=report.digest(),
                        detail=f"observable still satisfies every contract: {old}->{new} has no evidence"))
            continue
        result.findings.append(Finding(capability_id=cid, code="NEUTRAL_CHANGE", severity=ACK,
                                       before=old, after=new, probe_passed=report.passed,
                                       probe_digest=report.digest(),
                                       detail="level moved sideways (same ordinal rank)"))
    result.latency_ms = (time.perf_counter() - started) * 1000.0
    return result


def level_histogram(levels: Dict[str, str]) -> Dict[str, int]:
    """Small helper used by report renderers (counts per level, unknown levels included)."""
    out: Dict[str, int] = {}
    for level in levels.values():
        key = str(level or "UNKNOWN").upper()
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def probe_coverage(registry: ProbeRegistry, levels: Dict[str, str]) -> Dict[str, Any]:
    registered = set(registry.registered_capabilities())
    claim_real = {c for c, lv in levels.items() if str(lv).upper() == "REAL"}
    return {
        "ledger_capabilities": len(levels),
        "registered_capabilities": sorted(registered),
        "registered_count": len(registered),
        "covered_real_claims": sorted(claim_real & registered),
        "uncovered_real_claims": sorted(claim_real - registered),
        "real_claim_count": len(claim_real),
    }


__all__ = ["audit_claims", "evaluate_changes", "probe_coverage", "level_histogram", "GateResult",
           "Finding", "rank", "RANK", "RED", "ACK", "OK"]