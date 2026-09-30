# -*- coding: utf-8 -*-
"""
储能优化 API 测试
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key"


def test_storage_status_endpoint():
    """测试 /api/v1/storage/status 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.get("/api/v1/storage/status")
    assert response.status_code == 200
    data = response.json()
    assert "system_count" in data or "summary" in data


def test_storage_optimize_endpoint():
    """测试 /api/v1/storage/optimize 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.post(
        "/api/v1/storage/optimize",
        json={
            "entity_id": "test-entity",
            "strategy": "peak_shaving",
            "start_time": "2026-03-13T00:00:00",
            "end_time": "2026-03-13T23:59:59",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "schedule" in data or "strategy" in data
