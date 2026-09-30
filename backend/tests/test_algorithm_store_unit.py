# -*- coding: utf-8 -*-
"""
算法商店单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.algorithm_store import (
    list_algorithm_models,
    get_algorithm_model,
    list_categories,
    get_category_models,
    list_templates,
    get_template,
    get_recommended_models
)


class TestAlgorithmStoreUnit:
    """算法商店单元测试"""

    @pytest.fixture
    def mock_algorithm_store(self):
        """模拟算法商店服务"""
        with patch('api.algorithm_store.get_algorithm_store') as mock_get_store:
            mock_store = Mock()
            mock_get_store.return_value = mock_store
            yield mock_store

    def test_list_algorithm_models_all(self, mock_algorithm_store):
        """测试列出所有算法模型"""
        mock_result = {"total": 50, "items": [{"id": "model1", "name": "Linear Regression"}, {"id": "model2", "name": "Random Forest"}]}
        mock_algorithm_store.list_models.return_value = mock_result
        
        # Pass explicit None values to avoid Query object issues
        result = list_algorithm_models(category=None, difficulty=None, framework=None, search=None, page=1, size=20)
        assert result["total"] == 50
        assert len(result["items"]) == 2
        mock_algorithm_store.list_models.assert_called_once_with(
            category=None, difficulty=None, framework=None, search=None, page=1, size=20
        )

    def test_list_algorithm_models_filtered(self, mock_algorithm_store):
        """测试带过滤条件列出算法模型"""
        mock_result = {"total": 5, "items": [{"id": "model1", "name": "Deep Learning Model"}]}
        mock_algorithm_store.list_models.return_value = mock_result
        
        result = list_algorithm_models(category="deep_learning", difficulty="advanced", framework="pytorch", search="neural", page=2, size=10)
        assert result["total"] == 5
        mock_algorithm_store.list_models.assert_called_once_with(
            category="deep_learning", difficulty="advanced", framework="pytorch", search="neural", page=2, size=10
        )

    def test_get_algorithm_model_exists(self, mock_algorithm_store):
        """测试获取存在的算法模型"""
        mock_model = {
            "id": "model123",
            "name": "Gradient Boosting",
            "category": "ensemble",
            "difficulty": "intermediate",
            "framework": "sklearn",
            "description": "Powerful ensemble method"
        }
        mock_algorithm_store.get_model.return_value = mock_model
        
        result = get_algorithm_model("model123")
        assert result["id"] == "model123"
        assert result["name"] == "Gradient Boosting"
        mock_algorithm_store.get_model.assert_called_once_with("model123")

    def test_get_algorithm_model_not_exists(self, mock_algorithm_store):
        """测试获取不存在的算法模型"""
        mock_algorithm_store.get_model.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_algorithm_model("nonexistent")
        assert exc_info.value.status_code == 404

    def test_list_categories(self, mock_algorithm_store):
        """测试列出算法分类"""
        mock_categories = ["classification", "regression", "clustering", "deep_learning"]
        mock_algorithm_store.categories = mock_categories
        mock_algorithm_store.get_categories.return_value = mock_categories
        
        result = list_categories()
        assert result["total"] == 4
        assert len(result["items"]) == 4
        assert "classification" in result["items"]
        mock_algorithm_store.get_categories.assert_called_once()

    def test_get_category_models_exists(self, mock_algorithm_store):
        """测试获取存在的分类下的模型"""
        mock_models = [{"id": "clf1", "name": "Logistic Regression"}, {"id": "clf2", "name": "SVM"}]
        mock_algorithm_store.get_category_models.return_value = mock_models
        
        result = get_category_models("classification")
        assert result["total"] == 2
        assert result["category"] == "classification"
        assert len(result["items"]) == 2
        mock_algorithm_store.get_category_models.assert_called_once_with("classification")

    def test_get_category_models_empty(self, mock_algorithm_store):
        """测试获取空分类下的模型"""
        mock_algorithm_store.get_category_models.return_value = []
        
        result = get_category_models("empty_category")
        assert result["total"] == 0
        assert result["category"] == "empty_category"
        assert len(result["items"]) == 0
        mock_algorithm_store.get_category_models.assert_called_once_with("empty_category")

    def test_list_templates(self, mock_algorithm_store):
        """测试列出算法模板"""
        mock_templates = [{"id": "template1", "name": "Basic ML Pipeline"}, {"id": "template2", "name": "Deep Learning Template"}]
        mock_algorithm_store.templates = mock_templates
        mock_algorithm_store.get_templates.return_value = mock_templates
        
        result = list_templates()
        assert result["total"] == 2
        assert len(result["items"]) == 2
        mock_algorithm_store.get_templates.assert_called_once()

    def test_get_template_exists(self, mock_algorithm_store):
        """测试获取存在的模板"""
        mock_template = {
            "id": "template123",
            "name": "Advanced NLP Pipeline",
            "code": "import torch\n# NLP pipeline code...",
            "framework": "pytorch"
        }
        mock_algorithm_store.get_template.return_value = mock_template
        
        result = get_template("template123")
        assert result["id"] == "template123"
        assert result["name"] == "Advanced NLP Pipeline"
        mock_algorithm_store.get_template.assert_called_once_with("template123")

    def test_get_template_not_exists(self, mock_algorithm_store):
        """测试获取不存在的模板"""
        mock_algorithm_store.get_template.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_template("nonexistent")
        assert exc_info.value.status_code == 404

    def test_get_recommended_models_default(self, mock_algorithm_store):
        """测试获取默认推荐模型"""
        # Create mock model data
        mock_model_values = [
            {"id": "model1", "name": "Popular Model 1"},
            {"id": "model2", "name": "Popular Model 2"},
            {"id": "model3", "name": "Popular Model 3"},
            {"id": "model4", "name": "Popular Model 4"},
            {"id": "model5", "name": "Popular Model 5"},
            {"id": "model6", "name": "Popular Model 6"},
            {"id": "model7", "name": "Popular Model 7"}
        ]
        
        # Configure models as a MagicMock with values method
        mock_models = MagicMock()
        mock_models.values.return_value = mock_model_values
        mock_algorithm_store.models = mock_models
        
        result = get_recommended_models(use_case=None)
        assert isinstance(result["items"], list)
        assert len(result["items"]) == 6  # Default top 6
        assert result["items"][0]["id"] == "model1"
        assert result["items"][5]["id"] == "model6"

    def test_get_recommended_models_by_use_case(self, mock_algorithm_store):
        """测试按使用场景获取推荐模型"""
        mock_result = {"items": [{"id": "nlp1", "name": "NLP Model"}, {"id": "nlp2", "name": "Text Classification"}]}
        mock_algorithm_store.list_models.return_value = mock_result
        
        result = get_recommended_models(use_case="natural language processing")
        assert len(result["items"]) == 2
        assert "nlp1" in [item["id"] for item in result["items"]]
        mock_algorithm_store.list_models.assert_called_once_with(search="natural language processing", size=5)