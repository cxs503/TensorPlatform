# -*- coding: utf-8 -*-
"""
CI/CD单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from api.cicd import (
    list_pipelines,
    create_pipeline,
    get_pipeline,
    update_pipeline,
    delete_pipeline,
    trigger_pipeline,
    list_runs,
    get_run,
    cancel_run,
    create_webhook,
    trigger_webhook
)
from api.cicd import PipelineCreate, PipelineUpdate, TriggerRequest, WebhookCreate, WebhookPayload


class TestCICDUnit:
    """CI/CD单元测试"""

    @pytest.fixture
    def mock_cicd_service(self):
        """模拟CI/CD服务"""
        with patch('api.cicd.get_cicd_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_list_pipelines_all(self, mock_cicd_service):
        """测试列出所有流水线"""
        mock_pipelines = [{"id": "pipe1", "name": "build"}, {"id": "pipe2", "name": "deploy"}]
        mock_cicd_service.list_pipelines.return_value = mock_pipelines
        
        result = list_pipelines()
        assert len(result) == 2
        mock_cicd_service.list_pipelines.assert_called_once_with(None)

    def test_list_pipelines_by_project(self, mock_cicd_service):
        """测试按项目列出流水线"""
        mock_pipelines = [{"id": "pipe1", "name": "build"}]
        mock_cicd_service.list_pipelines.return_value = mock_pipelines
        
        result = list_pipelines("proj123")
        assert len(result) == 1
        mock_cicd_service.list_pipelines.assert_called_once_with("proj123")

    def test_create_pipeline(self, mock_cicd_service):
        """测试创建流水线"""
        mock_stage = Mock()
        mock_stage.name = "build"
        mock_stage.action = "docker-build"
        mock_stage.config = {}
        
        mock_pipeline = Mock()
        mock_pipeline.id = "pipeline123"
        mock_pipeline.name = "test-pipeline"
        mock_pipeline.status = "active"
        mock_pipeline.stages = [mock_stage]
        mock_cicd_service.create_pipeline.return_value = mock_pipeline
        
        req = PipelineCreate(name="test-pipeline", stages=[{"name": "build", "action": "docker-build"}])
        result = create_pipeline(req)
        assert result["id"] == "pipeline123"
        assert result["name"] == "test-pipeline"
        assert len(result["stages"]) == 1
        mock_cicd_service.create_pipeline.assert_called_once()

    def test_get_pipeline_exists(self, mock_cicd_service):
        """测试获取存在的流水线"""
        mock_stage = Mock()
        mock_stage.name = "build"
        mock_stage.action = "docker-build"
        mock_stage.config = {"image": "nginx"}
        mock_stage.status = "success"
        
        mock_pipeline = Mock()
        mock_pipeline.id = "pipeline123"
        mock_pipeline.name = "test-pipeline"
        mock_pipeline.project_id = "proj123"
        mock_pipeline.status = "active"
        mock_pipeline.stages = [mock_stage]
        mock_pipeline.created_at = datetime.now()
        mock_pipeline.updated_at = datetime.now()
        mock_cicd_service.get_pipeline.return_value = mock_pipeline
        
        result = get_pipeline("pipeline123")
        assert result["id"] == "pipeline123"
        assert result["project_id"] == "proj123"
        assert len(result["stages"]) == 1
        mock_cicd_service.get_pipeline.assert_called_once_with("pipeline123")

    def test_get_pipeline_not_exists(self, mock_cicd_service):
        """测试获取不存在的流水线"""
        mock_cicd_service.get_pipeline.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_pipeline("nonexistent")
        assert exc_info.value.status_code == 404

    def test_update_pipeline_exists(self, mock_cicd_service):
        """测试更新存在的流水线"""
        mock_cicd_service.update_pipeline.return_value = Mock()
        
        req = PipelineUpdate(stages=[{"name": "test", "action": "run-tests"}])
        result = update_pipeline("pipeline123", req)
        assert result["message"] == "更新成功"
        mock_cicd_service.update_pipeline.assert_called_once_with("pipeline123", req.stages)

    def test_update_pipeline_not_exists(self, mock_cicd_service):
        """测试更新不存在的流水线"""
        mock_cicd_service.update_pipeline.return_value = None
        
        from fastapi import HTTPException
        req = PipelineUpdate(stages=[])
        with pytest.raises(HTTPException) as exc_info:
            update_pipeline("nonexistent", req)
        assert exc_info.value.status_code == 404

    def test_delete_pipeline_exists(self, mock_cicd_service):
        """测试删除存在的流水线"""
        mock_cicd_service.delete_pipeline.return_value = True
        
        result = delete_pipeline("pipeline123")
        assert result["message"] == "删除成功"
        mock_cicd_service.delete_pipeline.assert_called_once_with("pipeline123")

    def test_delete_pipeline_not_exists(self, mock_cicd_service):
        """测试删除不存在的流水线"""
        mock_cicd_service.delete_pipeline.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_pipeline("nonexistent")
        assert exc_info.value.status_code == 404

    def test_trigger_pipeline_manual(self, mock_cicd_service):
        """测试手动触发流水线"""
        mock_run = Mock()
        mock_run.id = "run123"
        mock_run.status = "running"
        mock_run.started_at = datetime.now()
        mock_cicd_service.trigger_pipeline.return_value = mock_run
        
        result = trigger_pipeline("pipeline123")
        assert result["run_id"] == "run123"
        assert result["status"] == "running"
        mock_cicd_service.trigger_pipeline.assert_called_once_with("pipeline123", "manual", None)

    def test_trigger_pipeline_with_commit(self, mock_cicd_service):
        """测试带提交ID触发流水线"""
        mock_run = Mock()
        mock_run.id = "run456"
        mock_run.status = "running"
        mock_run.started_at = datetime.now()
        mock_cicd_service.trigger_pipeline.return_value = mock_run
        
        req = TriggerRequest(commit_id="abc123")
        result = trigger_pipeline("pipeline123", req)
        assert result["run_id"] == "run456"
        mock_cicd_service.trigger_pipeline.assert_called_once_with("pipeline123", "manual", "abc123")

    def test_trigger_pipeline_not_exists(self, mock_cicd_service):
        """测试触发不存在的流水线"""
        mock_cicd_service.trigger_pipeline.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            trigger_pipeline("nonexistent")
        assert exc_info.value.status_code == 404

    def test_list_runs(self, mock_cicd_service):
        """测试列出执行历史"""
        mock_runs = [{"id": "run1", "status": "success"}, {"id": "run2", "status": "failed"}]
        mock_cicd_service.list_runs.return_value = mock_runs
        
        result = list_runs("pipeline123")
        assert len(result) == 2
        mock_cicd_service.list_runs.assert_called_once_with("pipeline123")

    def test_get_run_exists(self, mock_cicd_service):
        """测试获取存在的执行"""
        mock_stage = Mock()
        mock_stage.name = "build"
        mock_stage.action = "docker-build"
        mock_stage.status = "success"
        mock_stage.logs = "Build successful"
        mock_stage.started_at = datetime.now()
        mock_stage.finished_at = datetime.now()
        
        mock_run = Mock()
        mock_run.id = "run123"
        mock_run.pipeline_id = "pipeline123"
        mock_run.status = "success"
        mock_run.triggered_by = "manual"
        mock_run.commit_id = "abc123"
        mock_run.started_at = datetime.now()
        mock_run.finished_at = datetime.now()
        mock_run.stages = [mock_stage]
        mock_cicd_service.get_run.return_value = mock_run
        
        result = get_run("run123")
        assert result["id"] == "run123"
        assert result["pipeline_id"] == "pipeline123"
        assert len(result["stages"]) == 1
        mock_cicd_service.get_run.assert_called_once_with("run123")

    def test_get_run_not_exists(self, mock_cicd_service):
        """测试获取不存在的执行"""
        mock_cicd_service.get_run.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_run("nonexistent")
        assert exc_info.value.status_code == 404

    def test_cancel_run_exists(self, mock_cicd_service):
        """测试取消存在的执行"""
        mock_cicd_service.cancel_run.return_value = True
        
        result = cancel_run("run123")
        assert result["message"] == "取消成功"
        mock_cicd_service.cancel_run.assert_called_once_with("run123")

    def test_cancel_run_not_exists(self, mock_cicd_service):
        """测试取消不存在的执行"""
        mock_cicd_service.cancel_run.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            cancel_run("nonexistent")
        assert exc_info.value.status_code == 404

    def test_create_webhook_exists(self, mock_cicd_service):
        """测试为存在的流水线创建Webhook"""
        mock_webhook = Mock()
        mock_webhook.id = "webhook123"
        mock_webhook.url = "https://example.com/webhook/123"
        mock_webhook.events = ["push", "pull_request"]
        mock_webhook.secret = "secret123"
        mock_cicd_service.create_webhook.return_value = mock_webhook
        
        req = WebhookCreate(events=["push", "pull_request"])
        result = create_webhook("pipeline123", req)
        assert result["id"] == "webhook123"
        assert result["url"] == "https://example.com/webhook/123"
        mock_cicd_service.create_webhook.assert_called_once_with("pipeline123", ["push", "pull_request"])

    def test_create_webhook_not_exists(self, mock_cicd_service):
        """测试为不存在的流水线创建Webhook"""
        mock_cicd_service.create_webhook.return_value = None
        
        from fastapi import HTTPException
        req = WebhookCreate(events=["push"])
        with pytest.raises(HTTPException) as exc_info:
            create_webhook("nonexistent", req)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_trigger_webhook_exists(self, mock_cicd_service):
        """测试触发存在的Webhook"""
        mock_run = Mock()
        mock_run.id = "run789"
        mock_run.status = "running"
        mock_cicd_service.trigger_by_webhook.return_value = mock_run
        
        payload = WebhookPayload(commit_id="def456", after="def456", user="testuser")
        result = await trigger_webhook("webhook123", "push", payload)
        assert result["run_id"] == "run789"
        assert result["status"] == "running"
        mock_cicd_service.trigger_by_webhook.assert_called_once_with(
            "webhook123", "push", {"commit_id": "def456", "after": "def456", "user": "testuser"}
        )

    @pytest.mark.asyncio
    async def test_trigger_webhook_not_exists(self, mock_cicd_service):
        """测试触发不存在的Webhook"""
        mock_cicd_service.trigger_by_webhook.return_value = None
        
        from fastapi import HTTPException
        payload = WebhookPayload()
        with pytest.raises(HTTPException) as exc_info:
            await trigger_webhook("nonexistent", "push", payload)
        assert exc_info.value.status_code == 404