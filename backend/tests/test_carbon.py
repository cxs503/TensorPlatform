# -*- coding: utf-8 -*-
"""
碳排放 API 测试
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key"


def test_carbon_list_factors_endpoint():
    """测试 /api/v1/carbon/factors 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.get("/api/v1/carbon/factors")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_carbon_calculate_endpoint():
    """测试 /api/v1/carbon/calculate 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.post(
        "/api/v1/carbon/calculate",
        json={
            "entity_id": "test-entity",
            "scope": 1,
            "source": "energy",
            "activity_data": 100,
            "factor_name": "grid_electricity",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "emission" in data
    assert data["emission"] > 0
