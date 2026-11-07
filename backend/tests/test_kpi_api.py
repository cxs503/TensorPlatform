"""
KPI Guide Tracker API 测试 — m5121

验证：
1. GET /api/v1/tensor/kpi/guide_tracker 返回 12 项 KPI
2. 每项 KPI 含 current / target / pct 字段
3. 数值与 DB 直接查询一致（误差=0）
4. 缓存机制正常工作

采用隔离 SQLite 内存库测试模式（同 test_capability_delta.py 范式），
避免全 app 导入依赖问题。
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── 隔离内存数据库 ─────────────────────────────────────────────────────
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
    # EXTRACT(EPOCH FROM ...) shim for avg_mission_duration
    dbapi_conn.create_function("extract_epoch", 1, lambda x: float(x) if x else 0.0)


SessionLocal = sessionmaker(bind=ENGINE)

# ── 建表 ──────────────────────────────────────────────────────────────
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS tensor_dev_missions ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  organization_id INTEGER NOT NULL DEFAULT 1,"
    "  goal TEXT NOT NULL DEFAULT '',"
    "  repo TEXT NOT NULL DEFAULT '',"
    "  base_ref TEXT DEFAULT 'main',"
    "  status TEXT NOT NULL DEFAULT 'pending',"
    "  budget TEXT,"
    "  created_at TEXT DEFAULT (now()),"
    "  completed_at TEXT,"
    "  heartbeat_at TEXT,"
    "  agent_session_id TEXT,"
    "  model TEXT,"
    "  worker_role TEXT,"
    "  campaign_id TEXT,"
    "  campaign_phase TEXT,"
    "  origin TEXT DEFAULT 'auto',"
    "  runner TEXT,"
    "  task_key TEXT,"
    "  session_id TEXT,"
    "  claimed_by TEXT,"
    "  claimed_user_id INTEGER,"
    "  result TEXT,"
    "  parent_mission_id INTEGER,"
    "  co_repos TEXT"
    ")",
    "CREATE TABLE IF NOT EXISTS tensor_reports ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  title TEXT NOT NULL DEFAULT '',"
    "  summary TEXT DEFAULT '',"
    "  scenario_key TEXT,"
    "  mission_id INTEGER,"
    "  stage TEXT,"
    "  tags TEXT,"
    "  markdown TEXT DEFAULT '',"
    "  figures TEXT,"
    "  status TEXT DEFAULT 'draft',"
    "  created_by INTEGER,"
    "  organization_id INTEGER DEFAULT 1,"
    "  created_at TEXT DEFAULT (now())"
    ")",
    "CREATE TABLE IF NOT EXISTS ship_samples ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  calc_type TEXT NOT NULL DEFAULT '',"
    "  params TEXT,"
    "  result TEXT,"
    "  error TEXT,"
    "  error_pct REAL,"
    "  passed INTEGER,"
    "  source TEXT,"
    "  tags TEXT,"
    "  created_at TEXT DEFAULT (now())"
    ")",
    "CREATE TABLE IF NOT EXISTS metered_usage ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  mission_id INTEGER NOT NULL,"
    "  agent_session_id TEXT,"
    "  tool_calls INTEGER DEFAULT 0,"
    "  tokens_used INTEGER DEFAULT 0,"
    "  wall_seconds REAL DEFAULT 0.0,"
    "  api_calls_detail TEXT,"
    "  recorded_by TEXT DEFAULT 'platform_middleware',"
    "  created_at TEXT DEFAULT (now())"
    ")",
    "CREATE TABLE IF NOT EXISTS integrity_events ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  mission_id INTEGER NOT NULL,"
    "  event_type TEXT NOT NULL DEFAULT '',"
    "  self_reported TEXT,"
    "  metered TEXT,"
    "  deviation_pct REAL,"
    "  severity TEXT DEFAULT 'warning',"
    "  resolved INTEGER DEFAULT 0,"
    "  notes TEXT,"
    "  created_at TEXT DEFAULT (now())"
    ")",
]


def _setup_db():
    """创建所有表并插入测试数据。"""
    with ENGINE.connect() as conn:
        for stmt in SCHEMA:
            conn.execute(text(stmt))
        conn.commit()

    # 插入测试数据
    with SessionLocal() as db:
        # 10 missions: 5 completed, 2 failed, 2 running, 1 claimed
        for i in range(1, 11):
            if i <= 5:
                status = "completed"
                completed_at = "2026-01-01T12:00:00"
            elif i <= 7:
                status = "failed"
                completed_at = None
            elif i == 8:
                status = "running"
                completed_at = None
            elif i == 9:
                status = "claimed"
                completed_at = None
            else:
                status = "pending"
                completed_at = None

            db.execute(text(
                "INSERT INTO tensor_dev_missions (id, goal, repo, status, created_at, completed_at) "
                "VALUES (:id, :goal, :repo, :status, '2026-01-01T00:00:00', :completed_at)"
            ), {
                "id": i,
                "goal": f"Test mission {i}",
                "repo": "TensorPlatform",
                "status": status,
                "completed_at": completed_at,
            })

        # 8 reports: 3 published, 5 draft
        for i in range(1, 9):
            status = "published" if i <= 3 else "draft"
            db.execute(text(
                "INSERT INTO tensor_reports (id, title, status) VALUES (:id, :title, :status)"
            ), {"id": i, "title": f"Report {i}", "status": status})

        # 15 samples: 10 passed, 5 failed
        for i in range(1, 16):
            passed = 1 if i <= 10 else 0
            db.execute(text(
                "INSERT INTO ship_samples (id, calc_type, passed) VALUES (:id, 'resistance', :passed)"
            ), {"id": i, "passed": passed})

        # metered_usage: total tool_calls = 12345
        for i in range(1, 6):
            db.execute(text(
                "INSERT INTO metered_usage (id, mission_id, tool_calls) VALUES (:id, :mid, :tc)"
            ), {"id": i, "mid": i, "tc": 2469})  # 5 * 2469 = 12345

        # integrity_events: 3 unresolved, 2 resolved
        for i in range(1, 6):
            resolved = 0 if i <= 3 else 1
            db.execute(text(
                "INSERT INTO integrity_events (id, mission_id, event_type, resolved) "
                "VALUES (:id, :mid, 'usage_deviation', :resolved)"
            ), {"id": i, "mid": i, "resolved": resolved})

        db.commit()


# ── 导入被测模块（在 DB 就绪后） ──────────────────────────────────────
# 需要 patch database.config 的 engine 指向我们的测试引擎
import database.config as db_config
db_config.engine = ENGINE


def _get_test_db():
    """返回测试用 DB session。"""
    return SessionLocal()


# 现在导入 KPI 模块（它会使用我们 patch 过的 engine）
from api.kpi import _compute_kpis, _get_cached, _set_cached, KPI_TARGETS, CACHE_TTL


# ── 预期 KPI 键 ──────────────────────────────────────────────────────
EXPECTED_KEYS = {
    "total_missions", "active_missions", "completed_missions", "failed_missions",
    "total_reports", "published_reports", "total_samples", "passed_samples",
    "mission_success_rate", "avg_mission_duration", "total_tool_calls", "integrity_events",
}


# ── Fixture ──────────────────────────────────────────────────────────
@pytest.fixture(autouse=True, scope="module")
def setup_test_db():
    """模块级 setup：建表 + 插入数据。"""
    _setup_db()
    yield
    # teardown: clear tables
    with ENGINE.connect() as conn:
        for table in ["integrity_events", "metered_usage", "ship_samples", "tensor_reports", "tensor_dev_missions"]:
            conn.execute(text(f"DELETE FROM {table}"))
        conn.commit()


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清除缓存。"""
    _set_cached({})
    yield


