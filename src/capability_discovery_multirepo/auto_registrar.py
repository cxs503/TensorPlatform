"""Auto-registrar: registers discovered capabilities to the ledger.

Compares extracted features against the existing capability ledger
and performs add/update operations with full audit trail. Supports
rollback via retract/restore operations.

Key invariants:
- Never overwrites a retracted entry (human decision is final)
- Every mutation is recorded in the hash chain
- Level downgrades are always applied (conservative)
- Level upgrades require evidence fingerprint change
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .feature_extractor import CapabilityFeature


def now_iso() -> str:
    """Current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(body: Any) -> str:
    """SHA-256 of JSON-serialized body."""
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class LedgerError(Exception):
    """Error in ledger operations."""
    pass


@dataclass
class RegistrationResult:
    """Result of a single registration action.

    Attributes
    ----------
    action : str
        "added" | "updated" | "unchanged" | "held_retracted".
    capability_id : str
        The capability that was acted upon.
    level : str
        Current evidence level after action.
    from_level : str
        Previous level (empty for new additions).
    level_changed : bool
        Whether the evidence level changed.
    revision : int
        Entry revision number.
    reasons : list[str]
        Human-readable reasons for the action.
    """
    action: str
    capability_id: str
    level: str = ""
    from_level: str = ""
    level_changed: bool = False
    revision: int = 1
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "capability_id": self.capability_id,
            "level": self.level,
            "from_level": self.from_level,
            "level_changed": self.level_changed,
            "revision": self.revision,
            "reasons": list(self.reasons),
        }


