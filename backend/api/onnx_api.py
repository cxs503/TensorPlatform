"""
ONNX 模型导出与推理加速 API

端点:
- POST /onnx/export       - 导出训练好的模型为 ONNX 格式
- GET  /onnx/models       - 列出所有已导出的 ONNX 模型
- POST /onnx/predict      - 使用 ONNX 模型进行推理
- POST /onnx/benchmark    - 对比 sklearn 与 ONNX 推理速度
"""

import json
import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.config import get_db, SessionLocal
from sqlalchemy import text
from services.onnx_exporter import (
    SklearnModelExporter,
    PyTorchModelExporter,
    ONNXInferenceService,
    get_onnx_inference_service,
    ONNX_MODEL_DIR,
    ONNXRUNTIME_AVAILABLE,
    ONNX_AVAILABLE,
    SKL2ONNX_AVAILABLE,
    ONNXMLTOOLS_AVAILABLE,
    TORCH_AVAILABLE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onnx", tags=["ONNX导出与推理加速"])

# 推理服务单例
inference_service = get_onnx_inference_service()


def _onnx_metadata(model: Any) -> Dict[str, str]:
    return {item.key: item.value for item in model.metadata_props}


def _embed_standard_scaler(onnx_path: Path, scaler: Any) -> bool:
    """Prepend a fitted sklearn StandardScaler to a PyTorch ONNX graph.

    PyTorch training scales features outside the network.  Exporting only the
    network produces a structurally valid but semantically wrong ONNX model.
    The marker makes this migration idempotent for previously exported files.
    """
    if scaler is None or not hasattr(scaler, "mean_") or not hasattr(scaler, "scale_"):
        return False
    import onnx
    from onnx import helper, numpy_helper

    model = onnx.load(str(onnx_path))
    if _onnx_metadata(model).get("foundation.preprocessing") == "standard_scaler":
        return False
    if len(model.graph.input) != 1:
        raise ValueError("标准化封装目前只支持单输入ONNX模型")

    input_name = model.graph.input[0].name
    scaled_name = f"{input_name}__foundation_scaled"
    centered_name = f"{input_name}__foundation_centered"
    mean_name = "foundation_scaler_mean"
    scale_name = "foundation_scaler_scale"
    mean = np.asarray(scaler.mean_, dtype=np.float32).reshape(1, -1)
    scale = np.asarray(scaler.scale_, dtype=np.float32).reshape(1, -1)
    scale = np.where(scale == 0, 1.0, scale).astype(np.float32)

    original_nodes = list(model.graph.node)
    for node in original_nodes:
        for index, value in enumerate(node.input):
            if value == input_name:
                node.input[index] = scaled_name
    del model.graph.node[:]
    model.graph.node.extend([
        helper.make_node("Sub", [input_name, mean_name], [centered_name], name="foundationStandardScalerSub"),
        helper.make_node("Div", [centered_name, scale_name], [scaled_name], name="foundationStandardScalerDiv"),
        *original_nodes,
    ])
    model.graph.initializer.extend([
        numpy_helper.from_array(mean, name=mean_name),
        numpy_helper.from_array(scale, name=scale_name),
    ])
    marker = model.metadata_props.add()
    marker.key = "foundation.preprocessing"
    marker.value = "standard_scaler"
    onnx.checker.check_model(model)
    temporary = onnx_path.with_suffix(".tmp.onnx")
    onnx.save_model(model, str(temporary))
    temporary.replace(onnx_path)
    return True


def _validate_onnx_equivalence(job_id: str, onnx_path: Path) -> Dict[str, Any]:
    """Compare the exported graph with the persisted predictor on fixed probes."""
    pkl_path = ONNX_MODEL_DIR / f"{job_id}_model.pkl"
    if not pkl_path.exists():
        return {"equivalence_verified": False, "equivalence_reason": "没有可对照的PKL产物"}
    model = joblib.load(str(pkl_path))
    scaler = getattr(model, "scaler", None)
    _embed_standard_scaler(onnx_path, scaler)

    n_features = getattr(model, "n_features_in_", None)
    if not n_features and scaler is not None:
        n_features = getattr(scaler, "n_features_in_", None)
    if not n_features:
        try:
            import onnxruntime as ort
            inferred = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"]).get_inputs()[0].shape[-1]
            n_features = int(inferred)
        except Exception as exc:
            raise ValueError(f"无法确定ONNX输入维度: {exc}") from exc

    probes = np.vstack([
        np.zeros((1, int(n_features)), dtype=np.float32),
        np.ones((1, int(n_features)), dtype=np.float32),
    ])
    expected = np.asarray(model.predict(probes)).reshape(-1)
    import onnxruntime as ort
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    actual = np.asarray(session.run([session.get_outputs()[0].name], {input_name: probes})[0]).reshape(-1)
    if expected.shape != actual.shape:
        raise ValueError(f"预测形状不一致: 原模型{expected.shape}, ONNX{actual.shape}")
    if np.issubdtype(expected.dtype, np.number) and np.issubdtype(actual.dtype, np.number):
        max_error = float(np.max(np.abs(expected.astype(float) - actual.astype(float)))) if expected.size else 0.0
        if not np.allclose(expected.astype(float), actual.astype(float), rtol=1e-3, atol=1e-3):
            raise ValueError(f"预测结果不一致，最大绝对误差 {max_error:.6g}")
    else:
        max_error = 0.0
        if not np.array_equal(expected, actual):
            raise ValueError("分类预测结果不一致")
    return {"equivalence_verified": True, "max_abs_error": max_error}


def validate_native_onnx(onnx_path: Path) -> Dict[str, Any]:
    """Validate an uploaded native ONNX artifact without converting it again."""
    import onnx
    import onnxruntime as ort

    onnx.checker.check_model(onnx.load(str(onnx_path)))
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    if len(inputs) != 1:
        raise ValueError(f"当前打包服务只支持单输入ONNX模型，检测到 {len(inputs)} 个输入")
    return {
        "equivalence_verified": False,
        "equivalence_reason": "原生ONNX没有平台PKL对照产物",
        "input_name": inputs[0].name,
        "input_shape": inputs[0].shape,
    }


# ============================================================
# 请求/响应模型
# ============================================================

class ExportRequest(BaseModel):
    """ONNX 导出请求"""
    job_id: str = ""                               # 训练任务ID，如 train_abc123
    model_id: int = 0                              # 模型ID（从DB加载model_blob）
    format: str = "onnx"                          # 导出格式
    opset_version: int = 14                       # ONNX opset 版本


class PredictRequest(BaseModel):
    """ONNX 推理请求"""
    model_name: str                               # 模型名称，如 train_abc123
    X: List[List[float]]                          # 输入特征数据


class BenchmarkRequest(BaseModel):
    """性能对比请求"""
    job_id: str                                   # 训练任务ID
    X: List[List[float]]                          # 用户提供的二维测试样本
    n_runs: int = Field(default=100, ge=1, le=10000)


# ============================================================
# API 端点
# ============================================================

@router.post("/export")
def export_to_onnx(req: ExportRequest):
    """
    将训练好的模型导出为 ONNX 格式。

    从 /app/uploads/models/ 加载训练好的 .pkl 或 .pt 模型，
    将其转换为 ONNX 格式并保存。

    请求体:
      {job_id: "train_abc123", format: "onnx"}

    返回:
      {status: "success", onnx_path: "/app/uploads/models/train_abc123.onnx", ...}
    """
    if not ONNX_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="ONNX 支持未启用，请安装 onnx 和 onnxruntime 包",
        )

    job_id = req.job_id
    model_id = req.model_id
    resolved_model_name = None
    model_dir = ONNX_MODEL_DIR

    # 如果传了model_id，优先解析其注册版本对应的真实训练job。
    # 部署管理和ONNX中心必须使用同一个job_id命名约定。
    if model_id > 0 and not job_id:
        from database.config import SessionLocal
        import io as _io
        _db = SessionLocal()
        try:
            _row = _db.execute(text(
                "SELECT m.model_blob, m.name, mv.file_path, mv.parameters "
                "FROM models m LEFT JOIN model_versions mv ON mv.model_id=m.id "
                "WHERE m.id=:id ORDER BY mv.created_at DESC, mv.id DESC LIMIT 1"
            ), {"id": model_id}).fetchone()
            _params = (_row[3] if _row and isinstance(_row[3], dict) else {}) or {}
            resolved_model_name = _row[1] if _row else None
            job_id = _params.get("job_id")
            if not job_id and _row and _row[2]:
                _stem = Path(_row[2]).stem
                job_id = _stem[:-6] if _stem.endswith("_model") else None
            if job_id:
                logger.info(f"model_id={model_id} 解析到训练任务 {job_id}")
            elif _row and _row[0]:
                _blob = bytes(_row[0])
                _model = joblib.load(_io.BytesIO(_blob))
                job_id = f"model_{model_id}"
                _tmp_path = model_dir / f"{job_id}_model.pkl"
                joblib.dump(_model, str(_tmp_path))
                logger.info(f"从 model_blob 恢复模型: {_tmp_path}")
            else:
                # model_blob 不存在,尝试用 model_id 做 job_id 直接从磁盘查找
                job_id = f"model_{model_id}"
                logger.info(f"model_id={model_id} 无 model_blob,尝试磁盘查找 job_id={job_id}")
        finally:
            _db.close()

    if not job_id:
        # 如果提供了 model_id 但无法找到文件,给出清晰提示
        if model_id > 0:
            raise HTTPException(
                status_code=404,
                detail=f"模型 id={model_id} 没有可用的权重文件(model_blob为空且磁盘无对应pkl/pt文件)"
            )
        raise HTTPException(status_code=400, detail="请提供 job_id 或 model_id")

    # 检查是否已有 ONNX 文件
    existing_onnx = model_dir / f"{job_id}.onnx"
    if existing_onnx.exists():
        logger.info(f"ONNX 模型已存在，执行等价性复核: {existing_onnx}")
        try:
            verification = _validate_onnx_equivalence(job_id, existing_onnx)
            return {
                "status": "success",
                "message": "ONNX 模型已存在并通过复核",
                "job_id": job_id,
                "onnx_path": str(existing_onnx),
                "onnx_size_bytes": existing_onnx.stat().st_size,
                "model_id": model_id or None,
                "model_name": resolved_model_name,
                "display_name": (
                    f"{resolved_model_name} (#{model_id})"
                    if resolved_model_name and model_id else job_id
                ),
                **verification,
            }
        except Exception as exc:
            logger.warning("已有ONNX产物复核失败，将重新转换: %s", exc)
            existing_onnx.unlink(missing_ok=True)

    pkl_path = model_dir / f"{job_id}_model.pkl"
    pt_path = model_dir / f"{job_id}_model.pt"
    config_path = model_dir / f"{job_id}_config.json"

    onnx_path = None
    model_type = "unknown"

    # === 路径1: PyTorch模型(.pt)。MLP同时存在部署pkl时仍优先原生pt。===
    if pt_path.exists() and config_path.exists():
        if not TORCH_AVAILABLE:
            raise HTTPException(
                status_code=501,
                detail="PyTorch 模型需要 PyTorch 支持，当前环境未安装 PyTorch",
            )

        logger.info(f"导出 PyTorch 模型: {pt_path}")
        try:
            onnx_path = PyTorchModelExporter.export_from_job_id(job_id)
            model_type = "pytorch"
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"PyTorch 模型转换为 ONNX 失败: {e}",
            )

    # === 路径2: sklearn/LightGBM/XGBoost 模型 (.pkl) ===
    elif pkl_path.exists():
        logger.info(f"加载 sklearn 模型: {pkl_path}")
        try:
            model = joblib.load(str(pkl_path))
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"无法加载 sklearn 模型 {pkl_path}: {e}",
            )

        # 尝试确定 input_dim
        input_dim = _get_input_dim(model, job_id, config_path, model_dir)

        # 确定 output_dim
        output_dim = _get_output_dim(model, job_id)

        try:
            onnx_path = SklearnModelExporter.convert_sklearn_to_onnx(
                model=model,
                input_dim=input_dim,
                output_dim=output_dim,
                onnx_filename=job_id,
            )
            model_type = SklearnModelExporter._detect_model_type(model)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"sklearn 模型转换为 ONNX 失败: {e}",
            )

    else:
        raise HTTPException(
            status_code=404,
            detail=f"未找到训练模型文件。查找了: {pkl_path}, {pt_path}",
        )

    # 验证 ONNX 文件已生成
    final_path = Path(onnx_path) if onnx_path else None
    if not final_path or not final_path.exists():
        raise HTTPException(status_code=500, detail="ONNX 模型导出后文件不存在")

    try:
        verification = _validate_onnx_equivalence(job_id, final_path)
    except Exception as exc:
        final_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"ONNX预测等价性校验失败: {exc}") from exc

    return {
        "status": "success",
        "message": f"模型已成功导出为 ONNX 格式",
        "job_id": job_id,
        "model_type": model_type,
        "onnx_path": str(final_path),
        "onnx_size_bytes": final_path.stat().st_size,
        "onnx_size_mb": round(final_path.stat().st_size / (1024 * 1024), 2),
        "model_id": model_id or None,
        "model_name": resolved_model_name,
        "display_name": (
            f"{resolved_model_name} (#{model_id})"
            if resolved_model_name and model_id else job_id
        ),
        **verification,
    }


