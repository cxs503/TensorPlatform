"""
模型评估 API — 真实推理版
加载真实模型 + 真实数据集 → 预测 → 计算指标
"""
import ast
import io
import json
import os
import warnings
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd
from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    mean_absolute_error, mean_squared_error, r2_score, confusion_matrix,
    roc_auc_score, roc_curve,
)
from sklearn.decomposition import PCA
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sqlalchemy import text

try:
    from joblib.numpy_pickle import NumpyUnpickler, _validate_fileobject_and_memmap
except ImportError:  # pragma: no cover - guarded for future joblib changes
    NumpyUnpickler = None
    _validate_fileobject_and_memmap = None

router = APIRouter(prefix="/model-evaluation", tags=["模型评估"])


class EvaluationRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_id: int
    dataset_id: int
    task_type: Optional[str] = None
    target_column: Optional[str] = None
    feature_columns: Optional[list] = None
    cross_validation: bool = False
    cv_folds: int = 5
    test_size: float = 0.2


_EVALUATION_TASK_ALIASES = {
    "regression": "regression",
    "classification": "classification",
    "time_series": "time_series_forecasting",
    "timeseries": "time_series_forecasting",
    "forecasting": "time_series_forecasting",
    "time_series_forecasting": "time_series_forecasting",
}


def _normalize_evaluation_task_type(value: str) -> str:
    normalized = _EVALUATION_TASK_ALIASES.get(str(value or "").strip().lower())
    if not normalized:
        raise HTTPException(
            status_code=400,
            detail="任务类型仅支持 regression、classification、time_series_forecasting",
        )
    return normalized


class ONNXPredictor:
    """Expose an ONNX Runtime session through the predict contract used by evaluation."""

    def __init__(self, model_path: str):
        try:
            from services.onnx_exporter import get_onnx_inference_service
            self.service = get_onnx_inference_service()
            self.session = self.service.load_model(model_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"ONNX模型加载失败: {exc}")
        shape = self.session.get_inputs()[0].shape
        self.n_features_in_ = int(shape[-1]) if shape and isinstance(shape[-1], int) else None

    def predict(self, X):
        return self.service.predict(self.session, np.asarray(X, dtype=np.float32))


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else None
        except (TypeError, ValueError):
            return None
    return None


@dataclass
class _EvaluationArtifact:
    predictor: Any
    training_config: dict
    prefers_dataframe: bool = False


class _SnapshotNumpyUnpickler(NumpyUnpickler if NumpyUnpickler else object):
    """Resolve classes pickled from a training script's temporary __main__."""

    def __init__(self, *args, snapshot_namespace=None, **kwargs):
        self.snapshot_namespace = snapshot_namespace or {}
        super().__init__(*args, **kwargs)

    def find_class(self, module, name):
        if module in {"__main__", "__mp_main__"} and name in self.snapshot_namespace:
            return self.snapshot_namespace[name]
        if module == "numpy._core" or module.startswith("numpy._core."):
            # NumPy 2.x writes numpy._core references while older runtimes expose
            # the same implementation through numpy.core.
            legacy_module = module.replace("numpy._core", "numpy.core", 1)
            return super().find_class(legacy_module, name)
        return super().find_class(module, name)


def _load_training_context(db, model_id: int) -> dict:
    """Return the latest training metadata needed to reproduce model inference."""
    row = db.execute(text("""
        SELECT hyperparameters
        FROM training_jobs
        WHERE registered_model_id = :model_id OR model_id = :model_id
        ORDER BY id DESC
        LIMIT 1
    """), {"model_id": model_id}).fetchone()
    context = _as_dict(row[0]) if row else {}

    version = db.execute(text("""
        SELECT parameters
        FROM model_versions
        WHERE model_id = :model_id
        ORDER BY id DESC
        LIMIT 1
    """), {"model_id": model_id}).fetchone()
    version_context = _as_dict(version[0]) if version else {}
    merged = {**version_context, **context}

    # A user-script training job may initially persist an empty feature list,
    # then determine the final feature subset while the source code runs. The
    # completed model version stores that subset. Do not let the earlier empty
    # value erase the deployable/evaluable model contract.
    for key in ("feature_columns", "feature_cols"):
        if _as_list(merged.get(key)):
            continue
        version_features = _as_list(version_context.get(key))
        if version_features:
            merged[key] = version_features
    return merged



def _configured_feature_columns(training_config: dict) -> Optional[list[str]]:
    """Return the persisted final model feature order, if the model has one."""
    for key in ("feature_columns", "feature_cols", "selected_features"):
        columns = _as_list(training_config.get(key))
        if columns:
            return [str(column) for column in columns if str(column).strip()]
    return None
