# -*- coding: utf-8 -*-
"""
告警管理单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.alert import (
    list_rules,
    create_rule,
    get_rule,
    update_rule,
    delete_rule,
    trigger_alert,
    acknowledge_alert,
    resolve_alert,
    list_alerts,
    escalate_alert,
    get_escalations,
    get_alert_stats
)


class TestAlertUnit:
    """告警管理单元测试"""

    @pytest.fixture
    def mock_alert_service(self):
        """模拟告警服务"""
        with patch('api.alert.get_alert_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_list_rules_all(self, mock_alert_service):
        """测试列出所有告警规则"""
        mock_rules = [{"id": "rule1", "name": "cpu-high"}, {"id": "rule2", "name": "memory-low"}]
        mock_alert_service.list_rules.return_value = mock_rules
        
        result = list_rules()
        assert len(result) == 2
        mock_alert_service.list_rules.assert_called_once_with(None)

    def test_list_rules_enabled_only(self, mock_alert_service):
        """测试列出启用的告警规则"""
        mock_rules = [{"id": "rule1", "name": "cpu-high", "enabled": True}]
        mock_alert_service.list_rules.return_value = mock_rules
        
        result = list_rules(True)
        assert len(result) == 1
        mock_alert_service.list_rules.assert_called_once_with(True)

    def test_create_rule(self, mock_alert_service):
        """测试创建告警规则"""
        mock_rule = Mock()
        mock_rule.id = "rule123"
        mock_rule.name = "disk-space-low"
        mock_rule.enabled = True
        mock_alert_service.create_rule.return_value = mock_rule
        
        req = {
            "name": "disk-space-low",
            "condition": "disk_usage > 90",
            "level": "warning",
            "channels": ["dingtalk", "email"]
        }
        result = create_rule(req)
        assert result["id"] == "rule123"
        assert result["name"] == "disk-space-low"
        assert result["enabled"] is True
        mock_alert_service.create_rule.assert_called_once()

    def test_get_rule_exists(self, mock_alert_service):
        """测试获取存在的规则"""
        mock_rule = Mock()
        mock_rule.id = "rule123"
        mock_rule.name = "cpu-high"
        mock_rule.condition = "cpu_usage > 80"
        mock_rule.level = "critical"
        mock_rule.channels = ["dingtalk"]
        mock_rule.enabled = True
        mock_alert_service.get_rule.return_value = mock_rule
        
        result = get_rule("rule123")
        assert result["id"] == "rule123"
        assert result["name"] == "cpu-high"
        assert result["level"] == "critical"
        mock_alert_service.get_rule.assert_called_once_with("rule123")

    def test_get_rule_not_exists(self, mock_alert_service):
        """测试获取不存在的规则"""
        mock_alert_service.get_rule.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_rule("nonexistent")
        assert exc_info.value.status_code == 404

    def test_update_rule_exists(self, mock_alert_service):
        """测试更新存在的规则"""
        mock_alert_service.update_rule.return_value = Mock()
        
        req = {"name": "updated-rule", "enabled": False}
        result = update_rule("rule123", req)
        assert result["message"] == "更新成功"
        mock_alert_service.update_rule.assert_called_once_with("rule123", **req)

    def test_update_rule_not_exists(self, mock_alert_service):
        """测试更新不存在的规则"""
        mock_alert_service.update_rule.return_value = None
        
        from fastapi import HTTPException
        req = {"name": "new-name"}
        with pytest.raises(HTTPException) as exc_info:
            update_rule("nonexistent", req)
        assert exc_info.value.status_code == 404

    def test_delete_rule_exists(self, mock_alert_service):
        """测试删除存在的规则"""
        mock_alert_service.delete_rule.return_value = True
        
        result = delete_rule("rule123")
        assert result["message"] == "删除成功"
        mock_alert_service.delete_rule.assert_called_once_with("rule123")

    def test_delete_rule_not_exists(self, mock_alert_service):
        """测试删除不存在的规则"""
        mock_alert_service.delete_rule.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_rule("nonexistent")
        assert exc_info.value.status_code == 404

    def test_trigger_alert_success(self, mock_alert_service):
        """测试成功触发告警"""
        mock_alert = Mock()
        mock_alert.id = "alert123"
        mock_alert.title = "High CPU Usage"
        mock_alert.level = "warning"
        mock_alert.status = "firing"
        mock_alert_service.trigger_alert.return_value = mock_alert
        
        req = {
            "rule_id": "rule123",
            "source": "server-01",
            "data": {"cpu_usage": 95, "timestamp": "2026-04-07T20:00:00Z"}
        }
        result = trigger_alert(req)
        assert result["id"] == "alert123"
        assert result["title"] == "High CPU Usage"
        assert result["status"] == "firing"
        mock_alert_service.trigger_alert.assert_called_once()

    def test_trigger_alert_failure(self, mock_alert_service):
        """测试触发告警失败"""
        mock_alert_service.trigger_alert.return_value = None
        
        from fastapi import HTTPException
        req = {"rule_id": "invalid-rule", "source": "server-01"}
        with pytest.raises(HTTPException) as exc_info:
            trigger_alert(req)
        assert exc_info.value.status_code == 400

    def test_acknowledge_alert_exists(self, mock_alert_service):
        """测试确认存在的告警"""
        mock_alert_service.acknowledge_alert.return_value = True
        
        result = acknowledge_alert("alert123", "user123")
        assert result["message"] == "已确认"
        mock_alert_service.acknowledge_alert.assert_called_once_with("alert123", "user123")

    def test_acknowledge_alert_not_exists(self, mock_alert_service):
        """测试确认不存在的告警"""
        mock_alert_service.acknowledge_alert.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            acknowledge_alert("nonexistent", "user123")
        assert exc_info.value.status_code == 404

    def test_resolve_alert_exists(self, mock_alert_service):
        """测试解决存在的告警"""
        mock_alert_service.resolve_alert.return_value = True
        
        result = resolve_alert("alert123", "user123", "Issue fixed")
        assert result["message"] == "已解决"
        mock_alert_service.resolve_alert.assert_called_once_with("alert123", "user123", "Issue fixed")

    def test_resolve_alert_not_exists(self, mock_alert_service):
        """测试解决不存在的告警"""
        mock_alert_service.resolve_alert.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            resolve_alert("nonexistent", "user123")
        assert exc_info.value.status_code == 404

    def test_list_alerts_all(self, mock_alert_service):
        """测试列出所有告警"""
        mock_alerts = [{"id": "alert1", "title": "CPU High"}, {"id": "alert2", "title": "Memory Low"}]
        mock_alert_service.list_alerts.return_value = mock_alerts
        
        result = list_alerts()
        assert len(result) == 2
        mock_alert_service.list_alerts.assert_called_once_with(None, None, None, 100)

    def test_list_alerts_filtered(self, mock_alert_service):
        """测试带过滤条件列出告警"""
        mock_alerts = [{"id": "alert1", "title": "CPU Critical", "level": "critical"}]
        mock_alert_service.list_alerts.return_value = mock_alerts
        
        result = list_alerts("firing", "critical", "server-01", 10)
        assert len(result) == 1
        mock_alert_service.list_alerts.assert_called_once_with("firing", "critical", "server-01", 10)

    def test_escalate_alert_exists(self, mock_alert_service):
        """测试升级存在的告警"""
        mock_alert_service.escalate_alert.return_value = Mock()
        
        result = escalate_alert("alert123", "critical", "Escalating due to severity")
        assert result["message"] == "已升级"
        mock_alert_service.escalate_alert.assert_called_once_with("alert123", "critical", "Escalating due to severity")

    def test_escalate_alert_not_exists(self, mock_alert_service):
        """测试升级不存在的告警"""
        mock_alert_service.escalate_alert.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            escalate_alert("nonexistent", "critical", "Test reason")
        assert exc_info.value.status_code == 404

    def test_get_escalations_all(self, mock_alert_service):
        """测试获取所有升级记录"""
        mock_escalations = [{"id": "esc1", "alert_id": "alert123"}, {"id": "esc2", "alert_id": "alert456"}]
        mock_alert_service.get_escalations.return_value = mock_escalations
        
        result = get_escalations()
        assert len(result) == 2
        mock_alert_service.get_escalations.assert_called_once_with(None)

    def test_get_escalations_by_alert(self, mock_alert_service):
        """测试按告警ID获取升级记录"""
        mock_escalations = [{"id": "esc1", "alert_id": "alert123"}]
        mock_alert_service.get_escalations.return_value = mock_escalations
        
        result = get_escalations("alert123")
        assert len(result) == 1
        mock_alert_service.get_escalations.assert_called_once_with("alert123")

    def test_get_alert_stats(self, mock_alert_service):
        """测试获取告警统计"""
        mock_stats = {
            "total_alerts": 150,
            "active_alerts": 12,
            "resolved_alerts": 138,
            "rules_count": 25,
            "critical_count": 8,
            "warning_count": 4
        }
        mock_alert_service.get_alert_stats.return_value = mock_stats
        
        result = get_alert_stats()
        assert result == mock_stats
        mock_alert_service.get_alert_stats.assert_called_once()