class CapabilityLedger:
    """In-memory capability ledger with hash chain audit trail.

    The ledger stores capability entries and maintains a hash chain
    of all mutations for tamper detection.

    Parameters
    ----------
    path : Path | str | None
        Path to persist the ledger JSON. None for in-memory only.
    """

    SCHEMA = "capability_ledger.multirepo.v1"

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path else None
        self.doc: Dict[str, Any] = {
            "schema": self.SCHEMA,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "policy_digest": "",
        }
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.chain: List[Dict[str, Any]] = []

        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        """Load ledger from disk."""
        try:
            with open(self.path, encoding="utf-8") as f:
                self.doc = json.load(f)
            self.entries = self.doc.get("entries", {})
            self.chain = self.doc.get("chain", [])
        except (json.JSONDecodeError, OSError) as exc:
            raise LedgerError(f"Failed to load ledger: {exc}")

    def save(self) -> None:
        """Persist ledger to disk."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.doc["entries"] = self.entries
        self.doc["chain"] = self.chain
        self.doc["updated_at"] = now_iso()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.doc, f, indent=2, ensure_ascii=False)

    def head_hash(self) -> str:
        """Return the hash of the last chain entry (or empty)."""
        if not self.chain:
            return ""
        return self.chain[-1].get("hash", "")

    def active_ids(self) -> List[str]:
        """Return IDs of all active (non-retracted) entries."""
        return [
            cid for cid, entry in self.entries.items()
            if entry.get("state", "active") != "retracted"
        ]

    def get_entry(self, capability_id: str) -> Optional[Dict[str, Any]]:
        """Get a ledger entry by capability ID."""
        return self.entries.get(capability_id)

    def _entry_hash(self, entry: Dict[str, Any]) -> str:
        """Compute hash of an entry (excluding its own hash field)."""
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        return digest(body)

    def _append_chain(
        self, action: str, capability_id: str, entry_hash: str, reason: str = ""
    ) -> None:
        """Append a record to the hash chain."""
        prev = self.head_hash()
        seq = len(self.chain) + 1
        at = now_iso()
        body = {
            "seq": seq,
            "at": at,
            "action": action,
            "capability_id": capability_id,
            "entry_hash": entry_hash,
            "prev_hash": prev,
            "reason": reason,
        }
        body["hash"] = digest(body)
        self.chain.append(body)

    def verify_chain(self) -> Tuple[bool, str]:
        """Verify the integrity of the hash chain.

        Returns
        -------
        ok : bool
            True if chain is valid.
        message : str
            Description of result or error.
        """
        prev = ""
        for idx, rec in enumerate(self.chain, start=1):
            if rec.get("seq") != idx:
                return False, f"Chain sequence broken at seq={rec.get('seq')} (expected {idx})"
            if rec.get("prev_hash") != prev:
                return False, f"prev_hash mismatch at seq={idx}"
            body = {k: v for k, v in rec.items() if k != "hash"}
            if digest(body) != rec.get("hash"):
                return False, f"Record tampered at seq={idx}"
            prev = rec["hash"]

        # Verify entry hashes match chain
        last_by_cap: Dict[str, Dict[str, Any]] = {}
        for rec in self.chain:
            last_by_cap[rec["capability_id"]] = rec
        for cid, rec in last_by_cap.items():
            entry = self.entries.get(cid)
            if entry is None:
                return False, f"Chain references non-existent entry: {cid}"
            if self._entry_hash(entry) != entry.get("entry_hash"):
                return False, f"Entry content/hash mismatch: {cid}"
        return True, f"chain ok ({len(self.chain)} records)"

    def add_entry(
        self,
        feature: CapabilityFeature,
        reason: str = "",
    ) -> RegistrationResult:
        """Add a new capability entry to the ledger.

        Parameters
        ----------
        feature : CapabilityFeature
            The capability feature to register.
        reason : str
            Reason for registration.

        Returns
        -------
        RegistrationResult
            Result of the add operation.
        """
        cid = feature.capability_id
        if cid in self.entries:
            raise LedgerError(f"Entry already exists: {cid}")

        at = now_iso()
        entry = {
            "capability_id": cid,
            "label": feature.label,
            "level": feature.evidence_level,
            "state": "active",
            "revision": 1,
            "created_at": at,
            "updated_at": at,
            "repos": list(feature.repos),
            "params": dict(feature.params),
            "anchors": list(feature.anchors),
            "tags": list(feature.tags),
            "evidence": {
                "sources": list(feature.sources),
                "tests": list(feature.tests),
                "outcomes": dict(feature.outcomes),
                "discovery_types": list(feature.discovery_types),
                "fingerprint": feature.evidence_fingerprint(),
            },
            "confidence": feature.confidence,
            "level_history": [
                {"at": at, "from": "", "to": feature.evidence_level, "reason": reason or "initial registration"}
            ],
            "retraction": None,
        }
        entry["entry_hash"] = self._entry_hash(entry)
        self.entries[cid] = entry
        self._append_chain("added", cid, entry["entry_hash"], reason or "initial registration")
        self.doc["updated_at"] = at

        return RegistrationResult(
            action="added",
            capability_id=cid,
            level=feature.evidence_level,
            revision=1,
            reasons=[reason or "initial registration"],
        )

    def update_entry(
        self,
        feature: CapabilityFeature,
        reason: str = "",
    ) -> RegistrationResult:
        """Update an existing capability entry.

        Parameters
        ----------
        feature : CapabilityFeature
            Updated capability feature.
        reason : str
            Reason for update.

        Returns
        -------
        RegistrationResult
            Result of the update operation.
        """
        cid = feature.capability_id
        existing = self.entries.get(cid)
        if existing is None:
            raise LedgerError(f"Entry not found: {cid}")

        if existing.get("state") == "retracted":
            return RegistrationResult(
                action="held_retracted",
                capability_id=cid,
                level=existing["level"],
                revision=existing.get("revision", 1),
                reasons=["Entry is retracted; auto-discovery does not override"],
            )

        at = now_iso()
        old_level = existing["level"]
        new_level = feature.evidence_level
        old_fingerprint = existing.get("evidence", {}).get("fingerprint", "")
        new_fingerprint = feature.evidence_fingerprint()

        level_changed = old_level != new_level
        fingerprint_changed = old_fingerprint != new_fingerprint

        if not level_changed and not fingerprint_changed:
            return RegistrationResult(
                action="unchanged",
                capability_id=cid,
                level=old_level,
                revision=existing.get("revision", 1),
                reasons=["No change in level or evidence"],
            )

        # Apply update
        existing["level"] = new_level
        existing["updated_at"] = at
        existing["revision"] = int(existing.get("revision", 1)) + 1
        existing["repos"] = list(feature.repos)
        existing["params"] = dict(feature.params)
        existing["anchors"] = list(feature.anchors)
        existing["tags"] = list(feature.tags)
        existing["evidence"] = {
            "sources": list(feature.sources),
            "tests": list(feature.tests),
            "outcomes": dict(feature.outcomes),
            "discovery_types": list(feature.discovery_types),
            "fingerprint": new_fingerprint,
        }
        existing["confidence"] = feature.confidence
        existing.setdefault("level_history", []).append({
            "at": at,
            "from": old_level,
            "to": new_level,
            "reason": reason or "evidence update",
        })
        existing["entry_hash"] = self._entry_hash(existing)
        self._append_chain("updated", cid, existing["entry_hash"], reason or "evidence update")
        self.doc["updated_at"] = at

        return RegistrationResult(
            action="updated",
            capability_id=cid,
            level=new_level,
            from_level=old_level,
            level_changed=level_changed,
            revision=existing["revision"],
            reasons=[reason or "evidence update"],
        )

    def retract(
        self, capability_id: str, reason: str, actor: str = "human"
    ) -> Dict[str, Any]:
        """Retract (rollback) a capability entry.

        Parameters
        ----------
        capability_id : str
            The capability to retract.
        reason : str
            Required reason for retraction.
        actor : str
            Who is performing the retraction.

        Returns
        -------
        dict
            The retracted entry.
        """
        if not reason or not reason.strip():
            raise LedgerError("Retraction requires a reason")
        entry = self.entries.get(capability_id)
        if entry is None:
            raise LedgerError(f"Unknown capability: {capability_id}")
        if entry.get("state") == "retracted":
            raise LedgerError(f"Already retracted: {capability_id}")

        at = now_iso()
        entry["state"] = "retracted"
        entry["updated_at"] = at
        entry["revision"] = int(entry.get("revision", 1)) + 1
        entry["retraction"] = {
            "at": at,
            "by": actor,
            "reason": reason.strip(),
            "previous_level": entry["level"],
        }
        entry["entry_hash"] = self._entry_hash(entry)
        self._append_chain("retracted", capability_id, entry["entry_hash"], reason.strip())
        self.doc["updated_at"] = at
        return entry

    def restore(
        self, capability_id: str, reason: str, actor: str = "human"
    ) -> Dict[str, Any]:
        """Restore a retracted capability entry.

        Parameters
        ----------
        capability_id : str
            The capability to restore.
        reason : str
            Required reason for restoration.
        actor : str
            Who is performing the restoration.

        Returns
        -------
        dict
            The restored entry.
        """
        if not reason or not reason.strip():
            raise LedgerError("Restoration requires a reason")
        entry = self.entries.get(capability_id)
        if entry is None:
            raise LedgerError(f"Unknown capability: {capability_id}")
        if entry.get("state") != "retracted":
            raise LedgerError(f"Not retracted: {capability_id}")

        at = now_iso()
        entry["state"] = "active"
        entry["updated_at"] = at
        entry["revision"] = int(entry.get("revision", 1)) + 1
        entry["retraction"] = None
        entry.setdefault("level_history", []).append({
            "at": at, "from": entry["level"], "to": entry["level"],
            "reason": f"restored: {reason.strip()}"
        })
        entry["entry_hash"] = self._entry_hash(entry)
        self._append_chain("restored", capability_id, entry["entry_hash"], reason.strip())
        self.doc["updated_at"] = at
        return entry

    def missing(self, discovered_ids: List[str]) -> List[Dict[str, Any]]:
        """Find ledger entries not found in current discovery.

        Parameters
        ----------
        discovered_ids : list[str]
            Capability IDs found in current scan.

        Returns
        -------
        list[dict]
            Entries present in ledger but not in discovery.
        """
        seen = set(discovered_ids)
        out = []
        for cid in self.active_ids():
            if cid not in seen:
                entry = self.entries[cid]
                out.append({
                    "capability_id": cid,
                    "level": entry["level"],
                    "revision": entry.get("revision", 1),
                    "last_updated": entry.get("updated_at"),
                    "note": "In ledger but not discovered in current scan",
                })
        return out


class AutoRegistrar:
    """Orchestrates auto-registration of discovered capabilities.

    Compares extracted features against the ledger and performs
    add/update operations.

    Parameters
    ----------
    ledger : CapabilityLedger
        The capability ledger to register into.
    min_confidence : float
        Minimum confidence threshold for auto-registration.
    """

    def __init__(self, ledger: CapabilityLedger, min_confidence: float = 0.0):
        self.ledger = ledger
        self.min_confidence = min_confidence

    def register_all(
        self,
        features: List[CapabilityFeature],
        reason_prefix: str = "auto-discovery",
    ) -> Tuple[List[RegistrationResult], List[Dict[str, Any]]]:
        """Register all features to the ledger.

        Parameters
        ----------
        features : list[CapabilityFeature]
            Features to register.
        reason_prefix : str
            Prefix for registration reasons.

        Returns
        -------
        results : list[RegistrationResult]
            Results of all registration actions.
        missing : list[dict]
            Ledger entries not found in current discovery.
        """
        results: List[RegistrationResult] = []
        run_id = now_iso()

        for feature in features:
            if feature.confidence < self.min_confidence:
                results.append(RegistrationResult(
                    action="unchanged",
                    capability_id=feature.capability_id,
                    level=feature.evidence_level,
                    reasons=[f"Below confidence threshold: {feature.confidence:.2f} < {self.min_confidence:.2f}"],
                ))
                continue

            reason = f"{reason_prefix} run={run_id}"
            existing = self.ledger.get_entry(feature.capability_id)

            if existing is None:
                result = self.ledger.add_entry(feature, reason=reason)
            else:
                result = self.ledger.update_entry(feature, reason=reason)

            results.append(result)

        discovered_ids = [f.capability_id for f in features]
        missing = self.ledger.missing(discovered_ids)

        return results, missing

    def rollback(
        self, capability_id: str, reason: str, actor: str = "human"
    ) -> Dict[str, Any]:
        """Rollback (retract) a capability registration.

        Parameters
        ----------
        capability_id : str
            The capability to rollback.
        reason : str
            Required reason for rollback.
        actor : str
            Who is performing the rollback.

        Returns
        -------
        dict
            The retracted entry.
        """
        return self.ledger.retract(capability_id, reason, actor)

    def restore_rollback(
        self, capability_id: str, reason: str, actor: str = "human"
    ) -> Dict[str, Any]:
        """Restore a rolled-back capability.

        Parameters
        ----------
        capability_id : str
            The capability to restore.
        reason : str
            Required reason for restoration.
        actor : str
            Who is performing the restoration.

        Returns
        -------
        dict
            The restored entry.
        """
        return self.ledger.restore(capability_id, reason, actor)
