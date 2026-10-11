"""
工单管理 API
"""
from fastapi import APIRouter, HTTPException
from services.work_order_service import (
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderType,
    get_work_order_service,
)

router = APIRouter(prefix="/work-orders", tags=["工单管理"])


# ========== 工单创建与派发 ==========


@router.post("")
def create_work_order(req: dict):
    """创建工单"""
    service = get_work_order_service()
    wo = service.create_work_order(
        title=req.get("title"),
        description=req.get("description"),
        work_order_type=req.get("work_order_type", WorkOrderType.MAINTENANCE),
        priority=req.get("priority", WorkOrderPriority.NORMAL),
        equipment_id=req.get("equipment_id"),
        location=req.get("location"),
        created_by=req.get("created_by", "system"),
        estimated_hours=req.get("estimated_hours", 0),
    )
    return {
        "id": wo.id,
        "title": wo.title,
        "status": wo.status,
        "created_at": wo.created_at,
    }


@router.post("/{work_order_id}/assign")
def assign_work_order(work_order_id: str, assigned_to: str):
    """派发工单"""
    service = get_work_order_service()
    wo = service.assign_work_order(work_order_id, assigned_to)
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"message": "工单已派发", "assigned_to": wo.assigned_to}


@router.post("/batch-assign")
def batch_assign(req: dict):
    """批量派发工单"""
    service = get_work_order_service()
    count = service.batch_assign(
        work_order_ids=req.get("work_order_ids", []), assigned_to=req.get("assigned_to")
    )
    return {"message": f"已派发 {count} 个工单"}


# ========== 工单处理 ==========


@router.post("/{work_order_id}/start")
def start_work_order(work_order_id: str):
    """开始处理工单"""
    service = get_work_order_service()
    wo = service.start_work_order(work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"message": "已开始处理", "started_at": wo.started_at}


@router.post("/{work_order_id}/comment")
def add_comment(work_order_id: str, req: dict):
    """添加备注"""
    service = get_work_order_service()
    if not service.add_comment(
        work_order_id, req.get("comment", ""), req.get("user", "system")
    ):
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"message": "备注已添加"}


@router.put("/{work_order_id}/progress")
def update_progress(work_order_id: str, req: dict):
    """更新进度"""
    service = get_work_order_service()
    wo = service.update_progress(
        work_order_id, req.get("progress", 0), req.get("comment")
    )
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"message": "进度已更新"}


# ========== 工单完成 ==========


@router.post("/{work_order_id}/complete")
def complete_work_order(work_order_id: str, req: dict):
    """完成工单"""
    service = get_work_order_service()
    wo = service.complete_work_order(
        work_order_id,
        completed_by=req.get("completed_by", "system"),
        actual_hours=req.get("actual_hours", 0),
        comment=req.get("comment"),
    )
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {
        "message": "工单已完成",
        "completed_at": wo.completed_at,
        "actual_hours": wo.actual_hours,
    }


@router.post("/{work_order_id}/cancel")
def cancel_work_order(work_order_id: str, reason: str):
    """取消工单"""
    service = get_work_order_service()
    wo = service.cancel_work_order(work_order_id, reason)
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"message": "工单已取消"}


# ========== 工单查询 ==========


@router.get("/{work_order_id}")
def get_work_order(work_order_id: str):
    """获取工单详情"""
    service = get_work_order_service()
    wo = service.get_work_order(work_order_id)
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {
        "id": wo.id,
        "title": wo.title,
        "description": wo.description,
        "work_order_type": wo.work_order_type,
        "priority": wo.priority,
        "equipment_id": wo.equipment_id,
        "location": wo.location,
        "created_by": wo.created_by,
        "assigned_to": wo.assigned_to,
        "status": wo.status,
        "created_at": wo.created_at,
        "assigned_at": wo.assigned_at,
        "started_at": wo.started_at,
        "completed_at": wo.completed_at,
        "estimated_hours": wo.estimated_hours,
        "actual_hours": wo.actual_hours,
        "comments": wo.comments,
        "attachments": wo.attachments,
    }


@router.get("")
def list_work_orders(
    status: str = None,
    priority: str = None,
    work_order_type: str = None,
    assigned_to: str = None,
    limit: int = 100,
):
    """列出工单"""
    service = get_work_order_service()
    return {
        "total": len(service.work_orders),
        "items": service.list_work_orders(
            status, priority, work_order_type, assigned_to, limit
        ),
    }


@router.get("/my/{user}")
def get_my_work_orders(user: str):
    """获取我的工单"""
    service = get_work_order_service()
    return {"items": service.get_my_work_orders(user)}


# ========== 统计 ==========


@router.get("/stats/overview")
def get_statistics():
    """获取工单统计"""
    service = get_work_order_service()
    return service.get_statistics()


@router.get("/stats/overdue")
def get_overdue_orders():
    """获取逾期工单"""
    service = get_work_order_service()
    return {"items": service.get_overdue_orders()}
