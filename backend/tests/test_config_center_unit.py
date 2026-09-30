# -*- coding: utf-8 -*-
"""
配置中心单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from api.config_center import (
    list_namespaces,
    create_namespace,
    list_configs,
    create_config,
    get_config,
    update_config,
    release_config,
    rollback_config,
    delete_config,
    get_versions,
    get_version,
    add_listener,
    list_listeners,
    remove_listener,
    get_config_stats
)


class TestConfigCenterUnit:
    """配置中心单元测试"""

    @pytest.fixture
    def mock_config_service(self):
        """模拟配置中心服务"""
        with patch('api.config_center.get_config_center_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_list_namespaces(self, mock_config_service):
        """测试列出命名空间"""
        mock_namespaces = [{"id": "ns1", "name": "default"}, {"id": "ns2", "name": "prod"}]
        mock_config_service.list_namespaces.return_value = mock_namespaces
        
        result = list_namespaces()
        assert len(result) == 2
        mock_config_service.list_namespaces.assert_called_once()

    def test_create_namespace(self, mock_config_service):
        """测试创建命名空间"""
        mock_namespace = Mock()
        mock_namespace.id = "namespace123"
        mock_namespace.name = "test-namespace"
        mock_namespace.description = "Test namespace"
        mock_config_service.create_namespace.return_value = mock_namespace
        
        req = {"name": "test-namespace", "description": "Test namespace"}
        result = create_namespace(req)
        assert result["id"] == "namespace123"
        assert result["name"] == "test-namespace"
        assert result["description"] == "Test namespace"
        mock_config_service.create_namespace.assert_called_once()

    def test_list_configs_all(self, mock_config_service):
        """测试列出所有配置"""
        mock_configs = [{"id": "cfg1", "name": "app-config"}, {"id": "cfg2", "name": "db-config"}]
        mock_config_service.list_configs.return_value = mock_configs
        
        result = list_configs()
        assert len(result) == 2
        mock_config_service.list_configs.assert_called_once_with(None, None, None)

    def test_list_configs_filtered(self, mock_config_service):
        """测试带过滤条件列出配置"""
        mock_configs = [{"id": "cfg1", "name": "app-config"}]
        mock_config_service.list_configs.return_value = mock_configs
        
        result = list_configs("application", "myapp", "active")
        assert len(result) == 1
        mock_config_service.list_configs.assert_called_once_with("application", "myapp", "active")

    def test_create_config(self, mock_config_service):
        """测试创建配置"""
        mock_config = Mock()
        mock_config.id = "config123"
        mock_config.name = "database-config"
        mock_config.scope = "application"
        mock_config.version = 1
        mock_config.status = "draft"
        mock_config_service.create_config.return_value = mock_config
        
        req = {
            "name": "database-config",
            "config_type": "properties",
            "content": "db.url=jdbc:mysql://localhost:3306/mydb",
            "scope": "application",
            "application": "myapp"
        }
        result = create_config(req)
        assert result["id"] == "config123"
        assert result["name"] == "database-config"
        assert result["scope"] == "application"
        assert result["version"] == 1
        mock_config_service.create_config.assert_called_once()

    def test_get_config_exists(self, mock_config_service):
        """测试获取存在的配置"""
        mock_config = Mock()
        mock_config.id = "config123"
        mock_config.name = "database-config"
        mock_config.config_type = "properties"
        mock_config.content = "db.url=jdbc:mysql://localhost:3306/mydb"
        mock_config.scope = "application"
        mock_config.application = "myapp"
        mock_config.version = 1
        mock_config.status = "active"
        mock_config.created_at = datetime.now()
        mock_config.updated_at = datetime.now()
        mock_config_service.get_config.return_value = mock_config
        
        result = get_config("config123")
        assert result["id"] == "config123"
        assert result["name"] == "database-config"
        assert result["content"] == "db.url=jdbc:mysql://localhost:3306/mydb"
        mock_config_service.get_config.assert_called_once_with("config123")

    def test_get_config_not_exists(self, mock_config_service):
        """测试获取不存在的配置"""
        mock_config_service.get_config.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_config("nonexistent")
        assert exc_info.value.status_code == 404

    def test_update_config_exists(self, mock_config_service):
        """测试更新存在的配置"""
        mock_config = Mock()
        mock_config.version = 2
        mock_config.status = "draft"
        mock_config_service.update_config.return_value = mock_config
        
        req = {"content": "updated content", "comment": "Update database URL"}
        result = update_config("config123", req)
        assert result["message"] == "更新成功"
        assert result["version"] == 2
        mock_config_service.update_config.assert_called_once_with(
            config_id="config123", 
            content="updated content", 
            comment="Update database URL"
        )

    def test_update_config_not_exists(self, mock_config_service):
        """测试更新不存在的配置"""
        mock_config_service.update_config.return_value = None
        
        from fastapi import HTTPException
        req = {"content": "new content"}
        with pytest.raises(HTTPException) as exc_info:
            update_config("nonexistent", req)
        assert exc_info.value.status_code == 404

    def test_release_config_exists(self, mock_config_service):
        """测试发布存在的配置"""
        mock_config = Mock()
        mock_config.version = 3
        mock_config_service.release_config.return_value = mock_config
        
        result = release_config("config123")
        assert result["message"] == "发布成功"
        assert result["version"] == 3
        mock_config_service.release_config.assert_called_once_with("config123")

    def test_release_config_not_exists(self, mock_config_service):
        """测试发布不存在的配置"""
        mock_config_service.release_config.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            release_config("nonexistent")
        assert exc_info.value.status_code == 404

    def test_rollback_config_success(self, mock_config_service):
        """测试回滚配置成功"""
        mock_config_service.rollback_config.return_value = True
        
        result = rollback_config("config123", 1)
        assert result["message"] == "回滚成功"
        mock_config_service.rollback_config.assert_called_once_with("config123", 1)

    def test_rollback_config_failure(self, mock_config_service):
        """测试回滚配置失败"""
        mock_config_service.rollback_config.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            rollback_config("config123")
        assert exc_info.value.status_code == 400

    def test_delete_config_exists(self, mock_config_service):
        """测试删除存在的配置"""
        mock_config_service.delete_config.return_value = True
        
        result = delete_config("config123")
        assert result["message"] == "删除成功"
        mock_config_service.delete_config.assert_called_once_with("config123")

    def test_delete_config_not_exists(self, mock_config_service):
        """测试删除不存在的配置"""
        mock_config_service.delete_config.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_config("nonexistent")
        assert exc_info.value.status_code == 404

    def test_get_versions(self, mock_config_service):
        """测试获取配置版本历史"""
        mock_versions = [
            {"version": 1, "status": "draft"},
            {"version": 2, "status": "active"},
            {"version": 3, "status": "deprecated"}
        ]
        mock_config_service.get_versions.return_value = mock_versions
        
        result = get_versions("config123")
        assert len(result) == 3
        assert result[0]["version"] == 1
        mock_config_service.get_versions.assert_called_once_with("config123")

    def test_get_version_exists(self, mock_config_service):
        """测试获取存在的版本"""
        mock_version = {"version": 2, "content": "version 2 content", "status": "active"}
        mock_config_service.get_version.return_value = mock_version
        
        result = get_version("config123", 2)
        assert result["version"] == 2
        assert result["status"] == "active"
        mock_config_service.get_version.assert_called_once_with("config123", 2)

    def test_get_version_not_exists(self, mock_config_service):
        """测试获取不存在的版本"""
        mock_config_service.get_version.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_version("config123", 999)
        assert exc_info.value.status_code == 404

    def test_add_listener(self, mock_config_service):
        """测试添加配置监听器"""
        mock_listener = Mock()
        mock_listener.id = "listener123"
        mock_listener.callback_url = "https://example.com/webhook"
        mock_listener.events = ["update", "delete"]
        mock_config_service.add_listener.return_value = mock_listener
        
        req = {"callback_url": "https://example.com/webhook", "events": ["update", "delete"]}
        result = add_listener("config123", req)
        assert result["id"] == "listener123"
        assert result["callback_url"] == "https://example.com/webhook"
        mock_config_service.add_listener.assert_called_once_with(
            config_id="config123", 
            callback_url="https://example.com/webhook", 
            events=["update", "delete"]
        )

    def test_list_listeners(self, mock_config_service):
        """测试列出配置监听器"""
        mock_listeners = [
            {"id": "listener1", "callback_url": "https://example1.com"},
            {"id": "listener2", "callback_url": "https://example2.com"}
        ]
        mock_config_service.list_listeners.return_value = mock_listeners
        
        result = list_listeners("config123")
        assert len(result) == 2
        mock_config_service.list_listeners.assert_called_once_with("config123")

    def test_remove_listener_exists(self, mock_config_service):
        """测试移除存在的监听器"""
        mock_config_service.remove_listener.return_value = True
        
        result = remove_listener("config123", "listener123")
        assert result["message"] == "移除成功"
        mock_config_service.remove_listener.assert_called_once_with("config123", "listener123")

    def test_remove_listener_not_exists(self, mock_config_service):
        """测试移除不存在的监听器"""
        mock_config_service.remove_listener.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            remove_listener("config123", "nonexistent")
        assert exc_info.value.status_code == 404

    def test_get_config_stats(self, mock_config_service):
        """测试获取配置统计"""
        mock_stats = {
            "total_configs": 150,
            "active_configs": 120,
            "namespaces": 5,
            "applications": 12,
            "total_versions": 450
        }
        mock_config_service.get_config_stats.return_value = mock_stats
        
        result = get_config_stats()
        assert result == mock_stats
        mock_config_service.get_config_stats.assert_called_once()