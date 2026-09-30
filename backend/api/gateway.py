"""
API网关管理 API
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from services.gateway_service import RouteMethod, get_gateway_service

router = APIRouter(prefix="/gateway", tags=["API网关"])


@router.get("/stats")
def get_stats():
    """获取网关统计"""
    service = get_gateway_service()
    return service.get_gateway_stats()


# ========== 后端管理 ==========


@router.post("/backends")
def add_backend(req: dict):
    """添加后端服务"""
    service = get_gateway_service()
    backend = service.add_backend(
        name=req.get("name"),
        url=req.get("url"),
        weight=req.get("weight", 1),
        metadata=req.get("metadata", {}),
    )
    return {
        "id": backend.id,
        "name": backend.name,
        "url": backend.url,
        "weight": backend.weight,
    }


@router.get("/backends")
def list_backends():
    """列出后端服务"""
    service = get_gateway_service()
    return [
        {
            "id": b.id,
            "name": b.name,
            "url": b.url,
            "weight": b.weight,
            "health_status": b.health_status,
            "response_time_avg": round(b.response_time_avg, 2),
        }
        for b in service.backends.values()
    ]


@router.delete("/backends/{backend_id}")
def remove_backend(backend_id: str):
    """移除后端服务"""
    service = get_gateway_service()
    if not service.remove_backend(backend_id):
        raise HTTPException(status_code=404, detail="后端不存在")
    return {"message": "删除成功"}


# ========== 路由管理 ==========


@router.post("/routes")
def add_route(req: dict):
    """添加路由"""
    service = get_gateway_service()
    route = service.add_route(
        path=req.get("path"),
        method=req.get("method", "GET"),
        backend_url=req.get("backend_url"),
        backends=req.get("backends"),
        timeout=req.get("timeout", 30),
        retries=req.get("retries", 3),
        rate_limit=req.get("rate_limit", 100),
        auth_required=req.get("auth_required", False),
        plugins=req.get("plugins", []),
    )
    return {
        "id": route.id,
        "path": route.path,
        "method": route.method,
        "backend_url": route.backend_url,
        "enabled": route.enabled,
    }


@router.get("/routes")
def list_routes():
    """列出路由"""
    service = get_gateway_service()
    return [
        {
            "id": r.id,
            "path": r.path,
            "method": r.method,
            "backend_url": r.backend_url,
            "enabled": r.enabled,
            "rate_limit": r.rate_limit,
            "auth_required": r.auth_required,
        }
        for r in service.routes.values()
    ]


@router.get("/routes/{route_id}")
def get_route(route_id: str):
    """获取路由详情"""
    service = get_gateway_service()
    route = service.routes.get(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由不存在")
    return {
        "id": route.id,
        "path": route.path,
        "method": route.method,
        "backend_url": route.backend_url,
        "backends": route.backends,
        "timeout": route.timeout,
        "retries": route.retries,
        "rate_limit": route.rate_limit,
        "auth_required": route.auth_required,
        "plugins": route.plugins,
        "enabled": route.enabled,
    }


@router.put("/routes/{route_id}")
def update_route(route_id: str, req: dict):
    """更新路由"""
    service = get_gateway_service()
    route = service.routes.get(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="路由不存在")

    # 更新字段
    if "enabled" in req:
        route.enabled = req["enabled"]
    if "rate_limit" in req:
        route.rate_limit = req["rate_limit"]
        service.rate_limiters[route_id] = service.rate_limiters.get(
            route_id, service.rate_limiters.get("default", object())
        )
    if "backends" in req:
        route.backends = req["backends"]

    return {"message": "更新成功"}


@router.delete("/routes/{route_id}")
def remove_route(route_id: str):
    """移除路由"""
    service = get_gateway_service()
    if not service.remove_route(route_id):
        raise HTTPException(status_code=404, detail="路由不存在")
    return {"message": "删除成功"}


# ========== 速率限制 ==========


@router.get("/rate-limit/{route_id}/{client_key}")
def check_rate_limit(route_id: str, client_key: str):
    """检查速率限制"""
    service = get_gateway_service()
    allowed = service.check_rate_limit(route_id, client_key)
    remaining = service.get_rate_limit_remaining(route_id, client_key)
    return {"allowed": allowed, "remaining": remaining, "reset_in": 60}
