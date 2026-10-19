"""
测试新模块: organizations, metering, data-assets, evaluation-center, knowledge-graph
"""
import pytest
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Use in-memory SQLite for tests
os.environ["DATABASE_URL"] = "sqlite:///./test_new_modules.db"

from main import app
from database.config import engine, Base

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


class TestOrganizations:
    def test_create_org(self):
        r = client.post("/api/v1/organizations", json={"name": "测试企业", "industry": "制造业", "plan": "free"})
        assert r.status_code == 200
        assert r.json()["name"] == "测试企业"

    def test_list_orgs(self):
        client.post("/api/v1/organizations", json={"name": "企业A"})
        r = client.get("/api/v1/organizations")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_get_org(self):
        r = client.post("/api/v1/organizations", json={"name": "企业B"})
        oid = r.json()["id"]
        r = client.get(f"/api/v1/organizations/{oid}")
        assert r.status_code == 200

    def test_stats(self):
        client.post("/api/v1/organizations", json={"name": "S1", "industry": "制造业"})
        r = client.get("/api/v1/organizations/stats")
        assert r.status_code == 200
        assert "total" in r.json()


class TestMetering:
    def test_log_call(self):
        r = client.post("/api/v1/metering/log", json={
            "endpoint": "/test", "org_id": 1, "latency_ms": 12.5
        })
        assert r.status_code == 200

    def test_stats(self):
        client.post("/api/v1/metering/log", json={"endpoint": "/a", "org_id": 1})
        r = client.get("/api/v1/metering/stats")
        assert r.status_code == 200
        assert r.json()["total_calls"] >= 1


class TestDataAssets:
    def test_create(self):
        r = client.post("/api/v1/data-assets", json={"name": "行业月报", "category": "能源", "price": 10000})
        assert r.status_code == 200
        assert r.json()["name"] == "行业月报"

    def test_list(self):
        client.post("/api/v1/data-assets", json={"name": "P1"})
        r = client.get("/api/v1/data-assets")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_publish(self):
        r = client.post("/api/v1/data-assets", json={"name": "P2"})
        aid = r.json()["id"]
        r = client.put(f"/api/v1/data-assets/{aid}/publish")
        assert r.status_code == 200


class TestEvaluationCenter:
    def test_baselines(self):
        r = client.get("/api/v1/evaluation-center/baselines")
        assert r.status_code == 200
        assert r.json()["total"] == 9


class TestKnowledgeGraph:
    def test_domains(self):
        r = client.get("/api/v1/knowledge-graph/domains")
        assert r.status_code == 200
        assert len(r.json()["domains"]) >= 3

    def test_search(self):
        r = client.get("/api/v1/knowledge-graph/search?keyword=温度")
        assert r.status_code == 200

    def test_match(self):
        r = client.post("/api/v1/knowledge-graph/match", json={"values": {"temperature": 460}})
        assert r.status_code == 200
        assert r.json()["total"] >= 1
