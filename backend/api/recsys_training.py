"""推荐系统 API — 真实矩阵分解、持久化任务与模型产物推理。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_auth, require_permission
from api.experiment_http import audit_actor, experiment_call
from api.tenant_access import require_organization
from database.config import get_db
from services.experiment_runtime import (
    cancel_experiment,
    create_experiment,
    get_experiment,
    list_experiments,
    serialize_experiment,
)

router = APIRouter(prefix="/recsys", tags=["推荐系统"])


class RecSysTrainReq(BaseModel):
    algorithm: str = "mf"
    method: Optional[str] = None
    factors: int = Field(default=16, ge=2, le=64)
    epochs: int = Field(default=30, ge=1, le=200)
    learning_rate: float = Field(default=0.02, gt=0, le=0.2)
    regularization: float = Field(default=0.02, ge=0, le=1)
    dataset_id: Optional[int] = None
    fixture_mode: bool = False


def _shape(job):
    data = serialize_experiment(job)
    metrics = dict(data.get("metrics") or {})
    metrics.setdefault("rmse", metrics.get("validation_rmse"))
    return {
        **data,
        "metrics": metrics,
        "job_id": data.get("training_job_id"),
        "config": (data.get("hyperparameters") or {}).get("config", {}),
        "progress": metrics.get("progress", data.get("progress", 100 if str(data.get("status") or "").lower() == "completed" else 0)),
    }


@router.get("/status", dependencies=[Depends(require_permission("training:read"))])
def recsys_status():
    return {
        "available": True,
        "methods": ["matrix_factorization"],
        "fixture_supported": True,
        "simulated_fallback": False,
    }


@router.post("/train", dependencies=[Depends(require_permission("training:write"))])
def train_recsys(
    req: RecSysTrainReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    algorithm = (req.method or req.algorithm).lower()
    if algorithm not in {"mf", "matrix_factorization", "collaborative"}:
        raise HTTPException(status_code=422, detail="当前基础验证仅支持矩阵分解")
    if req.dataset_id is None and not req.fixture_mode:
        raise HTTPException(status_code=422, detail="请选择组织评分数据集，或明确启用固定小样本验证")

    config = req.model_dump(exclude={"dataset_id"}) if hasattr(req, "model_dump") else req.dict(exclude={"dataset_id"})
    config["algorithm"] = "matrix_factorization"
    config["method"] = "matrix_factorization"
    job = experiment_call(
        create_experiment,
        db,
        kind="recsys",
        name="推荐系统矩阵分解",
        config=config,
        dataset_id=req.dataset_id,
        organization_id=require_organization(current_user),
        actor_id=audit_actor(current_user),
    )
    return _shape(job)


@router.get("/jobs", dependencies=[Depends(require_permission("training:read"))])
def list_jobs(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    jobs = experiment_call(
        list_experiments,
        db,
        organization_id=require_organization(current_user),
        kind="recsys",
        limit=100,
    )
    return {"jobs": [_shape(job) for job in jobs], "total": len(jobs)}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("training:read"))])
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    return _shape(
        experiment_call(get_experiment, db, job_id, organization_id=require_organization(current_user))
    )


@router.delete("/jobs/{job_id}", dependencies=[Depends(require_permission("training:write"))])
def cancel_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    return _shape(
        experiment_call(
            cancel_experiment,
            db,
            job_id,
            organization_id=require_organization(current_user),
            actor_id=audit_actor(current_user),
        )
    )


@router.get("/recommend", dependencies=[Depends(require_permission("training:read"))])
def recommend(
    job_id: int,
    user_id: str = Query(default="u0", min_length=1, max_length=128),
    top_k: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    job = experiment_call(get_experiment, db, job_id, organization_id=require_organization(current_user))
    data = _shape(job)
    if str(data.get("status") or "").lower() != "completed":
        raise HTTPException(status_code=409, detail="推荐模型尚未训练完成")
    artifact_path = (data.get("artifact") or {}).get("path")
    if not artifact_path:
        artifact_path = (data.get("metrics") or {}).get("artifact_path")
    if not artifact_path:
        raise HTTPException(status_code=409, detail="训练任务没有可用模型产物")
    try:
        from services.experiment_algorithms_causal_recsys import recommend_from_artifact

        result = recommend_from_artifact(artifact_path, user_id=user_id, top_k=top_k)
        items = [
            {"item": item.get("item_id"), "score": item.get("score")}
            for item in result.get("items", [])
        ]
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "user": user_id,
        "recommendations": items,
        "model_source": "persisted_training_artifact",
    }
