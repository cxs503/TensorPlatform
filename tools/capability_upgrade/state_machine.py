"""Evidence-level state machine: ladder climbs, regression rollback, audit trail.

The machine owns *when a capability's evidence level changes*.  Three properties
make the transition auditable rather than asserted:

1. **Rung-by-rung.**  A level never jumps.  ``MOCK -> WITHHELD -> REAL`` is two
   moves, each backed by the rule of that rung and each written to the audit
   trail with its own completeness.  A skipped rung is visible as a missing
   record, not as a silently absent nuance.
2. **Regression is checked before every climb.**  Each ``apply`` first re-checks
   the rule that granted the *current* level.  If the incoming evidence no longer
   satisfies it (evidence withdrawn, error drifted out of tolerance, provenance
   lost), the machine rolls back and says which requirement failed — an upgrade is
   never one-way.
3. **A defect is a state, not a number.**  An injected defect opens a blocking
   record on the slot: no further climb while it is open, independent of what the
   next evidence bundle claims.  It is closed by :meth:`resolve_defect`, which is
   itself an audited action.

The machine is deterministic and idempotent: re-applying the same evidence digest
to an unchanged slot produces ``action="noop"`` and writes nothing.  State is a
JSON document written atomically; the trail is the hash-chained
:class:`~tools.capability_upgrade.upgrade_audit.UpgradeAudit`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .completeness import Assessment, EvidenceBundle, assess, chain_report
from .model import LEVELS, LEVEL_RANK, digest
from .upgrade_audit import UpgradeAudit, write_snapshot
from .upgrade_rules import UpgradeRule, UpgradeRuleTable

STATE_SCHEMA = "capability-upgrade-state/1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Slot:
    """One capability position under the state machine."""

    capability_slug: str
    level: str = "WITHHELD"
    initial_level: str = "WITHHELD"
    registered_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_rule_id: Optional[str] = None
    last_completeness: Optional[float] = None
    last_bundle_digest: Optional[str] = None
    last_snapshot: Dict[str, Any] = field(default_factory=dict)
    defects: List[Dict[str, Any]] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_slug": self.capability_slug,
            "level": self.level,
            "level_rank": LEVEL_RANK.get(self.level, 0),
            "initial_level": self.initial_level,
            "registered_at": self.registered_at,
            "updated_at": self.updated_at,
            "last_rule_id": self.last_rule_id,
            "last_completeness": self.last_completeness,
            "last_bundle_digest": self.last_bundle_digest,
            "last_snapshot": self.last_snapshot,
            "defects": list(self.defects),
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "Slot":
        return cls(
            capability_slug=str(doc["capability_slug"]),
            level=str(doc.get("level", "WITHHELD")).upper(),
            initial_level=str(doc.get("initial_level", "WITHHELD")).upper(),
            registered_at=str(doc.get("registered_at") or _now()),
            updated_at=str(doc.get("updated_at") or _now()),
            last_rule_id=doc.get("last_rule_id"),
            last_completeness=doc.get("last_completeness"),
            last_bundle_digest=doc.get("last_bundle_digest"),
            last_snapshot=dict(doc.get("last_snapshot") or {}),
            defects=list(doc.get("defects") or []),
            history=list(doc.get("history") or []),
        )

    @property
    def open_defects(self) -> List[Dict[str, Any]]:
        return [d for d in self.defects if str(d.get("status", "open")) == "open"]


class CapabilityStateMachine:
    """Deterministic evidence-level state machine with rollback and audit."""

    def __init__(self, rules: Optional[UpgradeRuleTable] = None,
                 audit: Optional[UpgradeAudit] = None,
                 state_path: Optional[Path | str] = None):
        # explicit None checks: an empty audit trail is falsy (it defines __len__)
        self.rules = rules if rules is not None else UpgradeRuleTable.default()
        self.audit = audit if audit is not None else UpgradeAudit(None)
        self.state_path = Path(state_path) if state_path is not None else None
        self.slots: Dict[str, Slot] = {}
        if self.state_path is not None and self.state_path.exists():
            self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        assert self.state_path is not None
        doc = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.slots = {k: Slot.from_dict(v) for k, v in (doc.get("slots") or {}).items()}

    def save(self) -> Optional[Path]:
        if self.state_path is None:
            return None
        return write_snapshot(self.state_path, {
            "schema": STATE_SCHEMA,
            "rules_digest": self.rules.digest,
            "slots": {k: v.as_dict() for k, v in self.slots.items()},
            "saved_at": _now(),
        })

    # -- registry ----------------------------------------------------------
    def register(self, capability_slug: str, level: str = "WITHHELD",
                 operator: str = "auto", reason: str = "initial registration") -> Dict[str, Any]:
        """Create (or re-affirm) a slot at ``level``; the act is audited."""
        slug = str(capability_slug)
        level = str(level).upper()
        if level not in LEVEL_RANK:
            raise ValueError(f"unknown evidence level {level!r}; allowed={LEVELS}")
        slot = Slot(capability_slug=slug, level=level, initial_level=level)
        self.slots[slug] = slot
        record = self.audit.append({
            "capability_slug": slug, "direction": "register",
            "from_level": None, "to_level": level,
            "rule_id": None, "rule_digest": None, "rules_digest": self.rules.digest,
            "evidence_snapshot": {"before": {}, "after": {}},
            "completeness": {"before": None, "after": None},
            "missing_before": [], "reason": reason, "operator": operator,
            "batch_id": None,
        })
        slot.history.append({"timestamp": record["timestamp"], "direction": "register",
                             "from_level": None, "to_level": level,
                             "audit_id": record["audit_id"]})
        self.save()
        return {"capability_slug": slug, "level": level, "action": "registered",
                "audit_id": record["audit_id"]}

    def slot(self, capability_slug: str) -> Slot:
        try:
            return self.slots[str(capability_slug)]
        except KeyError as exc:  # pragma: no cover - guarded by callers
            raise KeyError(f"unknown capability slot: {capability_slug}") from exc

    def level_of(self, capability_slug: str) -> str:
        return self.slot(capability_slug).level

    def slots_doc(self) -> Dict[str, Any]:
        return {k: v.as_dict() for k, v in self.slots.items()}

    def trail(self, capability_slug: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.audit.records(capability_slug)

    # -- adjudication ------------------------------------------------------
    def apply(self, capability_slug: str, bundle: EvidenceBundle, operator: str = "auto",
              max_steps: Optional[int] = None, reason: str = "") -> Dict[str, Any]:
        """Adjudicate one evidence bundle: roll back if regressed, else climb.

        ``max_steps`` bounds how many rungs may be climbed in one call (``None``
        = climb as far as the evidence allows).  The return value is the decision
        record; every level change it reports has a matching audit record.
        """
        slot = self.slot(capability_slug)
        if bundle.capability_id != slot.capability_slug:
            raise ValueError("bundle.capability_id does not match the slot")
        before_level = slot.level
        batch_id = self._new_batch(slot, bundle)

        # 1) regression: can the evidence still support the level we are at?
        #    Only a level that an adjudication actually granted can regress; a
        #    level asserted at registration is a prior, not an evidence claim.
        #    A level asserted at registration is a prior, not an evidence claim:
        #    it regresses only once evidence exists to contradict it (an empty
        #    bundle cannot demote anything - "we measured nothing" is not a defect).
        has_evidence = any(EvidenceBundle.admissible(i) for i in bundle.items)
        incoming = self.rules.incoming(before_level)
        if incoming is not None and (slot.last_bundle_digest is not None or has_evidence):
            check = assess(incoming, bundle)
            if not check.satisfied:
                # the honest level is the highest rung the new evidence supports,
                # capped one rung below the current level (a defect never raises)
                reachable = chain_report(self.rules, bundle)["best_reachable"]
                target = (reachable if LEVEL_RANK.get(reachable, 0)
                          < LEVEL_RANK.get(before_level, 0)
                          else (self.rules.rollback_target("evidence_regression")
                                or "WITHHELD"))
                record = self._apply_move(
                    slot, target, direction="rollback", rule=incoming, assessment=check,
                    bundle=bundle, operator=operator, batch_id=batch_id,
                    reason=reason or ("evidence regression: "
                                      + (", ".join(check.missing) or "requirements no longer met")),
                    blocked_by="evidence_regression")
                return self._decision(slot, before_level, "rolled_back", batch_id, [record],
                                      [check], operator)

        # 2) climb rung by rung while every requirement of the rung holds
        steps: List[Dict[str, Any]] = []
        checks: List[Assessment] = []
        cur = slot.level
        while True:
            if slot.open_defects:
                break
            if max_steps is not None and len(steps) >= max_steps:
                break
            nxt = self.rules.next_level(cur)
            if nxt is None:
                break
            rule = self.rules.rule_between(cur, nxt)
            check = assess(rule, bundle)
            checks.append(check)
            if not check.satisfied:
                break
            steps.append(self._apply_move(
                slot, nxt, direction="upgrade", rule=rule, assessment=check,
                bundle=bundle, operator=operator, batch_id=batch_id,
                reason=reason or (f"{rule.rule_id}: all {len(check.requirements)} "
                                  "requirements met"),
                blocked_by=None))
            cur = nxt

        if steps:
            return self._decision(slot, before_level, "upgraded", batch_id, steps, checks,
                                  operator)

        # 3) nothing moved: noop if the same evidence was already adjudicated
        if (slot.last_bundle_digest == bundle.digest and before_level == slot.level
                and not reason):
            return {"capability_slug": slot.capability_slug, "action": "noop",
                    "from_level": before_level, "to_level": slot.level,
                    "batch_id": batch_id, "steps": [], "checks": [],
                    "operator": operator, "audit_ids": [],
                    "reason": "same evidence digest already adjudicated"}

        blocking = checks[-1] if checks else None
        if slot.open_defects:
            hold_reason = ("blocked by open defect(s): "
                           + ", ".join(d["defect_id"] for d in slot.open_defects))
        elif blocking is not None:
            hold_reason = (f"{blocking.rule_id} unmet: "
                           + (", ".join(blocking.missing) or "no requirement satisfied"))
        else:
            hold_reason = reason or "no further rung configured"
        record = self.audit.append({
            "capability_slug": slot.capability_slug, "direction": "hold",
            "from_level": slot.level, "to_level": slot.level,
            "rule_id": blocking.rule_id if blocking else None,
            "rule_digest": None, "rules_digest": self.rules.digest,
            "evidence_snapshot": {"before": slot.last_snapshot,
                                  "after": bundle.snapshot()},
            "completeness": {"before": slot.last_completeness,
                             "after": blocking.completeness if blocking else None},
            "missing_before": list(blocking.missing) if blocking else [],
            "reason": hold_reason, "operator": operator, "batch_id": batch_id,
        })
        slot.last_bundle_digest = bundle.digest
        slot.last_snapshot = bundle.snapshot()
        slot.last_completeness = blocking.completeness if blocking else slot.last_completeness
        slot.updated_at = record["timestamp"]
        slot.history.append({"timestamp": record["timestamp"], "direction": "hold",
                             "from_level": slot.level, "to_level": slot.level,
                             "audit_id": record["audit_id"]})
        self.save()
        return {"capability_slug": slot.capability_slug, "action": "held",
                "from_level": before_level, "to_level": slot.level,
                "batch_id": batch_id, "steps": [], "checks": [c.as_dict() for c in checks],
                "operator": operator, "audit_ids": [record["audit_id"]],
                "reason": hold_reason, "missing": list(blocking.missing) if blocking else []}

    def recheck(self, capability_slug: str, bundle: EvidenceBundle,
                operator: str = "auto") -> Dict[str, Any]:
        """Periodic re-adjudication: identical to :meth:`apply` but always audited."""
        return self.apply(capability_slug, bundle, operator=operator,
                          reason="periodic recheck")

    # -- rollback ----------------------------------------------------------
    def inject_defect(self, capability_slug: str, defect: Mapping[str, Any],
                      operator: str = "auto") -> Dict[str, Any]:
        """Register a defect and roll the slot down to the level it can support.

        The target level comes from the rules table's ``rollback_rules`` for that
        defect kind, so "what does a broken content address cost you?" is
        configuration, not a branch in code.  A defect that would not lower the
        current level is still recorded (direction ``defect``) but changes nothing
        — a defect never *raises* a level.
        """
        slot = self.slot(capability_slug)
        kind = str(defect.get("kind") or "unspecified")
        target = self.rules.rollback_target(kind) or "WITHHELD"
        detail = defect.get("detail")
        defect_id = "df-" + digest({"slug": slot.capability_slug, "kind": kind,
                                    "detail": detail, "seq": len(slot.defects)})[:16]
        entry = {"defect_id": defect_id, "kind": kind, "status": "open",
                 "reason": str(defect.get("reason", "")), "detail": detail,
                 "injected_at": _now(), "injected_by": operator}
        slot.defects.append(entry)
        before_level = slot.level
        batch_id = self._new_batch(slot, None)
        lowers = LEVEL_RANK.get(target, 0) < LEVEL_RANK.get(before_level, 0)

        if lowers:
            record = self.audit.append({
                "capability_slug": slot.capability_slug, "direction": "rollback",
                "from_level": before_level, "to_level": target,
                "rule_id": None, "rule_digest": None, "rules_digest": self.rules.digest,
                "evidence_snapshot": {
                    "before": slot.last_snapshot,
                    "after": {"defect": entry, "capability_id": slot.capability_slug},
                },
                "completeness": {"before": slot.last_completeness, "after": 0.0},
                "missing_before": [kind],
                "reason": f"defect {kind}: {entry['reason'] or 'no detail supplied'}",
                "operator": operator, "batch_id": batch_id, "defect_id": defect_id,
                "rollback_kind": kind,
            })
            slot.level = target
            slot.last_rule_id = None
            slot.last_completeness = 0.0
            slot.last_bundle_digest = None
            slot.updated_at = record["timestamp"]
            slot.history.append({"timestamp": record["timestamp"], "direction": "rollback",
                                 "from_level": before_level, "to_level": target,
                                 "audit_id": record["audit_id"], "defect_id": defect_id})
            self.save()
            return {"capability_slug": slot.capability_slug, "action": "rolled_back",
                    "from_level": before_level, "to_level": target, "defect": entry,
                    "batch_id": batch_id, "audit_ids": [record["audit_id"]],
                    "reason": record["reason"]}

        record = self.audit.append({
            "capability_slug": slot.capability_slug, "direction": "defect",
            "from_level": before_level, "to_level": before_level,
            "rule_id": None, "rule_digest": None, "rules_digest": self.rules.digest,
            "evidence_snapshot": {"before": slot.last_snapshot,
                                  "after": {"defect": entry}},
            "completeness": {"before": slot.last_completeness,
                             "after": slot.last_completeness},
            "missing_before": [], "reason": f"defect {kind} recorded below current level",
            "operator": operator, "batch_id": batch_id, "defect_id": defect_id,
            "rollback_kind": kind,
        })
        slot.updated_at = record["timestamp"]
        slot.history.append({"timestamp": record["timestamp"], "direction": "defect",
                             "from_level": before_level, "to_level": before_level,
                             "audit_id": record["audit_id"], "defect_id": defect_id})
        self.save()
        return {"capability_slug": slot.capability_slug, "action": "defect_recorded",
                "from_level": before_level, "to_level": before_level, "defect": entry,
                "batch_id": batch_id, "audit_ids": [record["audit_id"]],
                "reason": record["reason"]}

    def resolve_defect(self, capability_slug: str, defect_id: Optional[str] = None,
                       operator: str = "auto", note: str = "") -> Dict[str, Any]:
        """Close one (or every) open defect; the slot may climb again afterwards."""
        slot = self.slot(capability_slug)
        closed: List[str] = []
        for entry in slot.defects:
            if entry.get("status") != "open":
                continue
            if defect_id is not None and entry["defect_id"] != defect_id:
                continue
            entry["status"] = "resolved"
            entry["resolved_at"] = _now()
            entry["resolved_by"] = operator
            entry["resolution_note"] = note
            closed.append(entry["defect_id"])
        record = self.audit.append({
            "capability_slug": slot.capability_slug, "direction": "defect",
            "from_level": slot.level, "to_level": slot.level,
            "rule_id": None, "rule_digest": None, "rules_digest": self.rules.digest,
            "evidence_snapshot": {"before": slot.last_snapshot, "after": {}},
            "completeness": {"before": slot.last_completeness,
                             "after": slot.last_completeness},
            "missing_before": [], "reason": f"defects resolved: {closed or 'none open'} "
                                            f"{note}".strip(),
            "operator": operator, "batch_id": None, "defect_id": defect_id,
        })
        slot.history.append({"timestamp": record["timestamp"], "direction": "defect",
                             "from_level": slot.level, "to_level": slot.level,
                             "audit_id": record["audit_id"]})
        self.save()
        return {"capability_slug": slot.capability_slug, "action": "defects_resolved",
                "closed": closed, "audit_ids": [record["audit_id"]]}

    # -- reporting ---------------------------------------------------------
    def chain_of(self, capability_slug: str, bundle: EvidenceBundle) -> Dict[str, Any]:
        return chain_report(self.rules, bundle)

    def summary(self) -> Dict[str, Any]:
        levels: Dict[str, int] = {}
        for slot in self.slots.values():
            levels[slot.level] = levels.get(slot.level, 0) + 1
        transitions = self.audit.transitions()
        return {
            "schema": STATE_SCHEMA,
            "rules_digest": self.rules.digest,
            "n_slots": len(self.slots),
            "by_level": levels,
            "n_upgrade_records": sum(1 for r in transitions if r["direction"] == "upgrade"),
            "n_rollback_records": sum(1 for r in transitions if r["direction"] == "rollback"),
            "n_audit_records": len(self.audit),
            "open_defects": sum(len(s.open_defects) for s in self.slots.values()),
            "audit_verify": self.audit.verify(),
        }

    # -- internals ---------------------------------------------------------
    def _new_batch(self, slot: Slot, bundle: Optional[EvidenceBundle]) -> str:
        payload = {"slug": slot.capability_slug, "level": slot.level,
                   "bundle": bundle.digest if bundle is not None else None,
                   "seq": len(self.audit)}
        return "b-" + digest(payload)[:12]

    def _apply_move(self, slot: Slot, target: str, direction: str, rule: UpgradeRule,
                    assessment: Assessment, bundle: EvidenceBundle, operator: str,
                    batch_id: str, reason: str, blocked_by: Optional[str]) -> Dict[str, Any]:
        before = slot.level
        record = self.audit.append({
            "capability_slug": slot.capability_slug, "direction": direction,
            "from_level": before, "to_level": target,
            "rule_id": rule.rule_id, "rule_digest": rule.digest,
            "rules_digest": self.rules.digest,
            "evidence_snapshot": {"before": slot.last_snapshot,
                                  "after": bundle.snapshot()},
            "completeness": {"before": slot.last_completeness,
                             "after": round(assessment.completeness, 6)},
            "missing_before": list(assessment.missing),
            "blocked_by": blocked_by,
            "reason": reason, "operator": operator, "batch_id": batch_id,
        })
        slot.level = target
        slot.last_rule_id = rule.rule_id
        slot.last_completeness = round(assessment.completeness, 6)
        slot.last_bundle_digest = bundle.digest
        slot.last_snapshot = bundle.snapshot()
        slot.updated_at = record["timestamp"]
        slot.history.append({"timestamp": record["timestamp"], "direction": direction,
                             "from_level": before, "to_level": target,
                             "rule_id": rule.rule_id, "audit_id": record["audit_id"]})
        self.save()
        return {"from_level": before, "to_level": target, "rule_id": rule.rule_id,
                "rule_digest": rule.digest, "audit_id": record["audit_id"],
                "completeness": round(assessment.completeness, 6)}

    @staticmethod
    def _decision(slot: Slot, before: str, action: str, batch_id: str,
                  steps: List[Dict[str, Any]], checks: List[Assessment],
                  operator: str) -> Dict[str, Any]:
        return {
            "capability_slug": slot.capability_slug, "action": action,
            "from_level": before, "to_level": slot.level,
            "batch_id": batch_id, "operator": operator,
            "steps": list(steps), "checks": [c.as_dict() for c in checks],
            "audit_ids": [s["audit_id"] for s in steps],
            "reason": "; ".join(s["rule_id"] for s in steps),
        }


__all__ = ["STATE_SCHEMA", "CapabilityStateMachine", "Slot"]