# ── 测试 1：返回 12 项 KPI ───────────────────────────────────────────
def test_kpi_returns_12_items():
    """验证返回恰好 12 项 KPI。"""
    db = _get_test_db()
    kpis = _compute_kpis(db)
    assert len(kpis) == 12, f"Expected 12 KPIs, got {len(kpis)}"
    db.close()


# ── 测试 2：每项含 current/target/pct ────────────────────────────────
def test_kpi_each_has_required_fields():
    """验证每项 KPI 含 current / target / pct 字段。"""
    db = _get_test_db()
    kpis = _compute_kpis(db)
    for kpi in kpis:
        assert "key" in kpi, f"KPI missing 'key': {kpi}"
        assert "label" in kpi, f"KPI missing 'label': {kpi}"
        assert "current" in kpi, f"KPI missing 'current': {kpi}"
        assert "target" in kpi, f"KPI missing 'target': {kpi}"
        assert "pct" in kpi, f"KPI missing 'pct': {kpi}"
        assert isinstance(kpi["current"], (int, float)), f"'current' must be numeric: {kpi}"
        assert isinstance(kpi["target"], (int, float)), f"'target' must be numeric: {kpi}"
        assert isinstance(kpi["pct"], (int, float)), f"'pct' must be numeric: {kpi}"
    db.close()


