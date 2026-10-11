"""配置中心 API。

内置实现尚未持久化。默认禁止写操作，避免调用方得到“保存成功”后在服务
重启时丢失配置。
"""
import os

from fastapi import APIRouter, HTTPException
from services.config_center_service import (
    ConfigScope,
    ConfigType,
    get_config_center_service,
)

router = APIRouter(prefix="/config", tags=["配置中心"])
CONFIG_PERSISTENCE_ENABLED = (
    os.getenv("CONFIG_PERSISTENCE_ENABLED", "false").lower() == "true"
)


def require_persistent_backend() -> None:
    if not CONFIG_PERSISTENCE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="配置中心尚未接入持久化存储，已关闭内存写入",
        )


@router.get("/status")
def get_config_center_status():
    return {
        "available": CONFIG_PERSISTENCE_ENABLED,
        "persistent": CONFIG_PERSISTENCE_ENABLED,
        "message": (
            "配置持久化后端已启用"
            if CONFIG_PERSISTENCE_ENABLED
            else "配置中心尚未接入持久化存储，内存写入已关闭"
        ),
    }


# ========== 命名空间 ==========


@router.get("/namespaces")
def list_namespaces():
    """列出命名空间"""
    service = get_config_center_service()
    return service.list_namespaces()


@router.post("/namespaces")
def create_namespace(req: dict):
    """创建命名空间"""
    require_persistent_backend()
    service = get_config_center_service()
    ns = service.create_namespace(
        name=req.get("name"), description=req.get("description")
    )
    return {"id": ns.id, "name": ns.name, "description": ns.description}


# ========== 配置管理 ==========


@router.get("/configs")
def list_configs(scope: str = None, application: str = None, status: str = None):
    """列出配置"""
    service = get_config_center_service()
    return service.list_configs(scope, application, status)


@router.post("/configs")
def create_config(req: dict):
    """创建配置"""
    require_persistent_backend()
    service = get_config_center_service()
    config = service.create_config(
        name=req.get("name"),
        config_type=req.get("config_type", ConfigType.PROPERTIES),
        content=req.get("content"),
        scope=req.get("scope", ConfigScope.APPLICATION),
        application=req.get("application"),
        description=req.get("description"),
    )
    return {
        "id": config.id,
        "name": config.name,
        "scope": config.scope,
        "version": config.version,
        "status": config.status,
    }


@router.get("/configs/{config_id}")
def get_config(config_id: str):
    """获取配置"""
    service = get_config_center_service()
    config = service.get_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {
        "id": config.id,
        "name": config.name,
        "config_type": config.config_type,
        "content": config.content,
        "scope": config.scope,
        "application": config.application,
        "version": config.version,
        "status": config.status,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


@router.put("/configs/{config_id}")
def update_config(config_id: str, req: dict):
    """更新配置"""
    require_persistent_backend()
    service = get_config_center_service()
    config = service.update_config(
        config_id=config_id, content=req.get("content"), comment=req.get("comment")
    )
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"message": "更新成功", "version": config.version, "status": config.status}


@router.post("/configs/{config_id}/release")
def release_config(config_id: str):
    """发布配置"""
    require_persistent_backend()
    service = get_config_center_service()
    config = service.release_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"message": "发布成功", "version": config.version}


@router.post("/configs/{config_id}/rollback")
def rollback_config(config_id: str, version: int = None):
    """回滚配置"""
    require_persistent_backend()
    service = get_config_center_service()
    if not service.rollback_config(config_id, version):
        raise HTTPException(status_code=400, detail="回滚失败")
    return {"message": "回滚成功"}


@router.delete("/configs/{config_id}")
def delete_config(config_id: str):
    """删除配置"""
    require_persistent_backend()
    service = get_config_center_service()
    if not service.delete_config(config_id):
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"message": "删除成功"}


# ========== 配置版本 ==========


@router.get("/configs/{config_id}/versions")
def get_versions(config_id: str):
    """获取配置版本历史"""
    service = get_config_center_service()
    return service.get_versions(config_id)


@router.get("/configs/{config_id}/versions/{version}")
def get_version(config_id: str, version: int):
    """获取指定版本"""
    service = get_config_center_service()
    v = service.get_version(config_id, version)
    if not v:
        raise HTTPException(status_code=404, detail="版本不存在")
    return v


# ========== 配置监听 ==========


@router.post("/configs/{config_id}/listeners")
def add_listener(config_id: str, req: dict):
    """添加配置监听器"""
    require_persistent_backend()
    service = get_config_center_service()
    listener = service.add_listener(
        config_id=config_id,
        callback_url=req.get("callback_url"),
        events=req.get("events", ["update", "delete"]),
    )
    return {
        "id": listener.id,
        "callback_url": listener.callback_url,
        "events": listener.events,
    }


@router.get("/configs/{config_id}/listeners")
def list_listeners(config_id: str):
    """列出配置监听器"""
    service = get_config_center_service()
    return service.list_listeners(config_id)


@router.delete("/configs/{config_id}/listeners/{listener_id}")
def remove_listener(config_id: str, listener_id: str):
    """移除配置监听器"""
    require_persistent_backend()
    service = get_config_center_service()
    if not service.remove_listener(config_id, listener_id):
        raise HTTPException(status_code=404, detail="监听器不存在")
    return {"message": "移除成功"}


# ========== 统计 ==========


@router.get("/stats")
def get_config_stats():
    """获取配置统计"""
    service = get_config_center_service()
    return service.get_config_stats()
