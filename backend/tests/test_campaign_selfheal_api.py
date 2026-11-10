"""API-level acceptance tests — campaign DAG self-healing endpoints (P162W1-N08).

Exercise the real FastAPI router against a file-backed SQLite platform slice
(dependency overrides for ``get_db`` / ``require_auth`` only — no healing logic
is re-implemented here).  They prove the HTTP contract, the DB round-trip of
node state, and the persistence/integrity of the hash-chained audit trail.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api import campaign_self_heal as csh
from api.deps import require_auth
from database.config import get_db

BASE = "/tensor/campaigns"

SCHEMA = """
CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE campaign_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER,
    campaign_id INTEGER,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '2000-01-01T00:00:00+00:00',
    updated_at TEXT DEFAULT '2000-01-01T00:00:00+00:00'
);
CREATE TABLE campaign_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    milestone_id INTEGER,
    mission_id INTEGER,
    name TEXT NOT NULL,
    node_type TEXT DEFAULT 'task',
    dependencies TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    condition TEXT,
    convergence_check TEXT,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    parent_node_id INTEGER,
    block_reason TEXT,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '2000-01-01T00:00:00+00:00',
    updated_at TEXT DEFAULT '2000-01-01T00:00:00+00:00'
);
CREATE TABLE campaign_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER,
    plan_id INTEGER,
    event_type TEXT,
    detail TEXT,
    created_at TEXT
);
"""


def exec_script(conn, script: str) -> None:
    for stmt in (s.strip() for s in script.split(";")):
        if stmt:
            conn.execute(text(stmt))


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path/'selfheal.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with engine.begin() as conn:
        exec_script(conn, SCHEMA)
        conn.execute(text("INSERT INTO organizations (id, name) VALUES (1, 'Org')"))

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(csh.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[require_auth] = lambda: {
        "id": 1, "role": "admin", "organization_id": 1,
    }
    client = TestClient(app)

    class Env:
        pass

    e = Env()
    e.client, e.engine, e.Session = client, engine, Session
    yield e
    engine.dispose()


# ───────────────────────── helpers ─────────────────────────

def add_plan(env, metadata=None, name="plan") -> int:
    with env.engine.begin() as conn:
        r = conn.execute(
            text("INSERT INTO campaign_plans (organization_id, name, metadata) "
                 "VALUES (1, :n, :m)"),
            {"n": name, "m": json.dumps(metadata or {})},
        )
        return int(r.lastrowid)


def add_node(env, plan_id, name, deps=(), status="pending", metadata=None,
             consecutive_fails=0) -> int:
    with env.engine.begin() as conn:
        r = conn.execute(
            text(
                "INSERT INTO campaign_nodes "
                "(plan_id, name, dependencies, status, consecutive_fails, metadata) "
                "VALUES (:p, :n, :d, :s, :cf, :m)"
            ),
            {
                "p": plan_id,
                "n": name,
                "d": json.dumps(list(deps)),
                "s": status,
                "cf": consecutive_fails,
                "m": json.dumps(metadata or {}),
            },
        )
        return int(r.lastrowid)


def get_node(env, node_id) -> dict:
    with env.engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM campaign_nodes WHERE id = :i"), {"i": node_id}
        ).mappings().first()
        return dict(row)


def chain(env, plan_id, labels=("a", "b", "c"), **node_kwargs) -> list[int]:
    ids: list[int] = []
    for i, label in enumerate(labels):
        deps = [ids[-1]] if ids else []
        meta = dict(node_kwargs)
        ids.append(add_node(env, plan_id, label, deps=deps, metadata=meta))
    return ids


def events(env, plan_id, kind=None) -> list[dict]:
    with env.engine.begin() as conn:
        sql = "SELECT * FROM campaign_events WHERE plan_id = :p"
        params = {"p": plan_id}
        if kind:
            sql += " AND event_type = :k"
            params["k"] = kind
        rows = conn.execute(text(sql + " ORDER BY id"), params).mappings().all()
        return [dict(r) for r in rows]


# ───────────────────────── tests ─────────────────────────

def test_bypass_flow_persists_and_audits(env):
    plan = add_plan(env)
    a = add_node(env, plan, "a")
    b = add_node(env, plan, "b-critical", deps=[a], metadata={"critical": True})
    side = add_node(env, plan, "side", deps=[a])
    for i in range(1, 5):
        r = env.client.post(
            f"{BASE}/plans/{plan}/selfheal/report",
            json={"node_id": a, "success": False, "error": "boom", "now": 100.0 + i},
        )
        assert r.status_code == 200, r.text
        action = r.json()["action"]
        assert action["action"] == ("retry_scheduled" if i < 4 else "bypassed")
    assert get_node(env, a)["status"] == "skipped"

    # Saturated tick: only the explicitly critical node may run.
    r = env.client.post(
        f"{BASE}/plans/{plan}/selfheal/step",
        json={"now": 200.0, "load": {"cpu_pct": 92, "mem_pct": 70, "queue_len": 60,
                                     "queue_capacity": 64}},
    )
    body = r.json()
    assert body["step"]["schedule"]["saturated"] is True
    assert b in [d["node_id"] for d in body["step"]["schedule"]["dispatched"]]
    deferred = {d["node_id"]: d for d in body["step"]["schedule"]["deferred"]}
    assert side in deferred and deferred[side]["delay_s"] < 30.0
    assert get_node(env, side)["status"] == "pending"

    # Load drops after the defer window: the bypassed branch keeps flowing.
    t = 200.0 + deferred[side]["delay_s"]
    r = env.client.post(
        f"{BASE}/plans/{plan}/selfheal/step",
        json={"now": t, "load": {"cpu_pct": 20}},
    )
    assert side in [d["node_id"] for d in r.json()["step"]["schedule"]["dispatched"]]

    assert env.client.post(
        f"{BASE}/plans/{plan}/selfheal/report",
        json={"node_id": b, "success": True, "result": 2.0, "now": t + 1},
    ).status_code == 200
    assert env.client.post(
        f"{BASE}/plans/{plan}/selfheal/report",
        json={"node_id": side, "success": True, "result": 3.0, "now": t + 2},
    ).status_code == 200
    assert get_node(env, b)["status"] == "done"
    assert get_node(env, side)["status"] == "done"

    audit = env.client.get(f"{BASE}/plans/{plan}/selfheal/audit").json()
    assert audit["integrity"] is True
    actions = [e["action"] for e in audit["events"]]
    for expected in ("bypassed", "saturation_detected", "schedule_deferred",
                     "schedule_dispatched"):
        assert expected in actions, actions
    # Every event is a persisted selfheal_* row.
    types = {row["event_type"] for row in events(env, plan)}
    assert "selfheal_bypassed" in types and "selfheal_saturation_detected" in types


def test_retry_then_recover_across_requests(env):
    plan = add_plan(env)
    (a,) = chain(env, plan, labels=("a",))
    r = env.client.post(
        f"{BASE}/plans/{plan}/selfheal/report",
        json={"node_id": a, "success": False, "error": "transient", "now": 10.0},
    )
    act = r.json()["action"]
    assert act["action"] == "retry_scheduled"
    assert act["delay_s"] == 4.0
    node = get_node(env, a)
    assert node["status"] == "retry_pending"
    meta = json.loads(node["metadata"])
    assert meta["next_attempt_at"] == pytest.approx(14.0)
    assert meta["attempts"] == 1

    early = env.client.post(
        f"{BASE}/plans/{plan}/selfheal/step", json={"now": 13.0}
    ).json()
    assert early["step"]["advanced"] == []

    due = env.client.post(
        f"{BASE}/plans/{plan}/selfheal/step", json={"now": 14.0}
    ).json()
    assert due["step"]["advanced"][0]["node_id"] == a
    assert get_node(env, a)["status"] == "in-flight"

    ok = env.client.post(
        f"{BASE}/plans/{plan}/selfheal/report",
        json={"node_id": a, "success": True, "result": 7.0, "now": 15.0},
    ).json()
    assert ok["action"]["action"] == "recovered"
    assert get_node(env, a)["status"] == "done"
    assert ok["audit"]["integrity"] is True

    chain_audit = env.client.get(
        f"{BASE}/plans/{plan}/selfheal/audit", params={"only_heal": True}
    ).json()
    assert [e["action"] for e in chain_audit["events"]] == [
        "retry_scheduled", "retry_dispatched", "recovered"
    ]


def test_degraded_execution_and_deviation_guard(env):
    plan = add_plan(env)
    good = add_node(env, plan, "good", metadata={
        "degradable": True, "baseline_result": 3.14159 * 0.999,
    })
    strict = add_node(env, plan, "strict", metadata={
        "degradable": True, "baseline_result": 3.14159,
    })
    for i in range(4):
        r = env.client.post(
            f"{BASE}/plans/{plan}/selfheal/report",
            json={"node_id": good, "success": False, "error": "oom",
                  "degraded_result": 3.14159 * 0.99, "now": 10.0 + i},
        )
        assert r.status_code == 200
    act = r.json()["action"]
    assert act["action"] == "degraded" and act["deviation"] < 0.15
    node = get_node(env, good)
    assert node["status"] == "degraded"
    assert json.loads(node["metadata"])["quality"] == "degraded"

    for i in range(4):
        r = env.client.post(
            f"{BASE}/plans/{plan}/selfheal/report",
            json={"node_id": strict, "success": False, "error": "oom",
                  "degraded_result": 1.0, "now": 20.0 + i},
        )
    assert r.json()["action"]["action"] == "bypassed"
    assert get_node(env, strict)["status"] == "skipped"
    audit = env.client.get(f"{BASE}/plans/{plan}/selfheal/audit").json()
    assert "degrade_rejected" in [e["action"] for e in audit["events"]]


def test_negative_control_healthy_plan_has_no_heal_events(env):
    plan = add_plan(env)
    (a,) = chain(env, plan, labels=("a",))
    env.client.post(
        f"{BASE}/plans/{plan}/selfheal/step", json={"now": 1.0}
    )
    env.client.post(
        f"{BASE}/plans/{plan}/selfheal/report",
        json={"node_id": a, "success": True, "result": 1.0, "now": 2.0},
    )
    audit = env.client.get(f"{BASE}/plans/{plan}/selfheal/audit").json()
    assert audit["heal_events"] == 0
    only = env.client.get(
        f"{BASE}/plans/{plan}/selfheal/audit", params={"only_heal": True}
    ).json()
    assert only["events"] == []
    assert audit["total_events"] == 2  # dispatch + success, both non-healing


def test_policy_roundtrip_and_validation(env):
    plan = add_plan(env)
    chain(env, plan, labels=("a",))
    r = env.client.get(f"{BASE}/plans/{plan}/selfheal/policy")
    assert r.json()["policy"]["retry"]["max_retries"] == 3

    new = {
        "retry": {"max_retries": 5, "base_delay_s": 1.5, "factor": 2.0,
                  "max_delay_s": 60.0, "jitter_ratio": 0.0},
        "resources": {"saturation_threshold": 0.75, "max_defer_s": 25.0,
                      "queue_capacity": 32},
        "degradation": {"enabled": True, "max_deviation": 0.10},
        "bypass_on_exhaust": False,
    }
    r = env.client.put(f"{BASE}/plans/{plan}/selfheal/policy", json=new)
    assert r.status_code == 200
    assert r.json()["policy"]["retry"]["max_retries"] == 5
    assert env.client.get(f"{BASE}/plans/{plan}/selfheal/policy").json()["policy"] == new

    bad = dict(new)
    bad["retry"] = dict(new["retry"], factor=0.5)
    r = env.client.put(f"{BASE}/plans/{plan}/selfheal/policy", json=bad)
    assert r.status_code == 422


def test_validate_endpoint_and_cycle_rejection(env):
    plan = add_plan(env)
    a, b, c = chain(env, plan)
    r = env.client.post(f"{BASE}/plans/{plan}/selfheal/validate")
    body = r.json()
    assert r.status_code == 200 and body["acyclic"] is True
    assert body["topological_order"] == [a, b, c]
    assert body["critical_path"] == [a, b, c]

    # Inject a cycle behind the API's back: a <- c.
    with env.engine.begin() as conn:
        conn.execute(
            text("UPDATE campaign_nodes SET dependencies = :d WHERE id = :i"),
            {"d": json.dumps([c]), "i": a},
        )
    r = env.client.post(f"{BASE}/plans/{plan}/selfheal/validate")
    assert r.status_code == 409
    assert r.json()["detail"]["acyclic"] is False
    r = env.client.get(f"{BASE}/plans/{plan}/selfheal/state")
    assert r.status_code == 409
    cycle_rows = events(env, plan, "selfheal_cycle_rejected")
    assert cycle_rows, "cycle rejection must be audited"


def test_audit_tampering_is_detected(env):
    plan = add_plan(env)
    (a,) = chain(env, plan, labels=("a",))
    env.client.post(
        f"{BASE}/plans/{plan}/selfheal/report",
        json={"node_id": a, "success": False, "error": "x", "now": 5.0},
    )
    assert env.client.get(f"{BASE}/plans/{plan}/selfheal/audit").json()["integrity"] is True

    with env.engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, detail FROM campaign_events WHERE plan_id = :p "
                 "ORDER BY id LIMIT 1"),
            {"p": plan},
        ).mappings().first()
        payload = json.loads(row["detail"])
        payload["detail"] = {"tampered": True}
        conn.execute(
            text("UPDATE campaign_events SET detail = :d WHERE id = :i"),
            {"d": json.dumps(payload), "i": row["id"]},
        )
    audit = env.client.get(f"{BASE}/plans/{plan}/selfheal/audit").json()
    assert audit["integrity"] is False


def test_unknown_plan_and_node_errors(env):
    assert env.client.get(f"{BASE}/plans/999/selfheal/state").status_code == 404
    plan = add_plan(env)
    chain(env, plan, labels=("a",))
    r = env.client.post(
        f"{BASE}/plans/{plan}/selfheal/report",
        json={"node_id": 999, "success": True},
    )
    assert r.status_code == 404
    empty = add_plan(env, name="empty")
    assert env.client.get(f"{BASE}/plans/{empty}/selfheal/state").status_code == 400