def _snapshot_namespace(
    snapshot_path: Optional[str] = None,
    snapshot_source: Optional[str] = None,
) -> dict:
    """Restore custom predictor definitions without re-running training code.

    New user-script models retain their executed source in the model-version
    metadata. This is the source of truth when older immutable training
    snapshots contain only adapter metadata. Built-in and legacy models still
    fall back to their on-disk snapshot.
    """
    source = str(snapshot_source or "")
    filename = "<persisted-user-script-source>"
    if not source.strip():
        if not snapshot_path or not os.path.isfile(snapshot_path):
            raise FileNotFoundError("模型对应的训练代码快照不存在")
        with open(snapshot_path, "r", encoding="utf-8") as source_file:
            source = source_file.read()
        filename = snapshot_path

    parsed = ast.parse(source, filename=filename)
    namespace = {
        "__name__": f"foundation_evaluation_snapshot_{os.path.basename(filename).split('.')[0]}",
        "np": np,
        "pd": pd,
    }

    # Literal constants are safe to restore and may be referenced by predict methods.
    for node in parsed.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                namespace[target.id] = value

    # Training-only packages (for example optuna) must not be required merely
    # to call predict. Restore imports independently and ignore unavailable
    # optional imports; class construction below will still fail clearly when
    # an import is genuinely required as a base class or decorator.
    for node in parsed.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        import_module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        try:
            exec(compile(import_module, filename, "exec"), namespace, namespace)
        except ImportError:
            continue

    definitions = [
        node for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    module = ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[]))
    exec(compile(module, filename, "exec"), namespace, namespace)
    return namespace


def _joblib_load_with_snapshot(
    file_object,
    snapshot_path: Optional[str] = None,
    snapshot_source: Optional[str] = None,
):
    """Load a joblib artifact while redirecting __main__ classes to its snapshot."""
    if not NumpyUnpickler or not _validate_fileobject_and_memmap:
        raise RuntimeError("当前joblib版本不支持自定义训练模型恢复")
    namespace = _snapshot_namespace(snapshot_path, snapshot_source)
    filename = getattr(file_object, "name", "")
    with _validate_fileobject_and_memmap(file_object, filename, None) as (validated, _):
        if isinstance(validated, str):
            raise RuntimeError("暂不支持旧版多文件joblib模型")
        return _SnapshotNumpyUnpickler(
            filename,
            validated,
            ensure_native_byte_order=True,
            mmap_mode=None,
            snapshot_namespace=namespace,
        ).load()


def _load_serialized_model(
    source,
    snapshot_path: Optional[str] = None,
    snapshot_source: Optional[str] = None,
):
    """Use ordinary joblib first, then recover custom script classes if required."""
    try:
        return joblib.load(source), False
    except (AttributeError, ModuleNotFoundError, ImportError) as original_error:
        if not snapshot_path and not snapshot_source:
            raise original_error
        if hasattr(source, "seek"):
            source.seek(0)
            retry_source = source
        else:
            retry_source = open(source, "rb")
        try:
            return _joblib_load_with_snapshot(retry_source, snapshot_path, snapshot_source), True
        finally:
            if retry_source is not source:
                retry_source.close()


def _load_legacy_pytorch_model(db, model_id: int, model_path: str):
    """Rebuild legacy state_dict-only MLP artifacts as predict-compatible models."""
    from pathlib import Path

    try:
        import torch
        from services.real_training_service import (
            PyTorchPredictor, PyTorchTrainer, split_training_data,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PyTorch运行环境不可用: {exc}")

    path = Path(model_path)
    config_path = path.with_name(path.name.replace("_model.pt", "_config.json"))
    if not config_path.is_file():
        raise HTTPException(status_code=400, detail="历史PyTorch模型缺少网络结构配置，无法评估")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        network = PyTorchTrainer.create_model(
            int(config["input_dim"]), int(config.get("output_dim", 1)), config
        )
        state_dict = torch.load(str(path), map_location="cpu")
        network.load_state_dict(state_dict)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"历史PyTorch模型结构恢复失败: {exc}")

    version_row = db.execute(text("""
        SELECT parameters FROM model_versions
        WHERE model_id = :model_id ORDER BY id DESC LIMIT 1
    """), {"model_id": model_id}).fetchone()
    parameters = _as_dict(version_row[0]) if version_row else {}
    job_id = parameters.get("job_id")
    job_row = db.execute(text("""
        SELECT dataset_id, hyperparameters FROM training_jobs
        WHERE registered_model_id = :model_id OR model_id = :model_id
           OR (:job_id IS NOT NULL AND training_job_id = :job_id)
        ORDER BY CASE WHEN training_job_id = :job_id THEN 0 ELSE 1 END, id DESC
        LIMIT 1
    """), {"model_id": model_id, "job_id": job_id}).fetchone()
    job_config = _as_dict(job_row[1]) if job_row else {}
    merged = {**parameters, **job_config}
    feature_columns = _as_list(
        merged.get("feature_columns") or merged.get("feature_cols")
    )
    dataset_id = (job_row[0] if job_row else None) or merged.get("dataset_id")

    scaler = None
    if merged.get("normalize", True) and dataset_id and feature_columns:
        dataset_row = db.execute(
            text("SELECT file_path FROM datasets WHERE id = :dataset_id"),
            {"dataset_id": int(dataset_id)},
        ).fetchone()
        if dataset_row and dataset_row[0] and os.path.exists(dataset_row[0]):
            frame = pd.read_csv(dataset_row[0])
            missing = [column for column in feature_columns if column not in frame.columns]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"原训练数据缺少特征列: {', '.join(missing)}",
                )
            features = frame[feature_columns].apply(pd.to_numeric, errors="coerce")
            sentinels = [-999, -9999, 9999, -99999, 99999, -999.0, -9999.0, 9999.0]
            features = features.replace(sentinels, np.nan).dropna()
            if len(features) >= 5:
                values = features.to_numpy(dtype=float)
                dummy = np.zeros(len(values))
                train_values, _, _, _ = split_training_data(
                    values, dummy, merged.get("split_strategy", "random")
                )
                scaler = StandardScaler().fit(train_values)

    return PyTorchPredictor(network, scaler)


