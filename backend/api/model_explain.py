"""
模型可解释性 API — 真实推理版
加载真实模型 → SHAP/LIME/特征重要性分析
"""
import io
import json
import os
from pathlib import Path
from typing import Any, List, Optional

import joblib
import numpy as np
import pandas as pd
from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from services.model_explain_service import get_model_explainer

router = APIRouter(prefix="/model-explain", tags=["模型可解释性"])


class SHAPExplainReq(BaseModel):
    model_id: int
    dataset_id: Optional[int] = None
    target_column: Optional[str] = None
    max_samples: int = 200


class LIMEExplainReq(BaseModel):
    model_id: int
    dataset_id: Optional[int] = None
    target_column: Optional[str] = None
    sample_idx: int = 0
    num_features: int = 10


class FeatureImportanceReq(BaseModel):
    model_id: int
    dataset_id: Optional[int] = None
    target_column: Optional[str] = None
    n_repeats: int = 5
    max_samples: int = 500


class PermutationImportanceReq(BaseModel):
    model_id: int
    dataset_id: int
    target_column: Optional[str] = None
    n_repeats: int = 5
    max_samples: int = 500


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _as_list(value: Any) -> Optional[list]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else None
        except (TypeError, ValueError):
            return None
    return None


def _version_metadata(db, model_id: int, file_path: Optional[str]) -> dict:
    row = db.execute(text("""
        SELECT parameters FROM model_versions
        WHERE model_id=:model_id ORDER BY id DESC LIMIT 1
    """), {"model_id": model_id}).fetchone()
    parameters = _as_dict(row[0]) if row else {}
    source_model_id = None

    if file_path and Path(file_path).suffix.lower() == ".onnx":
        manifest_path = Path(file_path).with_name("model-metadata.json")
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                source_metadata = _as_dict(manifest.get("source_metadata"))
                source_model_id = source_metadata.get("model_id")
            except (OSError, TypeError, ValueError):
                source_model_id = None

    source_parameters = {}
    if source_model_id:
        source_row = db.execute(text("""
            SELECT parameters FROM model_versions
            WHERE model_id=:model_id ORDER BY id DESC LIMIT 1
        """), {"model_id": int(source_model_id)}).fetchone()
        source_parameters = _as_dict(source_row[0]) if source_row else {}

    feature_names = _as_list(
        parameters.get("feature_cols") or parameters.get("feature_columns")
    ) or _as_list(
        source_parameters.get("feature_cols")
        or source_parameters.get("feature_columns")
    )
    target_column = (
        parameters.get("target_col")
        or parameters.get("target_column")
        or source_parameters.get("target_col")
        or source_parameters.get("target_column")
    )
    return {
        "feature_names": feature_names,
        "target_column": target_column or None,
        "source_model_id": int(source_model_id) if source_model_id else None,
    }


