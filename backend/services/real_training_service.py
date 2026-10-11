"""
机器学习训练服务
支持PyTorch深度学习模型和sklearn传统ML模型的真实训练
集成MLflow实验追踪，自动记录参数/指标/模型
"""
import io
import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import joblib
import numpy as np
import sklearn
from sqlalchemy import text

# ML库 - 可选依赖
try:
    import lightgbm as lgb
    LIGHTGBM_AVAILABLE = True
except ImportError:
    lgb = None
    LIGHTGBM_AVAILABLE = False

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    xgb = None
    XGBOOST_AVAILABLE = False

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    nn = type('MockNN', (), {'Module': object, 'Linear': lambda *a,**k: None, 'ReLU': lambda: None})()
    optim = None
    DataLoader = None
    TensorDataset = None
    TORCH_AVAILABLE = False
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# MLflow集成
try:
    import mlflow
    from services.mlflow_tracker import get_mlflow_tracker
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False

# Kafka事件桥接
try:
    from services.kafka_bridge import send_training_event, send_model_event, is_kafka_available
    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    def send_training_event(*a, **kw): pass
    def send_model_event(*a, **kw): pass
    def is_kafka_available(): return False

# 数据库持久化
from database.config import SessionLocal

# 训练状态存储
_training_jobs: Dict[str, Dict] = {}
_model_registry: Dict[str, Any] = {}

