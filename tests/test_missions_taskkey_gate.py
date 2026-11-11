"""Task-key deduplication gate tests (m15073).

Verifies:
  a) task_key field appears in list response
  b) task_key= exact filter
  c) task_key_like= substring filter
  d) conflicts endpoint hits same key with two missions
  e) time window (hours shrink) excludes old conflicts
  f) different organization isolation
  g) empty task_key not counted as conflict

Uses FastAPI TestClient against a throw-away app with in-memory SQLite.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

# Ensure backend is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── In-memory SQLite engine ──────────────────────────────────────────────
ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    future=True,
)


@event.listens_for(ENGINE, "connect")
def _register_sqlite_funcs(dbapi_conn, record):
    dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())


SCHEMA = [
    "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, organization_id INTEGER)",
    """CREATE TABLE tensor_dev_missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        goal TEXT NOT NULL,
        repo TEXT NOT NULL,
        base_ref TEXT DEFAULT 'main',
        co_repos TEXT,
        parent_mission_id INTEGER,
        budget TEXT DEFAULT '{}',
        runner TEXT DEFAULT 'openhands',
        origin TEXT DEFAULT 'owner',
        model TEXT,
        agent_session_id TEXT,
        worker_role TEXT,
        campaign_id INTEGER,
        campaign_phase TEXT,
        status TEXT DEFAULT 'queued',
        claim_token TEXT,
        claimed_by TEXT,
        session_id INTEGER,
        result TEXT DEFAULT '{}',
        created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        heartbeat_at TEXT,
        finished_at TEXT,
        task_key TEXT
    )""",
]

SEED = [
    "INSERT INTO organizations (id, name) VALUES (1, 'OrgA'), (2, 'OrgB')",
    "INSERT INTO users (id, username, role, organization_id) VALUES (1, 'admin', 'ADMIN', 1)",
    "INSERT INTO users (id, username, role, organization_id) VALUES (2, 'eng', 'ENGINEER', 1)",
    "INSERT INTO users (id, username, role, organization_id) VALUES (3, 'other', 'ENGINEER', 2)",
]


def _seed_mission(db, org_id, goal, repo, status="queued", task_key=None, created_at=None):
    """Insert a mission row and return its id."""
    now = created_at or datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, task_key, created_at, created_by)
            VALUES (:org, :goal, :repo, :status, :tk, :cat, 1)
        """),
        {"org": org_id, "goal": goal, "repo": repo, "status": status,
         "tk": task_key, "cat": now},
    )
    db.commit()
    return cur.lastrowid


# ── App fixture ──────────────────────────────────────────────────────────
@pytest.fixture
def app_and_client():
    """Build a minimal FastAPI app with the missions router, SQLite-backed."""
    from api import deps as deps_mod
    from api.tensor_missions import router as missions_router
    import api.tensor_missions as tm

    app = FastAPI()

    # Use dependency_overrides to bypass auth
    def _fake_auth():
        return {"id": 1, "username": "admin", "role": "ADMIN", "organization_id": 1}

    def _fake_get_db():
        Session = sessionmaker(bind=ENGINE, future=True)
        return Session()

    # Override the actual functions used by the router
    app.dependency_overrides[deps_mod.require_auth] = _fake_auth
    app.dependency_overrides[deps_mod.get_db] = _fake_get_db

    # Patch resolved_org and MISSION_STATUSES
    original_resolved_org = tm.resolved_org
    tm.resolved_org = lambda *a, **k: (
        a[0] if a[0] else (a[1].get("organization_id", 1) if len(a) > 1 and a[1] else 1)
    )
    tm.MISSION_STATUSES = (
        "queued", "claimed", "running", "completed", "failed",
        "stopped", "awaiting_acceptance", "agent_lost", "suspended",
    )

    app.include_router(missions_router, prefix="/api/v1")

    with ENGINE.begin() as conn:
        for stmt in SCHEMA:
            conn.execute(text(stmt))
        for stmt in SEED:
            conn.execute(text(stmt))

    client = TestClient(app)
    yield client, ENGINE

    # Restore
    tm.resolved_org = original_resolved_org
    app.dependency_overrides.clear()
    with ENGINE.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tensor_dev_missions"))
        conn.execute(text("DROP TABLE IF EXISTS users"))
        conn.execute(text("DROP TABLE IF EXISTS organizations"))


# ── Tests ────────────────────────────────────────────────────────────────

class TestTaskKeyInListResponse:
    """a) task_key field appears in list response."""

    def test_task_key_present_in_list(self, app_and_client):
        client, engine = app_and_client
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Test mission with key', 'TensorPlatform', 'queued',
                        'test_key_001', datetime('now'), 1)
            """))

        resp = client.get("/api/v1/tensor/missions?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) >= 1
        item = data["items"][0]
        assert "task_key" in item, "task_key field must be present in list response"
        assert item["task_key"] == "test_key_001"

    def test_task_key_null_in_list(self, app_and_client):
        client, engine = app_and_client
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, created_at, created_by)
                VALUES (1, 'Test mission without key', 'TensorPlatform', 'queued',
                        datetime('now'), 1)
            """))

        resp = client.get("/api/v1/tensor/missions?limit=10")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert "task_key" in item
        assert item["task_key"] is None


