# -*- coding: utf-8 -*-
"""
备份恢复单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from api.backup import (
    list_backups,
    create_backup,
    get_backup,
    delete_backup,
    list_restores,
    create_restore,
    get_restore,
    list_schedules,
    create_schedule,
    update_schedule,
    delete_schedule,
    list_policies,
    create_policy,
    get_backup_stats
)


class TestBackupUnit:
    """备份恢复单元测试"""

    @pytest.fixture
    def mock_backup_service(self):
        """模拟备份服务"""
        with patch('api.backup.get_backup_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_list_backups_all(self, mock_backup_service):
        """测试列出所有备份"""
        mock_backups = [{"id": "backup1", "name": "daily-backup"}, {"id": "backup2", "name": "weekly-backup"}]
        mock_backup_service.list_backups.return_value = mock_backups
        
        result = list_backups()
        assert len(result) == 2
        mock_backup_service.list_backups.assert_called_once_with(None, None, None)

    def test_list_backups_filtered(self, mock_backup_service):
        """测试带过滤条件列出备份"""
        mock_backups = [{"id": "backup1", "name": "db-backup"}]
        mock_backup_service.list_backups.return_value = mock_backups
        
        result = list_backups("database", "db123", "completed")
        assert len(result) == 1
        mock_backup_service.list_backups.assert_called_once_with("database", "db123", "completed")

    def test_create_backup(self, mock_backup_service):
        """测试创建备份"""
        mock_backup = Mock()
        mock_backup.id = "backup123"
        mock_backup.name = "test-backup"
        mock_backup.target_type = "database"
        mock_backup.target_id = "db123"
        mock_backup.backup_type = "full"
        mock_backup.status = "pending"
        mock_backup.size_mb = 0
        mock_backup.created_at = datetime.now()
        mock_backup_service.create_backup.return_value = mock_backup
        
        req = {
            "name": "test-backup",
            "target_type": "database",
            "target_id": "db123",
            "backup_type": "full",
            "retention_days": 7
        }
        result = create_backup(req)
        assert result["id"] == "backup123"
        assert result["name"] == "test-backup"
        assert result["target_type"] == "database"
        mock_backup_service.create_backup.assert_called_once()

    def test_get_backup_exists(self, mock_backup_service):
        """测试获取存在的备份"""
        mock_backup = Mock()
        mock_backup.id = "backup123"
        mock_backup.name = "test-backup"
        mock_backup.target_type = "database"
        mock_backup.target_id = "db123"
        mock_backup.backup_type = "full"
        mock_backup.size_mb = 150.5
        mock_backup.status = "completed"
        mock_backup.created_at = datetime.now()
        mock_backup.completed_at = datetime.now()
        mock_backup.checksum = "abc123def456"
        mock_backup_service.get_backup.return_value = mock_backup
        
        result = get_backup("backup123")
        assert result["id"] == "backup123"
        assert result["size_mb"] == 150.5
        assert result["checksum"] == "abc123def456"
        mock_backup_service.get_backup.assert_called_once_with("backup123")

    def test_get_backup_not_exists(self, mock_backup_service):
        """测试获取不存在的备份"""
        mock_backup_service.get_backup.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_backup("nonexistent")
        assert exc_info.value.status_code == 404

    def test_delete_backup_exists(self, mock_backup_service):
        """测试删除存在的备份"""
        mock_backup_service.delete_backup.return_value = True
        
        result = delete_backup("backup123")
        assert result["message"] == "删除成功"
        mock_backup_service.delete_backup.assert_called_once_with("backup123")

    def test_delete_backup_not_exists(self, mock_backup_service):
        """测试删除不存在的备份"""
        mock_backup_service.delete_backup.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_backup("nonexistent")
        assert exc_info.value.status_code == 404

    def test_list_restores_all(self, mock_backup_service):
        """测试列出所有恢复任务"""
        mock_restores = [{"id": "restore1", "backup_id": "backup123"}, {"id": "restore2", "backup_id": "backup456"}]
        mock_backup_service.list_restores.return_value = mock_restores
        
        result = list_restores()
        assert len(result) == 2
        mock_backup_service.list_restores.assert_called_once_with(None, None)

    def test_list_restores_filtered(self, mock_backup_service):
        """测试按备份ID列出恢复任务"""
        mock_restores = [{"id": "restore1", "backup_id": "backup123"}]
        mock_backup_service.list_restores.return_value = mock_restores
        
        result = list_restores("backup123", "running")
        assert len(result) == 1
        mock_backup_service.list_restores.assert_called_once_with("backup123", "running")

    def test_create_restore_exists(self, mock_backup_service):
        """测试创建存在的备份的恢复任务"""
        mock_restore = Mock()
        mock_restore.id = "restore123"
        mock_restore.backup_id = "backup123"
        mock_restore.target_type = "database"
        mock_restore.target_id = "db123"
        mock_restore.status = "pending"
        mock_restore.progress = 0
        mock_backup_service.create_restore.return_value = mock_restore
        
        req = {"backup_id": "backup123", "target_type": "database", "target_id": "db123"}
        result = create_restore(req)
        assert result["id"] == "restore123"
        assert result["backup_id"] == "backup123"
        mock_backup_service.create_restore.assert_called_once()

    def test_create_restore_not_exists(self, mock_backup_service):
        """测试创建不存在备份的恢复任务"""
        mock_backup_service.create_restore.return_value = None
        
        from fastapi import HTTPException
        req = {"backup_id": "nonexistent", "target_type": "database", "target_id": "db123"}
        with pytest.raises(HTTPException) as exc_info:
            create_restore(req)
        assert exc_info.value.status_code == 400

    def test_get_restore_exists(self, mock_backup_service):
        """测试获取存在的恢复任务"""
        mock_restore = Mock()
        mock_restore.id = "restore123"
        mock_restore.backup_id = "backup123"
        mock_restore.target_type = "database"
        mock_restore.target_id = "db123"
        mock_restore.status = "running"
        mock_restore.progress = 50
        mock_restore.created_at = datetime.now()
        mock_restore.completed_at = None
        mock_restore.error_message = None
        mock_backup_service.get_restore.return_value = mock_restore
        
        result = get_restore("restore123")
        assert result["id"] == "restore123"
        assert result["progress"] == 50
        assert result["error_message"] is None
        mock_backup_service.get_restore.assert_called_once_with("restore123")

    def test_get_restore_not_exists(self, mock_backup_service):
        """测试获取不存在的恢复任务"""
        mock_backup_service.get_restore.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_restore("nonexistent")
        assert exc_info.value.status_code == 404

    def test_list_schedules_all(self, mock_backup_service):
        """测试列出所有备份计划"""
        mock_schedules = [{"id": "schedule1", "name": "daily"}, {"id": "schedule2", "name": "weekly"}]
        mock_backup_service.list_schedules.return_value = mock_schedules
        
        result = list_schedules()
        assert len(result) == 2
        mock_backup_service.list_schedules.assert_called_once_with(None)

    def test_list_schedules_enabled_only(self, mock_backup_service):
        """测试列出启用的备份计划"""
        mock_schedules = [{"id": "schedule1", "name": "daily", "enabled": True}]
        mock_backup_service.list_schedules.return_value = mock_schedules
        
        result = list_schedules(True)
        assert len(result) == 1
        mock_backup_service.list_schedules.assert_called_once_with(True)

    def test_create_schedule(self, mock_backup_service):
        """测试创建备份计划"""
        mock_schedule = Mock()
        mock_schedule.id = "schedule123"
        mock_schedule.name = "nightly-backup"
        mock_schedule.target_type = "database"
        mock_schedule.target_id = "db123"
        mock_schedule.schedule = "0 2 * * *"
        mock_schedule.enabled = True
        mock_backup_service.create_schedule.return_value = mock_schedule
        
        req = {
            "name": "nightly-backup",
            "target_type": "database",
            "target_id": "db123",
            "schedule": "0 2 * * *",
            "retention_days": 30
        }
        result = create_schedule(req)
        assert result["id"] == "schedule123"
        assert result["name"] == "nightly-backup"
        assert result["schedule"] == "0 2 * * *"
        mock_backup_service.create_schedule.assert_called_once()

    def test_update_schedule_exists(self, mock_backup_service):
        """测试更新存在的备份计划"""
        mock_backup_service.update_schedule.return_value = Mock()
        
        req = {"enabled": False, "schedule": "0 3 * * *"}
        result = update_schedule("schedule123", req)
        assert result["message"] == "更新成功"
        mock_backup_service.update_schedule.assert_called_once_with(
            "schedule123", enabled=False, schedule="0 3 * * *"
        )

    def test_update_schedule_not_exists(self, mock_backup_service):
        """测试更新不存在的备份计划"""
        mock_backup_service.update_schedule.return_value = None
        
        from fastapi import HTTPException
        req = {"enabled": False}
        with pytest.raises(HTTPException) as exc_info:
            update_schedule("nonexistent", req)
        assert exc_info.value.status_code == 404

    def test_delete_schedule_exists(self, mock_backup_service):
        """测试删除存在的备份计划"""
        mock_backup_service.delete_schedule.return_value = True
        
        result = delete_schedule("schedule123")
        assert result["message"] == "删除成功"
        mock_backup_service.delete_schedule.assert_called_once_with("schedule123")

    def test_delete_schedule_not_exists(self, mock_backup_service):
        """测试删除不存在的备份计划"""
        mock_backup_service.delete_schedule.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_schedule("nonexistent")
        assert exc_info.value.status_code == 404

    def test_list_policies(self, mock_backup_service):
        """测试列出备份策略"""
        mock_policies = [
            {"id": "policy1", "name": "default", "compression_enabled": True},
            {"id": "policy2", "name": "secure", "encryption_enabled": True}
        ]
        mock_backup_service.list_policies.return_value = mock_policies
        
        result = list_policies()
        assert len(result) == 2
        mock_backup_service.list_policies.assert_called_once()

    def test_create_policy(self, mock_backup_service):
        """测试创建备份策略"""
        mock_policy = Mock()
        mock_policy.id = "policy123"
        mock_policy.name = "custom-policy"
        mock_policy.default_backup_type = "incremental"
        mock_policy.compression_enabled = True
        mock_policy.encryption_enabled = False
        mock_backup_service.create_policy.return_value = mock_policy
        
        req = {
            "name": "custom-policy",
            "default_backup_type": "incremental",
            "default_retention_days": 60,
            "compression_enabled": True,
            "encryption_enabled": False
        }
        result = create_policy(req)
        assert result["id"] == "policy123"
        assert result["name"] == "custom-policy"
        assert result["compression_enabled"] is True
        mock_backup_service.create_policy.assert_called_once()

    def test_get_backup_stats(self, mock_backup_service):
        """测试获取备份统计"""
        mock_stats = {
            "total_backups": 150,
            "total_size_gb": 2500,
            "active_schedules": 12,
            "failed_restores": 3,
            "success_rate": 98.5
        }
        mock_backup_service.get_backup_stats.return_value = mock_stats
        
        result = get_backup_stats()
        assert result == mock_stats
        mock_backup_service.get_backup_stats.assert_called_once()