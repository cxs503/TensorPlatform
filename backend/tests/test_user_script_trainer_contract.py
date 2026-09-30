import sys
from pathlib import Path

import pandas as pd
import pytest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.real_training_service import (  # noqa: E402
    MAX_USER_SCRIPT_TRAINING_SECONDS,
    validate_training_config,
)
from services.user_script_trainer import (  # noqa: E402
    RUN_TIMEOUT_SECONDS,
    _apply_source_hyperparameters,
    _prepare_training_dataframe,
)


def test_selected_features_and_target_are_the_only_columns_visible_to_source():
    frame = pd.DataFrame({
        "unused": [9, 8, 7],
        "feature_b": [1.0, 2.0, 3.0],
        "target": [10.0, 11.0, 12.0],
        "feature_a": [4.0, 5.0, 6.0],
    })

    prepared, features = _prepare_training_dataframe(
        frame, "target", ["feature_a", "feature_b"]
    )

    assert features == ["feature_a", "feature_b"]
    assert prepared.columns.tolist() == ["feature_a", "feature_b", "target"]


def test_target_is_removed_from_feature_selection_and_non_numeric_is_rejected():
    frame = pd.DataFrame({"x": [1, 2], "target": [3, 4], "text": ["a", "b"]})
    prepared, features = _prepare_training_dataframe(frame, "target", ["x", "target"])
    assert features == ["x"]
    assert prepared.columns.tolist() == ["x", "target"]

    with pytest.raises(RuntimeError, match="不是数值类型"):
        _prepare_training_dataframe(frame, "target", ["text"])


def test_user_source_config_keeps_effective_hyperparameters_and_runtime():
    config = validate_training_config({
        "name": "source-training",
        "task_type": "regression",
        "model_type": "user_script",
        "source_execution_mode": "execute_user_script",
        "effective_hyperparameters": {"epochs": 7, "learning_rate": 0.003},
        "runtime_environment_id": 4,
        "source_data_mode": "full_dataset",
        "source_time_column": "timestamp",
        "source_data_contract": {"target_column": "target", "time_column": "timestamp"},
    })

    assert config["effective_hyperparameters"]["epochs"] == 7
    assert config["runtime_environment_id"] == 4
    assert config["source_data_mode"] == "full_dataset"


def test_source_hyperparameters_override_literal_training_constants():
    source = "N_TRIALS = 100\nSEED = 42\nprint(N_TRIALS)\n"
    prepared = _apply_source_hyperparameters(source, {"N_TRIALS": 2})
    assert "N_TRIALS = 2" in prepared
    assert "SEED = 42" in prepared


def test_user_source_timeout_is_consistent_and_longer_than_builtin_limit():
    assert RUN_TIMEOUT_SECONDS == MAX_USER_SCRIPT_TRAINING_SECONDS
    assert RUN_TIMEOUT_SECONDS == 1800
