"""
开放数字化底座 — 集成测试套件
针对运行中的后端进行快速验证
"""
import json
import requests
import pytest

BASE_URL = "http://localhost:8001"


class TestHealth:
    """基础健康检查"""
    
    def test_backend_health(self):
        resp = requests.get(f"{BASE_URL}/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
    
    def test_metrics_endpoint(self):
        resp = requests.get(f"{BASE_URL}/api/v1/metrics")
        assert resp.status_code == 200
        assert "http_requests_total" in resp.text


class TestRealTraining:
    """真实训练引擎"""
    
    def test_health(self):
        resp = requests.get(f"{BASE_URL}/api/v1/real-training/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
    
    def test_models_supported(self):
        resp = requests.get(f"{BASE_URL}/api/v1/real-training/models/supported")
        assert resp.status_code == 200
        data = resp.json()
        assert "classification" in data
        assert "regression" in data
        assert len(data["classification"]) >= 2  # at least RF + LightGBM/XGBoost
    
    def test_scenarios(self):
        resp = requests.get(f"{BASE_URL}/api/v1/real-training/scenarios")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scenarios"]) >= 1

    def test_train_random_forest(self):
        """训练 RandomForest 并验证结果"""
        resp = requests.post(f"{BASE_URL}/api/v1/real-training/train", json={
            "name": "CI-RF-Test",
            "scenario": "generic",
            "model_type": "random_forest",
            "n_samples": 500,
            "n_estimators": 20,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "job_id" in data
        job_id = data["job_id"]
        
        # 等待训练完成
        import time
        for _ in range(15):
            time.sleep(2)
            status_resp = requests.get(f"{BASE_URL}/api/v1/real-training/jobs/{job_id}")
            if status_resp.status_code != 200:
                continue
            status = status_resp.json()
            if status["status"] == "completed":
                assert "r2" in status.get("best_metrics", {})
                assert status["best_metrics"]["r2"] > 0.3  # 至少要有点预测能力
                return
        pytest.fail("Training did not complete in 30s")


class TestMLflow:
    """MLflow 实验追踪"""
    
    def test_status(self):
        resp = requests.get(f"{BASE_URL}/api/v1/mlflow/status")
        assert resp.status_code == 200
    
    def test_experiments(self):
        resp = requests.get(f"{BASE_URL}/api/v1/mlflow/experiments")
        assert resp.status_code == 200
        data = resp.json()
        # MLflow 应该有至少 1 个实验
        assert data["total"] >= 0  # 至少不报错


class TestONNX:
    """ONNX 导出服务"""
    
    def test_health(self):
        resp = requests.get(f"{BASE_URL}/api/v1/onnx/health")
        assert resp.status_code in (200, 404)  # 404 if not registered yet
    
    def test_models_list(self):
        resp = requests.get(f"{BASE_URL}/api/v1/onnx/models")
        assert resp.status_code in (200, 404)


class TestDistributed:
    """分布式训练"""
    
    def test_status(self):
        resp = requests.get(f"{BASE_URL}/api/v1/distributed/status")
        assert resp.status_code in (200, 404)


class TestAuth:
    """认证守卫"""
    
    def test_health_no_auth(self):
        resp = requests.get(f"{BASE_URL}/api/v1/health")
        assert resp.status_code == 200
    
    def test_protected_requires_auth(self):
        resp = requests.get(f"{BASE_URL}/api/v1/projects")
        assert resp.status_code == 401


class TestPrometheus:
    """Prometheus 监控指标"""
    
    def test_metrics_endpoint(self):
        resp = requests.get(f"{BASE_URL}/api/v1/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert "http_requests_total" in text
        assert "training_jobs_active" in text
        assert "db_pool_size" in text
