"""Tests for campaign_plans API: ingest, phases, nodes, progress, DAG.

Uses SQLite in-memory for isolation. Validates:
  - Ingest creates plan + milestones + nodes + edges
  - All phases returned with non-empty criteria
  - All nodes have non-empty acceptance_criteria
  - Progress endpoint returns numeric values
  - DAG endpoint returns edges
"""
import json
import os
import sys
import tempfile

import pytest

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── SQLite in-memory setup ──
os.environ["DATABASE_URL"] = "sqlite:///test_campaign_plans.db"

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database.config import get_db as _get_db, Base
from api.campaign_plans import router
from api.deps import require_auth, require_role

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


# ── Minimal master plan fixture ──
MASTER_PLAN = {
    "schema_version": "2.0.0",
    "mission_id": "m611",
    "title": "冰区航行战役全局主计划 (Test)",
    "created_utc": "2026-10-31T12:00:00Z",
    "owner": "2026-10-31",
    "physics_scope": ["level ice", "brash ice"],
    "coupling_framework_requirements": ["FEM", "DEM"],
    "validation_standards": ["ITTC 7.5-02-03.1"],
    "milestones": {
        "M0": {
            "name": "地基校验",
            "description": "Foundation Verification",
            "gate_criteria": ["F01 Hetényi 板挠度误差 < 5%", "F03 质量守恒误差 < 1e-12 kg"],
            "target_date": "2026-11-15",
            "status": "in_progress"
        },
        "M1": {
            "name": "单物理场标定",
            "description": "Single-Physics Calibration",
            "gate_criteria": ["F05 摩擦系数 vs 实验值误差 < 10%"],
            "target_date": "2026-12-15",
            "status": "planned"
        }
    },
    "nodes": [
        {
            "id": "F01",
            "source": "m605",
            "layer": "L1_foundation",
            "title": "Winkler 弹性地基板连续模型",
            "goal": "实现 Winkler 弹性地基板",
            "acceptance_criteria": [
                {"metric": "center_deflection_relative_error", "operator": "<", "threshold": 0.05, "unit": "ratio"},
                {"metric": "kw0_free_plate_error", "operator": "<", "threshold": 1e-10, "unit": "m"},
                {"metric": "pytest_test_winkler_plate", "operator": "==", "threshold": "PASS", "unit": "bool"}
            ],
            "depends_on": [],
            "milestone": "M0",
            "budget_hours": 16,
            "risk": "low",
            "physics_domain": "ice_mechanics"
        },
        {
            "id": "F02",
            "source": "m605",
            "layer": "L1_foundation",
            "title": "板-水附加质量集成到 FEM 质量矩阵",
            "goal": "将 water_added_mass 接入动力学质量矩阵",
            "acceptance_criteria": [
                {"metric": "natural_frequency_error_vs_fluid_solid", "operator": "<", "threshold": 0.05, "unit": "ratio"},
                {"metric": "mass_matrix_positive_definite", "operator": "==", "threshold": True, "unit": "bool"}
            ],
            "depends_on": ["F01"],
            "milestone": "M1",
            "budget_hours": 12,
            "risk": "medium",
            "physics_domain": "fluid_structure_interaction"
        }
    ],
    "dag_edges": [
        {"from": "F01", "to": "F02"}
    ],
    "statistics": {
        "total_nodes": 2,
        "milestones": 2,
        "sources": {"m605": 2},
        "layers": {"L1_foundation": 2},
        "risk_distribution": {"low": 1, "medium": 1}
    }
}


@pytest.fixture(scope="module")
def test_app():
    """Create a test FastAPI app with SQLite backend."""
    engine = create_engine("sqlite:///test_campaign_plans.db", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    with engine.begin() as conn:
        # Minimal organizations table
        conn.execute(text("CREATE TABLE IF NOT EXISTS organizations (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT OR IGNORE INTO organizations (id, name) VALUES (1, 'test')"))
        # Campaign plan tables
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

    # Override auth
    def override_auth():
        return {"id": 1, "username": "test", "role": "admin", "organization_id": 1}

    def override_role(*roles):
        def checker():
            return override_auth()
        return checker

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_db] = override_get_db
    app.dependency_overrides[require_auth] = override_auth
    app.dependency_overrides[require_role] = lambda *a, **kw: override_auth

    yield app, engine

    # Cleanup
    engine.dispose()
    try:
        os.unlink("test_campaign_plans.db")
    except FileNotFoundError:
        pass


@pytest.fixture(scope="module")
def client(test_app):
    app, _ = test_app
    return TestClient(app)


@pytest.fixture(scope="module")
def ingested_plan(client):
    """Ingest the master plan and return the response."""
    resp = client.post("/tensor/campaign-plans/ingest", json={"plan_json": MASTER_PLAN})
    assert resp.status_code == 200, f"Ingest failed: {resp.text}"
    return resp.json()


class TestCampaignPlansIngest:
    def test_ingest_returns_plan_id(self, ingested_plan):
        assert "plan_id" in ingested_plan
        assert isinstance(ingested_plan["plan_id"], int)

    def test_ingest_counts(self, ingested_plan):
        assert ingested_plan["milestones_ingested"] == 2
        assert ingested_plan["nodes_ingested"] == 2
        assert ingested_plan["edges_ingested"] == 1

    def test_ingest_source_mission(self, ingested_plan):
        assert ingested_plan["source_mission_id"] == "m611"


