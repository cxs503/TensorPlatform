# -*- coding: utf-8 -*-
"""
高级功能单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import numpy as np

from api.advanced_features import (
    create_nas_job,
    list_nas_jobs,
    get_nas_job,
    quantize_model,
    prune_model,
    distill_model,
    get_model_info,
    benchmark_model,
    create_distributed_job,
    list_distributed_jobs,
    get_distributed_job,
    get_cluster_info,
    estimate_training_time,
    health_check
)
from api.advanced_features import (
    NASRequest,
    QuantizationRequest,
    PruningRequest,
    DistillationRequest,
    DistributedTrainingRequest
)


class TestAdvancedFeaturesUnit:
    """高级功能单元测试"""

    @pytest.fixture
    def mock_services(self):
        """模拟所有服务"""
        with patch('api.advanced_features.nas_service') as mock_nas, \
             patch('api.advanced_features.quant_service') as mock_quant, \
             patch('api.advanced_features.dist_service') as mock_dist:
            yield {
                'nas': mock_nas,
                'quant': mock_quant,
                'dist': mock_dist
            }

    def test_create_nas_job(self, mock_services):
        """测试创建NAS任务"""
        req = NASRequest(
            name="test_nas",
            X=[[1.0, 2.0], [3.0, 4.0]],
            y=[0, 1],
            n_trials=10,
            epochs=5
        )
        
        mock_services['nas'].create_job.return_value = "nas_123"
        mock_services['nas'].start_search.return_value = None
        
        result = create_nas_job(req)
        assert result["job_id"] == "nas_123"
        assert result["status"] == "started"
        mock_services['nas'].create_job.assert_called_once()
        mock_services['nas'].start_search.assert_called_once()

    def test_list_nas_jobs(self, mock_services):
        """测试列出NAS任务"""
        mock_jobs = [{"id": "nas_123", "name": "test"}, {"id": "nas_456", "name": "test2"}]
        mock_services['nas'].list_jobs.return_value = mock_jobs
        
        result = list_nas_jobs()
        assert result["total"] == 2
        assert len(result["items"]) == 2
        mock_services['nas'].list_jobs.assert_called_once()

    def test_get_nas_job(self, mock_services):
        """测试获取NAS任务状态"""
        mock_status = {"id": "nas_123", "status": "running"}
        mock_services['nas'].get_job_status.return_value = mock_status
        
        result = get_nas_job("nas_123")
        assert result["id"] == "nas_123"
        mock_services['nas'].get_job_status.assert_called_once_with("nas_123")

    def test_quantize_model_dynamic(self, mock_services):
        """测试动态量化模型"""
        req = QuantizationRequest(
            model_path="/path/to/model.pth",
            quantization_type="dynamic"
        )
        
        mock_result = {"status": "success", "output_path": "/path/to/quantized.pth"}
        mock_services['quant'].quantize_pytorch_dynamic.return_value = mock_result
        
        result = quantize_model(req)
        assert result["status"] == "success"
        mock_services['quant'].quantize_pytorch_dynamic.assert_called_once()

    def test_quantize_model_onnx(self, mock_services):
        """测试ONNX量化模型"""
        req = QuantizationRequest(
            model_path="/path/to/model.onnx",
            quantization_type="onnx"
        )
        
        mock_result = {"status": "success", "output_path": "/path/to/quantized.onnx"}
        mock_services['quant'].quantize_sklearn_model.return_value = mock_result
        
        result = quantize_model(req)
        assert result["status"] == "success"
        mock_services['quant'].quantize_sklearn_model.assert_called_once()

    def test_quantize_model_invalid_type(self):
        """测试无效量化类型"""
        req = QuantizationRequest(
            model_path="/path/to/model.pth",
            quantization_type="invalid_type"
        )
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            quantize_model(req)
        assert exc_info.value.status_code == 400

    def test_prune_model(self, mock_services):
        """测试剪枝模型"""
        req = PruningRequest(
            model_path="/path/to/model.pth",
            pruning_ratio=0.5
        )
        
        mock_result = {"status": "success", "output_path": "/path/to/pruned.pth"}
        mock_services['quant'].prune_model.return_value = mock_result
        
        result = prune_model(req)
        assert result["status"] == "success"
        mock_services['quant'].prune_model.assert_called_once()

    def test_distill_model(self, mock_services):
        """测试知识蒸馏"""
        req = DistillationRequest(
            teacher_path="/path/to/teacher.pth",
            X=[[1.0, 2.0], [3.0, 4.0]],
            y=[0, 1],
            epochs=5
        )
        
        mock_result = {"status": "success", "student_path": "/path/to/student.pth"}
        mock_services['quant'].knowledge_distillation.return_value = mock_result
        
        result = distill_model(req)
        assert result["status"] == "success"
        mock_services['quant'].knowledge_distillation.assert_called_once()

    def test_get_model_info(self, mock_services):
        """测试获取模型信息"""
        mock_info = {"model_type": "pytorch", "size_mb": 100}
        mock_services['quant'].get_model_info.return_value = mock_info
        
        result = get_model_info("/path/to/model.pth")
        assert result["model_type"] == "pytorch"
        mock_services['quant'].get_model_info.assert_called_once_with("/path/to/model.pth")

    def test_benchmark_model(self, mock_services):
        """测试模型基准测试"""
        mock_result = {"avg_time_ms": 10.5, "std_time_ms": 0.8}
        mock_services['quant'].benchmark_model.return_value = mock_result
        
        result = benchmark_model("/path/to/model.pth", [[1.0, 2.0], [3.0, 4.0]], 100)
        assert "avg_time_ms" in result
        mock_services['quant'].benchmark_model.assert_called_once()

    def test_create_distributed_job(self, mock_services):
        """测试创建分布式训练任务"""
        req = DistributedTrainingRequest(
            name="test_dist",
            X=[[1.0, 2.0], [3.0, 4.0]],
            y=[0, 1],
            epochs=10,
            num_workers=2
        )
        
        mock_services['dist'].create_job.return_value = "dist_123"
        mock_services['dist'].start_training.return_value = None
        
        result = create_distributed_job(req)
        assert result["job_id"] == "dist_123"
        assert result["status"] == "started"
        mock_services['dist'].create_job.assert_called_once()
        mock_services['dist'].start_training.assert_called_once()

    def test_list_distributed_jobs(self, mock_services):
        """测试列出分布式训练任务"""
        mock_jobs = [{"id": "dist_123", "name": "test"}, {"id": "dist_456", "name": "test2"}]
        mock_services['dist'].list_jobs.return_value = mock_jobs
        
        result = list_distributed_jobs()
        assert result["total"] == 2
        assert len(result["items"]) == 2
        mock_services['dist'].list_jobs.assert_called_once()

    def test_get_distributed_job(self, mock_services):
        """测试获取分布式训练任务状态"""
        mock_status = {"id": "dist_123", "status": "training"}
        mock_services['dist'].get_job_status.return_value = mock_status
        
        result = get_distributed_job("dist_123")
        assert result["id"] == "dist_123"
        mock_services['dist'].get_job_status.assert_called_once_with("dist_123")

    def test_get_cluster_info(self, mock_services):
        """测试获取集群信息"""
        mock_info = {"nodes": 4, "gpus_per_node": 8, "total_gpus": 32}
        mock_services['dist'].get_cluster_info.return_value = mock_info
        
        result = get_cluster_info()
        assert result["nodes"] == 4
        mock_services['dist'].get_cluster_info.assert_called_once()

    def test_estimate_training_time(self, mock_services):
        """测试估算训练时间"""
        mock_estimate = {"estimated_hours": 2.5, "factors": {"dataset_size": "large"}}
        mock_services['dist'].estimate_training_time.return_value = mock_estimate
        
        result = estimate_training_time(10000, 1000000, 10, 32)
        assert result["estimated_hours"] == 2.5
        mock_services['dist'].estimate_training_time.assert_called_once()

    def test_health_check(self):
        """测试健康检查 - 这个函数不依赖服务"""
        result = health_check()
        expected_features = [
            "neural_architecture_search",
            "model_quantization",
            "model_pruning",
            "knowledge_distillation",
            "distributed_training",
            "gradient_accumulation",
            "mixed_precision",
        ]
        assert result["status"] == "healthy"
        assert result["services"] == {
            "nas": "available",
            "quantization": "available",
            "distributed_training": "available",
        }
        assert result["features"] == expected_features