# ── 测试 3：KPI 键集合完整 ───────────────────────────────────────────
def test_kpi_keys_complete():
    """验证 12 项 KPI 的 key 集合与预期一致。"""
    db = _get_test_db()
    kpis = _compute_kpis(db)
    keys = {kpi["key"] for kpi in kpis}
    assert keys == EXPECTED_KEYS, f"Expected keys {EXPECTED_KEYS}, got {keys}"
    db.close()


# ── 测试 4：数值与 DB 直接查询一致（误差=0） ─────────────────────────
def test_kpi_values_match_db():
    """
    验证 KPI current 值与 DB 直接查询一致（误差=0）。
    """
    db = _get_test_db()
    kpis = _compute_kpis(db)
    kpi_map = {kpi["key"]: kpi for kpi in kpis}

    # total_missions
    r = db.execute(text("SELECT COUNT(*) FROM tensor_dev_missions")).scalar()
    assert kpi_map["total_missions"]["current"] == int(r), \
        f"total_missions: API={kpi_map['total_missions']['current']}, DB={int(r)}"

    # active_missions
    r = db.execute(text("SELECT COUNT(*) FROM tensor_dev_missions WHERE status IN ('claimed', 'running')")).scalar()
    assert kpi_map["active_missions"]["current"] == int(r), \
        f"active_missions: API={kpi_map['active_missions']['current']}, DB={int(r)}"

    # completed_missions
    r = db.execute(text("SELECT COUNT(*) FROM tensor_dev_missions WHERE status = 'completed'")).scalar()
    assert kpi_map["completed_missions"]["current"] == int(r), \
        f"completed_missions: API={kpi_map['completed_missions']['current']}, DB={int(r)}"

    # failed_missions
    r = db.execute(text("SELECT COUNT(*) FROM tensor_dev_missions WHERE status = 'failed'")).scalar()
    assert kpi_map["failed_missions"]["current"] == int(r), \
        f"failed_missions: API={kpi_map['failed_missions']['current']}, DB={int(r)}"

    # total_reports
    r = db.execute(text("SELECT COUNT(*) FROM tensor_reports")).scalar()
    assert kpi_map["total_reports"]["current"] == int(r), \
        f"total_reports: API={kpi_map['total_reports']['current']}, DB={int(r)}"

    # published_reports
    r = db.execute(text("SELECT COUNT(*) FROM tensor_reports WHERE status = 'published'")).scalar()
    assert kpi_map["published_reports"]["current"] == int(r), \
        f"published_reports: API={kpi_map['published_reports']['current']}, DB={int(r)}"

    # total_samples
    r = db.execute(text("SELECT COUNT(*) FROM ship_samples")).scalar()
    assert kpi_map["total_samples"]["current"] == int(r), \
        f"total_samples: API={kpi_map['total_samples']['current']}, DB={int(r)}"

    # passed_samples
    r = db.execute(text("SELECT COUNT(*) FROM ship_samples WHERE passed = 1")).scalar()
    assert kpi_map["passed_samples"]["current"] == int(r), \
        f"passed_samples: API={kpi_map['passed_samples']['current']}, DB={int(r)}"

    # mission_success_rate
    completed = int(db.execute(text("SELECT COUNT(*) FROM tensor_dev_missions WHERE status = 'completed'")).scalar())
    failed = int(db.execute(text("SELECT COUNT(*) FROM tensor_dev_missions WHERE status = 'failed'")).scalar())
    terminal = completed + failed
    if terminal > 0:
        db_rate = round(completed / terminal * 100, 2)
    else:
        db_rate = 0.0
    assert kpi_map["mission_success_rate"]["current"] == db_rate, \
        f"mission_success_rate: API={kpi_map['mission_success_rate']['current']}, DB={db_rate}"

    # total_tool_calls
    r = db.execute(text("SELECT COALESCE(SUM(tool_calls), 0) FROM metered_usage")).scalar()
    assert kpi_map["total_tool_calls"]["current"] == int(r), \
        f"total_tool_calls: API={kpi_map['total_tool_calls']['current']}, DB={int(r)}"

    # integrity_events (unresolved)
    r = db.execute(text("SELECT COUNT(*) FROM integrity_events WHERE resolved = 0")).scalar()
    assert kpi_map["integrity_events"]["current"] == int(r), \
        f"integrity_events: API={kpi_map['integrity_events']['current']}, DB={int(r)}"

    db.close()


