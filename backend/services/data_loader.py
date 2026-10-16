"""
数据加载器 - 从数据库、CSV文件或内存中加载训练数据
"""
import csv
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

UPLOADS_DIR = Path("/app/uploads")
DATASETS_DIR = UPLOADS_DIR / "datasets"


def load_dataset_data(
    db: Session, dataset_id: int, feature_cols: Optional[List[str]] = None,
    target_col: Optional[str] = None,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    从数据集加载特征和标签。
    返回: (X, y, feature_names)
    """
    # 1. 尝试从 CSV 文件加载
    # 用 SQL 直查避免 models.core 重复导入触发 SQLAlchemy 表重定义
    row = db.execute(
        text("SELECT id, name, file_path FROM datasets WHERE id = :did"),
        {"did": dataset_id}
    ).fetchone()
    if not row:
        raise ValueError(f"Dataset {dataset_id} not found")
    
    file_path = row[2]
    if file_path and os.path.exists(file_path):
        df = pd.read_csv(file_path)
    else:
        # 查找 uploads/datasets/ 下的 CSV 文件
        pattern = f"{dataset_id}_*.csv"
        csv_files = sorted(DATASETS_DIR.glob(pattern))
        if csv_files:
            df = pd.read_csv(csv_files[0])
        else:
            raise ValueError(
                f"No data file found for dataset {dataset_id}. "
                f"Upload a CSV file first."
            )

    if len(df) < 10:
        raise ValueError(
            f"Dataset {dataset_id} has only {len(df)} rows. "
            f"Need at least 10 rows for training."
        )

    # 智能选择列
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        raise ValueError(f"Dataset {dataset_id} has no numeric columns")

    # 确定目标列 y
    if target_col and target_col in df.columns:
        y = df[target_col].values
    elif feature_cols:
        raise ValueError(
            f"Dataset {dataset_id}: 指定了 feature_cols 但未提供有效的 target_col"
        )
    elif len(numeric_cols) >= 2:
        # 未指定 target:从后往前找第一个不全为 NaN 的数值列作 target
        target_idx = -1
        for i in range(len(numeric_cols) - 1, -1, -1):
            col = numeric_cols[i]
            if df[col].notna().sum() > 0:
                target_idx = i
                break
        if target_idx < 0:
            raise ValueError(f"Dataset {dataset_id}: all numeric columns are NaN")
        target_col = numeric_cols[target_idx]
        y = df[target_col].values
    else:
        target_col = numeric_cols[0]
        y = df[target_col].values

    # 确定特征列 X
    if feature_cols:
        X_cols = [c for c in feature_cols if c in df.columns and c != target_col]
        if not X_cols:
            raise ValueError(
                f"Dataset {dataset_id}: 指定的特征列均不可用: {feature_cols}"
            )
    else:
        X_cols = [c for c in numeric_cols if c != target_col]
        if not X_cols:
            X_cols = numeric_cols

    # 工业数据哨兵值清洗:将 -999/-9999/9999 等占位值替换为 NaN
    # (这些是工业传感器/SCADA 系统的"缺失数据"标记,直接喂入模型会导致负 R²)
    _SENTINELS = [-999, -9999, 9999, -99999, 99999, -999.0, -9999.0, 9999.0]
    for _col in numeric_cols:
        df[_col] = df[_col].replace(_SENTINELS, np.nan)

    # 去除 target 为 NaN 的行
    y_series = pd.Series(y)
    numeric_y = pd.to_numeric(y_series, errors="coerce")
    non_numeric_mask = y_series.notna() & numeric_y.isna()
    if non_numeric_mask.any():
        invalid_examples = y_series[non_numeric_mask].astype(str).head(3).tolist()
        raise ValueError(
            f"Dataset {dataset_id}: target column {target_col!r} contains non-numeric values "
            f"(examples: {invalid_examples}). Please select a numeric target column."
        )
    y = numeric_y.to_numpy(dtype=np.float64)
    valid_mask = numeric_y.notna() & np.isfinite(numeric_y)
    if not valid_mask.all():
        n_before = len(y)
        X = df[X_cols].loc[valid_mask[valid_mask].index].fillna(0).values.astype(np.float64)
        y = y[valid_mask.values]
        if len(y) < 10:
            raise ValueError(f"Dataset {dataset_id}: only {len(y)} valid rows after dropping NaN")
    else:
        X = df[X_cols].fillna(0).values.astype(np.float64)
    y = np.array(y, dtype=np.float64)

    return X, y, X_cols


def generate_synthetic_data(
    scenario: str, n_samples: int = 1000,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    生成通用合成回归数据（demo/测试 fallback）。

    2026-10-16 架构收敛：移除光伏/能耗/负荷/除尘/钢铁等行业场景，
    平台只保留领域中立的合成数据生成；行业数据由使用方自行准备。
    """
    np.random.seed(42)

    # 通用场景：生成随机回归数据
    X = np.random.randn(n_samples, 5)
    true_coef = np.array([1.5, -0.8, 2.0, 0.3, -1.2])
    y = X @ true_coef + np.random.normal(0, 0.5, n_samples)
    feature_names = [f"feature_{i}" for i in range(5)]

    return X, y, feature_names


def persist_synthetic_data(
    db: Session,
    scenario: str,
    X: np.ndarray,
    y: np.ndarray,
    feature_names: List[str],
    entity_id: str = None,
) -> int:
    """
    合成数据持久化入口。

    2026-10-16 架构收敛：行业业务表（烧结/除尘等）已随模型一并移除，
    通用合成数据不再写入业务表；保留函数签名以兼容既有调用方。
    返回 INSERT 记录数（恒为 0）。
    """
    return 0


def get_training_data(
    db: Session, dataset_id: Optional[int] = None,
    scenario: Optional[str] = None,
    feature_cols: Optional[List[str]] = None,
    target_col: Optional[str] = None,
    n_samples: int = 1000,
    persist_to_db: bool = False,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    统一入口：获取训练数据。
    优先使用 dataset_id，其次 scenario 生成合成数据。
    persist_to_db 参数保留以兼容调用方；通用合成数据不再落业务表。
    """
    if dataset_id:
        try:
            return load_dataset_data(db, dataset_id, feature_cols, target_col)
        except ValueError as e:
            # 数据集无法加载，回退到合成数据
            if scenario:
                print(f"Warning: Cannot load dataset {dataset_id}: {e}. Using synthetic data.")
                X, y, names = generate_synthetic_data(scenario, n_samples)
                if persist_to_db:
                    persist_synthetic_data(db, scenario, X, y, names)
                return X, y, names
            raise

    if scenario:
        X, y, names = generate_synthetic_data(scenario, n_samples)
        if persist_to_db:
            persist_synthetic_data(db, scenario, X, y, names)
        return X, y, names

    return generate_synthetic_data("generic", n_samples)
