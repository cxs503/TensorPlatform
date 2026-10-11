"""GNN API — 真实 GCN 训练、持久化任务与产物推理。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
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

router = APIRouter(prefix="/gnn", tags=["图神经网络"])


class GNNConfig(BaseModel):
    task_type: str = "node_classification"
    task: Optional[str] = None
    model: str = "gcn"
    epochs: int = Field(default=60, ge=1, le=200)
    hidden_dim: int = Field(default=16, ge=4, le=128)
    learning_rate: float = Field(default=0.02, gt=0, le=0.2)
    dataset_id: Optional[int] = None
    fixture_mode: bool = False


class GNNPredictReq(BaseModel):
    job_id: int
    nodes: list[str] = Field(default_factory=list, max_length=100)


def _shape(job):
    data = serialize_experiment(job)
    metrics = data.get("metrics") or {}
    return {
        **data,
        "job_id": data.get("training_job_id"),
        "config": (data.get("hyperparameters") or {}).get("config", {}),
        "progress": metrics.get("progress", data.get("progress", 100 if str(data.get("status") or "").lower() == "completed" else 0)),
    }


@router.get("/status", dependencies=[Depends(require_permission("training:read"))])
def gnn_status():
    try:
        import torch

        has_torch = True
    except ImportError:
        has_torch = False
    return {
        "torch_available": has_torch,
        "models": ["gcn"],
        "tasks": ["node_classification"],
        "fixture_supported": True,
        "simulated_fallback": False,
    }


@router.post("/train", dependencies=[Depends(require_permission("training:write"))])
def train_gnn(
    req: GNNConfig,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    task = (req.task or req.task_type).lower()
    if task != "node_classification" or req.model.lower() != "gcn":
        raise HTTPException(status_code=422, detail="当前基础验证仅支持 GCN 节点分类")
    if req.dataset_id is None and not req.fixture_mode:
        raise HTTPException(status_code=422, detail="请选择组织图数据集，或明确启用固定小图验证")

    config = req.model_dump(exclude={"dataset_id"}) if hasattr(req, "model_dump") else req.dict(exclude={"dataset_id"})
    config.update({"task": "node_classification", "model": "gcn"})
    job = experiment_call(
        create_experiment,
        db,
        kind="gnn",
        name="GNN-GCN节点分类",
        config=config,
        dataset_id=req.dataset_id,
        organization_id=require_organization(current_user),
        actor_id=audit_actor(current_user),
    )
    return _shape(job)


@router.get("/jobs", dependencies=[Depends(require_permission("training:read"))])
def list_gnn_jobs(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    jobs = experiment_call(
        list_experiments,
        db,
        organization_id=require_organization(current_user),
        kind="gnn",
        limit=100,
    )
    return {"jobs": [_shape(job) for job in jobs], "total": len(jobs)}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("training:read"))])
def get_gnn_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    return _shape(
        experiment_call(get_experiment, db, job_id, organization_id=require_organization(current_user))
    )


@router.delete("/jobs/{job_id}", dependencies=[Depends(require_permission("training:write"))])
def cancel_gnn_job(
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


@router.post("/predict", dependencies=[Depends(require_permission("training:read"))])
def predict_gnn(
    req: GNNPredictReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    job = experiment_call(
        get_experiment,
        db,
        req.job_id,
        organization_id=require_organization(current_user),
    )
    data = _shape(job)
    if str(data.get("status") or "").lower() != "completed":
        raise HTTPException(status_code=409, detail="GNN 模型尚未训练完成")
    artifact_path = (data.get("artifact") or {}).get("path")
    if not artifact_path:
        artifact_path = (data.get("metrics") or {}).get("artifact_path")
    if not artifact_path:
        raise HTTPException(status_code=409, detail="训练任务没有可用模型产物")
    try:
        from services.experiment_algorithms_gnn_gan import predict_gnn_from_artifact

        result = predict_gnn_from_artifact(artifact_path, nodes=req.nodes)
        predictions = result.get("predictions", [])
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job_id": req.job_id,
        "task": "node_classification",
        "predictions": predictions,
        "model_source": "persisted_training_artifact",
    }
