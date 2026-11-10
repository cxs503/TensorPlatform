"""Append-only, hash-chained audit log for validation runs.

Each line of the log is a self-describing JSON object::

    {"seq": 0, "ts": "...", "run_id": "...", "event": "run_start",
     "payload_sha256": "...", "prev": "00..0", "hash": "ab.."}

``hash = sha256(canonical_json({seq, ts, run_id, event, payload_sha256, prev}))``
so removing, reordering or editing any entry invalidates every later link and
``verify_audit_chain`` reports the first broken sequence number.  The chain head
hash is written into the validation report, which makes the report and the log
mutually checkable without a database.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from .profile import canonical_sha256

GENESIS = "0" * 64


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


class AuditLog:
    """Hash-chained JSONL writer (append-only, crash tolerant)."""

    def __init__(self, path: str, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._seq = 0
        self._head = GENESIS
        existing = self._read_entries()
        if existing:
            self._seq = int(existing[-1]["seq"]) + 1
            self._head = str(existing[-1]["hash"])

    def _read_entries(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.path):
            return []
        entries: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    @property
    def head_hash(self) -> str:
        return self._head

    @property
    def n_entries(self) -> int:
        return self._seq

    def append(self, event: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        body = {
            "seq": self._seq,
            "ts": _now(),
            "run_id": self.run_id,
            "event": str(event),
            "payload_sha256": canonical_sha256(payload),
            "prev": self._head,
        }
        body["hash"] = canonical_sha256(body)
        line = json.dumps(body, sort_keys=True, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seq += 1
        self._head = str(body["hash"])
        record = dict(body)
        record["payload"] = payload
        return record

    def summary(self) -> Dict[str, Any]:
        return {"path": os.path.abspath(self.path), "run_id": self.run_id,
                "entries_appended": self._seq, "head_hash": self._head}


def verify_audit_chain(path: str) -> Dict[str, Any]:
    """Recompute the chain; report the first inconsistency, if any."""
    if not os.path.isfile(path):
        return {"ok": False, "reason": "audit log not found", "path": path,
                "n_entries": 0, "head_hash": None, "broken_at": None}
    prev = GENESIS
    n = 0
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                return {"ok": False, "reason": f"line {lineno} is not JSON: {exc}",
                        "path": path, "n_entries": n, "head_hash": prev, "broken_at": n}
            if entry.get("seq") != n:
                return {"ok": False,
                        "reason": f"sequence gap at line {lineno}: expected {n}, "
                                  f"found {entry.get('seq')}",
                        "path": path, "n_entries": n, "head_hash": prev, "broken_at": n}
            if entry.get("prev") != prev:
                return {"ok": False, "reason": f"prev-hash mismatch at seq {n}",
                        "path": path, "n_entries": n, "head_hash": prev, "broken_at": n}
            body = {k: entry.get(k) for k in ("seq", "ts", "run_id", "event",
                                              "payload_sha256", "prev")}
            if canonical_sha256(body) != entry.get("hash"):
                return {"ok": False, "reason": f"entry hash mismatch at seq {n}",
                        "path": path, "n_entries": n, "head_hash": prev, "broken_at": n}
            prev = str(entry["hash"])
            n += 1
    return {"ok": True, "reason": None, "path": path, "n_entries": n,
            "head_hash": prev, "broken_at": None}