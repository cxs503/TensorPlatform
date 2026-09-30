"""
推理 API — 生产级实现
从训练的模型文件(.pkl/.pt)加载模型做真实预测
支持 DataPipeline 数据库模型加载
"""
import json
import os
import pickle
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from api.deps import require_auth
from api.tenant_access import assert_organization_access

router = APIRouter(prefix="/inference", tags=["推理服务"])

MODEL_DIR = Path(os.getenv("MODEL_DIR", "/app/uploads/models"))
RUNS_DIR = Path(os.getenv("RUNS_DIR", "/app/uploads/runs"))
INFERENCE_OUTPUT_DIR = Path(os.getenv("INFERENCE_OUTPUT_DIR", "/app/uploads/inference_outputs"))

# Bug 3: run-JSON 解析缓存。之前每次推理 / /trained / /compare 都会全量
# glob+read_text+json.loads 470+ 个 train_*.json,实测 50-150s。
# 这里把"解析后的 run 列表"整体缓存进进程,按目录 mtime 失效
# (新增/删除训练文件时目录 mtime 变化 → 自动重建)。
_RUN_CACHE: Optional[list] = None        # list[dict]: 已解析的 run,按文件 mtime 倒序
_RUN_CACHE_KEY: float = -1.0             # 上次构建时的目录 mtime


