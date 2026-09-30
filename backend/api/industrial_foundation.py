"""Industrial generalist V1: shared Transformer representation with four task heads."""
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import torch
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sqlalchemy import text
from sqlalchemy.orm import Session
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from api.deps import require_auth, require_permission
from api.tenant_access import assert_organization_access
from database.config import SessionLocal, get_db
from models.core import Model, ModelStatus, ModelVersion

router = APIRouter(prefix="/industrial-foundation", tags=["工业通用模型"])
ARTIFACT_DIR = Path(os.getenv("INDUSTRIAL_FOUNDATION_DIR", "/app/uploads/models/industrial_foundation"))
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
JOBS = {}


class TrainRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    dataset_id: int
    sensor_columns: List[str]
    text_column: str = "inspection_text"
    fault_column: str = "fault_type"
    anomaly_column: str = "anomaly_label"
    energy_column: str = "energy_kwh"
    efficiency_column: str = "efficiency_score"
    epochs: int = Field(12, ge=2, le=100)
    hidden_dim: int = Field(64, ge=32, le=256)
    batch_size: int = Field(64, ge=16, le=512)


class PredictRequest(BaseModel):
    sensor_values: dict
    inspection_text: str = ""


def _text_vector(value: str, size: int = 32) -> np.ndarray:
    """Dependency-free hashed character vector, stable across training and inference."""
    vector = np.zeros(size, dtype=np.float32)
    for char in str(value or ""):
        vector[ord(char) % size] += 1.0
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


