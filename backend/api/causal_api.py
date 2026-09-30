"""因果推断 API — 租户隔离、持久化、可审计的实验任务。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_auth, require_permission
from api.experiment_http import audit_actor, experiment_call
from api.tenant_access import require_organization
from database.config import get_db
from services.experiment_runtime import (
    create_experiment,
    get_experiment,
    list_experiments,
    serialize_experiment,
)

router = APIRouter(prefix="/causal", tags=["因果推断"])


class CausalReq(BaseModel):
    treatment: str = Field(default="X", min_length=1, max_length=128)
    outcome: str = Field(default="Y", min_length=1, max_length=128)
    method: str = "linear_regression"
    confounders: list[str] = Field(default_factory=list, max_length=32)
    dataset_id: Optional[int] = None
    fixture_mode: bool = False


def _shape(job):
    data = serialize_experiment(job)
    metrics = data.get("metrics") or {}
    return {
        **data,
        "job_id": data.get("training_job_id"),
        "config": (data.get("hyperparameters") or {}).get("config", {}),
        "progress": metrics.get("progress", data.get("progress", 100 if str(data.get("status") or "").lower() == "completed" else 0)),
        "effect": metrics.get("causal_effect"),
        "causal_effect": metrics.get("causal_effect"),
        "naive_correlation": metrics.get("naive_correlation"),
        "ci_lower": metrics.get("causal_effect_ci95_low"),
        "ci_upper": metrics.get("causal_effect_ci95_high"),
        "data_source": ((data.get("metadata") or {}).get("data_source") or {}).get("type"),
    }


@router.get("/status", dependencies=[Depends(require_permission("training:read"))])
def causal_status(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    org_id = require_organization(current_user)
    jobs = experiment_call(list_experiments, db, organization_id=org_id, kind="causal", limit=50)
    return {
        "available": True,
        "methods": ["linear_regression"],
        "fixture_supported": True,
        "results": [_shape(job) for job in jobs],
    }


@router.post("/analyze", dependencies=[Depends(require_permission("training:write"))])
def analyze_causal(
    req: CausalReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    if req.method not in {"linear_regression", "ate"}:
        raise HTTPException(status_code=422, detail="当前基础验证仅支持线性回归调整")
    if req.dataset_id is None and not req.fixture_mode:
        raise HTTPException(status_code=422, detail="请选择组织数据集，或明确启用固定小样本验证")

    config = req.model_dump(exclude={"dataset_id"}) if hasattr(req, "model_dump") else req.dict(exclude={"dataset_id"})
    config["method"] = "linear_regression"
    job = experiment_call(
        create_experiment,
        db,
        kind="causal",
        name=f"因果分析:{req.treatment}->{req.outcome}",
        config=config,
        dataset_id=req.dataset_id,
        organization_id=require_organization(current_user),
        actor_id=audit_actor(current_user),
    )
    return _shape(job)


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("training:read"))])
def get_causal_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    return _shape(
        experiment_call(
            get_experiment,
            db,
            job_id,
            organization_id=require_organization(current_user),
        )
    )


@router.post("/counterfactual", dependencies=[Depends(require_permission("training:read"))])
def counterfactual(actual: float = 100, treatment_effect: float = -15):
    """纯计算辅助接口，不冒充已训练模型推断。"""
    return {
        "actual": actual,
        "counterfactual": actual + treatment_effect,
        "treatment_effect": treatment_effect,
        "interpretation": f"按给定处理效应，结果从 {actual} 变为 {actual + treatment_effect}",
    }
