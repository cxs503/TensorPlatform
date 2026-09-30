"""Industrial multimodal training: sensor table + inspection text."""
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import joblib
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_permission
from api.tenant_access import assert_organization_access, create_organization_id
from database.config import SessionLocal, get_db
from models.core import Dataset

router = APIRouter(prefix="/multimodal-training", tags=["工业多模态融合训练"])
ARTIFACT_DIR = Path(os.getenv("MULTIMODAL_MODEL_DIR", "/app/uploads/models/multimodal"))
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
JOBS = {}


def flatten_text(values):
    """Pickle-safe adapter from ColumnTransformer's 2-D selection to text."""
    return np.asarray(values).reshape(-1).astype(str)


class MultimodalTrainRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dataset_id: int
    task_type: str = "classification"
    sensor_columns: List[str]
    text_column: str
    target_column: str
    test_size: float = Field(default=0.2, gt=0.05, lt=0.5)
    max_text_features: int = Field(default=1000, ge=50, le=10000)
    n_estimators: int = Field(default=100, ge=10, le=1000)
    fusion_mode: str = "fusion"  # fusion / sensor_only / text_only


class PredictRequest(BaseModel):
    sensor_values: dict
    inspection_text: str = ""


def _demo_frame(rows: int = 3600) -> pd.DataFrame:
    """Generate rule-based compressor conditions with noisy, incomplete inspection text."""
    rng = np.random.default_rng(20260812)
    labels = np.array(["normal", "filter_blocked", "cooling_fault", "bearing_wear", "leakage", "overload"])
    y = np.resize(labels, rows); rng.shuffle(y)
    base = {
        "discharge_pressure": 0.72, "discharge_temperature": 78.0, "vibration_rms": 2.2,
        "power_kw": 72.0, "current_a": 118.0, "air_flow": 12.8,
        "ambient_temperature": 27.0, "running_hours": 4200.0,
    }
    effects = {
        "normal": {}, "filter_blocked": {"power_kw": 11, "air_flow": -3.0, "discharge_pressure": -0.06},
        "cooling_fault": {"discharge_temperature": 24, "ambient_temperature": 5},
        "bearing_wear": {"vibration_rms": 5.0, "current_a": 9, "running_hours": 2800},
        "leakage": {"discharge_pressure": -0.15, "air_flow": -2.2, "power_kw": 7},
        "overload": {"power_kw": 25, "current_a": 38, "discharge_temperature": 15},
    }
    texts = {
        "normal": ["运行平稳，未发现异常", "压力流量正常", "例行巡检正常"],
        "filter_blocked": ["过滤器积尘，进气声音偏闷", "流量下降，建议检查滤芯", "滤网可能堵塞"],
        "cooling_fault": ["排气温度持续偏高", "冷却风扇存在异响", "散热器表面温度高"],
        "bearing_wear": ["轴承位置振动明显", "机体有周期性异响", "振动比上次巡检增大"],
        "leakage": ["管路附近可听到漏气声", "压力保持能力下降", "接头疑似泄漏"],
        "overload": ["电流偏大且机体发热", "设备长时间高负荷运行", "功率接近上限"],
    }
    records = []
    for idx, label in enumerate(y):
        record = {"timestamp": (pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=5 * idx)).isoformat(), "device_id": f"AC-{idx % 12 + 1:02d}"}
        for key, value in base.items():
            scale = {"discharge_pressure": .025, "discharge_temperature": 3.5, "vibration_rms": .45,
                     "power_kw": 4.0, "current_a": 7.0, "air_flow": .55,
                     "ambient_temperature": 3.0, "running_hours": 600.0}[key]
            record[key] = round(value + effects[str(label)].get(key, 0) + rng.normal(0, scale), 4)
        # 15% weak/missing text and 10% cross-condition ambiguity prevent label leakage.
        if rng.random() < .15:
            record["inspection_text"] = rng.choice(["", "需要复查", "现场噪声较大，判断不清", "例行巡检记录不完整"])
        elif rng.random() < .10:
            record["inspection_text"] = rng.choice(sum(texts.values(), []))
        else:
            record["inspection_text"] = rng.choice(texts[str(label)])
        record["fault_type"] = str(label)
        anomaly = 0 if str(label) == "normal" else 1
        # Targets share physical causes but retain independent noise, supporting real multi-task learning.
        load_factor = max(.35, record["power_kw"] / 100.0)
        record["anomaly_label"] = anomaly
        record["energy_kwh"] = round(record["power_kw"] / 12.0 + rng.normal(0, .12), 4)
        specific_power = record["power_kw"] / max(record["air_flow"], .5)
        record["efficiency_score"] = round(float(np.clip(105 - specific_power * 5.2 - anomaly * 5 + rng.normal(0, 1.2), 20, 100)), 4)
        records.append(record)
    return pd.DataFrame(records)


