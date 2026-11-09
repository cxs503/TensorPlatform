"""End-to-end verification of campaign conditional DAG routing on PostgreSQL.

Why this file exists
--------------------
The routing layer used to bootstrap its storage with SQLite-only DDL
(``PRAGMA table_info``, ``INTEGER PRIMARY KEY AUTOINCREMENT``,
``datetime('now')``) and to bind JSON strings / integer ``0|1`` flags into
JSONB / BOOLEAN columns. On the platform's production database (PostgreSQL)
every one of the 12 routing endpoints therefore failed with a 500 while the
SQLite-only suite stayed green. This module re-runs the whole acceptance
matrix on a *real* PostgreSQL server, i.e. on the dialect the platform
actually ships with.

Bootstrapping a server (the test is skipped when no DSN is given)::

    initdb -D /tmp/pgdata -U foundation --auth=trust      # unprivileged user
    pg_ctl -D /tmp/pgdata -o "-p 55432 -k /tmp -h 127.0.0.1" start
    createdb -h 127.0.0.1 -p 55432 -U foundation routing_test
    export CAMPAIGN_PG_DSN="postgresql+psycopg://foundation@127.0.0.1:55432/routing_test"
    python -m pytest backend/tests/test_campaign_routing_pg.py -q

Criteria covered (numbers follow the mission metadata):
  1 gate routing true/false branch          5 backward compatibility
  2 adaptive loop + auto retry (2x grid)    6 negative controls (3)
  3 convergence guard / escalation          7 edge idempotency
  4 concurrent evaluation                   8 audit trail in campaign_events
"""
from __future__ import annotations

import json
import os
import pathlib
import threading

import pytest

PG_DSN = os.getenv("CAMPAIGN_PG_DSN")

pytestmark = pytest.mark.skipif(
    not PG_DSN, reason="CAMPAIGN_PG_DSN not set — PostgreSQL integration test skipped"
)

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"

PREREQ_SQL = """
CREATE TABLE IF NOT EXISTS organizations (id BIGSERIAL PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, username TEXT);
CREATE TABLE IF NOT EXISTS factory_strategy_campaigns (
    id BIGSERIAL PRIMARY KEY, organization_id BIGINT, name TEXT);
CREATE TABLE IF NOT EXISTS tensor_dev_missions (id BIGSERIAL PRIMARY KEY);
"""


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    eng = create_engine(PG_DSN, future=True)
    with eng.begin() as conn:
        conn.exec_driver_sql(PREREQ_SQL)
    for name in ("095_campaign_plans.sql", "108_campaign_edge_conditions.sql"):
        sql = (MIGRATIONS / name).read_text(encoding="utf-8")
        with eng.begin() as conn:
            conn.exec_driver_sql(sql)
    return eng


@pytest.fixture()
def db(engine):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=engine)()
    session.execute(text("DELETE FROM campaign_events"))
    session.execute(text("DELETE FROM campaign_edge_conditions"))
    session.execute(text("DELETE FROM campaign_nodes"))
    session.execute(text("DELETE FROM campaign_plans"))
    session.execute(text("INSERT INTO organizations (id,name) VALUES (1,'Org') ON CONFLICT DO NOTHING"))
    session.execute(text(
        "INSERT INTO factory_strategy_campaigns (id,organization_id,name) "
        "VALUES (1,1,'C') ON CONFLICT DO NOTHING"))
    session.execute(text(
        "INSERT INTO campaign_plans (id, organization_id, campaign_id, name) VALUES (1,1,1,'P')"))
    for nid, name in ((1, "N1"), (2, "N2"), (3, "N3")):
        session.execute(
            text("INSERT INTO campaign_nodes (id, plan_id, name) VALUES (:i,:p,:n)"),
            {"i": nid, "p": 1, "n": name},
        )
    session.execute(text("SELECT setval('campaign_nodes_id_seq', 1000)"))
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def routing():
    from api import campaign_routing as cr

    return cr


@pytest.fixture()
def plans():
    from api import campaign_plans as cp

    return cp


USER = {"sub": "1", "role": "ADMIN", "username": "pg-tester"}


