"""
仪表盘 API — 从数据库获取真实统计数据
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from database.config import get_db
from api.deps import require_auth
from api.tenant_access import scope_query
from models.core import (
    Project, Dataset, Model, ModelVersion, TrainingJob,
    CarbonEmission, PhotovoltaicSystem, Alert,
)

router = APIRouter(prefix="/dashboard", tags=["仪表盘"])


@router.get("/recent-assets")
def get_recent_assets(
    limit: int = Query(default=8, ge=1, le=30),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """返回当前用户可见的真实近期数据集与模型，不生成任何演示条目。"""
    datasets = (
        scope_query(db.query(Dataset), Dataset.organization_id, current_user)
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
        .limit(limit)
        .all()
    )
    models = (
        scope_query(db.query(Model), Model.organization_id, current_user)
        .order_by(Model.created_at.desc(), Model.id.desc())
        .limit(limit)
        .all()
    )

    def enum_value(value):
        return value.value if hasattr(value, "value") else value

    def newest_version(model: Model):
        versions = list(model.versions or [])
        return max(versions, key=lambda version: (version.created_at or datetime.min, version.id or 0), default=None)

    return {
        "datasets": [
            {
                "id": item.id,
                "name": item.name,
                "stage": item.stage or "raw",
                "row_count": int(item.row_count or 0),
                "size_bytes": int(item.size_bytes or 0),
                "has_file": bool(item.file_path or item.data_blob),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in datasets
        ],
        "models": [
            {
                "id": item.id,
                "name": item.name,
                "model_type": item.model_type,
                "framework": item.framework,
                "status": enum_value(item.status),
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "latest_version": newest_version(item).version_number if newest_version(item) else None,
                "metrics": newest_version(item).metrics if newest_version(item) and isinstance(newest_version(item).metrics, dict) else {},
            }
            for item in models
        ],
        "limit": limit,
    }


@router.get("/statistics")
def get_dashboard_statistics(db: Session = Depends(get_db)):
    """获取仪表盘统计数据（来自数据库）"""
    try:
        project_count = db.query(func.count(Project.id)).scalar() or 0
        project_active = db.query(func.count(Project.id)).filter(
            Project.status == "ACTIVE"
        ).scalar() or 0

        dataset_count = db.query(func.count(Dataset.id)).scalar() or 0
        model_count = db.query(func.count(Model.id)).scalar() or 0
        # Deployments are independent facts. Creation writes deployments and
        # model_versions, so models.status alone cannot represent deployment state.
        deployment_total = 0
        deployment_active = 0
        model_deployed = 0
        try:
            deployment_stats = db.execute(text("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN LOWER(status) IN
                        ('running', 'healthy', 'active', 'deployed', 'serving')
                        THEN 1 ELSE 0 END) AS active,
                    COUNT(DISTINCT CASE WHEN LOWER(status) IN
                        ('running', 'healthy', 'active', 'deployed', 'serving')
                        THEN model_id END) AS deployed_models
                FROM deployments
            """)).one()
            deployment_total = int(deployment_stats[0] or 0)
            deployment_active = int(deployment_stats[1] or 0)
            model_deployed = int(deployment_stats[2] or 0)
        except Exception:
            # Keep older installations without the deployments migration usable.
            db.rollback()

        training_total = db.query(func.count(TrainingJob.id)).scalar() or 0
        training_running = db.query(func.count(TrainingJob.id)).filter(
            TrainingJob.status == "RUNNING"
        ).scalar() or 0
        training_completed = db.query(func.count(TrainingJob.id)).filter(
            TrainingJob.status == "COMPLETED"
        ).scalar() or 0

        pv_count = db.query(func.count(PhotovoltaicSystem.id)).scalar() or 0

        total_emission = db.query(func.sum(CarbonEmission.emission_amount)).scalar() or 0

        alert_count = db.query(func.count(Alert.id)).filter(
            Alert.is_resolved == False
        ).scalar() or 0

        return {
            "projects": {"total": project_count, "active": project_active},
            "datasets": {"total": dataset_count},
            "models": {"total": model_count, "deployed": model_deployed},
            "deployments": {
                "total": deployment_total,
                "active": deployment_active,
            },
            "training_jobs": {
                "total": training_total,
                "running": training_running,
                "completed": training_completed,
            },
            "photovoltaic": {"systems": pv_count},
            "carbon": {"total_emission_tco2e": round(total_emission, 2)},
            "alerts": {"unresolved": alert_count},
        }
    except Exception as e:
        return {
            "error": str(e),
            "projects": {"total": 0, "active": 0},
            "datasets": {"total": 0},
            "models": {"total": 0, "deployed": 0},
            "deployments": {"total": 0, "active": 0},
            "training_jobs": {"total": 0, "running": 0, "completed": 0},
        }


@router.get("/recent-activities")
def get_recent_activities(limit: int = 5, db: Session = Depends(get_db)):
    """获取最近活动（来自数据库）"""
    activities = []

    # 最近的训练任务
    recent_trainings = (
        db.query(TrainingJob)
        .order_by(TrainingJob.created_at.desc())
        .limit(3)
        .all()
    )
    for t in recent_trainings:
        status_map = {
            "completed": "training_completed",
            "running": "training_started",
            "failed": "training_failed",
            "pending": "training_pending",
        }
        activities.append({
            "id": t.id,
            "type": status_map.get(t.status.value if hasattr(t.status, 'value') else t.status, "training"),
            "message": f"训练任务「{t.name}」{_status_text(t.status)}",
            "time": _time_ago(t.created_at),
        })

    # 最近的模型
    recent_models = (
        db.query(Model).order_by(Model.created_at.desc()).limit(2).all()
    )
    for m in recent_models:
        s = m.status.value if hasattr(m.status, 'value') else str(m.status)
        activities.append({
            "id": m.id,
            "type": "model_created" if s != "DEPLOYED" else "model_deployed",
            "message": f"模型「{m.name}」{_status_text(m.status)}",
            "time": _time_ago(m.created_at),
        })

    activities.sort(key=lambda x: x.get("id", 0), reverse=True)
    return {"items": activities[:limit]}


def _status_text(status) -> str:
    if hasattr(status, 'value'):
        status = status.value
    return {
        "pending": "等待中", "running": "运行中", "completed": "已完成",
        "failed": "失败", "cancelled": "已取消",
        "draft": "草稿", "training": "训练中", "ready": "就绪",
        "deployed": "已部署", "deprecated": "已废弃",
    }.get(status, str(status))


def _time_ago(dt) -> str:
    if not dt:
        return "未知"
    delta = datetime.utcnow() - dt
    if delta.days > 30:
        return f"{delta.days // 30}个月前"
    if delta.days > 0:
        return f"{delta.days}天前"
    if delta.seconds > 3600:
        return f"{delta.seconds // 3600}小时前"
    if delta.seconds > 60:
        return f"{delta.seconds // 60}分钟前"
    return "刚刚"
