"""FEAT-BOARD-CLAIM 红队矩阵（PostgreSQL；无 DSN 自动 skip）。

为什么必须是 PostgreSQL
------------------------
本端点的正确性靠数据库级裁判而不是进程内锁：``anchor_leases`` 上的部分唯一索引
``(anchor_slug) WHERE released_at IS NULL`` + ``ON CONFLICT DO NOTHING`` 决定并发
抢同一锚的唯一赢家；SQLite 既没有 partial unique + ON CONFLICT 的同款语义，也没
有真并发写，跑它等于自欺。

起一个隔离库（不碰生产库）：:

    runuser -u postgres -- createdb tp_board_redteam
    export BOARD_PG_DSN="postgresql+psycopg:///tp_board_redteam?host=/var/run/postgresql"
    python -m pytest backend/tests/test_board_claim_pg.py -q

覆盖（对应任务书 ②）：
  A 同锚并发 10 领 → 只成 1（其余 claimed=false，mission 行数=1）
  B 租约过期回收（把 expires_at 拨到过去 = 时钟推进；无需真等 5min）
  C 在飞单覆盖的锚不再供给（task_key / goal 两种覆盖口径）
  D 终态单 → 返修接棒（task_key -r2），租约释放后可再领
  E 端点契约（TestClient：响应形状 + mission 落库 + 会话/plan 事件）
  F 释放租约后锚回到可领集合
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
for p in (str(REPO_ROOT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

PG_DSN = os.getenv("BOARD_PG_DSN")
pytestmark = pytest.mark.skipif(
    not PG_DSN, reason="BOARD_PG_DSN not set — PostgreSQL red-team matrix skipped")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS organizations (id BIGSERIAL PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, username TEXT);
CREATE TABLE IF NOT EXISTS feature_ledger (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    domain TEXT NOT NULL,
    source TEXT NOT NULL,
    acceptance TEXT NOT NULL,
    ledger_group TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    evidence TEXT,
    mission_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS tensor_agent_sessions (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    execution_id BIGINT,
    agent_name TEXT NOT NULL DEFAULT 'tensor-executor',
    status TEXT NOT NULL DEFAULT 'active',
    budget JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS tensor_agent_session_events (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, seq)
);
CREATE TABLE IF NOT EXISTS tensor_dev_missions (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    goal TEXT NOT NULL,
    repo TEXT NOT NULL,
    base_ref TEXT,
    co_repos TEXT[],
    budget JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued',
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    runner TEXT NOT NULL DEFAULT 'native',
    origin TEXT NOT NULL DEFAULT 'owner',
    model TEXT,
    parent_mission_id BIGINT,
    claimed_by TEXT,
    claim_token UUID,
    claimed_user_id BIGINT,
    worker_role TEXT,
    task_key TEXT,
    agent_session_id UUID,
    session_id BIGINT,
    campaign_id BIGINT,
    campaign_phase TEXT,
    suspended_at TIMESTAMPTZ,
    suspend_reason TEXT,
    suspended_by BIGINT,
    budget_frozen JSONB,
    budget_used JSONB NOT NULL DEFAULT '{}'::jsonb,
    fail_stage TEXT,
    report_owed BOOLEAN NOT NULL DEFAULT false,
    worktree_recyclable BOOLEAN NOT NULL DEFAULT false,
    write_off_at TIMESTAMPTZ,
    report_id BIGINT
);
CREATE UNIQUE INDEX IF NOT EXISTS board_test_task_key_uq
    ON tensor_dev_missions (organization_id, task_key) WHERE task_key IS NOT NULL;
"""

ORG_ID = 7
TEST_USER = {"id": 1, "username": "board-redteam", "role": "ADMIN", "organization_id": None}


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    eng = create_engine(PG_DSN, future=True)
    with eng.begin() as conn:
        conn.exec_driver_sql(SCHEMA_SQL)
    return eng


@pytest.fixture()
def db(engine):
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=engine)()
    from sqlalchemy import text

    session.execute(text("DELETE FROM anchor_leases"))
    session.execute(text("DELETE FROM tensor_agent_session_events"))
    session.execute(text("DELETE FROM tensor_dev_missions"))
    session.execute(text("DELETE FROM tensor_agent_sessions"))
    session.execute(text("DELETE FROM feature_ledger"))
    session.execute(text("DELETE FROM organizations"))
    session.execute(text("INSERT INTO organizations (id, name) VALUES (:o, 'Org')"), {"o": ORG_ID})
    session.commit()
    yield session
    session.close()


