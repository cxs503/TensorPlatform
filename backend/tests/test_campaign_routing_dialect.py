"""Dialect regression tests for the campaign routing layer.

The routing layer self-bootstraps its storage on first use. On the platform's
production database (PostgreSQL) the bootstrap used to emit SQLite-only DDL
(``PRAGMA table_info`` / ``AUTOINCREMENT`` / ``datetime('now')``), which aborts
the transaction and takes *every* routing endpoint down with a 500 — the
conditional-DAG feature was effectively dead in production while the
SQLite-only test suite stayed green.

These tests pin the generated SQL per dialect without needing a live server;
``tests/test_campaign_routing_pg.py`` covers the same behaviour end-to-end
against a real PostgreSQL.

Run: python -m pytest backend/tests/test_campaign_routing_dialect.py -q
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from api import campaign_routing as cr


# ───────────────────────── recording fake session ─────────────────────────

class _Row(dict):
    """dict that also supports positional access like a SQLAlchemy Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(item) from exc


class _Result:
    def __init__(self, rows: list[dict]):
        self._rows = [_Row(r) for r in rows]

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return [(i, r.get("name")) for i, r in enumerate(self._rows)]

    def scalar(self):
        if not self._rows:
            return None
        row = self._rows[0]
        return next(iter(row.values())) if isinstance(row, dict) else row

    def __iter__(self):
        return iter(self._rows)


class RecordingSession:
    """Minimal Session stand-in: records SQL, answers rows by matcher."""

    def __init__(self, dialect: str = "postgresql", rows_for=None):
        self._dialect = dialect
        self._rows_for = rows_for or (lambda sql: [])
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0

    # -- Session API used by the module under test --
    def get_bind(self):
        dialect = type("D", (), {"name": self._dialect})()
        return type("B", (), {"dialect": dialect})()

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params))
        return _Result(self._rows_for(sql))

    def commit(self):
        self.commits += 1

    def sql(self) -> str:
        return "\n".join(s for s, _ in self.statements)


SQLITE_ONLY_TOKENS = ("PRAGMA", "AUTOINCREMENT", "datetime('now')")


# ───────────────────────── column bootstrap ─────────────────────────

def test_pg_routing_columns_bootstrap_is_postgres_ddl():
    s = RecordingSession("postgresql")
    cr._ensure_routing_columns(s)
    sql = s.sql()
    assert "ADD COLUMN IF NOT EXISTS condition JSONB" in sql
    assert "iteration_count INTEGER NOT NULL DEFAULT 0" in sql
    for token in SQLITE_ONLY_TOKENS:
        assert token not in sql, f"SQLite-only token {token!r} emitted on PostgreSQL"


def test_sqlite_routing_columns_bootstrap_unchanged():
    """Negative control: the SQLite/embedded path must keep its PRAGMA probe."""
    s = RecordingSession("sqlite", rows_for=lambda sql: [{"name": "id"}, {"name": "plan_id"}])
    cr._ensure_routing_columns(s)
    sql = s.sql()
    assert "PRAGMA table_info(campaign_nodes)" in sql
    assert "ADD COLUMN condition TEXT" in sql
    assert "JSONB" not in sql


def test_pg_edge_conditions_table_ddl_is_postgres():
    s = RecordingSession("postgresql")
    cr._ensure_edge_conditions_table(s)
    sql = s.sql()
    assert "BIGSERIAL PRIMARY KEY" in sql
    assert "condition_expr JSONB" in sql
    assert "enabled BOOLEAN" in sql
    assert "TIMESTAMPTZ NOT NULL DEFAULT now()" in sql
    for token in SQLITE_ONLY_TOKENS:
        assert token not in sql, f"SQLite-only token {token!r} emitted on PostgreSQL"


def test_sqlite_edge_conditions_table_ddl_unchanged():
    s = RecordingSession("sqlite")
    cr._ensure_edge_conditions_table(s)
    sql = s.sql()
    assert "AUTOINCREMENT" in sql
    assert "CREATE TABLE IF NOT EXISTS campaign_edge_conditions" in sql


# ───────────────────────── event_type whitelist ─────────────────────────

_PG_CONSTRAINT_ROW = {
    "conname": "campaign_events_event_type_check",
    "cdef": "CHECK ((event_type = ANY (ARRAY['node_status_change'::text, "
            "'mission_completed'::text])))",
}


def _pg_with_constraint(row):
    return RecordingSession("postgresql", rows_for=lambda sql: [row] if "pg_constraint" in sql else [])


def test_pg_event_whitelist_is_widened_with_routing_types():
    s = _pg_with_constraint(_PG_CONSTRAINT_ROW)
    cr._ensure_event_types(s, ("routing_evaluated",))
    sql = s.sql()
    assert 'DROP CONSTRAINT "campaign_events_event_type_check"' in sql
    assert "ADD CONSTRAINT campaign_events_event_type_check" in sql
    assert "'routing_evaluated'" in sql
    # pre-existing literals must survive the rewrite
    assert "'node_status_change'" in sql and "'mission_completed'" in sql