def _load_model(db, model_id: int):
    """从数据库加载模型"""
    training_config = _load_training_context(db, model_id)
    snapshot_path = training_config.get("code_snapshot_path")
    snapshot_source = training_config.get("script_source")

    # 优先从 model_blob
    row = db.execute(text("SELECT model_blob, model_type, framework FROM models WHERE id = :id"), {"id": model_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")

    if row[0]:
        buf = io.BytesIO(bytes(row[0]))
        try:
            predictor, restored_from_snapshot = _load_serialized_model(buf, snapshot_path, snapshot_source)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"模型制品加载失败: {exc}") from exc
        return _EvaluationArtifact(
            predictor=predictor,
            training_config=training_config,
            prefers_dataframe=restored_from_snapshot,
        )

    # 回退到 model_versions.file_path
    ver = db.execute(text(
        "SELECT file_path, parameters FROM model_versions "
        "WHERE model_id = :id ORDER BY id DESC LIMIT 1"
    ), {"id": model_id}).fetchone()
    if ver and ver[0] and os.path.exists(ver[0]):
        params = _as_dict(ver[1])
        if str(ver[0]).lower().endswith(".onnx") or params.get("artifact_format") == "onnx":
            return _EvaluationArtifact(ONNXPredictor(ver[0]), training_config)
        if str(ver[0]).lower().endswith(".pt"):
            return _EvaluationArtifact(
                _load_legacy_pytorch_model(db, model_id, ver[0]), training_config
            )
        try:
            predictor, restored_from_snapshot = _load_serialized_model(ver[0], snapshot_path, snapshot_source)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"模型制品加载失败: {exc}") from exc
        return _EvaluationArtifact(
            predictor=predictor,
            training_config=training_config,
            prefers_dataframe=restored_from_snapshot,
        )

    raise HTTPException(status_code=404, detail="模型文件不存在（无 blob 也无文件路径）")


def _load_dataset(db, dataset_id: int):
    """从数据库加载数据集"""
    row = db.execute(text("SELECT file_path, columns_info FROM datasets WHERE id = :id"), {"id": dataset_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="数据集不存在")

    file_path = row[0]
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"数据集文件不存在: {file_path}")

    df = pd.read_csv(file_path)
    # 哨兵值清洗(与训练 load_dataset_data 一致,避免评估/训练 R² 不一致)
    _SENTINELS = [-999, -9999, 9999, -99999, 99999, -999.0, -9999.0, 9999.0]
    for _col in df.select_dtypes(include=[np.number]).columns:
        df[_col] = df[_col].replace(_SENTINELS, np.nan)
    return df, row[1]


def _preprocess(
    df, target_column, feature_columns, columns_info, task_type,
    include_frame: bool = False,
):
    """预处理：分离特征和目标，处理时间列"""
    # 自动检测时间列
    time_cols = [c for c in df.columns if 'time' in c.lower() or '时间' in c.lower() or 'date' in c.lower() or '日期' in c.lower()]
    for tc in time_cols:
        try:
            df[tc] = pd.to_datetime(df[tc])
        except Exception:
            pass

    # 时序评估必须保持真实时间先后关系；完整测试集仍然100%参与推理，
    # 这里只排序，不做训练/验证切分。
    if task_type == "time_series_forecasting" and time_cols:
        sortable = next(
            (column for column in time_cols if pd.api.types.is_datetime64_any_dtype(df[column])),
            None,
        )
        if sortable:
            df = df.sort_values(sortable, kind="mergesort", na_position="last").reset_index(drop=True)

    # 如果没指定 target_column，用最后一列
    if not target_column:
        target_column = df.columns[-1]

    if target_column not in df.columns:
        raise HTTPException(status_code=400, detail=f"目标列 '{target_column}' 不在数据集中")

    # 如果没指定 feature_columns，用除时间列和目标列外的数值列
    if not feature_columns:
        skip = set(time_cols) | {target_column}
        feature_columns = [c for c in df.columns if c not in skip and pd.api.types.is_numeric_dtype(df[c])]

    if not feature_columns:
        raise HTTPException(status_code=400, detail="未找到有效的特征列")

    X = df[feature_columns].copy()
    y = df[target_column].copy()

    # 处理缺失值
    X = X.fillna(X.mean())
    y = y.dropna()
    X = X.loc[y.index]
    evaluation_frame = df.loc[y.index].copy()

    # 分类任务编码标签
    if task_type == "classification":
        le = LabelEncoder()
        y = le.fit_transform(y.astype(str))

    result = (X.values, y.values, feature_columns)
    return (*result, evaluation_frame) if include_frame else result


