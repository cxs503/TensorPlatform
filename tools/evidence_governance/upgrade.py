"""WITHHELD -> REAL upgrade engine with a structural anti-fabrication gate.

The upgrade half of the node is *not* "trust a status field". A pass claim is
accepted only when the outcome record itself is auditable:

* at least ``min_independent_refs`` *distinct* evidence sources (a copy that
  renames its method is still a copy — same source id counts once),
* every declared acceptance check scored pass, and the check list is non-empty,
* the evidence references carry content hashes when the config demands them.

Outcomes that fail this gate leave the item at WITHHELD with an explicit
``claim rejected`` reason, so a fabricated "pass" cannot move the KPI. Closure
rate is then ``n_upgraded / n_withheld_total`` and the invariant
``upgraded + failed + remaining == total`` is validated before the ledger is
serialised.

Zero domain semantics: the engine never knows what a capability *is*; it only
compares levels, counts independent sources and reads configuration.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import GovernanceLedger, LibraryItem, UpgradeRecord, digest


class UpgradeEngine:
    def __init__(self, config: Dict[str, Any], root: Path | str | None = None):
        self.config = config
        self.root = Path(root or Path.cwd())
        ver = dict(config.get("verification", {}))
        self.min_independent_refs = int(ver.get("min_independent_refs", 2))
        self.require_hashes = bool(ver.get("require_content_hashes", True))
        self.accept_level = str(ver.get("accept_level", "REAL"))
        self.manifest_path = ver.get("manifest_path")

    # ------------------------------------------------------------------ input
    def load_outcomes(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        meta: Dict[str, Any] = {"source": "none", "n_outcomes": 0}
        if not self.manifest_path:
            return [], meta
        path = self.root / self.manifest_path
        if not path.exists():
            meta["source"] = f"missing:{self.manifest_path}"
            return [], meta
        payload = json.loads(path.read_text(encoding="utf-8"))
        outcomes = list(payload.get("outcomes", []))
        outcomes.sort(key=lambda o: str(o.get("capability_id", "")))
        meta.update({
            "source": self.manifest_path,
            "n_outcomes": len(outcomes),
            "manifest_digest": digest(payload),
        })
        return outcomes, meta

    # ------------------------------------------------------------------- gate
    def _gate(self, outcome: Dict[str, Any]) -> Tuple[bool, str]:
        refs = [r for r in outcome.get("evidence_refs", []) if isinstance(r, dict)]
        sources = {str(r.get("source_id", "")) for r in refs if r.get("source_id")}
        if len(sources) < self.min_independent_refs:
            return False, (
                f"only {len(sources)} independent evidence source(s) "
                f"(< {self.min_independent_refs})"
            )
        checks = outcome.get("checks") or []
        if not checks:
            return False, "no acceptance checks declared (unfalsifiable pass)"
        failed = [c for c in checks if not bool(c.get("passed"))]
        if failed:
            names = ",".join(str(c.get("name", "?")) for c in failed)
            return False, f"acceptance checks failed: {names}"
        if self.require_hashes:
            missing = [
                str(r.get("source_id", "?"))
                for r in refs
                if not r.get("sha256")
            ]
            if missing:
                return False, f"evidence without content hash: {','.join(sorted(missing))}"
        return True, "all acceptance checks pass with independent, hashed evidence"

    # ---------------------------------------------------------------- updates
    def run(self, items: List[LibraryItem]) -> GovernanceLedger:
        outcomes, meta = self.load_outcomes()
        by_id = {str(o.get("capability_id", "")): o for o in outcomes}
        item_ids = {i.capability_id for i in items}
        orphans = sorted(set(by_id) - item_ids)

        updates: List[UpgradeRecord] = []
        n_upgraded = n_failed = n_remaining = 0
        for item in items:
            outcome = by_id.get(item.capability_id)
            if outcome is None:
                n_remaining += 1
                updates.append(UpgradeRecord(
                    capability_id=item.capability_id,
                    before_level=item.reconciled_level,
                    after_level=item.reconciled_level,
                    outcome="no_outcome",
                    evidence_ref=None,
                    reason="no verification outcome recorded; debt stays open",
                ))
                continue
            status = str(outcome.get("status", "unknown"))
            ref_ids = [
                str(r.get("source_id", ""))
                for r in outcome.get("evidence_refs", [])
                if isinstance(r, dict)
            ]
            evidence_ref = ",".join(sorted(set(ref_ids))) or None
            if status in ("fail", "error", "timeout", "skipped"):
                n_failed += 1
                updates.append(UpgradeRecord(
                    capability_id=item.capability_id,
                    before_level=item.reconciled_level,
                    after_level=item.reconciled_level,
                    outcome=status,
                    evidence_ref=evidence_ref,
                    reason=str(outcome.get("reason", f"verification {status}")),
                ))
                continue
            if status != "pass":
                n_remaining += 1
                updates.append(UpgradeRecord(
                    capability_id=item.capability_id,
                    before_level=item.reconciled_level,
                    after_level=item.reconciled_level,
                    outcome="unknown",
                    evidence_ref=evidence_ref,
                    reason=f"unrecognised outcome status {status!r}; treated as open",
                ))
                continue
            ok, why = self._gate(outcome)
            if not ok:
                n_remaining += 1
                updates.append(UpgradeRecord(
                    capability_id=item.capability_id,
                    before_level=item.reconciled_level,
                    after_level=item.reconciled_level,
                    outcome="claim_rejected",
                    evidence_ref=evidence_ref,
                    reason=f"pass claim rejected: {why}",
                ))
                continue
            n_upgraded += 1
            updates.append(UpgradeRecord(
                capability_id=item.capability_id,
                before_level=item.reconciled_level,
                after_level=self.accept_level,
                outcome="pass",
                evidence_ref=evidence_ref,
                reason=why,
            ))

        updates.sort(key=lambda u: (u.outcome, u.capability_id))
        run_digest = digest({
            "items": [i.digest() for i in items],
            "outcomes": outcomes,
            "accept_level": self.accept_level,
        })
        ledger = GovernanceLedger(
            n_withheld_total=len(items),
            n_upgraded=n_upgraded,
            n_failed=n_failed,
            n_remaining=n_remaining,
            closure_rate=(n_upgraded / len(items)) if items else 0.0,
            run_digest=run_digest,
            updates=updates,
            orphan_outcomes=orphans,
        )
        ledger.validate()
        return ledger

    @staticmethod
    def ledger_payload(ledger: GovernanceLedger, meta: Dict[str, Any]) -> Dict[str, Any]:
        payload = ledger.as_dict()
        payload["outcome_source"] = meta
        payload["gate"] = {
            "min_independent_refs": None,  # filled by caller through attach_gate
        }
        return payload

    def attach_gate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["gate"] = {
            "min_independent_refs": self.min_independent_refs,
            "require_content_hashes": self.require_hashes,
            "accept_level": self.accept_level,
        }
        return payload