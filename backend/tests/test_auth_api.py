# -*- coding: utf-8 -*-
"""
认证API端点测试 - 提升auth.py覆盖率
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["REFRESH_SECRET_KEY"] = "test-refresh-key-for-testing-only"


class TestAuthAPI:
    """认证API端点测试"""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """设置测试客户端"""
        from main import app
        self.client = TestClient(app)

    def test_register_endpoint(self):
        """测试用户注册端点"""
        response = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "testuser123",
                "email": "test123@example.com",
                "password": "SecurePass123!",
                "role": "user"
            }
        )
        # 注册可能成功或因重复用户名失败，但不应是500错误
        assert response.status_code in [200, 400, 422]

    def test_login_endpoint(self):
        """测试用户登录端点"""
        response = self.client.post(
            "/api/v1/auth/login",
            json={
                "username": "nonexistent_user",
                "password": "wrong_password"
            }
        )
        # 登录失败应返回401，而不是500
        assert response.status_code in [401, 422]

    def test_get_current_user_info_unauthorized(self):
        """测试获取当前用户信息 - 未授权"""
        response = self.client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_refresh_token_unauthorized(self):
        """测试刷新令牌 - 未授权"""
        response = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid_token"}
        )
        assert response.status_code in [401, 422]

    def test_logout_unauthorized(self):
        """测试登出 - 未授权"""
        response = self.client.post("/api/v1/auth/logout")
        assert response.status_code == 401

    def test_change_password_unauthorized(self):
        """测试修改密码 - 未授权"""
        response = self.client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "old_pass",
                "new_password": "NewPass123!"
            }
        )
        assert response.status_code == 401

    def test_verify_token_unauthorized(self):
        """测试验证令牌 - 未授权"""
        response = self.client.get("/api/v1/auth/verify")
        assert response.status_code == 401

    def test_sanitize_for_log_function(self):
        """测试日志脱敏函数"""
        from api.auth import sanitize_for_log
        
        # 短字符串
        assert sanitize_for_log("ab") == "***"
        assert sanitize_for_log("") == "***"
        
        # 长字符串
        result = sanitize_for_log("testuser123")
        assert result.startswith("te")
        assert result.endswith("3")
        assert "***" in result

    def test_token_blacklist_functions(self):
        """测试令牌黑名单函数"""
        from api.auth import add_token_to_blacklist, is_token_blacklisted
        import time
        
        test_token = "test_token_12345678901234567890123456789012"
        
        # 添加到黑名单（1秒过期）
        add_token_to_blacklist(test_token, 1)
        assert is_token_blacklisted(test_token) == True
        
        # 等待过期
        time.sleep(1.1)
        assert is_token_blacklisted(test_token) == False