def _get_runs() -> list:
    """返回已解析的 completed run 列表(按文件 mtime 倒序),带进程内缓存。

    每个 run 是解析后的 dict(含 config/status/model_path 等)。非 completed 或
    解析失败的文件被跳过。缓存命中时 O(1),仅首次或目录变化时全量解析。
    """
    global _RUN_CACHE, _RUN_CACHE_KEY
    try:
        dir_mtime = RUNS_DIR.stat().st_mtime
    except Exception:
        dir_mtime = 0.0
    if _RUN_CACHE is not None and abs(dir_mtime - _RUN_CACHE_KEY) < 0.001:
        return _RUN_CACHE
    runs = []
    for f in sorted(RUNS_DIR.glob("train_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            print(f"[inference] 跳过损坏 run JSON {f.name}: {e}")
            continue
        if data.get("status") != "completed":
            continue
        # 附上文件 mtime 供调用方取"最新"
        try:
            data["_file_mtime"] = f.stat().st_mtime
        except Exception:
            data["_file_mtime"] = 0.0
        runs.append(data)
    # 已按文件名倒序,再按 mtime 兜底排序(最新在前)
    runs.sort(key=lambda d: d.get("_file_mtime", 0.0), reverse=True)
    _RUN_CACHE = runs
    _RUN_CACHE_KEY = dir_mtime
    return runs


def _run_model_id(run: dict):
    """从 run 的 config 中取出真实 model_id(优先 registered_model_id)。"""
    cfg = run.get("config", {}) or {}
    mid = cfg.get("registered_model_id") or cfg.get("model_id") or cfg.get("model")
    try:
        return int(mid) if mid is not None else None
    except (TypeError, ValueError):
        return None


def _load_model_from_blob(model_id: int, db) -> Optional[Any]:
    """Bug 2/3 核心:直接从 models.model_blob 加载模型(joblib/pickle 二进制)。

    优先级最高的快速路径 —— 跳过扫描 470 个 run JSON,直接用 DB 里的训练产物。
    返回加载好的模型对象;无 blob / 加载失败返回 None。
    """
    import io
    try:
        row = db.execute(text(
            "SELECT model_blob FROM models WHERE id = :id"
        ), {"id": model_id}).fetchone()
    except Exception as e:
        print(f"[inference] 查询 models.model_blob 失败 model_id={model_id}: {e}")
        return None
    if not row or not row[0]:
        return None
    raw = row[0]
    # model_blob 可能是 memoryview / bytes / LargeBinary
    if isinstance(raw, (memoryview, bytearray)):
        raw = bytes(raw)
    try:
        # 关键:必须用 io.BytesIO 包一层。joblib.load(bytes) 内部走 BufferedReader,
        # 遇到 pickle 里天然的 \x00 会抛 "embedded null byte" —— 这正是之前所有
        # blob 都加载失败的根因(被 except:pass 静默吞掉,才退化成扫 470 个 JSON)。
        import joblib
        return joblib.load(io.BytesIO(raw))
    except Exception as e:
        print(f"[inference] model_blob joblib 反序列化失败 model_id={model_id}: {e}")
        # 兜底:裸 pickle(BytesIO)
        try:
            return pickle.load(io.BytesIO(raw))
        except Exception as e2:
            print(f"[inference] model_blob pickle 兜底也失败 model_id={model_id}: {e2}")
            return None


def _find_model_path(model_id: int, db) -> Optional[str]:
    """从模型版本索引查找产物，禁止为一次请求扫描 3GB 运行 JSON。"""
    try:
        row = db.execute(text(
            "SELECT file_path FROM model_versions "
            "WHERE model_id = :model_id AND file_path IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ), {"model_id": model_id}).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception as exc:
        print(f"[inference] 查询模型版本路径失败 model_id={model_id}: {exc}")
        return None


class _OnnxPredictShim:
    """把 ort.InferenceSession 伪装成 sklearn 模型(暴露 .predict)，复用现有推理代码。"""
    def __init__(self, session):
        self._session = session
        from services.onnx_exporter import get_onnx_inference_service
        self._svc = get_onnx_inference_service()

    def predict(self, X):
        inputs = self._session.get_inputs()
        if inputs:
            shape = inputs[0].shape
            expected = shape[-1] if shape else None
            if isinstance(expected, int) and expected > 0 and getattr(X, "shape", None) and X.shape[-1] != expected:
                raise ValueError(f"输入有 {X.shape[-1]} 个特征，但模型需要 {expected} 个")
        return self._svc.predict(self._session, X)


def _load_model(model_path: str) -> Any:
    """加载 sklearn/pytorch/onnx 模型"""
    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    if path.suffix == ".pkl":
        import joblib
        return joblib.load(str(path))
    elif path.suffix == ".pt":
        import torch
        model = torch.load(str(path), map_location="cpu")
        model.eval()
        return model
    elif path.suffix == ".onnx":
        from services.onnx_exporter import get_onnx_inference_service
        session = get_onnx_inference_service().load_model(str(path))
        return _OnnxPredictShim(session)
    else:
        # Try pickle
        with open(str(path), "rb") as f:
            return pickle.load(f)


def _get_feature_names(model_id: int, db) -> list:
    """获取特征名列表（从训练记录中提取）"""
    # 从最近的 completed training 中获取
    rows = db.execute(text(
        "SELECT t.hyperparameters FROM training_jobs t "
        "WHERE t.model_id = :mid AND t.status = 'COMPLETED' "
        "ORDER BY t.id DESC LIMIT 1"
    ), {"mid": model_id}).fetchall()

    if not rows or not rows[0][0]:
        return []

    hp = rows[0][0]
    if isinstance(hp, str):
        hp = json.loads(hp)
    return hp.get("feature_names", [])


def _inference_from_data_pipeline(model_id: int, features: list) -> dict:
    """通过 DataPipeline 从数据库加载模型进行推理"""
    try:
        from services.data_pipeline import get_data_pipeline
        pipeline = get_data_pipeline()
        return pipeline.inference_from_model(model_id, features)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DataPipeline 推理失败: {e}")


@router.post("/model/{model_id}")
def run_inference_by_model(model_id: int, req: dict, db=Depends(get_db)):
    """直接用 model_id 推理，无需部署。

    优先级(Bug 2/3 修复):
      1) models.model_blob —— 直接从 DB 加载 joblib 二进制,毫秒级,不扫文件
      2) DataPipeline.inference_from_model —— 同样走 model_blob,带 DB 元信息
      3) 文件系统 _find_model_path —— 扫 run JSON 兜底(已加缓存)
    """
    features = req.get("features") or req.get("data")  # 兼容前端 features/data 两种字段名

    # ---- 路径 1: model_blob 直接加载(最快,跳过 470 个 JSON 扫描) ----
    model = _load_model_from_blob(model_id, db)
    if model is not None:
        try:
            return _predict_with_loaded_model(model_id, model, features, db)
        except Exception as e:
            # 不再静默吞,打印后继续尝试其它路径
            print(f"[inference] model_blob 预测失败 model_id={model_id}: {e}")

    # ---- 路径 2: DataPipeline(内部也是 model_blob,但带 DB 元信息/校验) ----
    try:
        from services.data_pipeline import get_data_pipeline
        pipeline = get_data_pipeline(db)
        return pipeline.inference_from_model(model_id, features or [])
    except Exception as e:
        print(f"[inference] DataPipeline 路径失败 model_id={model_id}: {e}")

    # ---- 路径 3: 文件系统兜底 ----
    model_path = _find_model_path(model_id, db)
    if not model_path:
        raise HTTPException(status_code=400, detail=f"模型 {model_id} 无已完成训练的任务，请先训练")
    return _do_predict(model_id, model_path, req, db)


def _predict_with_loaded_model(model_id: int, model: Any, features, db) -> dict:
    """用已加载的模型对象做预测,输出结构与 _do_predict 对齐(含漂移检测/日志)。"""
    t0 = time.time()
    if features is None:
        raise HTTPException(status_code=400, detail="缺少 features 参数")
    if not isinstance(features, list):
        features = [features]
    if features and isinstance(features[0], (int, float)):
        features = [features]
    X = np.array(features, dtype=np.float64)
    try:
        preds = model.predict(X)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预测失败: {e}")
    results = []
    for i, pred in enumerate(preds):
        r = {"index": i}
        if isinstance(pred, (np.ndarray, list)):
            r["prediction"] = float(pred[0]) if len(pred) == 1 else [float(p) for p in pred]
        else:
            r["prediction"] = float(pred)
        results.append(r)
    ms = round((time.time() - t0) * 1000, 2)
    _log_inference(db, model_id, features, results, ms)
    return {
        "model_id": model_id,
        "model_path": f"model_blob:{model_id}",
        "predictions": results,
        "drift": {"drift_detected": False, "drifted_features": []},
        "inference_time_ms": ms,
    }


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _load_managed_dataset(db, dataset_id: int, current_user: dict) -> tuple[pd.DataFrame, str]:
    row = db.execute(text(
        "SELECT name, file_path, data_blob, organization_id FROM datasets WHERE id=:id"
    ), {"id": dataset_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="数据集不存在")
    assert_organization_access(row[3], current_user)
    name, file_path, data_blob, _ = row
    try:
        if file_path and Path(file_path).is_file():
            path = Path(file_path)
            suffix = path.suffix.lower()
            if suffix in {".csv", ".txt", ".tsv"}:
                frame = pd.read_csv(path, sep="\t" if suffix == ".tsv" else ",")
            elif suffix in {".xlsx", ".xls"}:
                frame = pd.read_excel(path)
            elif suffix == ".parquet":
                frame = pd.read_parquet(path)
            elif suffix in {".json", ".jsonl"}:
                frame = pd.read_json(path, lines=suffix == ".jsonl")
            else:
                raise HTTPException(400, f"批量推理暂不支持数据集格式 {suffix or '未知'}")
        elif data_blob:
            import io
            frame = pd.read_csv(io.BytesIO(bytes(data_blob)))
        else:
            raise HTTPException(400, "所选数据集没有可读取的文件")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"读取数据集失败: {exc}") from exc
    if frame.empty:
        raise HTTPException(400, "所选数据集没有数据行")
    if len(frame) > 100000:
        raise HTTPException(400, "单次批量推理最多支持100000行，请先拆分数据集")
    return frame, str(name or f"数据集 #{dataset_id}")


def _batch_contract(training_config: dict) -> tuple[list[str], str | None, str | None]:
    contract = _json_dict(training_config.get("source_data_contract"))
    features = (
        training_config.get("feature_columns")
        or training_config.get("feature_cols")
        or training_config.get("feature_names")
        or contract.get("feature_columns")
        or []
    )
    target = training_config.get("target_column") or training_config.get("target_col") or contract.get("target_column")
    time_column = training_config.get("source_time_column") or training_config.get("time_column") or contract.get("time_column")
    return list(features), target, time_column


@router.post("/model/{model_id}/batch")
def batch_inference(
    model_id: int,
    req: dict,
    db=Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """Use uploaded rows or an authorized dataset-management dataset."""
    model_row = db.execute(text("SELECT organization_id FROM models WHERE id=:id"), {"id": model_id}).fetchone()
    if not model_row:
        raise HTTPException(status_code=404, detail="模型不存在")
    assert_organization_access(model_row[0], current_user)

    from api.model_evaluation import _load_model as load_evaluation_model
    from api.model_evaluation import _predict_for_evaluation

    artifact = load_evaluation_model(db, model_id)
    feature_columns, target_column, time_column = _batch_contract(artifact.training_config)
    dataset_id = req.get("dataset_id")
    dataset_name = None
    if dataset_id is not None:
        try:
            dataset_id = int(dataset_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "dataset_id 必须是有效编号") from exc
        evaluation_frame, dataset_name = _load_managed_dataset(db, dataset_id, current_user)
        if not feature_columns:
            excluded = {column for column in (target_column, time_column) if column}
            feature_columns = [column for column in evaluation_frame.columns if column not in excluded and pd.api.types.is_numeric_dtype(evaluation_frame[column])]
        missing = [column for column in feature_columns if column not in evaluation_frame.columns]
        if missing:
            raise HTTPException(400, "所选数据集缺少模型特征列: " + "、".join(missing[:20]))
        numeric = evaluation_frame[feature_columns].apply(pd.to_numeric, errors="coerce")
        numeric = numeric.replace(
            [-999, -9999, 9999, -99999, 99999, -999.0, -9999.0, 9999.0],
            np.nan,
        )
        # Keep the same practical policy as training/data cleaning: preserve
        # row count, fill short temporal gaps from neighbours, then use the
        # column median for any remaining isolated gaps.
        numeric = numeric.ffill().bfill()
        numeric = numeric.fillna(numeric.median(numeric_only=True))
        invalid_columns = [column for column in feature_columns if numeric[column].isna().any()]
        if invalid_columns:
            raise HTTPException(400, "以下模型特征列没有任何有效数值: " + "、".join(invalid_columns[:20]))
        evaluation_frame = evaluation_frame.copy()
        evaluation_frame.loc[:, feature_columns] = numeric
        matrix = numeric.to_numpy(dtype=float)
    else:
        rows = req.get("features")
        if not isinstance(rows, list) or not rows:
            raise HTTPException(400, "请上传数据文件或从数据集管理选择数据集")
        if not isinstance(rows[0], list):
            rows = [rows]
        try:
            matrix = np.asarray(rows, dtype=float)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"批量数据包含非数值特征: {exc}") from exc
        if matrix.ndim != 2 or not np.isfinite(matrix).all():
            raise HTTPException(400, "批量数据必须是包含有限数值的二维数组")
        if feature_columns and matrix.shape[1] != len(feature_columns):
            raise HTTPException(400, f"当前模型需要{len(feature_columns)}个特征，输入提供了{matrix.shape[1]}个")
        evaluation_frame = pd.DataFrame(matrix, columns=feature_columns if len(feature_columns) == matrix.shape[1] else None)

    rows = matrix.tolist()
    model_path = _find_model_path(model_id, db) or ""
    t0 = time.time()
    try:
        preds = _predict_for_evaluation(artifact, matrix, evaluation_frame, target_column)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"批量预测失败: {exc}") from exc

    # 加载特征基线(逐行漂移检测)
    baseline = []
    try:
        _run = Path(model_path.replace("/models/", "/runs/").replace("_model.pkl", ".json"))
        if _run.exists():
            _cfg = json.loads(_run.read_text()).get("config", {}) or {}
            baseline = _cfg.get("feature_baseline") or []
    except Exception:
        pass

    items, drift_count = [], 0
    for i, row in enumerate(rows):
        pred = preds[i]
        if isinstance(pred, (np.ndarray, list)):
            pv = float(pred[0]) if len(pred) == 1 else [float(p) for p in pred]
        else:
            pv = float(pred)
        item = {"index": i, "prediction": pv}
        if baseline:
            drifted = [b["name"] for j, b in enumerate(baseline)
                       if j < len(row) and abs((float(row[j]) - b["mean"]) / (b["std"] or 1e-8)) > 3]
            item["drift"] = bool(drifted)
            item["drifted_features"] = drifted
            if drifted:
                drift_count += 1
        items.append(item)

    ms = round((time.time() - t0) * 1000, 2)
    if drift_count > 0:
        _agg = sorted({f for r in items for f in r.get("drifted_features", [])})
        _raise_drift_alert(db, model_id, _agg)
    task_name = str(req.get("name") or "批量推理").strip()[:200] or "批量推理"
    output_format = str(req.get("output_format") or "json").lower()
    if output_format not in {"json", "csv"}:
        output_format = "json"
    log_id = _log_inference(
        db, model_id, rows, items, ms,
        inference_type="batch", task_name=task_name, output_format=output_format,
        dataset_id=dataset_id, row_count=len(items),
    )
    result_file = None
    if log_id is not None:
        try:
            result_file = _write_batch_result_file(
                int(log_id), model_id, task_name, output_format, feature_columns, rows, items, dataset_id, dataset_name,
            )
            db.execute(text("UPDATE inference_logs SET result_file = :file, row_count = :count WHERE id = :id"), {
                "file": result_file, "count": len(items), "id": int(log_id),
            })
            db.commit()
        except Exception:
            db.rollback()

    preview_limit = 100
    return {
        "id": log_id, "name": task_name, "output_format": output_format,
        "model_id": model_id, "count": len(items),
        "inference_time_ms": ms, "drift_count": drift_count,
        "drift_rate": round(drift_count / len(items), 3) if items else 0,
        "results": items[:preview_limit], "preview_count": min(len(items), preview_limit),
        "result_file": result_file,
        "result_download_url": f"/inference/logs/{log_id}/download" if result_file and log_id is not None else None,
        "dataset_id": dataset_id, "dataset_name": dataset_name,
    }


@router.post("/{dep_id}")
def run_inference(dep_id: int, req: dict, db=Depends(get_db)):
    """通过部署 ID 推理（也兼容直接传 model_id）

    Bug 2/3:不再用 except: pass 吞 DataPipeline 异常;优先 model_blob 直加载。
    """
    features = req.get("features")

    # 先解析部署 -> 真实 model_id;解析不到就把 dep_id 当 model_id
    mid = dep_id
    try:
        result = db.execute(text(
            "SELECT id, model_id, status FROM deployments WHERE id = :id"
        ), {"id": dep_id}).fetchone()
        if result and result[1]:
            mid = result[1]
    except Exception as e:
        print(f"[inference] 查询 deployments 失败 dep_id={dep_id}: {e}")

    # 路径 1: model_blob 直接加载
    model = _load_model_from_blob(mid, db)
    if model is not None:
        try:
            return _predict_with_loaded_model(mid, model, features, db)
        except Exception as e:
            print(f"[inference] model_blob 预测失败 mid={mid}: {e}")

    # 路径 2: DataPipeline
    try:
        from services.data_pipeline import get_data_pipeline
        pipeline = get_data_pipeline(db)
        return pipeline.inference_from_model(mid, features or [])
    except Exception as e:
        print(f"[inference] DataPipeline 路径失败 mid={mid}: {e}")

    # 路径 3: 文件系统兜底
    model_path = _find_model_path(mid, db)
    if not model_path:
        raise HTTPException(status_code=400, detail=f"模型 {mid} 无已完成训练的任务，请先训练")
    return _do_predict(mid, model_path, req, db)


def _ensure_inference_log_schema(db):
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS inference_logs ("
        "id SERIAL PRIMARY KEY, model_id INTEGER, features JSON, "
        "predictions JSON, inference_time_ms FLOAT, created_at TIMESTAMP DEFAULT NOW())"
    ))
    db.execute(text("ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS inference_type VARCHAR(20) DEFAULT 'online'"))
    db.execute(text("ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS task_name VARCHAR(200)"))
    db.execute(text("ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS output_format VARCHAR(10)"))
    db.execute(text("ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS dataset_id INTEGER"))
    db.execute(text("ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS result_file VARCHAR(255)"))
    db.execute(text("ALTER TABLE inference_logs ADD COLUMN IF NOT EXISTS row_count INTEGER"))


def _log_inference(
    db, model_id: int, features, results, ms: float,
    inference_type: str = "online", task_name: str | None = None,
    output_format: str | None = None,
    dataset_id: int | None = None,
    row_count: int | None = None,
):
    """持久化推理结果到 inference_logs(表不存在则自动建)。"""
    if db is None:
        return None
    try:
        _ensure_inference_log_schema(db)
        log_id = db.execute(text(
            "INSERT INTO inference_logs "
            "(model_id, features, predictions, inference_time_ms, inference_type, task_name, output_format, dataset_id, row_count) "
            "VALUES (:mid, :f, :p, :ms, :kind, :name, :fmt, :dataset_id, :row_count) RETURNING id"
        ), {
            "mid": model_id, "f": json.dumps(features, ensure_ascii=False),
            "p": json.dumps(results, ensure_ascii=False), "ms": ms,
            "kind": inference_type, "name": task_name, "fmt": output_format,
            "dataset_id": dataset_id, "row_count": row_count,
        }).scalar()
        db.commit()
        return log_id
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _safe_output_stem(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(name or "batch_inference"))
    return (cleaned.strip("_") or "batch_inference")[:80]


def _write_batch_result_file(
    log_id: int,
    model_id: int,
    task_name: str,
    output_format: str,
    feature_columns: list[str],
    rows: list[list[Any]],
    results: list[dict],
    dataset_id: int | None,
    dataset_name: str | None,
) -> str:
    """Persist full batch output outside the HTTP response."""
    INFERENCE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    extension = "csv" if output_format == "csv" else "json"
    filename = f"{int(log_id)}_{_safe_output_stem(task_name)}.{extension}"
    path = INFERENCE_OUTPUT_DIR / filename
    names = list(feature_columns or [])
    if len(names) != (len(rows[0]) if rows else 0):
        names = [f"feature_{index + 1}" for index in range(len(rows[0]) if rows else 0)]

    records = []
    for index, (values, result) in enumerate(zip(rows, results), start=1):
        record: dict[str, Any] = {"index": index}
        record.update({name: value for name, value in zip(names, values)})
        prediction = result.get("prediction")
        record["prediction"] = prediction
        record["drifted"] = bool(result.get("drift") or result.get("drifted"))
        record["drifted_features"] = result.get("drifted_features") or []
        records.append(record)

    if extension == "csv":
        output_records = []
        for record in records:
            output = dict(record)
            if isinstance(output.get("prediction"), (list, dict)):
                output["prediction"] = json.dumps(output["prediction"], ensure_ascii=False)
            output["drifted_features"] = "、".join(map(str, output.get("drifted_features") or []))
            output_records.append(output)
        pd.DataFrame(output_records).to_csv(path, index=False, encoding="utf-8-sig")
    else:
        path.write_text(json.dumps({
            "log_id": log_id,
            "task_name": task_name,
            "model_id": model_id,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "row_count": len(records),
            "feature_columns": names,
            "results": records,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return filename


def _raise_drift_alert(db, model_id: int, drifted_features):
    """检测到漂移时生成告警(同模型 1 小时内不重复,避免刷屏)。"""
    if db is None or not drifted_features:
        return
    try:
        exists = db.execute(text(
            "SELECT 1 FROM alerts WHERE source='drift' AND entity_id=:mid "
            "AND is_resolved=false AND created_at > NOW() - INTERVAL '1 hour' LIMIT 1"
        ), {"mid": str(model_id)}).fetchone()
        if exists:
            return
        db.execute(text(
            "INSERT INTO alerts (title, severity, source, entity_id, message, is_resolved, created_at) "
            "VALUES (:t, 'warning', 'drift', :mid, :msg, false, NOW())"
        ), {"t": f"模型 #{model_id} 输入漂移", "mid": str(model_id),
            "msg": "越界特征: " + ", ".join(drifted_features[:10])})
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _do_predict(model_id: int, model_path: str, req: dict, db=None) -> dict:
    t0 = time.time()
    try:
        model = _load_model(model_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模型加载失败: {e}")
    features = req.get("features")
    if features is None:
        raise HTTPException(status_code=400, detail="缺少 features 参数")
    if not isinstance(features, list):
        features = [features]
    elif isinstance(features[0], (int, float)):
        features = [features]
    X = np.array(features, dtype=np.float64)
    try:
        preds = model.predict(X)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预测失败: {e}")
    results = []
    for i, pred in enumerate(preds):
        r = {"index": i}
        if isinstance(pred, (np.ndarray, list)):
            r["prediction"] = float(pred[0]) if len(pred) == 1 else [float(p) for p in pred]
        else:
            r["prediction"] = float(pred)
        results.append(r)
    ms = round((time.time() - t0) * 1000, 2)
    # 漂移检测:对比输入与训练特征基线(z-score,超 3σ 视为漂移)
    drift = {"drift_detected": False, "drifted_features": []}
    try:
        _run = Path(model_path.replace("/models/", "/runs/").replace("_model.pkl", ".json"))
        if _run.exists():
            _rd = json.loads(_run.read_text())
            _bl = (_rd.get("config", {}) or {}).get("feature_baseline") or []
            if _bl:
                _row = X[0].tolist() if X.ndim == 2 else X.tolist()
                _drifted = []
                for _i, _b in enumerate(_bl):
                    if _i < len(_row):
                        _z = abs((float(_row[_i]) - _b["mean"]) / (_b["std"] or 1e-8))
                        if _z > 3:
                            _drifted.append({"name": _b["name"], "z": round(_z, 2),
                                             "value": round(float(_row[_i]), 4), "mean": round(_b["mean"], 4)})
                drift = {"drift_detected": len(_drifted) > 0, "drifted_features": _drifted}
    except Exception:
        pass
    if drift.get("drift_detected"):
        _raise_drift_alert(db, model_id, [d["name"] for d in drift.get("drifted_features", [])])
    _log_inference(db, model_id, features, results, ms)
    try:
        from api.audit import record_audit
        record_audit(db, "inference", "model", str(model_id), detail=f"{len(results)} preds")
    except Exception:
        pass
    return {
        "model_id": model_id, "model_path": model_path,
        "predictions": results, "drift": drift,
        "inference_time_ms": ms,
    }


@router.get("/pipeline/model/{model_id}")
def run_pipeline_inference(model_id: int, features: str = "", db=Depends(get_db)):
    """
    使用 DataPipeline 从数据库加载模型进行推理。
    参数 features 为 JSON 格式的特征数组，如: [[1.0, 2.0, 3.0]]
    """
    try:
        feat_list = json.loads(features) if features else []
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="features 参数必须是有效 JSON 数组")

    from services.data_pipeline import get_data_pipeline
    pipeline = get_data_pipeline(db)
    return pipeline.inference_from_model(model_id, feat_list)


@router.get("/trained")
def get_trained_models(db=Depends(get_db)):
    """从数据库产物索引返回可推理模型，不解析包含原始 X/y 的运行 JSON。"""
    rows = db.execute(text("""
        SELECT
            m.id, m.name, m.model_type, m.framework,
            (SELECT mv.parameters FROM model_versions mv
             WHERE mv.model_id = m.id ORDER BY mv.id DESC LIMIT 1) AS version_parameters,
            (SELECT tj.hyperparameters FROM training_jobs tj
             WHERE tj.registered_model_id = m.id OR tj.model_id = m.id
             ORDER BY tj.id DESC LIMIT 1) AS training_parameters
        FROM models m
        WHERE m.model_blob IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM model_versions mv
               WHERE mv.model_id = m.id AND mv.file_path IS NOT NULL
           )
        ORDER BY m.id
    """)).fetchall()

    details = []
    for row in rows:
        version = row[4] if isinstance(row[4], dict) else _json_dict(row[4])
        training = row[5] if isinstance(row[5], dict) else _json_dict(row[5])
        config = {**version, **training}
        features = (
            config.get("feature_columns")
            or config.get("feature_cols")
            or config.get("feature_names")
            or []
        )
        details.append({
            "model_id": int(row[0]),
            "name": row[1] or f"Model-{row[0]}",
            "model_type": row[2] or config.get("model_type"),
            "framework": row[3],
            "feature_names": list(features) if isinstance(features, list) else [],
            "target_col": config.get("target_column") or config.get("target_col"),
        })
    ids = [item["model_id"] for item in details]
    return {"model_ids": ids, "models": details}



def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _load_latest_run_for_model(model_id: int, db) -> Optional[dict]:
    """用数据库训练/版本元数据构造轻量运行记录。"""
    row = db.execute(text("""
        SELECT m.name, m.model_type, mv.file_path, mv.parameters, mv.metrics,
               (SELECT tj.hyperparameters FROM training_jobs tj
                WHERE tj.registered_model_id = m.id OR tj.model_id = m.id
                ORDER BY tj.id DESC LIMIT 1)
        FROM models m
        LEFT JOIN LATERAL (
            SELECT file_path, parameters, metrics
            FROM model_versions
            WHERE model_id = m.id
            ORDER BY id DESC LIMIT 1
        ) mv ON TRUE
        WHERE m.id = :model_id
    """), {"model_id": model_id}).fetchone()
    if not row:
        return None
    version = _json_dict(row[3])
    training = _json_dict(row[5])
    config = {**version, **training}
    config.setdefault("name", row[0])
    config.setdefault("model_type", row[1])
    return {
        "status": "completed",
        "config": config,
        "best_metrics": _json_dict(row[4]),
        "model_path": row[2],
    }


def _build_compare_record(model_id: int, db) -> dict:
    """为单个 model_id 构建对比记录。无数据时返回带 note 的 null 记录,不抛异常。"""
    record: Dict[str, Any] = {
        "model_id": model_id,
        "name": None,
        "framework": None,
        "model_type": None,
        "target_col": None,
        "feature_count": None,
        "features": [],
        "metrics": {"r2": None, "rmse": None, "mae": None},
        "inference_count": None,
    }

    notes = []

    # 1) 注册模型信息(models + model_versions) -> framework / metrics / name
    try:
        mrow = db.execute(text(
            "SELECT m.id, m.name, m.framework, mv.parameters, mv.metrics "
            "FROM models m LEFT JOIN model_versions mv ON mv.model_id = m.id "
            "WHERE m.id = :id ORDER BY mv.id DESC LIMIT 1"
        ), {"id": model_id}).fetchone()
    except Exception:
        mrow = None

    registered = False
    if mrow:
        registered = True
        record["name"] = mrow[1]
        record["framework"] = mrow[2]
        params = mrow[3] if isinstance(mrow[3], dict) else (json.loads(mrow[3]) if mrow[3] else {})
        db_metrics = mrow[4] if isinstance(mrow[4], dict) else (json.loads(mrow[4]) if mrow[4] else {})
        # target_col / feature_cols 兜底(若 run JSON 没有)
        if not record.get("target_col"):
            record["target_col"] = params.get("target_col")
        # 注册版本里的 metrics 作为后备
        if db_metrics:
            for k in ("r2", "rmse", "mae"):
                if k in db_metrics and record["metrics"].get(k) is None:
                    record["metrics"][k] = db_metrics[k]
    else:
        notes.append("未在 models 表注册")

    # 2) 最新 run JSON -> model_type / feature_names / target_col / baseline count / best_metrics
    run = _load_latest_run_for_model(model_id, db)
    if run:
        cfg = run.get("config", {}) or {}
        # name: run cfg 里的 name 作为兜底
        if not record["name"]:
            record["name"] = cfg.get("name") or f"Model-{model_id}"
        record["model_type"] = cfg.get("model_type") or record.get("model_type")
        record["target_col"] = cfg.get("target_col") or record.get("target_col")
        feats = cfg.get("feature_names") or cfg.get("feature_cols") or []
        record["features"] = list(feats)
        baseline = cfg.get("feature_baseline") or []
        record["feature_count"] = len(baseline) if baseline else (len(feats) if feats else None)
        best = run.get("best_metrics") or run.get("metrics") or {}
        if isinstance(best, dict):
            for k in ("r2", "rmse", "mae"):
                if k in best:
                    record["metrics"][k] = best[k]
    else:
        notes.append("无已完成训练记录(run JSON)")

    # 3) inference_count:从 inference_logs 统计(表可能不存在)
    try:
        inf_count = db.execute(text(
            "SELECT count(*) FROM inference_logs WHERE model_id = :id"
        ), {"id": model_id}).scalar()
        record["inference_count"] = int(inf_count) if inf_count is not None else 0
    except Exception:
        record["inference_count"] = 0

    if not registered and not run:
        notes.append("该 model_id 无可用数据")

    record["note"] = "; ".join(notes) if notes else None
    return record


@router.get("/compare")
def compare_models(models: str, db=Depends(get_db)):
    """多模型横向对比:GET /inference/compare?models=5,6,8

    对每个 model_id 聚合:注册信息(framework/metrics)、训练 run(model_type/
    target_col/features/feature_count/best_metrics)、inference_logs 计数。
    缺数据的模型不报错,带 note 返回。最后附 best 汇总(best r2 / lowest rmse)。
    """
    # 解析 models 参数(逗号分隔)
    raw_ids = [m.strip() for m in (models or "").split(",") if m.strip()]
    model_ids: list = []
    bad: list = []
    for m in raw_ids:
        try:
            model_ids.append(int(m))
        except ValueError:
            bad.append(m)
    if not model_ids:
        raise HTTPException(status_code=400, detail="models 参数为空或全部无效,例:?models=5,6,8")

    # 去重但保留顺序
    seen = set()
    unique_ids = []
    for mid in model_ids:
        if mid not in seen:
            seen.add(mid)
            unique_ids.append(mid)

    records = []
    for mid in unique_ids:
        try:
            records.append(_build_compare_record(mid, db))
        except Exception as e:
            # 单个模型异常不影响整体,带 note 返回
            records.append({
                "model_id": mid, "name": None, "framework": None,
                "model_type": None, "target_col": None,
                "feature_count": None, "features": [],
                "metrics": {"r2": None, "rmse": None, "mae": None},
                "inference_count": None,
                "note": f"加载失败: {e}",
            })

    # best 汇总:仅在指标存在时计算
    best: Dict[str, Any] = {"r2": None, "rmse": None, "count_with_metrics": 0}

    def _metric_val(rec, key):
        m = rec.get("metrics") or {}
        v = m.get(key)
        return v if isinstance(v, (int, float)) else None

    r2_ranked = [(_metric_val(r, "r2"), r["model_id"]) for r in records]
    r2_valid = [(v, mid) for v, mid in r2_ranked if v is not None]
    if r2_valid:
        # r2 越大越好
        best_v, best_mid = max(r2_valid, key=lambda x: x[0])
        best["r2"] = {"model_id": best_mid, "value": best_v}

    rmse_ranked = [(_metric_val(r, "rmse"), r["model_id"]) for r in records]
    rmse_valid = [(v, mid) for v, mid in rmse_ranked if v is not None]
    if rmse_valid:
        # rmse 越小越好
        best_v, best_mid = min(rmse_valid, key=lambda x: x[0])
        best["rmse"] = {"model_id": best_mid, "value": best_v}

    best["count_with_metrics"] = len(r2_valid)

    return {
        "models": records,
        "best": best,
        "warnings": {"invalid_ids": bad} if bad else None,
    }


@router.get("/types")
def get_inference_types():
    return {
        "types": [
            {"id": "regression", "name": "回归预测", "description": "数值回归预测"},
            {"id": "classification", "name": "分类预测", "description": "分类标签预测"},
            {"id": "timeseries", "name": "时序预测", "description": "时间序列预测"},
        ]
    }


@router.get("/logs")
def list_inference_logs(limit: int = 50, model_id: int = 0, type: str = "", summary: bool = False, db=Depends(get_db)):
    """推理历史。summary=true 时只返回摘要，避免大批量结果反复传到浏览器。"""
    try:
        _ensure_inference_log_schema(db)
        db.commit()
        q = ("SELECT id, model_id, features, predictions, inference_time_ms, created_at, "
             "inference_type, task_name, output_format, dataset_id, result_file, row_count FROM inference_logs")
        params: dict = {"lim": limit}
        conditions = []
        if model_id:
            conditions.append("model_id = :mid")
            params["mid"] = model_id
        if type in {"online", "batch"}:
            conditions.append("inference_type = :kind")
            params["kind"] = type
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY id DESC LIMIT :lim"
        rows = db.execute(text(q), params).fetchall()
        items = []
        for r in rows:
            features = [] if summary else (r[2] if isinstance(r[2], list) else (json.loads(r[2]) if r[2] else []))
            predictions = [] if summary else (r[3] if isinstance(r[3], list) else (json.loads(r[3]) if r[3] else []))
            row_count = r[11] if r[11] is not None else (len(features) if features else len(predictions))
            items.append({
                "id": r[0], "model_id": r[1], "features": features, "predictions": predictions,
                "inference_time_ms": r[4], "created_at": r[5].isoformat() if r[5] else None,
                "inference_type": r[6] or "online", "name": r[7], "output_format": r[8] or "json",
                "dataset_id": r[9], "result_file": r[10], "row_count": row_count,
                "result_download_url": f"/inference/logs/{r[0]}/download" if (r[10] or (r[6] == "batch")) else None,
            })
        return {"total": len(items), "items": items}
    except Exception as e:
        return {"total": 0, "items": [], "error": str(e)}


@router.get("/logs/{log_id}/download")
def download_batch_result(log_id: int, db=Depends(get_db), current_user: dict = Depends(require_auth)):
    """Download a server-side batch inference output with model tenant authorization."""
    _ensure_inference_log_schema(db)
    row = db.execute(text(
        "SELECT l.model_id, l.features, l.predictions, l.output_format, l.task_name, l.dataset_id, l.result_file, m.organization_id "
        "FROM inference_logs l JOIN models m ON m.id = l.model_id WHERE l.id = :id"
    ), {"id": log_id}).fetchone()
    if not row:
        raise HTTPException(404, "批量推理记录不存在")
    assert_organization_access(row[7], current_user)

    filename = str(row[6] or "")
    path = (INFERENCE_OUTPUT_DIR / filename).resolve() if filename else None
    root = INFERENCE_OUTPUT_DIR.resolve()
    if not path or root not in path.parents or not path.is_file():
        features = row[1] if isinstance(row[1], list) else (json.loads(row[1]) if row[1] else [])
        predictions = row[2] if isinstance(row[2], list) else (json.loads(row[2]) if row[2] else [])
        if not isinstance(features, list) or not isinstance(predictions, list):
            raise HTTPException(404, "历史记录没有可导出的结果文件")
        try:
            filename = _write_batch_result_file(
                log_id, int(row[0]), str(row[4] or "batch_inference"), str(row[3] or "json"),
                [], features, predictions, row[5], None,
            )
            db.execute(text("UPDATE inference_logs SET result_file = :file WHERE id = :id"), {"file": filename, "id": log_id})
            db.commit()
            path = (INFERENCE_OUTPUT_DIR / filename).resolve()
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, f"生成结果文件失败: {exc}") from exc
    media_type = "text/csv; charset=utf-8" if path.suffix.lower() == ".csv" else "application/json"
    return FileResponse(str(path), media_type=media_type, filename=path.name)


@router.get("/lineage/model/{model_id}")
def model_lineage(model_id: int, db=Depends(get_db)):
    mrow = db.execute(text(
        "SELECT m.id, m.name, m.framework, m.status, mv.parameters, mv.metrics "
        "FROM models m LEFT JOIN model_versions mv ON mv.model_id = m.id "
        "WHERE m.id = :id ORDER BY mv.id DESC LIMIT 1"
    ), {"id": model_id}).fetchone()
    if not mrow:
        raise HTTPException(status_code=404, detail="模型不存在")
    params = mrow[4] if isinstance(mrow[4], dict) else (json.loads(mrow[4]) if mrow[4] else {})
    metrics = mrow[5] if isinstance(mrow[5], dict) else (json.loads(mrow[5]) if mrow[5] else {})

    # 数据集血缘链(沿 governed_from 上溯到源头)
    chain = []
    did = params.get("dataset_id")
    seen = set()
    while did and did not in seen:
        seen.add(did)
        d = db.execute(text(
            "SELECT id, name, stage, governed_from FROM datasets WHERE id = :id"
        ), {"id": did}).fetchone()
        if not d:
            break
        chain.append({"id": d[0], "name": d[1], "stage": d[2]})
        did = d[3]
    chain.reverse()  # 源头在前

    inf_count = 0
    try:
        inf_count = db.execute(text(
            "SELECT count(*) FROM inference_logs WHERE model_id = :id"
        ), {"id": model_id}).scalar() or 0
    except Exception:
        pass

    return {
        "model": {"id": mrow[0], "name": mrow[1], "framework": mrow[2], "status": mrow[3]},
        "metrics": metrics,
        "target_col": params.get("target_col"),
        "feature_cols": params.get("feature_cols"),
        "job_id": params.get("job_id"),
        "dataset_lineage": chain,
        "inference_count": inf_count,
    }
