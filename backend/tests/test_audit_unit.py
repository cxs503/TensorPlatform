# -*- coding: utf-8 -*-
"""
审计日志单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.audit import (
    log_audit_event,
    search_audit_logs,
    get_user_activity,
    get_security_events,
    export_audit_logs,
    get_audit_actions
)
from api.audit import LogAuditReq


class TestAuditUnit:
    """审计日志单元测试"""

    @pytest.fixture
    def mock_audit_service(self):
        """模拟审计服务"""
        with patch('api.audit.get_audit_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    @pytest.fixture
    def mock_current_user(self):
        """模拟当前用户"""
        return {"user_id": 123, "username": "testuser"}

    def test_log_audit_event_success(self, mock_audit_service, mock_current_user):
        """测试成功记录审计事件"""
        mock_entry = {"event_id": "event123", "timestamp": "2026-04-07T20:45:00Z"}
        mock_audit_service.log_event.return_value = mock_entry
        
        req = LogAuditReq(
            action="login",
            resource_type="auth",
            resource_id="session123",
            level="info",
            details={"ip": "192.168.1.1"},
            result="success"
        )
        result = log_audit_event(req, mock_current_user)
        assert result["event_id"] == "event123"
        assert "Logged" in result["message"]
        mock_audit_service.log_event.assert_called_once_with(
            action="login",
            user_id="123",
            username="testuser",
            resource_type="auth",
            resource_id="session123",
            level="info",
            details={"ip": "192.168.1.1"},
            result="success"
        )

    def test_search_audit_logs_all(self, mock_audit_service):
        """测试搜索所有审计日志"""
        mock_logs = [{"id": "log1", "action": "login"}, {"id": "log2", "action": "logout"}]
        mock_audit_service.search.return_value = mock_logs
        
        result = search_audit_logs()
        assert result["total"] == 2
        assert len(result["items"]) == 2
        mock_audit_service.search.assert_called_once_with(
            user_id=None, action=None, resource_type=None, resource_id=None, result=None, limit=100
        )

    def test_search_audit_logs_filtered(self, mock_audit_service):
        """测试带过滤条件搜索审计日志"""
        mock_logs = [{"id": "log1", "action": "delete", "resource_type": "model"}]
        mock_audit_service.search.return_value = mock_logs
        
        result = search_audit_logs(user_id="123", action="delete", resource_type="model", limit=10)
        assert result["total"] == 1
        assert len(result["items"]) == 1
        mock_audit_service.search.assert_called_once_with(
            user_id="123", action="delete", resource_type="model", resource_id=None, result=None, limit=10
        )

    def test_get_user_activity(self, mock_audit_service):
        """测试获取用户活动统计"""
        mock_activity = {
            "user_id": "123",
            "days": 30,
            "total_events": 150,
            "login_count": 45,
            "api_calls": 105,
            "last_active": "2026-04-07T20:00:00Z"
        }
        mock_audit_service.get_user_activity.return_value = mock_activity
        
        result = get_user_activity("123", 30)
        assert result["user_id"] == "123"
        assert result["total_events"] == 150
        mock_audit_service.get_user_activity.assert_called_once_with("123", 30)

    def test_get_security_events(self, mock_audit_service):
        """测试获取安全相关事件"""
        mock_events = [
            {"id": "sec1", "action": "failed_login", "level": "warning"},
            {"id": "sec2", "action": "unauthorized_access", "level": "critical"}
        ]
        mock_audit_service.get_security_events.return_value = mock_events
        
        result = get_security_events(7)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        mock_audit_service.get_security_events.assert_called_once_with(7)

    def test_export_audit_logs_json(self, mock_audit_service):
        """测试导出JSON格式审计日志"""
        mock_data = [{"id": "log1", "action": "login"}, {"id": "log2", "action": "logout"}]
        mock_audit_service.export_logs.return_value = mock_data
        
        result = export_audit_logs("2026-04-01", "2026-04-07", "json")
        assert result["format"] == "json"
        assert result["start_date"] == "2026-04-01"
        assert result["end_date"] == "2026-04-07"
        assert len(result["data"]) == 2
        mock_audit_service.export_logs.assert_called_once_with("2026-04-01", "2026-04-07", "json")

    def test_export_audit_logs_csv(self, mock_audit_service):
        """测试导出CSV格式审计日志"""
        mock_data = "id,action,timestamp\n1,login,2026-04-01T10:00:00Z"
        mock_audit_service.export_logs.return_value = mock_data
        
        result = export_audit_logs("2026-04-01", "2026-04-07", "csv")
        assert result["format"] == "csv"
        assert isinstance(result["data"], str)
        mock_audit_service.export_logs.assert_called_once_with("2026-04-01", "2026-04-07", "csv")

    def test_get_audit_actions(self):
        """测试获取审计操作类型"""
        result = get_audit_actions()
        assert "actions" in result
        actions = result["actions"]
        assert len(actions) > 0
        
        # Check that it contains expected structure
        action_ids = [a["id"] for a in actions]
        action_names = [a["name"] for a in actions]
        
        # Verify some common actions (exact values depend on AuditAction enum)
        # At minimum, we should have some actions with proper formatting
        assert any("Login" in name for name in action_names)
        assert any("Logout" in name for name in action_names)
        assert any("Create" in name for name in action_names)
        assert any("Delete" in name for name in action_names)