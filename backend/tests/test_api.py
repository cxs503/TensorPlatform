# -*- coding: utf-8 -*-
"""
API 测试
测试 FastAPI 应用的 endpoints
"""
import os

# 导入应用
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 设置环境变量
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key"


def test_health_endpoint():
    """健康检查端点测试"""
    from main import app

    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "2.2.0"
    assert "services" in data


def test_root_endpoint():
    """根端点测试"""
    from main import app

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "开放数字化底座API"
    assert data["version"] == "2.2.0"


def test_docs_endpoint():
    """API 文档测试"""
    from main import app

    client = TestClient(app)
    response = client.get("/docs")

    # ReDoc 文档应该存在
    assert response.status_code == 200
    assert "redoc" in response.text.lower() or "docs" in response.text.lower()


def test_openapi_endpoint():
    """OpenAPI 规范测试"""
    from main import app

    client = TestClient(app)
    response = client.get("/openapi.json")

    # OpenAPI 规范应该返回有效的 JSON
    assert response.status_code == 200
    data = response.json()
    assert "openapi" in data
    assert "info" in data
    assert data["info"]["version"] == "2.2.0"
    assert "paths" in data
