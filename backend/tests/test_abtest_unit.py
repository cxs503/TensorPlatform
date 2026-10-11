# -*- coding: utf-8 -*-
"""
AB测试单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch

from api.abtest import (
    create_experiment,
    start_experiment,
    pause_experiment,
    end_experiment,
    list_experiments,
    get_experiment,
    calculate_sample_size,
    health_check
)
from api.abtest import ExperimentConfig


class TestABTestUnit:
    """AB测试单元测试"""

    @pytest.fixture
    def mock_abtest_service(self):
        """模拟AB测试服务"""
        with patch('api.abtest.abtest_service') as mock_service:
            yield mock_service

    def test_create_experiment(self, mock_abtest_service):
        """测试创建实验"""
        config = ExperimentConfig(
            name="test_experiment",
            description="Test experiment",
            model_a="model_v1",
            model_b="model_v2"
        )
        
        # 模拟服务返回值
        mock_abtest_service.create_experiment.return_value = "exp_123"
        
        result = create_experiment(config)
        assert result["experiment_id"] == "exp_123"
        assert result["status"] == "created"
        mock_abtest_service.create_experiment.assert_called_once()

    def test_start_experiment(self, mock_abtest_service):
        """测试启动实验"""
        mock_abtest_service.start_experiment.return_value = {"status": "started"}
        
        result = start_experiment("exp_123")
        assert result["status"] == "started"
        mock_abtest_service.start_experiment.assert_called_once_with("exp_123")

    def test_pause_experiment(self, mock_abtest_service):
        """测试暂停实验"""
        mock_abtest_service.pause_experiment.return_value = {"status": "paused"}
        
        result = pause_experiment("exp_123")
        assert result["status"] == "paused"
        mock_abtest_service.pause_experiment.assert_called_once_with("exp_123")

    def test_end_experiment(self, mock_abtest_service):
        """测试结束实验"""
        mock_abtest_service.end_experiment.return_value = {"status": "ended", "winner": "a"}
        
        result = end_experiment("exp_123", "a")
        assert result["status"] == "ended"
        mock_abtest_service.end_experiment.assert_called_once_with("exp_123", "a")

    def test_list_experiments(self, mock_abtest_service):
        """测试列出实验"""
        mock_return_value = {"items": [{"id": "exp_123"}, {"id": "exp_456"}], "total": 2}
        mock_abtest_service.list_experiments.return_value = mock_return_value
        
        result = list_experiments("active")
        assert "items" in result
        assert "total" in result
        assert result["total"] == 2
        mock_abtest_service.list_experiments.assert_called_once_with("active")

    def test_get_experiment(self, mock_abtest_service):
        """测试获取实验详情"""
        mock_abtest_service.get_experiment.return_value = {"id": "exp_123", "name": "test"}
        
        result = get_experiment("exp_123")
        assert result["id"] == "exp_123"
        mock_abtest_service.get_experiment.assert_called_once_with("exp_123")

    def test_calculate_sample_size(self, mock_abtest_service):
        """测试计算样本量"""
        mock_abtest_service.calculate_sample_size.return_value = 1000
        
        result = calculate_sample_size(0.1, 0.05)
        assert result == 1000
        mock_abtest_service.calculate_sample_size.assert_called_once_with(0.1, 0.05)

    def test_health_check(self):
        """测试健康检查 - 这个函数不依赖服务"""
        result = health_check()
        expected_features = [
            "experiment_management",
            "traffic_routing", 
            "statistical_analysis",
            "auto_switching",
            "sample_size_calculation"
        ]
        assert result["status"] == "healthy"
        assert result["service"] == "abtest"
        assert result["features"] == expected_features