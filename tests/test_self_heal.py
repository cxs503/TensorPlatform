"""Acceptance tests for mission-level self-healing (p393w2-n04).

Machine-checkable criteria (numbers land in ``artifacts/self_heal_report.json``):

(a) detection: 50 injected stalled missions → recall ≥ 0.95, false-positive ≤ 5%;
(b) root cause: six-class macro F1 ≥ 0.80 (confusion matrix persisted);
(c) recovery: recoverable causes (infra_lost/timeout/resource_contention) ≥ 0.70;
(d) idempotency: re-running one window 10× yields one ledger sha256 (10/10);
(e) zero application-domain semantics (platform lexicon scan, 0 hits);
(f) negative control: 30 healthy running missions → 0 detections;
(g) falsifiability: a recall < 0.80 or recovery < 0.50 run must record honest FAIL.

Run::

    python -m pytest tests/test_self_heal.py -q
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.self_heal import (  # noqa: E402
    MissionRow,
    SelfHealLedger,
    Thresholds,
    detect_stalled,
    plan_recovery,
    run_pipeline,
)
from tools.self_heal.executor import DryRunExecutor  # noqa: E402
from tools.self_heal.pipeline import build_context  # noqa: E402
from tools.self_heal.simulate import (  # noqa: E402
    CAUSES,
    SCENARIO_NOW,
    WINDOW_ID,
    build_scenario,
    run_acceptance,
)

UTC = timezone.utc
T0 = SCENARIO_NOW


def row(mid: int, *, status="running", heartbeat_min=120.0, created_min=200.0,
        session=None, task_key=None, result=None, budget=None, budget_used=None) -> dict:
    def ts(minutes):
        if minutes is None:
            return None
        return datetime.fromtimestamp(T0 - minutes * 60, UTC).isoformat()

    return {
        "id": mid,
        "status": status,
        "created_at": ts(created_min),
        "heartbeat_at": ts(heartbeat_min),
        "claimed_by": session,
        "agent_session_id": session,
        "task_key": task_key or f"task-{mid}",
        "result": result or {},
        "budget": budget or {},
        "budget_used": budget_used or {},
    }


# ── (a)/(f) detection ────────────────────────────────────────────────────


def test_detector_positive_negative_and_blind_rows():
    rows = [
        row(1, heartbeat_min=120),                      # stalled: heartbeat 2 h old
        row(2, status="queued", heartbeat_min=None, created_min=180),  # stalled: queue wait
        row(3, heartbeat_min=2),                        # healthy
        row(4, status="pending", heartbeat_min=None, created_min=10),  # healthy queue
        row(5, status="suspended", heartbeat_min=None, created_min=600),  # must be ignored
        row(6, heartbeat_min=None, created_min=None),   # unobservable
        {"id": 7, "status": "failed", "created_at": None, "heartbeat_at": None},
    ]
    detections, unobservable = detect_stalled(rows, now=T0, thresholds=Thresholds())
    detected = {d.mission_id: d.reason for d in detections}
    assert detected == {1: "heartbeat_stale", 2: "queue_wait_timeout"}
    assert [u["mission_id"] for u in unobservable] == [6], "unobservable rows must be reported, not silently clean"
    # threshold boundary is inclusive: exactly stall_after_s counts as stalled
    boundary = detect_stalled([row(9, heartbeat_min=30)], now=T0, thresholds=Thresholds())[0]
    assert [d.mission_id for d in boundary] == [9]


def test_acceptance_detection_recall_and_false_positive_rate():
    report = run_acceptance(runs=3)
    det = report["detection"]
    assert det["injected_stalled"] == 50
    assert det["detected_stalled"] >= 48, det
    assert det["recall"] >= 0.95
    assert det["injected_controls"] == 35
    assert det["false_positives"] == [], "healthy/suspended controls must never be touched"
    assert det["false_positive_rate"] <= 0.05
    assert report["criteria"]["f_negative_control_zero_fp"] is True


# ── (b) root cause classification ────────────────────────────────────────


def test_classification_confusion_matrix_and_macro_f1():
    report = run_acceptance(runs=3)
    scores = report["classification"]["scores"]
    assert set(CAUSES) <= set(scores)
    for cause in CAUSES:
        assert scores[cause]["support"] >= 8, f"{cause} needs ~8 injected samples"
        assert scores[cause]["recall"] >= 0.75, scores[cause]
    assert scores["macro"]["f1"] >= 0.80
    assert report["criteria"]["b_macro_f1_ge_0.80"] is True
    matrix = report["classification"]["confusion_matrix"]
    assert set(matrix) == set(CAUSES)
    assert sum(sum(row.values()) for row in matrix.values()) == 50


def test_decoy_infrastructure_row_is_not_misread_as_timeout():
    """infra rows carry a decoy 'timeout' string; session-closed evidence must win."""
    rows = [row(11, session="sess-x", result={"error": "executor heartbeat timeout: runner lost"})]
    ctx = build_context([MissionRow.from_mapping(r) for r in rows], T0, "W-1",
                        closed_sessions=["sess-x"])
    out = run_pipeline(rows, now=T0, window_id="W-1", context=ctx, executor=DryRunExecutor())
    assert [c.cause for c in out.classifications] == ["infra_lost"]


def test_duplicate_and_unknown_paths():
    rows = [
        row(21, heartbeat_min=35, session="s-a", task_key="dup-key"),
        row(22, heartbeat_min=34, session="s-b", task_key="dup-key"),
        row(23, heartbeat_min=50, session="s-c"),
    ]
    ctx = build_context([MissionRow.from_mapping(r) for r in rows], T0, "W-1",
                        session_last_seen={"s-a": T0, "s-b": T0, "s-c": T0})
    out = run_pipeline(rows, now=T0, window_id="W-1", context=ctx, executor=DryRunExecutor())
    causes = {c.mission_id: c.cause for c in out.classifications}
    assert causes[21] == "unknown", "older owner of the task key has no positive signal"
    assert causes[22] == "duplicate_seed"
    actions = {p.mission_id: p for p in out.plans}
    assert actions[22].action == "archive"
    assert actions[22].params["canonical_mission_id"] == 21
    assert actions[23].action == "manual_review"
    outcomes = {mid: o.status for mid, o in out.outcomes.items()}
    assert outcomes[22] == "archived"
    assert outcomes[23] == "escalated"


def test_criteria_and_timeout_actions():
    rows = [
        row(31, session="s-1", result={"acceptance": {"failures": 4}}),
        row(32, session="s-2", result={"error": "wall_exhausted"},
            budget={"max_wall_seconds": 7200}, budget_used={"wall_seconds": 7200}),
        row(33, status="queued", heartbeat_min=None, created_min=180,
            result={"scheduler": {"wait_rounds": 5}}),
    ]
    ctx = build_context([MissionRow.from_mapping(r) for r in rows], T0, "W-1",
                        session_last_seen={"s-1": T0, "s-2": T0})
    out = run_pipeline(rows, now=T0, window_id="W-1", context=ctx, executor=DryRunExecutor())
    plans = {p.mission_id: p for p in out.plans}
    assert plans[31].action == "escalate" and plans[31].params["target"] == "proposer"
    assert plans[32].action == "extend_timeout"
    assert plans[32].params["new_max_wall_seconds"] == int(7200 * 1.5)
    assert plans[33].action == "degrade_resources"


# ── (c) recovery success and caps ────────────────────────────────────────


def test_recovery_success_rate_meets_target():
    report = run_acceptance(runs=3)
    recovery = report["recovery"]
    assert recovery["attempted"] >= 24
    assert recovery["success_rate"] >= 0.70, recovery
    assert report["criteria"]["c_recovery_success_ge_0.70"] is True
    assert set(recovery["by_cause"]) == {"infra_lost", "timeout", "resource_contention"}
    assert recovery["mean_time_to_recover_s"] > 0


def test_reschedule_cap_escalates_on_fourth_window():
    rows = [row(41, session="s-9", heartbeat_min=120)]
    ctx = build_context([MissionRow.from_mapping(r) for r in rows], T0, "W-1", closed_sessions=["s-9"])
    ledger = SelfHealLedger()
    actions = []
    for window in range(1, 5):
        out = run_pipeline(rows, now=T0 + window * 3600, window_id=f"W-0{window}",
                           ledger=ledger, context=ctx, executor=DryRunExecutor())
        actions.append(out.plans[0].action)
        assert len(out.new_entries) == 1
    assert actions == ["reschedule", "reschedule", "reschedule", "escalate"]
    assert ledger.entries[-1].params["reason"] == "recovery_cap_exhausted"
    assert ledger.verify()["ok"] is True


def test_deferred_dispatch_is_not_counted_as_recovered():
    rows = [row(42, session="s-8")]
    ctx = build_context([MissionRow.from_mapping(r) for r in rows], T0, "W-1", closed_sessions=["s-8"])
    ledger = SelfHealLedger()
    out = run_pipeline(rows, now=T0, window_id="W-1", ledger=ledger, context=ctx,
                       executor=DryRunExecutor(availability={42: False}))
    assert out.outcomes[42].status == "deferred"
    from tools.self_heal.pipeline import recovery_success_rate
    assert recovery_success_rate(ledger.entries)["success_rate"] == 0.0


# ── (d) idempotency + integrity ──────────────────────────────────────────


def test_same_window_ten_runs_share_one_ledger_digest():
    scenario = build_scenario()
    ledger = SelfHealLedger()
    digests, executed = [], []
    common = dict(now=SCENARIO_NOW, window_id=WINDOW_ID, ledger=ledger,
                  context=scenario.context, executor=DryRunExecutor(scenario.availability))
    for _ in range(10):
        out = run_pipeline(scenario.rows, **common)
        digests.append(out.ledger_digest)
        executed.append(out.executed)
        # re-run must skip every action that the first run already recorded
    assert len(set(digests)) == 1, "same window must be byte-identical"
    assert executed[0] == 50 and executed[1:] == [0] * 9
    assert len(ledger) == 50
    assert ledger.verify()["ok"] is True


def test_ledger_detects_tampering():
    scenario = build_scenario()
    out = run_pipeline(scenario.rows, now=SCENARIO_NOW, window_id=WINDOW_ID,
                       ledger=SelfHealLedger(), context=scenario.context,
                       executor=DryRunExecutor(scenario.availability))
    ledger = SelfHealLedger(list(out.new_entries))
    assert ledger.verify()["ok"] is True

    victim = ledger.entries[3]
    original = victim.action
    victim.action = "escalate"
    verdict = ledger.verify()
    assert verdict["ok"] is False and verdict["reason"] == "hash_mismatch"
    victim.action = original
    assert ledger.verify()["ok"] is True

    reordered = SelfHealLedger([ledger.entries[1], ledger.entries[0]] + list(ledger.entries[2:]))
    assert reordered.verify()["ok"] is False


def test_ledger_roundtrip_preserves_digest(tmp_path):
    scenario = build_scenario()
    ledger = SelfHealLedger()
    run_pipeline(scenario.rows, now=SCENARIO_NOW, window_id=WINDOW_ID, ledger=ledger,
                 context=scenario.context, executor=DryRunExecutor(scenario.availability))
    path = ledger.save(tmp_path / "ledger.json")
    loaded = SelfHealLedger.load(path)
    assert loaded.digest() == ledger.digest()
    assert loaded.verify() == ledger.verify()


def test_ambiguity_probe_priority_is_deterministic():
    from tools.self_heal.simulate import ambiguity_probe

    probe = ambiguity_probe()
    assert len(probe) == 5
    assert all(case["match"] for case in probe), [c for c in probe if not c["match"]]


def test_session_id_is_the_liveness_key_and_freeform_claims_are_not():
    rows = [row(51, session="uuid-label")]
    rows[0]["session_id"] = 77
    ctx = build_context([MissionRow.from_mapping(r) for r in rows], T0, "W-1",
                        closed_sessions=["77"])
    out = run_pipeline(rows, now=T0, window_id="W-1", context=ctx, executor=DryRunExecutor())
    assert [c.cause for c in out.classifications] == ["infra_lost"]

    # a free-form label with no session row must NOT be reported as infra loss
    rows = [row(52, session="uuid-unknown")]
    ctx = build_context([MissionRow.from_mapping(r) for r in rows], T0, "W-1",
                        session_index_complete=False)
    out = run_pipeline(rows, now=T0, window_id="W-1", context=ctx, executor=DryRunExecutor())
    assert [c.cause for c in out.classifications] == ["unknown"]


# ── (e) zero application-domain semantics ────────────────────────────────


def test_zero_domain_semantics_scan_is_clean():
    from tools.benchmark_runner import domain_guard

    verdict = domain_guard.scan([
        "tools/self_heal",
        "backend/api/tensor_self_heal.py",
        "backend/migrations/115_self_heal_ledger.sql",
        "tests/test_self_heal.py",
    ])
    assert verdict["status"] == "clean", verdict.get("hits")
    assert verdict["n_hits"] == 0
    assert verdict["scanned_files"] >= 8


# ── (g) falsifiability wiring ────────────────────────────────────────────


def test_honest_fail_flag_is_reachable():
    """A degenerate corpus (everything missed) must trip the honest-FAIL flag."""
    report = run_acceptance(
        runs=3,
        scenario=build_scenario(),
        thresholds=Thresholds(stall_after_s=10 ** 9, queue_wait_s=10 ** 9),
    )
    assert report["detection"]["detected_stalled"] == 0
    assert report["criteria"]["a_detection_recall_ge_0.95"] is False
    assert report["honest_fail"] is True


# ── API surface (SQLite) ─────────────────────────────────────────────────


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
_API_BACKEND = REPO / "backend"
if str(_API_BACKEND) not in sys.path:
    sys.path.insert(0, str(_API_BACKEND))


def _api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from api.deps import require_auth
    from api.tensor_self_heal import router
    from database.config import get_db

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _session
    app.dependency_overrides[require_auth] = lambda: {"id": 1, "role": "ADMIN", "organization_id": 1}
    return TestClient(app), engine


def _api_missions() -> list:
    return [
        # stalled, owner session closed → infra_lost → reschedule
        row(101, session="s-101"),
        # stalled, healthy session, acceptance rejections → criteria → escalate
        row(102, session="s-102", result={"acceptance": {"failures": 4}}),
        # healthy control
        row(103, session="s-103", heartbeat_min=1),
        # duplicate pair (younger copy is archived)
        row(104, session="s-104", heartbeat_min=35, task_key="api-dup"),
        row(105, session="s-105", heartbeat_min=34, task_key="api-dup"),
    ]


def test_api_run_persists_ledger_and_is_idempotent():
    client, engine = _api_client()
    body = {
        "now": T0,
        "window_id": "W-api-01",
        "dry_run": True,
        "missions": _api_missions(),
        "closed_sessions": ["s-101"],
        "session_last_seen": {"s-102": T0, "s-103": T0, "s-104": T0, "s-105": T0},
    }
    first = client.post("/api/v1/tensor/self-heal/run", json=body)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["executed"] == 4, payload["detected"]
    assert payload["persisted_entries"] == 4
    digest = payload["ledger_digest"]

    second = client.post("/api/v1/tensor/self-heal/run", json=body)
    assert second.status_code == 200
    assert second.json()["persisted_entries"] == 0
    assert second.json()["ledger_digest"] == digest

    status = client.get("/api/v1/tensor/self-heal/status")
    assert status.status_code == 200
    assert status.json()["entries"] == 4
    assert status.json()["chain"]["ok"] is True

    dashboard = client.get("/api/v1/tensor/self-heal/dashboard")
    assert dashboard.status_code == 200
    dash = dashboard.json()
    assert dash["entries"] == 4
    assert dash["cause_distribution"]["infra_lost"] == 1
    assert dash["cause_distribution"]["criteria_infeasible"] == 1
    assert dash["cause_distribution"]["duplicate_seed"] == 1
    assert dash["cause_distribution"]["unknown"] == 1
    assert dash["ledger_digest"] == digest

    ledger = client.get("/api/v1/tensor/self-heal/ledger", params={"window_id": "W-api-01"})
    assert ledger.status_code == 200
    assert ledger.json()["count"] == 4


def test_api_run_reads_missions_from_db_and_resolves_session_liveness():
    from sqlalchemy import text

    client, engine = _api_client()
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE tensor_dev_missions (
                id INTEGER PRIMARY KEY, organization_id INTEGER NOT NULL,
                status TEXT, created_at TEXT, heartbeat_at TEXT, finished_at TEXT,
                claimed_by TEXT, agent_session_id TEXT, task_key TEXT, repo TEXT,
                goal TEXT, fail_stage TEXT, result TEXT, budget TEXT, budget_used TEXT,
                session_id INTEGER
            )
        """))
        conn.execute(text("CREATE TABLE tensor_agent_sessions (id INTEGER PRIMARY KEY, status TEXT, closed_at TEXT)"))
        conn.execute(text("INSERT INTO tensor_agent_sessions (id,status,closed_at) VALUES (1,'failed','2026-11-11T04:00:00+00:00'), (2,'active',NULL)"))
        stale = datetime.fromtimestamp(T0 - 7200, UTC).isoformat()
        fresh = datetime.fromtimestamp(T0 - 60, UTC).isoformat()
        created = datetime.fromtimestamp(T0 - 14400, UTC).isoformat()
        conn.execute(text(
            "INSERT INTO tensor_dev_missions (id,organization_id,status,created_at,heartbeat_at,"
            "task_key,result,budget,budget_used,session_id) VALUES "
            "(201,1,'running',:c,:h,'db-task-1','{}','{}','{}',1),"
            "(202,1,'running',:c,:h,'db-task-2','{}','{}','{}',2)"
        ), {"c": created, "h": stale})
        conn.execute(text("UPDATE tensor_dev_missions SET heartbeat_at = :f WHERE id = 202"), {"f": fresh})

    response = client.post("/api/v1/tensor/self-heal/run",
                           json={"now": T0, "window_id": "W-db-01", "dry_run": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scanned_missions"] == 2
    assert [d["mission_id"] for d in payload["detected"]] == [201]
    assert payload["classifications"][0]["cause"] == "infra_lost", payload["classifications"]
    assert payload["persisted_entries"] == 1


def test_api_refuses_to_append_onto_broken_chain():
    from sqlalchemy import text

    client, engine = _api_client()
    body = {"now": T0, "window_id": "W-api-02", "dry_run": True, "missions": _api_missions()}
    assert client.post("/api/v1/tensor/self-heal/run", json=body).status_code == 200
    with engine.begin() as conn:
        conn.execute(text("UPDATE self_heal_ledger SET action = 'tampered' WHERE seq = 1"))
    blocked = client.post("/api/v1/tensor/self-heal/run", json=body)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["error"] == "ledger_chain_broken"


def test_migration_and_api_ddl_column_parity():
    import re

    from api.tensor_self_heal import SCHEMA_DDL

    migration = (REPO / "backend/migrations/115_self_heal_ledger.sql").read_text(encoding="utf-8")
    table_sql = migration.split("CREATE TABLE IF NOT EXISTS self_heal_ledger", 1)[1].split("\n);", 1)[0]
    api_sql = SCHEMA_DDL.split("CREATE TABLE IF NOT EXISTS self_heal_ledger", 1)[1].split("\n)", 1)[0]

    def columns(ddl: str) -> set:
        found = set()
        for line in ddl.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith(("CONSTRAINT", ")", "--")):
                continue
            name = line.split()[0]
            if re.fullmatch(r"[a-z_]+", name):
                found.add(name)
        return found

    assert columns(table_sql) == columns(api_sql)


def test_acceptance_report_matches_generated_artifact():
    """The checked-in report (when present) must be reproducible from the code."""
    artifact = REPO / "artifacts/self_heal_report.json"
    if not artifact.is_file():
        pytest.skip("report artifact not generated in this checkout")
    stored = json.loads(artifact.read_text(encoding="utf-8"))
    fresh = run_acceptance(runs=10)
    for key in ("detection", "classification", "recovery", "idempotency", "criteria", "honest_fail"):
        assert stored[key] == fresh[key], f"{key} drifted between artifact and code"