def _dataframe_model_input(frame: pd.DataFrame, training_config: dict) -> pd.DataFrame:
    """Restore common aliases expected by source-code predictors."""
    model_input = frame.copy()
    contract = _as_dict(training_config.get("source_data_contract"))
    source_time_column = (
        training_config.get("source_time_column")
        or contract.get("time_column")
    )
    if source_time_column in model_input.columns and "时间" not in model_input.columns:
        # Source scripts commonly rename their selected platform time column to 时间
        # before fitting a bundled feature engineer. Keep the original column too.
        model_input["时间"] = model_input[source_time_column]
    return model_input


def _extract_prediction_values(prediction, target_column: Optional[str]) -> np.ndarray:
    """Normalize scalar, Series, array and multi-column predictor outputs."""
    if isinstance(prediction, tuple) and prediction:
        prediction = prediction[0]

    if isinstance(prediction, dict):
        preferred_keys = [
            target_column,
            "prediction",
            "predictions",
            "predicted",
            "y_pred",
            "预测值",
            "预测结果",
            "最终预测",
        ]
        selected = next((prediction[key] for key in preferred_keys if key and key in prediction), None)
        if selected is None:
            final_key = next((key for key in prediction if "最终预测" in str(key)), None)
            if final_key is not None:
                selected = prediction[final_key]
        if selected is None:
            raise ValueError("模型返回多个结果，但没有可识别的最终预测字段")
        prediction = selected

    if isinstance(prediction, pd.DataFrame):
        columns = list(prediction.columns)
        selected_column = target_column if target_column in columns else None
        if selected_column is None:
            preferred_tokens = ("最终预测", "预测结果", "预测值", "prediction", "predicted", "y_pred")
            selected_column = next(
                (column for token in preferred_tokens for column in columns
                 if token.lower() in str(column).lower()),
                None,
            )
        if selected_column is None:
            numeric_columns = list(prediction.select_dtypes(include=[np.number]).columns)
            if len(numeric_columns) == 1:
                selected_column = numeric_columns[0]
        if selected_column is None:
            raise ValueError(
                "模型返回多列结果，但无法确定最终预测列；请让predict返回单列或使用“最终预测”命名"
            )
        prediction = prediction[selected_column]

    values = np.asarray(prediction)
    if values.ndim == 0:
        values = values.reshape(1)
    elif values.ndim == 2 and values.shape[1] == 1:
        values = values[:, 0]
    elif values.ndim > 1:
        raise ValueError(f"模型返回了{values.shape[1]}列预测结果，评估任务只接受一列")
    return values.reshape(-1)


def _predict_for_evaluation(
    artifact: _EvaluationArtifact,
    numeric_features: np.ndarray,
    evaluation_frame: pd.DataFrame,
    target_column: Optional[str],
) -> np.ndarray:
    """Call ordinary estimators and source-code predictors through one contract."""
    dataframe_input = _dataframe_model_input(evaluation_frame, artifact.training_config)
    candidates = (
        [("原始字段表格", dataframe_input), ("数值特征矩阵", numeric_features)]
        if artifact.prefers_dataframe
        else [("数值特征矩阵", numeric_features), ("原始字段表格", dataframe_input)]
    )
    errors = []
    for input_name, model_input in candidates:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=pd.errors.PerformanceWarning)
                warnings.filterwarnings(
                    "ignore",
                    message="X does not have valid feature names.*",
                    category=UserWarning,
                )
                prediction = artifact.predictor.predict(model_input)
            return _extract_prediction_values(prediction, target_column)
        except Exception as exc:
            errors.append(f"{input_name}: {exc}")
    raise HTTPException(
        status_code=400,
        detail="模型预测失败；" + "；".join(errors),
    )


