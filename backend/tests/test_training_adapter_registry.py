"""Contract tests for deterministic model-factory adapter resolution."""
from types import SimpleNamespace

import numpy as np

from services.real_training_service import select_best_metrics, split_training_data
from services.training_adapter_registry import (
    resolve_algorithm_model,
    resolve_training_adapter,
)


def test_resolves_tabular_adapter_from_explicit_configuration():
    spec = resolve_training_adapter("classification", "xgboost", "sklearn")
    assert spec is not None
    assert spec.id == "xgboost"
    assert spec.runtime_model_type == "xgboost"
    assert spec.split_strategy == "random"


def test_resolves_time_series_adapter_with_chronological_split():
    spec = resolve_training_adapter("time_series_forecasting", "xgboost")
    assert spec is not None
    assert spec.id == "timeseries_xgboost"
    assert spec.runtime_model_type == "xgboost"
    assert spec.runtime_task_type == "regression"
    assert spec.split_strategy == "chronological"


def test_does_not_guess_ambiguous_sklearn_algorithm():
    assert resolve_training_adapter("regression", "sklearn", "sklearn") is None


def test_legacy_metric_type_can_resolve_without_display_name_guessing():
    algorithm = SimpleNamespace(
        metrics={"r2": 0.9},
        category="steel_process",
        algorithm="RandomForest",
        framework="Scikit-learn",
        name="名称不参与解析",
    )
    spec = resolve_algorithm_model(algorithm)
    assert spec is not None
    assert spec.id == "random_forest"


def test_chronological_split_preserves_future_holdout():
    X = np.arange(50, dtype=float).reshape(10, 5)
    y = np.arange(10)
    X_train, X_val, y_train, y_val = split_training_data(
        X, y, "chronological"
    )
    assert y_train.tolist() == list(range(8))
    assert y_val.tolist() == [8, 9]
    assert X_train[-1].tolist() == X[7].tolist()
    assert X_val[0].tolist() == X[8].tolist()


def test_regression_best_metrics_uses_highest_r2():
    history = [
        {"epoch": 1, "r2": -100.0, "rmse": 10.0},
        {"epoch": 2, "r2": 0.75, "rmse": 2.0},
        {"epoch": 3, "r2": 0.60, "rmse": 1.5},
    ]
    best = select_best_metrics(history, {"task_type": "regression"})
    assert best["epoch"] == 2
    assert best["r2"] == 0.75


def test_explicit_val_loss_is_minimized():
    history = [
        {"epoch": 1, "val_loss": 3.0},
        {"epoch": 2, "val_loss": 1.0},
    ]
    best = select_best_metrics(history, {"optimization_metric": "val_loss"})
    assert best["epoch"] == 2
