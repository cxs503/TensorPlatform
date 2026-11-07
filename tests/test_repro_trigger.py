"""Tests for src/services/repro_trigger.py.

Machine criteria covered (mission m5120):
  * complete 1 mission  -> exactly 1 new repro mission in the DB, linked via
    ``parent_mission_id``, with ``status='queued'`` and ``runner='repro'``;
  * repro mission finished -> verdict row in ``tensor_repro_verdicts``
    + mother mission status updated.

Run: ``pytest tests/test_repro_trigger.py -v``
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from services import repro_trigger as rt  # noqa: E402

ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    future=True,
)
SessionLocal = sessionmaker(bind=ENGINE, future=True)


@pytest.fixture()
def db():
    rt.ensure_schema(ENGINE)
    session = SessionLocal()
    # Isolate every test: scrub rows, keep schema.
    session.execute(text("DELETE FROM tensor_repro_verdicts"))
    session.execute(text("DELETE FROM tensor_dev_missions"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _add_mission(db, *, goal="Deliver feature X", status="running",
                 model="author-model-a", runner="openhands", parent_id=None,
                 org=1):
    mid = db.execute(
        text(
            """INSERT INTO tensor_dev_missions
                   (organization_id, goal, repo, base_ref, budget, runner, origin,
                    model, parent_mission_id, status)
               VALUES (:org, :goal, 'TensorPlatform', 'main', '{}', :runner,
                       'owner', :model, :pid, :status)
               RETURNING id"""
        ),
        {"org": org, "goal": goal, "runner": runner, "model": model,
         "pid": parent_id, "status": status},
    ).scalar()
    db.commit()
    return mid


def _missions(db, **where):
    sql = "SELECT * FROM tensor_dev_missions"
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = :{k}" for k in where)
    return db.execute(text(sql), where).mappings().all()


def _verdicts(db):
    return db.execute(text("SELECT * FROM tensor_repro_verdicts ORDER BY id")).mappings().all()


# ─────────────── 1. mission complete -> 1 repro mission (queued) ───────────────

class TestMissionCompleteTrigger:
    def test_complete_creates_exactly_one_queued_repro_mission(self, db):
        mother = _add_mission(db, status="running")
        assert _missions(db, runner="repro") == []

        receipt = rt.complete_mission(db, mother, status="succeeded",
                                      result={"summary": "done"})

        assert receipt["action"] == "enqueued"
        children = _missions(db, parent_mission_id=mother)
        assert len(children) == 1, "exactly one repro mission per completed mother"
        child = children[0]
        assert child["runner"] == "repro"
        assert child["status"] == "queued"
        assert child["model"] != "author-model-a", "blind order: repro model != author"
        assert child["repo"] == "TensorPlatform"
        assert json.loads(child["budget"])["max_tool_calls"] == rt.REPRO_BUDGET["max_tool_calls"]
        assert child["goal"].startswith(f"[REPRO] Verify claims of parent mission #{mother}")

        mother_row = _missions(db, id=mother)[0]
        assert mother_row["status"] == "succeeded"
        assert mother_row["finished_at"] is not None

    def test_trigger_is_idempotent(self, db):
        mother = _add_mission(db, status="succeeded")
        first = rt.on_mission_complete(db, mother)
        second = rt.on_mission_complete(db, mother)
        assert first["action"] == "enqueued"
        assert second["action"] == "noop"
        assert len(_missions(db, parent_mission_id=mother)) == 1

    def test_non_succeeded_mission_is_not_triggered(self, db):
        running = _add_mission(db, status="running")
        assert rt.on_mission_complete(db, running)["action"] == "noop"
        assert _missions(db, parent_mission_id=running) == []

    def test_repro_of_repro_is_never_spawned(self, db):
        mother = _add_mission(db, status="succeeded")
        rt.trigger_repro_for_mission(db, mother)
        child = _missions(db, parent_mission_id=mother)[0]
        assert rt.trigger_repro_for_mission(db, child["id"]) is None
        assert len(_missions(db)) == 2

    def test_repro_model_never_equals_author_even_for_default_names(self, db):
        for author in ("author-model-a", rt._DEFAULT_REPRO_MODEL, rt._ALT_REPRO_MODEL):
            model = rt.repro_model_for(author)
            assert model != author
            assert model in {rt._DEFAULT_REPRO_MODEL, rt._ALT_REPRO_MODEL}


# ─────────────── 2. verdict write-back + mother status update ───────────────

class TestVerdictWriteBack:
    def _mother_and_child(self, db, **kw):
        mother = _add_mission(db, status="succeeded", **kw)
        rt.trigger_repro_for_mission(db, mother)
        child = _missions(db, parent_mission_id=mother)[0]
        return mother, child["id"]

    def test_pass_verdict_row_and_mother_kept_succeeded(self, db):
        mother, child = self._mother_and_child(db)

        receipt = rt.record_repro_verdict(
            db, child, "pass",
            claims_vs_recomputed={"tests": "20/20 passed"},
            evidence_paths=["runs/repro.log"],
            wall_time_sec=812.5,
            notes="independent rerun reproduces claims",
        )

        rows = _verdicts(db)
        assert len(rows) == 1, "one verdict row for the mother mission"
        row = rows[0]
        assert row["mission_id"] == mother
        assert row["verdict"] == "pass"
        assert row["repro_model"] == "repro-agent-default"
        assert json.loads(row["claims_vs_recomputed"])["tests"] == "20/20 passed"
        assert json.loads(row["evidence_paths"]) == ["runs/repro.log"]
        assert row["wall_time_sec"] == pytest.approx(812.5)

        assert receipt["mother_status"] == "succeeded"
        mother_row = _missions(db, id=mother)[0]
        assert mother_row["status"] == "succeeded"
        assert json.loads(mother_row["result"])["repro"]["verdict"] == "pass"

        repro_row = _missions(db, id=child)[0]
        assert repro_row["status"] == "succeeded"
        assert repro_row["finished_at"] is not None

    def test_fail_verdict_revokes_mother(self, db):
        mother, child = self._mother_and_child(db, model="author-model-b")
        receipt = rt.record_repro_verdict(db, child, "fail", notes="claims not reproducible")
        assert receipt["mother_status"] == "revoked"
        assert _missions(db, id=mother)[0]["status"] == "revoked"

    def test_cheat_verdict_revokes_mother(self, db):
        mother, child = self._mother_and_child(db)
        rt.record_repro_verdict(db, child, "cheat", notes="no artifact, gates never ran")
        assert _missions(db, id=mother)[0]["status"] == "revoked"

    def test_duplicate_verdict_rejected_append_only(self, db):
        _mother, child = self._mother_and_child(db)
        rt.record_repro_verdict(db, child, "pass")
        with pytest.raises(ValueError, match="already recorded"):
            rt.record_repro_verdict(db, child, "fail")
        assert len(_verdicts(db)) == 1

    def test_invalid_verdict_rejected(self, db):
        _mother, child = self._mother_and_child(db)
        with pytest.raises(ValueError, match="verdict must be one of"):
            rt.record_repro_verdict(db, child, "green")
        assert _verdicts(db) == []

    def test_non_repro_mission_cannot_receive_verdict(self, db):
        mother, _child = self._mother_and_child(db)
        with pytest.raises(ValueError, match="not a repro mission"):
            rt.record_repro_verdict(db, mother, "pass")

    def test_repro_completion_without_verdict_is_noop(self, db):
        _mother, child = self._mother_and_child(db)
        out = rt.on_mission_complete(db, child)
        assert out["action"] == "noop"
        assert _verdicts(db) == []


# ─────────────── 3. cron fallback (5-min scan) ───────────────

class TestCronScanFallback:
    def test_scan_enqueues_for_missions_completed_without_hook(self, db):
        done = _add_mission(db, status="succeeded", goal="completed out-of-flow")
        running = _add_mission(db, status="running")

        receipts = rt.scan_completed_missions(db)
        assert [r["parent_mission_id"] for r in receipts] == [done]
        child = _missions(db, parent_mission_id=done)[0]
        assert child["runner"] == "repro" and child["status"] == "queued"

        # second scan: nothing new (idempotent cron)
        assert rt.scan_completed_missions(db) == []
        assert len(_missions(db, parent_mission_id=done)) == 1
        assert _missions(db, parent_mission_id=running) == []

    def test_scan_and_inflow_do_not_duplicate(self, db):
        mother = _add_mission(db, status="succeeded")
        rt.on_mission_complete(db, mother)
        assert rt.scan_completed_missions(db) == []
        assert len(_missions(db, parent_mission_id=mother)) == 1


def test_module_has_no_fastapi_dependency():
    """The trigger must be callable from cron/dispatcher without the API app."""
    import services.repro_trigger as mod

    src = open(mod.__file__, encoding="utf-8").read().lower()
    assert "import fastapi" not in src and "from fastapi" not in src
    assert "require_auth" not in src

# ─────────────── 4. dialect + cron CLI ───────────────

def test_json_param_cast_is_postgres_only():
    class _FakeDialect:
        name = "postgresql"

    class _FakeBind:
        dialect = _FakeDialect()

    class _FakeSession:
        def get_bind(self):
            return _FakeBind()

    assert rt._as_json_param(_FakeSession(), "budget") == "CAST(:budget AS jsonb)"
    assert rt._as_json_param(object.__new__(type("S", (), {"get_bind": lambda self: None})), "budget") == ":budget"


def test_cron_cli_scan_enqueues(tmp_path):
    """The documented cron entry point really works end-to-end."""
    import subprocess

    dsn = f"sqlite:///{tmp_path/'cron.db'}"
    engine = create_engine(dsn, future=True)
    rt.ensure_schema(engine)
    with sessionmaker(bind=engine, future=True)() as s:
        s.execute(
            text(
                """INSERT INTO tensor_dev_missions
                       (organization_id, goal, repo, model, status)
                   VALUES (1, 'cron pickup', 'TensorPlatform', 'author-x', 'succeeded')"""
            )
        )
        s.commit()
    engine.dispose()

    env = dict(os.environ, PYTHONPATH=os.path.join(REPO_ROOT, "src"))
    out = subprocess.run(
        [sys.executable, "-m", "services.repro_trigger", "--scan", "--dsn", dsn],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, check=True,
    )
    payload = json.loads(out.stdout)
    assert payload["enqueued"] == 1
    assert payload["items"][0]["status"] == "queued"
    assert payload["items"][0]["runner"] == "repro"
