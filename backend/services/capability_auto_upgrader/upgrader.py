"""Capability auto-upgrader: evidence in, level changes out (append-only).

Wiring::

    audit_capabilities payload ──► AuditWatcher ──► register / observe / mismatch events
                                                   │
    pytest junit + benchmark record ──► EvidenceRun ──► RunStore ──► streak.analyse ──► streak.decide
                                                                       └──► FeatureLedger.append (hash-chained)

Guarantees (all covered by the package test suite):

* promotion needs ``policy.promotion_window`` **distinct** qualifying runs;
* a duplicated run is dropped before it can extend a streak;
* a demotion is a new ledger row carrying ``level_before``, the failing run digest
  and the reason codes — history is never rewritten;
* an external audit level that disagrees with the derived level is recorded as a
  ``mismatch`` event and does **not** overwrite the derived level.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import ledger as ledger_mod
from . import streak as streak_mod
from .ledger import FeatureLedger
from .model import (
    LEVEL_INDEX,
    LEVELS,
    UPGRADABLE_LEVELS,
    EvidenceError,
    EvidenceRun,
    normalise_level,
    sha256_obj,
    utc_now,
)
from .policy import UpgradePolicy
from .store import RunStore
from .watcher import AuditSnapshot, AuditWatcher

OPERATOR = "capability_auto_upgrader/1.0"
#: Level assumed when a capability starts producing runs without being registered.
IMPLICIT_LEVEL = "STUB"


class CapabilityAutoUpgrader:
    def __init__(
        self,
        root: Path | str,
        policy: Optional[UpgradePolicy] = None,
        tenant: str = "platform",
        operator: str = OPERATOR,
        clock: Any = utc_now,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy = policy or UpgradePolicy()
        self.tenant = tenant
        self.operator = operator
        self.clock = clock
        self.ledger = FeatureLedger(self.root, tenant=tenant)
        self.store = RunStore(self.root)
        self.watcher = AuditWatcher()

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self.ledger.close()
        self.store.close()

    def __enter__(self) -> "CapabilityAutoUpgrader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- registration ----------------------------------------------------
    def register(
        self,
        capability_id: str,
        level: str,
        reason_codes: Sequence[str] = ("declared_by_audit_api",),
        audit_level: Optional[str] = None,
        snapshot_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        level = normalise_level(level)
        known = self.ledger.level_of(capability_id)
        if known is not None:
            raise EvidenceError(f"{capability_id} is already registered at {known}")
        event = self.ledger.append(
            {
                "capability_id": capability_id,
                "direction": ledger_mod.DIRECTION_REGISTER,
                "level_before": None,
                "level_after": level,
                "reason_codes": list(reason_codes),
                "policy_digest": self.policy.digest,
                "window_digests": [],
                "trigger_digest": snapshot_digest or sha256_obj({"declared": capability_id, "level": level}),
                "operator": self.operator,
                "audit_level": normalise_level(audit_level) if audit_level else None,
                "snapshot_digest": snapshot_digest,
            },
            ts=self.clock(),
        )
        return event

    # -- audit API listener ---------------------------------------------
    def ingest_audit_snapshot(self, payload: Any) -> Dict[str, Any]:
        """Consume one ``audit_capabilities`` payload.

        Unknown capabilities are registered at the level the audit reports; level
        changes and ledger/audit disagreements are recorded as ``observed`` /
        ``mismatch`` events (recorded, never applied).
        """
        snapshot = AuditSnapshot.from_api_payload(payload)
        observation = self.watcher.observe(snapshot)
        registered: List[str] = []
        events: List[Dict[str, Any]] = []
        mismatches: List[Dict[str, Any]] = []

        for row in snapshot.rows:
            known = self.ledger.level_of(row.capability_id)
            if known is None:
                events.append(
                    self.register(
                        row.capability_id,
                        row.level,
                        reason_codes=("declared_by_audit_api", f"audit_level={row.level}"),
                        audit_level=row.level,
                        snapshot_digest=snapshot.digest,
                    )
                )
                registered.append(row.capability_id)
                continue

            change = next(
                (c for c in observation["changes"] if c["capability_id"] == row.capability_id),
                None,
            )
            if change is not None:
                events.append(
                    self.ledger.append(
                        {
                            "capability_id": row.capability_id,
                            "direction": ledger_mod.DIRECTION_OBSERVED,
                            "level_before": known,
                            "level_after": known,  # observation never moves the level
                            "reason_codes": [
                                f"audit_{change['kind']}",
                                f"audit_before={change['before']}",
                                f"audit_after={change['after']}",
                            ],
                            "policy_digest": self.policy.digest,
                            "window_digests": [],
                            "trigger_digest": snapshot.digest,
                            "operator": self.operator,
                            "audit_level": row.level,
                            "snapshot_digest": snapshot.digest,
                        },
                        ts=self.clock(),
                    )
                )

            if row.level != known:
                mismatches.append(
                    self.ledger.append(
                        {
                            "capability_id": row.capability_id,
                            "direction": ledger_mod.DIRECTION_MISMATCH,
                            "level_before": known,
                            "level_after": known,  # recorded, not applied
                            "reason_codes": [
                                "audit_ledger_disagreement",
                                f"audit_level={row.level}",
                                f"ledger_level={known}",
                            ],
                            "policy_digest": self.policy.digest,
                            "window_digests": [],
                            "trigger_digest": snapshot.digest,
                            "operator": self.operator,
                            "audit_level": row.level,
                            "snapshot_digest": snapshot.digest,
                        },
                        ts=self.clock(),
                    )
                )

        # A capability that disappears from the audit API is recorded, but its
        # level is kept: absence of a row is not evidence of absence of the code.
        for change in observation["changes"]:
            if change["kind"] != "removed":
                continue
            known = self.ledger.level_of(change["capability_id"])
            if known is None:
                continue
            events.append(
                self.ledger.append(
                    {
                        "capability_id": change["capability_id"],
                        "direction": ledger_mod.DIRECTION_OBSERVED,
                        "level_before": known,
                        "level_after": known,
                        "reason_codes": ["audit_row_removed", f"audit_before={change['before']}"],
                        "policy_digest": self.policy.digest,
                        "window_digests": [],
                        "trigger_digest": snapshot.digest,
                        "operator": self.operator,
                        "snapshot_digest": snapshot.digest,
                    },
                    ts=self.clock(),
                )
            )

        return {
            "snapshot_digest": snapshot.digest,
            "n_rows": len(snapshot.rows),
            "changes": observation["changes"],
            "registered": registered,
            "n_events": len(events),
            "mismatches": [m["capability_id"] for m in mismatches],
        }

    # -- evidence ingestion ----------------------------------------------
    def record_run(self, run: EvidenceRun) -> Dict[str, Any]:
        """Store one run, re-evaluate its capability, and act on the decision."""
        if not isinstance(run, EvidenceRun):
            raise EvidenceError("record_run expects an EvidenceRun instance")
        cap = run.capability_id
        if self.ledger.level_of(cap) is None:
            self.register(
                cap,
                IMPLICIT_LEVEL,
                reason_codes=("implicit_registration_first_run", f"assumed_level={IMPLICIT_LEVEL}"),
            )

        if self.store.has(run.digest):
            # Replay of the very same evidence: a no-op, never a second streak step.
            return {
                "status": "duplicate_run",
                "capability_id": cap,
                "run_digest": run.digest,
                "level": self.ledger.level_of(cap),
                "note": "identical evidence already recorded; streak not affected",
            }

        expected_seq = self.store.latest_seq(cap) + 1
        if run.seq != expected_seq:
            raise EvidenceError(
                f"{cap}: seq must be {expected_seq} (next after stored runs), got {run.seq}"
            )
        if not self.store.add(run):  # pragma: no cover - guarded by has()
            return {
                "status": "duplicate_run",
                "capability_id": cap,
                "run_digest": run.digest,
                "level": self.ledger.level_of(cap),
                "note": "identical evidence already recorded; streak not affected",
            }

        level = self.ledger.level_of(cap) or IMPLICIT_LEVEL
        analysis = streak_mod.analyse(self.store.runs(cap), self.policy)
        decision = streak_mod.decide(level, analysis, self.policy)

        event: Optional[Dict[str, Any]] = None
        if decision.changed:
            direction = (
                ledger_mod.DIRECTION_UPGRADE
                if LEVEL_INDEX[decision.level_after] > LEVEL_INDEX[decision.level_before]
                else ledger_mod.DIRECTION_DOWNGRADE
            )
            evidence = {
                "run": run.as_dict(),
                "streak": {
                    "pass_streak": analysis.pass_streak,
                    "fail_streak": analysis.fail_streak,
                    "insufficient_streak": analysis.insufficient_streak,
                    "n_runs": len(analysis.verdicts),
                    "verdicts": [v.as_dict() for v in analysis.verdicts][-self.policy.promotion_window:],
                },
                "policy": self.policy.as_dict(),
            }
            event = self.ledger.append(
                {
                    "capability_id": cap,
                    "direction": direction,
                    "level_before": decision.level_before,
                    "level_after": decision.level_after,
                    "reason_codes": list(decision.reason_codes),
                    "policy_digest": self.policy.digest,
                    "window_digests": list(decision.window_digests),
                    "trigger_digest": run.digest,
                    "operator": self.operator,
                    "evidence": evidence,
                },
                ts=self.clock(),
            )

        evaluation = self.store.log_evaluation(
            {
                "capability_id": cap,
                "run_digest": run.digest,
                "seq": run.seq,
                "level_before": decision.level_before,
                "action": decision.action,
                "level_after": decision.level_after,
                "reason_codes": list(decision.reason_codes),
                "window_digests": list(decision.window_digests),
                "policy_digest": self.policy.digest,
                "event_sha256": event["sha256"] if event else None,
                "streaks": {
                    "pass": analysis.pass_streak,
                    "fail": analysis.fail_streak,
                    "insufficient": analysis.insufficient_streak,
                },
                "verdict": analysis.latest.as_dict() if analysis.latest else None,
            }
        )

        return {
            "status": "applied" if event else "hold",
            "capability_id": cap,
            "run_digest": run.digest,
            "decision": decision.as_dict(),
            "event": event,
            "evaluation": evaluation,
            "streaks": {
                "pass": analysis.pass_streak,
                "fail": analysis.fail_streak,
                "insufficient": analysis.insufficient_streak,
            },
            "level": self.ledger.level_of(cap),
        }

    # -- read ------------------------------------------------------------
    def levels(self) -> Dict[str, str]:
        return self.ledger.levels()

    def status(self) -> List[Dict[str, Any]]:
        levels = self.ledger.levels()
        out = []
        for cap in sorted(levels):
            runs = self.store.runs(cap)
            analysis = streak_mod.analyse(runs, self.policy)
            out.append(
                {
                    "capability_id": cap,
                    "level": levels[cap],
                    "n_runs": len(runs),
                    "pass_streak": analysis.pass_streak,
                    "fail_streak": analysis.fail_streak,
                    "latest_run_digest": runs[-1].digest if runs else None,
                    "latest_pass_rate": analysis.latest.pass_rate if analysis.latest else None,
                    "latest_error_pct": analysis.latest.error_pct if analysis.latest else None,
                    "history_len": len(self.ledger.history(cap)),
                }
            )
        return out

    def history(self, capability_id: str) -> List[Dict[str, Any]]:
        return self.ledger.history(capability_id)

    def verify(self) -> Dict[str, Any]:
        ledger_report = self.ledger.verify()
        evaluation_report = self.store.verify_evaluations()
        return {
            "ok": ledger_report["ok"] and evaluation_report["ok"],
            "ledger": ledger_report,
            "evaluations": evaluation_report,
            "policy_digest": self.policy.digest,
        }