"""
数据缓存服务
提供分布式缓存层、热数据加速、缓存策略、Redis集群管理
"""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

import redis


class CacheType(str, Enum):
    """缓存类型"""

    REDIS = "redis"
    MEMCACHED = "memcached"
    IN_MEMORY = "in_memory"


class EvictionPolicy(str, Enum):
    """淘汰策略"""

    LRU = "lru"  # 最近最少使用
    LFU = "lfu"  # 最不经常使用
    FIFO = "fifo"  # 先进先出
    TTL = "ttl"  # 基于过期时间


class CacheCluster:
    """缓存集群"""

    def __init__(
        self, name: str, cache_type: str = CacheType.REDIS, nodes: List[Dict] = None
    ):
        self.id = f"cluster_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.cache_type = cache_type
        self.nodes = nodes or []
        self.status = "active"
        self.created_at = datetime.now().isoformat()


class CacheKey:
    """缓存键"""

    def __init__(
        self, key: str, value: Any, ttl: int = 3600, namespace: str = "default"
    ):
        self.key = key
        self.namespace = namespace
        self.full_key = f"{namespace}:{key}"
        self.value = value
        self.ttl = ttl
        self.created_at = time.time()
        self.access_count = 0
        self.last_access = time.time()


class CachePolicy:
    """缓存策略"""

    def __init__(
        self,
        name: str,
        namespace: str,
        ttl: int = 3600,
        max_size: int = 1000,
        eviction_policy: str = EvictionPolicy.LRU,
    ):
        self.id = f"policy_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.namespace = namespace
        self.ttl = ttl
        self.max_size = max_size
        self.eviction_policy = eviction_policy


class CacheStats:
    """缓存统计"""

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.sets = 0
        self.deletes = 0


