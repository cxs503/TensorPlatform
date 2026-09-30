"""
端到端数据管道 — 串联数据上传、治理、训练、推理全流程
"""
import json
import pickle
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy.orm import Session

from models.core import (
    Dataset,
    Model,
    ModelVersion,
    TrainingJob,
    TrainingStatus,
    ModelStatus,
)
from database.config import get_db
from services.base import BaseService


class DataPipeline(BaseService):
    """端到端数据管道编排器"""

    def __init__(self, db: Session):
        super().__init__(db)

    # ====================================================================
    #  阶段 1: 数据上传与治理
    # ====================================================================

    def upload_and_govern(
        self,
        data: List[Dict[str, Any]],
        project_id: int,
        name: str = None,
        description: str = "",
    ) -> dict:
        """
        1. Raw upload -> datasets 表 (stage=raw)
        2. Quality check -> 计算 quality_score
        3. Standardization -> 创建 data_standard 映射 (stage=governed)
        4. Store governed version -> datasets 表 (stage=governed, governed_from=raw_id)
        """
        if not data:
            raise ValueError("数据不能为空")

        entity_id = f"pipeline_{uuid.uuid4().hex[:8]}"

        # ---- Step 1: Raw upload ----
        columns = list(data[0].keys()) if data else []
        raw_dataset = Dataset(
            name=name or f"raw_{entity_id}",
            description=description,
            project_id=project_id,
            stage="raw",
            row_count=len(data),
            columns_info={"columns": columns, "dtypes": {}},
        )
        self.db.add(raw_dataset)
        self.db.flush()  # 获取 raw_dataset.id

        # ---- Step 2: Quality check ----
        quality_score = self._compute_quality(data, columns)
        raw_dataset.quality_score = quality_score

        # ---- Step 3: Standardization ----
        # 简单标准化: 将数据序列化为 JSON blob 存储
        standardized_data = self._standardize(data, columns)
        raw_dataset.data_blob = json.dumps(standardized_data, ensure_ascii=False).encode("utf-8")
        raw_dataset.size_bytes = len(raw_dataset.data_blob)

        # ---- Step 4: Governed version ----
        governed = Dataset(
            name=f"governed_{entity_id}",
            description=f"Governed version of dataset {raw_dataset.id}",
            project_id=project_id,
            stage="governed",
            row_count=len(data),
            columns_info={
                "columns": columns,
                "standardized": True,
                "quality_score": quality_score,
            },
            governed_from=raw_dataset.id,
            data_blob=raw_dataset.data_blob,
            size_bytes=raw_dataset.size_bytes,
            quality_score=quality_score,
        )
        self.db.add(governed)
        self.db.commit()
        from services.lineage_sync_service import safe_sync_derived_dataset
        safe_sync_derived_dataset(self.db, governed.id)

        return {
            "raw_dataset_id": raw_dataset.id,
            "governed_dataset_id": governed.id,
            "quality_score": quality_score,
            "row_count": len(data),
            "columns": columns,
        }

    def _compute_quality(self, data: List[Dict], columns: List[str]) -> float:
        """计算数据质量分 (0-100)"""
        if not data:
            return 0.0
        n = len(data)
        scores = []
        # 完整性得分: 每列非空比例
        for col in columns:
            non_null = sum(1 for row in data if row.get(col) is not None)
            scores.append(non_null / n * 100)
        # 唯一性得分: 非重复行比例
        try:
            row_strs = [json.dumps(row, sort_keys=True, default=str) for row in data]
            unique_ratio = len(set(row_strs)) / n * 100
        except Exception:
            unique_ratio = 100.0
        scores.append(unique_ratio)
        return round(sum(scores) / len(scores), 1)

    def _standardize(self, data: List[Dict], columns: List[str]) -> List[Dict]:
        """数据标准化: 统一类型、去重、排序"""
        seen = set()
        result = []
        for row in data:
            key = json.dumps(row, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            std_row = {}
            for col in columns:
                val = row.get(col)
                if isinstance(val, (int, float, str, bool, type(None))):
                    std_row[col] = val
                else:
                    std_row[col] = str(val)
            result.append(std_row)
        return result

    # ====================================================================
    #  阶段 2: 从数据集训练模型
    # ====================================================================

    def train_from_dataset(
        self,
        dataset_id: int,
        model_config: Dict[str, Any],
    ) -> dict:
        """
        1. Load data from DB
        2. Split train/test
        3. Train model (sklearn)
        4. Save model blob to models 表
        5. Record metrics to training_jobs 表
        """
        from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
        from sklearn.linear_model import LinearRegression, LogisticRegression
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import r2_score, mean_squared_error, accuracy_score, f1_score
        import pandas as pd

        # ---- Step 1: Load data ----
        dataset = self.db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not dataset:
            raise ValueError(f"Dataset {dataset_id} not found")

        if dataset.data_blob:
            raw_data = json.loads(dataset.data_blob.decode("utf-8"))
            df = pd.DataFrame(raw_data)
        else:
            # Fallback: try CSV
            from services.data_loader import load_dataset_data
            X_arr, y_arr, feature_names = load_dataset_data(self.db, dataset_id)
            df = pd.DataFrame(X_arr, columns=feature_names)
            df["target"] = y_arr

        if len(df) < 10:
            raise ValueError(f"Dataset {dataset_id} has only {len(df)} rows, need at least 10")

        # Auto-detect numeric columns
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if len(numeric_cols) < 2:
            raise ValueError(f"Dataset {dataset_id} needs at least 2 numeric columns")

        target_col = model_config.get("target_col")
        if not target_col or target_col not in df.columns:
            target_col = numeric_cols[-1]

        feature_cols = [c for c in numeric_cols if c != target_col]
        if not feature_cols:
            feature_cols = numeric_cols

        X = df[feature_cols].fillna(0).values.astype(np.float64)
        y = df[target_col].fillna(0).values.astype(np.float64)

        # ---- Step 2: Split ----
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        # ---- Step 3: Train model ----
        task_type = model_config.get("task_type", "regression")
        model_type = model_config.get("model_type", "random_forest")

        if task_type == "classification":
            if model_type == "logistic":
                model = LogisticRegression(max_iter=1000, random_state=42)
            else:
                model = RandomForestClassifier(
                    n_estimators=model_config.get("n_estimators", 100),
                    max_depth=model_config.get("max_depth", 6),
                    random_state=42,
                )
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            metrics = {
                "accuracy": round(accuracy_score(y_test, y_pred), 4),
                "f1": round(f1_score(y_test, y_pred, average="weighted"), 4),
            }
        else:
            if model_type == "linear":
                model = LinearRegression()
            else:
                model = RandomForestRegressor(
                    n_estimators=model_config.get("n_estimators", 100),
                    max_depth=model_config.get("max_depth", 6),
                    random_state=42,
                )
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            metrics = {
                "r2": round(r2_score(y_test, y_pred), 4),
                "rmse": round(np.sqrt(mean_squared_error(y_test, y_pred)), 4),
                "mae": round(np.mean(np.abs(y_test - y_pred)), 4),
            }

        # ---- Step 4: Save model blob ----
        model_blob = pickle.dumps(model)

        db_model = Model(
            name=model_config.get("name", f"model_{uuid.uuid4().hex[:8]}"),
            model_type=task_type,
            framework="sklearn",
            status=ModelStatus.READY,
            project_id=dataset.project_id,
            model_blob=model_blob,
        )
        self.db.add(db_model)
        self.db.flush()

        # ---- Step 5: Record metrics ----
        training_job = TrainingJob(
            name=f"train_{db_model.id}",
            model_id=db_model.id,
            dataset_id=dataset_id,
            status=TrainingStatus.COMPLETED,
            hyperparameters={
                "model_type": model_type,
                "task_type": task_type,
                "feature_cols": feature_cols,
                "target_col": target_col,
                "n_estimators": model_config.get("n_estimators", 100),
                "max_depth": model_config.get("max_depth", 6),
            },
            metrics=metrics,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        self.db.add(training_job)

        # ModelVersion
        version = ModelVersion(
            model_id=db_model.id,
            version_number="v1.0",
            metrics=metrics,
            parameters={
                "feature_cols": feature_cols,
                "target_col": target_col,
                "model_type": model_type,
            },
        )
        self.db.add(version)
        self.db.commit()

        return {
            "model_id": db_model.id,
            "version_id": version.id,
            "training_job_id": training_job.id,
            "metrics": metrics,
            "feature_cols": feature_cols,
            "target_col": target_col,
            "data_size": len(X),
        }

    # ====================================================================
    #  阶段 3: 从模型推理
    # ====================================================================

    def inference_from_model(self, model_id: int, features: List[List[float]]) -> dict:
        """
        1. Load model from DB (models 表 model_blob)
        2. Run prediction
        3. Return result
        """
        db_model = self.db.query(Model).filter(Model.id == model_id).first()
        if not db_model:
            raise ValueError(f"Model {model_id} not found")

        if not db_model.model_blob:
            raise ValueError(f"Model {model_id} has no saved model blob")

        # model_blob 由 joblib.dump 产生(joblib 容器格式,非纯 pickle)。
        # 必须用 joblib.load(BytesIO(...)) 加载;裸 pickle.loads 会抛
        # "STACK_GLOBAL requires str"。BytesIO 是为了规避 joblib.load(bytes)
        # 对含 \x00 的 pickle 报 "embedded null byte"。
        import io
        raw = bytes(db_model.model_blob) if isinstance(db_model.model_blob, (memoryview, bytearray)) else db_model.model_blob
        try:
            try:
                import joblib
                model = joblib.load(io.BytesIO(raw))
            except Exception:
                model = pickle.load(io.BytesIO(raw))
        except Exception as e:
            raise ValueError(f"Failed to load model {model_id}: {e}")

        X = np.array(features, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)

        t0 = datetime.utcnow()
        try:
            preds = model.predict(X)
        except Exception as e:
            raise ValueError(f"Prediction failed: {e}")

        results = []
        for i, pred in enumerate(preds):
            if isinstance(pred, (np.ndarray, list)):
                val = float(pred[0]) if len(pred) == 1 else [float(p) for p in pred]
            else:
                val = float(pred) if hasattr(pred, '__float__') else pred
            results.append({"index": i, "prediction": val})

        return {
            "model_id": model_id,
            "model_name": db_model.name,
            "model_type": db_model.model_type,
            "predictions": results,
            "inference_time_ms": round(
                (datetime.utcnow() - t0).total_seconds() * 1000, 2
            ),
        }

    # ====================================================================
    #  便捷方法
    # ====================================================================

    def quick_train_from_scenario(
        self,
        scenario: str,
        project_id: int,
        model_config: Dict[str, Any] = None,
        n_samples: int = 1000,
    ) -> dict:
        """
        从业务场景一键完成: 生成数据 → 治理 → 训练 → 返回模型ID
        """
        from services.data_loader import generate_synthetic_data

        model_config = model_config or {}

        # 生成合成数据
        X, y, feature_names = generate_synthetic_data(scenario, n_samples)

        # 构造 rows
        rows = []
        for i in range(len(X)):
            row = {}
            for j, name in enumerate(feature_names):
                row[name] = float(X[i][j])
            row["target"] = float(y[i])
            rows.append(row)

        # 上传 & 治理
        upload_result = self.upload_and_govern(
            data=rows,
            project_id=project_id,
            name=f"{scenario}_{uuid.uuid4().hex[:6]}",
        )

        # 训练
        train_config = {
            "name": model_config.get("name", f"{scenario}_model"),
            "task_type": model_config.get("task_type", "regression"),
            "model_type": model_config.get("model_type", "random_forest"),
            "target_col": "target",
            "n_estimators": model_config.get("n_estimators", 100),
            "max_depth": model_config.get("max_depth", 6),
        }

        train_result = self.train_from_dataset(
            dataset_id=upload_result["governed_dataset_id"],
            model_config=train_config,
        )

        return {
            **upload_result,
            **train_result,
            "scenario": scenario,
            "feature_names": feature_names,
        }


# ========== 服务工厂 ==========

_pipeline: Optional[DataPipeline] = None


def get_data_pipeline(db: Session = None) -> DataPipeline:
    """获取数据管道实例"""
    global _pipeline
    if db:
        return DataPipeline(db)
    if _pipeline is None:
        from database.config import SessionLocal
        _pipeline = DataPipeline(SessionLocal())
    return _pipeline