class TestCampaignPlansList:
    def test_list_plans(self, client, ingested_plan):
        resp = client.get("/tensor/campaign-plans")
        assert resp.status_code == 200
        data = resp.json()
        assert "plans" in data
        assert len(data["plans"]) >= 1
        plan = data["plans"][0]
        assert plan["source_mission_id"] == "m611"
        assert plan["milestone_count"] == 2
        assert plan["node_count"] == 2
        assert plan["edge_count"] == 1


class TestCampaignPlansPhases:
    def test_phases_returned(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/phases")
        assert resp.status_code == 200
        data = resp.json()
        assert "phases" in data
        assert len(data["phases"]) == 2

    def test_phases_criteria_non_empty(self, client, ingested_plan):
        """Machine criterion: all phases have non-empty gate_criteria."""
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/phases")
        data = resp.json()
        for phase in data["phases"]:
            assert phase["gate_criteria"] is not None
            assert len(phase["gate_criteria"]) > 0, f"Phase {phase['milestone_id']} has empty gate_criteria"

    def test_phases_have_milestone_ids(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/phases")
        data = resp.json()
        ids = [p["milestone_id"] for p in data["phases"]]
        assert "M0" in ids
        assert "M1" in ids


class TestCampaignPlansNodes:
    def test_nodes_returned(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert len(data["nodes"]) == 2

    def test_nodes_criteria_non_empty(self, client, ingested_plan):
        """Machine criterion: all nodes have non-empty acceptance_criteria."""
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/nodes")
        data = resp.json()
        for node in data["nodes"]:
            assert node["acceptance_criteria"] is not None
            assert len(node["acceptance_criteria"]) > 0, f"Node {node['node_id']} has empty acceptance_criteria"

    def test_nodes_filter_by_milestone(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/nodes?milestone=M0")
        data = resp.json()
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["node_id"] == "F01"

    def test_nodes_filter_by_layer(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/nodes?layer=L1_foundation")
        data = resp.json()
        assert len(data["nodes"]) == 2


class TestCampaignPlansProgress:
    def test_progress_returns_numbers(self, client, ingested_plan):
        """Machine criterion: progress endpoint returns numeric values."""
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/progress")
        assert resp.status_code == 200
        data = resp.json()
        # All these must be numeric
        assert isinstance(data["total_nodes"], int)
        assert isinstance(data["total_criteria"], int)
        assert isinstance(data["nodes_without_criteria"], int)
        assert isinstance(data["criteria_coverage_pct"], (int, float))
        assert isinstance(data["completed_nodes"], int)
        assert isinstance(data["in_progress_nodes"], int)
        assert isinstance(data["planned_nodes"], int)
        assert isinstance(data["completion_pct"], (int, float))
        assert isinstance(data["total_budget_hours"], (int, float))
        assert isinstance(data["dag_edges"], int)

    def test_progress_values_correct(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/progress")
        data = resp.json()
        assert data["total_nodes"] == 2
        assert data["total_criteria"] == 5  # F01 has 3, F02 has 2
        assert data["nodes_without_criteria"] == 0
        assert data["criteria_coverage_pct"] == 100.0
        assert data["planned_nodes"] == 2
        assert data["completion_pct"] == 0.0
        assert data["total_budget_hours"] == 28.0  # 16 + 12
        assert data["dag_edges"] == 1

    def test_progress_milestone_breakdown(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/progress")
        data = resp.json()
        assert "milestone_progress" in data
        assert len(data["milestone_progress"]) == 2
        ms0 = next(m for m in data["milestone_progress"] if m["milestone_id"] == "M0")
        assert ms0["total_nodes"] == 1
        assert ms0["gate_criteria_count"] == 2

    def test_progress_risk_distribution(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/progress")
        data = resp.json()
        assert data["risk_distribution"]["low"] == 1
        assert data["risk_distribution"]["medium"] == 1

    def test_progress_layer_distribution(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/progress")
        data = resp.json()
        assert data["layer_distribution"]["L1_foundation"] == 2


class TestCampaignPlansDAG:
    def test_dag_edges_returned(self, client, ingested_plan):
        pid = ingested_plan["plan_id"]
        resp = client.get(f"/tensor/campaign-plans/{pid}/dag")
        assert resp.status_code == 200
        data = resp.json()
        assert "edges" in data
        assert len(data["edges"]) == 1
        assert data["edges"][0]["from_node"] == "F01"
        assert data["edges"][0]["to_node"] == "F02"


class TestCampaignPlansIdempotent:
    def test_reingest_is_idempotent(self, client, ingested_plan):
        """Re-ingesting the same plan should not duplicate data."""
        pid1 = ingested_plan["plan_id"]
        resp = client.post("/tensor/campaign-plans/ingest", json={"plan_json": MASTER_PLAN})
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_id"] == pid1
        assert data["idempotent"] is True

        # Verify no duplicates
        resp2 = client.get(f"/tensor/campaign-plans/{pid1}/nodes")
        assert len(resp2.json()["nodes"]) == 2

        resp3 = client.get(f"/tensor/campaign-plans/{pid1}/phases")
        assert len(resp3.json()["phases"]) == 2
