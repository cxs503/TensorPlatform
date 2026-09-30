"""
轻量数据校验器 (Great Expectations 式, 但不依赖 GE 库)。

用 pandas / numpy 自己算, 输出结构化校验报告, 供训练前 gate 与前端独立调用。

主要入口:
  - validate_dataset(df, target_col) -> {passed, checks, summary}
  - profile_dataset(df)              -> {n_rows, n_cols, columns: [...]}
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ── 哨兵值: 工业传感器/SCADA 常见的"缺失占位"标记 ──
SENTINEL_VALUES = [-999, -9999, 9999, -99999, 99999, -999.0, -9999.0, 9999.0]

# ── 阈值 (可在调用处覆盖) ──
DEFAULT_THRESHOLDS = {
    "max_missing_rate": 0.5,       # 单列缺失率 > 50% 视为严重 (target 列硬性拒绝)
    "warn_missing_rate": 0.2,      # 缺失率 > 20% 给警告
    "max_outlier_rate": 0.05,      # Z-score>3 占比 > 5% 视为异常
    "min_rows": 10,                # 至少 10 行才能训练
    "constant_std_eps": 1e-8,      # std < 此值视为常量列
    "z_threshold": 3.0,            # 异常值 Z-score 阈值
}


def _safe_float(x: Any) -> Optional[float]:
    """转 float; NaN/inf/None -> None (JSON 安全)"""
    try:
        if x is None:
            return None
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _check_missing_rate(df: pd.DataFrame, thresholds: Dict) -> Dict:
    """检查①: 每列缺失率 (null / NaN)"""
    n = len(df)
    col_rates = {}
    for c in df.columns:
        rate = float(df[c].isna().sum()) / n if n else 0.0
        col_rates[str(c)] = round(rate, 4)
    max_rate = max(col_rates.values()) if col_rates else 0.0
    passed = max_rate <= thresholds["max_missing_rate"]
    return {
        "name": "missing_rate",
        "passed": passed,
        "value": round(max_rate, 4),
        "threshold": thresholds["max_missing_rate"],
        "detail": {
            "per_column": col_rates,
            "warn_threshold": thresholds["warn_missing_rate"],
            "high_missing_columns": {
                k: v for k, v in col_rates.items()
                if v > thresholds["warn_missing_rate"]
            },
        },
    }


def _check_sentinel_values(df: pd.DataFrame, thresholds: Dict) -> Dict:
    """检查②: 哨兵值检测 (-999/9999 等工业占位值)"""
    numeric_df = df.select_dtypes(include=[np.number])
    col_counts: Dict[str, int] = {}
    total = 0
    for c in numeric_df.columns:
        cnt = int(numeric_df[c].isin(SENTINEL_VALUES).sum())
        if cnt:
            col_counts[str(c)] = cnt
            total += cnt
    # 哨兵值存在即报警 (无硬阈值, 但占比>1% 更严重)
    n_cells = numeric_df.shape[0] * numeric_df.shape[1] if numeric_df.shape[1] else 1
    ratio = total / n_cells if n_cells else 0.0
    passed = total == 0
    return {
        "name": "sentinel_values",
        "passed": passed,
        "value": total,
        "threshold": 0,
        "detail": {
            "sentinel_values_checked": SENTINEL_VALUES,
            "total_sentinel_cells": total,
            "ratio": round(ratio, 4),
            "per_column": col_counts,
        },
    }


def _check_outliers(df: pd.DataFrame, thresholds: Dict) -> Dict:
    """检查③: 异常值 (Z-score > threshold 的比例, 逐数值列)"""
    numeric_df = df.select_dtypes(include=[np.number])
    z = thresholds["z_threshold"]
    per_col: Dict[str, float] = {}
    for c in numeric_df.columns:
        s = numeric_df[c].dropna()
        if len(s) < 2:
            per_col[str(c)] = 0.0
            continue
        std = s.std()
        if std is None or std < thresholds["constant_std_eps"]:
            per_col[str(c)] = 0.0
            continue
        zscores = (s - s.mean()).abs() / std
        per_col[str(c)] = round(float((zscores > z).mean()), 4)
    max_rate = max(per_col.values()) if per_col else 0.0
    passed = max_rate <= thresholds["max_outlier_rate"]
    return {
        "name": "outliers_zscore",
        "passed": passed,
        "value": round(max_rate, 4),
        "threshold": thresholds["max_outlier_rate"],
        "detail": {
            "z_threshold": z,
            "per_column_outlier_rate": per_col,
            "high_outlier_columns": {
                k: v for k, v in per_col.items() if v > thresholds["max_outlier_rate"]
            },
        },
    }


def _check_target_distribution(
    df: pd.DataFrame, target_col: Optional[str], thresholds: Dict
) -> Dict:
    """检查④: 目标列分布 (均值/标准差/范围/缺失)"""
    if not target_col or target_col not in df.columns:
        return {
            "name": "target_distribution",
            "passed": False,
            "value": None,
            "threshold": None,
            "detail": {"error": f"目标列 '{target_col}' 不存在"},
        }
    s = df[target_col]
    n = len(s)
    n_missing = int(s.isna().sum())
    missing_rate = n_missing / n if n else 0.0
    detail: Dict[str, Any] = {
        "column": target_col,
        "count": int(n),
        "missing": n_missing,
        "missing_rate": round(missing_rate, 4),
    }
    # 数值目标: 给统计量
    numeric = pd.to_numeric(s, errors="coerce")
    valid = numeric.dropna()
    if len(valid):
        detail.update({
            "mean": _safe_float(valid.mean()),
            "std": _safe_float(valid.std()),
            "min": _safe_float(valid.min()),
            "max": _safe_float(valid.max()),
            "median": _safe_float(valid.median()),
            "q25": _safe_float(valid.quantile(0.25)),
            "q75": _safe_float(valid.quantile(0.75)),
        })
        # std≈0 → 目标常量, 无法回归
        std = valid.std()
        if std is not None and std < thresholds["constant_std_eps"]:
            detail["warning"] = "目标列标准差≈0, 可能是常量列, 回归无意义"
            passed = False
        else:
            passed = True
    else:
        detail["error"] = "目标列无数值数据"
        passed = False
    # 缺失率硬门槛: > 50% 拒绝
    if missing_rate > thresholds["max_missing_rate"]:
        passed = False
        detail["fatal"] = (
            f"目标列缺失率 {missing_rate*100:.1f}% > "
            f"{thresholds['max_missing_rate']*100:.0f}%, 拒绝训练"
        )
    return {
        "name": "target_distribution",
        "passed": passed,
        "value": round(missing_rate, 4),
        "threshold": thresholds["max_missing_rate"],
        "detail": detail,
    }


def _check_shape(df: pd.DataFrame, thresholds: Dict) -> Dict:
    """检查⑤: 特征数 / 行数"""
    n_rows, n_cols = df.shape
    passed = n_rows >= thresholds["min_rows"] and n_cols >= 2
    return {
        "name": "dataset_shape",
        "passed": passed,
        "value": {"rows": int(n_rows), "cols": int(n_cols)},
        "threshold": {
            "min_rows": thresholds["min_rows"],
            "min_cols": 2,
        },
        "detail": {},
    }


def _check_constant_columns(df: pd.DataFrame, thresholds: Dict) -> Dict:
    """检查⑥: 常量列 (std≈0, 无信息量)"""
    numeric_df = df.select_dtypes(include=[np.number])
    consts: List[str] = []
    for c in numeric_df.columns:
        s = numeric_df[c].dropna()
        if len(s) < 2:
            continue
        std = s.std()
        if std is not None and std < thresholds["constant_std_eps"]:
            consts.append(str(c))
    # 非数值列若唯一值只有 1 个, 也算常量
    for c in df.columns:
        if df[c].dtype.kind in "OUSb":  # object/string/bytes
            if df[c].nunique(dropna=True) <= 1:
                consts.append(str(c))
    passed = len(consts) == 0
    return {
        "name": "constant_columns",
        "passed": passed,
        "value": len(consts),
        "threshold": 0,
        "detail": {"constant_columns": consts},
    }


def _check_duplicates(df: pd.DataFrame, thresholds: Dict) -> Dict:
    """检查⑦: 完全重复行"""
    n_dup = int(df.duplicated().sum())
    n = len(df)
    rate = n_dup / n if n else 0.0
    # 重复率 > 10% 报警
    passed = rate <= 0.1
    return {
        "name": "duplicate_rows",
        "passed": passed,
        "value": n_dup,
        "threshold": int(n * 0.1) if n else 0,
        "detail": {
            "duplicate_count": n_dup,
            "duplicate_rate": round(rate, 4),
        },
    }


def validate_dataset(
    df: pd.DataFrame,
    target_col: Optional[str] = None,
    thresholds: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    对 DataFrame 跑全套数据质量校验。

    Args:
        df: 待校验数据 (pandas DataFrame)
        target_col: 目标列名 (可选; 指定则额外检查目标分布)
        thresholds: 覆盖默认阈值

    Returns:
        {
            "passed": bool,        # 所有 critical 检查通过?
            "checks": [...],       # 每项检查结果
            "summary": {...},      # 摘要
            "target_column": str,
        }
    """
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update(thresholds)

    checks: List[Dict] = [
        _check_shape(df, th),
        _check_missing_rate(df, th),
        _check_sentinel_values(df, th),
        _check_outliers(df, th),
        _check_constant_columns(df, th),
        _check_duplicates(df, th),
        _check_target_distribution(df, target_col, th),
    ]

    # critical 检查 (任一失败 → passed=False)
    critical_names = {
        "dataset_shape",
        "missing_rate",
        "target_distribution",
    }
    failed_critical = [c["name"] for c in checks if c["name"] in critical_names and not c["passed"]]
    overall_passed = len(failed_critical) == 0

    n_passed = sum(1 for c in checks if c["passed"])
    summary = {
        "total_checks": len(checks),
        "passed_checks": n_passed,
        "failed_checks": len(checks) - n_passed,
        "failed_critical": failed_critical,
        "n_rows": int(df.shape[0]),
        "n_cols": int(df.shape[1]),
    }
    return {
        "passed": overall_passed,
        "checks": checks,
        "summary": summary,
        "target_column": target_col,
    }


