# -*- coding: utf-8 -*-
"""
仪表盘 API 测试
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key"


def test_dashboard_statistics_endpoint():
    """测试 /api/v1/dashboard/statistics 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.get("/api/v1/dashboard/statistics")
    assert response.status_code == 200
    data = response.json()
    assert "projects" in data
    assert "datasets" in data
    assert "models" in data


def test_dashboard_recent_activities_endpoint():
    """测试 /api/v1/dashboard/recent-activities 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.get("/api/v1/dashboard/recent-activities?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)