@router.get("/models")
def list_onnx_models(db: Session = Depends(get_db)):
    """
    列出所有已导出的 ONNX 模型。

    返回:
      {total: N, models: [{name, path, size_bytes, size_mb, created_at}]}
    """
    models = []
    model_dir = ONNX_MODEL_DIR

    if not model_dir.exists():
        return {"total": 0, "models": []}

    lookup: Dict[str, Dict[str, Any]] = {}
    rows = db.execute(text(
        "SELECT m.id, m.name, mv.version_number, mv.file_path, mv.parameters "
        "FROM models m JOIN model_versions mv ON mv.model_id=m.id "
        "ORDER BY mv.id DESC"
    )).fetchall()
    for model_id_value, model_name, version, file_path, parameters in rows:
        params = parameters if isinstance(parameters, dict) else (
            json.loads(parameters) if parameters else {}
        )
        info = {
            "model_id": model_id_value,
            "model_name": model_name,
            "display_name": f"{model_name} (#{model_id_value})",
            "model_version": version,
        }
        keys = {f"model_{model_id_value}"}
        job_value = params.get("job_id") or params.get("training_job_id")
        if job_value:
            keys.add(str(job_value))
        if file_path:
            stem = Path(file_path).stem
            keys.add(stem[:-6] if stem.endswith("_model") else stem)
        for key in keys:
            lookup.setdefault(key, info)

    for onnx_file in sorted(model_dir.glob("*.onnx"), key=lambda f: f.stat().st_mtime, reverse=True):
        stat = onnx_file.stat()
        # 移除 .onnx 后缀获取名称
        name = onnx_file.stem
        model_info = lookup.get(name, {})
        input_name = None
        input_shape = None
        try:
            import onnx
            graph_input = onnx.load(str(onnx_file), load_external_data=False).graph.input[0]
            input_name = graph_input.name
            input_shape = []
            for dim in graph_input.type.tensor_type.shape.dim:
                if dim.dim_value > 0:
                    input_shape.append(dim.dim_value)
                elif dim.dim_param:
                    input_shape.append(dim.dim_param)
                else:
                    input_shape.append(None)
        except Exception as exc:
            logger.warning("读取ONNX输入结构失败 %s: %s", onnx_file, exc)
        models.append({
            "name": name,
            "file_name": onnx_file.name,
            "path": str(onnx_file),
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created_at": stat.st_mtime,
            "input_name": input_name,
            "input_shape": input_shape,
            **model_info,
        })

    return {
        "total": len(models),
        "models": models,
    }