def _finite(value, digits=6):
    """Convert numpy values to compact JSON-safe numbers."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if np.isfinite(number) else None


def _sample_indices(length: int, limit: int):
    if length <= limit:
        return np.arange(length, dtype=int)
    return np.linspace(0, length - 1, limit, dtype=int)


def _histogram(values, bins=20, value_range=None):
    array = np.asarray(values, dtype=float).reshape(-1)
    array = array[np.isfinite(array)]
    if not len(array):
        return {"labels": [], "counts": []}
    if value_range is None:
        low, high = float(array.min()), float(array.max())
    else:
        low, high = value_range
    if low == high:
        low, high = low - 0.5, high + 0.5
    bin_count = min(bins, max(5, int(np.sqrt(len(array)))))
    counts, edges = np.histogram(array, bins=bin_count, range=(low, high))
    labels = [f"{(edges[i] + edges[i + 1]) / 2:.4g}" for i in range(len(counts))]
    return {"labels": labels, "counts": counts.astype(int).tolist()}


def _build_visualization(X, y_true, y_pred, feature_names, task_type, model):
    """Build real, sampled chart data without changing the evaluation result."""
    X = np.asarray(X, dtype=float)
    y_true = np.asarray(y_true).reshape(-1)
    y_pred = np.asarray(y_pred).reshape(-1)
    usable = min(len(X), len(y_true), len(y_pred))
    X, y_true, y_pred = X[:usable], y_true[:usable], y_pred[:usable]
    # 指标仍使用完整测试集；PCA、相关性和分布图只需要有代表性的样本。
    # 对全量 X 再做 StandardScaler/PCA 会同时复制多份矩阵，大数据集下会造成
    # 明显内存峰值。等距抽样保持首尾覆盖且结果可复现。
    analysis_indices = _sample_indices(usable, 5000)
    X_analysis = X[analysis_indices]
    y_analysis = y_true[analysis_indices]
    y_pred_analysis = y_pred[analysis_indices]
    payload = {
        "schema_version": 1,
        "sample_count": int(usable),
        "feature_columns": list(feature_names),
        "task_type": task_type,
    }

    chart_features = list(feature_names[:12])
    X_chart = X_analysis[:, :len(chart_features)]
    if len(chart_features):
        frame = pd.DataFrame(X_chart, columns=chart_features)
        corr = frame.corr().fillna(0.0).to_numpy()
        payload["correlation"] = {
            "features": chart_features,
            "values": [
                [col, row, _finite(corr[row, col])]
                for row in range(len(chart_features))
                for col in range(len(chart_features))
            ],
        }
        boxes = []
        for name in chart_features:
            values = frame[name].to_numpy(dtype=float)
            values = values[np.isfinite(values)]
            boxes.append({
                "name": name,
                "values": [_finite(v) for v in np.percentile(values, [0, 25, 50, 75, 100])] if len(values) else [],
                "mean": _finite(np.mean(values)) if len(values) else None,
            })
        payload["feature_distribution"] = boxes

    if X_analysis.shape[1] >= 2 and len(X_analysis) >= 2:
        try:
            scaled = StandardScaler().fit_transform(X_analysis)
            pca = PCA(n_components=2).fit(scaled)
            coords = pca.transform(scaled)
            indices = _sample_indices(len(X_analysis), 500)
            payload["pca"] = {
                "explained_variance": [_finite(v) for v in pca.explained_variance_ratio_],
                "points": [
                    [_finite(coords[i, 0]), _finite(coords[i, 1]), str(y_analysis[i])]
                    for i in indices
                ],
            }
        except Exception:
            pass

    if task_type == "classification":
        actual_labels = np.asarray([str(v) for v in y_true])
        predicted_labels = np.asarray([str(v) for v in y_pred])
        labels = sorted(set(actual_labels.tolist()) | set(predicted_labels.tolist()))
        matrix = confusion_matrix(actual_labels, predicted_labels, labels=labels)
        payload["confusion_matrix"] = {
            "labels": labels,
            "values": matrix.astype(int).tolist(),
        }
        payload["class_distribution"] = {
            "labels": labels,
            "actual": [int(np.sum(actual_labels == label)) for label in labels],
            "predicted": [int(np.sum(predicted_labels == label)) for label in labels],
        }
        if len(labels) == 2 and hasattr(model, "predict_proba"):
            try:
                probabilities = np.asarray(model.predict_proba(X_analysis))
                if probabilities.ndim == 2 and probabilities.shape[1] >= 2:
                    positive = probabilities[:, 1]
                    sampled_labels = np.asarray([str(v) for v in y_analysis])
                    binary_true = (sampled_labels == labels[1]).astype(int)
                    fpr, tpr, _ = roc_curve(binary_true, positive)
                    roc_indices = _sample_indices(len(fpr), 300)
                    payload["roc"] = {
                        "auc": _finite(roc_auc_score(binary_true, positive)),
                        "points": [[_finite(fpr[i]), _finite(tpr[i])] for i in roc_indices],
                        "positive_label": labels[1],
                    }
            except Exception:
                pass
    else:
        actual = np.asarray(y_true, dtype=float)
        predicted = np.asarray(y_pred, dtype=float)
        actual_analysis = np.asarray(y_analysis, dtype=float)
        predicted_analysis = np.asarray(y_pred_analysis, dtype=float)
        residual = actual_analysis - predicted_analysis
        trend_indices = _sample_indices(usable, 300)
        scatter_indices = _sample_indices(usable, 500)
        payload["prediction_trend"] = [
            [int(i + 1), _finite(actual[i]), _finite(predicted[i])] for i in trend_indices
        ]
        payload["actual_vs_predicted"] = [
            [_finite(actual[i]), _finite(predicted[i])] for i in scatter_indices
        ]
        payload["residual_histogram"] = _histogram(residual)
        finite_all = np.concatenate([
            actual_analysis[np.isfinite(actual_analysis)],
            predicted_analysis[np.isfinite(predicted_analysis)],
        ])
        shared_range = (float(finite_all.min()), float(finite_all.max())) if len(finite_all) else None
        payload["target_distribution"] = {
            "actual": _histogram(actual_analysis, value_range=shared_range),
            "predicted": _histogram(predicted_analysis, value_range=shared_range),
        }

    return payload


def _split_stored_metrics(value):
    stored = _as_dict(value)
    visualization = stored.pop("_visualization", None)
    evaluation_meta = stored.pop("_evaluation_meta", None) or {}
    return stored, visualization, evaluation_meta


def _metric_key(value: str) -> str:
    return str(value or "").strip().lower().replace("²", "2")


def _model_evaluation_profile(db, model_id: int, requested_task_type: str | None = None) -> dict:
    """Training-reported metrics define the primary evaluation cards."""
    row = db.execute(text("""
        SELECT tj.hyperparameters, tj.metrics, mv.metrics
        FROM models m
        LEFT JOIN LATERAL (
            SELECT hyperparameters, metrics FROM training_jobs
            WHERE COALESCE(registered_model_id, model_id) = m.id
            ORDER BY id DESC LIMIT 1
        ) tj ON TRUE
        LEFT JOIN LATERAL (
            SELECT metrics FROM model_versions WHERE model_id = m.id
            ORDER BY id DESC LIMIT 1
        ) mv ON TRUE
        WHERE m.id = :model_id
    """), {"model_id": model_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    config = _as_dict(row[0])
    training_context = _load_training_context(db, model_id)
    training_metrics = _as_dict(row[1]) or _as_dict(row[2])
    raw_task = requested_task_type or config.get("business_task_type") or config.get("task_type") or "regression"
    task_type = _normalize_evaluation_task_type(raw_task)
    available = {_metric_key(key) for key, value in training_metrics.items() if isinstance(value, (int, float))}
    aliases = {
        "mse": "MSE", "rmse": "RMSE", "mae": "MAE", "r2": "R2", "mape": "MAPE",
        "accuracy": "accuracy", "precision": "precision", "recall": "recall", "f1": "f1",
    }
    supported = (
        {"accuracy", "precision", "recall", "f1"}
        if task_type == "classification"
        else {"mse", "rmse", "mae", "r2", "mape"}
        if task_type == "time_series_forecasting"
        else {"mse", "rmse", "mae", "r2"}
    )
    primary = [aliases[key] for key in aliases if key in available and key in supported]
    if not primary:
        primary = (
            ["accuracy", "f1"] if task_type == "classification"
            else ["MAE", "RMSE", "MAPE"] if task_type == "time_series_forecasting"
            else ["MAE", "RMSE", "R2"]
        )
    return {
        "task_type": task_type,
        "target_column": str(training_context.get("target_column") or "").strip() or None,
        "primary_metrics": primary,
        "training_metric_keys": sorted(available),
        "source": "training_metrics" if available else "task_type_default",
    }


@router.get("/models/{model_id}/profile")
def get_model_evaluation_profile(model_id: int, db=Depends(get_db)):
    return _model_evaluation_profile(db, model_id)


@router.post("/evaluate")
def evaluate_model(request: EvaluationRequest, db=Depends(get_db)):
    """真实模型评估"""
    if request.cross_validation:
        raise HTTPException(
            status_code=400,
            detail="模型评估只对独立测试数据集执行全量推理，不支持在测试集上交叉验证或重新训练",
        )
    profile = _model_evaluation_profile(db, request.model_id, request.task_type)
    task_type = profile["task_type"]
    artifact = _load_model(db, request.model_id)
    model = artifact.predictor
    df, columns_info = _load_dataset(db, request.dataset_id)

    # Unless the user explicitly requests a different feature subset, replay
    # exactly the final feature contract saved with the trained model. This is
    # essential for source scripts that perform feature selection internally.
    target_column = (request.target_column or str(artifact.training_config.get("target_column") or "")).strip() or None
    if not target_column:
        raise HTTPException(
            status_code=400,
            detail="该模型没有保存训练目标列，请在评估页面选择预测目标列",
        )

    feature_columns = request.feature_columns or _configured_feature_columns(
        artifact.training_config
    )

    X, y, feature_names, evaluation_frame = _preprocess(
        df, target_column, feature_columns,
        columns_info, task_type, include_frame=True
    )

    if len(X) < 5:
        raise HTTPException(status_code=400, detail="数据量太少，至少需要5条")

    # 用户在模型评估中选择的是独立测试集，整份数据都应参与推理。
    # 不再沿用训练阶段的 80/20 切分，否则 80% 数据会被无意义地闲置。
    X_test, y_test = X, y

    # 使用已保存模型对完整测试集进行预测，不在这里重新训练模型。
    y_pred = _predict_for_evaluation(
        artifact, X_test, evaluation_frame, target_column
    )
    y_test = np.asarray(y_test).reshape(-1)
    if len(y_pred) != len(y_test):
        raise HTTPException(
            status_code=400,
            detail=f"模型返回 {len(y_pred)} 条预测，但测试集包含 {len(y_test)} 条数据",
        )

    # 计算指标
    if task_type == "classification":
        metrics = {
            "accuracy": float(accuracy_score(y_test, y_pred)),
            "precision": float(precision_score(y_test, y_pred, average="weighted", zero_division=0)),
            "recall": float(recall_score(y_test, y_pred, average="weighted", zero_division=0)),
            "f1": float(f1_score(y_test, y_pred, average="weighted", zero_division=0)),
        }
    else:
        mse = mean_squared_error(y_test, y_pred)
        metrics = {
            "MSE": float(mse),
            "RMSE": float(np.sqrt(mse)),
            "MAE": float(mean_absolute_error(y_test, y_pred)),
            "R2": float(r2_score(y_test, y_pred)),
        }
        if task_type == "time_series_forecasting":
            nonzero = np.abs(y_test) > 1e-12
            metrics["MAPE"] = (
                float(np.mean(np.abs((y_test[nonzero] - y_pred[nonzero]) / y_test[nonzero])) * 100)
                if np.any(nonzero) else 0.0
            )

    # 存库
    visualization = _build_visualization(
        X_test, y_test, y_pred, feature_names, task_type, model
    )
    stored = {**metrics, "_visualization": visualization, "_evaluation_meta": profile}

    result = db.execute(text("""
        INSERT INTO model_evaluation_results
        (model_id, dataset_id, task_type, metrics, cross_validation, cv_folds, created_at)
        VALUES (:mid, :did, :tt, :met, :cv, :cvf, NOW()) RETURNING id
    """), {
        "mid": request.model_id, "did": request.dataset_id,
        "tt": task_type, "met": json.dumps(stored),
        "cv": json.dumps(False),
        "cvf": None,
    })
    db.commit()
    eval_id = result.fetchone()[0]

    # 自动记录到监控系统
    try:
        _preds = y_pred.tolist() if hasattr(y_pred, 'tolist') else list(y_pred)
        _acts = y_test.tolist() if hasattr(y_test, 'tolist') else list(y_test)
        db.execute(text(
            "INSERT INTO monitoring_logs (model_id, dataset_id, predictions, actuals, metrics, sample_count, created_at) "
            "VALUES (:mid, :did, :pred, :act, :met, :sc, NOW())"
        ), {"mid": request.model_id, "did": request.dataset_id,
            "pred": json.dumps(_preds), "act": json.dumps(_acts),
            "met": json.dumps(metrics), "sc": len(y_test)})
        db.commit()
    except Exception:
        pass

    # ======== 自动灌入漂移检测预测(供 /drift/detect) ========
    # 关键:features 必须填入真实特征值(以特征名为键),drift service 才能对每列
    # 执行 KS/PSI,把"当前评估输入分布"vs"训练 baseline 分布"做比较。
    # 只填空 dict 会让特征漂移永远检不出来。
    _eval_pred_ms = None
    try:
        import time as _time
        _t0 = _time.time()
        from services.drift_detection_service import get_drift_detection_service
        _drift_svc = get_drift_detection_service()
        _mid_str = str(request.model_id)
        # X_test 行数与 y_pred 对齐;按行记录 {特征名: 值}
        for _i in range(min(len(y_pred), len(X_test))):
            _row = X_test[_i]
            _feat_dict = {}
            for _j, _fname in enumerate(feature_names):
                if _j < len(_row):
                    try:
                        _feat_dict[_fname] = float(_row[_j])
                    except (TypeError, ValueError):
                        pass
            _pv = y_pred[_i]
            if isinstance(_pv, (np.ndarray, list)):
                _pv = float(_pv[0]) if len(_pv) == 1 else float(_pv[0])
            else:
                _pv = float(_pv)
            _drift_svc.record_prediction(_mid_str, _pv, _feat_dict)
        _eval_pred_ms = round((_time.time() - _t0) * 1000 / max(len(y_pred), 1), 3)
        print(f"[drift] recorded {min(len(y_pred), len(X_test))} predictions for model {_mid_str}")
    except Exception as _e:
        print(f"[drift] record predictions failed: {_e}")

    # ======== 写入 inference_logs(供推理 Dashboard 真实 QPS/延迟) ========
    # 评估即批量推理,落一条 inference_logs 记录,Dashboard 可从 inference_time_ms
    # 计算真实延迟分布(不再是 drawCharts 里的写死假值)。
    try:
        _preds_list = y_pred.tolist() if hasattr(y_pred, 'tolist') else list(y_pred)
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS inference_logs ("
            "id SERIAL PRIMARY KEY, model_id INTEGER, features JSON, "
            "predictions JSON, inference_time_ms FLOAT, created_at TIMESTAMP DEFAULT NOW())"
        ))
        db.execute(text(
            "INSERT INTO inference_logs (model_id, features, predictions, inference_time_ms) "
            "VALUES (:mid, :f, :p, :ms)"
        ), {
            "mid": request.model_id,
            "f": json.dumps(feature_names, ensure_ascii=False),
            "p": json.dumps(_preds_list, ensure_ascii=False),
            "ms": _eval_pred_ms if _eval_pred_ms is not None else 0.0,
        })
        db.commit()
    except Exception as _ie:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[eval] inference_logs insert failed: {_ie}")

    return {
        "id": eval_id, "model_id": request.model_id, "dataset_id": request.dataset_id,
        "task_type": task_type, "metrics": metrics,
        "primary_metrics": profile["primary_metrics"], "evaluation_profile": profile,
        "visualization": visualization,
        "feature_columns": feature_names,
        "cross_validation": False, "cv_metrics": None,
        "test_size": 1.0, "test_samples": len(y_test),
        "dataset_samples": len(X), "evaluation_scope": "full_dataset",
        "created_at": datetime.now().isoformat(),
    }


@router.get("/results")
def list_evaluation_results(
    model_id: Optional[int] = None,
    dataset_id: Optional[int] = None,
    task_type: Optional[str] = None,
    limit: int = 20, offset: int = 0,
    db=Depends(get_db),
):
    query = "SELECT id, model_id, dataset_id, task_type, metrics, cross_validation, cv_folds, created_at, name FROM model_evaluation_results WHERE 1=1"
    params = {}
    if model_id is not None:
        query += " AND model_id = :mid"; params["mid"] = model_id
    if dataset_id is not None:
        query += " AND dataset_id = :did"; params["did"] = dataset_id
    if task_type:
        query += " AND task_type = :tt"; params["tt"] = task_type
    query += " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
    params["lim"] = limit; params["off"] = offset

    rows = db.execute(text(query), params).fetchall()
    cq = "SELECT COUNT(*) FROM model_evaluation_results WHERE 1=1"
    count_params = {}
    if model_id is not None: cq += " AND model_id = :mid"; count_params["mid"] = model_id
    if dataset_id is not None: cq += " AND dataset_id = :did"; count_params["did"] = dataset_id
    if task_type: cq += " AND task_type = :tt"; count_params["tt"] = task_type
    total = db.execute(text(cq), count_params).fetchone()[0]

    items = []
    for r in rows:
        metrics, visualization, evaluation_meta = _split_stored_metrics(r[4])
        items.append({
            "id": r[0], "model_id": r[1], "dataset_id": r[2], "task_type": r[3],
            "metrics": metrics, "has_visualization": bool(visualization),
            "primary_metrics": evaluation_meta.get("primary_metrics", []),
            "cross_validation": r[5], "cv_folds": r[6],
            "created_at": r[7].isoformat() if r[7] else None,
            "name": r[8],
        })
    return {"total": total, "items": items}


@router.get("/results/{evaluation_id}")
def get_evaluation_detail(evaluation_id: int, db=Depends(get_db)):
    r = db.execute(text("SELECT id, model_id, dataset_id, task_type, metrics, cross_validation, cv_folds, created_at, name FROM model_evaluation_results WHERE id = :id"), {"id": evaluation_id}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="评估结果不存在")
    mi = db.execute(text("SELECT id, name, model_type FROM models WHERE id = :id"), {"id": r[1]}).fetchone()
    di = db.execute(text("SELECT id, name FROM datasets WHERE id = :id"), {"id": r[2]}).fetchone()
    metrics, visualization, evaluation_meta = _split_stored_metrics(r[4])
    return {
        "id": r[0], "model_id": r[1], "model_name": mi[1] if mi else None, "model_type": mi[2] if mi else None,
        "dataset_id": r[2], "dataset_name": di[1] if di else None, "task_type": r[3],
        "metrics": metrics, "visualization": visualization,
        "primary_metrics": evaluation_meta.get("primary_metrics", []),
        "evaluation_profile": evaluation_meta,
        "cross_validation": r[5], "cv_folds": r[6],
        "created_at": r[7].isoformat() if r[7] else None,
        "name": r[8],
    }


@router.delete("/results/{evaluation_id}")
def delete_evaluation(evaluation_id: int, db=Depends(get_db)):
    r = db.execute(text("DELETE FROM model_evaluation_results WHERE id = :id RETURNING id"), {"id": evaluation_id}).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="评估结果不存在")
    db.commit()
    return {"message": "评估结果已删除", "id": evaluation_id}