class TestTaskKeyExactFilter:
    """b) task_key= exact filter."""

    def test_exact_filter_returns_match(self, app_and_client):
        client, engine = app_and_client
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Mission A', 'TensorPlatform', 'queued', 'exact_key',
                        datetime('now'), 1)
            """))
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Mission B', 'TensorPlatform', 'queued', 'other_key',
                        datetime('now'), 1)
            """))

        resp = client.get("/api/v1/tensor/missions?task_key=exact_key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["task_key"] == "exact_key"

    def test_exact_filter_returns_empty(self, app_and_client):
        client, engine = app_and_client
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Mission A', 'TensorPlatform', 'queued', 'some_key',
                        datetime('now'), 1)
            """))

        resp = client.get("/api/v1/tensor/missions?task_key=nonexistent_key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert len(data["items"]) == 0


class TestTaskKeyLikeFilter:
    """c) task_key_like= substring filter."""

    def test_like_filter_matches_substring(self, app_and_client):
        client, engine = app_and_client
        with engine.begin() as conn:
            for i in range(3):
                conn.execute(text("""
                    INSERT INTO tensor_dev_missions
                        (organization_id, goal, repo, status, task_key, created_at, created_by)
                    VALUES (1, :goal, 'TensorPlatform', 'queued', :tk,
                            datetime('now'), 1)
                """), {"goal": f"Mission {i}", "tk": f"prefix_task_{i:02d}"})
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Other', 'TensorPlatform', 'queued', 'different_key',
                        datetime('now'), 1)
            """))

        resp = client.get("/api/v1/tensor/missions?task_key_like=prefix_task")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        for item in data["items"]:
            assert "prefix_task" in item["task_key"]


class TestTaskKeyConflicts:
    """d) conflicts endpoint hits same key with two missions."""

    def test_conflict_detected(self, app_and_client):
        client, engine = app_and_client
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Mission 1', 'TensorPlatform', 'running', 'dup_key',
                        :now, 1)
            """), {"now": now})
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Mission 2', 'TensorPlatform', 'queued', 'dup_key',
                        :now, 1)
            """), {"now": now})

        resp = client.get("/api/v1/tensor/missions/task-key-conflicts?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["conflicts"]) >= 1
        conflict = data["conflicts"][0]
        assert conflict["task_key"] == "dup_key"
        assert conflict["count"] == 2
        assert len(conflict["mission_ids"]) == 2
        assert len(conflict["statuses"]) == 2


class TestConflictTimeWindow:
    """e) time window (hours shrink) excludes old conflicts."""

    def test_old_conflict_excluded(self, app_and_client):
        client, engine = app_and_client
        # Insert missions 48 hours ago
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Old 1', 'TensorPlatform', 'completed', 'old_key',
                        :old, 1)
            """), {"old": old})
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Old 2', 'TensorPlatform', 'completed', 'old_key',
                        :old, 1)
            """), {"old": old})

        # 24h window should NOT include 48h-old missions
        resp = client.get("/api/v1/tensor/missions/task-key-conflicts?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        # Filter out old_key from conflicts
        old_conflicts = [c for c in data["conflicts"] if c["task_key"] == "old_key"]
        assert len(old_conflicts) == 0, "48h-old missions should not appear in 24h window"

    def test_recent_conflict_included(self, app_and_client):
        client, engine = app_and_client
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Recent 1', 'TensorPlatform', 'running', 'recent_key',
                        :now, 1)
            """), {"now": now})
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Recent 2', 'TensorPlatform', 'queued', 'recent_key',
                        :now, 1)
            """), {"now": now})

        resp = client.get("/api/v1/tensor/missions/task-key-conflicts?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        recent_conflicts = [c for c in data["conflicts"] if c["task_key"] == "recent_key"]
        assert len(recent_conflicts) == 1
        assert recent_conflicts[0]["count"] == 2


class TestOrganizationIsolation:
    """f) different organization isolation."""

    def test_cross_org_not_conflict(self, app_and_client):
        client, engine = app_and_client
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            # Org 1
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Org1 Mission', 'TensorPlatform', 'running', 'shared_key',
                        :now, 1)
            """), {"now": now})
            # Org 2
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (2, 'Org2 Mission', 'TensorPlatform', 'queued', 'shared_key',
                        :now, 1)
            """), {"now": now})

        # Query as org 1 user - should NOT see cross-org conflict
        resp = client.get("/api/v1/tensor/missions/task-key-conflicts?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        shared = [c for c in data["conflicts"] if c["task_key"] == "shared_key"]
        assert len(shared) == 0, "Cross-org missions should not conflict"


class TestEmptyTaskKeyExcluded:
    """g) empty task_key not counted as conflict."""

    def test_null_task_key_not_conflict(self, app_and_client):
        client, engine = app_and_client
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'No key 1', 'TensorPlatform', 'running', NULL,
                        :now, 1)
            """), {"now": now})
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'No key 2', 'TensorPlatform', 'queued', NULL,
                        :now, 1)
            """), {"now": now})

        resp = client.get("/api/v1/tensor/missions/task-key-conflicts?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        # NULL task_keys should not appear in conflicts
        null_conflicts = [c for c in data["conflicts"] if c["task_key"] is None]
        assert len(null_conflicts) == 0

    def test_empty_string_task_key_not_conflict(self, app_and_client):
        client, engine = app_and_client
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Empty 1', 'TensorPlatform', 'running', '',
                        :now, 1)
            """), {"now": now})
            conn.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, task_key, created_at, created_by)
                VALUES (1, 'Empty 2', 'TensorPlatform', 'queued', '',
                        :now, 1)
            """), {"now": now})

        resp = client.get("/api/v1/tensor/missions/task-key-conflicts?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        empty_conflicts = [c for c in data["conflicts"] if c["task_key"] == ""]
        assert len(empty_conflicts) == 0, "Empty string task_key should not conflict"
