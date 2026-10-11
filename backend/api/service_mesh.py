"""
服务网格 API
"""
from fastapi import APIRouter, HTTPException
from services.service_mesh_service import get_service_mesh_service

router = APIRouter(prefix="/mesh", tags=["服务网格"])


# ========== 服务管理 ==========


@router.get("/services")
def list_services(namespace: str = None):
    """列出服务"""
    service = get_service_mesh_service()
    return service.list_services(namespace)


@router.post("/services")
def register_service(req: dict):
    """注册服务"""
    service = get_service_mesh_service()
    svc = service.register_service(
        name=req.get("name"), namespace=req.get("namespace", "default")
    )
    return {
        "name": svc.name,
        "namespace": svc.namespace,
        "version": svc.version,
        "instance_count": len(svc.instances),
    }


@router.get("/services/{namespace}/{name}")
def get_service(namespace: str, name: str):
    """获取服务"""
    service = get_service_mesh_service()
    svc = service.get_service(name, namespace)
    if not svc:
        raise HTTPException(status_code=404, detail="服务不存在")
    return {
        "name": svc.name,
        "namespace": svc.namespace,
        "version": svc.version,
        "instances": [
            {
                "id": i.id,
                "host": i.host,
                "port": i.port,
                "version": i.version,
                "status": i.status,
                "weight": i.weight,
                "last_heartbeat": i.last_heartbeat,
            }
            for i in svc.instances
        ],
    }


# ========== 服务实例管理 ==========


@router.post("/services/{namespace}/{name}/instances")
def register_instance(namespace: str, name: str, req: dict):
    """注册服务实例"""
    service = get_service_mesh_service()
    instance = service.register_instance(
        service_name=name,
        namespace=namespace,
        instance_id=req.get("instance_id"),
        host=req.get("host"),
        port=req.get("port"),
        metadata=req.get("metadata"),
    )
    if not instance:
        raise HTTPException(status_code=404, detail="服务不存在")
    return {
        "id": instance.id,
        "host": instance.host,
        "port": instance.port,
        "status": instance.status,
    }


@router.delete("/services/{namespace}/{name}/instances/{instance_id}")
def deregister_instance(namespace: str, name: str, instance_id: str):
    """注销服务实例"""
    service = get_service_mesh_service()
    if not service.deregister_instance(name, namespace, instance_id):
        raise HTTPException(status_code=404, detail="实例不存在")
    return {"message": "注销成功"}


@router.post("/services/{namespace}/{name}/instances/{instance_id}/heartbeat")
def heartbeat(namespace: str, name: str, instance_id: str):
    """服务心跳"""
    service = get_service_mesh_service()
    if not service.heartbeat(name, namespace, instance_id):
        raise HTTPException(status_code=404, detail="实例不存在")
    return {"message": "心跳更新成功"}


# ========== 服务发现 ==========


@router.get("/resolve/{namespace}/{name}")
def resolve_service(namespace: str, name: str):
    """服务发现"""
    service = get_service_mesh_service()
    instances = service.resolve_service(name, namespace)
    return {"service_name": name, "namespace": namespace, "instances": instances}


# ========== 流量路由 ==========


@router.post("/routes")
def create_route(req: dict):
    """创建流量路由"""
    service = get_service_mesh_service()
    route = service.create_route(
        service_name=req.get("service_name"), subsets=req.get("subsets")
    )
    return {
        "id": route.id,
        "service_name": route.service_name,
        "subsets": route.subsets,
        "enabled": route.enabled,
    }


@router.get("/routes/{service_name}")
def get_route(service_name: str):
    """获取流量路由"""
    service = get_service_mesh_service()
    route = service.get_route(service_name)
    if not route:
        raise HTTPException(status_code=404, detail="路由不存在")
    return route


@router.put("/routes/{service_name}")
def update_route(service_name: str, req: dict):
    """更新流量路由"""
    service = get_service_mesh_service()
    route = service.update_route(service_name=service_name, subsets=req.get("subsets"))
    if not route:
        raise HTTPException(status_code=404, detail="路由不存在")
    return {"message": "更新成功", "subsets": route.subsets}


@router.delete("/routes/{service_name}")
def delete_route(service_name: str):
    """删除流量路由"""
    service = get_service_mesh_service()
    if not service.delete_route(service_name):
        raise HTTPException(status_code=404, detail="路由不存在")
    return {"message": "删除成功"}


# ========== 熔断器 ==========


@router.get("/circuit-breakers")
def list_circuit_breakers():
    """列出熔断器"""
    service = get_service_mesh_service()
    return service.list_circuit_breakers()


@router.get("/circuit-breakers/{service_name}")
def get_circuit_breaker(service_name: str):
    """获取熔断器状态"""
    service = get_service_mesh_service()
    cb = service.get_circuit_breaker(service_name)
    if not cb:
        raise HTTPException(status_code=404, detail="熔断器不存在")
    return {
        "service_name": cb.service_name,
        "state": cb.state,
        "failure_count": cb.failure_count,
        "failure_threshold": cb.failure_threshold,
        "timeout": cb.timeout,
    }


@router.post("/circuit-breakers/{service_name}/success")
def record_success(service_name: str):
    """记录成功调用"""
    service = get_service_mesh_service()
    if not service.record_success(service_name):
        raise HTTPException(status_code=404, detail="服务不存在")
    return {"message": "成功记录"}


@router.post("/circuit-breakers/{service_name}/failure")
def record_failure(service_name: str):
    """记录失败调用"""
    service = get_service_mesh_service()
    if not service.record_failure(service_name):
        raise HTTPException(status_code=404, detail="服务不存在")
    return {"message": "失败记录"}


@router.get("/circuit-breakers/{service_name}/status")
def check_circuit_status(service_name: str):
    """检查熔断状态"""
    service = get_service_mesh_service()
    is_open = service.is_open(service_name)
    return {"service_name": service_name, "circuit_open": is_open}


# ========== 限流 ==========


@router.post("/rate-limits")
def create_rate_limit(req: dict):
    """创建限流规则"""
    service = get_service_mesh_service()
    rule = service.create_rate_limit(
        service_name=req.get("service_name"),
        requests_per_second=req.get("requests_per_second"),
        burst=req.get("burst"),
    )
    return {
        "id": rule.id,
        "service_name": rule.service_name,
        "requests_per_second": rule.requests_per_second,
        "burst": rule.burst,
    }


@router.delete("/rate-limits/{service_name}")
def delete_rate_limit(service_name: str):
    """删除限流规则"""
    service = get_service_mesh_service()
    if not service.delete_rate_limit(service_name):
        raise HTTPException(status_code=404, detail="限流规则不存在")
    return {"message": "删除成功"}


# ========== 统计 ==========


@router.get("/stats")
def get_mesh_stats():
    """获取网格统计"""
    service = get_service_mesh_service()
    return service.get_mesh_stats()
