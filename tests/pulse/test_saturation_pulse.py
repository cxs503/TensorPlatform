#!/usr/bin/env python3
"""Tests for the IN15-O1 saturation-pulse pipeline.

Coverage map (the mission acceptance criteria are the test names):

* time-window boundary            -> ``test_window_boundary_is_inclusive``
* empty tables                    -> ``test_empty_state_yields_zeroes``,
                                     ``test_report_on_empty_table``
* negative values rejected        -> ``test_negative_inputs_rejected``,
                                     ``test_negative_snapshot_rejected``
* dry-run == real run             -> ``test_dry_run_matches_commit``
* collector does not lock tables  -> ``test_read_phase_is_read_only_select_only``,
                                     ``test_live_snapshot_holds_only_access_share_locks``
* idempotent single schema action -> ``test_ddl_is_idempotent_and_contract_locked``
* three-state semantics           -> ``test_three_state_accounting``,
                                     ``test_busy_is_subset_of_online``
* trend report                    -> ``test_report_renders_trend``, ``test_legacy_note_row``

Run: ``pytest tests/pulse -q``
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pulse import pulse_report as pr  # noqa: E402
from tools.pulse import saturation_pulse as sp  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Minimal psycopg3 stand-in that records every statement it is given."""

    def __init__(self, *, workers=(), missions=(), nodes=(), previous=None,
                 stored_row=None, columns=None):
        self.workers = list(workers)
        self.missions = list(missions)
        self.nodes = dict(nodes)  # table -> rows
        self.previous = previous
        self.stored_row = stored_row or (NOW, 1, 1, 1, "", "{}")
        self.columns = columns if columns is not None else list(sp.TABLE_COLUMNS)
        self.statements = []
        self.read_only_flags = []
        self.commits = 0
        self.rollbacks = 0
        self.read_only = False

    # -- connection protocol ------------------------------------------- #
    def execute(self, sql, params=None):
        self.statements.append(sql.strip())
        self.read_only_flags.append(bool(self.read_only))
        flat = " ".join(sql.split())
        if flat == "BEGIN":
            return FakeCursor([])
        if "factory_resident_workers" in flat:
            return FakeCursor([_worker_row(w) for w in self.workers])
        if "tensor_dev_missions" in flat:
            return FakeCursor([_mission_row(m) for m in self.missions])
        if flat.startswith("SELECT id, status FROM campaign_plan_nodes"):
            return FakeCursor(self.nodes.get("campaign_plan_nodes", []))
        if flat.startswith("SELECT id, status FROM campaign_nodes"):
            return FakeCursor(self.nodes.get("campaign_nodes", []))
        if flat.startswith("SELECT ts, online, busy, held, released, note"):
            return FakeCursor([self.previous] if self.previous else [])
        if flat.startswith("INSERT INTO saturation_pulse"):
            return FakeCursor([self.stored_row])
        if "information_schema.columns" in flat:
            return FakeCursor([(c,) for c in self.columns])
        if "pg_locks" in flat:
            return FakeCursor([("AccessShareLock",)])
        return FakeCursor([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _worker(wid, *, age_s=10, enabled=True, state="idle", cmid=None):
    return {"worker_id": wid, "agent_session_id": wid, "enabled": enabled,
            "state": state, "current_mission_id": cmid,
            "heartbeat_at": NOW - timedelta(seconds=age_s)}


def _mission(mid, status, *, owner=None, age_s=5):
    return {"id": mid, "status": status, "claimed_by": owner,
            "heartbeat_at": None if age_s is None else NOW - timedelta(seconds=age_s)}


def _worker_row(worker):
    """SQL row order: agent_session_id, worker_id, enabled, state, heartbeat_at, current_mission_id."""
    return (worker.get("agent_session_id"), worker.get("worker_id"),
            worker.get("enabled", True), worker.get("state"),
            worker.get("heartbeat_at"), worker.get("current_mission_id"))


def _mission_row(mission):
    """SQL row order: id, status, claimed_by, heartbeat_at."""
    return (mission.get("id"), mission.get("status"),
            mission.get("claimed_by"), mission.get("heartbeat_at"))


def _raw(**kwargs):
    return sp.RawState(**kwargs)


# --------------------------------------------------------------------------- #
# 1. heartbeat freshness window (boundary)
# --------------------------------------------------------------------------- #
def test_window_boundary_is_inclusive():
    # exactly at the window edge -> still fresh (age <= window)
    assert sp.worker_is_fresh(NOW - timedelta(seconds=sp.WINDOW_SECONDS_DEFAULT), NOW,
                              sp.WINDOW_SECONDS_DEFAULT) == (True, False)
    # one second past the edge -> stale
    assert sp.worker_is_fresh(NOW - timedelta(seconds=sp.WINDOW_SECONDS_DEFAULT + 1), NOW,
                              sp.WINDOW_SECONDS_DEFAULT) == (False, False)
    # never beaten -> stale, not skewed
    assert sp.worker_is_fresh(None, NOW, 300) == (False, False)
    # future heartbeat inside skew tolerance is fresh but flagged
    fresh, skewed = sp.worker_is_fresh(NOW + timedelta(seconds=30), NOW, 300)
    assert fresh is True and skewed is False
    # far future heartbeat -> fresh but flagged as skewed
    fresh, skewed = sp.worker_is_fresh(NOW + timedelta(seconds=600), NOW, 300)
    assert fresh is True and skewed is True
    # naive timestamps are refused, not silently assumed UTC
    with pytest.raises(sp.PulseInputError):
        sp.worker_is_fresh(datetime(2026, 11, 7, 12, 0, 0), NOW, 300)


# --------------------------------------------------------------------------- #
# 2. three-state accounting
# --------------------------------------------------------------------------- #
def test_three_state_accounting():
    raw = _raw(
        workers=[
            _worker("w-fresh-idle", age_s=30),
            _worker("w-fresh-busy", age_s=30, state="running", cmid=11),
            _worker("w-stale", age_s=sp.WINDOW_SECONDS_DEFAULT + 60, state="running"),
            _worker("w-disabled", age_s=1, enabled=False),
        ],
        missions=[
            _mission(11, "running", owner="w-fresh-busy", age_s=10),
            _mission(12, "running", owner="w-claim-only", age_s=10),
            _mission(13, "running", owner="w-stuck", age_s=sp.ACTIVE_MISSION_HEARTBEAT_SECONDS + 5),
            _mission(14, "queued"),
            _mission(15, "queued"),
        ],
        nodes=[
            {"table": "campaign_nodes", "id": 7, "status": "blocked"},
            {"table": "campaign_nodes", "id": 8, "status": "done"},
            {"table": "campaign_plan_nodes", "id": 9, "status": "held"},
        ],
    )
    snap = sp.compute(raw, now=NOW)

    assert snap.fresh_workers == 2                      # stale + disabled workers excluded
    assert snap.online == 3                             # 2 fresh workers + claim-only unit
    assert snap.busy == 2                               # w-fresh-busy + w-claim-only
    assert snap.idle == 1
    assert snap.queued == 2
    assert snap.running_missions == 3
    assert snap.stale_claims == 1                       # mission 13 stopped beating
    assert snap.held == 2
    assert snap.held_ids == ("campaign_nodes:7", "campaign_plan_nodes:9")
    assert snap.skewed_heartbeats == 0


def test_busy_is_subset_of_online():
    snap = sp.compute(_raw(workers=[_worker("a")]), now=NOW)
    with pytest.raises(sp.PulseInputError):
        replace(snap, busy=snap.online + 1).validate()
    with pytest.raises(sp.PulseInputError):
        replace(snap, fresh_workers=snap.online + 1).validate()


# --------------------------------------------------------------------------- #
# 3. empty tables
# --------------------------------------------------------------------------- #
def test_empty_state_yields_zeroes():
    snap = sp.compute(_raw(), now=NOW)
    assert (snap.online, snap.busy, snap.queued, snap.idle, snap.running_missions,
            snap.held) == (0, 0, 0, 0, 0, 0)
    payload = json.loads(snap.note_json())
    assert payload["v"] == sp.NOTE_SCHEMA_VERSION
    assert payload["held_ids"] == [] and payload["released_ids"] == []
    assert payload["window_s"] == sp.WINDOW_SECONDS_DEFAULT
    row = snap.row()
    assert row["released"] == ""
    assert set(row) == {"online", "busy", "held", "released", "note"}
    assert len(row["note"]) < 4000


def test_empty_tables_via_connection_are_zero_and_committable():
    conn = FakeConn()
    result = sp.collect(conn=conn, dry_run=False, now=NOW)
    assert result["persisted"] is True
    snap = result["snapshot"]
    assert snap["online"] == 0 and snap["held"] == 0 and snap["queued"] == 0
    assert conn.commits == 1


# --------------------------------------------------------------------------- #
# 4. negative values rejected
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("window_s", [-1, 0, -300, sp.WINDOW_SECONDS_MAX + 1])
def test_negative_inputs_rejected(window_s):
    with pytest.raises(sp.PulseInputError):
        sp.compute(_raw(), now=NOW, window_s=window_s)
    with pytest.raises(sp.PulseInputError):
        sp.collect(conn=FakeConn(), dry_run=True, now=NOW, window_s=window_s)


def test_negative_snapshot_rejected():
    snap = sp.compute(_raw(), now=NOW)
    with pytest.raises(sp.PulseInputError):
        replace(snap, online=-3).validate()
    with pytest.raises(sp.PulseInputError):
        replace(snap, held=-1).validate()
    with pytest.raises(sp.PulseInputError):
        replace(snap, ts=datetime(2026, 11, 7)).validate()  # naive ts


def test_cli_rejects_negative_window_exit_code_2():
    assert sp.main(["--dry-run", "--window", "-5"]) == 2


# --------------------------------------------------------------------------- #
# 5. dry-run == commit
# --------------------------------------------------------------------------- #
def test_dry_run_matches_commit():
    kwargs = dict(workers=[_worker("w1", state="running", cmid=5)],
                  missions=[_mission(5, "running", owner="w1")],
                  nodes={"campaign_nodes": [(1, "blocked")]})
    dry_conn = FakeConn(**kwargs)
    commit_conn = FakeConn(**kwargs)

    dry = sp.collect(conn=dry_conn, dry_run=True, now=NOW)
    real = sp.collect(conn=commit_conn, dry_run=False, now=NOW)

    assert dry["snapshot"] == real["snapshot"]           # identical measured value
    assert dry["note"] == real["note"]
    assert dry["persisted"] is False and real["persisted"] is True
    assert sum("INSERT INTO saturation_pulse" in s for s in dry_conn.statements) == 0
    assert sum("INSERT INTO saturation_pulse" in s for s in commit_conn.statements) == 1
    assert sp.parse_previous_held_ids(real["note"]) == ("campaign_nodes:1",)


# --------------------------------------------------------------------------- #
# 6. collector does not lock tables
# --------------------------------------------------------------------------- #
def test_read_phase_is_read_only_select_only():
    conn = FakeConn(workers=[_worker("w1")], missions=[_mission(1, "queued")],
                    nodes={"campaign_nodes": [(1, "blocked")]})
    sp.collect(conn=conn, dry_run=True, now=NOW)
    read_statements = [s for s in conn.statements if not s.startswith("INSERT INTO")]
    assert read_statements, "collector must read something"
    for stmt in read_statements:
        flat = " ".join(stmt.split())
        assert flat == "BEGIN" or flat.startswith("SELECT"), flat
        assert "FOR UPDATE" not in flat.upper()
        assert "LOCK TABLE" not in flat.upper()
        assert "UPDATE " not in flat.upper() and "DELETE" not in flat.upper()
    # the read phase opened with BEGIN and did flag the session read-only
    assert conn.statements[0] == "BEGIN"
    assert conn.read_only_flags[0] is True


# --------------------------------------------------------------------------- #
# 7. release accounting between consecutive samples
# --------------------------------------------------------------------------- #
def test_released_ids_diff_between_samples():
    previous = json.dumps({"held_ids": ["campaign_nodes:1", "campaign_nodes:2"]})
    ids = sp.parse_previous_held_ids(previous)
    assert ids == ("campaign_nodes:1", "campaign_nodes:2")
    snap = sp.compute(_raw(nodes=[{"table": "campaign_nodes", "id": 2, "status": "blocked"}]),
                      now=NOW, previous_held_ids=ids)
    assert snap.held == 1
    assert snap.released_ids == ("campaign_nodes:1",)
    assert snap.row()["released"] == "campaign_nodes:1"
    # legacy free-text note cannot be diffed -> no invented releases
    assert sp.parse_previous_held_ids("online=43 busy=34 queued=2") == ()
    assert sp.parse_previous_released_ids("154,155") == ("154", "155")


def test_previous_sample_is_used_by_collect():
    prev_ts = NOW - timedelta(minutes=5)
    prev_note = json.dumps({"v": 1, "held_ids": ["campaign_nodes:99"]})
    conn = FakeConn(nodes={"campaign_nodes": []},
                    previous=(prev_ts, 5, 2, 1, "", prev_note))
    result = sp.collect(conn=conn, dry_run=True, now=NOW)
    assert result["snapshot"]["released_ids"] == ["campaign_nodes:99"]
    assert result["previous_sample_ts"] == prev_ts.isoformat()


# --------------------------------------------------------------------------- #
# 8. single idempotent schema action
# --------------------------------------------------------------------------- #
def test_ddl_is_idempotent_and_contract_locked():
    statements = sp.ddl_statements()
    assert statements
    for stmt in statements:
        flat = " ".join(stmt.split())
        assert "IF NOT EXISTS" in flat.upper(), flat
        assert not flat.upper().startswith("ALTER"), "IN15-O1 forbids schema surgery"
        assert "saturation_pulse" in flat
    sp.verify_schema(sp.TABLE_COLUMNS)  # 无异常即契约匹配
    with pytest.raises(sp.PulseSchemaError):
        sp.verify_schema(("ts", "online", "busy", "held"))  # dropped columns -> drift


def test_ensure_schema_verifies_columns():
    conn = FakeConn()
    out = sp.ensure_schema(conn)
    assert out["columns"] == list(sp.TABLE_COLUMNS)
    assert out["idempotent"] is True
    drift = FakeConn(columns=["ts", "online"])
    with pytest.raises(sp.PulseSchemaError):
        sp.ensure_schema(drift)


# --------------------------------------------------------------------------- #
# 9. report CLI
# --------------------------------------------------------------------------- #
def _sample(ts, online, busy, queued, held, note=True, released=""):
    payload = json.dumps({"v": 1, "queued": queued, "idle": online - busy,
                          "running_missions": busy, "window_s": 300}) if note else None
    return {"ts": ts.isoformat(), "online": online, "busy": busy, "held": held,
            "released": released, "source": "pulse-v1" if note else "legacy",
            "queued": queued if note else None, "idle": online - busy if note else None,
            "running_missions": busy if note else None, "window_s": 300 if note else None}


def test_report_renders_trend():
    samples = [_sample(NOW + timedelta(minutes=i), 40 + i, 30 + i, 2, 11 - i) for i in range(4)]
    text = pr.render(samples)
    assert "saturation pulse trend" in text
    assert "samples=4" in text
    for label in ("online", "busy", "queued", "held"):
        assert label in text
    assert any(ch in text for ch in pr.SPARK_CHARS.strip())
    assert "pulse-v1" in text


def test_report_on_empty_table():
    text = pr.render([])
    assert "no samples" in text
    assert sp.TABLE in text


def test_legacy_note_row_is_reported_without_inventing_queued():
    row = pr.normalise_row({"ts": NOW, "online": 43, "busy": 34, "held": 11,
                            "released": "154,155,156",
                            "note": "online=43 busy=34 queued=2 idle=7 held16=11"})
    assert row["source"] == "legacy"
    assert row["queued"] is None and row["idle"] is None
    text = pr.render([row])
    assert "legacy" in text and "154" not in text.split("rel")[0][-4:]  # rows, not ids


def test_sparkline_and_limit_validation():
    assert pr.sparkline([1, 2, 3])[0] == pr.SPARK_CHARS[0]
    assert pr.sparkline([1, 2, 3])[-1] == pr.SPARK_CHARS[-1]
    assert pr.sparkline([5, 5, 5]) == pr.SPARK_CHARS[4] * 3
    assert pr.sparkline([None, 1])[0] == " "
    for bad in (0, -3, True):
        with pytest.raises(sp.PulseInputError):
            pr.load_samples(FakeConn(), bad)


# --------------------------------------------------------------------------- #
# 10. acceptance-evidence driver
# --------------------------------------------------------------------------- #
def test_evidence_driver_dry_run_and_guard():
    from tools.pulse import acceptance_evidence as ae

    assert ae.main(["--dry-run"]) == 0  # prints the plan, touches nothing
    # the criterion is *two* consecutive samples: refuse a narrower request
    assert ae.main(["--samples", "1", "--dry-run"]) == 0
    assert ae.main(["--samples", "1"]) == 2


# --------------------------------------------------------------------------- #
# 11. live database checks (skipped when no PostgreSQL is reachable)
# --------------------------------------------------------------------------- #
def _live_conn():
    try:
        conn = sp.connect()
        conn.execute("SELECT 1")
        return conn
    except Exception:
        pytest.skip("no live PostgreSQL for the pulse pipeline")


def test_live_dry_run_is_select_only():
    conn = _live_conn()
    try:
        statements = []
        result = sp.collect(conn=conn, dry_run=True, statements=statements,
                            window_s=sp.WINDOW_SECONDS_DEFAULT)
        assert result["persisted"] is False
        assert statements
        for stmt in statements:
            assert " ".join(stmt.split()).startswith("SELECT"), stmt
        snap = result["snapshot"]
        assert snap["online"] >= 0 and snap["busy"] <= snap["online"]
    finally:
        conn.close()


def test_live_snapshot_holds_only_access_share_locks():
    """An open collector snapshot must not block writers (no table lock taken)."""
    psycopg = pytest.importorskip("psycopg")
    conn = _live_conn()
    try:
        with sp.open_read_snapshot(conn):
            conn.execute(sp.READ_SQL["workers"]).fetchall()
            pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            # relation-level locks only: every backend in a transaction also holds
            # a virtualxid lock (ExclusiveLock) which has nothing to do with tables
            modes = conn.execute(
                "SELECT relname, mode FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
                "WHERE l.pid = %s AND l.locktype = 'relation'", (pid,)).fetchall()
            assert modes, "expected at least one relation lock for our own backend"
            assert {mode for _rel, mode in modes} <= {"AccessShareLock"}, modes

            dsn = sp.resolve_dsn()
            other = psycopg.connect(dsn, autocommit=True,
                                    options="-c statement_timeout=2000 -c lock_timeout=1500")
            try:
                # a no-op UPDATE still needs RowExclusiveLock: allowed iff we hold
                # nothing stronger than AccessShareLock ourselves
                other.execute("UPDATE saturation_pulse SET note = note "
                              "WHERE ts < now() - interval '100 years'")
            finally:
                other.close()
    finally:
        conn.close()