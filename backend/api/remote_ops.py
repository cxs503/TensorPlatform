"""远程运维 API。

没有设备代理时不允许返回模拟操作成功。只有显式启用并接入真实远程执行
后端后，写操作才会开放。
"""
import os

from fastapi import APIRouter, HTTPException
from services.remote_ops_service import (
    OperationStatus,
    OperationType,
    get_remote_ops_service,
)

router = APIRouter(prefix="/remote-ops", tags=["远程运维"])
REMOTE_OPS_ENABLED = os.getenv("REMOTE_OPS_ENABLED", "false").lower() == "true"


def require_remote_backend() -> None:
    if not REMOTE_OPS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="远程设备代理尚未配置，已关闭模拟执行",
        )


@router.get("/status")
def get_remote_ops_status():
    return {
        "available": REMOTE_OPS_ENABLED,
        "message": (
            "远程执行后端已启用"
            if REMOTE_OPS_ENABLED
            else "远程设备代理尚未配置，模拟执行已关闭"
        ),
    }


# ========== 远程操作 ==========


@router.post("/operations")
def create_operation(req: dict):
    """创建远程操作"""
    require_remote_backend()
    service = get_remote_ops_service()
    op = service.create_operation(
        device_id=req.get("device_id"),
        operation_type=req.get("operation_type"),
        params=req.get("params"),
        operator=req.get("operator", "system"),
    )
    return {
        "id": op.id,
        "device_id": op.device_id,
        "operation_type": op.operation_type,
        "status": op.status,
        "created_at": op.created_at,
    }


@router.post("/operations/{operation_id}/execute")
def execute_operation(operation_id: str):
    """执行远程操作"""
    require_remote_backend()
    service = get_remote_ops_service()
    op = service.execute_operation(operation_id)
    if not op:
        raise HTTPException(status_code=404, detail="操作不存在")
    return {
        "id": op.id,
        "status": op.status,
        "progress": op.progress,
        "result": op.result,
        "error": op.error,
    }


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str):
    """获取操作详情"""
    service = get_remote_ops_service()
    op = service.get_operation(operation_id)
    if not op:
        raise HTTPException(status_code=404, detail="操作不存在")
    return {
        "id": op.id,
        "device_id": op.device_id,
        "operation_type": op.operation_type,
        "params": op.params,
        "operator": op.operator,
        "status": op.status,
        "progress": op.progress,
        "result": op.result,
        "error": op.error,
        "created_at": op.created_at,
        "started_at": op.started_at,
        "completed_at": op.completed_at,
    }


@router.get("/operations")
def list_operations(
    device_id: str = None,
    operation_type: str = None,
    status: str = None,
    limit: int = 50,
):
    """列出操作"""
    service = get_remote_ops_service()
    return {
        "total": len(service.operations),
        "items": service.list_operations(device_id, operation_type, status, limit),
    }


# ========== 远程会话 ==========


@router.post("/sessions")
def create_session(req: dict):
    """创建远程会话"""
    require_remote_backend()
    service = get_remote_ops_service()
    session = service.create_session(
        device_id=req.get("device_id"), session_type=req.get("session_type", "ssh")
    )
    return {
        "id": session.id,
        "device_id": session.device_id,
        "session_type": session.session_type,
        "status": session.status,
        "connected_at": session.connected_at,
    }


@router.post("/sessions/{session_id}/close")
def close_session(session_id: str):
    """关闭远程会话"""
    require_remote_backend()
    service = get_remote_ops_service()
    if not service.close_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "会话已关闭"}


@router.post("/sessions/{session_id}/command")
def execute_command(session_id: str, req: dict):
    """执行命令"""
    require_remote_backend()
    service = get_remote_ops_service()
    result = service.execute_command(session_id, req.get("command"))
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/sessions")
def list_sessions(device_id: str = None):
    """列出远程会话"""
    service = get_remote_ops_service()
    return {"items": service.list_sessions(device_id)}


# ========== 设备快照 ==========


@router.post("/devices/{device_id}/snapshot")
def create_snapshot(device_id: str):
    """创建设备快照"""
    require_remote_backend()
    service = get_remote_ops_service()
    snap = service.create_snapshot(device_id)
    return {"id": snap.id, "device_id": snap.device_id, "timestamp": snap.timestamp}


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str):
    """获取快照详情"""
    service = get_remote_ops_service()
    snap = service.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="快照不存在")
    return {
        "id": snap.id,
        "device_id": snap.device_id,
        "timestamp": snap.timestamp,
        "config": snap.config,
        "status": snap.status,
        "metrics": snap.metrics,
    }


@router.get("/snapshots")
def list_snapshots(device_id: str = None):
    """列出快照"""
    service = get_remote_ops_service()
    return {"items": service.list_snapshots(device_id)}


# ========== 批量操作 ==========


@router.post("/batch")
def batch_operation(req: dict):
    """批量操作"""
    require_remote_backend()
    service = get_remote_ops_service()
    results = service.batch_operation(
        device_ids=req.get("device_ids", []),
        operation_type=req.get("operation_type"),
        params=req.get("params"),
    )
    return {"total": len(results), "items": results}


# ========== 快捷操作 ==========


@router.post("/devices/{device_id}/reboot")
def reboot_device(device_id: str, operator: str = "system"):
    """重启设备"""
    require_remote_backend()
    service = get_remote_ops_service()
    op = service.create_operation(device_id, OperationType.REBOOT, operator=operator)
    service.execute_operation(op.id)
    return {"operation_id": op.id, "status": op.status, "result": op.result}


@router.post("/devices/{device_id}/stop")
def stop_device(device_id: str, operator: str = "system"):
    """停止设备"""
    require_remote_backend()
    service = get_remote_ops_service()
    op = service.create_operation(device_id, OperationType.STOP, operator=operator)
    service.execute_operation(op.id)
    return {"operation_id": op.id, "status": op.status}


@router.post("/devices/{device_id}/start")
def start_device(device_id: str, operator: str = "system"):
    """启动设备"""
    require_remote_backend()
    service = get_remote_ops_service()
    op = service.create_operation(device_id, OperationType.START, operator=operator)
    service.execute_operation(op.id)
    return {"operation_id": op.id, "status": op.status}


@router.post("/devices/{device_id}/diagnose")
def diagnose_device(device_id: str):
    """诊断设备"""
    require_remote_backend()
    service = get_remote_ops_service()
    op = service.create_operation(device_id, OperationType.DIAGNOSE)
    service.execute_operation(op.id)
    return op.result
