"""Threshold-driven, fail-closed ledger guard (p338w3-n07).

The guard consumes :class:`~.evidence.CompletenessReport` values and moves the
capability ledger:

* ``score >= promote``, no contradiction, and every critical requirement
  (independent verification, verifiable digest) present -> ``REAL`` (upgrade
  from ``MOCK``/``STUB``/``WITHHELD``/``NOT_SUPPORTED``, hold if already REAL)
* ``score < demote``                          -> ``WITHHELD`` (evidence gap)
* any contradiction                           -> ``MOCK`` (claim contradicted)
* malformed / unidentifiable report           -> ``WITHHELD`` (fail-closed)
* intermediate score while currently ``REAL`` -> ``WITHHELD`` (a REAL claim
  whose support eroded is not left standing)
* intermediate score below ``REAL``           -> ``HOLD`` (e.g. stays ``WITHHELD``)

Every decision — including HOLD — is written to an append-only, sha256-chained
audit log together with the timestamp, the triggering evidence digest and the
completeness change.  The state file is replaced atomically; a corrupt or
unreadable state file starts from an empty ledger instead of inheriting a
possibly forged level.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from tools.capability_auto_audit.append_only_log import AppendOnlyAuditLog

from .evidence import (
    CRITICAL_REQUIREMENTS,
    LEVELS,
    CapabilityEvidence,
    CompletenessReport,
    evaluate,
)

UPGRADE = "UPGRADE"
DOWNGRADE = "DOWNGRADE"
HOLD = "HOLD"

STATE_SCHEMA = "platform-governance-ledger/1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class Thresholds:
    promote: float = 0.8
    demote: float = 0.5

    def __post_init__(self) -> None:
        if not (0.0 <= self.demote <= self.promote <= 1.0):
            raise ValueError(
                f"invalid thresholds: demote={self.demote} promote={self.promote} "
                "must satisfy 0 <= demote <= promote <= 1")

    def as_dict(self) -> Dict[str, float]:
        return {"promote": self.promote, "demote": self.demote}


@dataclass
class Decision:
    capability_id: str
    from_level: str
    to_level: str
    action: str
    reason_code: str
    rationale: str
    score: float
    evidence_digest: str
    contradictions: int = 0

    @property
    def changed(self) -> bool:
        return self.from_level != self.to_level

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "action": self.action,
            "changed": self.changed,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
            "score": round(self.score, 6),
            "evidence_digest": self.evidence_digest,
            "contradictions": self.contradictions,
        }


def decide(report: CompletenessReport, current_level: str,
           thresholds: Thresholds = Thresholds()) -> Decision:
    """Map a completeness report to a ledger transition (fail-closed)."""
    level = current_level if current_level in LEVELS else "WITHHELD"
    if not report.valid:
        return Decision(
            report.capability_id, level, "WITHHELD", DOWNGRADE,
            "INVALID_EVIDENCE",
            f"evidence report invalid ({report.invalid_reason}); fail-closed demotion",
            0.0, report.evidence_digest,
        )
    if report.contradictions:
        return Decision(
            report.capability_id, level, "MOCK", DOWNGRADE,
            "CONTRADICTING_EVIDENCE",
            f"{len(report.contradictions)} contradiction(s) against the claim; "
            "claim demoted to MOCK",
            report.score, report.evidence_digest, len(report.contradictions),
        )
    if report.score >= thresholds.promote:
        missing_critical = [k for k in CRITICAL_REQUIREMENTS
                           if not report.checks.get(k)]
        if missing_critical:
            if level == "REAL":
                return Decision(report.capability_id, "REAL", "WITHHELD", DOWNGRADE,
                                "CRITICAL_EVIDENCE_MISSING",
                                f"critical requirement(s) {missing_critical} missing; "
                                "REAL demoted fail-closed",
                                report.score, report.evidence_digest)
            return Decision(report.capability_id, level, level, HOLD,
                            "CRITICAL_EVIDENCE_MISSING",
                            f"completeness {report.score:.3f} sufficient but critical "
                            f"requirement(s) {missing_critical} missing; no promotion",
                            report.score, report.evidence_digest)
        if level == "REAL":
            return Decision(report.capability_id, "REAL", "REAL", HOLD,
                            "CHAIN_COMPLETE_ALREADY_REAL",
                            f"completeness {report.score:.3f} >= {thresholds.promote}",
                            report.score, report.evidence_digest)
        return Decision(report.capability_id, level, "REAL", UPGRADE,
                        "COMPLETENESS_PROMOTE",
                        f"completeness {report.score:.3f} >= {thresholds.promote} "
                        "with no contradiction",
                        report.score, report.evidence_digest)
    if report.score < thresholds.demote:
        return Decision(report.capability_id, level, "WITHHELD", DOWNGRADE,
                        "INSUFFICIENT_COMPLETENESS",
                        f"completeness {report.score:.3f} < {thresholds.demote}",
                        report.score, report.evidence_digest)
    # intermediate band
    if level == "REAL":
        return Decision(report.capability_id, "REAL", "WITHHELD", DOWNGRADE,
                        "PARTIAL_EVIDENCE_REGRESSION",
                        f"REAL claim eroded to completeness {report.score:.3f} "
                        f"(< {thresholds.promote})",
                        report.score, report.evidence_digest)
    return Decision(report.capability_id, level, level, HOLD,
                    "INTERMEDIATE_COMPLETENESS_HOLD",
                    f"completeness {report.score:.3f} in "
                    f"[{thresholds.demote}, {thresholds.promote}); level unchanged",
                    report.score, report.evidence_digest)


# ---------------------------------------------------------------------------
# state + ledger
# ---------------------------------------------------------------------------
def load_state(path: Optional[str | Path]) -> Dict[str, Any]:
    """Read ledger state; unreadable/corrupt state is treated as empty."""
    empty = {"schema": STATE_SCHEMA, "levels": {}, "scores": {}, "digest": ""}
    if not path:
        return empty
    p = Path(path)
    if not p.is_file():
        return empty
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(raw, Mapping) or raw.get("schema") != STATE_SCHEMA:
        return empty
    levels = raw.get("levels")
    scores = raw.get("scores")
    if not isinstance(levels, Mapping) or not isinstance(scores, Mapping):
        return empty
    clean_levels = {str(k): str(v) for k, v in levels.items() if v in LEVELS}
    clean_scores = {}
    for k, v in scores.items():
        try:
            clean_scores[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return {"schema": STATE_SCHEMA, "levels": clean_levels,
            "scores": clean_scores, "digest": str(raw.get("digest", ""))}


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class LedgerGuardResult:
    decisions: List[Decision] = field(default_factory=list)
    levels: Dict[str, str] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    audit_tail: str = ""
    audit_entries: int = 0
    state_digest: str = ""

    @property
    def promoted(self) -> List[Dict[str, Any]]:
        return [d.as_dict() for d in self.decisions if d.action == UPGRADE]

    @property
    def demoted(self) -> List[Dict[str, Any]]:
        return [d.as_dict() for d in self.decisions if d.action == DOWNGRADE]

    @property
    def held(self) -> List[Dict[str, Any]]:
        return [d.as_dict() for d in self.decisions if d.action == HOLD]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decisions": [d.as_dict() for d in self.decisions],
            "levels": dict(self.levels),
            "scores": {k: round(v, 6) for k, v in self.scores.items()},
            "promoted": len(self.promoted),
            "demoted": len(self.demoted),
            "held": len(self.held),
            "audit_tail": self.audit_tail,
            "audit_entries": self.audit_entries,
            "state_digest": self.state_digest,
        }


class LedgerGuard:
    """Scan bundles, decide transitions, persist state and audit chain."""

    def __init__(self, thresholds: Thresholds = Thresholds(),
                 state_path: Optional[str | Path] = None,
                 audit_path: Optional[str | Path] = None):
        self.thresholds = thresholds
        self.state_path = Path(state_path) if state_path else None
        self.audit_path = Path(audit_path) if audit_path else None
        self.state = load_state(self.state_path)

    def level_of(self, capability_id: str, declared: str = "WITHHELD") -> str:
        return self.state["levels"].get(capability_id, declared)

    def run(self, bundles: Iterable[CapabilityEvidence],
            *, ts: Optional[str] = None) -> LedgerGuardResult:
        ts = ts or _now()
        bundles = list(bundles)
        reports = [evaluate(b) for b in bundles]
        result = LedgerGuardResult(
            levels=dict(self.state["levels"]),
            scores=dict(self.state["scores"]),
        )
        log = None
        if self.audit_path is not None:
            log = AppendOnlyAuditLog(self.audit_path)

        for report in reports:
            current = self.level_of(report.capability_id, report.current_level)
            decision = decide(report, current, self.thresholds)
            before_score = float(self.state["scores"].get(report.capability_id, 0.0))
            delta = round(report.score - before_score, 6)
            result.decisions.append(decision)
            result.levels[report.capability_id] = decision.to_level
            result.scores[report.capability_id] = report.score

            payload = {
                "ts": ts,
                "thresholds": self.thresholds.as_dict(),
                "completeness": round(report.score, 6),
                "completeness_before": round(before_score, 6),
                "completeness_delta": delta,
                "checks": dict(report.checks),
                "missing": list(report.missing),
                "contradictions": len(report.contradictions),
                "admissible": report.admissible,
                "valid": report.valid,
            }
            if log is not None:
                log.append(
                    action="GOVERNANCE_GATE",
                    capability_id=report.capability_id,
                    from_level=decision.from_level,
                    to_level=decision.to_level,
                    reason_code=decision.reason_code,
                    evidence_digest=decision.evidence_digest,
                    payload=payload,
                    ts=ts,
                )
            self.state["levels"][report.capability_id] = decision.to_level
            self.state["scores"][report.capability_id] = report.score

        state_body = {
            "schema": STATE_SCHEMA,
            "levels": dict(sorted(self.state["levels"].items())),
            "scores": {k: round(v, 6) for k, v in sorted(self.state["scores"].items())},
        }
        from .evidence import digest_json
        state_digest = digest_json(state_body)
        if self.state_path is not None:
            _atomic_write(self.state_path, {**state_body, "digest": state_digest})
        result.state_digest = state_digest

        if log is not None:
            integrity = log.verify()
            result.audit_tail = integrity.get("head_sha256", "")
            result.audit_entries = integrity.get("entries", 0)
        return result