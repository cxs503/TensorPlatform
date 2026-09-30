# -*- coding: utf-8 -*-
"""
AutoML单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from api.automl import (
    get_status,
    create_automl_task,
    list_automl_tasks,
    get_automl_task,
    start_automl_task,
    get_task_trials,
    get_best_model,
    get_leaderboard,
    list_supported_models
)
from api.automl import AutoMLTaskCreate


class TestAutoMLUnit:
    """AutoML单元测试"""

    def setup_method(self):
        """每个测试前清理任务存储"""
        import api.automl
        api.automl._automl_tasks.clear()

    def test_get_status_empty(self):
        """测试空状态"""
        result = get_status()
        assert result["status"] == "ready"
        assert result["active_tasks"] == 0
        assert result["completed_tasks"] == 0

    def test_create_automl_task_classification(self):
        """测试创建分类任务"""
        req = AutoMLTaskCreate(
            name="test_classification",
            task_type="classification",
            dataset_id="ds_123",
            target_column="target"
        )
        
        result = create_automl_task(req)
        assert "task_id" in result
        assert result["name"] == "test_classification"
        assert result["status"] == "pending"
        assert len(result["allowed_models"]) == 5

    def test_create_automl_task_regression(self):
        """测试创建回归任务"""
        req = AutoMLTaskCreate(
            name="test_regression",
            task_type="regression",
            dataset_id="ds_456",
            target_column="target"
        )
        
        result = create_automl_task(req)
        assert result["name"] == "test_regression"
        assert len(result["allowed_models"]) == 5

    def test_create_automl_task_time_series(self):
        """测试创建时间序列任务"""
        req = AutoMLTaskCreate(
            name="test_ts",
            task_type="time_series",
            dataset_id="ds_789",
            target_column="target"
        )
        
        result = create_automl_task(req)
        assert result["name"] == "test_ts"
        assert len(result["allowed_models"]) == 3

    def test_create_automl_task_with_custom_models(self):
        """测试创建自定义模型列表的任务"""
        req = AutoMLTaskCreate(
            name="test_custom",
            task_type="classification",
            dataset_id="ds_123",
            target_column="target",
            allowed_models=["lightgbm", "xgboost"]
        )
        
        result = create_automl_task(req)
        assert result["allowed_models"] == ["lightgbm", "xgboost"]

    def test_list_automl_tasks_all(self):
        """测试列出所有任务"""
        # 创建两个任务
        req1 = AutoMLTaskCreate(
            name="task1", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        req2 = AutoMLTaskCreate(
            name="task2", task_type="regression", dataset_id="ds2", target_column="t2"
        )
        
        create_automl_task(req1)
        create_automl_task(req2)
        
        result = list_automl_tasks()
        assert result["total"] == 2
        assert len(result["items"]) == 2

    def test_list_automl_tasks_by_status(self):
        """测试按状态筛选任务"""
        req = AutoMLTaskCreate(
            name="pending_task", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        result = create_automl_task(req)
        task_id = result["task_id"]
        
        # 列出pending任务
        pending_result = list_automl_tasks("pending")
        assert pending_result["total"] == 1
        
        # 列出running任务（应该为空）
        running_result = list_automl_tasks("running")
        assert running_result["total"] == 0

    def test_get_automl_task_exists(self):
        """测试获取存在的任务"""
        req = AutoMLTaskCreate(
            name="test_task", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        create_result = create_automl_task(req)
        task_id = create_result["task_id"]
        
        result = get_automl_task(task_id)
        assert result["id"] == task_id
        assert result["name"] == "test_task"

    def test_get_automl_task_not_exists(self):
        """测试获取不存在的任务"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_automl_task("nonexistent_task")
        assert exc_info.value.status_code == 404

    def test_start_automl_task_pending(self):
        """测试启动pending状态的任务"""
        req = AutoMLTaskCreate(
            name="start_test", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        create_result = create_automl_task(req)
        task_id = create_result["task_id"]
        
        # Mock BackgroundTasks
        mock_background_tasks = Mock()
        
        result = start_automl_task(task_id, mock_background_tasks)
        assert result["status"] == "running"
        assert result["task_id"] == task_id
        
        # Verify background task was added
        mock_background_tasks.add_task.assert_called_once()

    def test_start_automl_task_not_exists(self):
        """测试启动不存在的任务"""
        from fastapi import HTTPException
        mock_background_tasks = Mock()
        with pytest.raises(HTTPException) as exc_info:
            start_automl_task("nonexistent_task", mock_background_tasks)
        assert exc_info.value.status_code == 404

    def test_start_automl_task_wrong_status(self):
        """测试启动非pending状态的任务"""
        req = AutoMLTaskCreate(
            name="wrong_status_test", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        create_result = create_automl_task(req)
        task_id = create_result["task_id"]
        
        # Manually change status to running
        import api.automl
        api.automl._automl_tasks[task_id]["status"] = "running"
        
        from fastapi import HTTPException
        mock_background_tasks = Mock()
        with pytest.raises(HTTPException) as exc_info:
            start_automl_task(task_id, mock_background_tasks)
        assert exc_info.value.status_code == 400

    def test_get_task_trials_exists(self):
        """测试获取存在的任务试验"""
        req = AutoMLTaskCreate(
            name="trials_test", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        create_result = create_automl_task(req)
        task_id = create_result["task_id"]
        
        result = get_task_trials(task_id)
        assert result["task_id"] == task_id
        assert result["total_trials"] == 0
        assert result["trials"] == []

    def test_get_task_trials_not_exists(self):
        """测试获取不存在任务的试验"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_task_trials("nonexistent_task")
        assert exc_info.value.status_code == 404

    def test_get_best_model_pending(self):
        """测试获取pending任务的最佳模型"""
        req = AutoMLTaskCreate(
            name="best_model_test", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        create_result = create_automl_task(req)
        task_id = create_result["task_id"]
        
        result = get_best_model(task_id)
        assert result["status"] == "pending"
        assert "message" in result

    def test_get_best_model_not_exists(self):
        """测试获取不存在任务的最佳模型"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_best_model("nonexistent_task")
        assert exc_info.value.status_code == 404

    def test_get_leaderboard_exists(self):
        """测试获取存在的任务排行榜"""
        req = AutoMLTaskCreate(
            name="leaderboard_test", task_type="classification", dataset_id="ds1", target_column="t1"
        )
        create_result = create_automl_task(req)
        task_id = create_result["task_id"]
        
        result = get_leaderboard(task_id)
        assert result["task_id"] == task_id
        assert result["total_trials"] == 0
        assert result["leaderboard"] == []

    def test_get_leaderboard_not_exists(self):
        """测试获取不存在任务的排行榜"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_leaderboard("nonexistent_task")
        assert exc_info.value.status_code == 404

    def test_list_supported_models(self):
        """测试列出支持的模型"""
        result = list_supported_models()
        assert "classification" in result
        assert "regression" in result
        assert "time_series" in result
        
        # Check classification models
        class_models = [m["id"] for m in result["classification"]]
        assert "lightgbm" in class_models
        assert "xgboost" in class_models
        assert "random_forest" in class_models
        
        # Check regression models  
        reg_models = [m["id"] for m in result["regression"]]
        assert "linear_regression" in reg_models
        assert "ridge" in reg_models
        
        # Check time series models
        ts_models = [m["id"] for m in result["time_series"]]
        assert "prophet" in ts_models
        assert "arima" in ts_models