def _gate(cr, on_true=2, on_false=3, threshold=0.05, auto_retry=False):
    return cr.ConditionExpr(
        metric="max_rel_err", operator="<", threshold=threshold,
        on_true=on_true, on_false=on_false, auto_retry=auto_retry,
    )


def _events(db, event_type=None):
    from sqlalchemy import text

    sql = "SELECT event_type, detail FROM campaign_events"
    params = {}
    if event_type:
        sql += " WHERE event_type = :et"
        params["et"] = event_type
    return db.execute(text(sql + " ORDER BY id"), params).mappings().all()


# ───────────────────────── 1. gate routing ─────────────────────────

def test_gate_routing_branches_on_metric_true_and_false(db, routing):
    """err=0.03 < 0.05 → on_true (N2); err=0.08 > 0.05 → on_false (N3)."""
    cr = routing
    cr.update_node_condition(
        1,
        cr.NodeConditionUpdate(condition=_gate(cr, on_true=2, on_false=3)),
        db=db, user=USER,
    )

    hit = cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.03), db=db, user=USER)
    assert (hit["condition_met"], hit["activated_node_id"], hit["action"]) == (True, 2, "activated")

    miss = cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.08), db=db, user=USER)
    assert (miss["condition_met"], miss["activated_node_id"], miss["action"]) == (False, 3, "activated")


def test_gate_routing_through_edge_condition_table(db, routing):
    cr = routing
    body = cr.EdgeConditionCreate(plan_id=1, source_node_id=1, condition=_gate(cr))
    cr.create_edge_condition(body=body, db=db, user=USER)

    out = cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.01), db=db, user=USER)
    assert out["activated_node_id"] == 2
    out = cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.5), db=db, user=USER)
    assert out["activated_node_id"] == 3


# ───────────────────────── 2 + 3. adaptive loop / convergence ─────────────────────────

def test_adaptive_loop_auto_retry_then_converges(db, routing, plans):
    """Verdict fail → routing → auto retry node (grid 2x) → converge in ≤3 rounds."""
    cr, cp = routing, plans
    cr.update_node_condition(
        1,
        cr.NodeConditionUpdate(
            condition=_gate(cr, on_true=2, on_false=1, auto_retry=True),
            convergence_check=cr.ConvergenceCheckConfig(
                metric="max_rel_err", target=0.01, max_iterations=3),
        ),
        db=db, user=USER,
    )

    r1 = cp.update_node_verdict(
        1, cp.NodeVerdictUpdate(reproduction_verdict="fail", metrics={"max_rel_err": 0.08}),
        db=db, user=USER,
    )
    assert r1["routing"]["action"] == "loop_retry"
    retry = r1["routing"]["detail"]["retry_node"]
    assert retry["name"] == "N1_retry_2"
    assert retry["iteration"] == 1

    r2 = cp.update_node_verdict(
        1, cp.NodeVerdictUpdate(reproduction_verdict="fail", metrics={"max_rel_err": 0.06}),
        db=db, user=USER,
    )
    assert r2["routing"]["action"] == "loop_retry"

    # third round converges (metric under the loop target)
    r3 = cp.update_node_verdict(
        1, cp.NodeVerdictUpdate(reproduction_verdict="pass", metrics={"max_rel_err": 0.004}),
        db=db, user=USER,
    )
    assert r3["routing"]["action"] == "activated"
    assert r3["routing"]["activated_node_id"] == 2

    from sqlalchemy import text
    retries = db.execute(text(
        "SELECT name, metadata->>'grid_multiplier' AS gm FROM campaign_nodes "
        "WHERE parent_node_id = 1 ORDER BY id")).mappings().all()
    assert [r["name"] for r in retries] == ["N1_retry_2", "N1_retry_3"]
    assert all(float(r["gm"]) == 2.0 for r in retries)


