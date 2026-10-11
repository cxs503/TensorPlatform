"""
数据缓存 API
"""
from fastapi import APIRouter, HTTPException
from services.cache_service import CacheType, EvictionPolicy, get_cache_service

router = APIRouter(prefix="/cache", tags=["数据缓存"])


# ========== 集群管理 ==========


@router.get("/clusters")
def list_clusters():
    """列出缓存集群"""
    service = get_cache_service()
    return service.list_clusters()


@router.post("/clusters")
def create_cluster(req: dict):
    """创建缓存集群"""
    service = get_cache_service()
    cluster = service.create_cluster(
        name=req.get("name"),
        cache_type=req.get("cache_type", CacheType.REDIS),
        nodes=req.get("nodes"),
    )
    return {
        "id": cluster.id,
        "name": cluster.name,
        "cache_type": cluster.cache_type,
        "status": cluster.status,
    }


# ========== 缓存操作 ==========


@router.post("")
def set_cache(req: dict):
    """设置缓存"""
    service = get_cache_service()
    success = service.set(
        key=req.get("key"),
        value=req.get("value"),
        ttl=req.get("ttl"),
        namespace=req.get("namespace", "default"),
    )
    if not success:
        raise HTTPException(status_code=400, detail="设置失败")
    return {"message": "设置成功"}


@router.get("")
def get_cache(key: str, namespace: str = "default"):
    """获取缓存"""
    service = get_cache_service()
    value = service.get(key, namespace)
    return {"key": key, "value": value, "exists": value is not None}


@router.delete("")
def delete_cache(key: str, namespace: str = "default"):
    """删除缓存"""
    service = get_cache_service()
    success = service.delete(key, namespace)
    if not success:
        raise HTTPException(status_code=404, detail="键不存在")
    return {"message": "删除成功"}


@router.get("/exists")
def check_exists(key: str, namespace: str = "default"):
    """检查键是否存在"""
    service = get_cache_service()
    return {"exists": service.exists(key, namespace)}


@router.post("/expire")
def set_expire(key: str, ttl: int, namespace: str = "default"):
    """设置过期时间"""
    service = get_cache_service()
    success = service.expire(key, ttl, namespace)
    if not success:
        raise HTTPException(status_code=404, detail="键不存在")
    return {"message": "设置成功"}


@router.get("/ttl")
def get_ttl(key: str, namespace: str = "default"):
    """获取剩余过期时间"""
    service = get_cache_service()
    return {"ttl": service.ttl(key, namespace)}


# ========== 批量操作 ==========


@router.post("/mget")
def mget_cache(req: dict):
    """批量获取"""
    service = get_cache_service()
    keys = req.get("keys", [])
    namespace = req.get("namespace", "default")
    values = service.mget(keys, namespace)
    return {"values": values}


@router.post("/mset")
def mset_cache(req: dict):
    """批量设置"""
    service = get_cache_service()
    items = req.get("items", {})
    ttl = req.get("ttl")
    namespace = req.get("namespace", "default")
    success = service.mset(items, ttl, namespace)
    if not success:
        raise HTTPException(status_code=400, detail="设置失败")
    return {"message": "设置成功"}


# ========== 模式匹配 ==========


@router.get("/keys")
def find_keys(pattern: str, namespace: str = "default"):
    """查找键"""
    service = get_cache_service()
    keys = service.keys(pattern, namespace)
    return {"keys": keys, "count": len(keys)}


@router.delete("/keys")
def delete_keys(pattern: str, namespace: str = "default"):
    """删除匹配的键"""
    service = get_cache_service()
    count = service.delete_pattern(pattern, namespace)
    return {"message": f"删除{count}个键", "count": count}


# ========== 策略管理 ==========


@router.get("/policies")
def list_policies():
    """列出缓存策略"""
    service = get_cache_service()
    return [
        {
            "id": p.id,
            "name": p.name,
            "namespace": p.namespace,
            "ttl": p.ttl,
            "max_size": p.max_size,
            "eviction_policy": p.eviction_policy,
        }
        for p in service.policies.values()
    ]


@router.post("/policies")
def create_policy(req: dict):
    """创建缓存策略"""
    service = get_cache_service()
    policy = service.create_policy(
        name=req.get("name"),
        namespace=req.get("namespace"),
        ttl=req.get("ttl", 3600),
        max_size=req.get("max_size", 1000),
        eviction_policy=req.get("eviction_policy", EvictionPolicy.LRU),
    )
    return {"id": policy.id, "name": policy.name, "namespace": policy.namespace}


# ========== 统计 ==========


@router.get("/stats")
def get_cache_stats():
    """获取缓存统计"""
    service = get_cache_service()
    return service.get_stats()


@router.post("/clear")
def clear_cache(namespace: str = None):
    """清空缓存"""
    service = get_cache_service()
    service.clear(namespace)
    return {"message": "清空成功"}
