# -*- coding: utf-8 -*-
"""
模型压缩单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.compression import (
    quantize_model,
    prune_model,
    benchmark_model,
    get_job,
    get_compression_methods
)
from api.compression import QuantizeReq, PruneReq, BenchmarkReq


class TestCompressionUnit:
    """模型压缩单元测试"""

    @pytest.fixture
    def mock_compression_service(self):
        """模拟压缩服务"""
        with patch('api.compression.get_compression_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_quantize_model_success(self, mock_compression_service):
        """测试成功量化模型"""
        mock_compression_service.quantize.return_value = "job123"
        
        req = QuantizeReq(model_id="model1", model_path="/path/to/model.pth", method="dynamic")
        result = quantize_model(req)
        assert result["job_id"] == "job123"
        assert "Quantization started" in result["message"]
        mock_compression_service.quantize.assert_called_once_with("model1", "/path/to/model.pth", "dynamic")

    def test_prune_model_success(self, mock_compression_service):
        """测试成功剪枝模型"""
        mock_compression_service.prune.return_value = "job456"
        
        req = PruneReq(model_id="model2", model_path="/path/to/model.pth", amount=0.3)
        result = prune_model(req)
        assert result["job_id"] == "job456"
        assert "Pruning started" in result["message"]
        mock_compression_service.prune.assert_called_once_with("model2", "/path/to/model.pth", 0.3)

    def test_benchmark_model_success(self, mock_compression_service):
        """测试成功基准测试"""
        mock_result = {
            "original_size_mb": 150.5,
            "compressed_size_mb": 75.2,
            "compression_ratio": 2.0,
            "inference_time_ms": 15.3,
            "accuracy_drop": 0.02
        }
        mock_compression_service.benchmark.return_value = mock_result
        
        req = BenchmarkReq(model_path="/path/to/model.pth", input_shape=[1, 3, 224, 224])
        result = benchmark_model(req)
        assert result["original_size_mb"] == 150.5
        assert result["compression_ratio"] == 2.0
        mock_compression_service.benchmark.assert_called_once_with("/path/to/model.pth", [1, 3, 224, 224])

    def test_get_job_exists(self, mock_compression_service):
        """测试获取存在的压缩任务"""
        mock_job = {
            "id": "job123",
            "model_id": "model1",
            "status": "completed",
            "method": "dynamic",
            "start_time": "2026-04-07T20:00:00Z",
            "end_time": "2026-04-07T20:05:00Z",
            "result_path": "/path/to/quantized_model.pth"
        }
        mock_compression_service.get_job.return_value = mock_job
        
        result = get_job("job123")
        assert result["id"] == "job123"
        assert result["status"] == "completed"
        assert result["method"] == "dynamic"
        mock_compression_service.get_job.assert_called_once_with("job123")

    def test_get_job_not_exists(self, mock_compression_service):
        """测试获取不存在的压缩任务"""
        mock_compression_service.get_job.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_job("nonexistent")
        assert exc_info.value.status_code == 404

    def test_get_compression_methods(self):
        """测试获取压缩方法"""
        result = get_compression_methods()
        assert "methods" in result
        assert "pruning" in result
        
        # Check quantization methods
        methods = result["methods"]
        assert len(methods) == 3
        method_ids = [m["id"] for m in methods]
        assert "dynamic" in method_ids
        assert "static" in method_ids
        assert "qat" in method_ids
        
        # Check pruning methods
        pruning_methods = result["pruning"]
        assert len(pruning_methods) == 2
        pruning_ids = [p["id"] for p in pruning_methods]
        assert "magnitude" in pruning_ids
        assert " Lottery" in pruning_ids
        
        # Check descriptions and ratios
        dynamic_method = next(m for m in methods if m["id"] == "dynamic")
        assert "动态量化" in dynamic_method["name"]
        assert "2-4x" in dynamic_method["compression_ratio"]
        
        static_method = next(m for m in methods if m["id"] == "static")
        assert "静态量化" in static_method["name"]
        assert "3-4x" in static_method["compression_ratio"]
        
        qat_method = next(m for m in methods if m["id"] == "qat")
        assert "量化感知训练" in qat_method["name"]
        assert "4x" in qat_method["compression_ratio"]