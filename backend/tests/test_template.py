# -*- coding: utf-8 -*-
"""
通用API测试模板
使用方法：
1. 复制此文件为 test_<module_name>.py
2. 替换 MODULE_NAME 为实际模块名
3. 根据实际API端点调整测试URL和请求数据
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key"


def test_MODULE_NAME_list_endpoint():
    """测试 /api/v1/MODULE_NAME/list 端点"""
    from main import app
    
    client = TestClient(app)
    response = client.get("/api/v1/MODULE_NAME/list")
    # 根据实际实现调整预期状态码
    assert response.status_code in [200, 401, 404]


def test_MODULE_NAME_create_endpoint():
    """测试 /api/v1/MODULE_NAME/create 端点"""
    from main import app
    
    client = TestClient(app)
    response = client.post(
        "/api/v1/MODULE_NAME/create",
        json={
            "name": "test_item",
            "description": "Test item description"
        }
    )
    # 根据实际实现调整预期状态码
    assert response.status_code in [200, 400, 401, 422]


def test_MODULE_NAME_get_endpoint():
    """测试 /api/v1/MODULE_NAME/{id} 端点"""
    from main import app
    
    client = TestClient(app)
    response = client.get("/api/v1/MODULE_NAME/1")
    # 根据实际实现调整预期状态码
    assert response.status_code in [200, 401, 404]


def test_MODULE_NAME_update_endpoint():
    """测试 /api/v1/MODULE_NAME/update 端点"""
    from main import app
    
    client = TestClient(app)
    response = client.put(
        "/api/v1/MODULE_NAME/update",
        json={
            "id": 1,
            "name": "updated_test_item"
        }
    )
    # 根据实际实现调整预期状态码
    assert response.status_code in [200, 400, 401, 404, 422]


def test_MODULE_NAME_delete_endpoint():
    """测试 /api/v1/MODULE_NAME/delete 端点"""
    from main import app
    
    client = TestClient(app)
    response = client.delete("/api/v1/MODULE_NAME/delete?id=1")
    # 根据实际实现调整预期状态码
    assert response.status_code in [200, 401, 404]