"""
日志服务API
"""
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from services.logging_service import get_log_service

router = APIRouter(prefix="/logs", tags=["日志服务"])


class LogEventReq(BaseModel):
    event_type: str
    message: str
    level: str = "info"


class AuditLogReq(BaseModel):
    action: str
    resource_type: str
    resource_id: str
    details: dict = {}


@router.get("")
def search_logs(
    query: Optional[str] = Query(None, description="搜索关键词"),
    level: Optional[str] = Query(None, description="日志级别"),
    service: Optional[str] = Query(None, description="服务名"),
    user_id: Optional[str] = Query(None, description="用户ID"),
    limit: int = Query(100, ge=1, le=1000, description="返回数量"),
):
    """搜索日志"""
    service = get_log_service()
    results = service.search(
        query=query,
        level=level,
        service=service,
        user_id=user_id,
        limit=limit,
    )
    return {"total": len(results), "items": results}


@router.get("/recent")
def get_recent_logs(
    limit: int = Query(100, ge=1, le=1000),
    level: Optional[str] = Query(None),
):
    """获取最近日志"""
    service = get_log_service()
    logs = service.get_recent_logs(limit, level)
    return {"total": len(logs), "items": logs}


@router.get("/access")
def get_access_logs(
    limit: int = Query(100, ge=1, le=1000),
):
    """获取访问日志"""
    service = get_log_service()
    logs = service.search(level="info", limit=limit)
    return {"total": len(logs), "items": logs}


@router.get("/errors")
def get_error_logs(
    limit: int = Query(100, ge=1, le=1000),
):
    """获取错误日志"""
    service = get_log_service()
    logs = service.search(level="error", limit=limit)
    return {"total": len(logs), "items": logs}


@router.get("/audit")
def get_audit_logs(
    limit: int = Query(100, ge=1, le=1000),
    user_id: Optional[str] = Query(None),
):
    """获取审计日志"""
    service = get_log_service()
    logs = service.search(level="audit", user_id=user_id, limit=limit)
    return {"total": len(logs), "items": logs}


@router.get("/statistics")
def get_log_statistics():
    """获取日志统计"""
    service = get_log_service()
    return service.get_statistics()


@router.post("/event")
def log_event(req: LogEventReq):
    """记录事件日志"""
    service = get_log_service()
    service.log_event(req.event_type, req.message, req.level)
    return {"message": "Log recorded"}


@router.post("/audit")
def log_audit(req: AuditLogReq, user_id: str = "system"):
    """记录审计日志"""
    service = get_log_service()
    service.log_audit(
        req.action, req.resource_type, req.resource_id, user_id, req.details
    )
    return {"message": "Audit log recorded"}


@router.get("/status")
def get_logging_status():
    """获取日志服务状态"""
    service = get_log_service()
    return {
        "elk_available": service._es is not None,
        "elasticsearch_host": service.es_host,
        "elasticsearch_port": service.es_port,
        "index": service.es_index,
        "cache_size": len(service.log_cache),
        "log_directory": str(service.log_dir),
    }
