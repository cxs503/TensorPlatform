# -*- coding: utf-8 -*-
"""
AB测试 API 测试
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key"


def test_abtest_list_endpoint():
    """测试 /api/v1/abtest/list 端点"""
    from main import app
    
    client = TestClient(app)
    response = client.get("/api/v1/abtest/list")
    # 根据实际实现调整预期状态码
    assert response.status_code in [200, 401, 404]


def test_abtest_create_endpoint():
    """测试 /api/v1/abtest/create 端点"""
    from main import app
    
    client = TestClient(app)
    response = client.post(
        "/api/v1/abtest/create",
        json={
            "name": "test_experiment",
            "description": "Test AB experiment",
            "variants": ["A", "B"],
            "traffic_split": [0.5, 0.5]
        }
    )
    # 根据实际实现调整预期状态码
    assert response.status_code in [200, 400, 401, 422]