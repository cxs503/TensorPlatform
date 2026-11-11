"""Append-only submission ledger + payload store for the report pipeline.

Why a ledger
------------
A write-read assertion only helps if it is *recorded*: the probe can then ask
"the write side claims these rows exist — which of them can the read side
still see?" without trusting the report table itself (which is exactly what
loses the row).  The ledger is therefore the write-side ground truth, kept
outside the report table.

Design
------
* one JSON line per event (``submitted`` / ``verified`` / ``lost`` /
  ``healed`` / ``heal_failed`` / ``unhealable``); state is derived by folding
  events, so nothing is ever rewritten in place;
* every line carries ``prev_hash`` and ``hash`` (SHA-256 over the canonical
  payload + previous hash) — deleting, reordering or editing a line breaks
  :meth:`SubmissionLedger.verify`;
* the payload itself goes to a :class:`PayloadStore` (one JSON file per
  submission, content-addressed by SHA-256), because self-healing can only
  re-send what was actually written.  Digests are re-checked on load, so a
  tampered payload cannot be replayed.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

GENESIS_HASH = "0" * 64

#: Event kinds.  ``LOST_KINDS`` are the terminal bad states the probe reports.
KIND_SUBMITTED = "submitted"
KIND_VERIFIED = "verified"
KIND_LOST = "lost"
KIND_HEALED = "healed"
KIND_HEAL_FAILED = "heal_failed"
KIND_UNHEALABLE = "unhealable"
KINDS = (KIND_SUBMITTED, KIND_VERIFIED, KIND_LOST, KIND_HEALED, KIND_HEAL_FAILED,
         KIND_UNHEALABLE)

#: States that still count as "the write side believes this row exists".
LIVE_KINDS = frozenset({KIND_SUBMITTED, KIND_VERIFIED, KIND_HEALED, KIND_HEAL_FAILED})
#: States that need healing.
BROKEN_KINDS = frozenset({KIND_LOST, KIND_HEAL_FAILED})
#: States that must be reported but cannot be healed automatically.
DEAD_KINDS = frozenset({KIND_UNHEALABLE})


def payload_digest(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_hash(seq: int, ts: float, submission_id: str, kind: str,
                 report_id: int | None, mission_id: int | None,
                 digest: str | None, prev_hash: str, detail: Mapping[str, Any]) -> str:
    body = {
        "seq": int(seq),
        "ts": round(float(ts), 6),
        "submission_id": submission_id,
        "kind": kind,
        "report_id": report_id,
        "mission_id": mission_id,
        "payload_digest": digest,
        "detail": dict(detail),
        "prev_hash": prev_hash,
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    """One append-only ledger line."""

    seq: int
    ts: float
    submission_id: str
    kind: str
    report_id: int | None = None
    mission_id: int | None = None
    payload_digest: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "ts": self.ts, "submission_id": self.submission_id,
            "kind": self.kind, "report_id": self.report_id,
            "mission_id": self.mission_id, "payload_digest": self.payload_digest,
            "detail": self.detail, "prev_hash": self.prev_hash, "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LedgerEntry":
        return cls(
            seq=int(raw["seq"]), ts=float(raw["ts"]),
            submission_id=str(raw["submission_id"]), kind=str(raw["kind"]),
            report_id=None if raw.get("report_id") is None else int(raw["report_id"]),
            mission_id=None if raw.get("mission_id") is None else int(raw["mission_id"]),
            payload_digest=raw.get("payload_digest"),
            detail=dict(raw.get("detail") or {}),
            prev_hash=str(raw.get("prev_hash", GENESIS_HASH)),
            hash=str(raw.get("hash", "")),
        )

    def recomputed_hash(self) -> str:
        return compute_hash(self.seq, self.ts, self.submission_id, self.kind,
                            self.report_id, self.mission_id, self.payload_digest,
                            self.prev_hash, self.detail)


@dataclass(frozen=True)
class SubmissionState:
    """Folded view of one submission (latest event wins)."""

    submission_id: str
    report_id: int | None
    mission_id: int | None
    payload_digest: str | None
    status: str
    submitted_ts: float
    attempts: int = 0
    last_detail: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_healing(self) -> bool:
        return self.status in BROKEN_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id, "report_id": self.report_id,
            "mission_id": self.mission_id, "payload_digest": self.payload_digest,
            "status": self.status, "submitted_ts": self.submitted_ts,
            "attempts": self.attempts, "needs_healing": self.needs_healing,
        }


class PayloadStore:
    """Content-addressed payload snapshots: self-heal needs the original bytes."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else None

    def _path(self, submission_id: str) -> Path:
        assert self.root is not None
        return self.root / f"{submission_id}.json"

    def save(self, submission_id: str, payload: Mapping[str, Any]) -> str:
        digest = payload_digest(payload)
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self._path(submission_id).with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"payload": payload, "payload_digest": digest},
                                      ensure_ascii=False, default=str),
                           encoding="utf-8")
            tmp.replace(self._path(submission_id))
        return digest

    def load(self, submission_id: str) -> dict[str, Any] | None:
        """Return the stored payload, or ``None`` when unavailable/tampered."""
        if self.root is None:
            return None
        path = self._path(submission_id)
        if not path.is_file():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        payload = doc.get("payload")
        if not isinstance(payload, dict):
            return None
        if payload_digest(payload) != doc.get("payload_digest"):
            return None  # tampered snapshot -> never replay it
        return payload


