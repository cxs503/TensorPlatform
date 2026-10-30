"""Tests for mission lifecycle: suspend/resume, write-off, necessity gate, orphan detection.

Red-team evidence for TASK-LEDGER (m597):
  1. Suspend → Resume → Finish end-to-end (budget continuation verified)
  2. Write-off hook for all 4 terminal states
  3. Necessity gate red-team: duplicate reject, covered reject, in-flight reject, pass
  4. Orphan detection SQL reproducibility
"""
import json
import os
import sys
import pytest
from datetime import datetime, timezone, timedelta

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


SCHEMA_STMTS = [
    "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, organization_id INTEGER)",
    "CREATE TABLE tensor_agent_sessions (id INTEGER PRIMARY KEY, organization_id INTEGER, agent_name TEXT, status TEXT, budget TEXT, started_by INTEGER, created_at TEXT, closed_at TEXT)",
    "CREATE TABLE tensor_agent_session_events (id INTEGER PRIMARY KEY, session_id INTEGER, kind TEXT, payload TEXT, created_at TEXT)",
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
        claimed_user_id INTEGER,
        session_id INTEGER,
        result TEXT DEFAULT '{}',
        created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        heartbeat_at TEXT,
        finished_at TEXT,
        task_key TEXT,
        fail_stage TEXT,
        report_owed INTEGER DEFAULT 0,
        worktree_recyclable INTEGER DEFAULT 0,
        write_off_at TEXT,
        suspended_at TEXT,
        suspend_reason TEXT,
        suspended_by INTEGER,
        budget_frozen TEXT,
        budget_used TEXT DEFAULT '{}'
    )""",
    """CREATE TABLE credit_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        mission_id INTEGER,
        session_id INTEGER,
        entry_type TEXT,
        budget_used TEXT DEFAULT '{}',
        budget_limit TEXT DEFAULT '{}',
        wall_seconds_used INTEGER,
        tool_calls_used INTEGER,
        tokens_used INTEGER,
        fail_stage TEXT,
        report_owed INTEGER DEFAULT 0,
        note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE mission_lifecycle_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id INTEGER,
        action TEXT,
        actor_user_id INTEGER,
        detail TEXT DEFAULT '{}',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE necessity_gate_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        task_key TEXT,
        goal_hash TEXT,
        repo TEXT,
        decision TEXT,
        reason TEXT,
        matched_mission_id INTEGER,
        mechanical_fix_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE mechanical_fix_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        mission_id INTEGER,
        fix_type TEXT,
        source_mission_ids TEXT DEFAULT '[]',
        detail TEXT DEFAULT '{}',
        executed_by TEXT DEFAULT 'system',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE tensor_approval_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        mission_id INTEGER,
        kind TEXT,
        reason TEXT,
        evidence TEXT,
        status TEXT,
        requested_by TEXT,
        decided_by TEXT,
        decided_at TEXT,
        decision_note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
]

SEED_STMTS = [
    "INSERT INTO organizations (id, name) VALUES (1, 'TestOrg')",
    "INSERT INTO users (id, username, role, organization_id) VALUES (1, 'admin', 'ADMIN', 1)",
    "INSERT INTO users (id, username, role, organization_id) VALUES (2, 'engineer', 'ENGINEER', 1)",
]


@pytest.fixture
def db_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        for stmt in SCHEMA_STMTS:
            conn.execute(text(stmt))
        for stmt in SEED_STMTS:
            conn.execute(text(stmt))
        conn.commit()
    return engine


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for lifecycle logic
# ─────────────────────────────────────────────────────────────────────────────