class DataCacheService:
    """数据缓存服务"""

    def __init__(self):
        self.clusters: Dict[str, CacheCluster] = {}
        self.in_memory_cache: Dict[str, CacheKey] = {}
        self.policies: Dict[str, CachePolicy] = {}
        self.stats = CacheStats()
        self._init_default_cluster()
        self._init_default_policy()

    def _init_default_cluster(self):
        """初始化默认集群"""
        cluster = CacheCluster(
            name="default",
            cache_type=CacheType.IN_MEMORY,
            nodes=[{"host": "localhost", "port": 6379}],
        )
        self.clusters[cluster.id] = cluster

    def _init_default_policy(self):
        """初始化默认策略"""
        policy = CachePolicy(
            name="默认策略",
            namespace="default",
            ttl=3600,
            max_size=10000,
            eviction_policy=EvictionPolicy.LRU,
        )
        self.policies[policy.id] = policy

    # ========== 集群管理 ==========

    def create_cluster(
        self, name: str, cache_type: str = CacheType.REDIS, nodes: List[Dict] = None
    ) -> CacheCluster:
        """创建缓存集群"""
        cluster = CacheCluster(name, cache_type, nodes)
        self.clusters[cluster.id] = cluster
        return cluster

    def get_cluster(self, cluster_id: str) -> Optional[CacheCluster]:
        """获取集群"""
        return self.clusters.get(cluster_id)

    def list_clusters(self) -> List[Dict]:
        """列出集群"""
        return [
            {
                "id": c.id,
                "name": c.name,
                "cache_type": c.cache_type,
                "nodes": c.nodes,
                "status": c.status,
                "created_at": c.created_at,
            }
            for c in self.clusters.values()
        ]

    # ========== 缓存操作 ==========

    def set(
        self, key: str, value: Any, ttl: int = None, namespace: str = "default"
    ) -> bool:
        """设置缓存"""
        # 获取命名空间策略
        policy = self._get_policy(namespace)
        if not policy:
            return False

        # 使用策略的TTL
        if ttl is None:
            ttl = policy.ttl

        # 检查容量
        self._check_capacity(policy)

        # 创建缓存键
        cache_key = CacheKey(key, value, ttl, namespace)
        full_key = cache_key.full_key

        # 检查是否已存在
        if full_key in self.in_memory_cache:
            self.stats.sets += 1

        self.in_memory_cache[full_key] = cache_key
        return True

    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """获取缓存"""
        full_key = f"{namespace}:{key}"
        cache_key = self.in_memory_cache.get(full_key)

        if not cache_key:
            self.stats.misses += 1
            return None

        # 检查TTL
        if time.time() - cache_key.created_at > cache_key.ttl:
            del self.in_memory_cache[full_key]
            self.stats.misses += 1
            return None

        # 更新访问统计
        cache_key.access_count += 1
        cache_key.last_access = time.time()
        self.stats.hits += 1

        return cache_key.value

    def delete(self, key: str, namespace: str = "default") -> bool:
        """删除缓存"""
        full_key = f"{namespace}:{key}"
        if full_key in self.in_memory_cache:
            del self.in_memory_cache[full_key]
            self.stats.deletes += 1
            return True
        return False

    def exists(self, key: str, namespace: str = "default") -> bool:
        """检查键是否存在"""
        full_key = f"{namespace}:{key}"
        cache_key = self.in_memory_cache.get(full_key)
        if not cache_key:
            return False
        if time.time() - cache_key.created_at > cache_key.ttl:
            del self.in_memory_cache[full_key]
            return False
        return True

    def expire(self, key: str, ttl: int, namespace: str = "default") -> bool:
        """设置过期时间"""
        full_key = f"{namespace}:{key}"
        cache_key = self.in_memory_cache.get(full_key)
        if not cache_key:
            return False
        cache_key.ttl = ttl
        return True

    def ttl(self, key: str, namespace: str = "default") -> int:
        """获取剩余过期时间"""
        full_key = f"{namespace}:{key}"
        cache_key = self.in_memory_cache.get(full_key)
        if not cache_key:
            return -2
        remaining = cache_key.ttl - (time.time() - cache_key.created_at)
        return int(remaining) if remaining > 0 else -1

    # ========== 批量操作 ==========

    def mset(
        self, items: Dict[str, Any], ttl: int = None, namespace: str = "default"
    ) -> bool:
        """批量设置"""
        for key, value in items.items():
            if not self.set(key, value, ttl, namespace):
                return False
        return True

    def mget(self, keys: List[str], namespace: str = "default") -> List[Any]:
        """批量获取"""
        return [self.get(key, namespace) for key in keys]

    # ========== 模式匹配 ==========

    def keys(self, pattern: str, namespace: str = "default") -> List[str]:
        """查找键"""
        prefix = f"{namespace}:"
        full_pattern = f"{prefix}{pattern}"
        result = []
        for full_key in self.in_memory_cache.keys():
            # 简单匹配
            if full_key.startswith(prefix):
                key = full_key[len(prefix) :]
                if "*" in pattern:
                    if self._match_pattern(key, pattern):
                        result.append(key)
                else:
                    if key == pattern:
                        result.append(key)
        return result

    def _match_pattern(self, key: str, pattern: str) -> bool:
        """简单模式匹配"""
        import fnmatch

        return fnmatch.fnmatch(key, pattern)

    def delete_pattern(self, pattern: str, namespace: str = "default") -> int:
        """删除匹配的键"""
        keys = self.keys(pattern, namespace)
        count = 0
        for key in keys:
            if self.delete(key, namespace):
                count += 1
        return count

    # ========== 缓存策略 ==========

    def create_policy(
        self,
        name: str,
        namespace: str,
        ttl: int = 3600,
        max_size: int = 1000,
        eviction_policy: str = EvictionPolicy.LRU,
    ) -> CachePolicy:
        """创建缓存策略"""
        policy = CachePolicy(name, namespace, ttl, max_size, eviction_policy)
        self.policies[policy.id] = policy
        return policy

    def _get_policy(self, namespace: str) -> Optional[CachePolicy]:
        """获取命名空间的策略"""
        for policy in self.policies.values():
            if policy.namespace == namespace:
                return policy
        return None

    def _check_capacity(self, policy: CachePolicy):
        """检查容量并淘汰"""
        namespace_prefix = f"{policy.namespace}:"
        namespace_keys = [
            k for k in self.in_memory_cache.keys() if k.startswith(namespace_prefix)
        ]

        if len(namespace_keys) >= policy.max_size:
            # 淘汰
            self._evict(policy, namespace_keys)

    def _evict(self, policy: CachePolicy, keys: List[str]):
        """淘汰缓存"""
        if policy.eviction_policy == EvictionPolicy.LRU:
            # 最近最少使用
            sorted_keys = sorted(
                keys, key=lambda k: self.in_memory_cache[k].last_access
            )
            to_evict = sorted_keys[:10]  # 淘汰10个
        elif policy.eviction_policy == EvictionPolicy.LFU:
            # 最不经常使用
            sorted_keys = sorted(
                keys, key=lambda k: self.in_memory_cache[k].access_count
            )
            to_evict = sorted_keys[:10]
        elif policy.eviction_policy == EvictionPolicy.FIFO:
            # 先进先出
            sorted_keys = sorted(keys, key=lambda k: self.in_memory_cache[k].created_at)
            to_evict = sorted_keys[:10]
        else:
            to_evict = keys[:10]

        for key in to_evict:
            del self.in_memory_cache[key]
            self.stats.evictions += 1

    # ========== 统计 ==========

    def get_stats(self) -> Dict:
        """获取缓存统计"""
        total_requests = self.stats.hits + self.stats.misses
        hit_rate = (self.stats.hits / total_requests * 100) if total_requests > 0 else 0

        return {
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "hit_rate": round(hit_rate, 2),
            "evictions": self.stats.evictions,
            "sets": self.stats.sets,
            "deletes": self.stats.deletes,
            "total_keys": len(self.in_memory_cache),
            "clusters": len(self.clusters),
            "policies": len(self.policies),
        }

    def clear(self, namespace: str = None):
        """清空缓存"""
        if namespace:
            prefix = f"{namespace}:"
            keys = [k for k in self.in_memory_cache.keys() if k.startswith(prefix)]
            for key in keys:
                del self.in_memory_cache[key]
        else:
            self.in_memory_cache.clear()


