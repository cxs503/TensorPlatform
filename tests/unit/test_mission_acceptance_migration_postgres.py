from __future__ import annotations

import os
from pathlib import Path

import pytest

try:
    import psycopg2
except ModuleNotFoundError:
    psycopg2 = None

if psycopg2 is None or not os.environ.get("DATABASE_URL"):
    pytest.skip(
        "requires psycopg2 and an isolated PostgreSQL DATABASE_URL",
        allow_module_level=True,
    )


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend" / "migrations" / "111_mission_acceptance_criteria_freeze.sql"

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="requires an isolated PostgreSQL DATABASE_URL",
)


@pytest.fixture()
def connection():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tensor_dev_missions CASCADE")
        cur.execute("CREATE TABLE tensor_dev_missions (id BIGSERIAL PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued')")
        cur.execute(MIGRATION.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def test_database_trigger_allows_normal_lifecycle_but_rejects_contract_mutation(connection):
    with connection.cursor() as cur:
        cur.execute("""
            INSERT INTO tensor_dev_missions
                (acceptance_criteria, acceptance_criteria_sha256,
                 acceptance_criteria_frozen_at, acceptance_criteria_freeze_source)
            VALUES ('[]'::jsonb, repeat('a', 64), now(), 'mission_created')
            RETURNING id
        """)
        mission_id = cur.fetchone()[0]

        # Ordinary lifecycle updates remain legal.
        cur.execute("UPDATE tensor_dev_missions SET status='running' WHERE id=%s", (mission_id,))
        cur.execute("UPDATE tensor_dev_missions SET status='completed' WHERE id=%s", (mission_id,))

        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "UPDATE tensor_dev_missions SET acceptance_criteria='[{\"metric\":\"x\",\"target\":1,\"tolerance\":0}]'::jsonb WHERE id=%s",
                (mission_id,),
            )


def test_legacy_snapshot_can_be_frozen_once_then_becomes_immutable(connection):
    with connection.cursor() as cur:
        cur.execute("INSERT INTO tensor_dev_missions DEFAULT VALUES RETURNING id")
        mission_id = cur.fetchone()[0]
        cur.execute("""
            UPDATE tensor_dev_missions
            SET acceptance_criteria='[]'::jsonb,
                acceptance_criteria_sha256=repeat('b', 64),
                acceptance_criteria_frozen_at=now(),
                acceptance_criteria_freeze_source='legacy_start',
                status='running'
            WHERE id=%s
        """, (mission_id,))
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "UPDATE tensor_dev_missions SET acceptance_criteria_freeze_source='mission_created' WHERE id=%s",
                (mission_id,),
            )