# ── 测试 5：pct 计算正确 ─────────────────────────────────────────────
def test_kpi_pct_calculation():
    """验证 pct = current / target * 100。"""
    db = _get_test_db()
    kpis = _compute_kpis(db)
    for kpi in kpis:
        target = kpi["target"]
        current = kpi["current"]
        if target == 0:
            expected_pct = 0.0
        else:
            expected_pct = round(current / target * 100, 2)
        assert kpi["pct"] == expected_pct, \
            f"{kpi['key']}: pct={kpi['pct']}, expected={expected_pct} (current={current}, target={target})"
    db.close()


# ── 测试 6：缓存机制 ─────────────────────────────────────────────────
def test_kpi_caching():
    """验证缓存机制：首次计算后缓存，后续返回缓存。"""
    # 清除缓存
    _set_cached({})

    db = _get_test_db()

    # 首次计算
    kpis1 = _compute_kpis(db)
    assert len(kpis1) == 12

    # 写入缓存
    result = {"kpis": kpis1, "count": 12, "cached": False}
    _set_cached(result)

    # 检查缓存
    cached = _get_cached()
    assert cached is not None, "Cache should be populated"
    assert cached["count"] == 12
    assert cached["kpis"] == kpis1

    db.close()


# ── 测试 7：空表返回零值 ─────────────────────────────────────────────
def test_kpi_empty_tables():
    """验证空表时所有计数 KPI 返回 0。"""
    db = _get_test_db()

    # 清空所有表
    for table in ["integrity_events", "metered_usage", "ship_samples", "tensor_reports", "tensor_dev_missions"]:
        db.execute(text(f"DELETE FROM {table}"))
    db.commit()

    kpis = _compute_kpis(db)
    kpi_map = {kpi["key"]: kpi for kpi in kpis}

    # 计数类 KPI 应为 0
    assert kpi_map["total_missions"]["current"] == 0
    assert kpi_map["active_missions"]["current"] == 0
    assert kpi_map["completed_missions"]["current"] == 0
    assert kpi_map["failed_missions"]["current"] == 0
    assert kpi_map["total_reports"]["current"] == 0
    assert kpi_map["published_reports"]["current"] == 0
    assert kpi_map["total_samples"]["current"] == 0
    assert kpi_map["passed_samples"]["current"] == 0
    assert kpi_map["total_tool_calls"]["current"] == 0
    assert kpi_map["integrity_events"]["current"] == 0

    # 比率类 KPI 应为 0
    assert kpi_map["mission_success_rate"]["current"] == 0.0
    assert kpi_map["avg_mission_duration"]["current"] == 0.0

    db.close()


# ── 测试 8：target 值与定义一致 ──────────────────────────────────────
def test_kpi_targets_match_definition():
    """验证 KPI target 值与代码定义一致。"""
    expected_targets = {
        "total_missions": 1000,
        "active_missions": 50,
        "completed_missions": 500,
        "failed_missions": 20,
        "total_reports": 800,
        "published_reports": 400,
        "total_samples": 2000,
        "passed_samples": 1500,
        "mission_success_rate": 90,
        "avg_mission_duration": 3600,
        "total_tool_calls": 50000,
        "integrity_events": 5,
    }
    assert KPI_TARGETS == expected_targets, f"KPI targets mismatch: {KPI_TARGETS}"