def profile_dataset(df: pd.DataFrame) -> Dict[str, Any]:
    """
    数据 profiling — 类似 pandas.describe, 逐列给统计量。

    Returns:
        {
            "n_rows": int,
            "n_cols": int,
            "memory_mb": float,
            "columns": [
                {
                    "name", "dtype", "count", "missing", "missing_rate",
                    "unique", "n_unique",
                    # 数值列额外:
                    "min", "max", "mean", "std", "median", "q25", "q75",
                    # 非数值列额外:
                    "top", "top_freq",
                },
                ...
            ],
        }
    """
    n_rows, n_cols = df.shape
    cols: List[Dict[str, Any]] = []
    for c in df.columns:
        s = df[c]
        n_missing = int(s.isna().sum())
        info: Dict[str, Any] = {
            "name": str(c),
            "dtype": str(s.dtype),
            "count": int(s.count()),
            "missing": n_missing,
            "missing_rate": round(n_missing / n_rows, 4) if n_rows else 0.0,
            "n_unique": int(s.nunique(dropna=True)),
        }
        if s.dtype.kind in "iufc":  # 数值 (int/uint/float/complex)
            valid = s.dropna()
            if len(valid):
                info.update({
                    "min": _safe_float(valid.min()),
                    "max": _safe_float(valid.max()),
                    "mean": _safe_float(valid.mean()),
                    "std": _safe_float(valid.std()),
                    "median": _safe_float(valid.median()),
                    "q25": _safe_float(valid.quantile(0.25)),
                    "q75": _safe_float(valid.quantile(0.75)),
                })
                # 哨兵值计数
                sentinel_cnt = int(valid.isin(SENTINEL_VALUES).sum())
                if sentinel_cnt:
                    info["sentinel_count"] = sentinel_cnt
        else:
            vc = s.value_counts(dropna=True)
            if len(vc):
                info.update({
                    "top": str(vc.index[0]),
                    "top_freq": int(vc.iloc[0]),
                })
        cols.append(info)

    mem = df.memory_usage(deep=True).sum() / (1024 * 1024)
    return {
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "memory_mb": round(float(mem), 3),
        "columns": cols,
    }