def _seed_anchor(db, slug="FEAT-SYNTH-ONE", domain="SynthRepo", status="open",
                 acceptance="【判据】x == 1；ref: Synthetic Manual §1"):
    from sqlalchemy import text

    anchoring_id = db.execute(
        text("INSERT INTO feature_ledger (slug,title,domain,source,acceptance,status) "
             "VALUES (:s,:t,:d,'owner',:a,:st) RETURNING id"),
        {"s": slug, "t": f"合成锚 {slug}", "d": domain, "a": acceptance, "st": status},
    ).scalar()
    db.commit()
    return int(anchoring_id)


def _client(engine):
    """最小 FastAPI 应用：只挂 board 路由，覆盖 get_db / require_auth。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from api.deps import require_auth
    from api.tensor_board import router
    from database.config import get_db

    SessionLocal = sessionmaker(bind=engine)

    def _db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_auth] = lambda: dict(TEST_USER)
    return TestClient(app)


def test_a_ten_concurrent_claims_one_winner(engine, db):
    _seed_anchor(db)
    client = _client(engine)
    results: list[dict] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def worker(i: int) -> None:
        barrier.wait(timeout=30)
        r = client.post("/api/v1/tensor/board/claim",
                        json={"holder": f"agent-{i}", "domain": "SynthRepo"})
        with lock:
            results.append({"status": r.status_code, "body": r.json()})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert len(results) == 10, results
    winners = [r for r in results if r["status"] == 200 and r["body"].get("claimed")]
    losers = [r for r in results if r["status"] == 200 and not r["body"].get("claimed")]
    assert len(winners) == 1, [r["body"] for r in results]
    assert len(losers) == 9
    for r in losers:
        assert r["body"]["reason"] in ("no_open_anchor", "all_candidates_contended")

    from sqlalchemy import text

    n_missions = db.execute(text("SELECT count(*) FROM tensor_dev_missions")).scalar()
    n_leases = db.execute(
        text("SELECT count(*) FROM anchor_leases WHERE released_at IS NULL")).scalar()
    assert int(n_missions) == 1
    assert int(n_leases) == 1
    winner = winners[0]["body"]
    mission = db.execute(text("SELECT task_key, repo, runner, origin, status FROM tensor_dev_missions "
                              "WHERE id = :i"), {"i": winner["mission_id"]}).mappings().first()
    assert mission["task_key"] == "anchor-feat-synth-one"
    assert (mission["runner"], mission["origin"], mission["status"]) == ("native", "agent", "queued")
    # 租约回填 mission_id（占用表与在飞单双向可查）
    bound = db.execute(text("SELECT mission_id FROM anchor_leases WHERE anchor_slug = :s"),
                       {"s": "FEAT-SYNTH-ONE"}).scalar()
    assert int(bound) == winner["mission_id"]
    # plan 事件落库（记忆回路/审计面）
    ev = db.execute(text("SELECT count(*) FROM tensor_agent_session_events WHERE kind = 'plan'")).scalar()
    assert int(ev) == 1


def test_b_expired_lease_is_reclaimed(engine, db):
    _seed_anchor(db, slug="FEAT-SYNTH-EXPIRE")
    client = _client(engine)
    first = client.post("/api/v1/tensor/board/claim",
                        json={"anchor": "FEAT-SYNTH-EXPIRE", "holder": "a1"}).json()
    assert first["claimed"] is True
    # 时钟推进：把租约到期时间拨到过去（不用真等 5min）
    from sqlalchemy import text

    db.execute(text("UPDATE tensor_dev_missions SET status='failed' WHERE id = :i"),
               {"i": first["mission_id"]})
    db.execute(text("UPDATE anchor_leases SET expires_at = now() - interval '1 second'"))
    db.commit()

    second = client.post("/api/v1/tensor/board/claim",
                         json={"anchor": "FEAT-SYNTH-EXPIRE", "holder": "a2"}).json()
    assert second["claimed"] is True, second
    assert second["lease"]["reclaimed_lease_id"], second["lease"]
    reclaimed = db.execute(
        text("SELECT release_reason FROM anchor_leases WHERE id = :i"),
        {"i": second["lease"]["reclaimed_lease_id"]}).scalar()
    assert reclaimed == "expired"
    # 返修接棒：第二次落单的 task_key 带序号，不撞唯一索引
    assert second["task_key"] == "anchor-feat-synth-expire-r2"


def test_c_anchor_covered_by_live_mission_is_skipped(engine, db):
    _seed_anchor(db, slug="FEAT-SYNTH-BUSY")
    from sqlalchemy import text

    db.execute(text("INSERT INTO tensor_dev_missions (organization_id, goal, repo, status, task_key) "
                    "VALUES (:o, '【锚点=FEAT-SYNTH-BUSY】在飞', 'SynthRepo', 'queued', "
                    "'anchor-feat-synth-busy')"), {"o": ORG_ID})
    db.commit()
    client = _client(engine)
    out = client.post("/api/v1/tensor/board/claim",
                      json={"anchor": "FEAT-SYNTH-BUSY", "holder": "a1"}).json()
    assert out["claimed"] is False
    assert out["reason"] == "no_open_anchor"
    n = db.execute(text("SELECT count(*) FROM tensor_dev_missions")).scalar()
    assert int(n) == 1


def test_d_terminal_mission_allows_rework_handoff(engine, db):
    _seed_anchor(db, slug="FEAT-SYNTH-REWORK")
    client = _client(engine)
    first = client.post("/api/v1/tensor/board/claim",
                        json={"anchor": "FEAT-SYNTH-REWORK", "holder": "a1"}).json()
    assert first["claimed"] is True
    from sqlalchemy import text

    db.execute(text("UPDATE tensor_dev_missions SET status='failed', finished_at=now() "
                    "WHERE id = :i"), {"i": first["mission_id"]})
    db.commit()
    rel = client.post("/api/v1/tensor/board/leases/FEAT-SYNTH-REWORK/release",
                      json={"holder": "a1", "reason": "failed"}).json()
    assert rel["released"] == 1
    second = client.post("/api/v1/tensor/board/claim",
                         json={"anchor": "FEAT-SYNTH-REWORK", "holder": "a2"}).json()
    assert second["claimed"] is True, second
    assert second["task_key"] == "anchor-feat-synth-rework-r2"
    assert second["mission_id"] != first["mission_id"]


def test_e_release_returns_anchor_to_pool(engine, db):
    _seed_anchor(db, slug="FEAT-SYNTH-POOL")
    client = _client(engine)
    listed = client.get("/api/v1/tensor/board/anchors", params={"domain": "SynthRepo"}).json()
    assert [a["slug"] for a in listed["anchors"]] == ["FEAT-SYNTH-POOL"]
    got = client.post("/api/v1/tensor/board/claim",
                      json={"anchor": "FEAT-SYNTH-POOL", "holder": "a1"}).json()
    assert got["claimed"] is True
    assert client.get("/api/v1/tensor/board/anchors", params={"domain": "SynthRepo"}).json()["count"] == 0
    assert client.get("/api/v1/tensor/board/leases").json()["count"] == 1
    # 非持有者不能释放
    assert client.post("/api/v1/tensor/board/leases/FEAT-SYNTH-POOL/release",
                       json={"holder": "someone-else"}).json()["released"] == 0
    assert client.post("/api/v1/tensor/board/leases/FEAT-SYNTH-POOL/release",
                       json={"holder": "a1"}).json()["released"] == 1
    # 单仍在（终态后才可再接），但锚的租约占用已清空
    assert client.get("/api/v1/tensor/board/leases").json()["count"] == 0


def test_f_dry_run_does_not_write(engine, db):
    _seed_anchor(db, slug="FEAT-SYNTH-DRY")
    client = _client(engine)
    out = client.post("/api/v1/tensor/board/claim",
                      json={"anchor": "FEAT-SYNTH-DRY", "dry_run": True}).json()
    assert out["dry_run"] is True and "【锚点=FEAT-SYNTH-DRY】" in out["goal"]
    from sqlalchemy import text

    assert int(db.execute(text("SELECT count(*) FROM tensor_dev_missions")).scalar()) == 0
    assert int(db.execute(text("SELECT count(*) FROM anchor_leases")).scalar()) == 0