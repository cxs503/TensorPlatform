"""Core API tests for Open Digital Foundation"""
import pytest

class TestHealth:
    def test_health(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

class TestAuth:
    def test_login_success(self, client):
        r = client.post("/api/v1/auth/login", json={"username":"admin","password":"CHANGE_ME_ADMIN"})
        assert r.status_code == 200
        assert "access_token" in r.json()
    
    def test_login_fail(self, client):
        r = client.post("/api/v1/auth/login", json={"username":"admin","password":"wrong"})
        assert r.status_code in [401, 403, 422]

class TestAgent:
    def test_tools(self, client):
        r = client.get("/api/v1/agent/tools")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 22
    
    def test_chat_basic(self, client):
        r = client.post("/api/v1/agent/chat", json={"session_id":"t1","messages":[{"role":"user","content":"hi"}]})
        assert r.status_code == 200
        assert "content" in r.json() or "reply" in r.json()
    
    def test_chat_dataset_query(self, client):
        r = client.post("/api/v1/agent/chat", json={"session_id":"t2","messages":[{"role":"user","content":"列出数据集"}]})
        assert r.status_code == 200

class TestData:
    def test_datasets(self, client, auth_headers):
        r = client.get("/api/v1/datasets/", headers=auth_headers)
        assert r.status_code == 200
    
    def test_data_quality(self, client, auth_headers):
        r = client.get("/api/v1/quality/status", headers=auth_headers)
        assert r.status_code in [200, 404]  # May or may not exist

class TestFeatureStore:
    def test_status(self, client):
        r = client.get("/api/v1/feature-store/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "active"
        assert data["total_groups"] >= 3
    
    def test_feature_groups(self, client):
        r = client.get("/api/v1/feature-store/feature-groups")
        assert r.status_code == 200

class TestRAG:
    def test_status(self, client):
        r = client.get("/api/v1/rag/status")
        assert r.status_code in [200, 401]  # Auth optional
    
    def test_search(self, client, auth_headers):
        r = client.post("/api/v1/rag/search", json={"query":"数据质量","top_k":3}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "results" in data

class TestCV:
    def test_cv_status(self, client):
        r = client.get("/api/v1/cv/status")
        assert r.status_code == 200
        assert r.json()["torch_available"] == True

class TestAudio:
    def test_audio_status(self, client):
        r = client.get("/api/v1/audio/status")
        assert r.status_code == 200
        assert r.json()["torch_available"] == True

class TestNLP:
    def test_nlp_status(self, client):
        r = client.get("/api/v1/nlp/status")
        assert r.status_code == 200

class TestRL:
    def test_rl_status(self, client):
        r = client.get("/api/v1/rl/status")
        assert r.status_code == 200

class TestExperiments:
    def test_experiments_list(self, client):
        r = client.get("/api/v1/experiments/")
        assert r.status_code == 200

class TestAutoML:
    def test_automl_status(self, client):
        r = client.get("/api/v1/automl/status")
        assert r.status_code in [200, 404]