@router.get("/models/{model_name}/download")
def download_onnx_model(model_name: str):
    """下载已导出的 ONNX 模型文件(前端 OnnxExport 用 row.name 调本端点)"""
    from fastapi.responses import FileResponse
    safe = model_name.replace("..", "").replace("/", "").replace("\\", "")
    path = ONNX_MODEL_DIR / f"{safe}.onnx"
    if not path.exists():
        raise HTTPException(404, f"ONNX 模型不存在: {safe}.onnx")
    return FileResponse(str(path), media_type="application/octet-stream", filename=f"{safe}.onnx")


@router.post("/predict")
def predict_with_onnx(req: PredictRequest):
    """
    使用 ONNX 模型进行推理。

    请求体:
      {model_name: "train_abc123", X: [[1.0, 2.0, 3.0]]}

    返回:
      {model_name: "train_abc123", predictions: [...]}
    """
    if not ONNXRUNTIME_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="onnxruntime 未安装，无法执行推理",
        )

    model_name = req.model_name
    onnx_path = ONNX_MODEL_DIR / f"{model_name}.onnx"

    if not onnx_path.exists():
        # 也尝试不带 .onnx 后缀
        alt_path = ONNX_MODEL_DIR / f"{model_name}"
        if alt_path.exists():
            onnx_path = alt_path
        else:
            raise HTTPException(
                status_code=404,
                detail=f"ONNX 模型不存在: {onnx_path}。请先通过 POST /onnx/export 导出模型。",
            )

    try:
        # 加载模型
        session = inference_service.load_model(str(onnx_path), model_name)

        # 准备输入
        X = np.array(req.X, dtype=np.float32)

        # 推理
        predictions = inference_service.predict(session, X)

        return {
            "model_name": model_name,
            "onnx_path": str(onnx_path),
            "n_samples": len(req.X),
            "predictions": predictions.tolist() if predictions.ndim > 0 else [float(predictions)],
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"ONNX 推理失败: {e}",
        )


