#!/usr/bin/env python3
"""Saturation pulse collector -- three-state load metrology for the campaign dispatcher.

Context (IN15-O1, plan 16 "饱和推进·平台自优化军")
------------------------------------------------------
The autonomous dispatcher decides *how many nodes to release per wave* from a
load signal.  Before this tool the platform had no reproducible measurement of
that signal: the only trace was a single hand-written ``saturation_pulse`` row
(``note = 'online=43 busy=34 queued=2 idle=7 held16=11'``) that no code could
regenerate, so the "autoregressive fuel #1 -- measure the scale" loop could not
close.  This module turns that ad-hoc row into a programmatic pipeline.

Measured quantities (all from read-only SELECTs, none derived from a log file)
------------------------------------------------------------------------------
online
    Live execution units: enabled resident workers whose ``heartbeat_at`` is
    inside the freshness window (default 300 s = 5 min) **union** the claimants
    of missions whose own ``heartbeat_at`` is younger than 150 s.  The union is
    not cosmetic: on 2026-11-07 the deployed fleet had all 54
    ``factory_resident_workers`` rows at ``state='idle'`` /
    ``current_mission_id IS NULL`` while 36 missions were ``running`` and
    heartbeat-fresh, so a worker-table-only reading reports ``busy=0`` for a
    fully loaded fleet.  Both sources are therefore counted, and ``busy`` stays
    a subset of ``online`` by construction.
busy
    Units actually executing: claimants of an active mission whose mission
    heartbeat is fresh (<= 150 s), plus any online worker that self-reports
    ``state='running'`` or whose ``current_mission_id`` points at an active
    mission.
queued
    Missions in status ``queued`` -- the backlog the dispatcher may release.
idle
    ``online - busy`` (units warm but not on a mission).
running_missions
    Missions in ``running`` / ``claimed``, fresh or not.
stale_claims
    Active missions whose heartbeat is older than 150 s -- claims that look
    running but have stopped beating (the failure mode this metric exists for).
held
    Campaign DAG nodes in ``held`` or ``blocked`` status -- nodes the
    saturation valve (or an unsatisfied dependency) is holding back.  Held node
    identities are also stored so the next sample can report *releases*.

Storage contract
----------------
``saturation_pulse`` already exists in the deployed database with the column set
``ts | online | busy | held | released | note``.  IN15-O1 discipline says *add
only, never alter existing tables*, so this collector does not touch that
structure: the summary occupies ``online/busy/held``, ``released`` carries the
comma-separated ids released since the previous sample, and the full machine
readable snapshot (queued, idle, running_missions, threshold, held_ids, ...) is
stored as single-line JSON in ``note``.  Legacy free-text notes are tolerated by
the reader.

Safety properties (each covered by a test)
------------------------------------------
* the read phase runs inside ``BEGIN TRANSACTION READ ONLY`` and issues SELECT
  statements only -- no ``FOR UPDATE``, no ``LOCK TABLE``, no writes;
* a concurrent writer (``UPDATE``/``INSERT`` on the pulse tables) is not blocked
  by an open collector snapshot: SELECTs take only ``AccessShareLock``;
* the write phase is a single INSERT;
* ``--dry-run`` (the default) computes and prints exactly the snapshot that
  ``--commit`` would persist, and persists nothing;
* negative window / negative counts are rejected (``PulseInputError``), never
  clamped.

CLI
---
    python3 -m tools.pulse.saturation_pulse                 # dry-run (default)
    python3 -m tools.pulse.saturation_pulse --commit        # persist one sample
    python3 -m tools.pulse.saturation_pulse --ensure-schema  # idempotent DDL
    python3 -m tools.pulse.pulse_report --limit 20           # ASCII trend
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

TABLE = "saturation_pulse"
TABLE_COLUMNS: Tuple[str, ...] = ("ts", "online", "busy", "held", "released", "note")

WINDOW_SECONDS_DEFAULT = 300  # 5 min heartbeat freshness window
WINDOW_SECONDS_MIN = 1
WINDOW_SECONDS_MAX = 86_400

ACTIVE_MISSION_STATUSES: Tuple[str, ...] = ("running", "claimed")
# the factory API already treats a claim as alive only while the mission
# heartbeat is younger than 150 s; we reuse that number for "busy".
ACTIVE_MISSION_HEARTBEAT_SECONDS = 150
QUEUED_MISSION_STATUSES: Tuple[str, ...] = ("queued",)
HELD_NODE_STATUSES: Tuple[str, ...] = ("held", "blocked")

# tables carrying campaign DAG nodes; both are read, neither is modified
NODE_TABLES: Tuple[str, ...] = ("campaign_nodes", "campaign_plan_nodes")

# a heartbeat in the future beyond this tolerance is counted (clock skew) but flagged
SKEW_TOLERANCE_SECONDS = 60

NOTE_SCHEMA_VERSION = 1
MAX_STORED_IDS = 200  # bound the size of the JSON note

DSN_ENV = "SATURATION_PULSE_DSN"
SQL_DIR = Path(__file__).resolve().parent / "sql"
DDL_PATH = SQL_DIR / "001_saturation_pulse.sql"

COLLECTOR_ID = "tools/pulse/saturation_pulse.py"


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class PulseError(RuntimeError):
    """Base error for the pulse pipeline."""


class PulseInputError(PulseError, ValueError):
    """Rejected input (negative/absurd value, unknown status vocabulary, ...)."""


class PulseSchemaError(PulseError):
    """The deployed table drifted from the contract this collector was written for."""


# --------------------------------------------------------------------------- #
# raw state + snapshot
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RawState:
    """Whatever the source could read; nothing is interpreted yet."""

    workers: Tuple[Mapping[str, Any], ...] = ()
    missions: Tuple[Mapping[str, Any], ...] = ()
    nodes: Tuple[Mapping[str, Any], ...] = ()
    missing_tables: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    """One saturation sample -- the value that is stored (and can be dry-run)."""

    ts: datetime
    window_s: int
    online: int
    busy: int
    queued: int
    idle: int
    running_missions: int
    held: int
    held_ids: Tuple[str, ...] = ()
    released_ids: Tuple[str, ...] = ()
    skewed_heartbeats: int = 0
    stale_claims: int = 0
    missing_tables: Tuple[str, ...] = ()
    fresh_workers: int = 0

    # -- validation ------------------------------------------------------- #
    def validate(self) -> "Snapshot":
        _require_int("window_s", self.window_s, minimum=WINDOW_SECONDS_MIN,
                     maximum=WINDOW_SECONDS_MAX)
        for name in ("online", "busy", "queued", "idle", "running_missions", "held",
                     "skewed_heartbeats", "stale_claims", "fresh_workers"):
            _require_int(name, getattr(self, name), minimum=0)
        if self.fresh_workers > self.online:
            raise PulseInputError(f"fresh_workers={self.fresh_workers} > online={self.online}")
        if self.busy > self.online:
            raise PulseInputError(f"busy={self.busy} > online={self.online}: "
                                  "busy must be a subset of online")
        if self.held != len(self.held_ids) and len(self.held_ids) < MAX_STORED_IDS:
            raise PulseInputError(f"held={self.held} but {len(self.held_ids)} held ids stored")
        if not isinstance(self.ts, datetime) or self.ts.tzinfo is None:
            raise PulseInputError("ts must be a timezone-aware datetime")
        return self

    # -- serialisation ---------------------------------------------------- #
    def note_payload(self) -> Dict[str, Any]:
        return {
            "v": NOTE_SCHEMA_VERSION,
            "collector": COLLECTOR_ID,
            "window_s": self.window_s,
            "online": self.online,
            "busy": self.busy,
            "queued": self.queued,
            "idle": self.idle,
            "running_missions": self.running_missions,
            "held": self.held,
            "fresh_workers": self.fresh_workers,
            "skewed": self.skewed_heartbeats,
            "stale_claims": self.stale_claims,
            "held_ids": list(self.held_ids),
            "released_ids": list(self.released_ids),
            "missing_tables": list(self.missing_tables),
        }

    def note_json(self) -> str:
        return json.dumps(self.note_payload(), separators=(",", ":"), sort_keys=True)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.astimezone(timezone.utc).isoformat(),
            "window_s": self.window_s,
            "online": self.online,
            "busy": self.busy,
            "queued": self.queued,
            "idle": self.idle,
            "running_missions": self.running_missions,
            "held": self.held,
            "fresh_workers": self.fresh_workers,
            "skewed_heartbeats": self.skewed_heartbeats,
            "stale_claims": self.stale_claims,
            "held_ids": list(self.held_ids),
            "released_ids": list(self.released_ids),
            "missing_tables": list(self.missing_tables),
            "thresholds": {
                "window_s": self.window_s,
                "active_mission_statuses": list(ACTIVE_MISSION_STATUSES),
                "active_mission_heartbeat_s": ACTIVE_MISSION_HEARTBEAT_SECONDS,
                "queued_mission_statuses": list(QUEUED_MISSION_STATUSES),
                "held_node_statuses": list(HELD_NODE_STATUSES),
            },
        }

    def row(self) -> Dict[str, Any]:
        """Values for the five writable columns of ``saturation_pulse``."""
        return {
            "online": self.online,
            "busy": self.busy,
            "held": self.held,
            "released": ",".join(self.released_ids),
            "note": self.note_json(),
        }


def _require_int(name: str, value: Any, *, minimum: Optional[int] = None,
                 maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PulseInputError(f"{name} must be an int, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise PulseInputError(f"{name}={value} < {minimum} (negative/zero rejected)")
    if maximum is not None and value > maximum:
        raise PulseInputError(f"{name}={value} > {maximum}")
    return value


# --------------------------------------------------------------------------- #
# pure computation (no I/O) -- this is what the unit tests drive
# --------------------------------------------------------------------------- #
def _as_utc(moment: datetime) -> datetime:
    if not isinstance(moment, datetime):
        raise PulseInputError(f"timestamp must be datetime, got {type(moment).__name__}")
    if moment.tzinfo is None:
        raise PulseInputError("naive datetime rejected: heartbeat comparison needs tz")
    return moment.astimezone(timezone.utc)


def worker_is_fresh(heartbeat_at: Any, now: datetime, window_s: int) -> Tuple[bool, bool]:
    """Return ``(fresh, skewed)`` for one worker heartbeat.

    Boundary rule (pinned by ``test_window_boundary_is_inclusive``): a heartbeat
    whose age is exactly ``window_s`` is still fresh; ``window_s + 1 s`` is not.
    """
    _require_int("window_s", window_s, minimum=WINDOW_SECONDS_MIN,
                 maximum=WINDOW_SECONDS_MAX)
    if heartbeat_at is None:
        return False, False
    age = (_as_utc(now) - _as_utc(heartbeat_at)).total_seconds()
    skewed = age < -SKEW_TOLERANCE_SECONDS
    return age <= window_s, skewed


def node_key(node: Mapping[str, Any]) -> str:
    table = str(node.get("table") or "?")
    return f"{table}:{node.get('id')}"


def compute(raw: RawState, *, now: datetime, window_s: int = WINDOW_SECONDS_DEFAULT,
            previous_held_ids: Iterable[str] = ()) -> Snapshot:
    """Turn a :class:`RawState` into a :class:`Snapshot` (pure, deterministic)."""
    now = _as_utc(now)
    _require_int("window_s", window_s, minimum=WINDOW_SECONDS_MIN,
                 maximum=WINDOW_SECONDS_MAX)

    missions = list(raw.missions)
    mission_status: Dict[Any, str] = {}
    for mission in missions:
        mission_status[mission.get("id")] = str(mission.get("status") or "").lower()

    # --- mission side: who holds a live claim? --------------------------- #
    live_claimants: set = set()
    running_missions = 0
    stale_claims = 0
    for mission in missions:
        status = mission_status.get(mission.get("id"), "")
        if status not in ACTIVE_MISSION_STATUSES:
            continue
        running_missions += 1
        owner = str(mission.get("claimed_by") or "").strip()
        heartbeat = mission.get("heartbeat_at")
        alive = False
        if heartbeat is not None:
            age = (now - _as_utc(heartbeat)).total_seconds()
            alive = age <= ACTIVE_MISSION_HEARTBEAT_SECONDS
        if owner:
            if alive:
                live_claimants.add(owner)
            else:
                stale_claims += 1

    # --- worker side: who beats inside the freshness window? -------------- #
    fresh_worker_ids: set = set()
    self_reported_busy: set = set()
    skewed = 0
    for worker in raw.workers:
        if not bool(worker.get("enabled", True)):
            continue
        fresh, is_skewed = worker_is_fresh(worker.get("heartbeat_at"), now, window_s)
        if is_skewed:
            skewed += 1
        if not fresh:
            continue
        wid = str(worker.get("worker_id") or worker.get("agent_session_id") or "")
        fresh_worker_ids.add(wid)
        state = str(worker.get("state") or "").lower()
        cmid = worker.get("current_mission_id")
        if state == "running" or (cmid is not None
                                  and mission_status.get(cmid) in ACTIVE_MISSION_STATUSES):
            self_reported_busy.add(wid)

    online_ids = fresh_worker_ids | live_claimants
    busy_ids = live_claimants | self_reported_busy

    queued = sum(1 for status in mission_status.values() if status in QUEUED_MISSION_STATUSES)

    held_all = sorted(
        node_key(node) for node in raw.nodes
        if str(node.get("status") or "").lower() in HELD_NODE_STATUSES
    )
    held_ids = tuple(held_all[:MAX_STORED_IDS])
    previous = {str(item) for item in previous_held_ids}
    released_ids = tuple(sorted(previous - set(held_all))) if previous else ()

    snap = Snapshot(
        ts=now,
        window_s=window_s,
        online=len(online_ids),
        busy=len(busy_ids),
        queued=queued,
        idle=max(len(online_ids) - len(busy_ids), 0),
        running_missions=running_missions,
        held=len(held_all),
        held_ids=held_ids,
        released_ids=released_ids,
        skewed_heartbeats=skewed,
        stale_claims=stale_claims,
        missing_tables=tuple(raw.missing_tables),
        fresh_workers=len(fresh_worker_ids),
    )
    return snap.validate()


def parse_previous_held_ids(previous_note: Any) -> Tuple[str, ...]:
    """Extract ``held_ids`` from a stored note; legacy free text yields ``()``."""
    if not previous_note:
        return ()
    if isinstance(previous_note, (bytes, bytearray)):
        previous_note = previous_note.decode("utf-8", "replace")
    try:
        payload = json.loads(previous_note)
    except (TypeError, ValueError):
        return ()
    if not isinstance(payload, dict):
        return ()
    ids = payload.get("held_ids") or ()
    if not isinstance(ids, (list, tuple)):
        return ()
    return tuple(str(item) for item in ids)


def parse_previous_released_ids(previous_released: Any) -> Tuple[str, ...]:
    if not previous_released:
        return ()
    if isinstance(previous_released, (bytes, bytearray)):
        previous_released = previous_released.decode("utf-8", "replace")
    return tuple(part for part in str(previous_released).split(",") if part)


# --------------------------------------------------------------------------- #
# DDL / schema contract
# --------------------------------------------------------------------------- #
def ddl_statements() -> Tuple[str, ...]:
    text = DDL_PATH.read_text(encoding="utf-8")
    # strip SQL line comments *before* splitting: the documentation comments
    # contain semicolons, and a naive split would shred them into fake statements
    code_lines = []
    for line in text.splitlines():
        code_lines.append(line.split("--", 1)[0])
    stmts = [chunk.strip() for chunk in "\n".join(code_lines).split(";") if chunk.strip()]
    if not stmts:
        raise PulseSchemaError(f"no statements found in {DDL_PATH}")
    return tuple(stmts)


def verify_schema(columns: Sequence[str]) -> None:
    got = tuple(columns)
    if got != TABLE_COLUMNS:
        raise PulseSchemaError(
            f"{TABLE} column drift: deployed={got} expected={TABLE_COLUMNS}. "
            "IN15-O1 forbids altering existing tables -- adapt the collector, not the table."
        )


# --------------------------------------------------------------------------- #
# database access
# --------------------------------------------------------------------------- #
def resolve_dsn(dsn: Optional[str] = None) -> str:
    """DSN precedence: explicit arg -> $SATURATION_PULSE_DSN -> $DATABASE_URL -> socket."""
    for candidate in (dsn, os.environ.get(DSN_ENV), os.environ.get("DATABASE_URL")):
        if candidate:
            return candidate
    dbname = os.environ.get("TP_PG_DB", "tensorplatform")
    user = os.environ.get("TP_PG_USER", "postgres")
    return f"host=/var/run/postgresql dbname={dbname} user={user}"


def connect(dsn: Optional[str] = None):
    """Open a psycopg3 connection with a bounded statement timeout."""
    try:
        import psycopg  # local import: unit tests must run without a driver
    except ModuleNotFoundError as exc:  # pragma: no cover - driver is deployed
        raise PulseError("psycopg (v3) is required for database access") from exc
    resolved = resolve_dsn(dsn)
    kwargs: Dict[str, Any] = {}
    if "://" in resolved or "=" in resolved:
        kwargs["options"] = "-c statement_timeout=5000 -c timezone=utc"
    return psycopg.connect(resolved, autocommit=False, **kwargs)


READ_SQL: Dict[str, str] = {
    "workers": (
        "SELECT agent_session_id::text AS agent_session_id, worker_id, enabled, state, "
        "heartbeat_at, current_mission_id FROM factory_resident_workers"
    ),
    "missions": (
        "SELECT id, status, claimed_by, heartbeat_at FROM tensor_dev_missions "
        "WHERE status IN ('queued','running','claimed')"
    ),
}
for _tbl in NODE_TABLES:
    READ_SQL[_tbl] = f"SELECT id, status FROM {_tbl}"

PREVIOUS_SQL = (
    f"SELECT ts, online, busy, held, released, note FROM {TABLE} "
    "ORDER BY ts DESC LIMIT 1"
)

INSERT_SQL = (
    f"INSERT INTO {TABLE}(online, busy, held, released, note) "
    "VALUES(%s, %s, %s, %s, %s) RETURNING ts, online, busy, held, released, note"
)


class PgSource:
    """Read-only source over a live psycopg3 connection."""

    def __init__(self, conn, *, statements: Optional[list] = None):
        self.conn = conn
        self.statements = statements if statements is not None else []

    def _select(self, sql: str) -> list:
        self.statements.append(sql)
        return list(self.conn.execute(sql).fetchall())

    def read(self) -> RawState:
        workers = [
            {
                "agent_session_id": row[0],
                "worker_id": row[1],
                "enabled": row[2],
                "state": row[3],
                "heartbeat_at": row[4],
                "current_mission_id": row[5],
            }
            for row in self._select(READ_SQL["workers"])
        ]
        missions = [
            {"id": row[0], "status": row[1], "claimed_by": row[2], "heartbeat_at": row[3]}
            for row in self._select(READ_SQL["missions"])
        ]
        nodes: list = []
        missing: list = []
        for table in NODE_TABLES:
            try:
                nodes.extend(
                    {"table": table, "id": row[0], "status": row[1]}
                    for row in self._select(READ_SQL[table])
                )
            except Exception as exc:  # UndefinedTable in a fresh deployment
                if type(exc).__name__ != "UndefinedTable":
                    raise
                missing.append(table)
                self.conn.rollback()
        return RawState(workers=tuple(workers), missions=tuple(missions),
                        nodes=tuple(nodes), missing_tables=tuple(missing))

    def previous_sample(self) -> Optional[Mapping[str, Any]]:
        self.statements.append(PREVIOUS_SQL)
        row = self.conn.execute(PREVIOUS_SQL).fetchone()
        if row is None:
            return None
        return {"ts": row[0], "online": row[1], "busy": row[2], "held": row[3],
                "released": row[4], "note": row[5]}


def open_read_snapshot(conn):
    """Context manager: an explicitly READ ONLY transaction around the read phase.

    Exposed so that the "collector does not lock tables" claim is testable: while
    the block is open another session can still INSERT/UPDATE the pulse tables.
    """
    class _Ctx:
        def __enter__(self_inner):
            # a connection handed over mid-transaction cannot change its
            # read-only attribute (psycopg refuses); close the stale tx first
            info = getattr(conn, "info", None)
            if info is not None and getattr(info, "transaction_status", 0) != 0:
                conn.rollback()
            conn.read_only = True
            conn.execute("BEGIN")
            return conn

        def __exit__(self_inner, exc_type, exc, tb):
            try:
                conn.rollback()
            finally:
                conn.read_only = False
            return False

    return _Ctx()


def read_state(conn, *, statements: Optional[list] = None) -> Tuple[RawState, Optional[Mapping[str, Any]]]:
    source = PgSource(conn, statements=statements)
    with open_read_snapshot(conn):
        raw = source.read()
        previous = source.previous_sample()
    return raw, previous


def ensure_schema(conn) -> Dict[str, Any]:
    """Run the idempotent DDL, then verify the column contract."""
    executed = []
    for stmt in ddl_statements():
        conn.execute(stmt)
        executed.append(stmt.split()[0:3])
    conn.commit()
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (TABLE,),
    ).fetchall()
    columns = [row[0] for row in rows]
    verify_schema(columns)
    return {"statements": len(executed), "columns": columns, "idempotent": True}


def insert_snapshot(conn, snapshot: Snapshot) -> Mapping[str, Any]:
    snapshot.validate()
    row = snapshot.row()
    cur = conn.execute(
        INSERT_SQL,
        (row["online"], row["busy"], row["held"], row["released"], row["note"]),
    )
    stored = cur.fetchone()
    conn.commit()
    return {"ts": stored[0].isoformat(), "online": stored[1], "busy": stored[2],
            "held": stored[3], "released": stored[4], "note_len": len(stored[5] or "")}


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def collect(*, conn=None, dsn: Optional[str] = None, dry_run: bool = True,
            now: Optional[datetime] = None, window_s: int = WINDOW_SECONDS_DEFAULT,
            statements: Optional[list] = None) -> Dict[str, Any]:
    """Compute one sample; persist it unless ``dry_run``.

    ``dry_run`` and ``commit`` share the very same code path for the snapshot, so
    the printed snapshot is bit-identical in both modes (pinned by a test).
    """
    _require_int("window_s", window_s, minimum=WINDOW_SECONDS_MIN,
                 maximum=WINDOW_SECONDS_MAX)
    own_conn = conn is None
    conn = conn if conn is not None else connect(dsn)
    try:
        raw, previous = read_state(conn, statements=statements)
        moment = _as_utc(now) if now is not None else datetime.now(timezone.utc)
        previous_ids = parse_previous_held_ids(previous.get("note")) if previous else ()
        snapshot = compute(raw, now=moment, window_s=window_s, previous_held_ids=previous_ids)
        result: Dict[str, Any] = {
            "mode": "dry-run" if dry_run else "commit",
            "persisted": False,
            "snapshot": snapshot.as_dict(),
            "previous_sample_ts": previous["ts"].isoformat() if previous else None,
            "note": snapshot.note_json(),
        }
        if not dry_run:
            result["stored"] = dict(insert_snapshot(conn, snapshot))
            result["persisted"] = True
        return result
    finally:
        if own_conn:
            conn.close()


def _parse_now(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PulseInputError(f"--now not ISO-8601: {text!r}") from exc
    if moment.tzinfo is None:
        raise PulseInputError("--now must carry a timezone offset")
    return moment


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.pulse.saturation_pulse",
        description="Collect one saturation sample (online/busy/queued x held nodes).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true", help="persist the sample (default: dry-run)")
    mode.add_argument("--dry-run", action="store_true", help="compute only (default)")
    parser.add_argument("--window", type=int, default=WINDOW_SECONDS_DEFAULT,
                        help=f"heartbeat freshness window in seconds (default {WINDOW_SECONDS_DEFAULT})")
    parser.add_argument("--dsn", default=None, help="override DSN")
    parser.add_argument("--now", default=None, help="override sample instant (ISO-8601, tests)")
    parser.add_argument("--ensure-schema", action="store_true",
                        help="run the idempotent DDL before sampling")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)

    dry_run = not args.commit
    try:
        conn = connect(args.dsn)
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"ERROR: cannot connect: {exc}", file=sys.stderr)
        return 1
    try:
        if args.ensure_schema:
            schema = ensure_schema(conn)
            print(json.dumps({"ensure_schema": schema}, ensure_ascii=False))
        result = collect(conn=conn, dry_run=dry_run, window_s=args.window,
                         now=_parse_now(args.now))
    except PulseInputError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 2
    except PulseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        snap = result["snapshot"]
        print(f"[{result['mode']}] ts={snap['ts']} window={snap['window_s']}s "
              f"online={snap['online']} busy={snap['busy']} queued={snap['queued']} "
              f"idle={snap['idle']} running={snap['running_missions']} held={snap['held']} "
              f"released={len(snap['released_ids'])} persisted={result['persisted']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())