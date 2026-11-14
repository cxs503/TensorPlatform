"""Tests for the batch mission API (``POST/GET /tensor/missions/batch``).

Run with::

    pytest tests/test_batch_api.py -v

The router is loaded from its file path so that a *standalone* module name is
used; ``backend/`` is placed first on ``sys.path`` so the real platform
dependencies (``api.deps.require_auth`` / ``database.config.get_db``) are the
ones under test, backed by a throw-away SQLite database created per test.

Two schema worlds are exercised, because the deliverable has to be honest about
what production can do today:

* ``atomic``            — ``batch_id`` column + ``tensor_mission_batches`` table
  exist (i.e. the migration proposal has been applied) → single transaction,
  evidence REAL.
* ``degraded_sequential`` — today's production schema (no ``batch_id``) → the
  API degrades to a loop of single inserts, evidence MOCK, ``atomic: false``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
BACKEND_DIR = REPO_ROOT / "backend"

_TMP = Path(tempfile.mkdtemp(prefix="batch_api_"))
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'batch_api.db'}"
os.environ["BATCH_MISSION_REGISTRY"] = str(_TMP / "registry.sqlite3")

# ``backend`` first: both backend/api and src/api exist, and the module under
# test must bind to the real platform dependencies.
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(BACKEND_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "batch_missions_under_test", SRC_DIR / "api" / "routes" / "batch_missions.py"
    )
    module = importlib.util.module_from_spec(spec)
    # register before exec: the module uses ``from __future__ import annotations``
    # and pydantic resolves the model annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BATCH = _load_module()
from database.config import engine  # noqa: E402  (same DATABASE_URL, imported after env is set)

MISSION_DDL = """
CREATE TABLE {table} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    goal            TEXT NOT NULL,
    repo            VARCHAR(120) NOT NULL CHECK (repo <> 'fault-inject'),
    base_ref        VARCHAR(120) NOT NULL DEFAULT 'main',
    budget          TEXT NOT NULL DEFAULT '{{}}',
    status          VARCHAR(24) NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','claimed','running','awaiting_acceptance',
                                      'succeeded','failed','stopped')),
    result          TEXT NOT NULL DEFAULT '{{}}',
    created_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    acceptance_criteria_sha256 TEXT,
    acceptance_criteria_frozen_at TEXT,
    acceptance_criteria_version INTEGER NOT NULL DEFAULT 1,
    acceptance_criteria_freeze_source TEXT NOT NULL DEFAULT 'legacy_unfrozen'{extra}
)
"""

BATCH_DDL = """
CREATE TABLE tensor_mission_batches (
    batch_id        TEXT PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    requested_count INTEGER NOT NULL,
    mission_ids     TEXT NOT NULL DEFAULT '[]',
    mode            TEXT NOT NULL,
    created_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _reset_schema(atomic: bool) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tensor_dev_missions"))
        conn.execute(text("DROP TABLE IF EXISTS tensor_mission_batches"))
        extra = ",\n    batch_id        TEXT" if atomic else ""
        conn.execute(text(MISSION_DDL.format(table="tensor_dev_missions", extra=extra)))
        if atomic:
            conn.execute(text(BATCH_DDL))


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "registry.sqlite3"
    monkeypatch.setenv("BATCH_MISSION_REGISTRY", str(path))
    return path


@pytest.fixture
def atomic_schema(registry):
    _reset_schema(atomic=True)
    yield
    _reset_schema(atomic=True)


@pytest.fixture
def degraded_schema(registry):
    _reset_schema(atomic=False)
    yield
    _reset_schema(atomic=False)


def _app(with_auth: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(BATCH.router)
    if with_auth:

        def _user():
            return {"username": "tester", "role": "ADMIN", "organization_id": 7}

        app.dependency_overrides[BATCH.require_auth] = _user
    return TestClient(app)


@pytest.fixture
def client(atomic_schema):
    return _app()


def _payload(n: int, repo: str = "TensorLBM") -> list[dict]:
    return [
        {
            "goal": f"batch mission {i}: verify resistance kernel {i}",
            "repo": repo,
            "base_ref": "main",
            "budget": {"max_tool_calls": 10 + i},
        }
        for i in range(n)
    ]


def _count_rows(where: str = "", params: dict | None = None) -> int:
    sql = "SELECT count(*) FROM tensor_dev_missions"
    if where:
        sql += f" WHERE {where}"
    with engine.begin() as conn:
        return int(conn.execute(text(sql), params or {}).scalar_one())


# --------------------------------------------------------------------------- #
# atomic mode (schema migrated) — REAL
# --------------------------------------------------------------------------- #
def test_submit_50_missions_is_atomic_and_all_rows_queued(client):
    resp = client.post("/tensor/missions/batch", json=_payload(50))
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["batch_id"] and uuid.UUID(body["batch_id"])
    assert body["mode"] == "atomic"
    assert body["evidence"] == "REAL"
    assert body["atomic"] is True
    assert body["count"] == 50
    assert len(body["mission_ids"]) == 50 == len(set(body["mission_ids"]))
    assert body["schema"]["batch_id_column"] is True
    assert body["schema"]["schema_migration_required"] is None

    # the acceptance criterion: DB has exactly 50 rows, all queued, all tagged
    assert _count_rows("batch_id = :b", {"b": body["batch_id"]}) == 50
    assert _count_rows("batch_id = :b AND status = 'queued'", {"b": body["batch_id"]}) == 50
    assert _count_rows() == 50
    with engine.begin() as conn:
        goals = [r[0] for r in conn.execute(text("SELECT goal FROM tensor_dev_missions ORDER BY id"))]
        budget = conn.execute(
            text("SELECT budget FROM tensor_dev_missions WHERE id = :i"), {"i": body["mission_ids"][0]}
        ).scalar_one()
        batch_row = conn.execute(
            text("SELECT requested_count, mission_ids FROM tensor_mission_batches WHERE batch_id = :b"),
            {"b": body["batch_id"]},
        ).fetchone()
    assert goals[0] == "batch mission 0: verify resistance kernel 0"
    assert json.loads(budget)["max_tool_calls"] == 10  # per-item budget preserved
    assert batch_row[0] == 50
    assert len(json.loads(batch_row[1])) == 50


def test_duplicate_submission_rejected_and_inserts_nothing(client):
    first = client.post("/tensor/missions/batch", json=_payload(50))
    assert first.status_code == 201
    batch_id = first.json()["batch_id"]

    second = client.post("/tensor/missions/batch", json=_payload(50))
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["error"] == "batch_already_submitted"
    assert detail["batch_id"] == batch_id
    assert len(detail["mission_ids"]) == 50
    assert _count_rows() == 50  # nothing new


def test_explicit_idempotency_key_wins_over_content_hash(client):
    client.post("/tensor/missions/batch", json=_payload(2), headers={"Idempotency-Key": "wave-1"})
    # different payload, same key → still a replay
    resp = client.post(
        "/tensor/missions/batch", json=_payload(3), headers={"Idempotency-Key": "wave-1"}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["idempotency_key"] == "wave-1"
    assert _count_rows() == 2


def test_progress_summary_reports_status_counts(client):
    resp = client.post("/tensor/missions/batch", json=_payload(10))
    batch_id = resp.json()["batch_id"]
    ids = resp.json()["mission_ids"]

    with engine.begin() as conn:
        for mid in ids[:4]:
            conn.execute(text("UPDATE tensor_dev_missions SET status='succeeded' WHERE id=:i"), {"i": mid})
        conn.execute(text("UPDATE tensor_dev_missions SET status='running' WHERE id=:i"), {"i": ids[4]})

    summary = client.get(f"/tensor/missions/batch/{batch_id}")
    assert summary.status_code == 200
    body = summary.json()
    assert body["batch_id"] == batch_id
    assert body["requested"] == 10 and body["count"] == 10
    assert body["status_counts"]["succeeded"] == 4
    assert body["status_counts"]["running"] == 1
    assert body["status_counts"]["queued"] == 5
    assert body["terminal"] == 4
    assert body["progress_percent"] == 40.0
    assert body["batch_status"] == "in_progress"
    assert body["mode"] == "atomic" and body["evidence"] == "REAL"
    assert len(body["missions"]) == 10

    compact = client.get(f"/tensor/missions/batch/{batch_id}", params={"include_missions": False})
    assert "missions" not in compact.json()


def test_progress_finished_when_all_terminal(client):
    resp = client.post("/tensor/missions/batch", json=_payload(3))
    batch_id = resp.json()["batch_id"]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tensor_dev_missions SET status='succeeded' WHERE batch_id=:b"),
            {"b": batch_id},
        )
    body = client.get(f"/tensor/missions/batch/{batch_id}").json()
    assert body["batch_status"] == "finished"
    assert body["progress_percent"] == 100.0


def test_unknown_batch_returns_404(client):
    resp = client.get(f"/tensor/missions/batch/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "batch_not_found"


def test_atomic_failure_rolls_back_everything(client):
    items = _payload(3)
    items[1]["repo"] = "fault-inject"  # CHECK constraint violation mid-batch
    resp = client.post("/tensor/missions/batch", json=items)
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "batch_insert_failed"
    assert detail["atomic"] is True
    assert detail["applied"] == []
    assert _count_rows() == 0  # nothing persisted → atomicity proven


def test_empty_and_non_array_payloads_rejected(client):
    assert client.post("/tensor/missions/batch", json=[]).status_code == 422
    assert client.post("/tensor/missions/batch", json={"missions": []}).status_code == 422
    assert client.post("/tensor/missions/batch", json=[{"repo": "TensorLBM"}]).status_code == 422


def test_auth_required(atomic_schema):
    anon = _app(with_auth=False)
    assert anon.post("/tensor/missions/batch", json=_payload(1)).status_code == 401
    assert anon.get(f"/tensor/missions/batch/{uuid.uuid4()}").status_code == 401


# --------------------------------------------------------------------------- #
# degraded mode (today's production schema) — MOCK
# --------------------------------------------------------------------------- #
def test_degraded_mode_lands_50_rows_and_is_labelled_mock(degraded_schema):
    client = _app()
    resp = client.post("/tensor/missions/batch", json=_payload(50))
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["batch_id"] and uuid.UUID(body["batch_id"])
    assert body["mode"] == "degraded_sequential"
    assert body["evidence"] == "MOCK"
    assert body["atomic"] is False
    assert body["count"] == 50
    assert body["schema"]["batch_id_column"] is False
    assert body["schema"]["schema_migration_required"] == BATCH.MIGRATION_FILE
    assert "MOCK" in body["notice"]

    assert _count_rows("status = 'queued'") == 50
    assert _count_rows() == 50

    summary = client.get(f"/tensor/missions/batch/{body['batch_id']}").json()
    assert summary["mode"] == "degraded_sequential"
    assert summary["evidence"] == "MOCK"
    assert summary["atomic"] is False
    assert summary["requested"] == 50 and summary["count"] == 50
    assert summary["status_counts"]["queued"] == 50


def test_degraded_mode_idempotency_rejects_replay(degraded_schema):
    client = _app()
    first = client.post("/tensor/missions/batch", json=_payload(50))
    assert first.status_code == 201
    second = client.post("/tensor/missions/batch", json=_payload(50))
    assert second.status_code == 409
    assert second.json()["detail"]["batch_id"] == first.json()["batch_id"]
    assert _count_rows() == 50


def test_degraded_mode_partial_failure_is_reported_honestly(degraded_schema):
    client = _app()
    items = _payload(3)
    items[1]["repo"] = "fault-inject"
    resp = client.post("/tensor/missions/batch", json=items)
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail["atomic"] is False
    assert detail["applied"] and len(detail["applied"]) == 1
    assert detail["failed_index"] == 1
    assert _count_rows() == 1  # non-atomic: the first row survives


def test_capabilities_probe_matches_schema(atomic_schema):
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        caps = BATCH.capabilities(session)
    assert caps["mode"] == "atomic" and caps["evidence"] == "REAL"
    assert caps["batch_id_column"] is True and caps["batch_table"] is True

def test_batch_submission_persists_predeclared_acceptance_contract(client):
    items = _payload(1)
    items[0]["acceptance_criteria"] = [
        "Implement the requested solver change and include a reproducible regression test.",
        {"metric": "relative_error", "target": "<0.03", "tolerance": "0.001"},
    ]
    response = client.post("/tensor/missions/batch", json=items)
    assert response.status_code == 201, response.text
    mission_id = response.json()["mission_ids"][0]
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT acceptance_criteria, acceptance_criteria_sha256,
                   acceptance_criteria_frozen_at, acceptance_criteria_freeze_source
            FROM tensor_dev_missions WHERE id=:mid
        """), {"mid": mission_id}).fetchone()
    criteria = json.loads(row[0])
    assert [item["criterion_id"] for item in criteria] == ["C001", "C002"]
    assert row[1] and len(row[1]) == 64
    assert row[2]
    assert row[3] == "mission_created"
    canonical = json.dumps(criteria, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    import hashlib
    assert row[1] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_batch_rejects_blank_qualitative_acceptance_criterion(client):
    items = _payload(1)
    items[0]["acceptance_criteria"] = ["   "]
    response = client.post("/tensor/missions/batch", json=items)
    assert response.status_code == 422
    assert _count_rows() == 0