@router.post("/demo/air-compressor", dependencies=[Depends(require_permission("datasets:upload"))])
def create_air_compressor_demo(rows: int = 3600, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    rows = max(600, min(int(rows), 20000))
    frame = _demo_frame(rows)
    dataset = Dataset(name="空压机传感器与巡检文本故障诊断（规律仿真）",
                      description="工业多模态V1示范数据：物理规律、噪声、弱文本和缺失文本；仅用于平台验证。",
                      row_count=len(frame), stage="virtual_multimodal", organization_id=create_organization_id(None, user))
    db.add(dataset); db.commit(); db.refresh(dataset)
    path = Path("/app/uploads/datasets") / f"{dataset.id}_multimodal_air_compressor.csv"
    path.parent.mkdir(parents=True, exist_ok=True); frame.to_csv(path, index=False)
    dataset.file_path, dataset.size_bytes = str(path), path.stat().st_size
    dataset.columns_info = [{"name": c, "dtype": str(frame[c].dtype), "role": "text" if c == "inspection_text" else "target" if c == "fault_type" else "sensor" if pd.api.types.is_numeric_dtype(frame[c]) else "metadata"} for c in frame.columns]
    db.commit()
    return {"dataset_id": dataset.id, "name": dataset.name, "rows": len(frame), "stage": dataset.stage}


def _dataset_path(db: Session, dataset_id: int, user: dict) -> Path:
    row = db.execute(text("SELECT id, file_path, organization_id FROM datasets WHERE id=:id"), {"id": dataset_id}).fetchone()
    if not row:
        raise HTTPException(404, "数据集不存在")
    assert_organization_access(row[2], user)
    path = Path(row[1]) if row[1] else None
    if not path or not path.exists():
        raise HTTPException(400, "数据集没有可读取的 CSV 文件")
    return path


@router.get("/datasets/{dataset_id}/schema", dependencies=[Depends(require_permission("training:read"))])
def dataset_schema(dataset_id: int, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    path = _dataset_path(db, dataset_id, user)
    frame = pd.read_csv(path, nrows=200)
    return {"dataset_id": dataset_id, "columns": [{"name": c, "dtype": str(frame[c].dtype),
             "role_hint": "sensor" if pd.api.types.is_numeric_dtype(frame[c]) else "text"} for c in frame.columns]}


def _train(job_id: str, request: dict, organization_id: Optional[int]):
    job = JOBS[job_id]
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT file_path FROM datasets WHERE id=:id"), {"id": request["dataset_id"]}).fetchone()
        if not row or not row[0] or not Path(row[0]).exists():
            raise ValueError("训练数据文件不存在")
        frame = pd.read_csv(row[0])
        required = request["sensor_columns"] + [request["text_column"], request["target_column"]]
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError("缺少字段: " + ", ".join(missing))
        if request["text_column"] in request["sensor_columns"] or request["target_column"] in request["sensor_columns"]:
            raise ValueError("传感器列不能包含文本列或目标列")
        data = frame[required].copy().dropna(subset=[request["target_column"]])
        if len(data) < 20:
            raise ValueError("有效配对记录少于20条")
        for col in request["sensor_columns"]:
            data[col] = pd.to_numeric(data[col], errors="coerce")
        data[request["text_column"]] = data[request["text_column"]].fillna("").astype(str)
        if not data[request["text_column"]].str.strip().any():
            raise ValueError("文本模态全部为空")

        mode = request.get("fusion_mode", "fusion")
        if mode not in {"fusion", "sensor_only", "text_only"}:
            raise ValueError("fusion_mode 仅支持 fusion/sensor_only/text_only")
        transformers = []
        if mode in {"fusion", "sensor_only"}:
            transformers.append(("sensor", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), request["sensor_columns"]))
        if mode in {"fusion", "text_only"}:
            transformers.append(("inspection_text", Pipeline([("flatten", FunctionTransformer(flatten_text, validate=False)),
                                          ("tfidf", TfidfVectorizer(max_features=request["max_text_features"], ngram_range=(1, 2)))]), request["text_column"]))
        features = ColumnTransformer(transformers)
        task_type = request["task_type"]
        if task_type == "classification":
            estimator = RandomForestClassifier(n_estimators=request["n_estimators"], random_state=42, class_weight="balanced")
        elif task_type == "regression":
            estimator = RandomForestRegressor(n_estimators=request["n_estimators"], random_state=42)
        else:
            raise ValueError("task_type 仅支持 classification/regression")
        model = Pipeline([("features", features), ("model", estimator)])
        X = data[request["sensor_columns"] + [request["text_column"]]]
        y = data[request["target_column"]]
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=request["test_size"], random_state=42,
                                                             stratify=y if task_type == "classification" and y.nunique() > 1 else None)
        job.update({"status": "running", "progress": 25, "rows": len(data)})
        model.fit(X_train, y_train)
        prediction = model.predict(X_test)
        metrics = ({"accuracy": float(accuracy_score(y_test, prediction)), "f1_weighted": float(f1_score(y_test, prediction, average="weighted")),
                    "labels": sorted(map(str, pd.Series(y_test).unique())),
                    "confusion_matrix": confusion_matrix(y_test, prediction, labels=sorted(pd.Series(y_test).unique())).tolist()}
                   if task_type == "classification" else
                   {"r2": float(r2_score(y_test, prediction)), "rmse": float(np.sqrt(mean_squared_error(y_test, prediction))), "mae": float(mean_absolute_error(y_test, prediction))})
        artifact = ARTIFACT_DIR / f"{job_id}.joblib"
        joblib.dump(model, artifact)
        manifest = {"job_id": job_id, "name": request["name"], "task_type": task_type, "dataset_id": request["dataset_id"],
                    "modalities": ["sensor_table", "inspection_text"] if mode == "fusion" else ["sensor_table" if mode == "sensor_only" else "inspection_text"],
                    "fusion_mode": mode, "sensor_columns": request["sensor_columns"],
                    "text_column": request["text_column"], "target_column": request["target_column"], "metrics": metrics,
                    "artifact_path": str(artifact), "created_at": datetime.now().isoformat(), "organization_id": organization_id}
        (ARTIFACT_DIR / f"{job_id}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        job.update({**manifest, "status": "completed", "progress": 100, "finished_at": datetime.now().isoformat()})
    except Exception as exc:
        job.update({"status": "failed", "error": str(exc), "finished_at": datetime.now().isoformat()})
    finally:
        db.close()


@router.post("/jobs", dependencies=[Depends(require_permission("training:write"))])
def create_job(req: MultimodalTrainRequest, db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    _dataset_path(db, req.dataset_id, user)
    if not req.sensor_columns and req.fusion_mode != "text_only":
        raise HTTPException(400, "至少选择一个传感器列")
    job_id = "mm_" + uuid.uuid4().hex[:10]
    JOBS[job_id] = {"job_id": job_id, "name": req.name, "status": "pending", "progress": 0,
                    "dataset_id": req.dataset_id, "modalities": ["sensor_table", "inspection_text"], "created_at": datetime.now().isoformat()}
    threading.Thread(target=_train, args=(job_id, req.model_dump(), user.get("organization_id")), daemon=True).start()
    return JOBS[job_id]


@router.get("/jobs", dependencies=[Depends(require_permission("training:read"))])
def list_jobs(user: dict = Depends(require_auth)):
    items = sorted(JOBS.values(), key=lambda j: j.get("created_at", ""), reverse=True)
    return {"total": len(items), "items": items}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_permission("training:read"))])
