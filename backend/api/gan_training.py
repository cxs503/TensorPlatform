"""GAN API — 真实反向传播训练、持久化任务与产物生成。"""

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

router = APIRouter(prefix="/gan", tags=["生成式AI"])


class GANTrainReq(BaseModel):
    model_type: str = "gan"
    gan_type: Optional[str] = None
    latent_dim: int = Field(default=8, ge=2, le=64)
    hidden_dim: int = Field(default=32, ge=8, le=128)
    epochs: int = Field(default=40, ge=1, le=100)
    batch_size: int = Field(default=32, ge=4, le=256)
    sample_count: int = Field(default=256, ge=32, le=2048)
    dataset_id: Optional[int] = None
    fixture_mode: bool = False


class GANGenerateReq(BaseModel):
    job_id: int
    count: int = Field(default=8, ge=1, le=16)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


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
def gan_status():
    try:
        import torch

        has_torch = True
    except ImportError:
        has_torch = False
    return {
        "torch_available": has_torch,
        "models": ["gan"],
        "fixture_supported": True,
        "simulated_fallback": False,
    }


@router.post("/train", dependencies=[Depends(require_permission("training:write"))])
def train_gan(
    req: GANTrainReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    model_type = (req.gan_type or req.model_type).lower()
    if model_type not in {"gan", "dcgan"}:
        raise HTTPException(status_code=422, detail="当前基础验证仅支持轻量 GAN")
    if req.dataset_id is None and not req.fixture_mode:
        raise HTTPException(status_code=422, detail="请选择组织数据集，或明确启用固定二维样本验证")

    config = req.model_dump(exclude={"dataset_id"}) if hasattr(req, "model_dump") else req.dict(exclude={"dataset_id"})
    config["model_type"] = "gan"
    job = experiment_call(
        create_experiment,
        db,
        kind="gan",
        name="轻量GAN数据分布学习",
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
        kind="gan",
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


@router.post("/generate", dependencies=[Depends(require_permission("training:read"))])
def generate_samples(
    req: GANGenerateReq,
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
        raise HTTPException(status_code=409, detail="GAN 模型尚未训练完成")
    artifact_path = (data.get("artifact") or {}).get("path")
    if not artifact_path:
        artifact_path = (data.get("metrics") or {}).get("artifact_path")
    if not artifact_path:
        raise HTTPException(status_code=409, detail="训练任务没有可用模型产物")
    try:
        from services.experiment_algorithms_gnn_gan import generate_gan_from_artifact

        result = generate_gan_from_artifact(
            artifact_path,
            count=req.count,
            seed=req.seed,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job_id": req.job_id,
        "count": req.count,
        "model_source": "persisted_training_artifact",
        **result,
    }