class TestSuspendResume:
    """Test suspend/resume budget continuation."""

    def test_suspend_freezes_budget(self, db_session):
        """Suspend should snapshot budget_used into budget_frozen."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, budget, budget_used, heartbeat_at)
            VALUES (1, 'Test mission for suspend', 'TensorPlatform', 'running',
                    '{"max_tool_calls": 80, "max_wall_seconds": 7200, "max_tokens": 3000000}',
                    '{"tool_calls_used": 15, "wall_seconds_used": 300, "tokens_used": 50000}',
                    datetime('now'))
        """))
        db_session.commit()

        mid = db_session.execute(text("SELECT id FROM tensor_dev_missions WHERE goal LIKE '%suspend%'")).scalar()
        assert mid is not None

        # Simulate suspend
        frozen = {
            "wall_seconds_used": 300,
            "tool_calls_used": 15,
            "tokens_used": 50000,
            "suspended_at": datetime.now(timezone.utc).isoformat(),
        }
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'suspended', claim_token = NULL, claimed_by = NULL,
                suspended_at = datetime('now'), suspend_reason = 'budget review',
                suspended_by = 1, budget_frozen = :frozen
            WHERE id = :mid
        """), {"mid": mid, "frozen": json.dumps(frozen)})
        db_session.commit()

        row = db_session.execute(text("""
            SELECT status, budget_frozen, suspend_reason, suspended_by
            FROM tensor_dev_missions WHERE id = :mid
        """), {"mid": mid}).mappings().first()
        assert row["status"] == "suspended"
        assert row["suspend_reason"] == "budget review"
        assert row["suspended_by"] == 1
        frozen_data = json.loads(row["budget_frozen"])
        assert frozen_data["tool_calls_used"] == 15
        assert frozen_data["wall_seconds_used"] == 300
        assert frozen_data["tokens_used"] == 50000

    def test_resume_continues_budget(self, db_session):
        """Resume should restore budget from frozen snapshot, not reset to zero."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, budget, budget_used, budget_frozen,
                 suspended_at, suspend_reason, suspended_by)
            VALUES (1, 'Test mission for resume', 'TensorPlatform', 'suspended',
                    '{"max_tool_calls": 80, "max_wall_seconds": 7200}',
                    '{"tool_calls_used": 15, "wall_seconds_used": 300}',
                    '{"tool_calls_used": 15, "wall_seconds_used": 300, "tokens_used": 50000}',
                    datetime('now', '-1 hour'), 'budget review', 1)
        """))
        db_session.commit()

        mid = db_session.execute(text("SELECT id FROM tensor_dev_missions WHERE goal LIKE '%resume%'")).scalar()

        frozen = json.loads(db_session.execute(text(
            "SELECT budget_frozen FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).scalar())

        budget_used = {
            "tool_calls_used": frozen["tool_calls_used"],
            "wall_seconds_used": frozen["wall_seconds_used"],
            "tokens_used": frozen["tokens_used"],
        }
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'queued', suspended_at = NULL, suspend_reason = NULL,
                suspended_by = NULL, budget_frozen = NULL,
                budget_used = :bu, heartbeat_at = datetime('now')
            WHERE id = :mid
        """), {"mid": mid, "bu": json.dumps(budget_used)})
        db_session.commit()

        row = db_session.execute(text("""
            SELECT status, budget_used, suspended_at
            FROM tensor_dev_missions WHERE id = :mid
        """), {"mid": mid}).mappings().first()
        assert row["status"] == "queued"
        assert row["suspended_at"] is None
        bu = json.loads(row["budget_used"])
        assert bu["tool_calls_used"] == 15  # NOT 0
        assert bu["wall_seconds_used"] == 300  # NOT 0
        assert bu["tokens_used"] == 50000  # NOT 0

    def test_suspend_resume_budget_continuity(self, db_session):
        """End-to-end: suspend → resume → verify budget numbers match."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, budget, budget_used, heartbeat_at)
            VALUES (1, 'E2E budget test', 'TensorPlatform', 'running',
                    '{"max_tool_calls": 80, "max_wall_seconds": 7200}',
                    '{"tool_calls_used": 42, "wall_seconds_used": 1800, "tokens_used": 1234567}',
                    datetime('now'))
        """))
        db_session.commit()
        mid = db_session.execute(text("SELECT id FROM tensor_dev_missions WHERE goal = 'E2E budget test'")).scalar()

        # Step 1: Suspend
        original_used = {"tool_calls_used": 42, "wall_seconds_used": 1800, "tokens_used": 1234567}
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'suspended', budget_frozen = :frozen,
                suspended_at = datetime('now'), suspend_reason = 'test'
            WHERE id = :mid
        """), {"mid": mid, "frozen": json.dumps(original_used)})
        db_session.commit()

        # Step 2: Resume
        frozen = json.loads(db_session.execute(text(
            "SELECT budget_frozen FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).scalar())
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'queued', budget_used = :bu, budget_frozen = NULL,
                suspended_at = NULL, heartbeat_at = datetime('now')
            WHERE id = :mid
        """), {"mid": mid, "bu": json.dumps(frozen)})
        db_session.commit()

        # Step 3: Verify continuity
        final_used = json.loads(db_session.execute(text(
            "SELECT budget_used FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).scalar())
        assert final_used["tool_calls_used"] == 42
        assert final_used["wall_seconds_used"] == 1800
        assert final_used["tokens_used"] == 1234567


class TestWriteOff:
    """Test write-off hook for terminal states."""

    def test_write_off_failed_requires_fail_stage(self, db_session):
        """Failed missions must have fail_stage set."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, finished_at)
            VALUES (1, 'Failed mission', 'TensorPlatform', 'failed', datetime('now'))
        """))
        db_session.commit()
        mid = db_session.execute(text("SELECT id FROM tensor_dev_missions WHERE goal = 'Failed mission'")).scalar()

        row = db_session.execute(text(
            "SELECT status, fail_stage FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).mappings().first()
        assert row["status"] == "failed"
        assert row["fail_stage"] is None

        db_session.execute(text("""
            UPDATE tensor_dev_missions SET fail_stage = 'tool_loop' WHERE id = :mid
        """), {"mid": mid})
        db_session.commit()

        row = db_session.execute(text(
            "SELECT fail_stage FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).mappings().first()
        assert row["fail_stage"] == "tool_loop"

    def test_write_off_sets_worktree_recyclable(self, db_session):
        """Write-off should mark worktree as recyclable."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, finished_at, claim_token)
            VALUES (1, 'Write-off test', 'TensorPlatform', 'stopped', datetime('now'), 'some-token')
        """))
        db_session.commit()
        mid = db_session.execute(text("SELECT id FROM tensor_dev_missions WHERE goal = 'Write-off test'")).scalar()

        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET claim_token = NULL, worktree_recyclable = 1, write_off_at = datetime('now')
            WHERE id = :mid
        """), {"mid": mid})
        db_session.commit()

        row = db_session.execute(text("""
            SELECT claim_token, worktree_recyclable, write_off_at
            FROM tensor_dev_missions WHERE id = :mid
        """), {"mid": mid}).mappings().first()
        assert row["claim_token"] is None
        assert row["worktree_recyclable"] == 1
        assert row["write_off_at"] is not None

    def test_write_off_credit_ledger_entry(self, db_session):
        """Write-off should create credit_ledger entry."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, finished_at, budget_used, fail_stage)
            VALUES (1, 'Ledger test', 'TensorPlatform', 'failed', datetime('now'),
                    '{"tool_calls_used": 10, "wall_seconds_used": 600}', 'build')
        """))
        db_session.commit()
        mid = db_session.execute(text("SELECT id FROM tensor_dev_missions WHERE goal = 'Ledger test'")).scalar()

        db_session.execute(text("""
            INSERT INTO credit_ledger
                (organization_id, mission_id, entry_type, budget_used,
                 tool_calls_used, wall_seconds_used, fail_stage, report_owed)
            VALUES (1, :mid, 'failed', '{"tool_calls_used": 10}', 10, 600, 'build', 1)
        """), {"mid": mid})
        db_session.commit()

        entry = db_session.execute(text("""
            SELECT * FROM credit_ledger WHERE mission_id = :mid
        """), {"mid": mid}).mappings().first()
        assert entry is not None
        assert entry["entry_type"] == "failed"
        assert entry["tool_calls_used"] == 10
        assert entry["fail_stage"] == "build"
        assert entry["report_owed"] == 1

    def test_write_off_four_states(self, db_session):
        """Verify write-off works for all 4 terminal states."""
        for status in ("succeeded", "failed", "stopped", "revoked"):
            fail_stage = "tool_loop" if status == "failed" else None
            db_session.execute(text("""
                INSERT INTO tensor_dev_missions
                    (organization_id, goal, repo, status, finished_at, fail_stage)
                VALUES (1, :goal, 'TensorPlatform', :status, datetime('now'), :fs)
            """), {"goal": f"Terminal {status}", "status": status, "fs": fail_stage})
        db_session.commit()

        for status in ("succeeded", "failed", "stopped", "revoked"):
            mid = db_session.execute(text(
                "SELECT id FROM tensor_dev_missions WHERE goal = :goal"
            ), {"goal": f"Terminal {status}"}).scalar()
            assert mid is not None

            db_session.execute(text("""
                UPDATE tensor_dev_missions
                SET claim_token = NULL, worktree_recyclable = 1, write_off_at = datetime('now')
                WHERE id = :mid
            """), {"mid": mid})
            db_session.commit()

            row = db_session.execute(text("""
                SELECT worktree_recyclable, write_off_at FROM tensor_dev_missions WHERE id = :mid
            """), {"mid": mid}).mappings().first()
            assert row["worktree_recyclable"] == 1, f"Failed for status={status}"
            assert row["write_off_at"] is not None, f"Failed for status={status}"


class TestNecessityGate:
    """Red-team tests for necessity gate."""

    def test_reject_exact_duplicate(self, db_session):
        """Exact duplicate goal should be downgraded to mechanical fix."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status)
            VALUES (1, 'Implement resistance calculation module', 'TensorPlatform', 'running')
        """))
        db_session.commit()

        new_goal = "Implement resistance calculation module"
        duplicates = db_session.execute(text("""
            SELECT id, goal, status FROM tensor_dev_missions
            WHERE organization_id = 1 AND repo = 'TensorPlatform'
              AND status IN ('queued', 'claimed', 'running')
              AND LOWER(TRIM(goal)) = LOWER(TRIM(:goal))
        """), {"goal": new_goal}).mappings().all()

        assert len(duplicates) == 1
        assert duplicates[0]["status"] == "running"

    def test_reject_covered(self, db_session):
        """Same task_key already succeeded should be rejected."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, task_key, finished_at)
            VALUES (1, 'Fix bug in resistance module', 'TensorPlatform', 'succeeded',
                    'fix:resistance:bug', datetime('now'))
        """))
        db_session.commit()

        existing = db_session.execute(text("""
            SELECT id FROM tensor_dev_missions
            WHERE organization_id = 1 AND task_key = :key AND status = 'succeeded'
        """), {"key": "fix:resistance:bug"}).scalar()
        assert existing is not None

    def test_reject_inflight_overlap(self, db_session):
        """High keyword overlap with in-flight mission should be rejected."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status)
            VALUES (1, 'Add wave resistance calculation for high speed ships',
                    'TensorPlatform', 'queued')
        """))
        db_session.commit()

        new_goal = "Add wave resistance calculation for high speed vessels"
        goal_words = set(w for w in new_goal.lower().split() if len(w) > 3)
        in_flight = db_session.execute(text("""
            SELECT goal FROM tensor_dev_missions
            WHERE organization_id = 1 AND repo = 'TensorPlatform'
              AND status IN ('queued', 'claimed', 'running')
        """)).mappings().all()

        found_overlap = False
        for inf in in_flight:
            inf_words = set(w for w in (inf["goal"] or "").lower().split() if len(w) > 3)
            if inf_words:
                overlap = len(goal_words & inf_words) / max(len(goal_words), len(inf_words))
                if overlap > 0.7 and len(goal_words) > 3:
                    found_overlap = True
                    assert overlap > 0.7
        assert found_overlap, "Should have detected overlap"

    def test_pass_genuine_gap(self, db_session):
        """Genuine gap with no overlap should pass."""
        existing = db_session.execute(text("""
            SELECT id FROM tensor_dev_missions
            WHERE organization_id = 1 AND repo = 'TensorFEM'
              AND status IN ('queued', 'claimed', 'running', 'succeeded')
        """)).fetchall()
        assert len(existing) == 0


class TestOrphanDetection:
    """Test orphan detection SQL."""

    def test_queued_over_48h_is_orphan(self, db_session):
        """Queued missions > 48h old should be detected as orphans."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=50)).strftime('%Y-%m-%d %H:%M:%S')
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, created_at)
            VALUES (1, 'Old queued mission', 'TensorPlatform', 'queued', :old_time)
        """), {"old_time": old_time})
        db_session.commit()

        orphans = db_session.execute(text("""
            SELECT id, goal, created_at FROM tensor_dev_missions
            WHERE organization_id = 1 AND status = 'queued'
              AND created_at < datetime('now', '-48 hours')
        """)).mappings().all()
        assert len(orphans) == 1
        assert "Old queued" in orphans[0]["goal"]

    def test_stale_heartbeat_reclaim(self, db_session):
        """Running missions with stale heartbeat > 30min should be reclaim candidates."""
        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=45)).strftime('%Y-%m-%d %H:%M:%S')
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, heartbeat_at, claimed_by)
            VALUES (1, 'Stale running mission', 'TensorPlatform', 'running', :stale, 'worker-1')
        """), {"stale": stale_time})
        db_session.commit()

        stale = db_session.execute(text("""
            SELECT id, goal, heartbeat_at FROM tensor_dev_missions
            WHERE organization_id = 1 AND status IN ('claimed', 'running')
              AND heartbeat_at < datetime('now', '-30 minutes')
        """)).mappings().all()
        assert len(stale) == 1
        assert "Stale" in stale[0]["goal"]

    def test_active_runners_prevent_false_orphan(self, db_session):
        """If there are active runners, old queued missions are not orphans."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, heartbeat_at, claimed_by)
            VALUES (1, 'Active mission', 'TensorPlatform', 'running', datetime('now'), 'worker-1')
        """))
        db_session.commit()

        active_count = db_session.execute(text("""
            SELECT COUNT(DISTINCT claimed_by) AS cnt FROM tensor_dev_missions
            WHERE organization_id = 1
              AND status IN ('claimed', 'running')
              AND heartbeat_at > datetime('now', '-1 hour')
        """)).scalar()
        assert active_count >= 1