def test_convergence_guard_blocks_fourth_iteration(db, routing, plans):
    """max_iterations=3 → the 4th failed round escalates instead of looping."""
    cr, cp = routing, plans
    cr.update_node_condition(
        1,
        cr.NodeConditionUpdate(
            condition=_gate(cr, on_true=2, on_false=1, auto_retry=True),
            convergence_check=cr.ConvergenceCheckConfig(
                metric="max_rel_err", target=0.01, max_iterations=3),
        ),
        db=db, user=USER,
    )

    actions = []
    for _ in range(4):
        r = cp.update_node_verdict(
            1, cp.NodeVerdictUpdate(reproduction_verdict="fail", metrics={"max_rel_err": 0.08}),
            db=db, user=USER,
        )
        actions.append(r["routing"]["action"])
    assert actions == ["loop_retry", "loop_retry", "loop_retry", "escalated"]

    from sqlalchemy import text
    node = db.execute(text(
        "SELECT status, block_reason, iteration_count FROM campaign_nodes WHERE id = 1"
    )).mappings().first()
    assert node["status"] == "blocked"
    assert "convergence exhausted" in node["block_reason"]
    assert node["iteration_count"] == 3
    # the 4th round must NOT have materialised another retry node
    retries = db.execute(text(
        "SELECT count(*) FROM campaign_nodes WHERE parent_node_id = 1")).scalar()
    assert retries == 3


# ───────────────────────── 4. concurrency ─────────────────────────

def test_concurrent_evaluations_do_not_race(db, engine, routing):
    """Two conditional edges evaluated from two threads → serialised, no lost writes."""
    cr = routing
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    cr.update_node_condition(1, cr.NodeConditionUpdate(condition=_gate(cr)), db=db, user=USER)
    cr.update_node_condition(2, cr.NodeConditionUpdate(condition=_gate(cr)), db=db, user=USER)

    Session = sessionmaker(bind=engine)
    errors: list[BaseException] = []
    rounds = 10

    def worker(node_id: int):
        s = Session()
        try:
            for _ in range(rounds):
                cr.evaluate_node_routing(node_id, cr.NodeEvaluateRequest(metric_value=0.01),
                                         db=s, user=USER)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    count = db.execute(text(
        "SELECT count(*) FROM campaign_events WHERE event_type = 'routing_evaluated'")).scalar()
    assert count == 2 * rounds


# ───────────────────────── 5. backward compatibility ─────────────────────────

def test_unconditional_legacy_verdict_is_untouched(db, routing, plans):
    """No condition → verdict path writes no routing event and returns routing=None."""
    cp = plans
    out = cp.update_node_verdict(
        1, cp.NodeVerdictUpdate(reproduction_verdict="pass"), db=db, user=USER,
    )
    assert out["status"] == "done"
    assert out["routing"] is None
    assert _events(db, "routing_evaluated") == []

    # ... but if a metric is supplied the router answers "no_condition" without
    # any side effect on the DAG.
    out2 = cp.update_node_verdict(
        1, cp.NodeVerdictUpdate(reproduction_verdict="pass", metrics={"max_rel_err": 0.01}),
        db=db, user=USER,
    )
    assert out2["routing"]["action"] == "no_condition"
    assert out2["routing"]["evaluated"] is False
    assert _events(db, "routing_evaluated") == []


# ───────────────────────── 6. negative controls ─────────────────────────

def test_negative_control_null_condition_routes_without_condition(db, routing):
    cr = routing
    out = cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.01), db=db, user=USER)
    assert out["action"] == "no_condition"
    assert out["evaluated"] is False
    assert out["activated_node_id"] is None


def test_negative_control_on_true_equals_on_false_is_unconditional(db, routing):
    cr = routing
    cr.update_node_condition(
        1, cr.NodeConditionUpdate(condition=_gate(cr, on_true=3, on_false=3)), db=db, user=USER,
    )
    hi = cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.01), db=db, user=USER)
    lo = cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=9.9), db=db, user=USER)
    assert hi["activated_node_id"] == lo["activated_node_id"] == 3


def test_negative_control_missing_source_node_is_rejected(db, routing):
    from fastapi import HTTPException

    cr = routing
    body = cr.EdgeConditionCreate(plan_id=1, source_node_id=999, condition=_gate(cr))
    with pytest.raises(HTTPException) as excinfo:
        cr.create_edge_condition(body=body, db=db, user=USER)
    assert excinfo.value.status_code == 404

    out = cr.route_after_verdict(db, 999, 0.01)
    assert out["evaluated"] is False and out["action"] == "skipped"


