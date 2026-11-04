"""PR-5 red-team: SPEC §7 capability delta must be booked exactly once per
accepted mission — and the gate must be able to fail (QUALITY_BAR R1).

Covers:
  1. _declared_capability_delta probes (undeclared → None; malformed → 422)
  2. decide_approval E2E on sqlite (直调路由函数，repo idiom):
     approve → exactly one delta row; reject → zero rows;
     malformed declaration → 422 rollback, mission stays awaiting_acceptance;
     replay approve → still one row (idempotent).
  3. GET /tensor/missions/{id}/capability-delta read path.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.tensor_approvals import ApprovalDecision, _declared_capability_delta

# Module-private in-memory DB (StaticPool: one connection, schema survives).
# sqlite dialect lacks FOR UPDATE / now(): rewrite the former, register the
# latter — the production semantics under test are the delta rows, not the
# row locks.
ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    future=True,
)


@event.listens_for(ENGINE, "connect")
def _register_sqlite_funcs(dbapi_conn, record):
    from datetime import datetime, timezone

    dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())


class _PgDialectShim:
    """Session proxy: strips FOR UPDATE from TextClause SQL before it reaches
    the sqlite dialect (event-based rewrite proved unreliable on this path).
    Row-lock semantics are postgres-only and not what these tests assert."""

    def __init__(self, session):
        self._session = session

    def execute(self, stmt, params=None):
        st = getattr(stmt, "text", None)
        if st and "FOR UPDATE" in st:
            stmt = text(st.replace(" FOR UPDATE OF a", "").replace(" FOR UPDATE", ""))
        return (
            self._session.execute(stmt, params)
            if params is not None
            else self._session.execute(stmt)
        )

    def __getattr__(self, name):
        return getattr(self._session, name)


SCHEMA = [
    "CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, organization_id INTEGER)",
    "CREATE TABLE tensor_scenarios (id INTEGER PRIMARY KEY, organization_id INTEGER, scenario_key TEXT)",
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
        created_at TEXT,
        heartbeat_at TEXT,
        finished_at TEXT
    )""",
    """CREATE TABLE tensor_approval_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        scenario_id INTEGER,
        mission_id INTEGER,
        kind TEXT,
        from_stage TEXT,
        to_stage TEXT,
        reason TEXT,
        evidence TEXT,
        status TEXT,
        requested_by TEXT,
        decided_by TEXT,
        decided_at TEXT,
        decision_note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE tensor_capability_deltas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        mission_id INTEGER UNIQUE,
        category TEXT NOT NULL,
        summary TEXT NOT NULL,
        reason TEXT,
        artifacts TEXT DEFAULT '[]',
        evidence TEXT DEFAULT '{}',
        card_id INTEGER,
        reviewer TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
]


@pytest.fixture
def db():
    with ENGINE.begin() as conn:
        for ddl in SCHEMA:
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO organizations (id, name) VALUES (1, 'TestOrg')"))
        conn.execute(
            text(
                "INSERT INTO users (id, username, role, organization_id) VALUES (9, 'decider', 'ADMIN', 1)"
            )
        )
    session = sessionmaker(bind=ENGINE, future=True)()
    yield session
    session.close()
    with ENGINE.begin() as conn:
        conn.execute(text("DROP TABLE tensor_capability_deltas"))
        conn.execute(text("DROP TABLE tensor_approval_requests"))
        conn.execute(text("DROP TABLE tensor_dev_missions"))
        conn.execute(text("DROP TABLE tensor_scenarios"))
        conn.execute(text("DROP TABLE users"))
        conn.execute(text("DROP TABLE organizations"))


ADMIN = {"id": 9, "username": "decider", "role": "ADMIN", "organization_id": 1}


def _seed_mission(session, mid: int, result: dict | str | None) -> None:
    blob = result if isinstance(result, str) else json.dumps(result or {})
    session.execute(
        text("""
        INSERT INTO tensor_dev_missions (id, organization_id, goal, repo, status, result)
        VALUES (:mid, 1, 'g', 'TensorPlatform', 'awaiting_acceptance', :res)
    """),
        {"mid": mid, "res": blob},
    )
    session.execute(
        text("""
        INSERT INTO tensor_approval_requests (id, organization_id, mission_id, kind, status)
        VALUES (:aid, 1, :mid, 'dev_acceptance', 'pending')
    """),
        {"aid": mid * 10, "mid": mid},
    )
    session.commit()


def _decide(session, aid: int, decision: str = "approved"):
    from api import tensor_approvals

    return tensor_approvals.decide_approval(
        aid,
        ApprovalDecision(decision=decision, note="t"),
        db=_PgDialectShim(session),
        user=ADMIN,
    )


# ────────────────── declaration probes ──────────────────


class TestDeclaredDelta:
    def test_undeclared_returns_none(self):
        assert _declared_capability_delta({"headline": "x"}) is None
        assert _declared_capability_delta({}) is None
        assert _declared_capability_delta(None) is None

    def test_unparseable_legacy_result_is_undeclared(self):
        assert _declared_capability_delta("not-json{") is None

    def test_valid_declaration_roundtrip(self):
        out = _declared_capability_delta(
            {
                "capability_delta": {
                    "category": "defect_fix",
                    "summary": "修 kkr 断言",
                    "artifacts": ["tests/test_kkr.py"],
                    "evidence": {"pr": 116},
                }
            }
        )
        assert out["category"] == "defect_fix" and out["card_id"] is None

    def test_bad_category_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            _declared_capability_delta(
                {"capability_delta": {"category": "magic", "summary": "s"}}
            )
        assert e.value.status_code == 422

    def test_missing_summary_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            _declared_capability_delta(
                {"capability_delta": {"category": "new_capability"}}
            )
        assert e.value.status_code == 422

    def test_no_substantive_delta_requires_reason(self):
        with pytest.raises(HTTPException) as e:
            _declared_capability_delta(
                {
                    "capability_delta": {
                        "category": "no_substantive_delta",
                        "summary": "重复单",
                    }
                }
            )
        assert e.value.status_code == 422
        ok = _declared_capability_delta(
            {
                "capability_delta": {
                    "category": "no_substantive_delta",
                    "summary": "重复单",
                    "reason": "与 m3312 同目标",
                }
            }
        )
        assert ok["category"] == "no_substantive_delta"


# ────────────────── decide hook E2E ──────────────────


class TestDecideHooks:
    def test_approve_books_exactly_one_declared_row(self, db, monkeypatch):
        from api import tensor_approvals

        monkeypatch.setattr(tensor_approvals, "resolved_org", lambda *a, **k: 1)
        _seed_mission(
            db,
            1,
            {
                "capability_delta": {
                    "category": "new_capability",
                    "summary": "CAD 非对称船体布尔",
                }
            },
        )
        out = _decide(db, 10)
        assert out["mission_status"] == "succeeded"
        rows = (
            db.execute(
                text("SELECT category, summary, reviewer FROM tensor_capability_deltas")
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        assert rows[0]["category"] == "new_capability"
        assert rows[0]["reviewer"] == "decider"

    def test_approve_without_declaration_books_honest_default(self, db, monkeypatch):
        from api import tensor_approvals

        monkeypatch.setattr(tensor_approvals, "resolved_org", lambda *a, **k: 1)
        _seed_mission(db, 2, {"headline": "no delta declared"})
        _decide(db, 20)
        row = (
            db.execute(text("SELECT category, reason FROM tensor_capability_deltas"))
            .mappings()
            .first()
        )
        assert row["category"] == "no_substantive_delta"
        assert row["reason"]  # must say why, never fabricate

    def test_reject_books_no_row(self, db, monkeypatch):
        from api import tensor_approvals

        monkeypatch.setattr(tensor_approvals, "resolved_org", lambda *a, **k: 1)
        _seed_mission(db, 3, {})
        out = _decide(db, 30, decision="rejected")
        assert out["mission_status"] == "failed"
        assert (
            db.execute(text("SELECT count(*) FROM tensor_capability_deltas")).scalar()
            == 0
        )

    def test_malformed_declaration_blocks_the_decision(self, db, monkeypatch):
        from api import tensor_approvals

        monkeypatch.setattr(tensor_approvals, "resolved_org", lambda *a, **k: 1)
        _seed_mission(db, 4, {"capability_delta": {"category": "magic"}})
        with pytest.raises(HTTPException) as e:
            _decide(db, 40)
        assert e.value.status_code == 422
        db.rollback()
        # decision rolled back: approval still pending, mission still awaiting
        assert (
            db.execute(
                text("SELECT status FROM tensor_approval_requests WHERE id=40")
            ).scalar()
            == "pending"
        )
        assert (
            db.execute(
                text("SELECT status FROM tensor_dev_missions WHERE id=4")
            ).scalar()
            == "awaiting_acceptance"
        )

    def test_replayed_approve_stays_one_row(self, db, monkeypatch):
        from api import tensor_approvals

        monkeypatch.setattr(tensor_approvals, "resolved_org", lambda *a, **k: 1)
        _seed_mission(
            db,
            5,
            {"capability_delta": {"category": "reusable_knowledge", "summary": "s"}},
        )
        _decide(db, 50)
        with pytest.raises(
            HTTPException
        ) as e:  # replayed decide → 409, not a second row
            _decide(db, 50)
        assert e.value.status_code == 409
        assert (
            db.execute(text("SELECT count(*) FROM tensor_capability_deltas")).scalar()
            == 1
        )

    def test_preexisting_row_survives_approve(self, db, monkeypatch):
        # ON CONFLICT DO NOTHING: a row booked by an earlier (crashed) decide
        # is preserved verbatim, never overwritten or duplicated.
        from api import tensor_approvals

        monkeypatch.setattr(tensor_approvals, "resolved_org", lambda *a, **k: 1)
        _seed_mission(db, 8, {})
        db.execute(
            text("""
            INSERT INTO tensor_capability_deltas
                (organization_id, mission_id, category, summary, reason, reviewer)
            VALUES (1, 8, 'defect_fix', '早先已入账', 'crash-replay guard', 'decider')
        """)
        )
        db.commit()
        _decide(db, 80)
        rows = (
            db.execute(
                text(
                    "SELECT category, summary FROM tensor_capability_deltas WHERE mission_id=8"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1 and rows[0]["summary"] == "早先已入账"


# ────────────────── read endpoint ──────────────────


class TestReadEndpoint:
    def test_get_returns_booked_delta(self, db, monkeypatch):
        from api import tensor_approvals, tensor_missions

        monkeypatch.setattr(tensor_approvals, "resolved_org", lambda *a, **k: 1)
        monkeypatch.setattr(tensor_missions, "resolved_org", lambda *a, **k: 1)
        _seed_mission(
            db,
            6,
            {
                "capability_delta": {
                    "category": "defect_fix",
                    "summary": "s",
                    "artifacts": ["a.py"],
                    "evidence": {"ci": "green"},
                }
            },
        )
        _decide(db, 60)
        out = tensor_missions.get_capability_delta(6, db=db, user=ADMIN)
        assert out["category"] == "defect_fix"
        assert out["artifacts"] == ["a.py"] and out["evidence"] == {"ci": "green"}

    def test_get_404_when_not_booked(self, db, monkeypatch):
        from api import tensor_missions

        monkeypatch.setattr(tensor_missions, "resolved_org", lambda *a, **k: 1)
        _seed_mission(db, 7, {})
        with pytest.raises(HTTPException) as e:
            tensor_missions.get_capability_delta(7, db=db, user=ADMIN)
        assert e.value.status_code == 404