@router.post("/benchmark")
def benchmark_onnx_vs_sklearn(req: BenchmarkRequest):
    """
    对比 sklearn 原始模型与 ONNX 模型的推理速度。

    请求体:
      {job_id: "train_abc123", X: [[...]], n_runs: 100}

    返回:
      {sklearn_time_ms, onnx_time_ms, speedup, ...}
    """
    if not ONNXRUNTIME_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="onnxruntime 未安装，无法执行性能对比",
        )

    job_id = req.job_id
    pkl_path = ONNX_MODEL_DIR / f"{job_id}_model.pkl"
    onnx_path = ONNX_MODEL_DIR / f"{job_id}.onnx"

    if not onnx_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"ONNX 模型不存在: {onnx_path}。请先通过 POST /onnx/export 导出模型。",
        )

    if not req.X or any(not row for row in req.X):
        raise HTTPException(status_code=400, detail="请至少提供一行非空的测试数据")
    row_lengths = {len(row) for row in req.X}
    if len(row_lengths) != 1:
        raise HTTPException(status_code=400, detail="每一行测试数据的特征数量必须一致")
    X = np.asarray(req.X, dtype=np.float32)
    if X.ndim != 2 or not np.isfinite(X).all():
        raise HTTPException(status_code=400, detail="测试数据必须是由有限数值组成的二维数组")

    try:
        session = inference_service.load_model(str(onnx_path), job_id)
        inputs = session.get_inputs()
        if len(inputs) != 1:
            raise HTTPException(status_code=400, detail=f"当前测试仅支持单输入模型，检测到 {len(inputs)} 个输入")
        expected_features = inputs[0].shape[-1] if inputs[0].shape else None
        if isinstance(expected_features, int) and expected_features > 0 and X.shape[1] != expected_features:
            raise HTTPException(
                status_code=400,
                detail=f"测试数据有 {X.shape[1]} 个特征，但模型需要 {expected_features} 个",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法校验ONNX输入结构: {exc}") from exc

    try:
        if pkl_path.exists():
            result = inference_service.benchmark(
                sklearn_path=str(pkl_path),
                onnx_path=str(onnx_path),
                X=X,
                n_runs=req.n_runs,
            )
            result["mode"] = "comparison"
        else:
            result = inference_service.benchmark_onnx(
                onnx_path=str(onnx_path),
                X=X,
                n_runs=req.n_runs,
            )
        predictions = np.asarray(inference_service.predict(session, X))
        result["job_id"] = job_id
        result["input_samples"] = req.X
        result["output_name"] = session.get_outputs()[0].name
        result["predictions"] = (
            predictions.tolist()
            if predictions.ndim > 0
            else [predictions.item()]
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"性能对比失败: {e}",
        )


@router.get("/health")
def onnx_health_check():
    """ONNX 服务健康检查"""
    return {
        "status": "healthy" if (ONNX_AVAILABLE and ONNXRUNTIME_AVAILABLE) else "degraded",
        "service": "onnx",
        "onnx_available": ONNX_AVAILABLE,
        "onnxruntime_available": ONNXRUNTIME_AVAILABLE,
        "skl2onnx_available": SKL2ONNX_AVAILABLE,
        "onnxmltools_available": ONNXMLTOOLS_AVAILABLE,
        "torch_available": TORCH_AVAILABLE,
        "model_directory": str(ONNX_MODEL_DIR),
        "cached_sessions": len(inference_service._sessions),
    }


# ============================================================
# 辅助函数
# ============================================================

def _get_input_dim(model: Any, job_id: str, config_path: Path, model_dir: Path) -> int:
    """尝试从多种来源确定模型的输入维度"""
    # 方法1: 尝试从 sklearn 模型属性获取
    for attr in ["n_features_in_", "n_features_", "n_estimators"]:
        if hasattr(model, attr):
            val = getattr(model, attr)
            if attr == "n_features_in_" and val is not None:
                return int(val)

    # 方法2: 检查 RandomForest 的估计器
    if hasattr(model, "estimators_") and model.estimators_:
        first_tree = model.estimators_[0]
        if hasattr(first_tree, "tree_") and hasattr(first_tree.tree_, "n_features"):
            return int(first_tree.tree_.n_features)

    # 方法3: 从模型配置 JSON 获取
    if config_path.exists():
        with open(config_path) as f:
            pt_config = json.load(f)
        if "input_dim" in pt_config:
            return int(pt_config["input_dim"])

    # 方法4: 从运行记录 JSON 获取
    run_file = Path("/app/uploads/runs") / f"{job_id}.json"
    if run_file.exists():
        with open(run_file) as f:
            run_data = json.load(f)
        config = run_data.get("config", {})
        # 尝试从 metrics_history 推断
        history = run_data.get("metrics_history", [])
        if history:
            return 10  # 常见默认值

    # 默认值
    logger.warning(f"无法确定 input_dim，使用默认值 10")
    return 10


def _get_output_dim(model: Any, job_id: str) -> int:
    """尝试确定模型的输出维度"""
    # 方法1: 检查分类器的类别数
    if hasattr(model, "classes_"):
        classes = model.classes_
        return len(classes) if len(classes) > 2 else 1

    if hasattr(model, "n_classes_"):
        n = model.n_classes_
        return n if n > 2 else 1

    # 方法2: 检查回归器
    if hasattr(model, "n_outputs_"):
        return int(model.n_outputs_)

    # 默认回归输出
    return 1
