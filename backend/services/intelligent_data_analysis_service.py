"""Local, deterministic profiling used by the LLM-assisted data analyst."""
from __future__ import annotations

import json
import math
import re
from typing import Any

import numpy as np
import pandas as pd


MAX_PROFILE_ROWS = 200_000
MAX_LLM_COLUMNS = 30
SENSITIVE_NAME_PATTERN = re.compile(
    r"(^|_)(id|name|phone|mobile|email|address|password|token|secret|身份证|姓名|电话|手机|邮箱|地址)($|_)",
    re.IGNORECASE,
)


def _safe(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else round(float(value), 6)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return str(value) if not isinstance(value, (str, int)) else value


def _kind(series: pd.Series) -> str:
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    return "categorical"


def detect_time_column(frame: pd.DataFrame, requested: str | None = None) -> str | None:
    if requested and requested in frame.columns:
        return requested
    for column in frame.columns:
        if str(column).lower() in {"timestamp", "time", "datetime", "date", "时间", "日期"}:
            return str(column)
    for column in frame.columns:
        if frame[column].dtype == object:
            converted = pd.to_datetime(frame[column].dropna().head(200), errors="coerce")
            if len(converted) >= 3 and converted.notna().mean() >= 0.9:
                return str(column)
    return None


def profile_frame(
    frame: pd.DataFrame,
    *,
    source_name: str,
    total_rows: int | None = None,
    time_column: str | None = None,
    target_column: str | None = None,
) -> dict[str, Any]:
    if frame.empty:
        raise ValueError("数据集没有可分析的数据行")
    if len(frame.columns) == 0:
        raise ValueError("数据集没有字段")
    original_profiled_rows = len(frame)
    truncated = original_profiled_rows > MAX_PROFILE_ROWS
    if truncated:
        frame = frame.iloc[:MAX_PROFILE_ROWS].copy()
    frame.columns = [str(column) for column in frame.columns]
    profiled_rows = len(frame)
    reported_total = int(total_rows or original_profiled_rows)
    numeric_columns = frame.select_dtypes(include=[np.number]).columns.tolist()
    categorical_columns = [column for column in frame.columns if column not in numeric_columns]
    detected_time = detect_time_column(frame, time_column)

    columns = []
    missing_chart = []
    anomalies = []
    category_charts = []
    distribution_charts = []
    for column in frame.columns:
        series = frame[column]
        missing_count = int(series.isna().sum())
        missing_rate = round(missing_count / max(profiled_rows, 1) * 100, 4)
        sensitive = bool(SENSITIVE_NAME_PATTERN.search(str(column)))
        item: dict[str, Any] = {
            "name": str(column),
            "dtype": str(series.dtype),
            "kind": _kind(series),
            "missing_count": missing_count,
            "missing_rate": missing_rate,
            "unique_count": int(series.nunique(dropna=True)),
            "sensitive": sensitive,
        }
        missing_chart.append({"name": str(column), "value": missing_rate})
        if column in numeric_columns:
            numeric = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            if len(numeric):
                q1, median, q3 = numeric.quantile([0.25, 0.5, 0.75]).tolist()
                iqr = q3 - q1
                lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                anomaly_count = int(((numeric < lower) | (numeric > upper)).sum()) if iqr > 0 else 0
                item["statistics"] = {
                    "count": int(len(numeric)), "mean": _safe(numeric.mean()),
                    "std": _safe(numeric.std()), "min": _safe(numeric.min()),
                    "q1": _safe(q1), "median": _safe(median), "q3": _safe(q3),
                    "max": _safe(numeric.max()), "anomaly_count": anomaly_count,
                    "anomaly_rate": round(anomaly_count / max(len(numeric), 1) * 100, 4),
                }
                anomalies.append({"name": str(column), "value": item["statistics"]["anomaly_rate"]})
                if len(distribution_charts) < 8:
                    counts, edges = np.histogram(numeric.to_numpy(dtype=float), bins=min(20, max(5, int(math.sqrt(len(numeric))))))
                    distribution_charts.append({
                        "column": str(column), "counts": counts.astype(int).tolist(),
                        "bins": [round(float(value), 6) for value in edges.tolist()],
                    })
        elif not sensitive and len(category_charts) < 6:
            counts = series.dropna().astype(str).value_counts().head(10)
            category_charts.append({
                "column": str(column), "labels": counts.index.tolist(),
                "values": counts.astype(int).tolist(),
            })
        columns.append(item)

    correlation = None
    corr_columns = numeric_columns[:15]
    if len(corr_columns) >= 2:
        matrix = frame[corr_columns].corr().replace([np.inf, -np.inf], np.nan)
        correlation = {
            "columns": [str(column) for column in corr_columns],
            "matrix": [[_safe(value) for value in row] for row in matrix.to_numpy().tolist()],
        }

    time_series = None
    if detected_time and detected_time in frame.columns:
        parsed_time = pd.to_datetime(frame[detected_time], errors="coerce")
        valid = frame.loc[parsed_time.notna()].copy()
        valid[detected_time] = parsed_time[parsed_time.notna()]
        valid = valid.sort_values(detected_time)
        if len(valid):
            stride = max(1, math.ceil(len(valid) / 500))
            sampled = valid.iloc[::stride]
            series_columns = [column for column in numeric_columns if column != detected_time][:4]
            time_series = {
                "time_column": detected_time,
                "timestamps": [value.isoformat() for value in sampled[detected_time]],
                "series": [
                    {"name": str(column), "values": [_safe(value) for value in sampled[column].tolist()]}
                    for column in series_columns
                ],
            }

    total_missing = sum(item["missing_count"] for item in columns)
    return {
        "source_name": source_name,
        "total_rows": reported_total,
        "profiled_rows": profiled_rows,
        "profile_truncated": truncated or reported_total > profiled_rows,
        "total_columns": len(frame.columns),
        "numeric_columns": [str(column) for column in numeric_columns],
        "categorical_columns": [str(column) for column in categorical_columns],
        "time_column": detected_time,
        "target_column": target_column if target_column in frame.columns else None,
        "missing_cells": int(total_missing),
        "missing_rate": round(total_missing / max(profiled_rows * len(frame.columns), 1) * 100, 4),
        "duplicate_rows": int(frame.duplicated().sum()),
        "columns": columns,
        "charts": {
            "missing": sorted(missing_chart, key=lambda item: item["value"], reverse=True)[:30],
            "anomalies": sorted(anomalies, key=lambda item: item["value"], reverse=True)[:20],
            "distributions": distribution_charts,
            "categories": category_charts,
            "correlation": correlation,
            "time_series": time_series,
        },
    }


def llm_summary(profile: dict[str, Any]) -> dict[str, Any]:
    columns = []
    for item in profile["columns"][:MAX_LLM_COLUMNS]:
        columns.append({
            "name": item["name"], "kind": item["kind"],
            "missing_rate": item["missing_rate"], "unique_count": item["unique_count"],
            "sensitive": item["sensitive"], "statistics": item.get("statistics"),
        })
    payload = {
        "source_name": profile["source_name"], "total_rows": profile["total_rows"],
        "profiled_rows": profile["profiled_rows"], "total_columns": profile["total_columns"],
        "missing_rate": profile["missing_rate"], "duplicate_rows": profile["duplicate_rows"],
        "detected_time_column": profile["time_column"],
        "columns": columns,
    }
    return payload


def local_narrative(profile: dict[str, Any]) -> str:
    return (
        f"数据集共 {profile['total_rows']} 行、{profile['total_columns']} 列，"
        f"本次分析 {profile['profiled_rows']} 行。整体缺失率 {profile['missing_rate']}%，"
        f"重复行 {profile['duplicate_rows']} 条。"
    )


def build_llm_prompt(summary: dict[str, Any]) -> str:
    return (
        "你是工业数据分析助手。以下内容是平台本地计算出的真实统计摘要，不要虚构未提供的数据。\n"
        "当前没有指定业务场景、预测目标或算法，不要自行假设这些信息。"
        "请用中文输出结构清晰的数据分析报告，包含：数据概览、数据类型、质量问题、"
        "主要分布或关联、时序特征（如自动识别到时间字段）、清洗与数据整理建议。"
        "明确区分统计事实和处理建议，不要推荐具体预测模型。\n"
        + json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    )
