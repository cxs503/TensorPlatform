"""Integration test: ingest the real master_plan.json from m611 and validate machine criteria."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.campaign_plans import router
from api.deps import require_auth, require_role
from database.config import get_db as _get_db

PLAN_PATH = "/srv/tensor/agent/work/m611/artifacts/master_plan.json"


@pytest.fixture(scope="module")
def full_app():
    engine = create_engine("sqlite:///test_full_campaign.db", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS organizations (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT OR IGNORE INTO organizations (id, name) VALUES (1, 'test')"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS campaign_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id BIGINT NOT NULL,
                source_mission_id TEXT NOT NULL,
                schema_version TEXT NOT NULL DEFAULT '2.0.0',
                title TEXT NOT NULL,
                created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                owner TEXT,
                physics_scope TEXT NOT NULL DEFAULT '[]',
                coupling_framework_requirements TEXT NOT NULL DEFAULT '[]',
                validation_standards TEXT NOT NULL DEFAULT '[]',
                statistics TEXT NOT NULL DEFAULT '{}',
                ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(organization_id, source_mission_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS campaign_plan_milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_plan_id INTEGER NOT NULL,
                milestone_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                gate_criteria TEXT NOT NULL DEFAULT '[]',
                target_date TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                UNIQUE(campaign_plan_id, milestone_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS campaign_plan_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_plan_id INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                source TEXT NOT NULL,
                layer TEXT NOT NULL,
                title TEXT NOT NULL,
                goal TEXT NOT NULL DEFAULT '',
                acceptance_criteria TEXT NOT NULL DEFAULT '[]',
                depends_on TEXT NOT NULL DEFAULT '[]',
                milestone TEXT NOT NULL,
                budget_hours REAL NOT NULL DEFAULT 0,
                risk TEXT NOT NULL DEFAULT 'medium',
                physics_domain TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned',
                UNIQUE(campaign_plan_id, node_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS campaign_plan_dag_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_plan_id INTEGER NOT NULL,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                UNIQUE(campaign_plan_id, from_node, to_node)
            )
        """))

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.rollback()
            db.close()

    def override_auth():
        return {"id": 1, "username": "test", "role": "admin", "organization_id": 1}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_db] = override_get_db
    app.dependency_overrides[require_auth] = override_auth
    app.dependency_overrides[require_role] = lambda *a, **kw: override_auth

    yield app, engine

    engine.dispose()
    try:
        os.unlink("test_full_campaign.db")
    except FileNotFoundError:
        pass


@pytest.fixture(scope="module")
def full_client(full_app):
    app, _ = full_app
    return TestClient(app)


@pytest.fixture(scope="module")
def full_plan(full_client):
    """Ingest the real master_plan.json."""
    if not os.path.exists(PLAN_PATH):
        pytest.skip(f"master_plan.json not found at {PLAN_PATH}")
    with open(PLAN_PATH) as f:
        plan_json = json.load(f)
    resp = full_client.post("/tensor/campaign-plans/ingest", json={"plan_json": plan_json})
    assert resp.status_code == 200, f"Ingest failed: {resp.text}"
    return resp.json()


class TestFullMasterPlan:
    def test_ingest_full_plan(self, full_plan):
        assert full_plan["source_mission_id"] == "m611"
        assert full_plan["milestones_ingested"] == 6
        assert full_plan["nodes_ingested"] == 27
        # Actual dag_edges count in the JSON is 34 (statistics says 33, but data has 34)
        assert full_plan["edges_ingested"] == 34

    def test_all_phases_returned(self, full_client, full_plan):
        """Machine criterion: API returns ALL phases."""
        pid = full_plan["plan_id"]
        resp = full_client.get(f"/tensor/campaign-plans/{pid}/phases")
        data = resp.json()
        assert len(data["phases"]) == 6
        phase_ids = [p["milestone_id"] for p in data["phases"]]
        assert set(phase_ids) == {"M0", "M1", "M2", "M3", "M4", "M5"}

    def test_all_phases_criteria_non_empty(self, full_client, full_plan):
        """Machine criterion: all phases have non-empty gate_criteria."""
        pid = full_plan["plan_id"]
        resp = full_client.get(f"/tensor/campaign-plans/{pid}/phases")
        data = resp.json()
        for phase in data["phases"]:
            assert phase["gate_criteria"] is not None
            assert len(phase["gate_criteria"]) > 0, f"Phase {phase['milestone_id']} has empty gate_criteria"

    def test_all_nodes_criteria_non_empty(self, full_client, full_plan):
        """Machine criterion: all nodes have non-empty acceptance_criteria."""
        pid = full_plan["plan_id"]
        resp = full_client.get(f"/tensor/campaign-plans/{pid}/nodes")
        data = resp.json()
        assert len(data["nodes"]) == 27
        for node in data["nodes"]:
            assert node["acceptance_criteria"] is not None
            assert len(node["acceptance_criteria"]) > 0, f"Node {node['node_id']} has empty acceptance_criteria"

    def test_progress_returns_numbers(self, full_client, full_plan):
        """Machine criterion: progress endpoint returns numeric values."""
        pid = full_plan["plan_id"]
        resp = full_client.get(f"/tensor/campaign-plans/{pid}/progress")
        data = resp.json()
        assert data["total_nodes"] == 27
        assert data["total_criteria"] > 0
        assert data["nodes_without_criteria"] == 0
        assert data["criteria_coverage_pct"] == 100.0
        assert data["completion_pct"] == 0.0  # all planned
        assert data["dag_edges"] == 34
        assert data["total_budget_hours"] > 0

    def test_progress_milestone_breakdown(self, full_client, full_plan):
        pid = full_plan["plan_id"]
        resp = full_client.get(f"/tensor/campaign-plans/{pid}/progress")
        data = resp.json()
        assert len(data["milestone_progress"]) == 6
        for ms in data["milestone_progress"]:
            assert ms["gate_criteria_count"] > 0, f"Milestone {ms['milestone_id']} has 0 gate criteria"
            assert ms["total_nodes"] > 0, f"Milestone {ms['milestone_id']} has 0 nodes"

    def test_dag_edges(self, full_client, full_plan):
        pid = full_plan["plan_id"]
        resp = full_client.get(f"/tensor/campaign-plans/{pid}/dag")
        data = resp.json()
        assert len(data["edges"]) == 34
