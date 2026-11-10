"""Append-only, sha256-chained audit log for capability level changes (p262w1-N07).

Design constraints (why it looks like this):

* **append-only** -- the file is only ever opened in ``"a"`` mode; there is no
  truncate/rewrite code path in this module.
* **tamper-evident** -- each entry stores ``prev_sha256`` (the hash of the
  previous entry) and its own ``sha256`` computed over the canonical JSON of the
  entry with the ``sha256`` field removed.  Mutating any byte, deleting an entry
  in the middle, reordering entries or resetting the sequence is detected by
  :meth:`AppendOnlyAuditLog.verify`.
* **no wall clock in the hash** -- timestamps are part of the hashed payload but
  callers may inject them, so tests can be deterministic.

The log carries capability ids, levels and digests only -- no domain semantics.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .evidence_chain import canonical_json, sha256_json

GENESIS_SHA256 = "0" * 64
CHAIN_VERSION = "sha256-chain-v1"

_ENTRY_FIELDS = (
    "seq",
    "ts",
    "chain_version",
    "action",
    "capability_id",
    "from_level",
    "to_level",
    "reason_code",
    "evidence_digest",
    "payload",
    "prev_sha256",
)


class AuditLogCorrupted(RuntimeError):
    """Raised when an append is attempted on a log that fails verification."""


@dataclass
class AuditEntry:
    seq: int
    ts: str
    action: str
    capability_id: str
    from_level: str
    to_level: str
    reason_code: str
    evidence_digest: str
    payload: Dict[str, Any]
    prev_sha256: str
    sha256: str
    chain_version: str = CHAIN_VERSION

    def as_dict(self) -> Dict[str, Any]:
        d = {name: getattr(self, name) for name in _ENTRY_FIELDS}
        d["sha256"] = self.sha256
        return d

    def recompute_sha256(self) -> str:
        body = {name: getattr(self, name) for name in _ENTRY_FIELDS}
        return sha256_json(body)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class AppendOnlyAuditLog:
    """JSONL audit log where every line is chained to the previous one."""

    def __init__(self, path: "str | Path", *, create: bool = True):
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
        self._entries: List[AuditEntry] = []
        self._integrity: Dict[str, Any] = {"ok": True, "errors": [], "entries": 0,
                                           "head_sha256": GENESIS_SHA256}
        self._load()

    # -- reading -------------------------------------------------------------
    def _load(self) -> None:
        entries: List[AuditEntry] = []
        errors: List[str] = []
        if self.path.exists():
            for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"line {lineno}: not JSON ({exc})")
                    continue
                missing = [f for f in _ENTRY_FIELDS + ("sha256",) if f not in raw]
                if missing:
                    errors.append(f"line {lineno}: missing fields {missing}")
                    continue
                entries.append(AuditEntry(**{k: raw[k] for k in _ENTRY_FIELDS + ("sha256",)}))
        self._entries = entries
        self._integrity = self._verify_entries(entries, errors)

    @staticmethod
    def _verify_entries(entries: List[AuditEntry], errors: Optional[List[str]] = None) -> Dict[str, Any]:
        errors = list(errors or [])
        prev = GENESIS_SHA256
        for idx, entry in enumerate(entries):
            expected_seq = idx + 1
            if entry.seq != expected_seq:
                errors.append(f"entry {idx}: seq {entry.seq} != expected {expected_seq} "
                              f"(deletion or reordering)")
            if entry.prev_sha256 != prev:
                errors.append(f"entry {idx} (seq {entry.seq}): prev_sha256 mismatch")
            recomputed = entry.recompute_sha256()
            if recomputed != entry.sha256:
                errors.append(f"entry {idx} (seq {entry.seq}): sha256 mismatch "
                              f"(stored {entry.sha256[:12]}..., recomputed {recomputed[:12]}...)")
            prev = entry.sha256
        head = entries[-1].sha256 if entries else GENESIS_SHA256
        return {"ok": not errors, "errors": errors, "entries": len(entries),
                "head_sha256": head, "chain_version": CHAIN_VERSION}

    def verify(self) -> Dict[str, Any]:
        """Re-read the file from disk and verify the whole chain."""
        self._load()
        return dict(self._integrity)

    # -- writing -------------------------------------------------------------
    @property
    def integrity(self) -> Dict[str, Any]:
        return dict(self._integrity)

    @property
    def head_sha256(self) -> str:
        return self._entries[-1].sha256 if self._entries else GENESIS_SHA256

    @property
    def entries(self) -> List[AuditEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[AuditEntry]:
        return iter(self._entries)

    def append(
        self,
        *,
        action: str,
        capability_id: str,
        from_level: str,
        to_level: str,
        reason_code: str = "",
        evidence_digest: str = "",
        payload: Optional[Dict[str, Any]] = None,
        ts: Optional[str] = None,
    ) -> AuditEntry:
        if not self._integrity["ok"]:
            raise AuditLogCorrupted(
                "refusing to append to a corrupted audit log: "
                + "; ".join(self._integrity["errors"][:3]))
        entry = AuditEntry(
            seq=len(self._entries) + 1,
            ts=ts or _now_iso(),
            action=action,
            capability_id=capability_id,
            from_level=from_level,
            to_level=to_level,
            reason_code=reason_code,
            evidence_digest=evidence_digest,
            payload=payload or {},
            prev_sha256=self.head_sha256,
            sha256="",
        )
        entry.sha256 = entry.recompute_sha256()
        line = canonical_json(entry.as_dict())
        # "a" + single write + fsync: the only write path in this module.
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._entries.append(entry)
        self._integrity = self._verify_entries(self._entries)
        return entry

    def tail(self, n: int = 5) -> List[Dict[str, Any]]:
        return [e.as_dict() for e in self._entries[-n:]]

    def digest_sidecar(self, out_path: "str | Path") -> Dict[str, Any]:
        """Write ``{entries, head_sha256}`` next to the log and return it."""
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"log": str(self.path.name), "entries": len(self._entries),
                   "head_sha256": self.head_sha256,
                   "chain_version": CHAIN_VERSION,
                   "ok": self._integrity["ok"]}
        out.write_text(canonical_json(payload) + "\n", encoding="utf-8")
        return payload