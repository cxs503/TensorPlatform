"""Fail-closed adapters for executing supported ML pipeline nodes.

The API layer owns authentication, RBAC and tenant lookup.  It must pass only
resources whose organisation access has already been checked through a
``VerifiedResourceAccess`` capability.  This module deliberately does not query
resources by unscoped IDs.

Adapters execute bounded, existing platform primitives and return identifiers,
shape information and aggregate metrics only.  They never return full datasets,
predictions or model bytes.
"""
from __future__ import annotations

import io
import hashlib
import hmac
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


DEFAULT_MAX_ROWS = 100_000
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_COLUMNS = 512
DEFAULT_MAX_MODEL_BYTES = 128 * 1024 * 1024
MAX_CONFIG_BYTES = 512 * 1024  # 512KB — 支持更大配置
MAX_TRANSFORMATIONS = 32


class PipelineNodeExecutionError(ValueError):
    """A user/configuration error that must fail the pipeline node."""


@dataclass(frozen=True)
class AuthorizedDataset:
    """Minimal dataset capability produced after the caller's tenant check."""

    dataset_id: int
    organization_id: int
    file_path: str
    name: str = "dataset"
    row_count: int = 0
    size_bytes: int = 0

    @classmethod
    def from_record(cls, record: Any) -> "AuthorizedDataset":
        return cls(
            dataset_id=int(record.id),
            organization_id=int(record.organization_id),
            file_path=str(record.file_path or ""),
            name=str(record.name or "dataset"),
            row_count=int(record.row_count or 0),
            size_bytes=int(record.size_bytes or 0),
        )


@dataclass(frozen=True)
class AuthorizedModel:
    """Minimal model capability produced after the caller's tenant check."""

    model_id: int
    organization_id: int
    name: str = "model"
    model_blob: bytes | None = field(default=None, repr=False)
    file_path: str | None = None
    model_type: str | None = None
    artifact_source: str | None = None
    artifact_sha256: str | None = None

    @classmethod
    def from_record(cls, record: Any, *, file_path: str | None = None) -> "AuthorizedModel":
        blob = record.model_blob
        if isinstance(blob, (memoryview, bytearray)):
            blob = bytes(blob)
        description = str(getattr(record, "description", "") or "")
        source_match = re.search(r"artifact_source=([a-z0-9_-]+)", description)
        hash_match = re.search(r"artifact_sha256=([0-9a-f]{64})", description)
        return cls(
            model_id=int(record.id),
            organization_id=int(record.organization_id),
            name=str(getattr(record, "name", None) or "model"),
            model_blob=blob,
            file_path=file_path,
            model_type=str(record.model_type) if record.model_type else None,
            artifact_source=source_match.group(1) if source_match else None,
            artifact_sha256=hash_match.group(1) if hash_match else None,
        )


