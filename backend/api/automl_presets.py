"""AutoML 算法超参预设 CRUD。

把「内置算法 + 一套超参」存成命名档案（如 "RF-深树"），供 AutoML 对比时复用，
避免每次重填参数。组织隔离：用户只能看到本组织预设 + 平台内置预设。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.tenant_access import is_admin
from database.config import get_db
from models.core import AlgorithmHyperparamPreset
from services.builtin_algorithm_registry import validate_algorithm_params


router = APIRouter(prefix="/automl/presets", tags=["AutoML-预设"])


class PresetBody(BaseModel):
    name: str
    description: Optional[str] = None
    task_type: str  # classification/regression/time_series
    algorithm: str  # 内置算法 key，如 random_forest
    params: dict = {}


def _is_owner(preset: AlgorithmHyperparamPreset, user: dict) -> bool:
    return (preset.organization_id == user.get("organization_id")
            and preset.created_by == user.get("id"))


@router.get("")
def list_presets(
    algorithm: Optional[str] = None,
    task_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """列表：本组织预设 + 平台内置预设，可按 algorithm/task_type 过滤。"""
    query = db.query(AlgorithmHyperparamPreset).filter(
        (AlgorithmHyperparamPreset.organization_id == current_user.get("organization_id"))
        | (AlgorithmHyperparamPreset.is_builtin.is_(True))
    )
    if algorithm:
        query = query.filter(AlgorithmHyperparamPreset.algorithm == algorithm)
    if task_type:
        query = query.filter(AlgorithmHyperparamPreset.task_type == task_type)
    rows = query.order_by(
        AlgorithmHyperparamPreset.is_builtin.desc(),
        AlgorithmHyperparamPreset.updated_at.desc(),
    ).all()
    return {"items": [row.to_dict() for row in rows], "total": len(rows)}


@router.post("")
def create_preset(
    body: PresetBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    try:
        params = validate_algorithm_params(body.algorithm, body.task_type, body.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    exists = db.query(AlgorithmHyperparamPreset).filter(
        AlgorithmHyperparamPreset.organization_id == current_user.get("organization_id"),
        AlgorithmHyperparamPreset.name == body.name,
        AlgorithmHyperparamPreset.algorithm == body.algorithm,
        AlgorithmHyperparamPreset.task_type == body.task_type,
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"预设名「{body.name}」在该算法/任务下已存在")
    preset = AlgorithmHyperparamPreset(
        name=body.name.strip(), description=body.description,
        task_type=body.task_type, algorithm=body.algorithm, params=params,
        organization_id=current_user.get("organization_id"),
        created_by=current_user.get("id"),
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return preset.to_dict()


@router.get("/{preset_id}")
def get_preset(
    preset_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    preset = db.get(AlgorithmHyperparamPreset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    if not (preset.is_builtin or preset.organization_id == current_user.get("organization_id")):
        raise HTTPException(status_code=403, detail="无权访问该预设")
    return preset.to_dict()


@router.put("/{preset_id}")
def update_preset(
    preset_id: int,
    body: PresetBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    preset = db.get(AlgorithmHyperparamPreset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    if preset.is_builtin and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="平台内置预设仅管理员可修改")
    if not preset.is_builtin and not _is_owner(preset, current_user) and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="只能修改自己创建的预设")
    try:
        params = validate_algorithm_params(body.algorithm, body.task_type, body.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    preset.name = body.name.strip()
    preset.description = body.description
    preset.task_type = body.task_type
    preset.algorithm = body.algorithm
    preset.params = params
    db.commit()
    db.refresh(preset)
    return preset.to_dict()


@router.delete("/{preset_id}")
def delete_preset(
    preset_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    preset = db.get(AlgorithmHyperparamPreset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    if preset.is_builtin and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="平台内置预设仅管理员可删除")
    if not preset.is_builtin and not _is_owner(preset, current_user) and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="只能删除自己创建的预设")
    db.delete(preset)
    db.commit()
    return {"message": "预设已删除"}
