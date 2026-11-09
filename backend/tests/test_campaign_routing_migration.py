"""Migration test for alembic revision 109 (campaign conditional DAG).

Runs the real alembic migration against a scratch SQLite database whose
campaign tables are in the pre-109 (legacy) shape, then asserts the schema the
routing API expects, and that the downgrade is clean.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]
BASE_REV = "ca5e2b2ff9e3"
HEAD_REV = "110_campaign_routing_events"

LEGACY_SCHEMA = [
    """CREATE TABLE campaign_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER,
        campaign_id INTEGER,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'active'
    )""",
    """CREATE TABLE campaign_nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        milestone_id INTEGER,
        mission_id INTEGER,
        name TEXT NOT NULL,
        node_type TEXT DEFAULT 'task',
        dependencies TEXT DEFAULT '[]',
        status TEXT DEFAULT 'pending',
        metadata TEXT DEFAULT '{}'
    )""",
]


@pytest.fixture()
def alembic_env(tmp_path):
    db_path = tmp_path / "migration.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url)
    with engine.begin() as conn:
        for stmt in LEGACY_SCHEMA:
            conn.execute(text(stmt))
    engine.dispose()

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.stamp(cfg, BASE_REV)
    return cfg, url


def _columns(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
    engine.dispose()
    return cols


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        names = {r[0] for r in rows}
    engine.dispose()
    return names


def test_upgrade_adds_routing_columns_and_edge_table(alembic_env):
    cfg, url = alembic_env
    command.upgrade(cfg, "head")

    node_cols = _columns(url, "campaign_nodes")
    assert {"condition", "convergence_check", "iteration_count",
            "parent_node_id"} <= node_cols

    tables = _tables(url)
    assert "campaign_edge_conditions" in tables
    edge_cols = _columns(url, "campaign_edge_conditions")
    assert {"id", "plan_id", "source_node_id", "condition_expr", "priority",
            "enabled", "created_at", "updated_at"} <= edge_cols

    engine = create_engine(url)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == HEAD_REV
    engine.dispose()


def test_unique_key_enforces_idempotency(alembic_env):
    """UNIQUE(plan_id, source_node_id, priority) is the idempotency key of POST edges."""
    cfg, url = alembic_env
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO campaign_plans (id, name) VALUES (1, 'p')"))
        conn.execute(text("INSERT INTO campaign_nodes (id, plan_id, name) VALUES (1, 1, 'n')"))
        insert = text("""
            INSERT INTO campaign_edge_conditions
                (plan_id, source_node_id, condition_expr, priority, enabled, created_at, updated_at)
            VALUES (1, 1, '{}', 0, 1, '2026-01-01', '2026-01-01')
        """)
        conn.execute(insert)
    with pytest.raises(Exception):
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO campaign_edge_conditions
                    (plan_id, source_node_id, condition_expr, priority, enabled, created_at, updated_at)
                VALUES (1, 1, '{}', 0, 1, '2026-01-01', '2026-01-01')
            """))
    engine.dispose()


def test_downgrade_restores_legacy_shape(alembic_env):
    cfg, url = alembic_env
    command.upgrade(cfg, "head")
    command.downgrade(cfg, BASE_REV)

    node_cols = _columns(url, "campaign_nodes")
    assert "condition" not in node_cols and "convergence_check" not in node_cols
    assert "campaign_edge_conditions" not in _tables(url)