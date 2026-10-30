# -*- coding: utf-8 -*-
"""
算法商店单元测试：持久化算法资产、分类和训练适配器。
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.algorithm_store import (
    list_algorithm_models,
    get_algorithm_model,
    list_categories,
    list_adapters,
    create_algorithm_model,
    AlgorithmCreateReq,
    recommend,
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

    @pytest.fixture
    def db(self):
        db = MagicMock()
        db.query.return_value.all.return_value = []
        return db

    def test_list_algorithm_models_all(self, mock_algorithm_store, db):
        """测试列出所有算法模型"""
        mock_result = {"total": 50, "items": [{"id": "model1", "name": "Linear Regression"}, {"id": "model2", "name": "Random Forest"}]}
        mock_algorithm_store.list_models.return_value = mock_result
        
        # Pass explicit None values to avoid Query object issues
        result = list_algorithm_models(category=None, search=None, page=1, size=20, db=db)
        assert result["total"] == 50
        assert len(result["items"]) == 2
        mock_algorithm_store.list_models.assert_called_once_with(
            db, category=None, search=None, page=1, size=20
        )

    def test_list_algorithm_models_filtered(self, mock_algorithm_store, db):
        """测试带过滤条件列出算法模型"""
        mock_result = {"total": 5, "items": [{"id": "model1", "name": "Deep Learning Model"}]}
        mock_algorithm_store.list_models.return_value = mock_result
        
        result = list_algorithm_models(category="uploaded_source", search="neural", page=2, size=10, db=db)
        assert result["total"] == 5
        mock_algorithm_store.list_models.assert_called_once_with(
            db, category="uploaded_source", search="neural", page=2, size=10
        )

    def test_get_algorithm_model_exists(self, mock_algorithm_store, db):
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
        
        result = get_algorithm_model(123, db=db)
        assert result["id"] == "model123"
        assert result["name"] == "Gradient Boosting"
        mock_algorithm_store.get_model.assert_called_once_with(db, 123)

    def test_get_algorithm_model_not_exists(self, mock_algorithm_store, db):
        """测试获取不存在的算法模型"""
        mock_algorithm_store.get_model.return_value = None
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            get_algorithm_model(999, db=db)
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
        assert mock_algorithm_store.get_categories.call_count == 2

    def test_get_category_models_exists(self, mock_algorithm_store, db):
        """分类通过 /models 的过滤参数查询，不再使用旧分类专用接口。"""
        mock_models = [{"id": "clf1", "name": "Logistic Regression"}, {"id": "clf2", "name": "SVM"}]
        mock_algorithm_store.list_models.return_value = {"total": 2, "items": mock_models}
        
        result = list_algorithm_models(category="builtin", search=None, page=1, size=20, db=db)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        mock_algorithm_store.list_models.assert_called_once_with(
            db, category="builtin", search=None, page=1, size=20
        )

    def test_get_category_models_empty(self, mock_algorithm_store, db):
        """测试获取空分类下的模型"""
        mock_algorithm_store.list_models.return_value = {"total": 0, "items": []}
        
        result = list_algorithm_models(category="empty_category", search=None, page=1, size=20, db=db)
        assert result["total"] == 0
        assert len(result["items"]) == 0
        mock_algorithm_store.list_models.assert_called_once_with(
            db, category="empty_category", search=None, page=1, size=20
        )

    def test_list_adapters(self):
        """旧模板已由显式训练适配器注册表替代。"""
        result = list_adapters()
        assert len(result["items"]) > 0
        assert len({(item["id"], item["task_type"]) for item in result["items"]}) == len(result["items"])

    def test_adapter_metadata(self):
        result = list_adapters()
        for item in result["items"]:
            assert item["task_type"]
            assert item["algorithm_type"]
            assert item["runtime_model_type"]
            assert item["runtime_task_type"]

    def test_metadata_only_template_is_rejected(self, db):
        """当前资产必须有真实文件，不能恢复旧的无文件模板。"""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            create_algorithm_model(AlgorithmCreateReq(name="metadata only"), db=db)
        assert exc_info.value.status_code == 400
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_get_recommended_models_default(self, mock_algorithm_store, db):
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
        
        mock_algorithm_store.recommend.return_value = {
            "total": 7, "items": mock_model_values,
        }
        result = recommend(task=None, category=None, db=db)
        assert isinstance(result["items"], list)
        assert len(result["items"]) == 7
        assert result["items"][0]["id"] == "model1"
        assert result["items"][5]["id"] == "model6"
        mock_algorithm_store.recommend.assert_called_once_with(db, None, None)

    def test_get_recommended_models_by_use_case(self, mock_algorithm_store, db):
        """测试按使用场景获取推荐模型"""
        mock_result = {"items": [{"id": "nlp1", "name": "NLP Model"}, {"id": "nlp2", "name": "Text Classification"}]}
        mock_algorithm_store.recommend.return_value = mock_result
        
        result = recommend(task="natural language processing", category="builtin", db=db)
        assert len(result["items"]) == 2
        assert "nlp1" in [item["id"] for item in result["items"]]
        mock_algorithm_store.recommend.assert_called_once_with(db, "natural language processing", "builtin")