"""API-level acceptance tests — campaign conditional DAG routing (mission m7918).

Covers the eight machine acceptance criteria of the "campaign DAG conditional
branching / adaptive research loop maturity" node:

  1. conditional routing correctness (err<thr -> on_true, err>thr -> on_false)
  2. adaptive loop: failed verification -> auto retry with 2x grid refinement ->
     convergence within 3 iterations
  3. convergence detection: max_iterations=3 -> 4th iteration blocked + escalated
  4. concurrency safety: two conditional edges evaluated in parallel, no race
  5. backward compatibility: plans without conditions behave as before
  6. negative controls: condition=NULL, on_true==on_false, missing source node
  7. API idempotency: repeated POST does not create duplicate edges
  8. audit trail: every routing evaluation lands in campaign_events

The tests exercise the real FastAPI router with a SQLite session (dependency
overrides for get_db / require_auth only — no routing logic is re-implemented
here, unlike the older standalone suite which mirrors the logic).
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from api import campaign_routing as cr
from api.deps import require_auth
from database.config import get_db

BASE = "/tensor/campaigns"

# Routing columns added by migration 109 (the API self-heals them on SQLite).
ROUTING_COLS = """
    condition TEXT,
    convergence_check TEXT,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    parent_node_id INTEGER,
"""

SCHEMA_HEAD = """
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
CREATE TABLE campaign_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER,
    plan_id INTEGER,
    event_type TEXT,
    detail TEXT,
    created_at TEXT
);
"""

SCHEMA_NODES_MODERN = """
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
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '2000-01-01T00:00:00+00:00',
    updated_at TEXT DEFAULT '2000-01-01T00:00:00+00:00'
);
"""

SCHEMA_NODES_LEGACY = """
CREATE TABLE campaign_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    milestone_id INTEGER,
    mission_id INTEGER,
    name TEXT NOT NULL,
    node_type TEXT DEFAULT 'task',
    dependencies TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    block_reason TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '2000-01-01T00:00:00+00:00',
    updated_at TEXT DEFAULT '2000-01-01T00:00:00+00:00'
);
"""


def exec_script(conn, script: str) -> None:
    """Execute a multi-statement DDL script (sqlite drivers run one at a time)."""
    for stmt in (s.strip() for s in script.split(";")):
        if stmt:
            conn.execute(text(stmt))


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture()
def make_env(tmp_path):
    """Factory: isolated file-backed SQLite platform slice + FastAPI test client."""
    engines = []

    def _make(legacy: bool = False) -> SimpleNamespace:
        db_file = tmp_path / f"routing_{len(engines)}_{'legacy' if legacy else 'std'}.db"
        engine = create_engine(
            f"sqlite:///{db_file}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        engines.append(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        with engine.begin() as conn:
            exec_script(conn, SCHEMA_HEAD)
            exec_script(conn, SCHEMA_NODES_LEGACY if legacy else SCHEMA_NODES_MODERN)
            conn.execute(text("INSERT INTO organizations (id, name) VALUES (1, 'Test Org')"))

        def _get_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app = FastAPI()
        app.include_router(cr.router)
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[require_auth] = lambda: {
            "id": 1, "role": "admin", "organization_id": 1,
        }
        client = TestClient(app)
        return SimpleNamespace(
            client=client, engine=engine, Session=Session,
            session=lambda: Session(), db_file=str(db_file),
        )

    yield _make
    for engine in engines:
        engine.dispose()


@pytest.fixture()
def env(make_env):
    return make_env()


# ───────────────────────── helpers ─────────────────────────

def add_plan(env, name="plan", metadata=None) -> int:
    with env.engine.begin() as conn:
        r = conn.execute(
            text("INSERT INTO campaign_plans (organization_id, name, metadata) "
                 "VALUES (1, :n, :m)"),
            {"n": name, "m": json.dumps(metadata or {})},
        )
        return int(r.lastrowid)


def add_node(env, plan_id, name, *, status="pending", condition=None,
             convergence=None, iteration=0, metadata=None) -> int:
    """Insert a campaign node, tolerating the pre-migration (legacy) schema."""
    with env.engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(campaign_nodes)"))}
        payload = {"p": plan_id, "n": name, "s": status,
                   "m": json.dumps(metadata or {})}
        names = ["plan_id", "name", "status", "metadata"]
        if "condition" in cols:
            names += ["condition", "convergence_check", "iteration_count"]
            payload.update({
                "c": json.dumps(condition) if condition else None,
                "v": json.dumps(convergence) if convergence else None,
                "i": iteration,
            })
        placeholders = {"plan_id": ":p", "name": ":n", "status": ":s",
                        "metadata": ":m", "condition": ":c",
                        "convergence_check": ":v", "iteration_count": ":i"}
        r = conn.execute(
            text(f"INSERT INTO campaign_nodes ({', '.join(names)}) VALUES "
                 f"({', '.join(placeholders[c] for c in names)})"),
            payload,
        )
        return int(r.lastrowid)


def gate(on_true: int, on_false: int, threshold=0.05, metric="max_rel_err"):
    return {
        "type": "gate", "metric": metric, "operator": "<",
        "threshold": threshold, "on_true": on_true, "on_false": on_false,
    }


def node_row(env, node_id) -> dict:
    with env.engine.begin() as conn:
        return dict(conn.execute(
            text("SELECT * FROM campaign_nodes WHERE id = :i"), {"i": node_id}
        ).mappings().one())


def events(env, plan_id, event_type=None) -> list[dict]:
    q = "SELECT * FROM campaign_events WHERE plan_id = :p"
    params = {"p": plan_id}
    if event_type:
        q += " AND event_type = :t"
        params["t"] = event_type
    with env.engine.begin() as conn:
        return [dict(r) for r in conn.execute(text(q + " ORDER BY id"), params).mappings()]


def edge_count(env, plan_id) -> int:
    with env.engine.begin() as conn:
        return int(conn.execute(
            text("SELECT COUNT(*) FROM campaign_edge_conditions WHERE plan_id = :p"),
            {"p": plan_id},
        ).scalar())


def gate_dag(env):
    """3-node DAG: N1 gate -> N2 (pass) / N3 (fail)."""
    plan = add_plan(env, "gate_dag")
    n2 = add_node(env, plan, "verify_pass")
    n3 = add_node(env, plan, "verify_fail")
    n1 = add_node(env, plan, "verify", condition=gate(n2, n3))
    return plan, n1, n2, n3


# ─────────────────── 1. conditional routing correctness ───────────────────

def test_gate_pass_activates_on_true(env):
    """err=0.03 < 0.05 -> on_true (N2) is the routed target."""
    plan, n1, n2, n3 = gate_dag(env)
    r = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                        json={"metric_value": 0.03, "metric_name": "max_rel_err"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["condition_met"] is True
    assert body["activated_node_id"] == n2
    assert body["activated_node_id"] != n3
    assert body["action"] == "activated"


def test_gate_fail_activates_on_false(env):
    """err=0.08 > 0.05 -> on_false (N3) is the routed target."""
    plan, n1, n2, n3 = gate_dag(env)
    r = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                        json={"metric_value": 0.08, "metric_name": "max_rel_err"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["condition_met"] is False
    assert body["activated_node_id"] == n3
    assert body["activated_node_id"] != n2


def test_gate_boundary_is_strict_less_than(env):
    """err == threshold with operator '<' routes to on_false (strict gate)."""
    plan, n1, n2, n3 = gate_dag(env)
    r = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                        json={"metric_value": 0.05, "metric_name": "max_rel_err"})
    assert r.json()["condition_met"] is False
    assert r.json()["activated_node_id"] == n3


def test_gate_metric_name_mismatch_routes_on_false(env):
    """A metric the condition does not reference is treated as not-met."""
    plan, n1, n2, n3 = gate_dag(env)
    r = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                        json={"metric_value": 0.001, "metric_name": "cb_error"})
    assert r.json()["condition_met"] is False
    assert r.json()["activated_node_id"] == n3


def test_edge_table_condition_routes_correctly(env):
    """Explicit edge condition (POST /plans/{id}/edges) drives the same routing."""
    plan = add_plan(env, "edge_dag")
    n2 = add_node(env, plan, "ok")
    n3 = add_node(env, plan, "bad")
    n1 = add_node(env, plan, "verify")
    r = env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": n1, "condition": gate(n2, n3), "priority": 0,
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"

    low = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                          json={"metric_value": 0.03}).json()
    high = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                           json={"metric_value": 0.08}).json()
    assert low["activated_node_id"] == n2
    assert high["activated_node_id"] == n3


# ─────────────────── 2. adaptive research loop ───────────────────

def test_adaptive_loop_retry_requests_refinement(env):
    """Failed verification (err > target) triggers loop_retry, not activation."""
    plan = add_plan(env, "loop")
    n1 = add_node(env, plan, "verify", condition=gate(0, 0),
                  convergence={"metric": "max_rel_err", "target": 0.01,
                               "max_iterations": 3, "action_on_exhaust": "escalate"},
                  metadata={"params": {"nx": 100, "ny": 20}})
    # self-loop: on_false points back to itself
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE campaign_nodes SET condition = :c WHERE id = :i"), {
            "c": json.dumps(gate(999, n1, threshold=0.005)), "i": n1,
        })
    body = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                           json={"metric_value": 0.4}).json()
    assert body["action"] == "loop_retry"
    assert body["activated_node_id"] == n1
    assert node_row(env, n1)["iteration_count"] == 1


def test_adaptive_loop_retry_refines_grid_2x(env):
    """Retry node inherits parameters with grid multiplier doubled (2x)."""
    plan = add_plan(env, "loop_refine")
    n1 = add_node(env, plan, "verify", metadata={"params": {"nx": 100, "ny": 20}})
    r = env.client.post(f"{BASE}/nodes/{n1}/create-retry", json={})
    assert r.status_code == 200, r.text
    retry_id = r.json()["id"]
    meta = json.loads(node_row(env, retry_id)["metadata"])
    assert meta["is_retry"] is True
    assert meta["parent_node_id"] == n1
    assert meta["grid_multiplier"] == pytest.approx(2.0)
    assert meta["params"]["nx"] == 200 and meta["params"]["ny"] == 20 * 2
    # iteration is incremented at routing evaluation, not again at retry creation
    assert node_row(env, retry_id)["iteration_count"] == 0
    assert r.json()["name"].endswith("_retry_1")


def test_adaptive_loop_second_retry_refines_4x(env):
    """Chained retries keep compounding the refinement (2 -> 4)."""
    plan = add_plan(env, "loop_refine2")
    n1 = add_node(env, plan, "verify", metadata={"params": {"nx": 100}})
    r1 = env.client.post(f"{BASE}/nodes/{n1}/create-retry", json={}).json()
    meta1 = json.loads(node_row(env, r1["id"])["metadata"])
    r2 = env.client.post(f"{BASE}/nodes/{r1['id']}/create-retry", json={}).json()
    meta2 = json.loads(node_row(env, r2["id"])["metadata"])
    assert meta1["grid_multiplier"] == pytest.approx(2.0)
    assert meta2["grid_multiplier"] == pytest.approx(4.0)
    assert meta2["params"]["nx"] == 400


def test_adaptive_loop_converges_within_3_iterations(env):
    """verify(fail) -> retry(2x) -> verify(fail) -> retry(4x) -> verify: converged."""
    plan = add_plan(env, "loop_converge")
    conv = {"metric": "max_rel_err", "target": 0.01, "max_iterations": 3,
            "action_on_exhaust": "escalate"}
    n1 = add_node(env, plan, "verify", condition=gate(0, 0), convergence=conv,
                  metadata={"params": {"nx": 100}})
    with env.engine.begin() as conn:
        # tight gate (0.005) + looser convergence target (0.01): refinement
        # saturates before the gate is met, so the loop ends by convergence.
        conn.execute(text("UPDATE campaign_nodes SET condition = :c WHERE id = :i"), {
            "c": json.dumps(gate(999, n1, threshold=0.005)), "i": n1,
        })

    node = n1
    errors = [0.4, 0.18, 0.008]          # grid refinement drives the error down
    actions, multipliers, iterations = [], [], []
    for err in errors:
        body = env.client.post(f"{BASE}/nodes/{node}/evaluate",
                               json={"metric_value": err}).json()
        actions.append(body["action"])
        iterations.append(node_row(env, node)["iteration_count"])
        if body["action"] == "converged":
            break
        assert body["action"] == "loop_retry"
        retry = env.client.post(f"{BASE}/nodes/{node}/create-retry", json={}).json()
        node = retry["id"]
        multipliers.append(json.loads(node_row(env, node)["metadata"])["grid_multiplier"])

    assert actions == ["loop_retry", "loop_retry", "converged"]
    assert multipliers == [pytest.approx(2.0), pytest.approx(4.0)]
    assert max(iterations) <= 3
    assert node_row(env, node)["status"] == "done"


def test_converged_loop_does_not_reactivate_retry_branch(env):
    """Regression: converging must stop the loop instead of activating on_false."""
    plan = add_plan(env, "loop_stop")
    n1 = add_node(env, plan, "verify",
                  convergence={"metric": "max_rel_err", "target": 0.01,
                               "max_iterations": 5, "action_on_exhaust": "escalate"})
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE campaign_nodes SET condition = :c WHERE id = :i"), {
            "c": json.dumps(gate(999, n1, threshold=0.001)), "i": n1,
        })
    body = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                           json={"metric_value": 0.005}).json()
    assert body["action"] == "converged"
    assert body["activated_node_id"] is None
    assert node_row(env, n1)["status"] == "done"


# ─────────────────── 3. convergence / escalation ───────────────────

def test_max_iterations_allows_up_to_three(env):
    """iteration 0..2 may continue looping (max_iterations=3)."""
    plan = add_plan(env, "esc_ok")
    n1 = add_node(env, plan, "verify", iteration=2,
                  convergence={"metric": "max_rel_err", "target": 0.01,
                               "max_iterations": 3, "action_on_exhaust": "escalate"})
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE campaign_nodes SET condition = :c WHERE id = :i"), {
            "c": json.dumps(gate(999, n1, threshold=0.005)), "i": n1,
        })
    body = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                           json={"metric_value": 0.4}).json()
    assert body["action"] == "loop_retry"


def test_max_iterations_blocks_fourth_and_escalates(env):
    """iteration_count == 3 (4th attempt) is blocked and escalated."""
    plan = add_plan(env, "esc_block")
    n1 = add_node(env, plan, "verify", iteration=3,
                  convergence={"metric": "max_rel_err", "target": 0.01,
                               "max_iterations": 3, "action_on_exhaust": "escalate"})
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE campaign_nodes SET condition = :c WHERE id = :i"), {
            "c": json.dumps(gate(999, n1)), "i": n1,
        })
    body = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                           json={"metric_value": 0.4}).json()
    assert body["action"] == "escalated"
    assert body["activated_node_id"] is None
    row = node_row(env, n1)
    assert row["status"] == "blocked"
    assert "convergence exhausted after 3 iterations" in row["block_reason"]


def test_escalation_is_audited(env):
    plan = add_plan(env, "esc_audit")
    n1 = add_node(env, plan, "verify", iteration=3,
                  convergence={"metric": "max_rel_err", "target": 0.01,
                               "max_iterations": 3, "action_on_exhaust": "escalate"})
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE campaign_nodes SET condition = :c WHERE id = :i"), {
            "c": json.dumps(gate(999, n1)), "i": n1,
        })
    env.client.post(f"{BASE}/nodes/{n1}/evaluate", json={"metric_value": 0.4})
    evs = events(env, plan, "routing_evaluated")
    assert len(evs) == 1
    assert json.loads(evs[0]["detail"])["action"] == "escalated"


# ─────────────────── 7. idempotency ───────────────────

def test_post_edges_is_idempotent(env):
    """Repeating the same POST (same source+priority) does not duplicate the edge."""
    plan = add_plan(env, "idem")
    n2 = add_node(env, plan, "ok")
    n3 = add_node(env, plan, "bad")
    n1 = add_node(env, plan, "verify")
    first = env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": n1, "condition": gate(n2, n3), "priority": 0,
    }).json()
    second = env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": n1, "condition": gate(n2, n3), "priority": 0,
    }).json()
    assert first["id"] == second["id"]
    assert first["status"] == "created"
    assert second["status"] == "updated"
    assert edge_count(env, plan) == 1


def test_post_edges_distinct_priority_creates_new_edge(env):
    plan = add_plan(env, "idem2")
    n2 = add_node(env, plan, "ok")
    n3 = add_node(env, plan, "bad")
    n1 = add_node(env, plan, "verify")
    env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": n1, "condition": gate(n2, n3), "priority": 0})
    env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": n1, "condition": gate(n3, n2), "priority": 5})
    assert edge_count(env, plan) == 2


# ─────────────────── 8. audit trail ───────────────────

def test_routing_evaluation_writes_campaign_event(env):
    plan, n1, n2, n3 = gate_dag(env)
    env.client.post(f"{BASE}/nodes/{n1}/evaluate", json={"metric_value": 0.03})
    evs = events(env, plan, "routing_evaluated")
    assert len(evs) == 1
    detail = json.loads(evs[0]["detail"])
    assert detail["node_id"] == n1
    assert detail["activated_node_id"] == n2
    assert detail["action"] == "activated"


def test_routing_view_exposes_audit_tail(env):
    plan, n1, n2, n3 = gate_dag(env)
    env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": n1, "condition": gate(n2, n3), "priority": 1})
    env.client.post(f"{BASE}/nodes/{n1}/evaluate", json={"metric_value": 0.03})
    view = env.client.get(f"{BASE}/plans/{plan}/routing").json()
    assert view["plan_id"] == plan
    assert view["total_nodes"] == 3
    assert view["nodes_with_conditions"] == 1
    assert view["event_count"] >= 2
    kinds = {e["event_type"] for e in view["events"]}
    assert "routing_evaluated" in kinds and "edge_condition_created" in kinds


# ─────────────────── 6. negative controls (3) ───────────────────

def test_neg_condition_null_means_unconditional(env):
    """Negative control 1: no condition at all -> no_condition, nothing activated."""
    plan = add_plan(env, "neg_null")
    a = add_node(env, plan, "a")
    b = add_node(env, plan, "b")
    body = env.client.post(f"{BASE}/nodes/{a}/evaluate",
                           json={"metric_value": 0.9}).json()
    assert body["evaluated"] is False
    assert body["action"] == "no_condition"
    assert body["activated_node_id"] is None
    assert node_row(env, a)["status"] == "pending"
    assert node_row(env, b)["status"] == "pending"


def test_neg_on_true_equals_on_false_is_unconditional(env):
    """Negative control 2: on_true == on_false -> same target either way."""
    plan = add_plan(env, "neg_same")
    target = add_node(env, plan, "target")
    other = add_node(env, plan, "other")
    n1 = add_node(env, plan, "verify", condition=gate(target, target))
    low = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                          json={"metric_value": 0.03}).json()
    high = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                           json={"metric_value": 0.9}).json()
    assert low["activated_node_id"] == high["activated_node_id"] == target
    assert other != low["activated_node_id"]


def test_neg_missing_source_node_degrades_gracefully(env):
    """Negative control 3: unknown source node -> 404, no orphan edge/event."""
    plan = add_plan(env, "neg_missing")
    n2 = add_node(env, plan, "ok")
    n3 = add_node(env, plan, "bad")
    r = env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": 9999, "condition": gate(n2, n3), "priority": 0})
    assert r.status_code == 404
    assert "source node not found" in r.json()["detail"]
    assert edge_count(env, plan) == 0
    assert events(env, plan) == []

    r2 = env.client.post(f"{BASE}/nodes/9999/evaluate", json={"metric_value": 0.03})
    assert r2.status_code == 404


# ─────────────────── 5. backward compatibility ───────────────────

def _legacy_plan(env, name="legacy"):
    """5-node linear plan, no conditions anywhere (pre-109 schema shape)."""
    plan = add_plan(env, name)
    ids = [add_node(env, plan, f"step{i}") for i in range(5)]
    return plan, ids


def test_legacy_plan_evaluation_is_noop(env):
    plan, ids = _legacy_plan(env)
    for nid in ids:
        body = env.client.post(f"{BASE}/nodes/{nid}/evaluate",
                               json={"metric_value": 0.5}).json()
        assert body["action"] == "no_condition"
        assert body["evaluated"] is False
    assert [node_row(env, i)["status"] for i in ids] == ["pending"] * 5


def test_legacy_plan_routing_view_stable(env):
    plan, ids = _legacy_plan(env, "legacy_view")
    view = env.client.get(f"{BASE}/plans/{plan}/routing").json()
    assert view["total_nodes"] == 5
    assert view["nodes_with_conditions"] == 0
    assert view["edge_conditions"] == []
    assert all(n["has_condition"] is False for n in view["nodes"])


def test_legacy_plan_validate_dag_ok(env):
    plan, ids = _legacy_plan(env, "legacy_dag")
    body = env.client.get(f"{BASE}/plans/{plan}/validate-dag").json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["node_count"] == 5


def test_legacy_plan_edge_list_endpoint_still_works(env):
    plan, ids = _legacy_plan(env, "legacy_edges")
    body = env.client.get(f"{BASE}/edge-conditions", params={"plan_id": plan}).json()
    assert body == [] or body.get("items") == [] or body.get("edge_conditions") == []


def test_legacy_schema_self_heals_routing_columns(make_env):
    """Legacy DB (no condition column) still serves routing calls without error."""
    env = make_env(legacy=True)
    plan = add_plan(env, "legacy_schema")
    n1 = add_node(env, plan, "step")
    body = env.client.post(f"{BASE}/nodes/{n1}/evaluate",
                           json={"metric_value": 0.2}).json()
    assert body["action"] == "no_condition"
    with env.engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(campaign_nodes)"))}
    assert {"condition", "convergence_check", "iteration_count",
            "parent_node_id"} <= cols


# ─────────────────── 4. concurrency safety ───────────────────

def test_concurrent_edge_evaluations_no_race(make_env):
    """Two conditional edges evaluated from two threads both route correctly."""
    env = make_env()
    plan = add_plan(env, "conc")
    a_ok = add_node(env, plan, "a_ok")
    a_bad = add_node(env, plan, "a_bad")
    b_ok = add_node(env, plan, "b_ok")
    b_bad = add_node(env, plan, "b_bad")
    a = add_node(env, plan, "a_verify")
    b = add_node(env, plan, "b_verify")

    # pre-create the routing tables single-threaded (schema creation is not
    # what this test measures; the router's per-plan lock is)
    with env.Session() as db:
        cr._ensure_routing_columns(db)
        cr._ensure_edge_conditions_table(db)

    assert env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": a, "condition": gate(a_ok, a_bad), "priority": 0,
    }).status_code == 200
    assert env.client.post(f"{BASE}/plans/{plan}/edges", json={
        "source_node_id": b, "condition": gate(b_ok, b_bad), "priority": 0,
    }).status_code == 200

    results: dict[str, dict] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def run(label, node_id, value):
        try:
            with env.Session() as db:
                barrier.wait(timeout=10)
                out = cr.evaluate_routing(
                    cr.RoutingEvaluationRequest(node_id=node_id, metric_value=value),
                    db=db,
                    user={"id": 1, "role": "admin", "organization_id": 1},
                )
                results[label] = out
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    t1 = threading.Thread(target=run, args=("a", a, 0.03))
    t2 = threading.Thread(target=run, args=("b", b, 0.08))
    t1.start(); t2.start(); t1.join(timeout=60); t2.join(timeout=60)

    assert not errors, errors
    assert results["a"]["activated_node_id"] == a_ok
    assert results["b"]["activated_node_id"] == b_bad
    # exactly one audit event per evaluation: no duplicated/partial routing
    evs = events(env, plan, "routing_evaluated")
    assert len(evs) == 2
    routed = {json.loads(e["detail"])["node_id"] for e in evs}
    assert routed == {a, b}

def test_iteration_accounting_allows_exactly_three_rounds(env):
    """Regression: 3 refinement rounds allowed, the 4th escalates (no double count).

    Before the fix the retry node added +1 on top of the evaluation increment, so
    iteration_count advanced 2 per round and max_iterations=3 escalated already
    on the second retry.
    """
    plan = add_plan(env, "rounds")
    conv = {"metric": "max_rel_err", "target": 0.001, "max_iterations": 3,
            "action_on_exhaust": "escalate"}
    node = add_node(env, plan, "verify", convergence=conv)
    with env.engine.begin() as conn:
        conn.execute(text("UPDATE campaign_nodes SET condition = :c WHERE id = :i"), {
            "c": json.dumps(gate(999, node, threshold=0.001)), "i": node,
        })

    observed = []
    for round_index in range(4):
        row = node_row(env, node)
        assert row["iteration_count"] == round_index
        body = env.client.post(f"{BASE}/nodes/{node}/evaluate",
                               json={"metric_value": 0.5}).json()
        if body["action"] == "escalated":
            observed.append("escalated")
            break
        assert body["action"] == "loop_retry"
        observed.append("loop_retry")
        node = env.client.post(f"{BASE}/nodes/{node}/create-retry", json={}).json()["id"]

    assert observed == ["loop_retry", "loop_retry", "loop_retry", "escalated"]
    assert node_row(env, node)["status"] == "blocked"


def test_created_edge_reports_its_own_row_id(env):
    """Regression: returned edge id must be the inserted row's id.

    The previous implementation read ``SELECT last_insert_rowid()`` on a pooled
    connection that may have inserted other rows / may belong to another
    request, so a freshly created edge could report an unrelated id (observed:
    row 3 reported as id 50). Every returned id must resolve to the row that was
    actually created.
    """
    created = []
    for i in range(5):
        plan = add_plan(env, f"ids_{i}")
        t_ok, t_bad = add_node(env, plan, "ok"), add_node(env, plan, "bad")
        src = add_node(env, plan, "verify")
        body = env.client.post(f"{BASE}/plans/{plan}/edges", json={
            "source_node_id": src, "condition": gate(t_ok, t_bad), "priority": 0,
        }).json()
        created.append({"plan": plan, "id": body["id"], "src": src})

    assert len({c["id"] for c in created}) == len(created)
    for c in created:
        got = env.client.get(f"{BASE}/edge-conditions/{c['id']}")
        assert got.status_code == 200
        assert got.json()["source_node_id"] == c["src"]
        assert got.json()["plan_id"] == c["plan"]


def test_created_retry_reports_its_own_row_id(env):
    """Same invariant for POST /nodes/{id}/create-retry."""
    created = []
    for i in range(4):
        plan = add_plan(env, f"retry_ids_{i}")
        src = add_node(env, plan, "verify")
        body = env.client.post(f"{BASE}/nodes/{src}/create-retry", json={}).json()
        created.append({"plan": plan, "id": body["id"], "parent": src})

    assert len({c["id"] for c in created}) == len(created)
    for c in created:
        row = node_row(env, c["id"])
        assert row["plan_id"] == c["plan"]
        assert row["parent_node_id"] == c["parent"]
