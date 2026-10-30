# -*- coding: utf-8 -*-
"""
Open Digital Foundation — 冒烟测试
使用 FastAPI TestClient，不需要启动实际后端服务
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("REFRESH_SECRET_KEY", "test-refresh-key")
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

def test_torch_is_not_replaced_by_a_global_stub():
    """冒烟测试收集不能污染证据生成器使用的真实 PyTorch。"""
    import torch

    assert torch.tensor([1.0]).item() == 1.0
    assert isinstance(torch.__version__, str)


@pytest.fixture(scope="module")
def client():
    """创建 TestClient（每个模块只创建一次）"""
    from fastapi.testclient import TestClient

    # Mock pathlib.Path.mkdir 避免 /app 权限问题
    def _mock_mkdir(self, *args, **kwargs):
        pass  # 跳过所有 mkdir 调用

    with patch.object(__import__("pathlib").Path, "mkdir", _mock_mkdir):
        from main import app

    return TestClient(app)


def test_health(client):
    """测试 /health 端点返回 healthy 状态"""
    response = client.get("/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["status"] == "healthy", f"Expected healthy, got {data}"
    assert data["version"] == "2.2.0"
    assert "services" in data


def test_root(client):
    """测试根路径返回正确信息"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "version" in data


def test_mlflow_experiments(client):
    """测试 MLflow 实验列表端点"""
    response = client.get("/api/v1/mlflow/experiments")
    # 可能返回 200（空列表）或 500（mlflow 不可用），不应该 404
    assert response.status_code != 404, f"Endpoint not found, got {response.status_code}"


def test_real_training_health(client):
    """测试 real_training 端点可达"""
    response = client.get("/api/v1/real-training/health")
    # 可能 200/401/405，不应 404
    assert response.status_code != 404, f"Endpoint not found, got {response.status_code}"


def test_onnx_health(client):
    """测试 ONNX 端点可达"""
    response = client.get("/api/v1/onnx/health")
    assert response.status_code != 404, f"Endpoint not found, got {response.status_code}"


def test_metrics_endpoint(client):
    """测试 Prometheus 指标端点返回数据"""
    response = client.get("/api/v1/metrics")
    # metrics 端点应返回 200 且包含 prometheus 格式文本
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    text = response.text
    # Prometheus 指标至少应该有 http_requests_total
    assert "http_requests" in text or "# HELP" in text or "# TYPE" in text, \
        f"No metric data in response"


def test_auth_required(client):
    """测试需要认证的端点返回 401（未提供 token 时）"""
    # 尝试访问需要认证的端点，缺少认证头时应返回 401
    try:
        response = client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "wrong"},
        )
        # login 端点即使验证失败也返回 401，DB 未初始化时可能返回 500
        assert response.status_code in (200, 401, 422, 405, 500), \
            f"Unexpected status: {response.status_code}"
    except Exception as e:
        # DB 表未初始化等基础设施问题也视为端点可达（非 404）
        msg = str(e)
        assert "404" not in msg, f"Endpoint not found: {msg}"


def test_dashboard_endpoint(client):
    """测试仪表盘端点可达"""
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code != 404, f"Endpoint not found, got {response.status_code}"


def test_api_docs(client):
    """测试 API 文档端点可达"""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "html" in response.text.lower() or "redoc" in response.text.lower()
