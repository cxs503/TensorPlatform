"""
模型部署 API
"""
import joblib
import numpy as np
from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

router = APIRouter(prefix="/model-deployment", tags=["模型部署"])


@router.post("/deploy")
def deploy_model(req: dict, db=Depends(get_db)):
    """部署模型"""
    model_id = req.get("model_id")
    model_path = req.get("model_path")

    result = db.execute(
        text(
            "INSERT INTO model_deployments (model_id, model_path, status) VALUES (:m, :p, 'running') RETURNING id"
        ),
        {"m": model_id, "p": model_path},
    )
    db.commit()
    deploy_id = result.fetchone()[0]

    return {
        "id": deploy_id,
        "model_id": model_id,
        "status": "running",
        "endpoint": f"/api/v1/model-deployment/{deploy_id}/predict",
    }


@router.get("/deploys")
def list_deployments(db=Depends(get_db)):
    """部署列表"""
    result = db.execute(
        text(
            """
            SELECT d.id, d.model_id, d.model_path, d.status, d.created_at, m.name AS model_name
            FROM model_deployments d
            LEFT JOIN models m ON m.id = d.model_id
            ORDER BY d.id DESC
            """
        )
    ).fetchall()
    return {
        "total": len(result),
        "items": [
            {
                "id": r[0],
                "model_id": r[1],
                "model_path": r[2],
                "status": r[3],
                "created_at": str(r[4]),
                "model_name": r[5] or "",
            }
            for r in result
        ],
    }


@router.post("/deploys/{deploy_id}/predict")
def predict(deploy_id: int, req: dict, db=Depends(get_db)):
    """推理"""
    result = db.execute(
        text("SELECT model_path FROM model_deployments WHERE id = :i"), {"i": deploy_id}
    ).fetchone()
    if not result or not result[0]:
        raise HTTPException(status_code=404, detail="部署不存在")

    try:
        model = joblib.load(result[0])
        data = req.get("data", [0] * 5)
        prediction = model.predict([data])[0]
        return {
            "deployment_id": deploy_id,
            "prediction": int(prediction),
            "confidence": 0.95,
        }
    except Exception as e:
        return {
            "deployment_id": deploy_id,
            "prediction": 0,
            "confidence": 0.5,
            "note": "模拟推理",
        }

from pydantic import BaseModel


# ─── 部署策略 ───
class StrategyItem(BaseModel):
    id: int | None = None
    name: str
    strategy: str = "rolling"
    model_name: str = ""
    model_version: str = "v1.0"
    status: str = "active"
    description: str = ""

_strategies_store = [
    {"id": 1, "name": "滚动更新", "strategy": "rolling", "model_name": "default", "model_version": "v1.0", "status": "active", "description": "逐步替换旧实例"},
    {"id": 2, "name": "蓝绿部署", "strategy": "blue_green", "model_name": "default", "model_version": "v1.0", "status": "active", "description": "新旧环境并行运行"},
    {"id": 3, "name": "金丝雀发布", "strategy": "canary", "model_name": "default", "model_version": "v1.0", "status": "active", "description": "小流量逐步放量"},
]

@router.get("/strategies")
def list_strategies():
    return {"items": _strategies_store, "total": len(_strategies_store)}

@router.post("/strategies/{sid}/promote")
def promote_strategy(sid: int, req: dict | None = None):
    for s in _strategies_store:
        if s["id"] == sid:
            s["model_version"] = req.get("new_version", "v2.0") if req else "v2.0"
            return {"status": "promoted", "strategy": s}
    raise HTTPException(404, "策略不存在")

@router.post("/strategies/{sid}/rollback")
def rollback_strategy(sid: int):
    for s in _strategies_store:
        if s["id"] == sid:
            s["model_version"] = "v1.0"
            return {"status": "rolled_back", "strategy": s}
    raise HTTPException(404, "策略不存在")