def test_pg_event_whitelist_already_wide_is_left_alone():
    row = dict(_PG_CONSTRAINT_ROW)
    row["cdef"] = "CHECK ((event_type = ANY (ARRAY['routing_evaluated'::text])))"
    s = _pg_with_constraint(row)
    cr._ensure_event_types(s, ("routing_evaluated",))
    assert "DROP CONSTRAINT" not in s.sql()


def test_sqlite_event_whitelist_is_not_touched():
    s = RecordingSession("sqlite")
    cr._ensure_event_types(s, ("routing_evaluated",))
    assert s.statements == []


# ───────────────────────── JSON / bool binding ─────────────────────────

def test_json_sql_helper_is_dialect_aware():
    pg = RecordingSession("postgresql")
    lite = RecordingSession("sqlite")
    assert cr._json_sql(pg, ":expr") == "CAST(:expr AS jsonb)"
    assert cr._json_sql(lite, ":expr") == ":expr"


def test_pg_log_routing_event_casts_detail_to_jsonb():
    s = _pg_with_constraint(_PG_CONSTRAINT_ROW)
    cr._log_routing_event(s, org_id=1, plan_id=1, event_type="routing_evaluated",
                          detail={"node_id": 1})
    insert = [sql for sql, _ in s.statements if "INSERT INTO campaign_events" in sql]
    assert len(insert) == 1
    assert "CAST(:detail AS jsonb)" in insert[0]


def test_sqlite_log_routing_event_has_no_jsonb_cast():
    s = RecordingSession("sqlite")
    cr._log_routing_event(s, org_id=1, plan_id=1, event_type="routing_evaluated",
                          detail={"node_id": 1})
    insert = [sql for sql, _ in s.statements if "INSERT INTO campaign_events" in sql]
    assert len(insert) == 1
    assert "jsonb" not in insert[0]


def test_pg_create_edge_condition_binds_jsonb_and_real_boolean():
    def rows_for(sql: str):
        if "FROM campaign_plans" in sql:
            return [{"id": 1, "organization_id": 1}]
        if "FROM campaign_nodes" in sql:
            return [{"id": 1, "plan_id": 1}]
        if "FROM campaign_edge_conditions" in sql and sql.strip().startswith("SELECT id"):
            return []          # no existing edge → insert path
        if "pg_constraint" in sql:
            return [_PG_CONSTRAINT_ROW]
        if "MIN(id)" in sql:
            return [{"min_id": 1, "count": 1}]
        if "RETURNING" in sql:
            return [{"id": 7}]
        return []

    s = RecordingSession("postgresql", rows_for=rows_for)
    body = cr.EdgeConditionCreate(
        plan_id=1, source_node_id=1,
        condition=cr.ConditionExpr(metric="max_rel_err", operator="<", threshold=0.05,
                                   on_true=2, on_false=3),
    )
    # _insert_returning_id() needs RETURNING support → PG path returns a row
    out = cr.create_edge_condition(body=body, db=s, user={"sub": "1", "role": "ADMIN"})
    assert out["status"] in ("created", "updated")

    insert = [(sql, params) for sql, params in s.statements
              if "INSERT INTO campaign_edge_conditions" in sql]
    assert len(insert) == 1
    sql, params = insert[0]
    assert "CAST(:expr AS jsonb)" in sql
    assert params["en"] is True, "BOOLEAN column must receive a bool on PostgreSQL"
    assert json.loads(params["expr"])["on_false"] == 3


def test_pg_retry_node_binds_dependencies_as_array():
    original = {
        "id": 1, "plan_id": 1, "milestone_id": None, "mission_id": None,
        "name": "N1", "node_type": "task", "condition": {"on_false": 1},
        "convergence_check": {"target": 0.01, "max_iterations": 3},
        "iteration_count": 1, "metadata": {"grid_multiplier": 1.0},
        "organization_id": 1,
    }
    def rows_for(sql: str):  # noqa: E306 - nested matcher
        if "RETURNING id" in sql:
            return [{"id": 7}]
        if "FROM campaign_nodes" in sql:
            return [original]
        return []

    s = RecordingSession("postgresql", rows_for=rows_for)
    cr.create_retry_node(1, cr.RetryNodeRequest(), db=s, user=None)
    insert = [(sql, params) for sql, params in s.statements
              if "INSERT INTO campaign_nodes" in sql]
    assert len(insert) == 1
    sql, params = insert[0]
    assert "CAST(:cond AS jsonb)" in sql and "CAST(:meta AS jsonb)" in sql
    assert params["deps"] == [1], "INTEGER[] column must receive a list, not a JSON string"
    assert params["cond"] == json.dumps({"on_false": 1})


_MISSING = object()


@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_dialect_name_falls_back_when_bind_unavailable(dialect):
    class Broken:
        def get_bind(self):
            raise RuntimeError("no bind")

    assert cr._dialect_name(Broken()) == "sqlite"   # defensive default
    assert cr._dialect_name(RecordingSession(dialect)) == dialect
