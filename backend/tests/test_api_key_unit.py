# -*- coding: utf-8 -*-
"""
API密钥单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from api.api_key import (
    create_api_key,
    list_api_keys,
    get_api_key,
    revoke_api_key,
    delete_api_key,
    get_key_usage,
    verify_api_key
)
from api.api_key import CreateAPIKeyReq


class TestAPIKeyUnit:
    """API密钥单元测试"""

    @pytest.fixture
    def mock_api_key_manager(self):
        """模拟API密钥管理器"""
        with patch('api.api_key.get_api_key_manager') as mock_get_manager:
            mock_manager = Mock()
            mock_get_manager.return_value = mock_manager
            yield mock_manager

    @pytest.fixture
    def mock_current_user(self):
        """模拟当前用户"""
        return {"user_id": 123, "username": "testuser"}

    def test_create_api_key_success(self, mock_api_key_manager, mock_current_user):
        """测试成功创建API密钥"""
        mock_api_key_manager.generate_key.return_value = ("key123", "secret-key-value")
        
        req = CreateAPIKeyReq(
            name="test-key",
            permissions=["read", "write"],
            rate_limit=50,
            expires_days=30
        )
        result = create_api_key(req, mock_current_user)
        assert result["key_id"] == "key123"
        assert result["key"] == "secret-key-value"
        assert result["name"] == "test-key"
        assert "message" in result
        mock_api_key_manager.generate_key.assert_called_once_with(
            name="test-key",
            user_id=123,
            permissions=["read", "write"],
            rate_limit=50,
            days=30
        )

    def test_list_api_keys(self, mock_api_key_manager, mock_current_user):
        """测试列出API密钥"""
        mock_keys = [
            {"key_id": "key1", "name": "key1"},
            {"key_id": "key2", "name": "key2"}
        ]
        mock_api_key_manager.list_keys.return_value = mock_keys
        
        result = list_api_keys(mock_current_user)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        mock_api_key_manager.list_keys.assert_called_once_with(123)

    def test_get_api_key_exists(self, mock_api_key_manager, mock_current_user):
        """测试获取存在的密钥"""
        mock_key = Mock()
        mock_key.key_id = "key123"
        mock_key.name = "test-key"
        mock_key.permissions = ["read"]
        mock_key.rate_limit = 100
        mock_key.expires_at = datetime(2027, 1, 1)
        mock_key.created_at = datetime(2026, 1, 1)
        mock_key.last_used = datetime(2026, 4, 7)
        mock_key.is_active = True
        mock_key.usage_count = 42
        mock_key.user_id = 123  # Same as current user
        mock_api_key_manager.get_key.return_value = mock_key
        
        result = get_api_key("key123", mock_current_user)
        assert result["key_id"] == "key123"
        assert result["name"] == "test-key"
        assert result["permissions"] == ["read"]
        assert result["is_active"] is True
        mock_api_key_manager.get_key.assert_called_once_with("key123")

    def test_get_api_key_not_exists(self, mock_api_key_manager, mock_current_user):
        """测试获取不存在的密钥"""
        mock_api_key_manager.get_key.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_api_key("nonexistent", mock_current_user)
        assert exc_info.value.status_code == 404

    def test_get_api_key_permission_denied(self, mock_api_key_manager, mock_current_user):
        """测试无权限访问密钥"""
        mock_key = Mock()
        mock_key.user_id = 456  # Different user
        mock_api_key_manager.get_key.return_value = mock_key
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_api_key("key123", mock_current_user)
        assert exc_info.value.status_code == 403

    def test_revoke_api_key_exists(self, mock_api_key_manager, mock_current_user):
        """测试撤销存在的密钥"""
        mock_key = Mock()
        mock_key.user_id = 123  # Same as current user
        mock_api_key_manager.get_key.return_value = mock_key
        mock_api_key_manager.revoke_key.return_value = True
        
        result = revoke_api_key("key123", mock_current_user)
        assert result["message"] == "密钥已撤销"
        mock_api_key_manager.revoke_key.assert_called_once_with("key123")

    def test_revoke_api_key_not_exists(self, mock_api_key_manager, mock_current_user):
        """测试撤销不存在的密钥"""
        mock_api_key_manager.get_key.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            revoke_api_key("nonexistent", mock_current_user)
        assert exc_info.value.status_code == 404

    def test_revoke_api_key_permission_denied(self, mock_api_key_manager, mock_current_user):
        """测试无权限撤销密钥"""
        mock_key = Mock()
        mock_key.user_id = 456  # Different user
        mock_api_key_manager.get_key.return_value = mock_key
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            revoke_api_key("key123", mock_current_user)
        assert exc_info.value.status_code == 403

    def test_revoke_api_key_failure(self, mock_api_key_manager, mock_current_user):
        """测试撤销密钥失败"""
        mock_key = Mock()
        mock_key.user_id = 123
        mock_api_key_manager.get_key.return_value = mock_key
        mock_api_key_manager.revoke_key.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            revoke_api_key("key123", mock_current_user)
        assert exc_info.value.status_code == 500

    def test_delete_api_key_exists(self, mock_api_key_manager, mock_current_user):
        """测试删除存在的密钥"""
        mock_key = Mock()
        mock_key.user_id = 123  # Same as current user
        mock_api_key_manager.get_key.return_value = mock_key
        mock_api_key_manager.delete_key.return_value = True
        
        result = delete_api_key("key123", mock_current_user)
        assert result["message"] == "密钥已删除"
        mock_api_key_manager.delete_key.assert_called_once_with("key123")

    def test_delete_api_key_not_exists(self, mock_api_key_manager, mock_current_user):
        """测试删除不存在的密钥"""
        mock_api_key_manager.get_key.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_api_key("nonexistent", mock_current_user)
        assert exc_info.value.status_code == 404

    def test_delete_api_key_permission_denied(self, mock_api_key_manager, mock_current_user):
        """测试无权限删除密钥"""
        mock_key = Mock()
        mock_key.user_id = 456  # Different user
        mock_api_key_manager.get_key.return_value = mock_key
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_api_key("key123", mock_current_user)
        assert exc_info.value.status_code == 403

    def test_delete_api_key_failure(self, mock_api_key_manager, mock_current_user):
        """测试删除密钥失败"""
        mock_key = Mock()
        mock_key.user_id = 123
        mock_api_key_manager.get_key.return_value = mock_key
        mock_api_key_manager.delete_key.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_api_key("key123", mock_current_user)
        assert exc_info.value.status_code == 500

    def test_get_key_usage_exists(self, mock_api_key_manager, mock_current_user):
        """测试获取存在的密钥使用日志"""
        mock_key = Mock()
        mock_key.user_id = 123  # Same as current user
        mock_api_key_manager.get_key.return_value = mock_key
        
        mock_logs = [{"id": "log1", "timestamp": "2026-04-07T10:00:00"}]
        mock_api_key_manager.get_usage_log.return_value = mock_logs
        
        result = get_key_usage("key123", 10, mock_current_user)
        assert result["total"] == 1
        assert len(result["items"]) == 1
        mock_api_key_manager.get_usage_log.assert_called_once_with("key123", 10)

    def test_get_key_usage_not_exists(self, mock_api_key_manager, mock_current_user):
        """测试获取不存在密钥的使用日志"""
        mock_api_key_manager.get_key.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_key_usage("nonexistent", 10, mock_current_user)
        assert exc_info.value.status_code == 404

    def test_get_key_usage_permission_denied(self, mock_api_key_manager, mock_current_user):
        """测试无权限获取密钥使用日志"""
        mock_key = Mock()
        mock_key.user_id = 456  # Different user
        mock_api_key_manager.get_key.return_value = mock_key
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_key_usage("key123", 10, mock_current_user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self, mock_api_key_manager):
        """测试验证有效的API密钥"""
        mock_key = Mock()
        mock_key.key_id = "key123"
        mock_key.user_id = 123
        mock_key.permissions = ["read", "write"]
        mock_api_key_manager.verify_key.return_value = mock_key
        
        result = await verify_api_key("valid-api-key")
        assert result["key_id"] == "key123"
        assert result["user_id"] == 123
        assert result["permissions"] == ["read", "write"]
        mock_api_key_manager.verify_key.assert_called_once_with("valid-api-key")

    @pytest.mark.asyncio
    async def test_verify_api_key_invalid(self, mock_api_key_manager):
        """测试验证无效的API密钥"""
        mock_api_key_manager.verify_key.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key("invalid-api-key")
        assert exc_info.value.status_code == 401