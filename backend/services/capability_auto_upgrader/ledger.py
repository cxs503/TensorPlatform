"""Feature ledger: append-only events, hash chain, derived level view.

Three invariants are enforced in code, not by review:

1. **Append-only.**  Events are written to ``feature_ledger.jsonl`` and mirrored
   into a ``feature_ledger`` SQLite table with the platform's own DDL
   (``ledger_id, tenant, item_type, payload, created_at``).  Nothing is updated
   in place; a level change is a new row.
2. **No silent overwrite.**  The effective level is *derived* by replaying the
   ledger.  Only ``register``/``upgrade``/``downgrade`` events move a level;
   ``observed``/``mismatch`` events record what an external audit API said and
   leave the level alone, so an external report can never quietly rewrite
   history.
3. **Tamper evident.**  Every event carries ``prev_sha256`` and ``sha256`` over
   its canonical JSON; :meth:`FeatureLedger.verify` recomputes the chain and
   cross-checks the SQLite mirror, so editing either store is detected.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .model import SCHEMA, EvidenceError, canonical_json, normalise_level, sha256_obj, utc_now

GENESIS = "0" * 64

DIRECTION_REGISTER = "register"
DIRECTION_UPGRADE = "upgrade"
DIRECTION_DOWNGRADE = "downgrade"
DIRECTION_OBSERVED = "observed"
DIRECTION_MISMATCH = "mismatch"

#: Directions that move the derived level (everything else is a record only).
LEVEL_MOVING = (DIRECTION_REGISTER, DIRECTION_UPGRADE, DIRECTION_DOWNGRADE)

ITEM_TYPE = {
    DIRECTION_REGISTER: "capability_register",
    DIRECTION_UPGRADE: "capability_upgrade",
    DIRECTION_DOWNGRADE: "capability_downgrade",
    DIRECTION_OBSERVED: "capability_observed",
    DIRECTION_MISMATCH: "capability_audit_mismatch",
}

REQUIRED_KEYS = (
    "capability_id",
    "direction",
    "level_before",
    "level_after",
    "reason_codes",
    "policy_digest",
    "window_digests",
    "trigger_digest",
    "operator",
)

#: The platform's feature_ledger DDL (kept byte-compatible with the in-tree
#: definition so rows written here can be read by existing tooling).
FEATURE_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS feature_ledger (
    ledger_id  TEXT PRIMARY KEY,
    tenant     TEXT NOT NULL,
    item_type  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class LedgerError(EvidenceError):
    """Ledger misuse: incomplete event, unknown direction, broken chain."""


def _validate_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, Mapping):
        raise LedgerError("event must be a mapping")
    missing = [k for k in REQUIRED_KEYS if k not in event]
    if missing:
        raise LedgerError(f"event is incomplete, missing {missing}")
    direction = event["direction"]
    if direction not in ITEM_TYPE:
        raise LedgerError(f"unknown direction {direction!r}; expected one of {sorted(ITEM_TYPE)}")
    payload = dict(event)
    payload["capability_id"] = str(payload["capability_id"])
    if not payload["capability_id"].strip():
        raise LedgerError("capability_id must be non-empty")
    for name in ("level_before", "level_after"):
        value = payload[name]
        payload[name] = None if value is None else normalise_level(value)
    if direction in LEVEL_MOVING and payload["level_after"] is None:
        raise LedgerError("level-moving events must record level_after")
    if not payload["reason_codes"]:
        raise LedgerError("reason_codes must not be empty (no unexplained level change)")
    payload["reason_codes"] = [str(c) for c in payload["reason_codes"]]
    payload["window_digests"] = [str(d) for d in payload["window_digests"]]
    payload["policy_digest"] = str(payload["policy_digest"])
    payload["trigger_digest"] = str(payload["trigger_digest"])
    payload["operator"] = str(payload["operator"])
    return payload


class FeatureLedger:
    """Append-only feature ledger with a hash chain and a derived level view."""

    def __init__(self, root: Path | str, tenant: str = "platform", db_name: str = "feature_ledger.db"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.tenant = tenant
        self.events_path = self.root / "feature_ledger.jsonl"
        self.db_path = self.root / db_name
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(FEATURE_LEDGER_DDL)
        self._conn.commit()

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FeatureLedger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- read ------------------------------------------------------------
    def events(self) -> List[Dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        for lineno, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:  # pragma: no cover - tamper path
                raise LedgerError(f"event line {lineno} is not JSON: {exc}") from exc
        return out

    def rows(self, item_type: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM feature_ledger"
        params: tuple = ()
        if item_type is not None:
            sql += " WHERE item_type = ?"
            params = (item_type,)
        sql += " ORDER BY rowid"
        out = []
        for row in self._conn.execute(sql, params).fetchall():
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            out.append(item)
        return out

    def levels(self) -> Dict[str, str]:
        """Derived level per capability: replay level-moving events in order."""
        levels: Dict[str, str] = {}
        for event in self.events():
            if event.get("direction") in LEVEL_MOVING and event.get("level_after"):
                levels[event["capability_id"]] = event["level_after"]
        return levels

    def level_of(self, capability_id: str) -> Optional[str]:
        return self.levels().get(capability_id)

    def history(self, capability_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.events() if e["capability_id"] == capability_id]

    def last_event(self) -> Optional[Dict[str, Any]]:
        events = self.events()
        return events[-1] if events else None

    def find(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        for event in self.events():
            if event.get("idempotency_key") == idempotency_key:
                return event
        return None

    # -- write -----------------------------------------------------------
    def append(self, event: Mapping[str, Any], ts: Optional[str] = None) -> Dict[str, Any]:
        """Append one event.  Returns the stored event with ``noop`` flag set.

        Idempotency key = (capability_id, direction, level_before, level_after,
        trigger_digest).  Re-appending the same decision returns the existing
        event and writes nothing.
        """
        payload = _validate_event(event)
        key = sha256_obj({
            "capability_id": payload["capability_id"],
            "direction": payload["direction"],
            "level_before": payload["level_before"],
            "level_after": payload["level_after"],
            "trigger_digest": payload["trigger_digest"],
        })
        existing = self.find(key)
        if existing is not None:
            stored = dict(existing)
            stored["noop"] = True
            return stored

        events = self.events()
        seq = len(events) + 1
        prev = events[-1]["sha256"] if events else GENESIS
        entry: Dict[str, Any] = {
            "schema": SCHEMA + "/ledger-entry",
            "seq": seq,
            "ts": ts or utc_now(),
            "capability_id": payload["capability_id"],
            "direction": payload["direction"],
            "level_before": payload["level_before"],
            "level_after": payload["level_after"],
            "reason_codes": payload["reason_codes"],
            "policy_digest": payload["policy_digest"],
            "window_digests": payload["window_digests"],
            "trigger_digest": payload["trigger_digest"],
            "operator": payload["operator"],
            "tenant": self.tenant,
            "idempotency_key": key,
            "prev_sha256": prev,
        }
        for extra in ("evidence", "notes", "audit_level", "snapshot_digest"):
            if extra in payload:
                entry[extra] = payload[extra]
        entry["sha256"] = sha256_obj(entry)

        self._append_line(entry)
        self._insert_row(entry)
        stored = dict(entry)
        stored["noop"] = False
        return stored

    def _append_line(self, entry: Mapping[str, Any]) -> None:
        line = canonical_json(entry) + "\n"
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _insert_row(self, entry: Mapping[str, Any]) -> None:
        ledger_id = digest_id(entry["sha256"])
        self._conn.execute(
            "INSERT INTO feature_ledger (ledger_id, tenant, item_type, payload, created_at) "
            "VALUES (?,?,?,?,?)",
            (ledger_id, self.tenant, ITEM_TYPE[entry["direction"]], canonical_json(entry), entry["ts"]),
        )
        self._conn.commit()

    # -- verification ----------------------------------------------------
    def verify(self) -> Dict[str, Any]:
        """Recompute the hash chain and cross-check the SQLite mirror."""
        events = self.events()
        problems: List[str] = []
        prev = GENESIS
        for idx, event in enumerate(events, 1):
            sha = event.get("sha256")
            body = {k: v for k, v in event.items() if k != "sha256"}
            recomputed = sha256_obj(body)
            if sha != recomputed:
                problems.append(f"seq {event.get('seq', idx)}: sha256 mismatch")
            if event.get("prev_sha256") != prev:
                problems.append(f"seq {event.get('seq', idx)}: prev_sha256 does not chain")
            if event.get("seq") != idx:
                problems.append(f"seq {event.get('seq', idx)}: out of order (expected {idx})")
            prev = sha or ""

        db_rows = {row["ledger_id"]: row for row in self.rows()}
        for event in events:
            ledger_id = digest_id(event.get("sha256", ""))
            row = db_rows.get(ledger_id)
            if row is None:
                problems.append(f"seq {event.get('seq')}: missing SQLite mirror row")
                continue
            if canonical_json(row["payload"]) != canonical_json(event):
                problems.append(f"seq {event.get('seq')}: SQLite payload differs from jsonl event")
        if len(db_rows) != len(events):
            problems.append(f"mirror row count {len(db_rows)} != event count {len(events)}")

        return {
            "ok": not problems,
            "n_events": len(events),
            "n_rows": len(db_rows),
            "head_sha256": events[-1]["sha256"] if events else GENESIS,
            "problems": problems,
        }


def digest_id(sha: str) -> str:
    """Short row id derived from the event digest (stable, collision-safe enough)."""
    return f"fl-{sha[:24]}"