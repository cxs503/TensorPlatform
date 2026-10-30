# -*- coding: utf-8 -*-
"""
审计日志单元测试：验证当前 audit_logs SQL 契约而非旧内存服务。
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime

from api.audit import (
    record_audit,
    search_audit_logs,
    get_user_activity,
    get_security_events,
    export_audit_logs,
    get_audit_actions
)


class TestAuditUnit:
    """审计日志单元测试"""

    @pytest.fixture
    def db(self):
        return MagicMock()

    @pytest.fixture
    def mock_current_user(self):
        """模拟当前用户"""
        return {"user_id": 123, "username": "testuser"}

    def test_log_audit_event_success(self, db, mock_current_user):
        """测试成功记录审计事件"""
        result = record_audit(
            db, action="login", entity_type="auth", entity_id="session123",
            detail='{"ip": "192.168.1.1"}', user=mock_current_user["username"],
        )
        assert result is None  # record_audit is best-effort, not an event-ID API.
        assert db.execute.call_args.args[1] == {
            "action": "login", "etype": "auth", "eid": "session123",
            "detail": '{"ip": "192.168.1.1"}', "user": "testuser",
        }
        assert "INSERT INTO audit_logs" in str(db.execute.call_args.args[0])
        db.commit.assert_called_once()

    def test_log_audit_failure_rolls_back_without_breaking_caller(self, db):
        db.execute.side_effect = RuntimeError("database unavailable")
        assert record_audit(db, "login") is None
        db.rollback.assert_called_once()
        db.commit.assert_not_called()
        assert record_audit(None, "login") is None

    def test_search_audit_logs_all(self, db):
        """测试搜索所有审计日志"""
        db.execute.return_value.fetchall.return_value = [
            (1, "login", "auth", "s1", "ok", "testuser", datetime(2026, 4, 7)),
            (2, "logout", "auth", "s2", "ok", "testuser", datetime(2026, 4, 7)),
        ]
        result = search_audit_logs(db=db)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert [item["action"] for item in result["items"]] == ["login", "logout"]
        assert result["items"][0]["timestamp"] == "2026-04-07T00:00:00"
        assert db.execute.call_args.args[1] == {}

    def test_search_audit_logs_filtered(self, db):
        """测试带过滤条件搜索审计日志"""
        db.execute.return_value.fetchall.return_value = [
            (1, "delete", "model", "m1", "ok", "123", None),
            (2, "delete", "model", "m2", "ok", "123", None),
        ]
        result = search_audit_logs(user_id="123", action="delete", resource_type="model", page=2, page_size=1, db=db)
        assert result["total"] == 2
        assert len(result["items"]) == 1
        assert result["items"][0]["resource_id"] == "m2"
        assert db.execute.call_args.args[1] == {"user": "123", "action": "delete", "etype": "model"}

    def test_get_user_activity(self, db):
        """测试获取用户活动统计"""
        db.execute.return_value.fetchall.return_value = [
            (1, "login", "auth", "ok", datetime(2026, 4, 7)),
        ]
        result = get_user_activity("123", 30, db=db)
        assert result["user_id"] == "123"
        assert result["total"] == 1
        assert result["items"][0]["action"] == "login"
        assert db.execute.call_args.args[1] == {"u": "123"}

    def test_get_security_events(self, db):
        """测试获取安全相关事件"""
        db.execute.return_value.fetchall.return_value = [
            (3, "unauthorized_access", "testuser", "denied", None),
        ]
        result = get_security_events(7, db=db)
        assert result["total"] == len(result["items"])
        assert result["items"][-1]["id"] == 3
        assert result["items"][-1]["type"] == "unauthorized_access"
        assert "entity_type='security'" in str(db.execute.call_args.args[0])

    def test_export_audit_logs_json(self, db):
        """测试导出JSON格式审计日志"""
        db.execute.return_value.fetchall.return_value = [
            (1, "login", "auth", "s1", "ok", "testuser", datetime(2026, 4, 1)),
            (2, "logout", "auth", "s2", "ok", "testuser", datetime(2026, 4, 7)),
        ]
        result = export_audit_logs("2026-04-01", "2026-04-07", "json", db=db)
        assert result["format"] == "json"
        assert len(result["data"]) == 2
        assert result["data"][0]["action"] == "login"
        assert db.execute.call_args.args[1] == {"start": "2026-04-01", "end": "2026-04-07"}

    def test_export_dates_are_bound_parameters(self, db):
        """日期不能被拼接为 SQL，包括不可信的调用参数。"""
        value = "2026-04-01' OR 1=1 --"
        db.execute.return_value.fetchall.return_value = []
        result = export_audit_logs(value, value, "json", db=db)
        assert result["data"] == []
        statement, parameters = db.execute.call_args.args
        assert value not in str(statement)
        assert parameters == {"start": value, "end": value}

    def test_get_audit_actions(self):
        """测试获取审计操作类型"""
        result = get_audit_actions()
        assert result["total"] == len(result["items"])
        actions = result["items"]
        assert len(actions) > 0
        
        # Check that it contains expected structure
        action_ids = [a["id"] for a in actions]
        action_names = [a["name"] for a in actions]
        
        # Verify some common actions (exact values depend on AuditAction enum)
        # At minimum, we should have some actions with proper formatting
        assert {"login", "logout", "create", "delete"}.issubset(action_ids)
        assert all(action_names)