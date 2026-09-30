# -*- coding: utf-8 -*-
"""
缓存单元测试 - 直接测试函数而非API端点
"""
import pytest
from unittest.mock import Mock, patch, MagicMock

from api.cache import (
    list_clusters,
    create_cluster,
    set_cache,
    get_cache,
    delete_cache,
    check_exists,
    set_expire,
    get_ttl,
    mget_cache,
    mset_cache,
    find_keys,
    delete_keys,
    list_policies,
    create_policy,
    get_cache_stats,
    clear_cache
)


class TestCacheUnit:
    """缓存单元测试"""

    @pytest.fixture
    def mock_cache_service(self):
        """模拟缓存服务"""
        with patch('api.cache.get_cache_service') as mock_get_service:
            mock_service = Mock()
            mock_get_service.return_value = mock_service
            yield mock_service

    def test_list_clusters(self, mock_cache_service):
        """测试列出缓存集群"""
        mock_clusters = [{"id": "cluster1", "name": "test"}, {"id": "cluster2", "name": "prod"}]
        mock_cache_service.list_clusters.return_value = mock_clusters
        
        result = list_clusters()
        assert len(result) == 2
        mock_cache_service.list_clusters.assert_called_once()

    def test_create_cluster(self, mock_cache_service):
        """测试创建缓存集群"""
        mock_cluster = Mock()
        mock_cluster.id = "cluster123"
        mock_cluster.name = "test_cluster"
        mock_cluster.cache_type = "redis"
        mock_cluster.status = "active"
        mock_cache_service.create_cluster.return_value = mock_cluster
        
        req = {"name": "test_cluster", "cache_type": "redis", "nodes": 3}
        result = create_cluster(req)
        assert result["id"] == "cluster123"
        assert result["name"] == "test_cluster"
        mock_cache_service.create_cluster.assert_called_once()

    def test_set_cache_success(self, mock_cache_service):
        """测试设置缓存成功"""
        mock_cache_service.set.return_value = True
        
        req = {"key": "test_key", "value": "test_value", "ttl": 3600}
        result = set_cache(req)
        assert result["message"] == "设置成功"
        mock_cache_service.set.assert_called_once()

    def test_set_cache_failure(self, mock_cache_service):
        """测试设置缓存失败"""
        mock_cache_service.set.return_value = False
        
        from fastapi import HTTPException
        req = {"key": "test_key", "value": "test_value"}
        with pytest.raises(HTTPException) as exc_info:
            set_cache(req)
        assert exc_info.value.status_code == 400

    def test_get_cache_exists(self, mock_cache_service):
        """测试获取存在的缓存"""
        mock_cache_service.get.return_value = "test_value"
        
        result = get_cache("test_key")
        assert result["key"] == "test_key"
        assert result["value"] == "test_value"
        assert result["exists"] is True
        mock_cache_service.get.assert_called_once_with("test_key", "default")

    def test_get_cache_not_exists(self, mock_cache_service):
        """测试获取不存在的缓存"""
        mock_cache_service.get.return_value = None
        
        result = get_cache("nonexistent_key")
        assert result["exists"] is False
        mock_cache_service.get.assert_called_once_with("nonexistent_key", "default")

    def test_delete_cache_success(self, mock_cache_service):
        """测试删除缓存成功"""
        mock_cache_service.delete.return_value = True
        
        result = delete_cache("test_key")
        assert result["message"] == "删除成功"
        mock_cache_service.delete.assert_called_once_with("test_key", "default")

    def test_delete_cache_failure(self, mock_cache_service):
        """测试删除不存在的缓存"""
        mock_cache_service.delete.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_cache("nonexistent_key")
        assert exc_info.value.status_code == 404

    def test_check_exists_true(self, mock_cache_service):
        """测试检查键存在"""
        mock_cache_service.exists.return_value = True
        
        result = check_exists("test_key")
        assert result["exists"] is True
        mock_cache_service.exists.assert_called_once_with("test_key", "default")

    def test_check_exists_false(self, mock_cache_service):
        """测试检查键不存在"""
        mock_cache_service.exists.return_value = False
        
        result = check_exists("nonexistent_key")
        assert result["exists"] is False
        mock_cache_service.exists.assert_called_once_with("nonexistent_key", "default")

    def test_set_expire_success(self, mock_cache_service):
        """测试设置过期时间成功"""
        mock_cache_service.expire.return_value = True
        
        result = set_expire("test_key", 3600)
        assert result["message"] == "设置成功"
        mock_cache_service.expire.assert_called_once_with("test_key", 3600, "default")

    def test_set_expire_failure(self, mock_cache_service):
        """测试设置不存在键的过期时间"""
        mock_cache_service.expire.return_value = False
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            set_expire("nonexistent_key", 3600)
        assert exc_info.value.status_code == 404

    def test_get_ttl(self, mock_cache_service):
        """测试获取剩余过期时间"""
        mock_cache_service.ttl.return_value = 1800
        
        result = get_ttl("test_key")
        assert result["ttl"] == 1800
        mock_cache_service.ttl.assert_called_once_with("test_key", "default")

    def test_mget_cache(self, mock_cache_service):
        """测试批量获取缓存"""
        mock_values = ["value1", "value2", None]
        mock_cache_service.mget.return_value = mock_values
        
        req = {"keys": ["key1", "key2", "key3"], "namespace": "test"}
        result = mget_cache(req)
        assert result["values"] == mock_values
        mock_cache_service.mget.assert_called_once_with(["key1", "key2", "key3"], "test")

    def test_mset_cache_success(self, mock_cache_service):
        """测试批量设置缓存成功"""
        mock_cache_service.mset.return_value = True
        
        req = {"items": {"key1": "value1", "key2": "value2"}, "ttl": 3600, "namespace": "test"}
        result = mset_cache(req)
        assert result["message"] == "设置成功"
        mock_cache_service.mset.assert_called_once_with({"key1": "value1", "key2": "value2"}, 3600, "test")

    def test_mset_cache_failure(self, mock_cache_service):
        """测试批量设置缓存失败"""
        mock_cache_service.mset.return_value = False
        
        from fastapi import HTTPException
        req = {"items": {"key1": "value1"}}
        with pytest.raises(HTTPException) as exc_info:
            mset_cache(req)
        assert exc_info.value.status_code == 400

    def test_find_keys(self, mock_cache_service):
        """测试查找键"""
        mock_keys = ["key1", "key2", "key3"]
        mock_cache_service.keys.return_value = mock_keys
        
        result = find_keys("key*", "test")
        assert result["keys"] == mock_keys
        assert result["count"] == 3
        mock_cache_service.keys.assert_called_once_with("key*", "test")

    def test_delete_keys(self, mock_cache_service):
        """测试删除匹配的键"""
        mock_cache_service.delete_pattern.return_value = 5
        
        result = delete_keys("temp_*", "test")
        assert result["count"] == 5
        assert "删除5个键" in result["message"]
        mock_cache_service.delete_pattern.assert_called_once_with("temp_*", "test")

    def test_list_policies(self, mock_cache_service):
        """测试列出缓存策略"""
        mock_policy1 = Mock()
        mock_policy1.id = "policy1"
        mock_policy1.name = "default"
        mock_policy1.namespace = "default"
        mock_policy1.ttl = 3600
        mock_policy1.max_size = 1000
        mock_policy1.eviction_policy = "lru"
        
        mock_policy2 = Mock()
        mock_policy2.id = "policy2"
        mock_policy2.name = "high_priority"
        mock_policy2.namespace = "critical"
        mock_policy2.ttl = 7200
        mock_policy2.max_size = 5000
        mock_policy2.eviction_policy = "lfu"
        
        mock_cache_service.policies = {
            "policy1": mock_policy1,
            "policy2": mock_policy2
        }
        
        result = list_policies()
        assert len(result) == 2
        assert result[0]["id"] == "policy1"
        assert result[1]["id"] == "policy2"

    def test_create_policy(self, mock_cache_service):
        """测试创建缓存策略"""
        mock_policy = Mock()
        mock_policy.id = "new_policy"
        mock_policy.name = "test_policy"
        mock_policy.namespace = "test"
        mock_cache_service.create_policy.return_value = mock_policy
        
        req = {"name": "test_policy", "namespace": "test", "ttl": 1800, "max_size": 2000}
        result = create_policy(req)
        assert result["id"] == "new_policy"
        assert result["name"] == "test_policy"
        assert result["namespace"] == "test"
        mock_cache_service.create_policy.assert_called_once()

    def test_get_cache_stats(self, mock_cache_service):
        """测试获取缓存统计"""
        mock_stats = {"hits": 1000, "misses": 200, "keys": 500, "memory_usage_mb": 128}
        mock_cache_service.get_stats.return_value = mock_stats
        
        result = get_cache_stats()
        assert result == mock_stats
        mock_cache_service.get_stats.assert_called_once()

    def test_clear_cache_with_namespace(self, mock_cache_service):
        """测试清空指定命名空间的缓存"""
        clear_cache("test")
        mock_cache_service.clear.assert_called_once_with("test")

    def test_clear_cache_all(self, mock_cache_service):
        """测试清空所有缓存"""
        clear_cache()
        mock_cache_service.clear.assert_called_once_with(None)