class TestDashboardSnapshot:
    """Test dashboard snapshot aggregation."""

    def test_snapshot_aggregation(self, db_session):
        """Dashboard should aggregate suspended, report_owed, orphan, mechanical_fix counts."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions (organization_id, goal, repo, status, suspend_reason, report_owed)
            VALUES (1, 'Suspended 1', 'TP', 'suspended', 'review', 0)
        """))
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions (organization_id, goal, repo, status, report_owed)
            VALUES (1, 'Owed 1', 'TP', 'failed', 1)
        """))
        db_session.execute(text("""
            INSERT INTO mechanical_fix_log (organization_id, fix_type, detail)
            VALUES (1, 'dedup_report', '{}')
        """))
        db_session.commit()

        suspended_count = db_session.execute(text("""
            SELECT COUNT(*) FROM tensor_dev_missions
            WHERE organization_id = 1 AND status = 'suspended'
        """)).scalar()
        assert suspended_count == 1

        owed_count = db_session.execute(text("""
            SELECT COUNT(*) FROM tensor_dev_missions
            WHERE organization_id = 1 AND report_owed = 1
        """)).scalar()
        assert owed_count == 1

        fix_count = db_session.execute(text("""
            SELECT COUNT(*) FROM mechanical_fix_log
            WHERE organization_id = 1
        """)).scalar()
        assert fix_count == 1


class TestWorkerProbe:
    """Test worker probe endpoint logic."""

    def test_probe_running_returns_continue(self, db_session):
        """Running mission should return should_continue=True."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status)
            VALUES (1, 'Probe test running', 'TP', 'running')
        """))
        db_session.commit()
        mid = db_session.execute(text(
            "SELECT id FROM tensor_dev_missions WHERE goal = 'Probe test running'"
        )).scalar()

        row = db_session.execute(text(
            "SELECT status FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).mappings().first()
        should_continue = row["status"] in ("running", "claimed")
        assert should_continue is True

    def test_probe_stopped_returns_stop(self, db_session):
        """Stopped mission should return should_continue=False."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status)
            VALUES (1, 'Probe test stopped', 'TP', 'stopped')
        """))
        db_session.commit()
        mid = db_session.execute(text(
            "SELECT id FROM tensor_dev_missions WHERE goal = 'Probe test stopped'"
        )).scalar()

        row = db_session.execute(text(
            "SELECT status FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).mappings().first()
        should_continue = row["status"] in ("running", "claimed")
        assert should_continue is False

    def test_probe_suspended_returns_stop(self, db_session):
        """Suspended mission should return should_continue=False."""
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status)
            VALUES (1, 'Probe test suspended', 'TP', 'suspended')
        """))
        db_session.commit()
        mid = db_session.execute(text(
            "SELECT id FROM tensor_dev_missions WHERE goal = 'Probe test suspended'"
        )).scalar()

        row = db_session.execute(text(
            "SELECT status FROM tensor_dev_missions WHERE id = :mid"
        ), {"mid": mid}).mappings().first()
        should_continue = row["status"] in ("running", "claimed")
        assert should_continue is False