def _load_model(db, model_id: int):
    row = db.execute(text("""
        SELECT model_blob, model_type, framework FROM models WHERE id=:id
    """), {"id": model_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    version_row = db.execute(text("""
        SELECT file_path FROM model_versions
        WHERE model_id=:id ORDER BY id DESC LIMIT 1
    """), {"id": model_id}).fetchone()
    file_path = version_row[0] if version_row and version_row[0] else None
    metadata = _version_metadata(db, model_id, file_path)

    try:
        if file_path and os.path.exists(file_path) and Path(file_path).suffix.lower() == ".onnx":
            from api.model_evaluation import ONNXPredictor

            model = ONNXPredictor(file_path)
        elif row[0]:
            model = joblib.load(io.BytesIO(bytes(row[0])))
        elif file_path and os.path.exists(file_path):
            model = joblib.load(file_path)
        else:
            raise HTTPException(status_code=404, detail="模型文件不存在")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"模型文件加载失败：{type(exc).__name__}: {exc}",
        ) from exc

    try:
        model._foundation_feature_names = metadata["feature_names"]
        model._foundation_target_column = metadata["target_column"]
        model._foundation_source_model_id = metadata["source_model_id"]
        model._foundation_task_type = row[1] or "regression"
    except Exception:
        pass
    return model


def _model_feature_names(model) -> Optional[list[str]]:
    values = getattr(model, "_foundation_feature_names", None)
    if values:
        return [str(value) for value in values]
    values = getattr(model, "feature_names_in_", None)
    if values is not None:
        return [str(value) for value in list(values)]
    return None


def _load_data(
    db,
    dataset_id,
    target_column,
    max_rows=500,
    expected_feature_names: Optional[list[str]] = None,
    expected_feature_count: Optional[int] = None,
):
    row = db.execute(text("""
        SELECT file_path, columns_info FROM datasets WHERE id=:id
    """), {"id": dataset_id}).fetchone()
    if not row or not row[0] or not os.path.exists(row[0]):
        raise HTTPException(status_code=404, detail="数据集文件不存在")
    df = pd.read_csv(row[0])
    time_cols = [
        column for column in df.columns
        if "time" in column.lower() or "时间" in column.lower()
    ]
    skip = set(time_cols)

    derived_columns = set()
    columns_info = _as_list(row[1])
    if columns_info:
        derived_columns = {
            str(item.get("name")) for item in columns_info
            if isinstance(item, dict) and item.get("derived") is True
        }

    expected_feature_names = [
        str(column) for column in (expected_feature_names or [])
    ]
    if not target_column:
        numeric_candidates = [
            column for column in df.columns
            if pd.api.types.is_numeric_dtype(df[column])
            and column not in expected_feature_names
            and column not in time_cols
        ]
        preferred = [
            column for column in numeric_candidates
            if column.lower() in {"target", "label", "temperature", "y"}
            or "目标" in column
            or "温度" in column
        ]
        non_derived = [
            column for column in numeric_candidates
            if column not in derived_columns
        ]
        if preferred:
            target_column = preferred[0]
        elif len(non_derived) == 1:
            target_column = non_derived[0]
        elif len(numeric_candidates) == 1:
            target_column = numeric_candidates[0]
        else:
            raise HTTPException(
                status_code=400,
                detail="无法自动确定目标列，请在页面中选择需要预测的目标列",
            )
    if target_column not in df.columns:
        raise HTTPException(status_code=400, detail=f"目标列 '{target_column}' 不在数据集中")
    skip.add(target_column)

    if expected_feature_names:
        missing = [
            column for column in expected_feature_names if column not in df.columns
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"数据集缺少模型训练特征：{', '.join(missing)}",
            )
        non_numeric = [
            column for column in expected_feature_names
            if not pd.api.types.is_numeric_dtype(df[column])
        ]
        if non_numeric:
            raise HTTPException(
                status_code=400,
                detail=f"模型特征包含非数值列：{', '.join(non_numeric)}",
            )
        feature_cols = expected_feature_names
    else:
        feature_cols = [
            column for column in df.columns
            if column not in skip and pd.api.types.is_numeric_dtype(df[column])
        ]
    if not feature_cols:
        raise HTTPException(status_code=400, detail="未找到有效特征列")
    if expected_feature_count and len(feature_cols) != expected_feature_count:
        raise HTTPException(
            status_code=400,
            detail=(
                f"模型需要 {expected_feature_count} 个特征，但数据集筛选得到 "
                f"{len(feature_cols)} 个：{', '.join(feature_cols)}"
            ),
        )

    valid = df[target_column].notna()
    selected = df.loc[valid, feature_cols].copy()
    selected = selected.fillna(selected.mean(numeric_only=True))
    if selected.isna().any().any():
        raise HTTPException(status_code=400, detail="特征列存在无法填补的空值")
    X = selected.to_numpy(dtype=float)
    y = df.loc[valid, target_column].to_numpy()
    if len(X) > max_rows:
        idx = np.random.default_rng(42).choice(len(X), max_rows, replace=False)
        X, y = X[idx], y[idx]
    return X, y, feature_cols, target_column


@router.post("/shap")
def explain_with_shap(req: SHAPExplainReq, db=Depends(get_db)):
    explainer = get_model_explainer()
    if not explainer.SHAP_AVAILABLE:
        # SHAP 未安装:明确返回 501 + 提示,不再返回随机假数据(避免误导)
        raise HTTPException(
            status_code=501,
            detail={
                "error": "SHAP未安装",
                "available": False,
                "message": "SHAP 库未安装,无法提供真实归因分析。请联系管理员安装 `pip install shap`。",
            },
        )
    model = _load_model(db, req.model_id)
    expected_features = _model_feature_names(model)
    expected_count = getattr(model, "n_features_in_", None)
    target_column = req.target_column or getattr(
        model, "_foundation_target_column", None
    )
    if req.dataset_id:
        X, y, feature_names, target_column = _load_data(
            db, req.dataset_id, target_column, req.max_samples,
            expected_feature_names=expected_features,
            expected_feature_count=expected_count,
        )
        data_source = "real_dataset"
    else:
        # 未提供数据集:用合成随机数据演示(SHAP 算法真实,但输入非真实业务数据)
        feature_count = expected_count or len(expected_features or []) or 10
        X, y, feature_names = (
            np.random.randn(req.max_samples, feature_count), None,
            expected_features or [f"feature_{i}" for i in range(feature_count)]
        )
        data_source = "synthetic"

    result = explainer.explain_with_shap(model, X.tolist() if hasattr(X, 'tolist') else X, feature_names)
    if result.get("error"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": result["error"],
                "available": True,
                "message": result.get("message") or f"SHAP分析失败：{result['error']}",
            },
        )
    # 兜底:服务层异常返回了 mock 数据时,也明确标记
    if result.get("note", "").startswith("Mock"):
        raise HTTPException(
            status_code=501,
            detail={
                "error": "SHAP未安装",
                "available": False,
                "message": "SHAP 库未安装,无法提供真实归因分析。",
            },
        )
    result["model_id"] = req.model_id
    result["dataset_id"] = req.dataset_id
    result["feature_names"] = feature_names
    result["target_column"] = target_column
    result["n_samples"] = len(X)
    result["data_source"] = data_source
    if data_source == "synthetic":
        result["note"] = "未提供 dataset_id,使用合成随机数据演示;SHAP 归因为真实计算,但输入非真实业务数据"
    return result