# 全局实例
_cache_service: Optional[DataCacheService] = None


def get_cache_service() -> DataCacheService:
    """获取缓存服务"""
    global _cache_service
    if _cache_service is None:
        _cache_service = DataCacheService()
    return _cache_service


# ============================================================================
# Redis缓存后端 (合并自 services/redis_cache.py)
# ============================================================================

# Redis连接配置
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# 缓存键命名规范
CACHE_PREFIX = "foundation:cache"
CACHE_NAMESPACES = {
    "project": f"{CACHE_PREFIX}:project",
    "user": f"{CACHE_PREFIX}:user",
    "dataset": f"{CACHE_PREFIX}:dataset",
    "model": f"{CACHE_PREFIX}:model",
    "training": f"{CACHE_PREFIX}:training",
    "deployment": f"{CACHE_PREFIX}:deployment",
    "dashboard": f"{CACHE_PREFIX}:dashboard",
    "list": f"{CACHE_PREFIX}:list",
}

# 缓存过期时间(秒)
CACHE_TTL = {
    "project": 300,  # 项目信息 5分钟
    "project_list": 60,  # 项目列表 1分钟
    "user": 600,  # 用户信息 10分钟
    "dataset": 300,  # 数据集 5分钟
    "model": 300,  # 模型 5分钟
    "training": 30,  # 训练任务 30秒
    "deployment": 60,  # 部署 1分钟
    "dashboard": 30,  # 仪表盘 30秒
    "statistics": 60,  # 统计数据 1分钟
}