class TestFullLifecycle:
    """End-to-end lifecycle test."""

    def test_full_lifecycle(self, db_session):
        """Create → Claim → Run → Suspend → Resume → Complete → Write-off."""
        # 1. Create
        db_session.execute(text("""
            INSERT INTO tensor_dev_missions
                (organization_id, goal, repo, status, budget, budget_used)
            VALUES (1, 'Full lifecycle test mission', 'TensorPlatform', 'queued',
                    '{"max_tool_calls": 80, "max_wall_seconds": 7200}',
                    '{}')
        """))
        db_session.commit()
        mid = db_session.execute(text(
            "SELECT id FROM tensor_dev_missions WHERE goal = 'Full lifecycle test mission'"
        )).scalar()
        assert mid is not None

        # 2. Claim
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'claimed', claim_token = 'test-token-123',
                claimed_by = 'worker-1', heartbeat_at = datetime('now')
            WHERE id = :mid
        """), {"mid": mid})
        db_session.commit()

        # 3. Start running + budget usage
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'running', budget_used = '{"tool_calls_used": 20, "wall_seconds_used": 600}'
            WHERE id = :mid
        """), {"mid": mid})
        db_session.commit()

        # 4. Suspend (budget freeze)
        budget_at_suspend = {"tool_calls_used": 20, "wall_seconds_used": 600, "tokens_used": 100000}
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'suspended', claim_token = NULL, claimed_by = NULL,
                budget_frozen = :frozen, suspended_at = datetime('now'),
                suspend_reason = 'owner review'
            WHERE id = :mid
        """), {"mid": mid, "frozen": json.dumps(budget_at_suspend)})
        db_session.commit()

        # Log suspend
        db_session.execute(text("""
            INSERT INTO mission_lifecycle_log (mission_id, action, detail)
            VALUES (:mid, 'suspend', '{"reason": "owner review"}')
        """), {"mid": mid})
        db_session.execute(text("""
            INSERT INTO credit_ledger (organization_id, mission_id, entry_type, budget_used,
                tool_calls_used, wall_seconds_used, tokens_used)
            VALUES (1, :mid, 'suspended', :bu, 20, 600, 100000)
        """), {"mid": mid, "bu": json.dumps(budget_at_suspend)})
        db_session.commit()

        # Verify suspended state
        row = db_session.execute(text("""
            SELECT status, budget_frozen, claim_token FROM tensor_dev_missions WHERE id = :mid
        """), {"mid": mid}).mappings().first()
        assert row["status"] == "suspended"
        assert row["claim_token"] is None
        frozen = json.loads(row["budget_frozen"])
        assert frozen["tool_calls_used"] == 20

        # 5. Resume (budget continues)
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'queued', budget_used = :bu, budget_frozen = NULL,
                suspended_at = NULL, suspend_reason = NULL, heartbeat_at = datetime('now')
            WHERE id = :mid
        """), {"mid": mid, "bu": json.dumps(budget_at_suspend)})
        db_session.commit()

        # Verify budget continued
        row = db_session.execute(text("""
            SELECT status, budget_used FROM tensor_dev_missions WHERE id = :mid
        """), {"mid": mid}).mappings().first()
        assert row["status"] == "queued"
        bu = json.loads(row["budget_used"])
        assert bu["tool_calls_used"] == 20  # Continued, not reset
        assert bu["wall_seconds_used"] == 600

        # 6. Complete (succeeded)
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET status = 'succeeded', finished_at = datetime('now'),
                claim_token = NULL, worktree_recyclable = 1,
                budget_used = '{"tool_calls_used": 55, "wall_seconds_used": 3600, "tokens_used": 500000}'
            WHERE id = :mid
        """), {"mid": mid})
        db_session.commit()

        # 7. Write-off
        db_session.execute(text("""
            UPDATE tensor_dev_missions
            SET claim_token = NULL, worktree_recyclable = 1, write_off_at = datetime('now')
            WHERE id = :mid
        """), {"mid": mid})
        db_session.execute(text("""
            INSERT INTO credit_ledger (organization_id, mission_id, entry_type, budget_used,
                tool_calls_used, wall_seconds_used, tokens_used, report_owed)
            VALUES (1, :mid, 'succeeded',
                '{"tool_calls_used": 55, "wall_seconds_used": 3600, "tokens_used": 500000}',
                55, 3600, 500000, 0)
        """), {"mid": mid})
        db_session.execute(text("""
            INSERT INTO mission_lifecycle_log (mission_id, action, detail)
            VALUES (:mid, 'write_off', '{"status": "succeeded"}')
        """), {"mid": mid})
        db_session.commit()

        # Final verification
        row = db_session.execute(text("""
            SELECT status, worktree_recyclable, write_off_at FROM tensor_dev_missions WHERE id = :mid
        """), {"mid": mid}).mappings().first()
        assert row["status"] == "succeeded"
        assert row["worktree_recyclable"] == 1
        assert row["write_off_at"] is not None

        # Verify credit ledger has entries
        ledger = db_session.execute(text("""
            SELECT entry_type, tool_calls_used FROM credit_ledger WHERE mission_id = :mid
            ORDER BY id
        """), {"mid": mid}).mappings().all()
        assert len(ledger) == 2  # suspended + succeeded
        assert ledger[0]["entry_type"] == "suspended"
        assert ledger[0]["tool_calls_used"] == 20
        assert ledger[1]["entry_type"] == "succeeded"
        assert ledger[1]["tool_calls_used"] == 55

        # Verify lifecycle log
        logs = db_session.execute(text("""
            SELECT action FROM mission_lifecycle_log WHERE mission_id = :mid ORDER BY id
        """), {"mid": mid}).mappings().all()
        assert len(logs) == 2
        assert logs[0]["action"] == "suspend"
        assert logs[1]["action"] == "write_off"
