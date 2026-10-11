"""
Persistent scheduler API.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import require_auth
from services.scheduler_service import get_scheduler_service

router = APIRouter(prefix="/schedules", tags=["定时任务"])


class CreateScheduleReq(BaseModel):
    name: str
    task_type: str
    cron_expression: str
    config: Dict[str, Any] = Field(default_factory=dict)


class UpdateScheduleReq(BaseModel):
    name: Optional[str] = None
    cron_expression: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


def _owner_context(current_user: dict) -> dict:
    return {
        "id": current_user.get("id"),
        "username": current_user.get("username") or current_user.get("name"),
        "organization_id": current_user.get("organization_id"),
        "role": current_user.get("role"),
        "is_admin": bool(current_user.get("is_admin") or current_user.get("is_superuser")),
    }


@router.post("")
def create_schedule(req: CreateScheduleReq, current_user: dict = Depends(require_auth)):
    config = dict(req.config or {})
    config.pop("_scheduler_owner", None)
    config["_scheduler_owner"] = _owner_context(current_user)
    try:
        return get_scheduler_service().create_schedule(req.name, req.task_type, req.cron_expression, config).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_schedules(enabled: Optional[bool] = None, current_user: dict = Depends(require_auth)):
    schedules = get_scheduler_service().list_schedules(enabled)
    return {"total": len(schedules), "items": schedules}


@router.get("/cron/examples")
def get_cron_examples(current_user: dict = Depends(require_auth)):
    return {
        "timezone": "Asia/Shanghai",
        "examples": [
            {"expression": "0 0 * * *", "description": "每天 00:00"},
            {"expression": "0 * * * *", "description": "每小时整点"},
            {"expression": "0 0 * * 0", "description": "每周日 00:00"},
            {"expression": "0 0 1 * *", "description": "每月 1 日 00:00"},
            {"expression": "*/15 * * * *", "description": "每 15 分钟"},
        ],
    }


@router.get("/{schedule_id}")
def get_schedule(schedule_id: str, current_user: dict = Depends(require_auth)):
    schedule = get_scheduler_service().get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return schedule.to_dict()


@router.put("/{schedule_id}")
def update_schedule(schedule_id: str, req: UpdateScheduleReq, current_user: dict = Depends(require_auth)):
    try:
        success = get_scheduler_service().update_schedule(schedule_id, req.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return {"message": "定时任务已更新"}


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: str, current_user: dict = Depends(require_auth)):
    try:
        success = get_scheduler_service().delete_schedule(schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return {"message": "定时任务已删除"}


@router.post("/{schedule_id}/execute")
def execute_now(schedule_id: str, current_user: dict = Depends(require_auth)):
    result = get_scheduler_service().trigger_schedule(schedule_id)
    if "error" in result:
        status = 404 if result["error"] == "Schedule not found" else 409
        raise HTTPException(status_code=status, detail=result["error"])
    return result


@router.get("/{schedule_id}/executions")
def get_executions(schedule_id: str, limit: int = 50, current_user: dict = Depends(require_auth)):
    service = get_scheduler_service()
    if not service.get_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="定时任务不存在")
    executions = service.get_executions(schedule_id, limit)
    return {"total": len(executions), "items": executions}