class RedisCacheService:
    """Redis缓存服务"""

    def __init__(self):
        self._client = None
        self._enabled = os.getenv("REDIS_ENABLED", "true").lower() == "true"

    @property
    def client(self) -> redis.Redis:
        """懒加载Redis连接"""
        if self._client is None:
            try:
                self._client = redis.Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    db=REDIS_DB,
                    password=REDIS_PASSWORD,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True,
                )
                # 测试连接
                self._client.ping()
            except Exception as e:
                print(f"[RedisCache] 连接失败: {e}")
                self._enabled = False
                self._client = None
        return self._client

    def is_enabled(self) -> bool:
        """检查缓存是否启用"""
        if not self._enabled:
            return False
        try:
            self.client.ping()
            return True
        except:
            return False

    def _build_key(self, namespace: str, key: str) -> str:
        """构建缓存键"""
        prefix = CACHE_NAMESPACES.get(namespace, f"{CACHE_PREFIX}:{namespace}")
        return f"{prefix}:{key}"

    def _hash_key(self, data: dict) -> str:
        """生成哈希键"""
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.md5(data_str.encode()).hexdigest()[:12]

    def get(self, namespace: str, key: str) -> Optional[Any]:
        """获取缓存"""
        if not self.is_enabled():
            return None

        try:
            full_key = self._build_key(namespace, key)
            value = self.client.get(full_key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            print(f"[RedisCache] 获取缓存失败: {e}")
            return None

    def set(self, namespace: str, key: str, value: Any, ttl: int = None) -> bool:
        """设置缓存"""
        if not self.is_enabled():
            return False

        try:
            full_key = self._build_key(namespace, key)
            if ttl is None:
                ttl = CACHE_TTL.get(namespace, 300)

            self.client.setex(full_key, ttl, json.dumps(value, default=str))
            return True
        except Exception as e:
            print(f"[RedisCache] 设置缓存失败: {e}")
            return False

    def delete(self, namespace: str, key: str) -> bool:
        """删除缓存"""
        if not self.is_enabled():
            return False

        try:
            full_key = self._build_key(namespace, key)
            self.client.delete(full_key)
            return True
        except Exception as e:
            print(f"[RedisCache] 删除缓存失败: {e}")
            return False

    def delete_pattern(self, namespace: str, pattern: str = "*") -> int:
        """删除匹配的缓存"""
        if not self.is_enabled():
            return 0

        try:
            prefix = CACHE_NAMESPACES.get(namespace, f"{CACHE_PREFIX}:{namespace}")
            full_pattern = f"{prefix}:{pattern}"
            keys = self.client.keys(full_pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            print(f"[RedisCache] 批量删除缓存失败: {e}")
            return 0

    def invalidate_user_cache(self, user_id: str):
        """使用户相关缓存失效"""
        self.delete_pattern("project", f"user:{user_id}:*")
        self.delete_pattern("user", user_id)

    def invalidate_project_cache(self, project_id: str = None, user_id: str = None):
        """使项目相关缓存失效"""
        if project_id:
            self.delete("project", project_id)
        if user_id:
            self.delete_pattern("project", f"user:{user_id}:*")
        self.delete("list", "projects")
        self.delete("dashboard", "statistics")

    def get_or_set(
        self, namespace: str, key: str, callback: Callable, ttl: int = None
    ) -> Any:
        """获取缓存，不存在则执行回调并缓存结果"""
        # 尝试从缓存获取
        cached = self.get(namespace, key)
        if cached is not None:
            cached["_cached"] = True
            return cached

        # 执行回调
        result = callback()
        if result is not None:
            self.set(namespace, key, result, ttl)
            result["_cached"] = False
        return result

    def health_check(self) -> dict:
        """健康检查"""
        result = {
            "enabled": self._enabled,
            "connected": False,
            "host": REDIS_HOST,
            "port": REDIS_PORT,
        }

        if self._enabled:
            try:
                self.client.ping()
                result["connected"] = True
                result["latency_ms"] = (
                    round(self.client.latency_doctor(), 2)
                    if hasattr(self.client, "latency_doctor")
                    else 0
                )
            except Exception as e:
                result["error"] = str(e)

        return result


# 全局Redis缓存实例
_redis_cache_service: Optional[RedisCacheService] = None


def get_redis_cache() -> RedisCacheService:
    """获取Redis缓存服务实例"""
    global _redis_cache_service
    if _redis_cache_service is None:
        _redis_cache_service = RedisCacheService()
    return _redis_cache_service


# 缓存装饰器
def cache_result(namespace: str, key_builder: Callable = None, ttl: int = None):
    """
    缓存结果装饰器

    Args:
        namespace: 缓存命名空间
        key_builder: 缓存键生成函数
        ttl: 过期时间(秒)
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_redis_cache()

            # 构建缓存键
            if key_builder:
                key = key_builder(*args, **kwargs)
            else:
                # 默认使用函数参数构建键
                key_parts = [func.__name__]
                key_parts.extend(str(arg) for arg in args[1:])  # 跳过self/db
                key_parts.extend(f"{k}={v}" for k, v in kwargs.items())
                key = ":".join(key_parts)

            # 尝试从缓存获取
            cached = cache.get(namespace, key)
            if cached is not None:
                cached["_cached"] = True
                return cached

            # 执行函数
            result = func(*args, **kwargs)

            # 缓存结果
            if result is not None:
                cache.set(namespace, key, result, ttl)
                if isinstance(result, dict):
                    result["_cached"] = False

            return result

        return wrapper

    return decorator
