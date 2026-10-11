"""
CI/CD API - 流水线管理
"""
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.cicd_service import CICDService, get_cicd_service

router = APIRouter(prefix="/cicd", tags=["CI/CD流水线"])
RUNNER_ENABLED = os.getenv("CICD_RUNNER_ENABLED", "false").lower() == "true"


class PipelineCreate(BaseModel):
    name: str
    project_id: Optional[str] = None
    stages: Optional[list] = None


class PipelineUpdate(BaseModel):
    stages: list


class TriggerRequest(BaseModel):
    commit_id: Optional[str] = None


class WebhookCreate(BaseModel):
    events: list[str]


class WebhookPayload(BaseModel):
    commit_id: Optional[str] = None
    after: Optional[str] = None
    user: Optional[str] = None


@router.get("")
def list_pipelines(project_id: str = None):
    """列出流水线"""
    service = get_cicd_service()
    return service.list_pipelines(project_id)


@router.post("")
def create_pipeline(req: PipelineCreate):
    """创建流水线"""
    service = get_cicd_service()
    pipeline = service.create_pipeline(
        name=req.name, project_id=req.project_id, stages=req.stages
    )
    return {
        "id": pipeline.id,
        "name": pipeline.name,
        "status": pipeline.status,
        "stages": [{"name": s.name, "action": s.action} for s in pipeline.stages],
    }


@router.get("/{pipeline_id}")
def get_pipeline(pipeline_id: str):
    """获取流水线详情"""
    service = get_cicd_service()
    pipeline = service.get_pipeline(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return {
        "id": pipeline.id,
        "name": pipeline.name,
        "project_id": pipeline.project_id,
        "status": pipeline.status,
        "stages": [
            {"name": s.name, "action": s.action, "config": s.config, "status": s.status}
            for s in pipeline.stages
        ],
        "created_at": pipeline.created_at,
        "updated_at": pipeline.updated_at,
    }


@router.put("/{pipeline_id}")
def update_pipeline(pipeline_id: str, req: PipelineUpdate):
    """更新流水线"""
    service = get_cicd_service()
    pipeline = service.update_pipeline(pipeline_id, req.stages)
    if not pipeline:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return {"message": "更新成功"}


@router.delete("/{pipeline_id}")
def delete_pipeline(pipeline_id: str):
    """删除流水线"""
    service = get_cicd_service()
    if not service.delete_pipeline(pipeline_id):
        raise HTTPException(status_code=404, detail="流水线不存在")
    return {"message": "删除成功"}


@router.post("/{pipeline_id}/trigger")
def trigger_pipeline(pipeline_id: str, req: TriggerRequest = None):
    """触发流水线执行"""
    if not RUNNER_ENABLED:
        raise HTTPException(status_code=503, detail="CI/CD Runner尚未配置，拒绝模拟执行")
    service = get_cicd_service()
    commit_id = req.commit_id if req else None
    run = service.trigger_pipeline(pipeline_id, "manual", commit_id)
    if not run:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return {"run_id": run.id, "status": run.status, "started_at": run.started_at}


@router.get("/{pipeline_id}/runs")
def list_runs(pipeline_id: str):
    """列出流水线执行历史"""
    service = get_cicd_service()
    return service.list_runs(pipeline_id)


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """获取执行详情"""
    service = get_cicd_service()
    run = service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="执行不存在")
    return {
        "id": run.id,
        "pipeline_id": run.pipeline_id,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "commit_id": run.commit_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "stages": [
            {
                "name": s.name,
                "action": s.action,
                "status": s.status,
                "logs": s.logs,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
            }
            for s in run.stages
        ],
    }


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """取消执行"""
    service = get_cicd_service()
    if not service.cancel_run(run_id):
        raise HTTPException(status_code=404, detail="执行不存在或无法取消")
    return {"message": "取消成功"}


@router.post("/{pipeline_id}/webhooks")
def create_webhook(pipeline_id: str, req: WebhookCreate):
    """创建Webhook"""
    service = get_cicd_service()
    webhook = service.create_webhook(pipeline_id, req.events)
    if not webhook:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return {
        "id": webhook.id,
        "url": webhook.url,
        "events": webhook.events,
        "secret": webhook.secret,
    }


@router.post("/webhook/{webhook_id}")
async def trigger_webhook(webhook_id: str, event: str, payload: WebhookPayload):
    """Webhook触发端点"""
    service = get_cicd_service()
    run = service.trigger_by_webhook(
        webhook_id,
        event,
        {"commit_id": payload.commit_id, "after": payload.after, "user": payload.user},
    )
    if not run:
        raise HTTPException(status_code=404, detail="Webhook不存在或事件不匹配")
    return {"run_id": run.id, "status": run.status, "message": "流水线已触发"}
