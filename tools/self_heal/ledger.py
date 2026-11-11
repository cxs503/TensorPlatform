"""Tamper-evident, idempotent audit ledger for self-healing operations.

Every applied decision becomes one entry.  ``hash = sha256(canonical(entry
without hash) + prev_hash)`` so deleting, reordering or editing an entry breaks
:meth:`SelfHealLedger.verify`.  ``digest()`` is the SHA-256 of the whole entry
list; it is the machine-checkable idempotency witness: re-running the same
healing window appends nothing, so ten runs produce one digest.

Idempotency rule: an entry's key is ``sha256(window_id, mission_id, action,
attempt)``.  Attempt numbers are derived only from *earlier* windows (see
:mod:`tools.self_heal.recovery`), therefore a same-window re-run recomputes the
same keys and is skipped instead of double-acting.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .model import canonical, digest

GENESIS_HASH = "0" * 64
SCHEMA = "self_heal_ledger/v1"

_HASHED_FIELDS = (
    "seq",
    "window_id",
    "mission_id",
    "detection_reason",
    "cause",
    "confidence",
    "action",
    "params",
    "attempt",
    "outcome",
    "detail",
    "detected_at",
    "created_at",
    "idempotency_key",
    "prev_hash",
)


@dataclass
class LedgerEntry:
    seq: int
    window_id: str
    mission_id: int
    detection_reason: str
    cause: str
    confidence: float
    action: str
    params: Dict[str, Any]
    attempt: int
    outcome: str
    detail: Dict[str, Any]
    detected_at: float
    created_at: float
    idempotency_key: str
    prev_hash: str
    hash: str = ""

    def body(self) -> Dict[str, Any]:
        return {name: getattr(self, name) for name in _HASHED_FIELDS}

    def compute_hash(self) -> str:
        return digest(self.body())

    def to_dict(self) -> Dict[str, Any]:
        payload = self.body()
        payload["hash"] = self.hash
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "LedgerEntry":
        return cls(
            seq=int(payload["seq"]),
            window_id=str(payload["window_id"]),
            mission_id=int(payload["mission_id"]),
            detection_reason=str(payload.get("detection_reason", "")),
            cause=str(payload.get("cause", "unknown")),
            confidence=float(payload.get("confidence", 0.0)),
            action=str(payload.get("action", "")),
            params=dict(payload.get("params") or {}),
            attempt=int(payload.get("attempt", 1)),
            outcome=str(payload.get("outcome", "")),
            detail=dict(payload.get("detail") or {}),
            detected_at=float(payload.get("detected_at", 0.0)),
            created_at=float(payload.get("created_at", 0.0)),
            idempotency_key=str(payload["idempotency_key"]),
            prev_hash=str(payload.get("prev_hash", GENESIS_HASH)),
            hash=str(payload.get("hash", "")),
        )


class SelfHealLedger:
    """In-memory chain with optional JSON file persistence (single writer)."""

    def __init__(self, entries: Optional[Iterable[LedgerEntry]] = None, path: Optional[str | Path] = None):
        self._entries: List[LedgerEntry] = [e for e in (entries or [])]
        self._index: Dict[str, LedgerEntry] = {e.idempotency_key: e for e in self._entries}
        self.path = Path(path) if path else None

    # ── basics ────────────────────────────────────────────────────────────
    @property
    def entries(self) -> Tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def find(self, key: str) -> Optional[LedgerEntry]:
        return self._index.get(key)

    def prior_attempts(self, mission_id: int, action: str, window_id: str) -> int:
        """Number of *earlier-window* entries with the same mission + action."""
        return sum(
            1
            for e in self._entries
            if e.mission_id == mission_id and e.action == action and e.window_id < str(window_id)
        )

    # ── append ────────────────────────────────────────────────────────────
    def append(
        self,
        *,
        window_id: str,
        mission_id: int,
        detection_reason: str,
        cause: str,
        confidence: float,
        action: str,
        params: Dict[str, Any],
        attempt: int,
        outcome: str,
        detail: Dict[str, Any],
        detected_at: float,
        created_at: float,
        idempotency_key: str,
    ) -> Tuple[LedgerEntry, bool]:
        existing = self._index.get(idempotency_key)
        if existing is not None:
            return existing, False
        entry = LedgerEntry(
            seq=(self._entries[-1].seq + 1) if self._entries else 1,
            window_id=str(window_id),
            mission_id=int(mission_id),
            detection_reason=detection_reason,
            cause=cause,
            confidence=float(confidence),
            action=action,
            params=dict(params),
            attempt=int(attempt),
            outcome=outcome,
            detail=dict(detail),
            detected_at=float(detected_at),
            created_at=float(created_at),
            idempotency_key=idempotency_key,
            prev_hash=self._entries[-1].hash if self._entries else GENESIS_HASH,
        )
        entry.hash = entry.compute_hash()
        self._entries.append(entry)
        self._index[entry.idempotency_key] = entry
        return entry, True

    # ── integrity ─────────────────────────────────────────────────────────
    def verify(self) -> Dict[str, Any]:
        prev = GENESIS_HASH
        for expected_seq, entry in enumerate(self._entries, start=1):
            if entry.seq != expected_seq:
                return {"ok": False, "reason": "seq_gap", "at": entry.seq, "expected": expected_seq}
            if entry.prev_hash != prev:
                return {"ok": False, "reason": "prev_hash_mismatch", "at": entry.seq}
            if entry.compute_hash() != entry.hash:
                return {"ok": False, "reason": "hash_mismatch", "at": entry.seq}
            prev = entry.hash
        return {"ok": True, "entries": len(self._entries), "head": prev}

    def digest(self) -> str:
        return digest([e.to_dict() for e in self._entries])

    # ── persistence ───────────────────────────────────────────────────────
    def to_json(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "entries": [e.to_dict() for e in self._entries],
            "digest": self.digest(),
            "head": self._entries[-1].hash if self._entries else GENESIS_HASH,
        }

    @classmethod
    def from_json(cls, payload: Dict[str, Any], path: Optional[str | Path] = None) -> "SelfHealLedger":
        return cls([LedgerEntry.from_dict(item) for item in payload.get("entries", [])], path=path)

    @classmethod
    def load(cls, path: str | Path) -> "SelfHealLedger":
        file_path = Path(path)
        if not file_path.is_file():
            return cls(path=file_path)
        payload = json.loads(file_path.read_text(encoding="utf-8") or "{}")
        return cls.from_json(payload, path=file_path)

    def save(self, path: Optional[str | Path] = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("no ledger path configured")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".ledger-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.to_json(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self.path = target
        return target