@router.post("/lime")
def explain_with_lime(req: LIMEExplainReq, db=Depends(get_db)):
    explainer = get_model_explainer()
    if not explainer.LIME_AVAILABLE:
        # LIME 未安装:明确返回 501 + 提示,不再返回随机假数据(避免误导)
        raise HTTPException(
            status_code=501,
            detail={
                "error": "LIME未安装",
                "available": False,
                "message": "LIME 库未安装,无法提供真实局部解释。请联系管理员安装 `pip install lime`。",
            },
        )
    model = _load_model(db, req.model_id)
    expected_features = _model_feature_names(model)
    expected_count = getattr(model, "n_features_in_", None)
    target_column = req.target_column or getattr(
        model, "_foundation_target_column", None
    )
    if req.dataset_id:
        X, y, feature_names, target_column = _load_data(
            db, req.dataset_id, target_column, max_rows=300,
            expected_feature_names=expected_features,
            expected_feature_count=expected_count,
        )
        data_source = "real_dataset"
    else:
        # 未提供数据集:用合成随机数据演示(LIME 算法真实,但输入非真实业务数据)
        feature_count = expected_count or len(expected_features or []) or 10
        X = np.random.randn(100, feature_count)
        y = np.random.randn(100)
        feature_names = expected_features or [
            f"feature_{i}" for i in range(feature_count)
        ]
        data_source = "synthetic"

    result = explainer.explain_with_lime(model, X.tolist() if hasattr(X, 'tolist') else X,
                                          feature_names, req.num_features)
    if result.get("note", "").startswith("Mock"):
        raise HTTPException(
            status_code=501,
            detail={
                "error": "LIME未安装",
                "available": False,
                "message": "LIME 库未安装,无法提供真实局部解释。",
            },
        )
    result["model_id"] = req.model_id
    result["dataset_id"] = req.dataset_id
    result["feature_names"] = feature_names
    result["target_column"] = target_column
    result["data_source"] = data_source
    if data_source == "synthetic":
        result["note"] = "未提供 dataset_id,使用合成随机数据演示;LIME 局部解释为真实计算,但输入非真实业务数据"
    return result