class SubmissionLedger:
    """Append-only, hash-chained record of every report write attempt."""

    def __init__(self, path: Path | str | None = None,
                 payload_store: PayloadStore | None = None,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path) if path is not None else None
        self.payload_store = payload_store or PayloadStore(
            None if self.path is None else self.path.parent / "payloads")
        self._clock = clock
        self._entries: list[LedgerEntry] = []
        self._mem_payloads: dict[str, dict[str, Any]] = {}
        if self.path is not None and self.path.is_file():
            self._load()

    # ── persistence ────────────────────────────────────────────────────
    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                self._entries.append(LedgerEntry.from_dict(json.loads(line)))

    @property
    def _head_path(self) -> Path | None:
        return None if self.path is None else self.path.parent / "head.json"

    def _write_head(self) -> None:
        """Anchor the chain head in a sidecar so a truncated tail is visible.

        A hash chain alone detects edits in the middle (a link no longer
        matches) but *not* deletion of the last lines: a prefix of a valid
        chain is a valid chain.  The sidecar records ``(entries, head_hash)``
        so tail truncation — the cheapest way to hide a ``lost``/``heal_failed``
        event — becomes a ``verify()`` failure.  An attacker able to rewrite
        both files consistently is out of scope (see module docstring).
        """
        head_path = self._head_path
        if head_path is None:
            return
        head_path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"entries": len(self._entries),
               "head_hash": self._entries[-1].hash if self._entries else GENESIS_HASH,
               "updated_ts": float(self._clock())}
        tmp = head_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(head_path)

    def _read_head(self) -> dict[str, Any] | None:
        head_path = self._head_path
        if head_path is None or not head_path.is_file():
            return None
        try:
            return json.loads(head_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _append(self, kind: str, submission_id: str, *, report_id: int | None,
                mission_id: int | None, digest: str | None,
                detail: Mapping[str, Any] | None = None) -> LedgerEntry:
        prev = self._entries[-1].hash if self._entries else GENESIS_HASH
        seq = len(self._entries)
        ts = float(self._clock())
        entry = LedgerEntry(seq=seq, ts=ts, submission_id=submission_id, kind=kind,
                            report_id=report_id, mission_id=mission_id,
                            payload_digest=digest, detail=dict(detail or {}),
                            prev_hash=prev)
        entry = replace(entry, hash=entry.recomputed_hash())
        self._entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.as_dict(), ensure_ascii=False,
                                    default=str) + "\n")
            self._write_head()
        return entry

    # ── write-side API ─────────────────────────────────────────────────
    def submit(self, payload: Mapping[str, Any], *, report_id: int | None,
               mission_id: int | None = None,
               submission_id: str | None = None) -> LedgerEntry:
        sid = submission_id or uuid.uuid4().hex
        digest = payload_digest(payload)
        self._mem_payloads[sid] = dict(payload)
        self.payload_store.save(sid, payload)
        return self._append(KIND_SUBMITTED, sid, report_id=report_id,
                            mission_id=mission_id if mission_id is not None
                            else payload.get("mission_id"), digest=digest)

    def mark(self, submission_id: str, kind: str, *, detail: Mapping[str, Any] | None = None,
             report_id: int | None = None) -> LedgerEntry:
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind!r}")
        state = self.state(submission_id)
        if state is None:
            raise KeyError(f"unknown submission_id {submission_id!r}")
        return self._append(
            kind, submission_id,
            report_id=state.report_id if report_id is None else report_id,
            mission_id=state.mission_id, digest=state.payload_digest, detail=detail)

    # ── read-side API ──────────────────────────────────────────────────
    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def state(self, submission_id: str) -> SubmissionState | None:
        events = [e for e in self._entries if e.submission_id == submission_id]
        if not events:
            return None
        first = events[0]
        attempts = sum(1 for e in events if e.kind in (KIND_LOST, KIND_HEAL_FAILED))
        last = events[-1]
        return SubmissionState(
            submission_id=submission_id, report_id=last.report_id,
            mission_id=last.mission_id, payload_digest=last.payload_digest,
            status=last.kind, submitted_ts=first.ts, attempts=attempts,
            last_detail=dict(last.detail))

    def states(self) -> list[SubmissionState]:
        seen: dict[str, None] = {}
        for entry in self._entries:
            seen.setdefault(entry.submission_id, None)
        return [s for s in (self.state(sid) for sid in seen) if s is not None]

    def live_states(self) -> list[SubmissionState]:
        return [s for s in self.states() if s.status in LIVE_KINDS]

    def broken_states(self) -> list[SubmissionState]:
        return [s for s in self.states() if s.needs_healing]

    def payload(self, submission_id: str) -> dict[str, Any] | None:
        stored = self.payload_store.load(submission_id)
        if stored is not None:
            return stored
        return self._mem_payloads.get(submission_id)

    def submitted_ids(self) -> list[int]:
        """Every report id the write side ever claimed (dedup, insertion order).

        Lost submissions are included on purpose: a re-scan of a still-missing
        row must keep reporting it (idempotent), not forget it because it was
        already flagged once.
        """
        out: dict[int, None] = {}
        for state in self.states():
            if state.report_id is not None:
                out.setdefault(int(state.report_id), None)
        return list(out)

    def verify(self) -> dict[str, Any]:
        """Recompute the hash chain; report the first broken line if any."""
        prev = GENESIS_HASH
        for idx, entry in enumerate(self._entries):
            if entry.seq != idx:
                return {"ok": False, "entries": len(self._entries), "broken_seq": idx,
                        "reason": "seq_gap"}
            if entry.prev_hash != prev:
                return {"ok": False, "entries": len(self._entries), "broken_seq": idx,
                        "reason": "prev_hash_mismatch"}
            if entry.hash != entry.recomputed_hash():
                return {"ok": False, "entries": len(self._entries), "broken_seq": idx,
                        "reason": "hash_mismatch"}
            prev = entry.hash

        head = self._read_head()
        if head is not None:
            if int(head.get("entries", -1)) != len(self._entries):
                return {"ok": False, "entries": len(self._entries), "broken_seq": None,
                        "reason": "log_truncated_or_extended", "head_hash": prev,
                        "anchored_entries": head.get("entries")}
            if str(head.get("head_hash")) != prev:
                return {"ok": False, "entries": len(self._entries), "broken_seq": None,
                        "reason": "head_hash_mismatch", "head_hash": prev,
                        "anchored_head": head.get("head_hash")}
        return {"ok": True, "entries": len(self._entries), "broken_seq": None,
                "head_hash": prev, "reason": None, "anchored": head is not None}

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for entry in self._entries:
            counts[entry.kind] = counts.get(entry.kind, 0) + 1
        return {
            "entries": len(self._entries),
            "by_kind": counts,
            "submissions": len(self.states()),
            "live_submissions": len(self.live_states()),
            "broken_submissions": len(self.broken_states()),
            "chain": self.verify(),
            "path": None if self.path is None else str(self.path),
        }


def ledger_dir_from_env(default: str = "uploads/tensor_report_ledger") -> Path:
    return Path(os.environ.get("TENSOR_REPORT_LEDGER_DIR", default))


def open_ledger(path: Path | str | None = None,
                clock: Callable[[], float] = time.time) -> SubmissionLedger:
    """Open the configured ledger (``path=None`` → in-memory, for tests)."""
    if path is None:
        return SubmissionLedger(None, clock=clock)
    path = Path(path)
    return SubmissionLedger(path, clock=clock)