class IndustrialTransformer(nn.Module):
    def __init__(self, numeric_dim: int, fault_classes: int, hidden: int = 64, text_dim: int = 32):
        super().__init__()
        self.numeric_tokens = nn.ModuleList([nn.Linear(1, hidden) for _ in range(numeric_dim)])
        self.text_token = nn.Linear(text_dim, hidden)
        self.position = nn.Parameter(torch.randn(1, numeric_dim + 1, hidden) * .02)
        layer = nn.TransformerEncoderLayer(hidden, nhead=4, dim_feedforward=hidden * 2,
                                           dropout=.1, batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(layer, num_layers=2)
        self.norm = nn.LayerNorm(hidden)
        self.fault_head = nn.Linear(hidden, fault_classes)
        self.anomaly_head = nn.Linear(hidden, 1)
        self.energy_head = nn.Linear(hidden, 1)
        self.efficiency_head = nn.Linear(hidden, 1)

    def forward(self, numeric, text_features):
        tokens = [layer(numeric[:, i:i + 1]) for i, layer in enumerate(self.numeric_tokens)]
        tokens.append(self.text_token(text_features))
        shared = self.norm(self.encoder(torch.stack(tokens, dim=1) + self.position).mean(dim=1))
        return {"fault": self.fault_head(shared), "anomaly": self.anomaly_head(shared).squeeze(-1),
                "energy": self.energy_head(shared).squeeze(-1),
                "efficiency": self.efficiency_head(shared).squeeze(-1)}


def _masked(loss_fn, prediction, target, mask):
    return loss_fn(prediction[mask], target[mask]) if mask.any() else prediction.sum() * 0


def _train(job_id: str, req: dict, organization_id: Optional[int]):
    job, db = JOBS[job_id], SessionLocal()
    try:
        row = db.execute(text("SELECT file_path FROM datasets WHERE id=:id"), {"id": req["dataset_id"]}).fetchone()
        if not row or not row[0] or not Path(row[0]).exists():
            raise ValueError("训练数据文件不存在")
        frame = pd.read_csv(row[0])
        targets = [req["fault_column"], req["anomaly_column"], req["energy_column"], req["efficiency_column"]]
        required = req["sensor_columns"] + [req["text_column"]] + targets
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError("通用模型缺少字段: " + ", ".join(missing))
        if len(frame) < 100:
            raise ValueError("通用模型至少需要100条样本")
        numeric = frame[req["sensor_columns"]].apply(pd.to_numeric, errors="coerce")
        numeric = numeric.fillna(numeric.median()).fillna(0)
        scaler = StandardScaler().fit(numeric)
        x_num = scaler.transform(numeric).astype("float32")
        x_text = np.stack([_text_vector(value) for value in frame[req["text_column"]].fillna("")])
        fault_mask = frame[req["fault_column"]].notna().to_numpy()
        encoder = LabelEncoder().fit(frame.loc[fault_mask, req["fault_column"]].astype(str))
        y_fault = np.zeros(len(frame), dtype="int64")
        y_fault[fault_mask] = encoder.transform(frame.loc[fault_mask, req["fault_column"]].astype(str))
        arrays = []
        masks = []
        for column in targets[1:]:
            values = pd.to_numeric(frame[column], errors="coerce")
            masks.append(values.notna().to_numpy())
            arrays.append(values.fillna(0).to_numpy(dtype="float32"))
        train_idx, test_idx = train_test_split(np.arange(len(frame)), test_size=.2, random_state=42,
                                                stratify=y_fault if fault_mask.all() else None)
        energy_mean = float(np.mean(arrays[1][train_idx][masks[1][train_idx]]))
        energy_scale = float(np.std(arrays[1][train_idx][masks[1][train_idx]]) or 1.0)
        efficiency_mean = float(np.mean(arrays[2][train_idx][masks[2][train_idx]]))
        efficiency_scale = float(np.std(arrays[2][train_idx][masks[2][train_idx]]) or 1.0)
        tensors = [torch.from_numpy(item) for item in [x_num, x_text, y_fault, arrays[0], arrays[1], arrays[2],
                                                       fault_mask, masks[0], masks[1], masks[2]]]
        loader = DataLoader(TensorDataset(*[tensor[train_idx] for tensor in tensors]), batch_size=req["batch_size"], shuffle=True)
        torch.manual_seed(42)
        model = IndustrialTransformer(len(req["sensor_columns"]), len(encoder.classes_), req["hidden_dim"])
        optimizer = torch.optim.AdamW(model.parameters(), lr=8e-4, weight_decay=1e-4)
        ce, bce, mse = nn.CrossEntropyLoss(), nn.BCEWithLogitsLoss(), nn.MSELoss()
        job.update(status="running", progress=5, rows=len(frame))
        model.train()
        for epoch in range(req["epochs"]):
            running = 0.0
            for xn, xt, yf, ya, ye, yi, mf, ma, me, mi in loader:
                output = model(xn, xt)
                # Regression targets are scaled to comparable ranges for balanced optimization.
                loss = (_masked(ce, output["fault"], yf, mf.bool()) +
                        _masked(bce, output["anomaly"], ya, ma.bool()) +
                        _masked(mse, output["energy"], (ye - energy_mean) / energy_scale, me.bool()) +
                        _masked(mse, output["efficiency"], (yi - efficiency_mean) / efficiency_scale, mi.bool()))
                optimizer.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
                running += float(loss.detach())
            job.update(progress=10 + int(75 * (epoch + 1) / req["epochs"]), epoch=epoch + 1,
                       loss=running / max(1, len(loader)))
        model.eval()
        with torch.no_grad():
            output = model(tensors[0][test_idx], tensors[1][test_idx])
        actual_fault, predicted_fault = y_fault[test_idx], output["fault"].argmax(1).numpy()
        energy_prediction = output["energy"].numpy() * energy_scale + energy_mean
        efficiency_prediction = output["efficiency"].numpy() * efficiency_scale + efficiency_mean
        metrics = {
            "fault_accuracy": float(accuracy_score(actual_fault, predicted_fault)),
            "fault_f1_weighted": float(f1_score(actual_fault, predicted_fault, average="weighted")),
            "anomaly_accuracy": float(accuracy_score(arrays[0][test_idx], (torch.sigmoid(output["anomaly"]) >= .5).numpy())),
            "energy_mae": float(mean_absolute_error(arrays[1][test_idx], energy_prediction)),
            "energy_r2": float(r2_score(arrays[1][test_idx], energy_prediction)),
            "efficiency_mae": float(mean_absolute_error(arrays[2][test_idx], efficiency_prediction)),
            "efficiency_r2": float(r2_score(arrays[2][test_idx], efficiency_prediction)),
        }
        artifact = ARTIFACT_DIR / f"{job_id}.pt"
        torch.save({"state_dict": model.state_dict(), "sensor_columns": req["sensor_columns"],
                    "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
                    "target_scalers": {"energy": [energy_mean, energy_scale], "efficiency": [efficiency_mean, efficiency_scale]},
                    "fault_classes": encoder.classes_.tolist(), "hidden_dim": req["hidden_dim"], "text_dim": 32}, artifact)
        registered = Model(name=req["name"], description="共享Transformer编码器；故障、异常、能耗、能效四任务联合训练",
                           model_type="industrial_generalist", framework="pytorch", status=ModelStatus.READY,
                           stage="development", organization_id=organization_id)
        db.add(registered); db.flush()
        version = ModelVersion(model_id=registered.id, version_number="1.0.0", file_path=str(artifact), metrics=metrics,
                               parameters={**req, "architecture": "shared_transformer_four_heads", "job_id": job_id})
        db.add(version); db.commit()
        manifest = {"job_id": job_id, "name": req["name"], "status": "completed", "progress": 100,
                    "dataset_id": req["dataset_id"], "model_id": registered.id, "model_version_id": version.id,
                    "architecture": "shared_transformer_four_heads", "tasks": ["fault", "anomaly", "energy", "efficiency"],
                    "metrics": metrics, "artifact_path": str(artifact), "organization_id": organization_id,
                    "finished_at": datetime.utcnow().isoformat()}
        (ARTIFACT_DIR / f"{job_id}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        job.update(manifest)
    except Exception as exc:
        db.rollback(); job.update(status="failed", error=str(exc), finished_at=datetime.utcnow().isoformat())
    finally:
        db.close()


@router.post("/jobs", dependencies=[Depends(require_permission("training:write"))])
def create_job(req: TrainRequest, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    row = db.execute(text("SELECT organization_id FROM datasets WHERE id=:id"), {"id": req.dataset_id}).fetchone()
    if not row:
        raise HTTPException(404, "数据集不存在")
    assert_organization_access(row[0], user)
    if not req.sensor_columns:
        raise HTTPException(400, "至少选择一个传感器列")
    job_id = "ifm_" + uuid.uuid4().hex[:10]
    JOBS[job_id] = {"job_id": job_id, "name": req.name, "status": "pending", "progress": 0,
                    "dataset_id": req.dataset_id, "created_at": datetime.utcnow().isoformat()}
    threading.Thread(target=_train, args=(job_id, req.model_dump(), user.get("organization_id")), daemon=True).start()
    return JOBS[job_id]


@router.get("/jobs", dependencies=[Depends(require_permission("training:read"))])
def list_jobs(user: dict = Depends(require_auth)):
    manifests = {}
    for path in ARTIFACT_DIR.glob("ifm_*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        if not item.get("organization_id") or item.get("organization_id") == user.get("organization_id"):
            manifests[item["job_id"]] = item
    manifests.update(JOBS)
    items = sorted(manifests.values(), key=lambda item: item.get("created_at", item.get("finished_at", "")), reverse=True)
    return {"total": len(items), "items": items}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("training:read"))])
def get_job(job_id: str, user: dict = Depends(require_auth)):
    item = JOBS.get(job_id)
    path = ARTIFACT_DIR / f"{job_id}.json"
    if not item and path.exists():
        item = json.loads(path.read_text(encoding="utf-8"))
    if not item:
        raise HTTPException(404, "通用模型训练任务不存在")
    assert_organization_access(item.get("organization_id"), user)
    return item


@router.post("/jobs/{job_id}/predict", dependencies=[Depends(require_permission("training:read"))])
def predict(job_id: str, req: PredictRequest, user: dict = Depends(require_auth)):
    manifest_path, artifact_path = ARTIFACT_DIR / f"{job_id}.json", ARTIFACT_DIR / f"{job_id}.pt"
    if not manifest_path.exists() or not artifact_path.exists():
        raise HTTPException(404, "模型产物不存在")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); assert_organization_access(manifest.get("organization_id"), user)
    checkpoint = torch.load(artifact_path, map_location="cpu")
    missing = [column for column in checkpoint["sensor_columns"] if column not in req.sensor_values]
    if missing:
        raise HTTPException(400, "缺少传感器字段: " + ", ".join(missing))
    numeric = np.array([[float(req.sensor_values[column]) for column in checkpoint["sensor_columns"]]], dtype="float32")
    numeric = (numeric - np.array(checkpoint["scaler_mean"])) / np.array(checkpoint["scaler_scale"])
    model = IndustrialTransformer(len(checkpoint["sensor_columns"]), len(checkpoint["fault_classes"]), checkpoint["hidden_dim"])
    model.load_state_dict(checkpoint["state_dict"]); model.eval()
    with torch.no_grad():
        output = model(torch.tensor(numeric, dtype=torch.float32), torch.tensor(_text_vector(req.inspection_text)[None, :]))
    probabilities = torch.softmax(output["fault"], 1)[0]
    index = int(probabilities.argmax())
    return {"job_id": job_id, "fault": checkpoint["fault_classes"][index], "fault_confidence": float(probabilities[index]),
            "anomaly_probability": float(torch.sigmoid(output["anomaly"])[0]),
            "energy_kwh": float(output["energy"][0] * checkpoint["target_scalers"]["energy"][1] + checkpoint["target_scalers"]["energy"][0]),
            "efficiency_score": float(output["efficiency"][0] * checkpoint["target_scalers"]["efficiency"][1] + checkpoint["target_scalers"]["efficiency"][0])}


@router.get("/capabilities")
def capabilities():
    return {"status": "ready", "version": "industrial_generalist_v1", "backbone": "transformer_encoder",
            "tasks": ["fault_classification", "anomaly_detection", "energy_regression", "efficiency_regression"],
            "partial_label_masking": True, "model_registry": True}