@router.post("/feature-importance")
def get_feature_importance(req: FeatureImportanceReq, db=Depends(get_db)):
    model = _load_model(db, req.model_id)
    row = db.execute(text("""
        SELECT name, model_type FROM models WHERE id=:id
    """), {"id": req.model_id}).fetchone()
    feature_names = _model_feature_names(model)
    expected_count = getattr(model, "n_features_in_", None)
    target_column = req.target_column or getattr(
        model, "_foundation_target_column", None
    )

    explainer = get_model_explainer()
    result = explainer.get_feature_importance(model, feature_names)
    if result.get("available") is False:
        if not req.dataset_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "模型无内置特征重要性",
                    "message": (
                        "该模型需要结合数据集计算排列特征重要性，"
                        "请选择数据集和目标列后重试"
                    ),
                },
            )
        X, y, feature_names, target_column = _load_data(
            db, req.dataset_id, target_column, req.max_samples,
            expected_feature_names=feature_names,
            expected_feature_count=expected_count,
        )
        try:
            permutation = explainer.get_permutation_importance(
                model,
                X.tolist(),
                y.tolist(),
                feature_names,
                req.n_repeats,
                task_type=getattr(model, "_foundation_task_type", row[1] if row else None),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"排列特征重要性计算失败：{exc}",
            ) from exc
        result = {
            **permutation,
            "feature_importance": [
                {**item, "rank": index + 1}
                for index, item in enumerate(permutation["results"])
            ],
            "total_features": len(feature_names),
            "data_source": "real_dataset",
            "dataset_id": req.dataset_id,
            "target_column": target_column,
            "note": "模型无内置重要性属性，已使用所选数据集计算排列特征重要性。",
        }
    result["model_id"] = req.model_id
    result["model_name"] = row[0] if row else None
    result["feature_names"] = feature_names
    return result


@router.post("/permutation-importance")
def get_permutation_importance(req: PermutationImportanceReq, db=Depends(get_db)):
    model = _load_model(db, req.model_id)
    feature_names = _model_feature_names(model)
    target_column = req.target_column or getattr(
        model, "_foundation_target_column", None
    )
    X, y, feature_names, target_column = _load_data(
        db, req.dataset_id, target_column, req.max_samples,
        expected_feature_names=feature_names,
        expected_feature_count=getattr(model, "n_features_in_", None),
    )
    explainer = get_model_explainer()
    try:
        result = explainer.get_permutation_importance(
            model, X.tolist() if hasattr(X, 'tolist') else X,
            y.tolist() if hasattr(y, 'tolist') else y,
            feature_names, req.n_repeats,
            task_type=getattr(model, "_foundation_task_type", None),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"排列特征重要性计算失败：{exc}",
        ) from exc
    result["model_id"] = req.model_id
    result["dataset_id"] = req.dataset_id
    result["target_column"] = target_column
    result["feature_names"] = feature_names
    return result


@router.get("/methods")
def get_available_methods():
    explainer = get_model_explainer()
    return {
        "methods": [
            {"name": "SHAP", "description": "SHapley Additive exPlanations — 全局特征归因",
             "available": explainer.SHAP_AVAILABLE, "type": "global"},
            {"name": "LIME", "description": "Local Interpretable Model-agnostic Explanations — 单样本局部解释",
             "available": explainer.LIME_AVAILABLE, "type": "local"},
            {"name": "feature_importance", "description": "模型内置特征重要性",
             "available": True, "type": "global"},
            {"name": "permutation_importance", "description": "排列特征重要性",
             "available": True, "type": "global"},
        ]
    }
