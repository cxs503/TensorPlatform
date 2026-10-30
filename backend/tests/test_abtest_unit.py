# -*- coding: utf-8 -*-
"""
持久化 AB 实验单元测试，不再调用旧内存服务。
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

from api.abtest import (
    create_experiment,
    start_experiment,
    end_experiment,
    list_experiments,
    get_experiment,
    calc_sample_size,
    health_check
)
from api.abtest import ExperimentConfig


class TestABTestUnit:
    """AB测试单元测试"""

    @pytest.fixture
    def db(self):
        return MagicMock()

    @pytest.fixture
    def user(self):
        return {"id": 123, "role": "data_scientist", "organization_id": 7}

    def test_create_experiment(self, db, user):
        """测试创建实验"""
        config = ExperimentConfig(
            name="test_experiment",
            deployment_a_id=1,
            deployment_b_id=2,
        )
        
        with patch("api.abtest._deployment_info", side_effect=[
            {"model_id": 10, "deployment_id": 1, "organization_id": 7},
            {"model_id": 20, "deployment_id": 2, "organization_id": 7},
        ]), patch("api.abtest._validate_pair") as validate:
            result = create_experiment(config, db=db, current_user=user)
        assert result["experiment_id"].startswith("exp_")
        assert result["status"] == "created"
        validate.assert_called_once()
        assert db.execute.call_args.args[1] == {
            "experiment_id": result["experiment_id"], "name": "test_experiment",
            "model_a_id": 10, "model_b_id": 20, "deployment_a_id": 1,
            "deployment_b_id": 2, "organization_id": 7,
            "metric": "accuracy", "traffic_split_a": 50.0,
        }
        db.commit.assert_called_once()

    def test_start_experiment(self, db, user):
        """测试启动实验"""
        db.execute.return_value.mappings.return_value.first.return_value = {
            "status": "created", "organization_id": 7,
            "deployment_a_id": 1, "deployment_b_id": 2,
        }
        with patch("api.abtest._deployment_info", side_effect=[
            {"deployment_id": 1}, {"deployment_id": 2},
        ]), patch("api.abtest._validate_pair") as validate:
            result = start_experiment("exp_123", db=db, current_user=user)
        assert result["status"] == "running"
        assert db.execute.call_args.args[1]["experiment_id"] == "exp_123"
        validate.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.parametrize("status", ["running", "completed"])
    def test_start_experiment_rejects_invalid_transition(self, db, user, status):
        """当前生命周期仅允许 created -> running -> completed。"""
        db.execute.return_value.mappings.return_value.first.return_value = {
            "status": status, "organization_id": 7,
        }
        with pytest.raises(HTTPException) as exc_info:
            start_experiment("exp_123", db=db, current_user=user)
        assert exc_info.value.status_code == 409
        db.commit.assert_not_called()

    def test_end_experiment(self, db, user):
        """测试结束实验"""
        db.execute.return_value.mappings.return_value.first.return_value = {
            "status": "running", "organization_id": 7,
        }
        with patch("api.abtest._analyze_experiment", return_value={
            "significant": True, "best_variant": "A",
        }):
            result = end_experiment("exp_123", db=db, current_user=user)
        assert result["status"] == "completed"
        assert result["winner"] == "A"
        assert db.execute.call_args.args[1]["winner"] == "A"
        db.commit.assert_called_once()

    def test_list_experiments(self, db, user):
        """测试列出实验"""
        rows = [{"experiment_id": "exp_123"}, {"experiment_id": "exp_456"}]
        db.execute.return_value.mappings.return_value.all.return_value = rows
        result = list_experiments("running", db=db, current_user=user)
        assert len(result) == 2
        assert result[0]["experiment_id"] == "exp_123"
        assert db.execute.call_args.args[1] == {"status": "running", "organization_id": 7}
        assert "e.organization_id = :organization_id" in str(db.execute.call_args.args[0])

    def test_get_experiment(self, db, user):
        """测试获取实验详情"""
        db.execute.return_value.mappings.return_value.first.return_value = {
            "experiment_id": "exp_123", "name": "test", "organization_id": 7,
        }
        with patch("api.abtest._request_stats", return_value={"request_count": 3}):
            result = get_experiment("exp_123", db=db, current_user=user)
        assert result["experiment_id"] == "exp_123"
        assert result["name"] == "test"
        assert result["request_count"] == 3
        assert db.execute.call_args.args[1] == {"experiment_id": "exp_123"}

    def test_calculate_sample_size(self):
        """测试计算样本量"""
        result = calc_sample_size("accuracy", 0.1, 0.05, 0.8, 0.05)
        assert result["required_per_variant"] > 0
        assert result["total"] == 2 * result["required_per_variant"]

    @pytest.mark.parametrize("operation", [get_experiment, start_experiment, end_experiment])
    def test_cross_tenant_access_denied(self, db, user, operation):
        db.execute.return_value.mappings.return_value.first.return_value = {
            "experiment_id": "exp_123", "organization_id": 8, "status": "running",
        }
        with pytest.raises(HTTPException) as exc_info:
            operation("exp_123", db=db, current_user=user)
        assert exc_info.value.status_code == 404
        db.commit.assert_not_called()

    def test_health_check(self):
        """测试健康检查 - 这个函数不依赖服务"""
        result = health_check()
        expected_features = [
            "deployment_validation", "stable_traffic_routing", "baseline_fallback",
            "feedback_collection", "statistical_analysis", "sample_size_calculation",
        ]
        assert result["status"] == "healthy"
        assert result["service"] == "abtest"
        assert result["features"] == expected_features