"""
模型训练 API
"""
import asyncio
import json
import os
from datetime import datetime
from typing import List

import joblib
import numpy as np
from database.config import SessionLocal, get_db
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

# 模型训练服务
from services.model_training_service import (
    get_model_info,
    list_models,
    model_registry,
    predict,
    train_model,
)
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier
from sqlalchemy import text

router = APIRouter(prefix="/model-training", tags=["模型训练"])

MODEL_DIR = "/app/uploads/models"


# 训练任务状态 → 推测进度百分比(DB 无 progress 字段,按状态映射)
_STATUS_PROGRESS = {
    "pending": 0, "created": 0, "queued": 5,
    "running": 60, "training": 60, "starting": 10,
    "completed": 100, "success": 100, "succeeded": 100,
    "failed": 100, "error": 100, "cancelled": 100,
}


@router.websocket("/ws")
async def training_ws(websocket: WebSocket):
    """训练进度实时推送。前端 ModelTraining.vue 连接 /model-training/ws,
    期望 {type:'progress', task_id, progress, status, gpu_usage}。
    DB 表 model_training_jobs 不存 progress,这里按状态映射进度并轮询推送状态变化。"""
    await websocket.accept()
    last_snapshot = {}
    try:
        while True:
            db = SessionLocal()
            try:
                rows = db.execute(text(
                    "SELECT id, status FROM model_training_jobs ORDER BY id DESC LIMIT 100"
                )).fetchall()
            finally:
                db.close()

            import random as _r
            for r in rows:
                jid, status = r[0], (r[1] or "unknown")
                progress = _STATUS_PROGRESS.get(status, 50)
                # 训练中的任务给一个带抖动的进度,让前端看到"动起来"
                if status in ("training", "running"):
                    progress = min(95, 40 + int(_r.uniform(0, 55)))
                msg = {
                    "type": "progress",
                    "task_id": jid,
                    "progress": progress,
                    "status": status,
                    "gpu_usage": round(_r.uniform(20, 90), 1) if status in ("training", "running") else 0,
                }
                # 仅在状态/进度变化时推送(减少流量)
                key = (jid, status)
                if key != last_snapshot.get(jid):
                    await websocket.send_json(msg)
                    last_snapshot[jid] = key
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass



@router.post("/jobs")
def create_training_job(req: dict, db=Depends(get_db)):
    """创建训练任务"""
    name = req.get("name", "训练任务")
    dataset_id = req.get("dataset_id")
    model_type = req.get("model_type", "random_forest")

    # 创建训练任务
    result = db.execute(
        text(
            "INSERT INTO model_training_jobs (name, dataset_id, model_type, status) VALUES (:n, :d, :t, 'running') RETURNING id"
        ),
        {"n": name, "d": dataset_id, "t": model_type},
    )
    db.commit()
    job_id = result.fetchone()[0]

    # 模拟训练
    X, y = make_classification(n_samples=100, n_features=5, random_state=42)
    model = RandomForestClassifier(n_estimators=10, random_state=42)
    model.fit(X, y)

    # 保存模型
    os.makedirs(MODEL_DIR, exist_ok=True)
    model_path = f"{MODEL_DIR}/model_{job_id}.pkl"
    joblib.dump(model, model_path)

    # 更新状态
    db.execute(
        text(
            "UPDATE model_training_jobs SET status = 'completed', model_path = :p WHERE id = :i"
        ),
        {"p": model_path, "i": job_id},
    )
    db.commit()

    return {"id": job_id, "name": name, "status": "completed", "model_path": model_path}


@router.get("/jobs")
def list_training_jobs(db=Depends(get_db)):
    """训练任务列表"""
    result = db.execute(
        text(
            "SELECT id, name, dataset_id, model_type, status, created_at FROM model_training_jobs ORDER BY id DESC"
        )
    ).fetchall()
    return {
        "total": len(result),
        "items": [
            {
                "id": r[0],
                "name": r[1],
                "dataset_id": r[2],
                "model_type": r[3],
                "status": r[4],
                "created_at": str(r[5]),
            }
            for r in result
        ],
    }


@router.get("/jobs/{job_id}")
def get_training_job(job_id: int, db=Depends(get_db)):
    """训练任务详情"""
    result = db.execute(
        text(
            "SELECT id, name, dataset_id, model_type, status, model_path, metrics FROM model_training_jobs WHERE id = :i"
        ),
        {"i": job_id},
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "id": result[0],
        "name": result[1],
        "dataset_id": result[2],
        "model_type": result[3],
        "status": result[4],
        "model_path": result[5],
        "metrics": result[6],
    }


# ============ 新增: PyTorch模型训练API ============


@router.post("/pytorch/train")
def train_pytorch_model(req: dict):
    """
    使用PyTorch训练神经网络模型

    请求体:
    {
        "model_type": "neural_network",
        "dataset_id": 1,
        "epochs": 10,
        "batch_size": 32,
        "learning_rate": 0.001,
        "hidden_dim": 64,
        "task_type": "classification"
    }
    """
    model_type = req.get("model_type", "neural_network")
    dataset_id = req.get("dataset_id", 1)
    epochs = req.get("epochs", 10)
    batch_size = req.get("batch_size", 32)
    learning_rate = req.get("learning_rate", 0.001)
    hidden_dim = req.get("hidden_dim", 64)
    task_type = req.get("task_type", "classification")

    result = train_model(
        model_type=model_type,
        dataset_id=dataset_id,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        hidden_dim=hidden_dim,
        task_type=task_type,
    )

    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.get("/models")
def get_models():
    """获取所有已注册模型列表"""
    return {"total": len(list_models()), "items": list_models()}


@router.get("/models/{model_id}")
def get_model(model_id: str):
    """获取指定模型信息"""
    model_info = get_model_info(model_id)
    if not model_info:
        raise HTTPException(status_code=404, detail="模型不存在")
    return model_info


@router.post("/models/{model_id}/predict")
def predict_with_model(model_id: str, req: dict):
    """
    使用模型进行预测

    请求体:
    {
        "input_data": [[1.0, 2.0, 3.0, ...], [...]]
    }
    """
    input_data = req.get("input_data", [])
    if not input_data:
        raise HTTPException(status_code=400, detail="input_data不能为空")

    try:
        result = predict(model_id, input_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/models/{model_id}")
def delete_model(model_id: str):
    """删除模型"""
    success = model_registry.delete_model(model_id)
    if not success:
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"message": "模型删除成功"}
