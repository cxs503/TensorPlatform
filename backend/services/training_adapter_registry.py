"""Central registry for safe training adapters used by the model factory.

An algorithm-store entry describes business intent.  The adapter resolves that
intent to one of the training implementations that the platform can actually
execute.  Uploaded source is deliberately not imported or executed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class TrainingAdapterSpec:
    id: str
    name: str
    task_type: str
    algorithm_type: str
    runtime_model_type: str
    runtime_task_type: str
    split_strategy: str = "random"

    def to_dict(self) -> dict:
        return asdict(self)


_TASK_ALIASES = {
    "classification": "classification",
    "regression": "regression",
    "time_series": "time_series_forecasting",
    "timeseries": "time_series_forecasting",
    "forecasting": "time_series_forecasting",
    "time_series_forecasting": "time_series_forecasting",
}

_ALGORITHM_ALIASES = {
    "randomforest": "random_forest",
    "random forest": "random_forest",
    "random_forest": "random_forest",
    "rf": "random_forest",
    "xgb": "xgboost",
    "xgboost": "xgboost",
    "lgbm": "lightgbm",
    "lightgbm": "lightgbm",
    "mlp": "mlp",
    "neural_network": "mlp",
    "neural network": "mlp",
}


def normalize_task_type(value: Optional[str]) -> Optional[str]:
    key = (value or "").strip().lower().replace("-", "_")
    return _TASK_ALIASES.get(key)


def normalize_algorithm_type(value: Optional[str]) -> Optional[str]:
    key = (value or "").strip().lower().replace("-", "_")
    return _ALGORITHM_ALIASES.get(key)


def _build_specs() -> tuple[TrainingAdapterSpec, ...]:
    specs = []
    names = {
        "random_forest": "Random Forest",
        "xgboost": "XGBoost",
        "lightgbm": "LightGBM",
        "mlp": "MLP 神经网络",
    }
    for task_type in ("classification", "regression"):
        for algorithm_type, name in names.items():
            specs.append(TrainingAdapterSpec(
                id=algorithm_type,
                name=f"{name} · {'分类' if task_type == 'classification' else '回归'}",
                task_type=task_type,
                algorithm_type=algorithm_type,
                runtime_model_type=algorithm_type,
                runtime_task_type=task_type,
            ))
    for algorithm_type, name in names.items():
        specs.append(TrainingAdapterSpec(
            id=f"timeseries_{algorithm_type}",
            name=f"{name} · 时序预测",
            task_type="time_series_forecasting",
            algorithm_type=algorithm_type,
            runtime_model_type=algorithm_type,
            runtime_task_type="regression",
            split_strategy="chronological",
        ))
    return tuple(specs)


TRAINING_ADAPTERS = _build_specs()


def list_training_adapters() -> list[dict]:
    return [spec.to_dict() for spec in TRAINING_ADAPTERS]


def get_training_adapter(adapter_id: Optional[str]) -> Optional[TrainingAdapterSpec]:
    return next((spec for spec in TRAINING_ADAPTERS if spec.id == adapter_id), None)


def resolve_training_adapter(
    task_type: Optional[str],
    algorithm_type: Optional[str],
    framework: Optional[str] = None,
) -> Optional[TrainingAdapterSpec]:
    """Resolve only deterministic combinations; never infer from display names."""
    normalized_task = normalize_task_type(task_type)
    normalized_algorithm = normalize_algorithm_type(algorithm_type)
    if normalized_algorithm is None:
        normalized_algorithm = normalize_algorithm_type(framework)
    if normalized_task is None or normalized_algorithm is None:
        return None
    return next(
        (
            spec for spec in TRAINING_ADAPTERS
            if spec.task_type == normalized_task
            and spec.algorithm_type == normalized_algorithm
        ),
        None,
    )


def resolve_algorithm_model(algorithm) -> Optional[TrainingAdapterSpec]:
    """Resolve an ORM AlgorithmModel, including metadata used by legacy rows."""
    metadata = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
    task_type = metadata.get("task_type") or algorithm.category
    if normalize_task_type(task_type) is None:
        if any(key in metadata for key in ("r2", "rmse", "mae", "mape")):
            task_type = "regression"
        elif any(key in metadata for key in ("accuracy", "f1", "precision", "recall")):
            task_type = "classification"
    return resolve_training_adapter(
        task_type,
        metadata.get("algorithm_type") or metadata.get("training_adapter")
        or algorithm.algorithm,
        algorithm.framework,
    )