# ───────────────────────── 7. idempotency ─────────────────────────

def test_edge_creation_is_idempotent(db, routing):
    cr = routing
    body = cr.EdgeConditionCreate(plan_id=1, source_node_id=1, condition=_gate(cr))
    first = cr.create_edge_condition(body=body, db=db, user=USER)
    second = cr.create_edge_condition(body=body, db=db, user=USER)
    assert first["status"] == "created" and second["status"] == "updated"
    assert first["id"] == second["id"]
    assert cr.list_edge_conditions(1, None, db=db, user=USER)["total"] == 1


# ───────────────────────── 8. audit trail ─────────────────────────

def test_audit_trail_records_every_evaluation(db, routing):
    cr = routing
    cr.update_node_condition(1, cr.NodeConditionUpdate(condition=_gate(cr)), db=db, user=USER)
    cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.01), db=db, user=USER)
    cr.evaluate_node_routing(1, cr.NodeEvaluateRequest(metric_value=0.2), db=db, user=USER)

    rows = _events(db, "routing_evaluated")
    assert len(rows) == 2
    details = [r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"])
               for r in rows]
    assert details[0]["action"] == "activated" and details[0]["activated_node_id"] == 2
    assert details[1]["activated_node_id"] == 3
    assert {d["metric_name"] for d in details} == {"max_rel_err"}

    view = cr.get_plan_routing(1, db=db, user=USER)
    assert view["event_count"] >= 2
    assert {e["event_type"] for e in view["events"]} >= {"routing_evaluated"}


def test_routing_status_and_dag_validation_endpoints(db, routing):
    cr = routing
    cr.update_node_condition(1, cr.NodeConditionUpdate(condition=_gate(cr)), db=db, user=USER)
    status = cr.get_plan_routing_status(1, db=db, user=USER)
    assert status["plan_id"] == 1

    dag = cr.validate_dag(1, db=db, user=USER)
    assert dag["valid"] is True
    assert dag["errors"] == []


# ───────────────────────── migration 110 ─────────────────────────

def _load_migration(name: str):
    import importlib.util

    path = MIGRATIONS.parent / "alembic" / "versions" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_110_widens_event_type_whitelist_idempotently(engine):
    """A database migrated up to 108 must accept routing events after 110."""
    from sqlalchemy import text

    mod = _load_migration("110_campaign_routing_event_types.py")
    narrow = ("CHECK (event_type IN ('node_status_change','milestone_status_change',"
              "'block_detected','block_resolved','spinning_detected','escalation_created',"
              "'snapshot_taken','plan_updated','mission_dispatched','mission_completed',"
              "'guerrilla_detected'))")

    with engine.begin() as conn:
        # restore the pre-110 (migration 095) constraint
        for row in conn.execute(text(
                "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = 'campaign_events' AND c.contype = 'c' "
                "AND pg_get_constraintdef(c.oid) LIKE '%event_type%'")).mappings():
            conn.execute(text(f'ALTER TABLE campaign_events DROP CONSTRAINT "{row["conname"]}"'))
        conn.execute(text(
            "ALTER TABLE campaign_events ADD CONSTRAINT campaign_events_event_type_check "
            + narrow))

    with engine.begin() as conn:
        rewritten = mod.widen_event_type_check(conn)
    assert rewritten, "migration 110 must rewrite the narrow whitelist"

    with engine.begin() as conn:
        assert mod.widen_event_type_check(conn) == []   # idempotent
        cdef = conn.execute(text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid WHERE t.relname = 'campaign_events' "
            "AND c.contype = 'c' AND pg_get_constraintdef(c.oid) LIKE '%event_type%'"
        )).scalar()
        for event_type in mod.ROUTING_EVENT_TYPES:
            assert event_type in cdef
        assert "mission_completed" in cdef   # pre-existing literals preserved
        # a routing event is now insertable on a migrated database
        conn.execute(text(
            "INSERT INTO campaign_events (organization_id, plan_id, event_type, detail) "
            "VALUES (1, 1, 'routing_evaluated', CAST('{}' AS jsonb))"))
