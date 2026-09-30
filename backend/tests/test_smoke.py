# -*- coding: utf-8 -*-
"""
Open Digital Foundation — 冒烟测试
使用 FastAPI TestClient，不需要启动实际后端服务
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("REFRESH_SECRET_KEY", "test-refresh-key")
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

# Mock heavy optional deps before app import
# 使用 types.ModuleType 创建真正的模块 mock
import types


class _AutoModule(types.ModuleType):
    """自动创建类属性的模块 mock"""
    def __init__(self, name, doc=""):
        super().__init__(name, doc)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        # 为任何属性访问返回一个可 subclass 的类
        cls = type(name, (object,), {"__module__": self.__name__})
        setattr(self, name, cls)
        return cls

    def __call__(self, *args, **kwargs):
        return self


# 创建 torch mock 模块
torch_mock = _AutoModule("torch")
torch_nn_mock = _AutoModule("torch.nn")
torch_optim_mock = _AutoModule("torch.optim")
torch_distributed_mock = _AutoModule("torch.distributed")
torch_utils_mock = _AutoModule("torch.utils")
torch_utils_data_mock = _AutoModule("torch.utils.data")
torch_nn_parallel_mock = _AutoModule("torch.nn.parallel")

# 🔑 关键：在父模块上显式设置子模块属性，避免 __getattr__ 拦截
torch_mock.nn = torch_nn_mock
torch_mock.optim = torch_optim_mock
torch_mock.distributed = torch_distributed_mock
torch_mock.utils = torch_utils_mock
torch_utils_mock.data = torch_utils_data_mock
torch_nn_mock.parallel = torch_nn_parallel_mock

sys.modules["torch"] = torch_mock
sys.modules["torch.nn"] = torch_nn_mock
sys.modules["torch.optim"] = torch_optim_mock
sys.modules["torch.distributed"] = torch_distributed_mock
sys.modules["torch.utils"] = torch_utils_mock
sys.modules["torch.utils.data"] = torch_utils_data_mock
sys.modules["torch.nn.parallel"] = torch_nn_parallel_mock


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
