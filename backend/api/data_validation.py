"""
数据校验 API — 训练前 Great Expectations 式数据质量校验 + 数据 profiling。

端点:
  POST /api/v1/data-validation/validate    传 dataset_id + target_column, 返回校验报告
  GET  /api/v1/data-validation/profile/{dataset_id}  返回列统计 (pandas.describe 式)
  GET  /api/v1/data-validation/health      健康检查

不依赖 Great Expectations 库; 校验逻辑在 services/data_validator.py (pandas/numpy 自实现)。
"""
import os
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.config import get_db
from services.data_validator import validate_dataset, profile_dataset

router = APIRouter(prefix="/data-validation", tags=["数据校验"])

UPLOADS_DIR = Path("/app/uploads")
DATASETS_DIR = UPLOADS_DIR / "datasets"


class ValidateRequest(BaseModel):
    """数据校验请求"""
    dataset_id: int
    target_column: Optional[str] = None  # 为空则只做通用检查, 不检查目标分布
    # 可选阈值覆盖 (留空用默认)
    max_missing_rate: Optional[float] = None
    max_outlier_rate: Optional[float] = None
    min_rows: Optional[int] = None


def _load_dataset_df(db: Session, dataset_id: int) -> pd.DataFrame:
    """从数据集加载 DataFrame (与 data_loader.load_dataset_data 一致的文件查找逻辑)。"""
    row = db.execute(
        text("SELECT id, name, file_path FROM datasets WHERE id = :did"),
        {"did": dataset_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"数据集 {dataset_id} 不存在")

    file_path = row[2]
    if file_path and os.path.exists(file_path):
        try:
            return pd.read_csv(file_path)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"数据集 {dataset_id} CSV 解析失败: {e}",
            )
    # 回退: uploads/datasets/{id}_*.csv
    csv_files = sorted(DATASETS_DIR.glob(f"{dataset_id}_*.csv"))
    if csv_files:
        try:
            return pd.read_csv(csv_files[0])
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"数据集 {dataset_id} CSV 解析失败: {e}",
            )
    raise HTTPException(
        status_code=404,
        detail=f"数据集 {dataset_id} 无数据文件 (file_path 为空且 uploads/datasets/{dataset_id}_*.csv 不存在)",
    )


@router.post("/validate")
def validate_dataset_endpoint(req: ValidateRequest, db: Session = Depends(get_db)):
    """
    对指定数据集跑数据质量校验 (不训练)。

    返回结构:
      {
        "dataset_id": 56,
        "target_column": "终点平均温度",
        "passed": true,
        "checks": [{name, passed, value, threshold, detail}, ...],
        "summary": {total_checks, passed_checks, failed_checks, failed_critical, ...}
      }
    """
    df = _load_dataset_df(db, req.dataset_id)

    # 校验目标列存在
    if req.target_column and req.target_column not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=(
                f"目标列 '{req.target_column}' 不在数据集中。"
                f" 可用列: {list(df.columns)}"
            ),
        )

    # 可选阈值覆盖
    thresholds = {}
    if req.max_missing_rate is not None:
        thresholds["max_missing_rate"] = req.max_missing_rate
    if req.max_outlier_rate is not None:
        thresholds["max_outlier_rate"] = req.max_outlier_rate
    if req.min_rows is not None:
        thresholds["min_rows"] = req.min_rows

    report = validate_dataset(df, target_col=req.target_column, thresholds=thresholds or None)
    report["dataset_id"] = req.dataset_id
    report["dataset_name"] = None
    # 回填数据集名
    try:
        row = db.execute(
            text("SELECT name FROM datasets WHERE id = :did"),
            {"did": req.dataset_id},
        ).fetchone()
        if row:
            report["dataset_name"] = row[0]
    except Exception:
        pass
    return report


@router.get("/profile/{dataset_id}")
def profile_dataset_endpoint(dataset_id: int, db: Session = Depends(get_db)):
    """
    数据 profiling — 逐列统计 (类型/缺失/唯一值/min/max/mean/std/median/分位数)。

    类似 pandas.describe, 但结构化为 JSON 供前端渲染。
    """
    df = _load_dataset_df(db, dataset_id)
    profile = profile_dataset(df)
    profile["dataset_id"] = dataset_id
    # 回填数据集名
    try:
        row = db.execute(
            text("SELECT name FROM datasets WHERE id = :did"),
            {"did": dataset_id},
        ).fetchone()
        if row:
            profile["dataset_name"] = row[0]
    except Exception:
        pass
    return profile


@router.get("/health")
def health():
    """健康检查"""
    return {"status": "healthy", "service": "data-validation"}
