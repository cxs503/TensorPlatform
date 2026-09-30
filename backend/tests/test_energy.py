# -*- coding: utf-8 -*-
"""
能源生成 API 测试
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key"


def test_photovoltaic_list_systems_endpoint():
    """测试 /api/v1/photovoltaic/systems 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.get("/api/v1/photovoltaic/systems")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_storage_list_systems_endpoint():
    """测试 /api/v1/storage/systems 端点返回预期结构"""
    from main import app

    client = TestClient(app)
    response = client.get("/api/v1/storage/systems")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_energy_system_count():
    """测试能源系统数量非零"""
    from main import app

    client = TestClient(app)
    
    pv_response = client.get("/api/v1/photovoltaic/systems")
    storage_response = client.get("/api/v1/storage/systems")
    
    assert pv_response.status_code == 200
    assert storage_response.status_code == 200
    
    pv_data = pv_response.json()
    storage_data = storage_response.json()
    
    assert len(pv_data) > 0
    assert len(storage_data) > 0