@dataclass(frozen=True)
class VerifiedResourceAccess:
    """Tenant-scoped resource capability.

    Construct this only after the API caller has authenticated and checked that
    every supplied dataset/model belongs to ``organization_id``.  Null or
    cross-organisation resources are rejected again here as defence in depth.
    """

    organization_id: int
    datasets: Mapping[int, AuthorizedDataset] = field(default_factory=dict)
    models: Mapping[int, AuthorizedModel] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.organization_id:
            raise PipelineNodeExecutionError("节点执行必须绑定非空 organization_id")
        for resource in (*self.datasets.values(), *self.models.values()):
            if resource.organization_id != self.organization_id:
                raise PipelineNodeExecutionError("授权资源与流水线组织不一致")

    @classmethod
    def from_records(
        cls,
        organization_id: int,
        *,
        datasets: Iterable[Any] = (),
        models: Iterable[Any] = (),
    ) -> "VerifiedResourceAccess":
        dataset_caps = [AuthorizedDataset.from_record(item) for item in datasets]
        model_caps = [AuthorizedModel.from_record(item) for item in models]
        return cls(
            organization_id=int(organization_id),
            datasets={item.dataset_id: item for item in dataset_caps},
            models={item.model_id: item for item in model_caps},
        )

    def dataset(self, dataset_id: Any) -> AuthorizedDataset:
        try:
            resource = self.datasets[int(dataset_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineNodeExecutionError("dataset_id 未经调用方组织访问校验") from exc
        return resource

    def model(self, model_id: Any) -> AuthorizedModel:
        try:
            resource = self.models[int(model_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineNodeExecutionError("model_id 未经调用方组织访问校验") from exc
        return resource


@dataclass(frozen=True)
class PersistedDataset:
    dataset_id: int
    row_count: int
    size_bytes: int
    stage: str


class DerivedDatasetSink(Protocol):
    """Persistence boundary for transformed data."""

    def persist(
        self,
        *,
        source: AuthorizedDataset,
        frame: Any,
        name: str,
        description: str,
        stage: str,
        columns_info: list[dict[str, Any]],
    ) -> PersistedDataset: ...


class SQLAlchemyDerivedDatasetSink:
    """Atomically persist a derived CSV and tenant-propagated Dataset record."""

    def __init__(
        self,
        db: Any,
        output_root: str | Path = "/app/uploads/datasets",
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self.db = db
        self.output_root = Path(output_root).resolve()
        self.max_bytes = min(max(int(max_bytes), 1024), 256 * 1024 * 1024)

    def persist(
        self,
        *,
        source: AuthorizedDataset,
        frame: Any,
        name: str,
        description: str,
        stage: str,
        columns_info: list[dict[str, Any]],
    ) -> PersistedDataset:
        from models.core import Dataset

        self.output_root.mkdir(parents=True, exist_ok=True)
        record = Dataset(
            name=name[:128],
            description=description,
            file_path=None,
            row_count=int(len(frame)),
            size_bytes=0,
            stage=stage,
            governed_from=source.dataset_id,
            columns_info=columns_info,
            organization_id=source.organization_id,
        )
        final_path: Path | None = None
        temporary_path: Path | None = None
        try:
            self.db.add(record)
            self.db.flush()
            final_path = self.output_root / f"{record.id}_{stage}.csv"
            temporary_path = self.output_root / f".{record.id}.{uuid.uuid4().hex}.tmp"
            frame.to_csv(temporary_path, index=False)
            actual_size = temporary_path.stat().st_size
            if actual_size > self.max_bytes:
                raise PipelineNodeExecutionError(
                    f"派生数据集超过 {self.max_bytes} 字节上限"
                )
            os.replace(temporary_path, final_path)
            record.file_path = str(final_path)
            record.size_bytes = actual_size
            self.db.commit()
            from services.lineage_sync_service import safe_sync_derived_dataset
            safe_sync_derived_dataset(self.db, int(record.id))
            return PersistedDataset(
                dataset_id=int(record.id),
                row_count=int(record.row_count or 0),
                size_bytes=int(record.size_bytes or 0),
                stage=stage,
            )
        except Exception:
            self.db.rollback()
            for path in (temporary_path, final_path):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            raise


class PipelineNodeExecutor:
    """Execute bounded real nodes against pre-authorized resources."""

    SUPPORTED_TASK_TYPES = {
        "data_load",
        "data_process",
        "feature_engineering",
        "model_training",
        "model_evaluate",
        "model_deploy",
    }

    def __init__(
        self,
        access: VerifiedResourceAccess,
        *,
        derived_dataset_sink: DerivedDatasetSink | None = None,
        dataset_roots: Iterable[str | Path] = ("/app/uploads/datasets",),
        model_roots: Iterable[str | Path] = ("/app/uploads/models",),
        max_rows: int = DEFAULT_MAX_ROWS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_columns: int = DEFAULT_MAX_COLUMNS,
        max_model_bytes: int = DEFAULT_MAX_MODEL_BYTES,
    ):
        self.access = access
        self.derived_dataset_sink = derived_dataset_sink
        self.dataset_roots = tuple(Path(root).resolve() for root in dataset_roots)
        self.model_roots = tuple(Path(root).resolve() for root in model_roots)
        self.max_rows = min(max(int(max_rows), 10), DEFAULT_MAX_ROWS)
        self.max_bytes = min(max(int(max_bytes), 1024), 256 * 1024 * 1024)
        self.max_columns = min(max(int(max_columns), 2), DEFAULT_MAX_COLUMNS)
        self.max_model_bytes = min(max(int(max_model_bytes), 1024), DEFAULT_MAX_MODEL_BYTES)

    def execute(self, task_type: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
        config = dict(config or {})
        self._validate_config(config)
        handlers = {
            "data_load": self._data_load,
            "data_process": self._data_process,
            "feature_engineering": self._feature_engineering,
            "model_training": self._model_training,
            "model_evaluate": self._model_evaluate,
            "model_deploy": self._model_deploy,
        }
        handler = handlers.get(str(task_type))
        if handler is None:
            raise PipelineNodeExecutionError(f"未支持的流水线节点类型: {task_type}")
        return handler(config)

    def _validate_config(self, config: Mapping[str, Any]) -> None:
        try:
            size = len(json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode())
        except (TypeError, ValueError) as exc:
            raise PipelineNodeExecutionError(f"节点配置不是有效 JSON: {exc}") from exc
        if size > MAX_CONFIG_BYTES:
            raise PipelineNodeExecutionError("节点配置超过 128KiB")

    @staticmethod
    def _is_below(path: Path, roots: tuple[Path, ...]) -> bool:
        return any(path == root or root in path.parents for root in roots)

    def _load_frame(self, dataset: AuthorizedDataset):
        import pandas as pd

        if not dataset.file_path:
            raise PipelineNodeExecutionError("数据集没有可读取的 CSV 文件")
        path = Path(dataset.file_path).resolve(strict=True)
        if path.suffix.lower() != ".csv" or not self._is_below(path, self.dataset_roots):
            raise PipelineNodeExecutionError("数据集文件不在受控 CSV 根目录")
        actual_size = path.stat().st_size
        declared_size = max(0, int(dataset.size_bytes))
        declared_rows = max(0, int(dataset.row_count))
        if max(actual_size, declared_size) > self.max_bytes:
            raise PipelineNodeExecutionError(f"数据集超过 {self.max_bytes} 字节上限")
        if declared_rows > self.max_rows:
            raise PipelineNodeExecutionError(f"数据集超过 {self.max_rows} 行上限")
        try:
            frame = pd.read_csv(path, nrows=self.max_rows + 1)
        except Exception as exc:
            raise PipelineNodeExecutionError(f"CSV 读取失败: {exc}") from exc
        if len(frame) > self.max_rows:
            raise PipelineNodeExecutionError(f"数据集超过 {self.max_rows} 行上限")
        if len(frame.columns) > self.max_columns:
            raise PipelineNodeExecutionError(f"数据集超过 {self.max_columns} 列上限")
        if len(set(map(str, frame.columns))) != len(frame.columns):
            raise PipelineNodeExecutionError("数据集包含重复列名")
        return frame, actual_size

    def _data_load(self, config: Mapping[str, Any]) -> dict[str, Any]:
        dataset = self.access.dataset(config.get("dataset_id"))
        frame, size_bytes = self._load_frame(dataset)
        return {
            "dataset_id": dataset.dataset_id,
            "dataset_name": dataset.name,
            "row_count": int(len(frame)),
            "column_count": int(len(frame.columns)),
            "size_bytes": int(size_bytes),
            "loaded": True,
        }

    def _require_sink(self) -> DerivedDatasetSink:
        if self.derived_dataset_sink is None:
            raise PipelineNodeExecutionError("派生数据节点未配置受控持久化 sink")
        return self.derived_dataset_sink

    @staticmethod
    def _columns_info(frame: Any, derived: set[str] | None = None) -> list[dict[str, Any]]:
        derived = derived or set()
        return [
            {"name": str(column), "dtype": str(frame[column].dtype), "derived": str(column) in derived}
            for column in frame.columns
        ]

    def _validate_result_frame(self, frame: Any) -> None:
        if len(frame) > self.max_rows:
            raise PipelineNodeExecutionError(f"派生数据集超过 {self.max_rows} 行上限")
        if len(frame.columns) > self.max_columns:
            raise PipelineNodeExecutionError(f"派生数据集超过 {self.max_columns} 列上限")
        memory_bytes = int(frame.memory_usage(index=True, deep=True).sum())
        if memory_bytes > self.max_bytes:
            raise PipelineNodeExecutionError(f"派生数据集内存超过 {self.max_bytes} 字节上限")

    def _data_process(self, config: Mapping[str, Any]) -> dict[str, Any]:
        import numpy as np
        from services.data_cleaning_service import (
            CleaningRuleError,
            apply_definition,
            completeness_score,
        )

        dataset = self.access.dataset(config.get("dataset_id"))
        operation = str(config.get("operation") or "").lower()
        sink = self._require_sink()
        frame, _ = self._load_frame(dataset)
        before_rows, before_columns = frame.shape
        rule_id = None
        rule_name = None
        cleaning_summary = None

        if operation == "govern":
            # 保留原有的轻量默认治理，方便没有自定义规则时快速使用。
            missing_pct = [
                float(frame[column].isna().mean() * 100) for column in frame.columns
            ]
            quality_score = round(
                max(
                    0.0,
                    100.0
                    - float(np.mean(missing_pct) if missing_pct else 0.0) * 2,
                ),
                1,
            )
            result = frame.dropna(axis=1, how="all")
            result = result.dropna(thresh=max(1, len(result.columns) // 2))
            output_name = str(config.get("name") or f"{dataset.name}(已治理)")
            description = (
                f"由数据集 {dataset.dataset_id} 基础治理生成；质量评分 {quality_score}"
            )
        elif operation == "clean_rule":
            definition = config.get("cleaning_definition")
            rule_id = config.get("cleaning_rule_id")
            rule_name = str(config.get("cleaning_rule_name") or "").strip()
            if not isinstance(definition, dict) or not rule_name:
                raise PipelineNodeExecutionError(
                    "自定义清洗规则未在运行时完成校验，请重新选择已启用规则"
                )
            try:
                result, cleaning_summary = apply_definition(frame, definition)
                quality_score = completeness_score(result)
            except CleaningRuleError as exc:
                raise PipelineNodeExecutionError(f"清洗规则执行失败: {exc}") from exc
            output_name = str(
                config.get("name") or f"{dataset.name}(清洗：{rule_name})"
            )
            description = (
                f"由数据集 {dataset.dataset_id} 按清洗规则 #{rule_id}"
                f"（{rule_name}）生成；完整度 {quality_score}%"
            )
        else:
            raise PipelineNodeExecutionError(
                "data_process 仅支持 govern 或 clean_rule 操作"
            )

        self._validate_result_frame(result)
        save_dataset = bool(config.get("save_dataset", True))
        persisted = sink.persist(
            source=dataset,
            frame=result,
            name=output_name,
            description=description,
            stage="governed" if save_dataset else "pipeline_temp",
            columns_info=self._columns_info(result),
        )
        outputs = {
            "dataset_id": persisted.dataset_id,
            # Persist the display name with the run result. A derived dataset may
            # later be cleaned up, so the UI must not rely on a later list lookup.
            "dataset_name": output_name,
            "source_dataset_id": dataset.dataset_id,
            "source_dataset_name": dataset.name,
            "row_count": persisted.row_count,
            "column_count": int(len(result.columns)),
            "size_bytes": persisted.size_bytes,
            "stage": persisted.stage,
            "saved_to_dataset_management": save_dataset,
            "quality_score": quality_score,
            "removed_rows": int(before_rows - len(result)),
            "removed_columns": int(before_columns - len(result.columns)),
            "operation": operation,
        }
        if not save_dataset:
            outputs["transient_dataset_id"] = persisted.dataset_id
        if rule_id is not None:
            outputs["cleaning_rule_id"] = int(rule_id)
            outputs["cleaning_rule_name"] = rule_name
            outputs["cleaning_summary"] = cleaning_summary
        return outputs

    def _feature_engineering(self, config: Mapping[str, Any]) -> dict[str, Any]:
        from services.feature_engineering_service import run_transformations

        dataset = self.access.dataset(config.get("dataset_id"))
        transformations = config.get("transformations", config.get("transforms"))
        if not isinstance(transformations, list) or not transformations:
            raise PipelineNodeExecutionError("feature_engineering 必须配置非空 transformations")
        if len(transformations) > MAX_TRANSFORMATIONS or any(not isinstance(item, dict) for item in transformations):
            raise PipelineNodeExecutionError(f"transformations 必须是最多 {MAX_TRANSFORMATIONS} 项的对象数组")
        sink = self._require_sink()
        frame, _ = self._load_frame(dataset)
        original_columns = set(map(str, frame.columns))
        try:
            result = run_transformations(frame, transformations, config.get("time_column"))
        except (KeyError, SyntaxError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise PipelineNodeExecutionError(f"特征工程失败: {exc}") from exc
        if len(result) != len(frame):
            raise PipelineNodeExecutionError("特征工程不得改变数据集行数")
        self._validate_result_frame(result)
        new_columns = [str(item) for item in result.columns if str(item) not in original_columns]
        if not new_columns:
            raise PipelineNodeExecutionError("特征工程没有产生新列")
        persisted = sink.persist(
            source=dataset,
            frame=result,
            name=str(config.get("name") or f"{dataset.name}(特征工程)"),
            description=f"由数据集 {dataset.dataset_id} 生成 {len(new_columns)} 个派生特征",
            stage="engineered",
            columns_info=self._columns_info(result, set(new_columns)),
        )
        return {
            "dataset_id": persisted.dataset_id,
            "source_dataset_id": dataset.dataset_id,
            "row_count": persisted.row_count,
            "column_count": int(len(result.columns)),
            "size_bytes": persisted.size_bytes,
            "stage": persisted.stage,
            "new_columns": new_columns,
        }

    def _load_model(self, model: AuthorizedModel):
        import joblib

        if model.artifact_source not in {"real_training", "pipeline_internal"}:
            raise PipelineNodeExecutionError("模型产物缺少受信任内部来源标识")
        if not model.artifact_sha256 or not re.fullmatch(r"[0-9a-f]{64}", model.artifact_sha256):
            raise PipelineNodeExecutionError("模型产物缺少有效 SHA-256 信任元数据")
        if model.model_blob is not None:
            raw = bytes(model.model_blob)
            if len(raw) > self.max_model_bytes:
                raise PipelineNodeExecutionError("模型产物超过容量上限")
            if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), model.artifact_sha256):
                raise PipelineNodeExecutionError("模型产物 SHA-256 校验失败")
            source: Any = io.BytesIO(raw)
        elif model.file_path:
            path = Path(model.file_path).resolve(strict=True)
            if not self._is_below(path, self.model_roots):
                raise PipelineNodeExecutionError("模型文件不在受控根目录")
            if path.stat().st_size > self.max_model_bytes:
                raise PipelineNodeExecutionError("模型产物超过容量上限")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if not hmac.compare_digest(digest, model.artifact_sha256):
                raise PipelineNodeExecutionError("模型产物 SHA-256 校验失败")
            source = path
        else:
            raise PipelineNodeExecutionError("模型没有可评估的真实产物")
        try:
            estimator = joblib.load(source)
        except Exception as exc:
            raise PipelineNodeExecutionError(f"模型产物加载失败: {exc}") from exc
        if not callable(getattr(estimator, "predict", None)):
            raise PipelineNodeExecutionError("模型产物不支持 predict")
        return estimator

    @staticmethod
    def _finite_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, raw in metrics.items():
            value = float(raw)
            if not math.isfinite(value):
                raise PipelineNodeExecutionError(f"评估指标 {key} 不是有限数")
            result[str(key)] = value
        return result

    def _model_training(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """执行模型训练节点"""
        import sklearn
        from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.metrics import r2_score, accuracy_score
        from sklearn.model_selection import train_test_split
        import numpy as np

        dataset_id = config.get("dataset_id")
        model_type = config.get("model_type", "random_forest")
        task_type = config.get("task_type", "regression")
        params = config.get("params", {})

        # Load dataset
        if not dataset_id:
            raise PipelineNodeExecutionError("未指定训练数据集(dataset_id)")
        
        authorized = self.access.datasets.get(str(dataset_id))
        if not authorized:
            raise PipelineNodeExecutionError(f"无权访问数据集 {dataset_id}")
        
        df = self._load_frame(authorized)
        if len(df.columns) < 2:
            raise PipelineNodeExecutionError("数据集列数不足，需要至少1个特征列+1个目标列")
        
        target_col = config.get("target_column", df.columns[-1])
        if target_col not in df.columns:
            raise PipelineNodeExecutionError(f"目标列 {target_col} 不在数据集中")
        
        X = df.drop(columns=[target_col]).values
        y = df[target_col].values

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        # Select and train model with timeout-aware limit
        n_estimators = min(params.get("n_estimators", 30), 30)  # Cap for speed
        
        if task_type == "classification":
            if model_type == "logistic_regression":
                model = LogisticRegression(max_iter=1000, random_state=42)
            else:
                model = RandomForestClassifier(n_estimators=n_estimators, random_state=42, n_jobs=-1)
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            score = float(accuracy_score(y_test, y_pred))
            metric_name = "accuracy"
        else:
            if model_type == "ridge":
                model = Ridge(alpha=params.get("alpha", 1.0))
            else:
                model = RandomForestRegressor(n_estimators=n_estimators, random_state=42, n_jobs=-1)
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            score = float(r2_score(y_test, y_pred))
            metric_name = "r2"

        return {
            "success": True,
            "model_type": model_type,
            "task_type": task_type,
            metric_name: round(score, 4),
            "samples": len(df),
            "features": X.shape[1],
        }

    def _model_evaluate(self, config: Mapping[str, Any]) -> dict[str, Any]:
        import numpy as np
        import pandas as pd
        from sklearn.metrics import (
            accuracy_score,
            f1_score,
            mean_absolute_error,
            mean_squared_error,
            precision_score,
            r2_score,
            recall_score,
        )
        from sklearn.preprocessing import LabelEncoder

        dataset = self.access.dataset(config.get("dataset_id"))
        model = self.access.model(config.get("model_id"))
        if config.get("cross_validation"):
            raise PipelineNodeExecutionError("Pipeline 评估暂不支持高开销 cross_validation")
        raw_task_type = str(config.get("task_type") or model.model_type or "regression").lower()
        # 时序预测的输出本质为连续数值，沿用回归评估指标；仅改变评估语义，不改变模型。
        task_type = {
            "time_series": "regression",
            "time_series_forecasting": "regression",
            "timeseries": "regression",
            "forecasting": "regression",
        }.get(raw_task_type, raw_task_type)
        if task_type not in {"regression", "classification"}:
            raise PipelineNodeExecutionError(
                "model_evaluate 仅支持回归、分类或时序预测模型"
            )
        frame, _ = self._load_frame(dataset)
        target = str(config.get("target_column") or frame.columns[-1])
        if target not in frame.columns:
            raise PipelineNodeExecutionError(f"目标列不存在: {target}")
        requested_features = config.get("feature_columns")
        if requested_features is not None and (
            not isinstance(requested_features, list)
            or not requested_features
            or any(not isinstance(item, str) for item in requested_features)
        ):
            raise PipelineNodeExecutionError("feature_columns 必须是非空字符串数组")
        if requested_features:
            missing = sorted(set(requested_features) - set(frame.columns))
            if missing:
                raise PipelineNodeExecutionError(f"特征列不存在: {', '.join(missing)}")
            feature_columns = list(requested_features)
        else:
            feature_columns = [
                str(column)
                for column in frame.columns
                if str(column) != target and pd.api.types.is_numeric_dtype(frame[column])
            ]
        if not feature_columns or target in feature_columns:
            raise PipelineNodeExecutionError("未找到有效且不含目标列的特征列")
        if len(feature_columns) > self.max_columns - 1:
            raise PipelineNodeExecutionError("评估特征列超过容量上限")
        features = frame[feature_columns].apply(pd.to_numeric, errors="coerce")
        features = features.fillna(features.mean()).fillna(0)
        labels = frame[target]
        valid = labels.notna()
        features, labels = features.loc[valid], labels.loc[valid]
        if len(features) < 5:
            raise PipelineNodeExecutionError("评估至少需要 5 条有效数据")
        if task_type == "classification":
            labels = LabelEncoder().fit_transform(labels.astype(str))
        else:
            labels = pd.to_numeric(labels, errors="coerce")
            valid_numeric = labels.notna()
            features, labels = features.loc[valid_numeric], labels.loc[valid_numeric]
        if len(features) < 5:
            raise PipelineNodeExecutionError("评估至少需要 5 条数值有效数据")
        # Pipeline 评估节点接收的是用户明确选择的独立测试数据集。
        # 整份数据都参与推理，不再从中随机抽取 20%。为兼容历史流水线，
        # 已保存的 test_size 字段会被忽略。
        test_x = features.to_numpy(dtype=float)
        test_y = np.asarray(labels)
        estimator = self._load_model(model)
        try:
            predictions = estimator.predict(test_x)
        except Exception as exc:
            raise PipelineNodeExecutionError(f"模型预测失败: {exc}") from exc
        if len(predictions) != len(test_y):
            raise PipelineNodeExecutionError("模型预测数量与评估样本不一致")
        if task_type == "classification":
            metrics = self._finite_metrics(
                {
                    "accuracy": accuracy_score(test_y, predictions),
                    "precision": precision_score(test_y, predictions, average="weighted", zero_division=0),
                    "recall": recall_score(test_y, predictions, average="weighted", zero_division=0),
                    "f1": f1_score(test_y, predictions, average="weighted", zero_division=0),
                }
            )
        else:
            mse = mean_squared_error(test_y, predictions)
            metrics = self._finite_metrics(
                {
                    "MSE": mse,
                    "RMSE": math.sqrt(mse),
                    "MAE": mean_absolute_error(test_y, predictions),
                    "R2": r2_score(test_y, predictions),
                }
            )
        requested_metrics = config.get("metrics")
        metric_notice = None
        if requested_metrics is not None:
            if not isinstance(requested_metrics, list) or not requested_metrics:
                raise PipelineNodeExecutionError("metrics 必须是非空数组")
            aliases = {str(key).lower(): key for key in metrics}
            selected_keys = []
            unsupported = []
            for item in requested_metrics:
                key = aliases.get(str(item).strip().lower())
                if key is None:
                    unsupported.append(str(item))
                elif key not in selected_keys:
                    selected_keys.append(key)
            if unsupported:
                metric_notice = (
                    "已忽略不适用于当前任务类型的指标: "
                    + ", ".join(unsupported)
                    + "；已使用可计算的默认指标"
                )
            if selected_keys:
                metrics = {key: metrics[key] for key in selected_keys}

        result = {
            "model_id": model.model_id,
            "model_name": model.name,
            "dataset_id": dataset.dataset_id,
            "dataset_name": dataset.name,
            "task_type": task_type,
            "sample_count": int(len(test_y)),
            "feature_count": int(len(feature_columns)),
            "test_scope": "full_dataset",
            "metrics": metrics,
        }
        if metric_notice:
            result["metric_notice"] = metric_notice
        return result

    def _model_deploy(self, config: Mapping[str, Any]) -> dict[str, Any]:
        self.access.model(config.get("model_id"))
        raise PipelineNodeExecutionError(
            "model_deploy 未开放：现有部署服务仍含内存态/模拟回退，必须经独立发布审批与健康门禁"
        )


def execute_pipeline_node(
    task_type: str,
    config: Mapping[str, Any] | None,
    *,
    access: VerifiedResourceAccess,
    derived_dataset_sink: DerivedDatasetSink | None = None,
    dataset_roots: Iterable[str | Path] = ("/app/uploads/datasets",),
    model_roots: Iterable[str | Path] = ("/app/uploads/models",),
) -> dict[str, Any]:
    """Convenience API intended for ``api.ml_pipeline`` integration."""

    return PipelineNodeExecutor(
        access,
        derived_dataset_sink=derived_dataset_sink,
        dataset_roots=dataset_roots,
        model_roots=model_roots,
    ).execute(task_type, config)