def get_job(job_id: str, user: dict = Depends(require_auth)):
    job = JOBS.get(job_id)
    if not job:
        manifest = ARTIFACT_DIR / f"{job_id}.json"
        if manifest.exists():
            return json.loads(manifest.read_text(encoding="utf-8"))
        raise HTTPException(404, "多模态训练任务不存在")
    return job


@router.post("/jobs/{job_id}/predict", dependencies=[Depends(require_permission("training:read"))])
def predict(job_id: str, req: PredictRequest, user: dict = Depends(require_auth)):
    manifest_path = ARTIFACT_DIR / f"{job_id}.json"
    artifact_path = ARTIFACT_DIR / f"{job_id}.joblib"
    if not manifest_path.exists() or not artifact_path.exists():
        raise HTTPException(404, "模型产物不存在或训练尚未完成")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert_organization_access(manifest.get("organization_id"), user)
    missing = [c for c in manifest["sensor_columns"] if c not in req.sensor_values]
    if missing:
        raise HTTPException(400, "缺少传感器字段: " + ", ".join(missing))
    row = {c: req.sensor_values[c] for c in manifest["sensor_columns"]}
    row[manifest["text_column"]] = req.inspection_text
    result = joblib.load(artifact_path).predict(pd.DataFrame([row]))
    value = result[0].item() if hasattr(result[0], "item") else result[0]
    return {"job_id": job_id, "prediction": value, "modalities": manifest["modalities"]}


@router.get("/capabilities")
def capabilities():
    return {"status": "ready", "version": "industrial_multimodal_v1", "supported_modalities": ["sensor_table", "inspection_text"],
            "fusion": "feature_level", "task_types": ["classification", "regression"],
            "generalist_endpoint": "/industrial-foundation/jobs",
            "planned_modalities": ["image", "audio", "vibration_waveform"]}