# 模型保存目录
MODEL_DIR = Path("/app/uploads/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# 训练记录目录
RUNS_DIR = Path("/app/uploads/runs")
RUNS_DIR.mkdir(parents=True, exist_ok=True)

# Bounded legacy in-process training. Distributed training is scheduled
# elsewhere; HTTP/Celery callers of this service must not consume a worker
# without an explicit resource ceiling.
MAX_TRAINING_ROWS = 100_000
MAX_TRAINING_COLUMNS = 512
MAX_TRAINING_INPUT_BYTES = 64 * 1024 * 1024
MAX_TRAINING_CONFIG_BYTES = 128 * 1024
MAX_TRAINING_SECONDS = 300
MAX_USER_SCRIPT_TRAINING_SECONDS = int(
    os.getenv("USER_SCRIPT_TRAINING_TIMEOUT_SECONDS", "1800")
)
MAX_CONCURRENT_TRAININGS = 2
MAX_EPOCHS = 1_000
MAX_BATCH_SIZE = 4_096
MAX_ESTIMATORS = 1_000
MAX_DEPTH = 64
MAX_HIDDEN_LAYERS = 8
MAX_HIDDEN_UNITS = 2_048
_training_slots = threading.BoundedSemaphore(MAX_CONCURRENT_TRAININGS)
_RUNTIME_DATA_KEYS = {"X", "y", "X_train", "y_train", "X_val", "y_val", "data"}

_ALLOWED_CONFIG_KEYS = {
    "name", "task_type", "model_type", "model_id", "dataset_id", "target_col",
    "target_column", "feature_cols", "feature_columns", "feature_columns_explicit", "feature_names", "epochs",
    "batch_size", "learning_rate", "hidden_layers", "dropout", "n_estimators",
    "hyperparameter_source",
    "max_depth", "normalize", "batch_norm", "normalize_target",
    "source_execution_mode", "source_entry_function",
    "organization_id", "validation_context",
    "validation_run_id", "validation_report", "optimization_metric", "project_name",
    "skip_model_registration", "mlflow_run_id", "registered_model_id", "algorithm_id", "algorithm_name",
    "registered_model_version_id", "mlflow_name", "feature_baseline", "scenario",
    "training_adapter", "business_task_type", "split_strategy",
    "code_snapshot_path", "code_snapshot_sha256", "code_snapshot_filename",
    "algorithm_version", "resume_artifact_path", "resume_artifact_sha256",
    "resume_training_mode", "source_model_id",
    "effective_hyperparameters", "runtime_environment_id",
    "source_data_mode", "source_time_column", "source_data_contract",
    "model_folder_id",
}


def _bounded_json_size(value: Any, label: str, maximum: int) -> None:
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须为可序列化 JSON") from exc
    if size > maximum:
        raise ValueError(f"{label} 超过 {maximum} 字节上限")


def _persisted_training_config(config: Optional[Dict]) -> Dict:
    """Remove runtime matrices while preserving reproducibility metadata."""
    return {
        key: value
        for key, value in (config or {}).items()
        if key not in _RUNTIME_DATA_KEYS
    }


def validate_training_config(config: Dict) -> Dict:
    """Return a safe copy of a supported training configuration."""
    if not isinstance(config, dict):
        raise ValueError("训练配置必须是对象")
    _bounded_json_size(config, "训练配置", MAX_TRAINING_CONFIG_BYTES)
    unknown = set(config) - _ALLOWED_CONFIG_KEYS
    if unknown:
        raise ValueError(f"训练配置包含未允许参数: {', '.join(sorted(map(str, unknown)))}")
    safe = dict(config)
    model_type = str(safe.get("model_type", "lightgbm")).lower()
    if safe.get("source_execution_mode") != "execute_user_script" and model_type not in {"lightgbm", "xgboost", "random_forest", "mlp", "neural_network"}:
        raise ValueError("不支持的 model_type")
    task_type = str(safe.get("task_type", "classification")).lower()
    supported_task_types = {"classification", "regression"}
    if safe.get("source_execution_mode") == "execute_user_script":
        supported_task_types.add("time_series_forecasting")
    if task_type not in supported_task_types:
        raise ValueError("task_type 仅支持 classification/regression（上传时序源码可使用 time_series_forecasting）")
    # execute_user_script: 参数由用户脚本自己管（optuna 等），跳过所有超参校验
    if safe.get("source_execution_mode") == "execute_user_script":
        return safe
    integer_fields = (
        (("epochs", 10, MAX_EPOCHS), ("batch_size", 32, MAX_BATCH_SIZE))
        if model_type in {"mlp", "neural_network"}
        else (("n_estimators", 100, MAX_ESTIMATORS), ("max_depth", 6, MAX_DEPTH))
    )
    for key, default, maximum in integer_fields:
        value = safe.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{key} 必须为 1 到 {maximum} 的整数")
        safe[key] = value
    if model_type in {"mlp", "neural_network"}:
        invalid = {"n_estimators", "max_depth"}.intersection(config)
        if invalid:
            raise ValueError(f"MLP 不支持参数: {', '.join(sorted(invalid))}")
    else:
        invalid = {
            "epochs", "batch_size", "hidden_layers", "dropout",
            "batch_norm", "normalize_target",
        }.intersection(config)
        if invalid:
            raise ValueError(f"{model_type} 不支持参数: {', '.join(sorted(invalid))}")
    learning_rate = safe.get("learning_rate", 0.001)
    if isinstance(learning_rate, bool) or not isinstance(learning_rate, (int, float)) or not 0 < float(learning_rate) <= 1:
        raise ValueError("learning_rate 必须在 (0, 1] 之间")
    safe["learning_rate"] = float(learning_rate)
    if model_type in {"mlp", "neural_network"}:
        dropout = safe.get("dropout", 0.2)
        if isinstance(dropout, bool) or not isinstance(dropout, (int, float)) or not 0 <= float(dropout) < 1:
            raise ValueError("dropout 必须在 [0, 1) 之间")
        safe["dropout"] = float(dropout)
        hidden_layers = safe.get("hidden_layers", [128, 64])
        if (not isinstance(hidden_layers, list) or not 1 <= len(hidden_layers) <= MAX_HIDDEN_LAYERS
                or any(isinstance(unit, bool) or not isinstance(unit, int) or not 1 <= unit <= MAX_HIDDEN_UNITS for unit in hidden_layers)):
            raise ValueError("hidden_layers 超过受控网络结构上限")
        safe["hidden_layers"] = list(hidden_layers)
        for key in ("batch_norm", "normalize_target"):
            value = safe.get(key, False)
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
            safe[key] = value
    return safe


def validate_training_data(data: Dict) -> tuple[np.ndarray, np.ndarray]:
    """Validate shape, finite numeric features and bounded serialized size."""
    if not isinstance(data, dict) or set(data) != {"X", "y"}:
        raise ValueError("训练数据必须且只能包含 X 和 y")
    _bounded_json_size(data, "训练数据", MAX_TRAINING_INPUT_BYTES)
    X = np.asarray(data["X"], dtype=float)
    y = np.asarray(data["y"])
    if X.ndim != 2 or not 5 <= X.shape[0] <= MAX_TRAINING_ROWS or not 1 <= X.shape[1] <= MAX_TRAINING_COLUMNS:
        raise ValueError(f"训练数据形状必须在 5-{MAX_TRAINING_ROWS} 行和 1-{MAX_TRAINING_COLUMNS} 列之间")
    if y.ndim != 1 or len(y) != X.shape[0]:
        raise ValueError("y 必须是一维且与 X 行数一致")
    if not np.isfinite(X).all():
        raise ValueError("训练特征包含非有限数")
    return X, y


def split_training_data(X: np.ndarray, y: np.ndarray, strategy: str = "random"):
    """Split training data without leaking future rows into time-series training."""
    if strategy == "chronological":
        split_index = int(len(X) * 0.8)
        if split_index < 1 or split_index >= len(X):
            raise ValueError("时序训练数据不足以按时间顺序切分")
        return X[:split_index], X[split_index:], y[:split_index], y[split_index:]
    if strategy != "random":
        raise ValueError(f"不支持的数据切分策略: {strategy}")
    return train_test_split(X, y, test_size=0.2, random_state=42)


_LOWER_IS_BETTER_METRICS = {
    "loss", "val_loss", "rmse", "mae", "mape", "mse",
}


class PyTorchPredictor:
    """A joblib-serializable prediction wrapper for trained PyTorch MLPs.

    The raw ``state_dict`` is retained for framework-native use, while this
    wrapper keeps the fitted feature scaler and exposes the same ``predict``
    contract used by the deployment service.
    """

    def __init__(self, model, scaler=None, target_scaler=None):
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch is not available")
        self.model = model.cpu().eval()
        self.scaler = scaler
        self.target_scaler = target_scaler
        first_linear = next(
            (layer for layer in self.model.modules() if isinstance(layer, nn.Linear)),
            None,
        )
        if first_linear is None:
            raise ValueError("PyTorch MLP has no input layer")
        self.n_features_in_ = int(first_linear.in_features)

    def predict(self, X):
        values = np.asarray(X, dtype=np.float32)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        if values.ndim != 2 or values.shape[1] != self.n_features_in_:
            raise ValueError(
                f"Expected {self.n_features_in_} features, got shape {values.shape}"
            )
        if self.scaler is not None:
            values = self.scaler.transform(values).astype(np.float32)
        self.model.eval()
        with torch.no_grad():
            predictions = self.model(torch.as_tensor(values, dtype=torch.float32))
        result = predictions.detach().cpu().numpy().reshape(-1, 1)
        # Older persisted wrappers predate target scaling and therefore do not
        # have this attribute after unpickling. Keep those artifacts usable.
        target_scaler = getattr(self, "target_scaler", None)
        if target_scaler is not None:
            result = target_scaler.inverse_transform(result)
        return result.reshape(-1)


def select_best_metrics(metrics_history: List[Dict], config: Optional[Dict] = None) -> Dict:
    """Select the real best epoch for classification and regression histories."""
    if not metrics_history:
        return {}
    config = config or {}
    requested = config.get("optimization_metric")
    available = set().union(*(entry.keys() for entry in metrics_history))
    if requested not in available:
        if config.get("task_type") == "regression" and "r2" in available:
            requested = "r2"
        elif "accuracy" in available:
            requested = "accuracy"
        else:
            requested = next(
                (name for name in ("val_loss", "loss", "rmse", "mae") if name in available),
                None,
            )
    if not requested:
        return dict(metrics_history[-1])
    candidates = [entry for entry in metrics_history if isinstance(entry.get(requested), (int, float))]
    if not candidates:
        return dict(metrics_history[-1])
    chooser = min if requested in _LOWER_IS_BETTER_METRICS else max
    return dict(chooser(candidates, key=lambda entry: entry[requested]))


def _artifact_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_resume_artifact(config: Dict):
    path_value = config.get("resume_artifact_path")
    if not path_value:
        return None
    path = Path(str(path_value)).resolve()
    root = Path("/app/uploads/algorithms").resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("继续训练产物路径无效")
    raw = path.read_bytes()
    expected = config.get("resume_artifact_sha256")
    if not expected or _artifact_digest(raw) != expected:
        raise ValueError("继续训练产物完整性校验失败")
    return joblib.load(io.BytesIO(raw))


class TrainingJob:
    """训练任务"""

    def __init__(self, job_id: str, config: Dict):
        self.job_id = job_id
        self.config = config
        self.status = "pending"
        self.progress = 0.0
        self.current_epoch = 0
        self.total_epochs = config.get("epochs", 10)
        self.metrics_history: List[Dict] = []
        self.best_metrics: Dict = {}
        self.model_path: Optional[str] = None
        self.error: Optional[str] = None
        self.error_traceback: Optional[str] = None
        self.created_at: str = datetime.now().isoformat()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.deadline_monotonic: Optional[float] = None

    def ensure_runtime_budget(self) -> None:
        if self.deadline_monotonic is not None and time.monotonic() > self.deadline_monotonic:
            limit = (
                MAX_USER_SCRIPT_TRAINING_SECONDS
                if self.config.get("source_execution_mode") == "execute_user_script"
                else MAX_TRAINING_SECONDS
            )
            raise TimeoutError(f"训练超过 {limit} 秒运行上限")

    def update_progress(self, epoch: int, metrics: Dict):
        self.current_epoch = epoch
        self.progress = (epoch / self.total_epochs) * 100
        self.metrics_history.append(
            {"epoch": epoch, "timestamp": datetime.now().isoformat(), **metrics}
        )

        # 回归默认最大化 R²，分类默认最大化 accuracy；loss 类指标取最小值。
        self.best_metrics = select_best_metrics(self.metrics_history, self.config)

        # 保存训练记录
        self._save_run()

        # 发送Kafka训练进度事件
        try:
            send_training_event("training.progress", {
                "job_id": self.job_id,
                "epoch": epoch,
                "total_epochs": self.total_epochs,
                "progress_pct": round(self.progress, 1),
                "metrics": metrics,
            })
        except Exception:
            pass  # Kafka 不可用时静默降级

    def _save_run(self):
        """保存训练记录到JSON文件。

        配置只保存轻量元数据；训练矩阵由执行器独立接收，不写入运行记录。
        """
        run_file = RUNS_DIR / f"{self.job_id}.json"
        run_data = {
            "job_id": self.job_id,
            "config": _persisted_training_config(self.config),
            "status": self.status,
            "progress": self.progress,
            "current_epoch": self.current_epoch,
            "total_epochs": self.total_epochs,
            "metrics_history": self.metrics_history,
            "best_metrics": self.best_metrics,
            "model_path": self.model_path,
            "error": self.error,
            "error_traceback": self.error_traceback,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": datetime.now().isoformat(),
        }
        temp_file = run_file.with_suffix(".json.tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(run_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, run_file)
        except Exception as _save_err:
            try:
                temp_file.unlink(missing_ok=True)
            except Exception:
                pass
            print(f"[SAVE_RUN] FAILED to write {run_file}: {type(_save_err).__name__}: {_save_err}")
            raise


class PyTorchTrainer:
    """PyTorch模型训练器"""

    @staticmethod
    def create_model(input_dim: int, output_dim: int, config: Dict) -> nn.Module:
        hidden_layers = config.get("hidden_layers", [128, 64])
        layers = []
        prev_dim = input_dim

        for hidden_dim in hidden_layers:
            layers.extend([nn.Linear(prev_dim, hidden_dim), nn.ReLU()])
            if config.get("batch_norm", False):
                layers.append(nn.BatchNorm1d(hidden_dim))
            if float(config.get("dropout", 0.2)) > 0:
                layers.append(nn.Dropout(config.get("dropout", 0.2)))
            prev_dim = hidden_dim

        layers.append(nn.Linear(prev_dim, output_dim))
        return nn.Sequential(*layers)

    @staticmethod
    def train(
        job: TrainingJob, X_train, y_train, X_val, y_val,
        initial_model=None, target_scaler=None,
    ):
        config = job.config

        # 准备数据
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch is not available for neural network training")

        X_train_tensor = torch.FloatTensor(X_train)
        y_train_tensor = torch.FloatTensor(y_train).reshape(-1, 1)
        X_val_tensor = torch.FloatTensor(X_val)
        y_val_loss = (
            target_scaler.transform(np.asarray(y_val).reshape(-1, 1)).reshape(-1)
            if target_scaler is not None else y_val
        )
        y_val_tensor = torch.FloatTensor(y_val_loss).reshape(-1, 1)

        train_dataset = TensorDataset(X_train_tensor, y_train_tensor)
        train_loader = DataLoader(
            train_dataset, batch_size=config.get("batch_size", 32), shuffle=True
        )

        # 创建新模型，或从可信封装产物恢复网络权重后继续微调。
        input_dim = X_train.shape[1]
        output_dim = 1
        model = initial_model or PyTorchTrainer.create_model(input_dim, output_dim, config)
        first_linear = next(
            (layer for layer in model.modules() if isinstance(layer, nn.Linear)),
            None,
        )
        if first_linear is None or int(first_linear.in_features) != int(input_dim):
            raise ValueError(
                f"继续训练特征数量不匹配: 模型需要 "
                f"{getattr(first_linear, 'in_features', '未知')} 个，数据集提供 {input_dim} 个"
            )
        model = model.cpu()

        criterion = nn.MSELoss()
        optimizer = optim.Adam(
            model.parameters(), lr=config.get("learning_rate", 0.001)
        )

        epochs = config.get("epochs", 10)

        for epoch in range(1, epochs + 1):
            job.ensure_runtime_budget()
            model.train()
            total_loss = 0

            for batch_X, batch_y in train_loader:
                job.ensure_runtime_budget()
                optimizer.zero_grad()
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                total_loss += loss.item()

            avg_loss = total_loss / len(train_loader)

            # 验证
            model.eval()
            with torch.no_grad():
                val_outputs = model(X_val_tensor)
                val_loss = criterion(val_outputs, y_val_tensor).item()
                val_preds = val_outputs.numpy().reshape(-1, 1)
                if target_scaler is not None:
                    val_preds = target_scaler.inverse_transform(val_preds)
                val_preds = val_preds.flatten()

                r2 = r2_score(y_val, val_preds)
                rmse = np.sqrt(mean_squared_error(y_val, val_preds))

            metrics = {"loss": avg_loss, "val_loss": val_loss, "r2": r2, "rmse": rmse}
            job.update_progress(epoch, metrics)

        # 保存模型
        model_path = MODEL_DIR / f"{job.job_id}_model.pt"
        torch.save(model.state_dict(), model_path)
        job.model_path = str(model_path)

        # 同时保存模型配置以便恢复
        config_path = MODEL_DIR / f"{job.job_id}_config.json"
        with open(config_path, "w") as f:
            json.dump({
                "model_type": "mlp",
                "task_type": job.config.get("task_type", "regression"),
                "input_dim": X_train.shape[1],
                "output_dim": 1,
                "hidden_layers": job.config.get("hidden_layers", [128, 64]),
                "dropout": job.config.get("dropout", 0.2),
                "batch_norm": job.config.get("batch_norm", False),
                "normalize_target": job.config.get("normalize_target", False),
            }, f)

        return model


class SklearnTrainer:
    """Sklearn/LightGBM/XGBoost训练器"""

    @staticmethod
    def train(job: TrainingJob, X_train, y_train, X_val, y_val):
        config = job.config
        job.ensure_runtime_budget()
        model_type = config.get("model_type", "lightgbm")
        task_type = config.get("task_type", "classification")

        model = None
        # 创建模型
        if model_type == "lightgbm":
            if not LIGHTGBM_AVAILABLE:
                model_type = "random_forest"  # fallback
                model = RandomForestRegressor(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    random_state=42,
                ) if task_type == "regression" else RandomForestClassifier(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    random_state=42,
                )
            elif task_type == "classification":
                model = lgb.LGBMClassifier(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    learning_rate=config.get("learning_rate", 0.1),
                    random_state=42,
                    verbose=-1,
                )
            else:
                model = lgb.LGBMRegressor(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    learning_rate=config.get("learning_rate", 0.1),
                    random_state=42,
                    verbose=-1,
                )
        elif model_type == "xgboost":
            if not XGBOOST_AVAILABLE:
                model_type = "random_forest"  # fallback
                model = RandomForestRegressor(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    random_state=42,
                ) if task_type == "regression" else RandomForestClassifier(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    random_state=42,
                )
            elif task_type == "classification":
                model = xgb.XGBClassifier(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    learning_rate=config.get("learning_rate", 0.1),
                    random_state=42,
                    verbosity=0,
                )
            else:
                model = xgb.XGBRegressor(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    learning_rate=config.get("learning_rate", 0.1),
                    random_state=42,
                    verbosity=0,
                )
        elif model_type == "random_forest":
            if task_type == "classification":
                model = RandomForestClassifier(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    random_state=42,
                )
            else:
                model = RandomForestRegressor(
                    n_estimators=config.get("n_estimators", 100),
                    max_depth=config.get("max_depth", 6),
                    random_state=42,
                )

        if model is None:
            raise ValueError(f"Unsupported model type: {config.get('model_type')} (available: random_forest, lightgbm={LIGHTGBM_AVAILABLE}, xgboost={XGBOOST_AVAILABLE})")

        # 在【原始未缩放】空间上 fit scaler,然后封装进 sklearn Pipeline。
        # 这样 joblib.dump 持久化的是 Pipeline(scaler + model),评估/推理加载后
        # 喂原始特征即可,Pipeline 内部自动缩放 —— 修复 scaler 没随模型持久化
        # 导致评估端喂未缩放特征、R² 为负的 bug。
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_val_scaled = scaler.transform(X_val)
        job.progress = max(float(job.progress or 0), 15.0)
        job._save_run()

        # sklearn 树模型只需训练一次，不需要多轮 epoch
        job.progress = max(float(job.progress or 0), 20.0)
        job._save_run()
        model.fit(X_train_scaled, y_train)
        job.ensure_runtime_budget()
        job.progress = max(float(job.progress or 0), 75.0)
        job._save_run()

        # 预测和评估(用缩放后的验证集,与训练分布一致)
        y_pred = model.predict(X_val_scaled)
        job.progress = max(float(job.progress or 0), 85.0)
        job._save_run()

        if task_type == "classification":
            metrics = {
                "accuracy": accuracy_score(y_val, y_pred),
                "f1": f1_score(y_val, y_pred, average="weighted"),
                "precision": precision_score(y_val, y_pred, average="weighted"),
                "recall": recall_score(y_val, y_pred, average="weighted"),
            }
        else:
            metrics = {
                "r2": r2_score(y_val, y_pred),
                "rmse": np.sqrt(mean_squared_error(y_val, y_pred)),
                "mae": mean_absolute_error(y_val, y_pred),
            }

        job.update_progress(1, metrics)
        job.current_epoch = 1
        job.total_epochs = 1
        job.progress = 100.0

        # 保存模型 —— 持久化 Pipeline(scaler + model),评估/推理加载后喂原始特征即可
        pipe = Pipeline([("scaler", scaler), ("model", model)])
        model_path = MODEL_DIR / f"{job.job_id}_model.pkl"
        joblib.dump(pipe, model_path)
        job.model_path = str(model_path)
        # Serialize model to bytes for model_blob storage
        blob_buf = io.BytesIO()
        joblib.dump(pipe, blob_buf)
        job.model_blob = blob_buf.getvalue()

        return pipe

    @staticmethod
    def continue_train(job: TrainingJob, base_model, X_train, y_train, X_val, y_val):
        """Continue a trusted sklearn Pipeline through partial_fit or warm_start."""
        job.ensure_runtime_budget()
        mode = job.config.get("resume_training_mode")
        if isinstance(base_model, Pipeline):
            scaler = base_model.named_steps.get("scaler")
            estimator = base_model.named_steps.get("model") or list(base_model.named_steps.values())[-1]
        else:
            scaler = None
            estimator = base_model
        X_train_ready = scaler.transform(X_train) if scaler is not None else X_train
        X_val_ready = scaler.transform(X_val) if scaler is not None else X_val

        if mode == "partial_fit" and hasattr(estimator, "partial_fit"):
            if job.config.get("task_type") == "classification":
                estimator.partial_fit(X_train_ready, y_train, classes=np.unique(y_train))
            else:
                estimator.partial_fit(X_train_ready, y_train)
        elif mode == "warm_start" and hasattr(estimator, "n_estimators"):
            current_estimators = int(getattr(estimator, "n_estimators", 0) or 0)
            additional_estimators = int(job.config.get("n_estimators", 100))
            estimator.set_params(
                warm_start=True,
                n_estimators=current_estimators + additional_estimators,
            )
            estimator.fit(X_train_ready, y_train)
        else:
            raise ValueError(f"模型不支持继续训练模式: {mode}")
        job.ensure_runtime_budget()

        y_pred = estimator.predict(X_val_ready)
        if job.config.get("task_type") == "classification":
            metrics = {
                "accuracy": accuracy_score(y_val, y_pred),
                "f1": f1_score(y_val, y_pred, average="weighted"),
                "precision": precision_score(y_val, y_pred, average="weighted"),
                "recall": recall_score(y_val, y_pred, average="weighted"),
            }
        else:
            metrics = {
                "r2": r2_score(y_val, y_pred),
                "rmse": np.sqrt(mean_squared_error(y_val, y_pred)),
                "mae": mean_absolute_error(y_val, y_pred),
            }
        job.update_progress(1, metrics)
        job.current_epoch = 1
        job.total_epochs = 1
        job.progress = 100.0

        result = base_model
        model_path = MODEL_DIR / f"{job.job_id}_model.pkl"
        joblib.dump(result, model_path)
        job.model_path = str(model_path)
        blob_buf = io.BytesIO()
        joblib.dump(result, blob_buf)
        job.model_blob = blob_buf.getvalue()
        return result


class TrainingService:
    """训练服务主类"""

    @staticmethod
    def create_job(config: Dict) -> str:
        import uuid

        config = validate_training_config(config)
        job_id = f"train_{uuid.uuid4().hex[:8]}"
        job = TrainingJob(job_id, config)
        _training_jobs[job_id] = job

        # 在数据库中创建训练任务记录（SQL 直写，避免 fork 后 ORM 表重定义）
        try:
            db = SessionLocal()
            try:
                db.execute(
                    text("INSERT INTO training_jobs (name, training_job_id, dataset_id, organization_id, hyperparameters, status) "
                         "VALUES (:name, :jid, :did, :org, :hp, :status)"),
                    {
                        "name": config.get("name", "训练任务"),
                        "jid": job_id,
                        "did": config.get("dataset_id"),
                        "org": config.get("organization_id"),
                        "hp": json.dumps({k: v for k, v in config.items() if k not in ("X", "y", "feature_columns", "feature_baseline")}),
                        "status": "PENDING",
                    }
                )
                db.commit()
                print(f"[DB] Created training job record: {job_id}")
            finally:
                db.close()
        except Exception as e:
            print(f"[DB] Warning: Could not create DB record for {job_id}: {e}")

        return job_id

    @staticmethod
    def _save_to_db(job):
        """将训练结果保存到 PostgreSQL 数据库（SQL 直写）"""
        # 如果任务已被删除，不再写回数据库（防止幽灵任务）
        if job.job_id not in _training_jobs:
            print(f"[DB] Job {job.job_id} was deleted, skipping save")
            return
        try:
            db = SessionLocal()
            try:
                status = job.status.upper() if hasattr(job, 'status') else 'PENDING'
                metrics_json = json.dumps(job.best_metrics) if job.best_metrics else None
                # 过滤掉大数据字段(X/y/特征列),避免 hyperparameters 列膨胀到 GB 级(HyperOpt 等 trial 会把全量数据塞进 config)
                _BIG_KEYS = ("X", "y", "feature_columns", "feature_baseline")
                _hp = {k: v for k, v in (job.config or {}).items() if k not in _BIG_KEYS}
                hp_json = json.dumps(_hp) if _hp else None

                # UPSERT
                db.execute(
                    text("""
                        INSERT INTO training_jobs (name, training_job_id, dataset_id, hyperparameters, status, metrics,
                            mlflow_run_id, registered_model_id, model_id, organization_id,
                            started_at, completed_at, error_message)
                        VALUES (:name, :jid, :did, :hp, :status, :metrics, :run_id, :model_id, :model_id,
                            :org, :started, :completed, :error)
                        ON CONFLICT (training_job_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            metrics = EXCLUDED.metrics,
                            hyperparameters = EXCLUDED.hyperparameters,
                            mlflow_run_id = COALESCE(EXCLUDED.mlflow_run_id, training_jobs.mlflow_run_id),
                            registered_model_id = COALESCE(EXCLUDED.registered_model_id, training_jobs.registered_model_id),
                            model_id = COALESCE(EXCLUDED.model_id, training_jobs.model_id),
                            organization_id = COALESCE(EXCLUDED.organization_id, training_jobs.organization_id),
                            started_at = EXCLUDED.started_at,
                            completed_at = EXCLUDED.completed_at,
                            error_message = EXCLUDED.error_message
                    """),
                    {
                        "name": job.config.get("name", "训练任务") if hasattr(job, 'config') else "训练任务",
                        "jid": job.job_id,
                        "did": job.config.get("dataset_id") if hasattr(job, 'config') else None,
                        "hp": hp_json,
                        "status": status,
                        "metrics": metrics_json,
                        "run_id": (job.config or {}).get("mlflow_run_id"),
                        "model_id": (job.config or {}).get("registered_model_id"),
                        "org": (job.config or {}).get("organization_id"),
                        "started": job.started_at if hasattr(job, 'started_at') and job.started_at else None,
                        "completed": job.finished_at if hasattr(job, 'finished_at') and job.finished_at else None,
                        "error": job.error if hasattr(job, 'error') and job.error else None,
                    }
                )
                db.commit()
                print(f"[DB] Saved training results for job: {job.job_id} (status={status})")
            finally:
                db.close()
        except Exception as e:
            print(f"[DB] Error saving to DB for {job.job_id}: {e}")

    @staticmethod
    def _update_source_algorithm_status(job, status: str):
        """Keep the algorithm-store status aligned with its real training job."""
        algorithm_id = (job.config or {}).get("algorithm_id")
        if not algorithm_id:
            return
        try:
            db = SessionLocal()
            try:
                db.execute(
                    text("UPDATE algorithm_models SET status=:status, updated_at=NOW() WHERE id=:id"),
                    {"status": status, "id": algorithm_id},
                )
                db.commit()
            finally:
                db.close()
        except Exception as e:
            print(f"[DB] Error updating algorithm {algorithm_id} status to {status}: {e}")

    @staticmethod
    def _load_from_db():
        """服务启动时从 PostgreSQL 恢复训练历史（SQL 直读）"""
        try:
            db = SessionLocal()
            try:
                rows = db.execute(
                    text("SELECT training_job_id, hyperparameters, status, metrics, "
                         "started_at, completed_at, error_message, mlflow_run_id, registered_model_id, organization_id, created_at "
                         "FROM training_jobs "
                         "WHERE training_job_id IS NOT NULL")
                ).fetchall()
                loaded_count = 0
                for row in rows:
                    jid = row[0]
                    if not jid or jid in _training_jobs:
                        continue
                    hp = row[1] if isinstance(row[1], dict) else (json.loads(row[1]) if row[1] else {})
                    if row[7]:
                        hp["mlflow_run_id"] = row[7]
                    if row[8]:
                        hp["registered_model_id"] = row[8]
                    hp["organization_id"] = row[9]
                    job = TrainingJob(jid, hp)
                    job.status = (row[2] or "unknown").lower()
                    job.best_metrics = row[3] if isinstance(row[3], dict) else (json.loads(row[3]) if row[3] else {})
                    job.progress = 100.0 if job.status == "completed" else 0.0
                    job.error = row[6]
                    run_file = RUNS_DIR / f"{jid}.json"
                    if run_file.exists():
                        try:
                            run_data = json.loads(run_file.read_text())
                            history = run_data.get("metrics_history") or []
                            if history:
                                job.metrics_history = history
                                job.best_metrics = select_best_metrics(history, hp)
                            job.current_epoch = run_data.get("current_epoch", job.current_epoch)
                            job.total_epochs = run_data.get("total_epochs", job.total_epochs)
                            job.model_path = run_data.get("model_path")
                            job.created_at = run_data.get("created_at", job.created_at)
                        except Exception as run_error:
                            print(f"[DB] Warning: Could not restore run file {jid}: {run_error}")
                    if row[4]:
                        job.started_at = row[4].isoformat() if hasattr(row[4], 'isoformat') else str(row[4])
                    if row[5]:
                        job.finished_at = row[5].isoformat() if hasattr(row[5], 'isoformat') else str(row[5])
                    if row[10]:
                        job.created_at = row[10].isoformat() if hasattr(row[10], 'isoformat') else str(row[10])
                    if hp:
                        completed_steps = hp.get("source_total_steps") or hp.get("epochs") or 1
                        job.total_epochs = int(completed_steps)
                        if job.status == "completed":
                            job.current_epoch = int(completed_steps)
                    _training_jobs[jid] = job
                    loaded_count += 1
                print(f"[DB] Loaded {loaded_count} training jobs from database")
            finally:
                db.close()
        except Exception as e:
            print(f"[DB] Warning: Could not load training history from DB: {e}")

    @staticmethod
    def start_training(job_id: str, data: Dict):
        job = _training_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        X_checked, y_checked = validate_training_data(data)
        if not _training_slots.acquire(blocking=False):
            raise RuntimeError(f"训练并发已达 {MAX_CONCURRENT_TRAININGS} 个上限")

        def train_thread():
            try:
                job.status = "running"
                job.started_at = datetime.now().isoformat()
                job.progress = max(float(job.progress or 0), 2.0)
                runtime_limit = (
                    MAX_USER_SCRIPT_TRAINING_SECONDS
                    if job.config.get("source_execution_mode") == "execute_user_script"
                    else MAX_TRAINING_SECONDS
                )
                job.deadline_monotonic = time.monotonic() + runtime_limit
                # Persist immediately so every API worker can observe that the
                # task has left the queue before the first epoch/trial finishes.
                job._save_run()

                # 发送Kafka训练开始事件
                try:
                    send_training_event("training.started", {
                        "job_id": job.job_id,
                        "config": {
                            "model_type": job.config.get("model_type"),
                            "task_type": job.config.get("task_type"),
                            "epochs": job.config.get("epochs", 1),
                            "name": job.config.get("name", ""),
                        },
                        "started_at": job.started_at,
                    })
                except Exception:
                    pass

                # ---- execute_user_script 分支：真正执行用户上传的脚本（跳过内置 trainer）----
                if job.config.get("source_execution_mode") == "execute_user_script":
                    from services.user_script_trainer import execute_user_script_train
                    execute_user_script_train(job)
                    job._save_run()
                    TrainingService._save_to_db(job)
                    return

                X = X_checked
                y = y_checked

                X_train, X_val, y_train, y_val = split_training_data(
                    X, y, job.config.get("split_strategy", "random")
                )
                job.progress = max(float(job.progress or 0), 8.0)
                job._save_run()

                model_type = job.config.get("model_type", "lightgbm")
                feature_scaler = None
                target_scaler = None
                resume_model = _load_resume_artifact(job.config)

                if model_type in ["mlp", "neural_network"]:
                    # PyTorch路径预缩放，并将拟合后的scaler和网络一起保存为部署产物。
                    initial_network = None
                    if resume_model is not None:
                        if not isinstance(resume_model, PyTorchPredictor):
                            raise ValueError("继续训练产物不是平台PyTorch预测模型")
                        initial_network = resume_model.model
                        feature_scaler = resume_model.scaler
                        target_scaler = getattr(resume_model, "target_scaler", None)
                        if feature_scaler is not None:
                            X_train = feature_scaler.transform(X_train)
                            X_val = feature_scaler.transform(X_val)
                    elif job.config.get("normalize", True):
                        feature_scaler = StandardScaler()
                        X_train = feature_scaler.fit_transform(X_train)
                        X_val = feature_scaler.transform(X_val)
                    if target_scaler is None and job.config.get("normalize_target", False):
                        target_scaler = StandardScaler()
                        y_train = target_scaler.fit_transform(
                            np.asarray(y_train).reshape(-1, 1)
                        ).reshape(-1)
                    elif target_scaler is not None:
                        y_train = target_scaler.transform(
                            np.asarray(y_train).reshape(-1, 1)
                        ).reshape(-1)
                    model = PyTorchTrainer.train(
                        job, X_train, y_train, X_val, y_val,
                        initial_model=initial_network,
                        target_scaler=target_scaler,
                    )
                else:
                    # sklearn 路径:传【原始未缩放】数据,由 SklearnTrainer 内部
                    # fit scaler 并封装进 sklearn Pipeline 一并 joblib.dump。
                    # 这样评估/推理加载 Pipeline 后喂原始特征即可(Pipeline 自动缩放),
                    # 修复 scaler 没随模型持久化导致评估 R² 为负的 bug。
                    model = (
                        SklearnTrainer.continue_train(
                            job, resume_model, X_train, y_train, X_val, y_val
                        )
                        if resume_model is not None
                        else SklearnTrainer.train(job, X_train, y_train, X_val, y_val)
                    )

                # 记录特征分布基线(供推理时漂移检测)——用未标准化的原始 X,与推理输入同空间
                try:
                    _fnames = job.config.get("feature_names") or []
                    _baseline = []
                    for _i in range(X.shape[1]):
                        _col = X[:, _i]
                        _baseline.append({
                            "name": _fnames[_i] if _i < len(_fnames) else f"feature_{_i}",
                            "mean": float(np.mean(_col)),
                            "std": float(np.std(_col)) or 1e-8,
                            "min": float(np.min(_col)),
                            "max": float(np.max(_col)),
                        })
                    job.config["feature_baseline"] = _baseline
                except Exception:
                    pass

                serving_model = model
                if model_type in ["mlp", "neural_network"]:
                    serving_model = PyTorchPredictor(
                        model, feature_scaler, target_scaler
                    )
                    deploy_path = MODEL_DIR / f"{job.job_id}_model.pkl"
                    joblib.dump(serving_model, deploy_path)
                    deploy_buffer = io.BytesIO()
                    joblib.dump(serving_model, deploy_buffer)
                    job.model_blob = deploy_buffer.getvalue()
                    # model_versions与部署服务统一指向可直接predict的产物；原生.pt仍保留。
                    job.model_path = str(deploy_path)

                _model_registry[job_id] = serving_model
                job.status = "completed"
                job.finished_at = datetime.now().isoformat()
                job.progress = 100.0
                job._save_run()

                # 保存训练结果到数据库
                TrainingService._save_to_db(job)
                TrainingService._update_source_algorithm_status(job, "trained")

                # 注册到模型库(models + model_versions),让"模型管理"可见真实训练产物
                try:
                    _reg_db = SessionLocal()
                    try:
                        _mname = job.config.get("name", "训练模型") or "训练模型"
                        _mtype = job.config.get("task_type", "regression")
                        _mfw = "pytorch" if job.config.get("model_type") in ("mlp", "neural_network") else "sklearn"
                        # Trust contract for consumers that deserialize joblib:
                        # only artifacts written by this service get a source and
                        # immutable digest.  External/legacy blobs remain
                        # untrusted until re-trained or explicitly re-signed.
                        _artifact_hash = None
                        if job.model_path and os.path.isfile(job.model_path):
                            with open(job.model_path, "rb") as _artifact_file:
                                _artifact_hash = _artifact_digest(_artifact_file.read())
                        _artifact_source = "real_training"
                        _description = (
                            f"训练自动注册 (job={job.job_id}; artifact_source={_artifact_source}; "
                            f"artifact_sha256={_artifact_hash or 'unavailable'})"
                        )
                        _row = _reg_db.execute(text(
                            "INSERT INTO models (name, description, model_type, framework, status, organization_id, folder_id, created_at, updated_at) "
                            "VALUES (:name, :desc, :mt, :fw, 'READY', :org, :folder_id, NOW(), NOW()) RETURNING id"
                        ), {"name": _mname, "desc": _description,
                            "mt": _mtype, "fw": _mfw,
                            "org": job.config.get("organization_id"), "folder_id": job.config.get("model_folder_id")}).fetchone()
                        if _row:
                            _mid = _row[0]
                            # 统一 MLflow 命名:foundation_model_{models.id}
                            # 打通 models.id ↔ MLflow registered model name 双向映射
                            _mlflow_name = f"foundation_model_{_mid}"
                            _reg_db.execute(text(
                                "UPDATE models SET mlflow_name = :mn WHERE id = :mid"
                            ), {"mn": _mlflow_name, "mid": _mid})
                            _params = {
                                "model_type": job.config.get("model_type"),
                                "framework": _mfw,
                                "feature_cols": (
                                    job.config.get("feature_columns") or job.config.get("feature_cols")
                                ),
                                "target_col": (
                                    job.config.get("target_column") or job.config.get("target_col")
                                ),
                                "job_id": job.job_id,
                                "dataset_id": job.config.get("dataset_id"),
                                "artifact_source": _artifact_source,
                                "artifact_sha256": _artifact_hash,
                            }
                            _version_row = _reg_db.execute(text(
                                "INSERT INTO model_versions (model_id, version_number, file_path, metrics, parameters, is_deployed, created_at) "
                                "VALUES (:mid, :ver, :fp, :met, :par, false, NOW()) RETURNING id"
                            ), {
                                "mid": _mid, "ver": "v1", "fp": job.model_path,
                                "met": json.dumps(job.best_metrics) if job.best_metrics else None,
                                "par": json.dumps(_params, ensure_ascii=False),
                            })
                            _model_version_id = _version_row.scalar_one()
                            if hasattr(job, 'model_blob') and job.model_blob:
                                _reg_db.execute(text(
                                    "UPDATE models SET model_blob = :blob WHERE id = :mid"
                                ), {"blob": job.model_blob, "mid": _mid})
                            _reg_db.commit()
                            job.config["registered_model_id"] = _mid
                            job.config["registered_model_version_id"] = _model_version_id
                            job.config["mlflow_name"] = _mlflow_name  # 血缘链接:models.id → MLflow name
                            job._save_run()  # 更新 run JSON 写入 model_id 映射
                            TrainingService._save_to_db(job)

                            # ======== 自动同步血缘: dataset → 训练任务 → 模型 ========
                            try:
                                from services.lineage_sync_service import upsert_training_lineage
                                _ljid = _reg_db.execute(text(
                                    "SELECT id FROM training_jobs WHERE training_job_id = :ref"
                                ), {"ref": job.job_id}).fetchone()
                                if _ljid:
                                    upsert_training_lineage(_reg_db, int(_ljid[0]))
                            except Exception as _lexc:
                                print(f"[lineage] 训练任务 {job.job_id} 同步血缘失败: {_lexc}")

                            # ======== 自动注册漂移检测 baseline ========
                            # 训练完成 → 用训练数据的真实特征分布(每列原始值)注册 baseline,
                            # 这样后续评估/推理灌入预测后,/drift/detect/{model_id} 可执行 KS/PSI。
                            # 不再用空 predictions:必须灌入真实列值作为 reference_data。
                            try:
                                from services.drift_detection_service import get_drift_detection_service
                                _drift_svc = get_drift_detection_service()
                                _bl = job.config.get("feature_baseline") or []
                                _fnames_drift = job.config.get("feature_names") or []
                                _mid_str = str(_mid)
                                for _bi, _b in enumerate(_bl):
                                    _fname = _b.get("name") or (
                                        _fnames_drift[_bi] if _bi < len(_fnames_drift) else f"feature_{_bi}"
                                    )
                                    # 取该列的真实训练值(截前 500 行作为 reference 分布)
                                    try:
                                        _col_vals = X[:, _bi].tolist()[:500] if X.ndim == 2 else []
                                    except Exception:
                                        _col_vals = []
                                    if not _col_vals:
                                        continue
                                    _drift_svc.register_model(_mid_str, _fname, "ks", 0.05)
                                    _drift_svc.set_baseline(_mid_str, _fname, [float(v) for v in _col_vals])
                                print(f"[drift] auto-registered baseline for model {_mid_str} ({len(_bl)} features)")
                            except Exception as _drift_err:
                                print(f"[drift] auto-register baseline failed: {_drift_err}")
                    finally:
                        _reg_db.close()
                except Exception as _registration_err:
                    job.config["registration_error"] = str(_registration_err)[:200]
                    print(f"[registry] model registration failed for {job.job_id}: {_registration_err}")
                    TrainingService._save_to_db(job)
                    try:
                        job._save_run()
                    except Exception as _save_err:
                        print(f"[registry] failed to persist registration error: {_save_err}")

                # 发送Kafka训练完成事件
                try:
                    send_training_event("training.completed", {
                        "job_id": job.job_id,
                        "status": "completed",
                        "best_metrics": job.best_metrics,
                        "model_path": job.model_path,
                        "finished_at": job.finished_at,
                        "total_epochs": job.total_epochs,
                    })
                except Exception:
                    pass

                # ======== MLflow实验追踪 ========
                if MLFLOW_AVAILABLE:
                    tracker = None
                    run_id = None
                    _mlflow_success = False
                    try:
                        job_name = f"{job.job_id}_{job.config.get('model_type', 'unknown')}"
                        experiment_name = job.config.get("project_name", "foundation_training")

                        tracker = get_mlflow_tracker()
                        run_id = tracker.start_run(
                            run_name=job_name,
                            experiment_name=experiment_name,
                            tags={
                                "job_id": job.job_id,
                                "model_type": job.config.get("model_type", "unknown"),
                                "task_type": job.config.get("task_type", "unknown"),
                            },
                        )

                        if run_id:
                            job.config["mlflow_run_id"] = run_id  # 血缘链接:训练→MLflow run
                            TrainingService._save_to_db(job)
                            _version_id = job.config.get("registered_model_version_id")
                            if _version_id:
                                _link_db = SessionLocal()
                                try:
                                    _link_db.execute(text(
                                        "UPDATE model_versions SET mlflow_run_id = :run_id WHERE id = :version_id"
                                    ), {"run_id": run_id, "version_id": _version_id})
                                    _link_db.commit()
                                finally:
                                    _link_db.close()
                            # 立即落盘 mlflow_run_id:后续 log_params/log_model 可能耗时/抛错,
                            # 先保存可保证血缘链接不丢(即使后面失败,run_id 也已写入 JSON)
                            try:
                                job._save_run()
                            except Exception as _e:
                                print(f"[MLflow] early save of mlflow_run_id failed: {_e}")
                            # 记录配置参数
                            tracker.log_params(job.config)

                            # 记录最终指标
                            best_metrics = job.best_metrics
                            if best_metrics:
                                tracker.log_metrics(best_metrics, step=job.current_epoch)

                            # 记录模型文件
                            if job.model_path and os.path.exists(job.model_path):
                                tracker.log_artifact(job.model_path, "models")

                                # 统一命名:用 foundation_model_{models.id} 作为 MLflow registered model name,
                                # 与 models 表 mlflow_name 列一致,形成 models.id ↔ MLflow name 双向映射
                                _mlf_name = job.config.get("mlflow_name")

                                # 根据模型类型用不同的API记录模型
                                model_type = job.config.get("model_type", "lightgbm")
                                if model_type in ["mlp", "neural_network"] and TORCH_AVAILABLE:
                                    _registered = tracker.log_pytorch_model(
                                        model, "model", registered_model_name=_mlf_name
                                    )
                                    if not _registered:
                                        _registered = tracker.register_artifact_model(
                                            _mlf_name, run_id, "models"
                                        )
                                else:
                                    _registered = tracker.log_sklearn_model(
                                        model, "model", registered_model_name=_mlf_name
                                    )
                                if not _registered:
                                    raise RuntimeError("MLflow 模型注册失败")

                                # Registry registration is synchronous. Persist the real
                                # MLflow model version instead of deriving a fake value.
                                _registry = tracker.get_registry_model(_mlf_name) if _mlf_name else None
                                if (_registry and _version_id
                                        and _registry.get("latest_run_id") == run_id):
                                    _link_db = SessionLocal()
                                    try:
                                        _link_db.execute(text(
                                            "UPDATE model_versions SET mlflow_model_version = :mlflow_version "
                                            "WHERE id = :version_id"
                                        ), {
                                            "mlflow_version": str(_registry.get("latest_version")),
                                            "version_id": _version_id,
                                        })
                                        _link_db.commit()
                                    finally:
                                        _link_db.close()
                                elif _registry and _registry.get("latest_run_id") != run_id:
                                    raise RuntimeError("MLflow Registry 最新版本不属于当前训练 run，拒绝误绑定")

                            _mlflow_success = True
                            print(f"[MLflow] Run {run_id} logged successfully")
                    except Exception as mlflow_err:
                        print(f"[MLflow] Tracking failed: {mlflow_err}")
                        job.config["mlflow_error"] = str(mlflow_err)[:200]  # 不静默吞,存入 config 供排查
                    finally:
                        if tracker is not None and run_id:
                            tracker.end_run("FINISHED" if _mlflow_success else "FAILED")
                    # 强制保存含 mlflow_run_id/mlflow_error 的 config(确保血缘链接落 JSON)
                    # 注意:不再用 except:pass 吞掉写盘失败——否则 7MB json.dump 出错时
                    # mlflow_run_id 会静默丢失且无任何日志,这正是原 bug 的成因。
                    try:
                        job._save_run()
                    except Exception as _force_err:
                        print(f"[MLflow] forced _save_run FAILED, mlflow_run_id may be missing from JSON: {_force_err}")

            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                job.error_traceback = traceback.format_exc()
                job.finished_at = datetime.now().isoformat()
                job._save_run()

                # 保存失败状态到数据库
                TrainingService._save_to_db(job)
                TrainingService._update_source_algorithm_status(job, "failed")

                # 发送Kafka训练失败事件
                try:
                    send_training_event("training.failed", {
                        "job_id": job.job_id,
                        "status": "failed",
                        "error": str(e),
                        "finished_at": job.finished_at,
                    })
                except Exception:
                    pass
                print(f"Training error: {e}")
                import traceback

                traceback.print_exc()
            finally:
                _training_slots.release()

        thread = threading.Thread(target=train_thread)
        thread.daemon = True
        thread.start()

        return {"job_id": job_id, "status": "started"}

    @staticmethod
    def get_job_status(job_id: str, summary_only: bool = False) -> Dict:
        job = _training_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        result = {
            "job_id": job.job_id,
            "name": (job.config or {}).get("name") or job.job_id,
            "status": job.status,
            "progress": job.progress,
            "current_epoch": job.current_epoch,
            "total_epochs": job.total_epochs,
            "dataset_id": (job.config or {}).get("dataset_id"),
            "progress_kind": (job.config or {}).get("source_progress_kind") or "epoch",
            "best_metrics": job.best_metrics,
            "model_path": job.model_path,
            "mlflow_run_id": (job.config or {}).get("mlflow_run_id"),
            "model_id": (
                (job.config or {}).get("registered_model_id")
                or (job.config or {}).get("model_id")
            ),
            "registered_model_id": (job.config or {}).get("registered_model_id"),
            "organization_id": (job.config or {}).get("organization_id"),
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
        if not summary_only:
            result["config"] = _persisted_training_config(job.config)
            result["metrics_history"] = job.metrics_history
        return result


    @staticmethod
    def list_jobs(summary_only: bool = False) -> List[Dict]:
        return [
            TrainingService.get_job_status(job_id, summary_only=summary_only)
            for job_id in _training_jobs.keys()
        ]

    @staticmethod
    def predict(job_id: str, X: np.ndarray) -> np.ndarray:
        model = _model_registry.get(job_id)
        if model is None:
            # 尝试从磁盘加载模型
            model_path = MODEL_DIR / f"{job_id}_model.pkl"
            pt_path = MODEL_DIR / f"{job_id}_model.pt"
            if model_path.exists():
                model = joblib.load(str(model_path))
                _model_registry[job_id] = model
            elif pt_path.exists():
                # PyTorch 模型：从配置 JSON 恢复
                config_path = MODEL_DIR / f"{job_id}_config.json"
                if config_path.exists() and TORCH_AVAILABLE:
                    with open(config_path) as f:
                        pt_config = json.load(f)
                    input_dim = pt_config["input_dim"]
                    output_dim = pt_config["output_dim"]
                    model = PyTorchTrainer.create_model(input_dim, output_dim, pt_config)
                    model.load_state_dict(torch.load(str(pt_path), map_location="cpu"))
                    model.eval()
                    _model_registry[job_id] = model
                else:
                    raise ValueError(f"Cannot reload PyTorch model {job_id} (config or PyTorch missing)")
            else:
                raise ValueError(f"Model for job {job_id} not found (no {model_path} or {pt_path})")

        if TORCH_AVAILABLE and isinstance(model, nn.Module):
            model.eval()
            with torch.no_grad():
                X_tensor = torch.FloatTensor(X)
                predictions = model(X_tensor).numpy()
            return predictions
        else:
            return model.predict(X)


# 模块加载时从数据库恢复训练历史
try:
    TrainingService._load_from_db()
except Exception as e:
    print(f"[DB] Initial load from DB failed (may be unavailable): {e}")


def get_training_service():
    return TrainingService
