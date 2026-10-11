"""Shared training-algorithm resolution for the low-code ML Pipeline."""
from __future__ import annotations

from typing import Any

from services.algorithm_source_service import (
    algorithm_asset_type,
    ensure_algorithm_source_contract,
)
from services.training_adapter_registry import normalize_task_type


_DATA_CONTRACT_KEYS = {
    "TARGET", "TARGET_COL", "TARGET_COLUMN", "Y_COL", "LABEL",
    "TIME_COL", "TIME_COLUMN", "TIMESTAMP_COL", "DATE_COL",
    "RAW_FEATURE_COLS", "FEATURE_COLUMNS", "FEATURE_COLS",
    "INPUT_COLUMNS", "X_COLS",
}


def _form_parameter_candidates(contract: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = contract.get("parameter_candidates")
    if not isinstance(candidates, list):
        return []
    return [
        item for item in candidates
        if isinstance(item, dict)
        and str(item.get("key") or "").upper() not in _DATA_CONTRACT_KEYS
    ]


def list_pipeline_trainable_algorithms(db) -> list[dict[str, Any]]:
    """Return only algorithm-store entries that a pipeline can genuinely train."""
    from models.core import AlgorithmModel

    items: list[dict[str, Any]] = []
    for algorithm in (
        db.query(AlgorithmModel)
        .order_by(AlgorithmModel.created_at.desc(), AlgorithmModel.id.desc())
        .all()
    ):
        asset_type = algorithm_asset_type(algorithm)
        metadata = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
        raw_task_type = str(metadata.get("task_type") or algorithm.category or "").lower()
        business_task_type = normalize_task_type(raw_task_type)
        if asset_type == "uploaded_source":
            # 控制项目的有效入口是“控制仿真”，不应伪装成监督训练算法。
            if raw_task_type == "control_optimization" or business_task_type == "control_optimization":
                continue
            contract = ensure_algorithm_source_contract(db, algorithm) or {}
            items.append(
                {
                    "id": int(algorithm.id),
                    "name": str(algorithm.name),
                    "kind": "uploaded_source",
                    "label": "上传源码",
                    "task_type": business_task_type,
                    "target_column": (
                        (contract.get("data_contract") or {}).get("target_column")
                    ),
                    "feature_columns": (
                        (contract.get("data_contract") or {}).get("feature_columns") or []
                    ),
                    "time_column": (
                        (contract.get("data_contract") or {}).get("time_column")
                    ),
                    "parameter_candidates": _form_parameter_candidates(contract),
                    "declared_defaults": contract.get("declared_defaults") or {},
                    "entry_point": contract.get("entry_function") or algorithm.entry_point,
                }
            )
        elif asset_type == "finetune_model":
            runtime_model_type = metadata.get("runtime_model_type")
            task_type = str(metadata.get("task_type") or "regression").lower()
            if runtime_model_type and task_type in {
                "classification",
                "regression",
                "time_series_forecasting",
            }:
                items.append(
                    {
                        "id": int(algorithm.id),
                        "name": str(algorithm.name),
                        "kind": "finetune_model",
                        "label": "可继续训练模型",
                        "task_type": task_type,
                        "target_column": None,
                        "feature_columns": [],
                        "time_column": None,
                        "parameter_candidates": [],
                        "declared_defaults": {},
                        "entry_point": None,
                    }
                )
    return items


def enrich_pipeline_training_config(db, config: dict[str, Any]) -> dict[str, Any]:
    """Make a persisted pipeline algorithm selection executable at run time.

    A pipeline stores an algorithm ID and optional parameter overrides only.
    Here the current artifact metadata and source contract are read afresh.
    """
    effective = dict(config or {})
    algorithm_id = effective.get("algorithm_id")
    if algorithm_id in (None, ""):
        return effective

    try:
        algorithm_id = int(algorithm_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("流水线训练节点的算法 ID 无效") from exc

    from models.core import AlgorithmModel

    algorithm = db.query(AlgorithmModel).filter(AlgorithmModel.id == algorithm_id).first()
    if not algorithm:
        raise ValueError("所选上传算法不存在")
    metadata = algorithm.metrics if isinstance(algorithm.metrics, dict) else {}
    asset_type = algorithm_asset_type(algorithm)
    raw_task_type = str(metadata.get("task_type") or algorithm.category or "").lower()
    business_task_type = normalize_task_type(raw_task_type)

    if raw_task_type == "control_optimization" or business_task_type == "control_optimization":
        raise ValueError("控制优化算法不走训练节点，请使用控制仿真任务")
    if asset_type == "uploaded_source":
        contract = ensure_algorithm_source_contract(db, algorithm) or {}
        data_contract = (
            contract.get("data_contract")
            if isinstance(contract.get("data_contract"), dict)
            else {}
        )
        configured_target = str(effective.get("target_column") or "").strip()
        target_column = configured_target or str(
            data_contract.get("target_column") or ""
        ).strip()
        if not target_column:
            raise ValueError(
                "上传源码未声明预测目标列，请在训练节点填写“预测目标列”"
            )
        configured_features = effective.get("feature_columns")
        feature_columns_explicit = bool(effective.get("feature_columns_explicit")) and isinstance(
            configured_features, list
        )
        feature_columns = (
            configured_features
            if feature_columns_explicit and configured_features
            else data_contract.get("feature_columns") or []
        )
        if not isinstance(feature_columns, list):
            feature_columns = []

        defaults = contract.get("declared_defaults")
        overrides = effective.get("effective_hyperparameters")
        if not isinstance(defaults, dict):
            defaults = {}
        if not isinstance(overrides, dict):
            overrides = {}
        resolved_parameters = {**defaults, **overrides}
        display_keys = {
            "epochs", "batch_size", "learning_rate", "hidden_layers", "dropout",
            "n_estimators", "max_depth", "normalize", "batch_norm",
            "normalize_target",
        }
        effective.update(
            {
                "algorithm_id": algorithm_id,
                "algorithm_name": str(algorithm.name),
                "algorithm_version": str(algorithm.version or "1.0.0"),
                "model_type": "user_script",
                "task_type": (
                    business_task_type
                    if business_task_type in {
                        "classification", "regression", "time_series_forecasting"
                    }
                    else "regression"
                ),
                "business_task_type": business_task_type,
                "source_execution_mode": "execute_user_script",
                "source_entry_function": contract.get("entry_function"),
                "source_data_mode": "full_dataset",
                "source_time_column": data_contract.get("time_column"),
                "source_data_contract": data_contract,
                "target_column": target_column,
                "feature_columns": feature_columns,
                "feature_columns_explicit": feature_columns_explicit,
                "effective_hyperparameters": resolved_parameters,
                "hyperparameter_source": (
                    "manual" if overrides else "default"
                ),
            }
        )
        for key in display_keys:
            if key in resolved_parameters:
                effective[key] = resolved_parameters[key]
        if metadata.get("dependency_id"):
            effective["runtime_environment_id"] = metadata["dependency_id"]
        return effective

    if asset_type == "finetune_model":
        runtime_model_type = str(metadata.get("runtime_model_type") or "").strip()
        task_type = str(metadata.get("task_type") or "regression").lower()
        if not runtime_model_type or task_type not in {
            "classification", "regression", "time_series_forecasting"
        }:
            raise ValueError("该封装模型缺少可继续训练的运行配置")
        effective.pop("algorithm_id", None)
        effective.update(
            {
                "resume_algorithm_id": algorithm_id,
                "algorithm_name": str(algorithm.name),
                "algorithm_version": str(algorithm.version or "1.0.0"),
                "model_type": runtime_model_type,
                "task_type": task_type,
                "resume_artifact_path": metadata.get("artifact_path"),
                "resume_artifact_sha256": metadata.get("artifact_sha256"),
                "resume_training_mode": metadata.get("resume_training_mode"),
                "source_model_id": metadata.get("source_model_id"),
                "training_adapter": metadata.get("training_adapter"),
            }
        )
        return effective

    raise ValueError("所选算法不是可训练的上传源码或可继续训练模型")
