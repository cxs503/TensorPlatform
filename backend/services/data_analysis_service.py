"""Deterministic table/time-series analysis algorithms for the data platform."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from services import drift_metrics
import pandas as pd
from scipy import signal, stats
from scipy.linalg import toeplitz
from sklearn.decomposition import PCA
from sklearn.cross_decomposition import CCA
from sklearn.ensemble import IsolationForest
from sklearn.feature_selection import mutual_info_regression
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from services.intelligent_data_analysis_service import detect_time_column, profile_frame

MAX_ROWS = 200_000
MAX_NUMERIC_COLUMNS = 100
SUPPORTED_ANALYSES = {
    "overview", "distribution", "correlation", "anomaly",
    "trend", "lag", "change_point", "pca",
    "normality", "acf_pacf", "cross_correlation", "spectrum", "filtering",
    "time_quality", "mutual_information", "time_drift", "hampel", "sensor_health", "operating_segments", "window_quality", "effective_excitation", "cca", "dynamic_regression", "sensor_consistency", "operation_impact", "anomaly_attribution", "period_impact_attribution",
}


def _safe(value: Any, digits: int = 6):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return round(number, digits) if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value if isinstance(value, (str, int)) else str(value)


def _numeric_frame(frame: pd.DataFrame, columns: list[str] | None) -> tuple[pd.DataFrame, list[str]]:
    available = list(frame.select_dtypes(include=[np.number]).columns)
    selected = [column for column in (columns or available) if column in available][:MAX_NUMERIC_COLUMNS]
    if not selected:
        raise ValueError("数据集中没有可分析的数值字段")
    numeric = frame[selected].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return numeric, selected


def _matrix(matrix: pd.DataFrame) -> dict:
    return {
        "columns": list(matrix.columns),
        "matrix": [[_safe(value) for value in row] for row in matrix.to_numpy().tolist()],
    }


def _distribution(numeric: pd.DataFrame, columns: list[str], bin_count: int = 0) -> dict:
    items = []
    for column in columns[:12]:
        values = numeric[column].dropna()
        if values.empty:
            continue
        q1, median, q3 = values.quantile([.25, .5, .75]).tolist()
        bins = max(5, min(100, int(bin_count))) if bin_count else min(30, max(5, int(np.sqrt(len(values)))))
        counts, edges = np.histogram(values, bins=bins)
        items.append({
            "column": column,
            "statistics": {
                "count": len(values), "mean": _safe(values.mean()), "std": _safe(values.std()),
                "min": _safe(values.min()), "q1": _safe(q1), "median": _safe(median),
                "q3": _safe(q3), "max": _safe(values.max()), "skew": _safe(values.skew()),
            },
            "histogram": {"counts": counts.astype(int).tolist(), "bins": [_safe(value) for value in edges]},
            "boxplot": [_safe(values.min()), _safe(q1), _safe(median), _safe(q3), _safe(values.max())],
        })
    return {"available": bool(items), "items": items}


def _anomaly(
    numeric: pd.DataFrame, columns: list[str], iqr_multiplier: float = 1.5,
    threshold_sigma: float = 5.0, estimators: int = 100, sigma_multiplier: float = 3.0,
    timestamps: pd.Series | None = None,
) -> dict:
    column_results, iqr_examples = [], []
    row_flags = np.zeros(len(numeric), dtype=bool)
    sigma_results, sigma_examples = [], []
    sigma_row_flags = np.zeros(len(numeric), dtype=bool)
    time_values = pd.to_datetime(timestamps, errors="coerce") if timestamps is not None else None
    def location(index: int) -> dict:
        value = None if time_values is None else time_values.iloc[index]
        return {"row": int(index), "time": value.isoformat() if pd.notna(value) else None}
    for column in columns:
        values = numeric[column]
        valid = values.dropna()
        if valid.empty:
            continue
        q1, q3 = valid.quantile([.25, .75]).tolist()
        iqr = q3 - q1
        low, high = q1 - iqr_multiplier * iqr, q3 + iqr_multiplier * iqr
        flags = ((values < low) | (values > high)).fillna(False).to_numpy()
        row_flags |= flags
        indexes = np.flatnonzero(flags)[:50]
        column_results.append({
            "column": column, "count": int(flags.sum()),
            "rate": _safe(flags.mean() * 100), "lower": _safe(low), "upper": _safe(high),
        })
        iqr_examples.extend([{
            **location(index), "column": column, "value": _safe(values.iloc[index]),
            "direction": "低于下界" if values.iloc[index] < low else "高于上界",
            "lower": _safe(low), "upper": _safe(high),
        } for index in indexes])
        mean = float(valid.mean())
        std = float(valid.std(ddof=1)) if len(valid) > 1 else 0.0
        sigma_low = mean - sigma_multiplier * std
        sigma_high = mean + sigma_multiplier * std
        sigma_flags = (
            ((values < sigma_low) | (values > sigma_high)).fillna(False).to_numpy()
            if std > 0 else np.zeros(len(values), dtype=bool)
        )
        sigma_row_flags |= sigma_flags
        indexes = np.flatnonzero(sigma_flags)[:50]
        sigma_results.append({
            "column": column, "count": int(sigma_flags.sum()),
            "rate": _safe(sigma_flags.mean() * 100), "mean": _safe(mean),
            "std": _safe(std), "lower": _safe(sigma_low), "upper": _safe(sigma_high),
        })
        sigma_examples.extend([{
            **location(index), "column": column, "value": _safe(values.iloc[index]),
            "direction": "低于下界" if values.iloc[index] < sigma_low else "高于上界",
            "lower": _safe(sigma_low), "upper": _safe(sigma_high),
        } for index in indexes])
    forest_columns = [
        column for column in columns
        if numeric[column].notna().sum() >= 8 and numeric[column].nunique(dropna=True) > 1
    ]
    forest_flags = np.zeros(len(numeric), dtype=bool)
    scores = np.zeros(len(numeric), dtype=float)
    if forest_columns:
        filled = numeric[forest_columns].fillna(numeric[forest_columns].median()).fillna(0)
        forest = IsolationForest(n_estimators=estimators, contamination="auto", random_state=42, n_jobs=-1)
        forest.fit(filled)
        scores = -forest.score_samples(filled)
        score_mean = float(np.mean(scores))
        score_std = float(np.std(scores))
        threshold = score_mean + threshold_sigma * score_std if score_std > 0 else math.inf
        forest_flags = scores > threshold
    flagged = np.flatnonzero(forest_flags)
    top = flagged[np.argsort(scores[flagged])[::-1]][:100] if len(flagged) else []
    overlap = {
        "iqr_only": int(np.sum(row_flags & ~sigma_row_flags & ~forest_flags)),
        "three_sigma_only": int(np.sum(~row_flags & sigma_row_flags & ~forest_flags)),
        "isolation_forest_only": int(np.sum(~row_flags & ~sigma_row_flags & forest_flags)),
        "multiple_methods": int(np.sum(((row_flags.astype(int) + sigma_row_flags.astype(int) + forest_flags.astype(int)) >= 2))),
    }
    return {
        "available": bool(column_results),
        "reason": None if column_results else "所选字段没有可用于异常检测的有效数值",
        "iqr": {
            "row_count": int(row_flags.sum()), "row_rate": _safe(row_flags.mean() * 100),
            "columns": sorted(column_results, key=lambda item: item["rate"] or 0, reverse=True),
            "examples": sorted(iqr_examples, key=lambda item: (item["column"], item["row"]))[:200],
        },
        "three_sigma": {
            "algorithm": f"均值 ± {sigma_multiplier:g}σ",
            "sigma_multiplier": sigma_multiplier,
            "assumption": "适用于近似正态分布的连续数值字段；建议结合正态性检验和QQ图判断",
            "row_count": int(sigma_row_flags.sum()),
            "row_rate": _safe(sigma_row_flags.mean() * 100),
            "columns": sorted(sigma_results, key=lambda item: item["rate"] or 0, reverse=True),
            "examples": sorted(sigma_examples, key=lambda item: (item["column"], item["row"]))[:200],
        },
        "isolation_forest": {
            "available": bool(forest_columns), "columns": forest_columns,
            "threshold_method": f"Isolation Forest异常分数 + {threshold_sigma:g}σ阈值",
            "estimators": estimators,
            "count": int(forest_flags.sum()), "rate": _safe(forest_flags.mean() * 100),
            "top_rows": [{**location(index), "score": _safe(scores[index])} for index in top],
        },
        "overlap": overlap,
    }


def _time_frame(frame: pd.DataFrame, requested: str | None) -> tuple[pd.DataFrame | None, str | None]:
    time_column = detect_time_column(frame, requested)
    if not time_column:
        return None, None
    parsed = pd.to_datetime(frame[time_column], errors="coerce")
    valid = frame.loc[parsed.notna()].copy()
    if valid.empty:
        if requested:
            raise ValueError(f"时间列 '{time_column}' 没有可解析的时间值")
        return None, None
    valid[time_column] = parsed[parsed.notna()]
    return valid.sort_values(time_column).reset_index(drop=True), time_column


def _duration_label(seconds: float | int | None) -> str | None:
    if seconds is None or not math.isfinite(float(seconds)):
        return None
    value = float(seconds)
    if value == 0:
        return "0秒"
    if value % 86400 == 0:
        return f"{value / 86400:g}天"
    if value % 3600 == 0:
        return f"{value / 3600:g}小时"
    if value % 60 == 0:
        return f"{value / 60:g}分钟"
    return f"{value:g}秒"


def _time_quality(frame: pd.DataFrame, requested: str | None) -> dict:
    time_column = detect_time_column(frame, requested)
    if not time_column:
        return {"available": False, "algorithm": "时间轴完整性检查", "reason": "未识别到时间列"}
    parsed = pd.to_datetime(frame[time_column], errors="coerce")
    valid = parsed.dropna()
    if len(valid) < 2:
        return {"available": False, "algorithm": "时间轴完整性检查", "reason": "有效时间值不足2条"}
    original_differences = valid.diff().dt.total_seconds().dropna()
    backward_jumps = int((original_differences < 0).sum())
    duplicate_rows = int(valid.duplicated(keep=False).sum())
    unique_sorted = pd.Series(valid.unique()).sort_values().reset_index(drop=True)
    differences = unique_sorted.diff().dt.total_seconds().dropna()
    positive = differences[differences > 0]
    if positive.empty:
        return {"available": False, "algorithm": "时间轴完整性检查", "reason": "时间列没有有效跨度"}
    interval = float(positive.median())
    tolerance = max(1.0, interval * 0.01)
    regular_steps = int(np.isclose(positive.to_numpy(), interval, atol=tolerance, rtol=.01).sum())
    expected_points = int(round((unique_sorted.iloc[-1] - unique_sorted.iloc[0]).total_seconds() / interval)) + 1
    missing_slots = max(0, expected_points - len(unique_sorted))
    coverage = len(unique_sorted) / expected_points * 100 if expected_points else 100.0
    long_gaps = positive[positive > interval * 1.5]
    # Keep exact locations as well as aggregate counts, so data owners can
    # trace a time-quality warning back to the source data.
    gap_examples = []
    for index, seconds in long_gaps.head(500).items():
        before = unique_sorted.iloc[index]
        after = unique_sorted.iloc[index + 1]
        gap_examples.append({
            "before_time": before.isoformat(),
            "after_time": after.isoformat(),
            "gap_seconds": _safe(seconds),
            "gap_label": _duration_label(float(seconds)),
            "estimated_missing_points": max(0, int(round(float(seconds) / interval)) - 1),
            "kind": "采样缺口",
        })
    offsets = []
    for column in frame.columns:
        if column == time_column or not any(token in str(column).lower() for token in ("time", "date", "时间", "日期")):
            continue
        candidate = pd.to_datetime(frame[column], errors="coerce")
        paired = parsed.notna() & candidate.notna()
        if int(paired.sum()) < max(3, int(len(frame) * .5)):
            continue
        delta = (candidate[paired] - parsed[paired]).dt.total_seconds()
        offsets.append({
            "column": str(column), "valid_pairs": int(len(delta)),
            "median_seconds": _safe(delta.median()),
            "median_label": _duration_label(float(delta.median())),
            "q05_seconds": _safe(delta.quantile(.05)), "q95_seconds": _safe(delta.quantile(.95)),
            "is_constant": bool(delta.nunique(dropna=True) == 1),
        })
    issues = []
    if backward_jumps:
        issues.append(f"原始行顺序存在{backward_jumps}次时间倒序")
    if duplicate_rows:
        issues.append(f"存在{duplicate_rows}行重复时间")
    if missing_slots:
        issues.append(f"按典型间隔估算缺少{missing_slots}个时间点")
    if int(parsed.isna().sum()):
        issues.append(f"存在{int(parsed.isna().sum())}个无效时间值")
    status = "problem" if backward_jumps or duplicate_rows else "warning" if missing_slots or parsed.isna().any() else "good"
    return {
        "available": True, "algorithm": "时间轴完整性与对齐检查", "status": status,
        "time_column": time_column, "row_count": int(len(frame)), "valid_time_count": int(len(valid)),
        "invalid_time_count": int(parsed.isna().sum()), "unique_time_count": int(len(unique_sorted)),
        "start_time": unique_sorted.iloc[0].isoformat(), "end_time": unique_sorted.iloc[-1].isoformat(),
        "is_originally_sorted": backward_jumps == 0, "backward_jumps": backward_jumps,
        "duplicate_time_rows": duplicate_rows, "sample_interval_seconds": _safe(interval),
        "sample_interval_label": _duration_label(interval),
        "regular_step_rate": _safe(regular_steps / len(positive) * 100),
        "expected_points": expected_points, "missing_time_points": missing_slots,
        "coverage_rate": _safe(coverage), "gap_count": int(len(long_gaps)),
        "max_gap_seconds": _safe(positive.max()), "max_gap_label": _duration_label(float(positive.max())),
        "gap_examples": gap_examples,
        "gap_examples_truncated": bool(len(long_gaps) > len(gap_examples)),
        "interval_distribution": [
            {"seconds": _safe(seconds), "label": _duration_label(float(seconds)), "count": int(count)}
            for seconds, count in positive.value_counts().sort_values(ascending=False).head(20).items()
        ],
        "time_offsets": offsets, "issues": issues,
    }


def _uniform_numeric_frame(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    columns: list[str],
    requested_interval_seconds: float = 0.0,
    interpolation_limit: int = 3,
) -> tuple[pd.DataFrame, float, bool] | None:
    if ordered is None or not time_column:
        return None
    selected = [column for column in dict.fromkeys(columns) if column in ordered.columns]
    if not selected:
        return None
    working = ordered[[time_column, *selected]].copy()
    for column in selected:
        working[column] = pd.to_numeric(working[column], errors="coerce")
    grouped = working.groupby(time_column, sort=True)[selected].mean()
    if len(grouped) < 3:
        return None
    differences = grouped.index.to_series().diff().dt.total_seconds().dropna()
    positive = differences[differences > 0]
    if positive.empty:
        return None
    natural_interval = float(positive.median())
    interval = float(requested_interval_seconds or natural_interval)
    if not math.isfinite(interval) or interval <= 0:
        return None
    frequency = pd.to_timedelta(interval, unit="s")
    # Use time bins rather than exact timestamp reindexing. Industrial collectors
    # commonly alternate between e.g. 5 s and 6 s records; exact matching would
    # turn an otherwise continuous series into mostly NaN values.
    binned = working.set_index(time_column)[selected].resample(frequency, origin="start").mean()
    full_index = pd.date_range(binned.index.min(), binned.index.max(), freq=frequency)
    if len(full_index) > MAX_ROWS:
        return None
    regular = len(full_index) == len(grouped) and np.allclose(
        positive.to_numpy(), interval, atol=max(1.0, interval * .01), rtol=.01
    )
    aligned = binned.reindex(full_index)
    limit = max(0, min(int(interpolation_limit), 48))
    if limit:
        aligned = aligned.interpolate(method="time", limit=limit, limit_area="inside")
    return aligned, interval, not regular


def _mutual_information(
    numeric: pd.DataFrame, target: str | None, columns: list[str],
    neighbors: int = 3, sample_size: int = 20_000,
) -> dict:
    if not target or target not in numeric.columns:
        return {"available": False, "algorithm": "互信息回归", "reason": "请选择数值目标列"}
    features = [
        column for column in columns if column != target
        and numeric[column].notna().sum() >= 10 and numeric[column].nunique(dropna=True) > 1
    ]
    if not features:
        return {"available": False, "algorithm": "互信息回归", "reason": "目标列之外没有可用数值特征"}
    working = numeric[[*features, target]].replace([np.inf, -np.inf], np.nan)
    working = working.loc[working[target].notna()].copy()
    missing_feature_values = int(working[features].isna().sum().sum())
    working[features] = working[features].fillna(working[features].median()).fillna(0)
    sampled = len(working) > sample_size
    if sampled:
        working = working.sample(sample_size, random_state=42).sort_index()
    if len(working) < 10 or working[target].nunique() <= 1:
        return {"available": False, "algorithm": "互信息回归", "reason": "目标列有效样本或变化不足"}
    count = max(2, min(int(neighbors), 20, len(working) - 1))
    scores = mutual_info_regression(
        working[features], working[target].to_numpy(dtype=float),
        n_neighbors=count, random_state=42,
    )
    items = []
    for column, score in zip(features, scores):
        pearson = working[column].corr(working[target], method="pearson")
        spearman = working[column].corr(working[target], method="spearman")
        items.append({
            "column": column, "mutual_information": _safe(score),
            "pearson": _safe(pearson), "spearman": _safe(spearman),
        })
    items.sort(key=lambda item: item["mutual_information"] or 0, reverse=True)
    return {
        "available": bool(items), "algorithm": "K近邻互信息回归(Mutual Information Regression)",
        "target_column": target, "sample_size": int(len(working)), "neighbors": count,
        "random_state": 42, "sampled": sampled, "missing_feature_values_filled": missing_feature_values,
        "score_note": "互信息为非负的相对排序分数，越大表示非线性依赖越强；不表示影响方向、因果关系或可直接比较的相关系数。",
        "items": items,
    }


def _psi(reference: pd.Series, current: pd.Series, bins: int) -> float:
    """PSI —— 统一委托 services.drift_metrics.psi。

    原实现在基准分位数去重后不足 3 个边界时直接返回 0.0（离散数据"完全分离"
    被判成"无漂移"）；共享实现改为按去重取值的中点分箱，两箱闭式解可复算。
    """
    return drift_metrics.psi(reference.to_numpy(dtype=float),
                            current.to_numpy(dtype=float), bins=bins)


def _time_drift(
    ordered: pd.DataFrame | None, numeric: pd.DataFrame, columns: list[str],
    window_fraction: float = .3, alpha: float = .05, psi_bins: int = 10,
) -> dict:
    source = ordered if ordered is not None else numeric
    fraction = max(.1, min(float(window_fraction), .5))
    window_size = max(10, int(len(source) * fraction))
    if len(source) < 2 * window_size:
        return {"available": False, "algorithm": "KS + PSI + Wasserstein时间漂移", "reason": "数据量不足以划分前后窗口"}
    items = []
    for column in columns:
        if column not in source.columns:
            continue
        reference = pd.to_numeric(source[column].iloc[:window_size], errors="coerce").dropna()
        current = pd.to_numeric(source[column].iloc[-window_size:], errors="coerce").dropna()
        if len(reference) < 10 or len(current) < 10 or reference.nunique() <= 1:
            continue
        ks_statistic, p_value = stats.ks_2samp(reference, current)
        pooled_std = float(pd.concat([reference, current]).std(ddof=1))
        wasserstein = float(stats.wasserstein_distance(reference, current))
        normalized_wasserstein = wasserstein / pooled_std if pooled_std > 0 else 0.0
        psi = _psi(reference, current, max(5, min(int(psi_bins), 50)))
        level = "significant" if psi >= .25 else "warning" if psi >= .1 else "stable"
        items.append({
            "column": column, "psi": _safe(psi), "level": level,
            "ks_statistic": _safe(ks_statistic), "ks_p_value": _safe(p_value),
            "ks_significant": bool(p_value < alpha), "wasserstein": _safe(wasserstein),
            "normalized_wasserstein": _safe(normalized_wasserstein),
            "reference_mean": _safe(reference.mean()), "current_mean": _safe(current.mean()),
            "mean_change": _safe(current.mean() - reference.mean()),
        })
    items.sort(key=lambda item: (item["psi"] or 0, item["ks_statistic"] or 0), reverse=True)
    return {
        "available": bool(items), "algorithm": "KS双样本检验 + PSI + 标准化Wasserstein距离",
        "window_fraction": fraction, "window_size": window_size, "alpha": alpha,
        "psi_thresholds": {"stable": "<0.1", "warning": "0.1~0.25", "significant": ">=0.25"},
        "summary": {
            "stable": sum(item["level"] == "stable" for item in items),
            "warning": sum(item["level"] == "warning" for item in items),
            "significant": sum(item["level"] == "significant" for item in items),
        },
        "items": items,
    }


def _trend(ordered: pd.DataFrame | None, time_column: str | None, columns: list[str], requested_window: int = 0) -> dict:
    if ordered is None or not time_column:
        return {"available": False, "reason": "未识别到时间列，请选择时间列后运行"}
    series = []
    stride = max(1, math.ceil(len(ordered) / 800))
    sampled = ordered.iloc[::stride]
    window = max(3, min(int(requested_window), min(500, len(ordered)))) if requested_window else max(3, min(30, len(ordered) // 20 or 3))
    for column in columns[:6]:
        values = pd.to_numeric(ordered[column], errors="coerce")
        rolling = values.rolling(window, min_periods=1).mean()
        series.append({
            "column": column,
            "values": [_safe(value) for value in values.iloc[::stride]],
            "moving_average": [_safe(value) for value in rolling.iloc[::stride]],
            "direction": "上升" if rolling.iloc[-1] > rolling.iloc[0] else "下降" if rolling.iloc[-1] < rolling.iloc[0] else "平稳",
        })
    return {
        "available": bool(series), "time_column": time_column, "moving_window": window,
        "timestamps": [value.isoformat() for value in sampled[time_column]], "series": series,
    }


def _lag(
    ordered: pd.DataFrame | None, time_column: str | None, target: str | None,
    columns: list[str], max_lag: int, requested_interval_seconds: float = 0.0,
    interpolation_limit: int = 3,
) -> dict:
    if ordered is None:
        return {"available": False, "reason": "滞后分析需要有效时间列"}
    if not target or target not in ordered.columns:
        return {"available": False, "reason": "请选择目标列后运行滞后分析"}
    candidate_columns = [item for item in columns if item != target][:20]
    uniform = _uniform_numeric_frame(
        ordered, time_column, [target, *candidate_columns],
        requested_interval_seconds, interpolation_limit,
    )
    if uniform is None:
        return {"available": False, "reason": "无法将时间轴对齐为等间隔序列"}
    aligned, interval, resampled = uniform
    y = aligned[target]
    results = []
    for column in candidate_columns:
        x = aligned[column]
        correlations = []
        for lag in range(0, max_lag + 1):
            value = x.shift(lag).corr(y)
            seconds = lag * interval
            correlations.append({
                "lag": lag, "lag_seconds": _safe(seconds),
                "lag_label": _duration_label(seconds), "correlation": _safe(value),
            })
        valid = [item for item in correlations if item["correlation"] is not None]
        best = max(valid, key=lambda item: abs(item["correlation"])) if valid else None
        results.append({"column": column, "best": best, "correlations": correlations})
    results.sort(key=lambda item: abs((item.get("best") or {}).get("correlation") or 0), reverse=True)
    return {
        "available": bool(results), "target_column": target, "max_lag": max_lag,
        "sample_interval_seconds": _safe(interval), "sample_interval_label": _duration_label(interval),
        "resampled": resampled, "interpolation_limit": interpolation_limit,
        "lag_definition": "滞后按等间隔时间轴计算；正滞后表示该字段早于目标",
        "items": results,
    }


def _change_points(
    ordered: pd.DataFrame | None, time_column: str | None, target: str | None,
    columns: list[str], requested_window: int = 0, threshold_sigma: float = 3.0,
) -> dict:
    if ordered is None or not time_column:
        return {"available": False, "reason": "变化点检测需要有效时间列"}
    selected = [target] if target and target in columns else columns[:4]
    items = []
    for column in selected:
        values = pd.to_numeric(ordered[column], errors="coerce").interpolate().ffill().bfill()
        if len(values) < 10 or values.std() == 0:
            continue
        window = max(3, min(int(requested_window), min(500, len(values) // 2))) if requested_window else max(3, min(30, len(values) // 20))
        before = values.rolling(window, min_periods=window).mean()
        delta = (before - before.shift(window)).abs()
        threshold = float(delta.mean() + threshold_sigma * delta.std())
        candidates = np.flatnonzero((delta > threshold).fillna(False).to_numpy())
        kept = []
        for index in candidates:
            if not kept or index - kept[-1] >= window:
                kept.append(int(index))
        points = []
        for index in kept[:100]:
            pre_values = values.iloc[max(0, index - 2 * window):max(0, index - window)]
            post_values = values.iloc[max(0, index - window + 1):index + 1]
            pre_mean = float(pre_values.mean()) if len(pre_values) else float("nan")
            post_mean = float(post_values.mean()) if len(post_values) else float("nan")
            points.append({
                "index": index, "time": ordered.iloc[index][time_column].isoformat(),
                "value": _safe(values.iloc[index]), "change": _safe(delta.iloc[index]),
                "pre_mean": _safe(pre_mean), "post_mean": _safe(post_mean),
                "pre_std": _safe(pre_values.std(ddof=1)) if len(pre_values) > 1 else 0.0,
                "post_std": _safe(post_values.std(ddof=1)) if len(post_values) > 1 else 0.0,
                "mean_shift": _safe(post_mean - pre_mean), "threshold": _safe(threshold),
                "threshold_exceeded_by": _safe(float(delta.iloc[index]) - threshold),
            })
        stride = max(1, math.ceil(len(values) / 1000))
        items.append({
            "column": column, "window": window, "threshold_sigma": threshold_sigma,
            "threshold": _safe(threshold), "point_count": len(points), "points": points,
            "series": {
                "timestamps": [ordered.iloc[index][time_column].isoformat() for index in range(0, len(values), stride)],
                "values": [_safe(value) for value in values.iloc[::stride]],
                "rolling_mean": [_safe(value) for value in before.iloc[::stride]],
            },
        })
    return {
        "available": bool(items), "algorithm": "滑动窗口均值差 + 3σ阈值",
        "time_column": time_column,
        "note": "变化点表示相邻两个窗口的均值差超过阈值；它提示工况可能改变，仍需结合工艺上下文确认原因。",
        "items": items,
    }


def _pca(numeric: pd.DataFrame, columns: list[str], requested_components: int = 5) -> dict:
    usable = [column for column in columns if numeric[column].notna().sum() >= 3 and numeric[column].nunique(dropna=True) > 1]
    if len(usable) < 2:
        return {"available": False, "reason": "PCA至少需要两个有变化的数值字段"}
    missing_cells = int(numeric[usable].isna().sum().sum())
    filled = numeric[usable].fillna(numeric[usable].median()).fillna(0)
    sampled = len(filled) > 20_000
    sample = filled if not sampled else filled.sample(20_000, random_state=42)
    scaled = StandardScaler().fit_transform(sample)
    count = min(max(2, int(requested_components)), 20, len(usable), len(sample))
    model = PCA(n_components=count, random_state=42)
    transformed = model.fit_transform(scaled)
    ratios = model.explained_variance_ratio_
    cumulative = np.cumsum(ratios)
    recommended = next((index + 1 for index, value in enumerate(cumulative) if value >= .8), count)
    loadings = [{"component": f"PC{i + 1}", "values": {column: _safe(model.components_[i, j]) for j, column in enumerate(usable)}} for i in range(count)]
    summaries = [
        {
            "component": f"PC{i + 1}", "explained_variance": _safe(ratios[i] * 100),
            "cumulative_variance": _safe(cumulative[i] * 100), "values": loadings[i]["values"],
        }
        for i in range(count)
    ]
    stride = max(1, math.ceil(len(transformed) / 1000))
    return {
        "available": True, "columns": usable, "sample_size": int(len(sample)),
        "sampled": sampled, "standardized": True, "missing_cells_filled": missing_cells,
        "recommended_components_80": recommended,
        "recommended_cumulative_variance": _safe(cumulative[recommended - 1] * 100),
        "note": "PCA已对各字段标准化；建议保留达到累计解释方差80%的最少主成分数，仍应结合业务可解释性确认。",
        "explained_variance_ratio": [_safe(value) for value in ratios],
        "cumulative_variance": [_safe(value) for value in cumulative],
        "loadings": loadings, "component_summaries": summaries,
        "scatter": [[_safe(row[0]), _safe(row[1])] for row in transformed[::stride, :2]],
    }



def _cca(
    numeric: pd.DataFrame,
    columns: list[str],
    x_columns: list[str] | None,
    y_columns: list[str] | None,
    requested_components: int = 3,
    sample_size: int = 20_000,
) -> dict:
    """Canonical correlation between two explicitly selected groups of numeric variables."""
    x_selected = list(dict.fromkeys(column for column in (x_columns or []) if column in columns))
    y_selected = list(dict.fromkeys(column for column in (y_columns or []) if column in columns))
    if not x_selected or not y_selected:
        return {"available": False, "algorithm": "标准化典型相关分析（CCA）", "reason": "请在参数设置中分别选择 X 变量组和 Y 变量组"}
    overlap = sorted(set(x_selected) & set(y_selected))
    if overlap:
        return {"available": False, "algorithm": "标准化典型相关分析（CCA）", "reason": "同一字段不能同时属于 X 与 Y 变量组：" + "、".join(overlap)}
    usable_x = [column for column in x_selected if numeric[column].nunique(dropna=True) > 1]
    usable_y = [column for column in y_selected if numeric[column].nunique(dropna=True) > 1]
    excluded = [column for column in [*x_selected, *y_selected] if column not in [*usable_x, *usable_y]]
    if not usable_x or not usable_y:
        return {"available": False, "algorithm": "标准化典型相关分析（CCA）", "reason": "X、Y 两组都至少需要一个有变化的数值字段"}
    working = numeric[[*usable_x, *usable_y]].dropna()
    if len(working) < 3:
        return {"available": False, "algorithm": "标准化典型相关分析（CCA）", "reason": "两组字段共同有效的完整样本少于 3 条"}
    sampled = len(working) > sample_size
    if sampled:
        working = working.sample(sample_size, random_state=42).sort_index()
    component_count = min(max(1, int(requested_components)), len(usable_x), len(usable_y), len(working) - 1)
    x_values = StandardScaler().fit_transform(working[usable_x])
    y_values = StandardScaler().fit_transform(working[usable_y])
    try:
        model = CCA(n_components=component_count, max_iter=1000, tol=1e-06)
        x_scores, y_scores = model.fit_transform(x_values, y_values)
    except Exception as exc:
        return {"available": False, "algorithm": "标准化典型相关分析（CCA）", "reason": f"CCA计算失败：{exc}"}
    components = []
    for index in range(component_count):
        correlation = float(np.corrcoef(x_scores[:, index], y_scores[:, index])[0, 1])
        components.append({
            "component": f"CC{index + 1}", "correlation": _safe(correlation),
            "x_weights": {column: _safe(model.x_weights_[field_index, index]) for field_index, column in enumerate(usable_x)},
            "y_weights": {column: _safe(model.y_weights_[field_index, index]) for field_index, column in enumerate(usable_y)},
            "x_loadings": {column: _safe(np.corrcoef(x_values[:, field_index], x_scores[:, index])[0, 1]) for field_index, column in enumerate(usable_x)},
            "y_loadings": {column: _safe(np.corrcoef(y_values[:, field_index], y_scores[:, index])[0, 1]) for field_index, column in enumerate(usable_y)},
        })
    return {
        "available": True, "algorithm": "标准化典型相关分析（Canonical Correlation Analysis, CCA）",
        "x_columns": usable_x, "y_columns": usable_y, "excluded_constant_columns": excluded,
        "sample_size": int(len(working)), "sampled": sampled, "standardized": True,
        "component_count": component_count, "components": components,
        "note": "CCA用于衡量两组变量的整体线性关联。典型相关系数越接近 1，说明两组组合后的关联越强；权重用于构成典型变量，载荷更适合判断各原始字段与典型变量的关联强弱。若 Y 组只有一个字段，则 CC1 表示 X 组对该字段的综合线性关联，不替代每个字段单独的 Pearson/Spearman 相关系数。",
    }



def _sensor_consistency(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    numeric: pd.DataFrame,
    columns: list[str],
    sensor_columns: list[str] | None,
    reference_method: str = "median",
    threshold: float = 3.0,
) -> dict:
    """Compare each sensor with a leave-one-out consensus built from its peers."""
    selected = list(dict.fromkeys(column for column in (sensor_columns or []) if column in columns))
    if len(selected) < 3:
        return {"available": False, "algorithm": "多传感器一致性分析", "reason": "请在参数设置中至少选择 3 个同类传感器字段"}
    if ordered is not None and time_column:
        working = ordered[[time_column, *selected]].copy()
        timestamps = pd.to_datetime(working[time_column], errors="coerce")
    else:
        working = numeric[selected].copy()
        timestamps = pd.Series([pd.NaT] * len(working))
    values = working[selected].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    method = "mean" if reference_method == "mean" else "median"
    threshold = max(1.0, min(float(threshold), 10.0))
    items, examples = [], []
    for column in selected:
        peers = [peer for peer in selected if peer != column]
        peer_values = values[peers]
        reference = peer_values.mean(axis=1, skipna=True) if method == "mean" else peer_values.median(axis=1, skipna=True)
        valid = values[column].notna() & reference.notna()
        errors = (values.loc[valid, column] - reference.loc[valid]).astype(float)
        if len(errors) < 3:
            items.append({"column": column, "available": False, "reason": "与其他传感器共同有效的样本不足"})
            continue
        median_error = float(errors.median())
        mad = float(np.median(np.abs(errors - median_error)))
        robust_sigma = 1.4826 * mad
        fallback_std = float(errors.std(ddof=1)) if len(errors) > 1 else 0.0
        scale = robust_sigma if robust_sigma > 1e-12 else fallback_std
        limit = threshold * scale
        abnormal = np.abs(errors - median_error) > limit if limit > 0 else np.zeros(len(errors), dtype=bool)
        candidate_values = values.loc[valid, column]
        reference_values = reference.loc[valid]
        correlation = candidate_values.corr(reference_values, method="pearson") if candidate_values.nunique(dropna=True) > 1 and reference_values.nunique(dropna=True) > 1 else None
        rmse = float(np.sqrt(np.mean(np.square(errors))))
        item = {
            "column": column, "available": True, "valid_pairs": int(len(errors)),
            "coverage_rate": _safe(len(errors) / max(1, len(values)) * 100),
            "reference_method": method, "bias": _safe(errors.mean()),
            "median_bias": _safe(median_error), "mae": _safe(np.abs(errors).mean()),
            "rmse": _safe(rmse), "pearson_to_peers": _safe(correlation),
            "robust_sigma": _safe(scale), "abnormal_limit": _safe(limit),
            "abnormal_count": int(np.sum(abnormal)), "abnormal_rate": _safe(float(np.mean(abnormal) * 100)),
        }
        items.append(item)
        error_indexes = errors.index[np.flatnonzero(abnormal)[:50]]
        for row_index in error_indexes:
            examples.append({
                "time": timestamps.iloc[row_index].isoformat() if row_index < len(timestamps) and pd.notna(timestamps.iloc[row_index]) else None,
                "row": int(row_index), "column": column,
                "value": _safe(values.iloc[row_index][column]), "peer_reference": _safe(reference.iloc[row_index]),
                "deviation": _safe(values.iloc[row_index][column] - reference.iloc[row_index]),
                "deviation_limit": _safe(limit),
            })
    available_items = [item for item in items if item.get("available")]
    available_items.sort(key=lambda item: (item.get("abnormal_rate") or 0, item.get("mae") or 0), reverse=True)
    return {
        "available": bool(available_items), "algorithm": "留一传感器参考值 + MAD 鲁棒偏差检测",
        "columns": selected, "reference_method": method, "threshold": threshold,
        "note": "每台传感器均与其余传感器形成的参考值比较。偏差、MAE、RMSE 越大或异常点比例越高，越需要复核；结果用于一致性检查，不代表参考值一定是真实值。",
        "items": available_items, "unavailable_items": [item for item in items if not item.get("available")],
        "examples": sorted(examples, key=lambda item: abs(item.get("deviation") or 0), reverse=True)[:200],
    }


def _attribution_independent_validation(
    aligned: pd.DataFrame,
    target: str,
    drivers: list[str],
    lag: int,
    ridge_alpha: float = 1.0,
    holdout_ratio: float = 0.2,
) -> dict:
    """Chronological conditional permutation evidence for anomaly attribution.

    A full lagged Ridge model is fitted on an earlier time segment.  On the later
    hold-out segment, each candidate's complete lag block is jointly shuffled.
    The deterioration relative to the intact prediction estimates information
    that candidate contributes after the target history and the other selected
    candidates are already present.  This is still observational evidence, not
    causal identification.
    """
    drivers = list(dict.fromkeys(drivers))[:12]
    if not drivers:
        return {"available": False, "reason": "没有可进行独立验证的候选字段", "items": []}
    labels, features = [], []
    for step in range(1, lag + 1):
        labels.append(f"{target} (t-{step})")
        features.append(aligned[target].shift(step))
    for driver in drivers:
        for step in range(0, lag + 1):
            labels.append(f"{driver} (t-{step})")
            features.append(aligned[driver].shift(step))
    design = pd.concat(features, axis=1)
    design.columns = labels
    combined = pd.concat([aligned[target].rename("__target__"), design], axis=1).dropna()
    minimum = max(60, len(labels) * 3)
    if len(combined) < minimum:
        return {"available": False, "reason": f"独立验证所需的连续样本不足（至少 {minimum} 条）", "items": []}
    split = int(len(combined) * (1 - holdout_ratio))
    split = max(minimum // 2, min(split, len(combined) - 10))
    train, test = combined.iloc[:split], combined.iloc[split:]
    x_scaler = StandardScaler().fit(train[labels])
    x_train, x_test = x_scaler.transform(train[labels]), x_scaler.transform(test[labels])
    y_train, y_test = train["__target__"].to_numpy(dtype=float), test["__target__"].to_numpy(dtype=float)
    model = Ridge(alpha=ridge_alpha).fit(x_train, y_train)

    def metrics(actual: np.ndarray, estimate: np.ndarray) -> dict:
        residual = actual - estimate
        ss_total = float(np.sum((actual - actual.mean()) ** 2))
        return {
            "mae": float(np.mean(np.abs(residual))),
            "r2": (1 - float(np.sum(residual ** 2)) / ss_total) if ss_total > 1e-12 else None,
        }

    full_prediction = model.predict(x_test)
    full_metrics = metrics(y_test, full_prediction)
    history_labels = labels[:lag]
    history_scaler = StandardScaler().fit(train[history_labels])
    history_model = Ridge(alpha=ridge_alpha).fit(history_scaler.transform(train[history_labels]), y_train)
    baseline_metrics = metrics(y_test, history_model.predict(history_scaler.transform(test[history_labels])))
    items = []
    rng = np.random.default_rng(20260920)
    for driver_index, driver in enumerate(drivers):
        positions = [index for index, label in enumerate(labels) if label.startswith(driver + " (")]
        coefficients = model.coef_[positions]
        strongest_position = int(np.argmax(np.abs(coefficients)))
        coefficient = float(coefficients[strongest_position])
        permuted = x_test.copy()
        order = rng.permutation(len(permuted))
        # Shuffle every lag of the same candidate together, retaining the
        # candidate's internal lag pattern while breaking its link to target.
        permuted[:, positions] = permuted[order][:, positions]
        permuted_metrics = metrics(y_test, model.predict(permuted))
        r2_drop = None
        if full_metrics.get("r2") is not None and permuted_metrics.get("r2") is not None:
            r2_drop = float(full_metrics["r2"] - permuted_metrics["r2"])
        mae_increase = float(permuted_metrics["mae"] - full_metrics["mae"])
        if r2_drop is not None and r2_drop >= .02 and mae_increase > 0:
            level = "较强统计证据"
        elif r2_drop is not None and r2_drop >= .005 and mae_increase > 0:
            level = "中等统计证据"
        elif r2_drop is not None and r2_drop > 0 and mae_increase > 0:
            level = "较弱统计证据"
        else:
            level = "未发现独立证据"
        items.append({
            "column": driver, "permutation_r2_drop": _safe(r2_drop),
            "permutation_mae_increase": _safe(mae_increase),
            "strongest_term": labels[positions[strongest_position]],
            "strongest_coefficient": _safe(coefficient),
            "model_direction": "正向统计关联" if coefficient > 0 else "负向统计关联" if coefficient < 0 else "方向不明显",
            "evidence_level": level,
        })
    return {
        "available": True, "train_rows": int(len(train)), "test_rows": int(len(test)),
        "test_r2": _safe(full_metrics.get("r2")), "baseline_r2": _safe(baseline_metrics.get("r2")),
        "incremental_r2": _safe((full_metrics.get("r2") or 0) - (baseline_metrics.get("r2") or 0)) if full_metrics.get("r2") is not None and baseline_metrics.get("r2") is not None else None,
        "items": items,
    }


def _attribution_evidence_level(precursor_count: int, event_count: int, r2_drop: float | None) -> str:
    coverage = precursor_count / max(1, event_count)
    if precursor_count >= 3 and coverage >= .3 and r2_drop is not None:
        if r2_drop >= .02:
            return "较强统计证据"
        if r2_drop >= .005:
            return "中等统计证据"
        if r2_drop > 0:
            return "较弱统计证据"
    if precursor_count >= 3 and coverage >= .15:
        return "仅有异常前线索"
    return "线索不足"


def _anomaly_attribution(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    columns: list[str],
    target_columns: list[str] | None,
    candidate_columns: list[str] | None,
    hampel_window: int = 11,
    threshold: float = 3.0,
    pre_window_seconds: int = 300,
    minimum_event_gap_seconds: int = 60,
    max_lag: int = 24,
    requested_interval_seconds: float = 0.0,
    interpolation_limit: int = 3,
) -> dict:
    """Screen anomalous outcomes and quantify conditional candidate evidence.

    The output separates detected anomalies from explanatory evidence: precursor
    coverage says how often a candidate changed before an anomaly; conditional
    permutation evidence says whether it adds hold-out predictive information
    after the target history and other candidates are present.  Neither proves a
    physical root cause.
    """
    targets = list(dict.fromkeys(column for column in (target_columns or []) if column in columns))[:12]
    candidates = list(dict.fromkeys(column for column in (candidate_columns or []) if column in columns and column not in targets))[:24]
    if not targets:
        return {"available": False, "algorithm": "异常归因证据分析", "reason": "请在参数设置中选择至少一个异常目标字段"}
    if not candidates:
        return {"available": False, "algorithm": "异常归因证据分析", "reason": "请在参数设置中选择至少一个候选影响字段"}
    uniform = _uniform_numeric_frame(ordered, time_column, [*targets, *candidates], requested_interval_seconds, interpolation_limit)
    if uniform is None:
        return {"available": False, "algorithm": "异常归因证据分析", "reason": "请选择有效时间列，并保证目标和候选字段具有足够的连续时序数据"}
    aligned, interval, resampled = uniform
    window = max(3, min(int(hampel_window), 101))
    if window % 2 == 0:
        window += 1
    threshold = max(1.0, min(float(threshold), 10.0))
    pre_steps = max(1, min(int(math.ceil(max(5, pre_window_seconds) / interval)), 720))
    gap_steps = max(1, min(int(math.ceil(max(0, minimum_event_gap_seconds) / interval)), 720))
    lag = max(1, min(int(max_lag), 120))

    candidate_change: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        values = aligned[candidate].astype(float)
        delta = values.diff()
        valid = delta.dropna()
        median = float(valid.median()) if len(valid) else 0.0
        mad = float(np.median(np.abs(valid.to_numpy(dtype=float) - median))) if len(valid) else 0.0
        scale = 1.4826 * mad
        if scale <= 1e-12:
            scale = float(valid.std(ddof=1)) if len(valid) > 1 else 0.0
        score = (delta - median).abs() / scale if scale > 1e-12 else pd.Series(0.0, index=delta.index)
        candidate_change[candidate] = {"values": values, "delta": delta, "scores": score.replace([np.inf, -np.inf], np.nan).fillna(0.0)}

    target_items, events, target_evidence, global_stats = [], [], [], {candidate: [] for candidate in candidates}
    for target in targets:
        series = aligned[target].astype(float)
        local_median = series.rolling(window, center=True, min_periods=max(3, window // 2)).median()
        local_mad = (series - local_median).abs().rolling(window, center=True, min_periods=max(3, window // 2)).median()
        robust_sigma = 1.4826 * local_mad
        fallback = float(series.diff().dropna().std(ddof=1)) if series.notna().sum() > 3 else 0.0
        scale = robust_sigma.where(robust_sigma > 1e-12, fallback if fallback > 1e-12 else np.nan)
        anomaly_score = ((series - local_median).abs() / scale).replace([np.inf, -np.inf], np.nan)
        flags = anomaly_score.ge(threshold).fillna(False).to_numpy(dtype=bool)
        raw_indexes = np.flatnonzero(flags)
        selected_indexes: list[int] = []
        for index in raw_indexes:
            if index < pre_steps or not selected_indexes or index - selected_indexes[-1] >= gap_steps:
                selected_indexes.append(int(index))
            elif anomaly_score.iloc[index] > anomaly_score.iloc[selected_indexes[-1]]:
                selected_indexes[-1] = int(index)
        total_event_count = len(selected_indexes)
        # Aggregate evidence across at most 1,000 strongest events/target; retain
        # a smaller 100-event detail list for responsive page rendering.
        analysis_indexes = sorted(selected_indexes, key=lambda idx: float(anomaly_score.iloc[idx] or 0), reverse=True)[:1000]
        reported_indexes = set(analysis_indexes[:100])
        per_candidate: dict[str, list[dict]] = {candidate: [] for candidate in candidates}
        detailed_events: list[dict] = []
        for index in analysis_indexes:
            target_deviation = float(series.iloc[index] - local_median.iloc[index]) if pd.notna(local_median.iloc[index]) else None
            details = []
            for candidate in candidates:
                meta = candidate_change[candidate]
                start = max(1, index - pre_steps)
                scores = meta["scores"].iloc[start:index + 1]
                if scores.empty:
                    continue
                peak_position = int(np.nanargmax(scores.to_numpy(dtype=float))) if scores.notna().any() else 0
                source_index = start + peak_position
                peak_score = float(scores.iloc[peak_position]) if pd.notna(scores.iloc[peak_position]) else 0.0
                lead_seconds = max(0.0, (index - source_index) * interval)
                value_change = float(meta["delta"].iloc[source_index]) if pd.notna(meta["delta"].iloc[source_index]) else None
                before = meta["values"].iloc[max(0, source_index - min(pre_steps, 12)):source_index]
                before_median = float(before.median()) if len(before) else None
                precursor = peak_score >= threshold
                direction = "不确定"
                if value_change is not None and target_deviation is not None and abs(value_change) > 1e-12 and abs(target_deviation) > 1e-12:
                    direction = "同向" if value_change * target_deviation > 0 else "反向"
                statistic = {"score": min(peak_score, 20.0) * math.exp(-lead_seconds / max(pre_steps * interval, 1.0)), "precursor": precursor, "lead_seconds": lead_seconds, "value_change": value_change, "direction": direction}
                per_candidate[candidate].append(statistic)
                global_stats[candidate].append({**statistic, "target": target})
                details.append({
                    "column": candidate, "candidate_score": _safe(statistic["score"]), "change_score": _safe(peak_score),
                    "lead_seconds": _safe(lead_seconds), "value_change": _safe(value_change), "value_before_median": _safe(before_median),
                    "direction_to_target": direction, "evidence": "异常前存在明显变化" if precursor else "异常前未发现明显突变",
                })
            details.sort(key=lambda item: item.get("candidate_score") or 0, reverse=True)
            if index in reported_indexes:
                detailed_events.append({
                    "target_column": target, "time": aligned.index[index].isoformat(), "row": int(index),
                    "target_value": _safe(series.iloc[index]), "expected_local_median": _safe(local_median.iloc[index]),
                    "target_deviation": _safe(target_deviation), "anomaly_score": _safe(anomaly_score.iloc[index]), "candidate_causes": details[:8],
                })

        # Select the strongest precursor candidates for a bounded conditional
        # validation.  Other candidates keep their precursor statistics but do
        # not receive a misleading independent-model score.
        ranked_for_validation = sorted(candidates, key=lambda candidate: float(np.mean([item["score"] for item in per_candidate[candidate]]) or 0.0), reverse=True)[:12]
        validation = _attribution_independent_validation(aligned, target, ranked_for_validation, lag)
        validation_map = {item["column"]: item for item in validation.get("items", [])}
        evidence_rows = []
        for candidate in candidates:
            stats_for_candidate = per_candidate[candidate]
            precursor_items = [item for item in stats_for_candidate if item["precursor"]]
            same = sum(item["direction"] == "同向" for item in precursor_items)
            opposite = sum(item["direction"] == "反向" for item in precursor_items)
            directed = same + opposite
            validation_item = validation_map.get(candidate, {})
            r2_drop = validation_item.get("permutation_r2_drop")
            evidence_rows.append({
                "target_column": target, "column": candidate,
                "analyzed_event_count": int(len(analysis_indexes)), "precursor_event_count": int(len(precursor_items)),
                "precursor_coverage_rate": _safe(len(precursor_items) / max(1, len(analysis_indexes)) * 100),
                "median_lead_seconds": _safe(float(np.median([item["lead_seconds"] for item in precursor_items]))) if precursor_items else None,
                "median_value_change": _safe(float(np.median([item["value_change"] for item in precursor_items if item["value_change"] is not None]))) if any(item["value_change"] is not None for item in precursor_items) else None,
                "same_direction_rate": _safe(same / directed * 100) if directed else None,
                "permutation_r2_drop": r2_drop, "permutation_mae_increase": validation_item.get("permutation_mae_increase"),
                "strongest_term": validation_item.get("strongest_term"), "model_direction": validation_item.get("model_direction"),
                "evidence_level": _attribution_evidence_level(len(precursor_items), len(analysis_indexes), r2_drop),
                "validation_status": "已控制其他入选候选变量" if candidate in validation_map else "未纳入独立模型（仅验证前 12 个）",
            })
        evidence_rows.sort(key=lambda item: ({"较强统计证据": 4, "中等统计证据": 3, "较弱统计证据": 2, "仅有异常前线索": 1}.get(item["evidence_level"], 0), item.get("permutation_r2_drop") or -999, item.get("precursor_coverage_rate") or 0), reverse=True)
        target_evidence.extend(evidence_rows)
        evidence_map = {item["column"]: item for item in evidence_rows}
        for event in detailed_events:
            for detail in event["candidate_causes"]:
                item = evidence_map.get(detail["column"], {})
                detail["evidence_level"] = item.get("evidence_level")
                detail["permutation_r2_drop"] = item.get("permutation_r2_drop")
                if item.get("evidence_level") in {"较强统计证据", "中等统计证据", "较弱统计证据"} and detail["evidence"] == "异常前存在明显变化":
                    detail["evidence"] += f"；{item['evidence_level']}"
        events.extend(detailed_events)
        target_items.append({
            "target_column": target, "anomaly_event_count": int(total_event_count), "analyzed_event_count": int(len(analysis_indexes)), "reported_event_count": int(len(detailed_events)),
            "hampel_window": window, "threshold": threshold,
            "test_r2": validation.get("test_r2"), "baseline_r2": validation.get("baseline_r2"), "incremental_r2": validation.get("incremental_r2"),
            "model_available": bool(validation.get("available")), "model_reason": validation.get("reason"),
        })

    summary = []
    for candidate, values in global_stats.items():
        precursor_items = [item for item in values if item["precursor"]]
        same = sum(item["direction"] == "同向" for item in precursor_items)
        opposite = sum(item["direction"] == "反向" for item in precursor_items)
        directed = same + opposite
        relevant_evidence = [item for item in target_evidence if item["column"] == candidate]
        best_evidence = sorted(relevant_evidence, key=lambda item: ({"较强统计证据": 4, "中等统计证据": 3, "较弱统计证据": 2, "仅有异常前线索": 1}.get(item["evidence_level"], 0), item.get("permutation_r2_drop") or -999), reverse=True)
        summary.append({
            "column": candidate, "appeared_event_count": int(len(precursor_items)), "analyzed_event_count": int(len(values)),
            "precursor_coverage_rate": _safe(len(precursor_items) / max(1, len(values)) * 100),
            "average_candidate_score": _safe(float(np.mean([item["score"] for item in values]))) if values else 0.0,
            "max_candidate_score": _safe(float(np.max([item["score"] for item in values]))) if values else 0.0,
            "same_direction_rate": _safe(same / directed * 100) if directed else None,
            "best_evidence_level": best_evidence[0]["evidence_level"] if best_evidence else "线索不足",
            "best_permutation_r2_drop": best_evidence[0].get("permutation_r2_drop") if best_evidence else None,
        })
    summary.sort(key=lambda item: ({"较强统计证据": 4, "中等统计证据": 3, "较弱统计证据": 2, "仅有异常前线索": 1}.get(item["best_evidence_level"], 0), item.get("best_permutation_r2_drop") or -999, item.get("precursor_coverage_rate") or 0), reverse=True)
    events.sort(key=lambda item: item.get("anomaly_score") or 0, reverse=True)
    return {
        "available": bool(target_items), "algorithm": "局部异常检测 + 异常前对比 + 条件置换验证",
        "target_columns": targets, "candidate_columns": candidates,
        "sample_interval_seconds": _safe(interval), "sample_interval_label": _duration_label(interval), "resampled": resampled,
        "hampel_window": window, "threshold": threshold, "pre_window_seconds": _safe(pre_steps * interval),
        "minimum_event_gap_seconds": _safe(gap_steps * interval), "max_lag": lag,
        "targets": target_items, "candidate_summary": summary, "target_candidate_evidence": target_evidence, "events": events[:300],
        "note": "先定位目标异常，再对比异常前候选变量的突变与领先时间；随后在独立时间后段中保留目标历史和其他入选候选变量，联合置换单一候选变量以检查预测误差是否变差。该结果是用于现场排查的统计证据，不是对物理根因或控制因果的证明。",
    }

def _period_impact_attribution(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    columns: list[str],
    target_columns: list[str] | None,
    candidate_columns: list[str] | None,
    max_lag: int = 24,
    requested_interval_seconds: float = 0.0,
    interpolation_limit: int = 3,
    enabled_methods: list[str] | None = None,
    ridge_alpha: float = 1.0,
    holdout_ratio: float = 0.2,
    rolling_folds: int = 3,
) -> dict:
    """Rank observed process influences inside a user-selected time interval.

    This mode does not label points as anomalous.  It combines positive-lag
    correlation of changes, conditional permutation contribution on a held-out
    chronological segment, and three rolling chronological validations.  The
    output is an observational ranking, not a statement of physical causality.
    """
    targets = list(dict.fromkeys(column for column in (target_columns or []) if column in columns))[:8]
    candidates = list(dict.fromkeys(column for column in (candidate_columns or []) if column in columns and column not in targets))[:12]
    if not targets:
        return {"available": False, "algorithm": "时间段过程影响分析", "reason": "请选择至少一个因变量"}
    if not candidates:
        return {"available": False, "algorithm": "时间段过程影响分析", "reason": "请选择至少一个自变量"}
    uniform = _uniform_numeric_frame(ordered, time_column, [*targets, *candidates], requested_interval_seconds, interpolation_limit)
    if uniform is None:
        return {"available": False, "algorithm": "时间段过程影响分析", "reason": "请选择有效时间列，并保证所选字段在该时间段有足够连续数据"}
    aligned, interval, resampled = uniform
    enabled = set(enabled_methods or ["lag_correlation", "conditional_permutation", "rolling_validation"])
    enabled &= {"lag_correlation", "conditional_permutation", "rolling_validation"}
    if not enabled:
        return {"available": False, "algorithm": "时间段过程影响分析", "reason": "请至少勾选一种分析算法"}
    lag = max(1, min(int(max_lag), 120))
    ridge_alpha = max(0.0001, min(float(ridge_alpha), 1000.0))
    holdout_ratio = max(0.1, min(float(holdout_ratio), 0.4))
    rolling_folds = max(2, min(int(rolling_folds), 5))

    def lag_evidence(target: str, candidate: str) -> dict:
        target_delta = aligned[target].astype(float).diff()
        candidate_delta = aligned[candidate].astype(float).diff()
        best_lag, best_corr, pairs = 0, None, 0
        for step in range(0, lag + 1):
            if step == 0:
                x, y = candidate_delta, target_delta
            else:
                x, y = candidate_delta.iloc[:-step], target_delta.iloc[step:]
            paired = pd.concat([x.reset_index(drop=True), y.reset_index(drop=True)], axis=1).dropna()
            if len(paired) < 12 or paired.iloc[:, 0].std() <= 1e-12 or paired.iloc[:, 1].std() <= 1e-12:
                continue
            correlation = float(paired.iloc[:, 0].corr(paired.iloc[:, 1]))
            if best_corr is None or abs(correlation) > abs(best_corr):
                best_lag, best_corr, pairs = step, correlation, len(paired)
        return {
            "best_lag_steps": int(best_lag), "best_lag_seconds": _safe(best_lag * interval),
            "lag_correlation": _safe(best_corr), "valid_pairs": int(pairs),
            "response_direction": "同向变化" if best_corr is not None and best_corr > 0 else "反向变化" if best_corr is not None and best_corr < 0 else "方向不明显",
        }

    def rolling_validation(target: str) -> dict:
        labels, features = [], []
        for step in range(1, lag + 1):
            labels.append(f"{target} (t-{step})"); features.append(aligned[target].shift(step))
        for candidate in candidates:
            for step in range(lag + 1):
                labels.append(f"{candidate} (t-{step})"); features.append(aligned[candidate].shift(step))
        design = pd.concat(features, axis=1); design.columns = labels
        combined = pd.concat([aligned[target].rename("__target__"), design], axis=1).dropna()
        minimum = max(60, len(labels) * 3)
        if len(combined) < minimum:
            return {"available": False, "reason": f"滚动验证所需连续样本不足（至少 {minimum} 条）"}
        test_size = max(10, min(len(combined) // 6, 1000))
        splits = [int(len(combined) * ratio) for ratio in np.linspace(.55, .85, rolling_folds)]
        scores = []
        for split in splits:
            train_end = max(minimum // 2, split)
            test_end = min(len(combined), train_end + test_size)
            if test_end - train_end < 10:
                continue
            train, test = combined.iloc[:train_end], combined.iloc[train_end:test_end]
            scaler = StandardScaler().fit(train[labels])
            model = Ridge(alpha=ridge_alpha).fit(scaler.transform(train[labels]), train["__target__"].to_numpy(dtype=float))
            actual = test["__target__"].to_numpy(dtype=float); predicted = model.predict(scaler.transform(test[labels]))
            total = float(np.sum((actual - actual.mean()) ** 2))
            if total > 1e-12:
                scores.append(1 - float(np.sum((actual - predicted) ** 2)) / total)
        if not scores:
            return {"available": False, "reason": "无法形成有效滚动测试窗口"}
        return {"available": True, "fold_count": len(scores), "rolling_r2_mean": _safe(float(np.mean(scores))), "rolling_r2_min": _safe(float(np.min(scores))), "rolling_r2_values": [_safe(value) for value in scores]}

    items, target_summaries = [], []
    for target in targets:
        validation = _attribution_independent_validation(aligned, target, candidates, lag, ridge_alpha, holdout_ratio) if "conditional_permutation" in enabled else {"available": False, "reason": "未勾选条件置换贡献", "items": []}
        validation_map = {item["column"]: item for item in validation.get("items", [])}
        rolling = rolling_validation(target) if "rolling_validation" in enabled else {"available": False, "reason": "未勾选滚动时间验证"}
        for candidate in candidates:
            lag_item = lag_evidence(target, candidate) if "lag_correlation" in enabled else {"best_lag_steps": None, "best_lag_seconds": None, "lag_correlation": None, "valid_pairs": 0, "response_direction": "未执行"}
            validation_item = validation_map.get(candidate, {})
            r2_drop = validation_item.get("permutation_r2_drop")
            corr = lag_item.get("lag_correlation")
            if r2_drop is not None and r2_drop >= .02 and corr is not None and abs(corr) >= .1:
                level = "较强统计证据"
            elif r2_drop is not None and r2_drop >= .005:
                level = "中等统计证据"
            elif (r2_drop is not None and r2_drop > 0) or (corr is not None and abs(corr) >= .15):
                level = "较弱统计证据"
            else:
                level = "证据不足"
            score = max(float(r2_drop or 0), 0) + abs(float(corr or 0)) * .1
            items.append({
                "target_column": target, "candidate_column": candidate, **lag_item,
                "independent_r2_drop": r2_drop, "independent_mae_increase": validation_item.get("permutation_mae_increase"),
                "model_direction": validation_item.get("model_direction"), "strongest_term": validation_item.get("strongest_term"),
                "evidence_level": level, "influence_score": _safe(score),
                "validation_status": "已控制其他所选自变量" if candidate in validation_map else "未纳入独立模型",
                "rolling_r2_mean": rolling.get("rolling_r2_mean"), "rolling_r2_min": rolling.get("rolling_r2_min"),
            })
        target_summaries.append({
            "target_column": target, "test_r2": validation.get("test_r2"), "baseline_r2": validation.get("baseline_r2"),
            "incremental_r2": validation.get("incremental_r2"), "model_available": bool(validation.get("available")),
            "model_reason": validation.get("reason"), "rolling_available": bool(rolling.get("available")),
            "rolling_r2_mean": rolling.get("rolling_r2_mean"), "rolling_r2_min": rolling.get("rolling_r2_min"), "rolling_reason": rolling.get("reason"),
        })
    items.sort(key=lambda item: ({"较强统计证据": 4, "中等统计证据": 3, "较弱统计证据": 2}.get(item["evidence_level"], 0), item.get("influence_score") or 0), reverse=True)
    return {
        "available": bool(items), "algorithm": " + ".join({"lag_correlation": "差分滞后互相关分析", "conditional_permutation": "条件置换贡献", "rolling_validation": "滚动时间验证"}[name] for name in ("lag_correlation", "conditional_permutation", "rolling_validation") if name in enabled),
        "enabled_methods": sorted(enabled), "target_columns": targets, "candidate_columns": candidates, "sample_interval_seconds": _safe(interval),
        "sample_interval_label": _duration_label(interval), "resampled": resampled, "max_lag": lag,
        "ridge_alpha": _safe(ridge_alpha), "holdout_ratio": _safe(holdout_ratio), "rolling_folds": rolling_folds,
        "items": items, "target_summaries": target_summaries,
        "note": "分析只使用所选时间段和已勾选算法。正滞后表示自变量变化在前、因变量变化在后；条件置换的独立 R² 损失表示控制其他变量后该字段的独立统计贡献。结果用于比较统计影响证据，不等同证明物理因果或给出自动控制指令。",
    }

def _operation_impact(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    columns: list[str],
    operation_column: str | None,
    response_columns: list[str] | None,
    change_threshold: float = .02,
    pre_window_seconds: int = 60,
    post_window_seconds: int = 300,
    minimum_event_gap_seconds: int = 60,
    max_lag: int = 60,
    requested_interval_seconds: float = 0.0,
    interpolation_limit: int = 3,
) -> dict:
    """Estimate statistical response after discrete operator changes.

    This is deliberately an observational analysis: it screens isolated setpoint
    changes, aligns response traces, verifies them with distributed-lag models,
    and reports operating-range summaries. It never labels the outcome causal.
    """
    operation = operation_column if operation_column in columns else None
    responses = list(dict.fromkeys(
        column for column in (response_columns or []) if column in columns and column != operation
    ))[:12]
    if not operation:
        return {"available": False, "algorithm": "操作影响组合分析", "reason": "请在参数设置中选择操作变量"}
    if not responses:
        return {"available": False, "algorithm": "操作影响组合分析", "reason": "请至少选择一个需要观察响应的数值字段"}
    uniform = _uniform_numeric_frame(
        ordered, time_column, [operation, *responses], requested_interval_seconds, interpolation_limit,
    )
    if uniform is None:
        return {"available": False, "algorithm": "操作影响组合分析", "reason": "请选择有效时间列，并保证操作变量和响应字段具有足够的连续时序数据"}
    aligned, interval, resampled = uniform
    threshold = max(1e-12, min(float(change_threshold), 1e12))
    pre_steps = max(1, min(int(math.ceil(max(1, pre_window_seconds) / interval)), 720))
    post_steps = max(1, min(int(math.ceil(max(5, post_window_seconds) / interval)), 720))
    gap_steps = max(1, min(int(math.ceil(max(0, minimum_event_gap_seconds) / interval)), 720))
    op_values = aligned[operation].astype(float)
    changes = op_values.diff().abs().ge(threshold).fillna(False).to_numpy(dtype=bool)
    candidates = np.flatnonzero(changes)
    events: list[dict] = []
    last_selected = -gap_steps
    for index in candidates:
        if index < pre_steps or index + 1 >= len(aligned) or index - last_selected < gap_steps:
            continue
        # Keep only changes preceded by a short stable setting period.  The later
        # next change is not a rejection condition; it merely limits usable horizon.
        if changes[max(1, index - pre_steps + 1):index].any():
            continue
        before = op_values.iloc[index - pre_steps:index].median()
        after = op_values.iloc[index:min(len(aligned), index + max(2, min(3, gap_steps)))].median()
        delta = float(after - before) if pd.notna(before) and pd.notna(after) else float(op_values.iloc[index] - op_values.iloc[index - 1])
        if not math.isfinite(delta) or abs(delta) < threshold:
            continue
        later = candidates[candidates > index]
        uninterrupted = int((later[0] - index) if len(later) else post_steps)
        events.append({
            "index": int(index), "time": aligned.index[index].isoformat(),
            "operation_before": _safe(before), "operation_after": _safe(after), "operation_change": _safe(delta),
            "direction": "increase" if delta > 0 else "decrease",
            "uninterrupted_seconds": _safe(min(uninterrupted, post_steps) * interval),
        })
        last_selected = index
    if len(events) < 3:
        return {
            "available": False, "algorithm": "操作变更事件响应 + 分布滞后验证 + 工况分段统计",
            "reason": f"仅识别到 {len(events)} 个满足“变更前稳定、事件间隔”条件的操作变更；请降低变更阈值或缩短稳定/事件间隔",
            "operation_column": operation, "response_columns": responses, "event_count": len(events),
        }

    # Response trajectories: each event uses its own pre-change median as baseline.
    grid_steps = sorted(set([0, *range(1, post_steps + 1)]))
    response_items: list[dict] = []
    event_examples: list[dict] = []
    for response in responses:
        series = aligned[response].astype(float)
        timeline = []
        for step in grid_steps:
            samples = []
            for event in events:
                index = event["index"]
                if step > int(round((event["uninterrupted_seconds"] or 0) / interval)) or index + step >= len(series):
                    continue
                baseline = series.iloc[index - pre_steps:index].median()
                value = series.iloc[index + step]
                if pd.notna(baseline) and pd.notna(value):
                    samples.append(float(value - baseline))
            if samples:
                timeline.append({
                    "lag_seconds": _safe(step * interval), "mean_response": _safe(float(np.mean(samples))),
                    "median_response": _safe(float(np.median(samples))), "q25_response": _safe(float(np.quantile(samples, .25))),
                    "q75_response": _safe(float(np.quantile(samples, .75))), "event_count": int(len(samples)),
                })
        usable = [item for item in timeline if item["event_count"] >= max(3, min(8, len(events)))]
        peak = max(usable, key=lambda item: abs(item["median_response"] or 0), default=None)
        consistency = None
        if peak:
            peak_step = int(round((peak["lag_seconds"] or 0) / interval))
            signs = []
            for event in events:
                index = event["index"]
                if peak_step > int(round((event["uninterrupted_seconds"] or 0) / interval)) or index + peak_step >= len(series):
                    continue
                baseline, value = series.iloc[index - pre_steps:index].median(), series.iloc[index + peak_step]
                if pd.notna(baseline) and pd.notna(value):
                    signs.append(float(value - baseline))
            if signs and abs(peak["median_response"] or 0) > 1e-12:
                consistency = float(np.mean(np.sign(signs) == np.sign(peak["median_response"]))) * 100
        response_items.append({
            "column": response, "available": bool(timeline), "peak_lag_seconds": peak.get("lag_seconds") if peak else None,
            "peak_median_response": peak.get("median_response") if peak else None,
            "peak_mean_response": peak.get("mean_response") if peak else None,
            "peak_event_count": peak.get("event_count") if peak else 0,
            "direction_consistency_rate": _safe(consistency), "timeline": timeline,
        })
        for event in events[:100]:
            index = event["index"]
            baseline = series.iloc[index - pre_steps:index].median()
            value = series.iloc[index] if index < len(series) else np.nan
            event_examples.append({
                "time": event["time"], "response_column": response, "direction": event["direction"],
                "operation_change": event["operation_change"], "response_at_event": _safe(float(value - baseline)) if pd.notna(value) and pd.notna(baseline) else None,
                "uninterrupted_seconds": event["uninterrupted_seconds"],
            })

    # Operating-range summaries are intentionally descriptive (not adjustment effects).
    binned_items: list[dict] = []
    base = aligned[[operation, *responses]].dropna(subset=[operation]).copy()
    try:
        bin_codes = pd.qcut(base[operation], q=min(8, max(2, base[operation].nunique())), labels=False, duplicates="drop")
    except (ValueError, TypeError):
        bin_codes = pd.Series(np.zeros(len(base), dtype=int), index=base.index)
    for code, part in base.groupby(bin_codes, sort=True):
        if len(part) < 3:
            continue
        row = {"operation_range": f"{_safe(part[operation].min())} ~ {_safe(part[operation].max())}", "operation_mean": _safe(part[operation].mean()), "sample_size": int(len(part))}
        for response in responses:
            values = part[response].dropna()
            row[f"{response}__mean"] = _safe(values.mean()) if len(values) else None
            row[f"{response}__median"] = _safe(values.median()) if len(values) else None
        binned_items.append(row)

    # A separate chronological distributed-lag model checks whether the operation
    # provides extra explanatory power after the response's own history is known.
    lag = max(1, min(int(max_lag), 120))
    dynamic_summaries = []
    for response in responses:
        model_result = _dynamic_regression(
            ordered, time_column, response, columns, [operation], lag, 1.0, .2,
            requested_interval_seconds, interpolation_limit, True,
        )
        if model_result.get("available"):
            driver_coefficients = [item for item in model_result.get("coefficients", []) if str(item.get("feature", "")).startswith(operation + " ")]
            strongest = driver_coefficients[0] if driver_coefficients else None
            dynamic_summaries.append({
                "column": response, "available": True,
                "test_r2": model_result.get("test_metrics", {}).get("r2"),
                "baseline_r2": (model_result.get("baseline_metrics") or {}).get("r2"),
                "incremental_r2": model_result.get("incremental_r2"),
                "strongest_operation_term": strongest.get("feature") if strongest else None,
                "strongest_operation_coefficient": strongest.get("coefficient") if strongest else None,
            })
        else:
            dynamic_summaries.append({"column": response, "available": False, "reason": model_result.get("reason")})
    return {
        "available": bool(response_items), "algorithm": "操作变更事件响应 + 分布滞后岭回归 + 工况分段统计",
        "operation_column": operation, "response_columns": responses, "sample_interval_seconds": _safe(interval),
        "sample_interval_label": _duration_label(interval), "resampled": resampled,
        "change_threshold": _safe(threshold), "pre_window_seconds": _safe(pre_steps * interval),
        "post_window_seconds": _safe(post_steps * interval), "minimum_event_gap_seconds": _safe(gap_steps * interval),
        "event_count": len(events), "events": [{key: value for key, value in event.items() if key != "index"} for event in events[:300]],
        "responses": response_items, "dynamic_summaries": dynamic_summaries, "operating_bins": binned_items,
        "event_examples": event_examples[:300],
        "note": "先从变更前稳定的操作设定中识别事件，再对齐各响应字段的变化；随后用时间后段的分布滞后回归检查操作变量是否提供额外解释能力，并列出不同操作区间下的描述性统计。现场工况、物料与其他联动操作未纳入时，结果只能作为统计证据，不能直接证明因果。",
    }


def _dynamic_regression(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    target: str | None,
    columns: list[str],
    driver_columns: list[str] | None,
    max_lag: int = 12,
    alpha: float = 1.0,
    test_fraction: float = .2,
    requested_interval_seconds: float = 0.0,
    interpolation_limit: int = 3,
    include_target_history: bool = True,
) -> dict:
    """Chronological distributed-lag ridge regression for time-series exploration."""
    drivers = list(dict.fromkeys(column for column in (driver_columns or []) if column in columns and column != target))[:12]
    if not target or target not in columns:
        return {"available": False, "algorithm": "分布滞后岭回归", "reason": "请选择数值目标列"}
    if not drivers:
        return {"available": False, "algorithm": "分布滞后岭回归", "reason": "请在参数设置中选择至少一个操作/输入字段"}
    uniform = _uniform_numeric_frame(ordered, time_column, [target, *drivers], requested_interval_seconds, interpolation_limit)
    if uniform is None:
        return {"available": False, "algorithm": "分布滞后岭回归", "reason": "请选择有效时间列，并保证目标和输入字段具有足够的时间序列数据"}
    aligned, interval, resampled = uniform
    lag = max(1, min(int(max_lag), 120))
    features, labels = [], []
    if include_target_history:
        for step in range(1, lag + 1):
            features.append(aligned[target].shift(step))
            labels.append(f"{target} (t-{step})")
    for driver in drivers:
        for step in range(0, lag + 1):
            features.append(aligned[driver].shift(step))
            labels.append(f"{driver} (t-{step})")
    design = pd.concat(features, axis=1)
    design.columns = labels
    combined = pd.concat([aligned[target].rename("__target__"), design], axis=1).dropna()
    minimum = max(30, len(labels) * 3)
    if len(combined) < minimum:
        return {"available": False, "algorithm": "分布滞后岭回归", "reason": f"有效连续样本不足，至少需要 {minimum} 条"}
    split = int(len(combined) * (1 - max(.1, min(float(test_fraction), .4))))
    split = max(minimum // 2, min(split, len(combined) - 10))
    train, test = combined.iloc[:split], combined.iloc[split:]
    x_scaler = StandardScaler().fit(train[labels])
    x_train, x_test = x_scaler.transform(train[labels]), x_scaler.transform(test[labels])
    y_train, y_test = train["__target__"].to_numpy(dtype=float), test["__target__"].to_numpy(dtype=float)
    ridge_alpha = max(1e-6, min(float(alpha), 1e6))
    model = Ridge(alpha=ridge_alpha).fit(x_train, y_train)
    predicted = model.predict(x_test)
    def metrics(actual: np.ndarray, estimate: np.ndarray) -> dict:
        residual = actual - estimate
        ss_total = float(np.sum((actual - actual.mean()) ** 2))
        return {
            "mae": _safe(np.mean(np.abs(residual))),
            "rmse": _safe(np.sqrt(np.mean(residual ** 2))),
            "r2": _safe(1 - float(np.sum(residual ** 2)) / ss_total) if ss_total > 1e-12 else None,
        }
    full_metrics = metrics(y_test, predicted)
    baseline_metrics = None
    if include_target_history:
        baseline_labels = labels[:lag]
        baseline_scaler = StandardScaler().fit(train[baseline_labels])
        baseline = Ridge(alpha=ridge_alpha).fit(baseline_scaler.transform(train[baseline_labels]), y_train)
        baseline_metrics = metrics(y_test, baseline.predict(baseline_scaler.transform(test[baseline_labels])))
    coefficient_items = [
        {"feature": label, "coefficient": _safe(value), "abs_coefficient": _safe(abs(value))}
        for label, value in zip(labels, model.coef_)
    ]
    coefficient_items.sort(key=lambda item: item["abs_coefficient"] or 0, reverse=True)
    stride = max(1, math.ceil(len(test) / 1000))
    return {
        "available": True, "algorithm": "按时间切分的分布滞后岭回归（Ridge）",
        "target_column": target, "driver_columns": drivers, "max_lag": lag,
        "lag_seconds": _safe(lag * interval), "sample_interval_seconds": _safe(interval),
        "sample_interval_label": _duration_label(interval), "resampled": resampled,
        "interpolation_limit": interpolation_limit, "include_target_history": include_target_history,
        "ridge_alpha": _safe(ridge_alpha), "train_rows": int(len(train)), "test_rows": int(len(test)),
        "test_metrics": full_metrics, "baseline_metrics": baseline_metrics,
        "incremental_r2": _safe((full_metrics.get("r2") or 0) - (baseline_metrics.get("r2") or 0)) if baseline_metrics and full_metrics.get("r2") is not None and baseline_metrics.get("r2") is not None else None,
        "coefficients": coefficient_items[:100],
        "series": {"timestamps": [value.isoformat() for value in test.index[::stride]], "actual": [_safe(value) for value in y_test[::stride]], "predicted": [_safe(value) for value in predicted[::stride]]},
        "note": "模型只使用训练时间段拟合，并在之后的独立时间段评估。操作字段的系数描述控制其他已选变量后的统计关联；它不自动证明因果，且需要结合时间滞后、工况分段和现场知识解释。若“增量R²”接近0，说明在已知历史料厚后，所选操作字段没有提供明显的额外解释信息。",
    }


def _normality(numeric: pd.DataFrame, columns: list[str], alpha: float = 0.05, max_sample: int = 5000) -> dict:
    items = []
    for column in columns[:20]:
        values = numeric[column].dropna().to_numpy(dtype=float)
        if len(values) < 3 or np.std(values) == 0:
            continue
        sample = values if len(values) <= max_sample else values[np.linspace(0, len(values) - 1, max_sample, dtype=int)]
        if len(sample) <= 5000:
            statistic, p_value = stats.shapiro(sample)
            test_name = "Shapiro-Wilk"
        else:  # Kept as an explicit fallback if the sample policy changes later.
            statistic, p_value = stats.normaltest(sample)
            test_name = "D'Agostino K²"
        ordered = np.sort(sample)
        probabilities = (np.arange(1, len(ordered) + 1) - 0.5) / len(ordered)
        theoretical = stats.norm.ppf(probabilities)
        stride = max(1, math.ceil(len(ordered) / 500))
        items.append({
            "column": column, "algorithm": test_name,
            "source_sample_size": int(len(values)), "sample_size": int(len(sample)),
            "sampled": bool(len(sample) < len(values)), "statistic": _safe(statistic),
            "p_value": _safe(p_value), "alpha": alpha,
            "skew": _safe(stats.skew(sample)), "kurtosis": _safe(stats.kurtosis(sample)),
            "is_normal": bool(p_value >= alpha),
            "qq": [[_safe(x), _safe(y)] for x, y in zip(theoretical[::stride], ordered[::stride])],
            "qq_reference": [
                [_safe(theoretical[0]), _safe(sample.mean() + sample.std(ddof=1) * theoretical[0])],
                [_safe(theoretical[-1]), _safe(sample.mean() + sample.std(ddof=1) * theoretical[-1])],
            ],
        })
    return {"available": bool(items), "algorithm": "Shapiro-Wilk正态性检验 + 正态QQ图", "note": "p值大于等于α只表示当前样本没有足够证据拒绝正态假设，不等于证明数据必然服从正态分布。", "items": items}


def _filled_series(ordered: pd.DataFrame | None, column: str | None) -> np.ndarray | None:
    if ordered is None or not column or column not in ordered.columns:
        return None
    values = pd.to_numeric(ordered[column], errors="coerce").interpolate().ffill().bfill()
    if len(values) < 8 or values.nunique() <= 1:
        return None
    return values.to_numpy(dtype=float)


def _acf_values(values: np.ndarray, max_lag: int) -> np.ndarray:
    centered = values - values.mean()
    denominator = float(np.dot(centered, centered))
    if denominator <= 0:
        return np.zeros(max_lag + 1)
    return np.asarray([
        1.0 if lag == 0 else float(np.dot(centered[:-lag], centered[lag:]) / denominator)
        for lag in range(max_lag + 1)
    ])


def _pacf_yule_walker(acf: np.ndarray, max_lag: int) -> np.ndarray:
    pacf = np.ones(max_lag + 1, dtype=float)
    for lag in range(1, max_lag + 1):
        matrix = toeplitz(acf[:lag])
        vector = acf[1:lag + 1]
        try:
            coefficients = np.linalg.solve(matrix, vector)
            pacf[lag] = float(coefficients[-1])
        except np.linalg.LinAlgError:
            pacf[lag] = np.nan
    return pacf


def _acf_pacf(ordered: pd.DataFrame | None, time_column: str | None, target: str | None, columns: list[str], max_lag: int) -> dict:
    column = target if target else (columns[0] if columns else None)
    raw_values = pd.to_numeric(ordered[column], errors="coerce") if ordered is not None and column in ordered.columns else pd.Series(dtype=float)
    values = _filled_series(ordered, column)
    if values is None:
        return {"available": False, "algorithm": "ACF + Yule-Walker PACF", "reason": "请选择时间列和有效数值目标列"}
    allowed_lag = min(max_lag, max(1, len(values) // 4), 200)
    acf = _acf_values(values, allowed_lag)
    pacf = _pacf_yule_walker(acf, allowed_lag)
    confidence = 1.96 / math.sqrt(len(values))
    return {
        "available": True, "algorithm": "标准化自相关函数(ACF) + Yule-Walker偏自相关(PACF)",
        "column": column, "max_lag": allowed_lag, "sample_size": int(len(values)),
        "filled_missing_count": int(raw_values.isna().sum()),
        "time_start": ordered[time_column].iloc[0].isoformat() if time_column and ordered is not None else None,
        "time_end": ordered[time_column].iloc[-1].isoformat() if time_column and ordered is not None else None,
        "preprocessing": "按时间排序；数值缺失值采用线性插值并对首尾前后填充。",
        "confidence_95": _safe(confidence),
        "lags": list(range(allowed_lag + 1)),
        "acf": [_safe(value) for value in acf], "pacf": [_safe(value) for value in pacf],
    }


def _cross_correlation(
    ordered: pd.DataFrame | None, time_column: str | None, target: str | None,
    columns: list[str], max_lag: int, requested_interval_seconds: float = 0.0,
    interpolation_limit: int = 3,
) -> dict:
    candidate_columns = [item for item in columns if item != target][:20]
    uniform = _uniform_numeric_frame(
        ordered, time_column, [target, *candidate_columns] if target else candidate_columns,
        requested_interval_seconds, interpolation_limit,
    )
    if uniform is None or not target:
        return {"available": False, "algorithm": "双向滞后Pearson互相关", "reason": "请选择时间列和目标列"}
    aligned, interval, resampled = uniform
    target_values = aligned[target]
    items = []
    for column in candidate_columns:
        values = aligned[column]
        correlations = []
        for lag in range(-max_lag, max_lag + 1):
            value = values.shift(lag).corr(target_values)
            seconds = lag * interval
            correlations.append({
                "lag": lag, "lag_seconds": _safe(seconds),
                "lag_label": _duration_label(seconds), "correlation": _safe(value),
            })
        valid = [item for item in correlations if item["correlation"] is not None]
        best = max(valid, key=lambda item: abs(item["correlation"])) if valid else None
        items.append({"column": column, "best": best, "correlations": correlations})
    items.sort(key=lambda item: abs((item.get("best") or {}).get("correlation") or 0), reverse=True)
    return {
        "available": bool(items), "algorithm": "双向滞后Pearson互相关(Cross-Correlation)",
        "target_column": target, "lag_definition": "按等间隔时间轴计算；正滞后表示该字段早于目标列",
        "max_lag": max_lag, "sample_interval_seconds": _safe(interval),
        "sample_interval_label": _duration_label(interval), "resampled": resampled,
        "interpolation_limit": interpolation_limit,
        "items": items,
    }


def _sample_interval_seconds(ordered: pd.DataFrame | None, time_column: str | None) -> float:
    if ordered is None or not time_column:
        return 1.0
    differences = ordered[time_column].diff().dt.total_seconds().dropna()
    positive = differences[differences > 0]
    return float(positive.median()) if len(positive) else 1.0


def _uniform_time_series(
    ordered: pd.DataFrame | None, time_column: str | None, column: str | None,
    requested_interval: float = 0.0,
) -> tuple[np.ndarray, float, bool] | None:
    """Return an evenly sampled series suitable for FFT."""
    if ordered is None or not time_column or not column or column not in ordered.columns:
        return None
    working = pd.DataFrame({
        "time": ordered[time_column],
        "value": pd.to_numeric(ordered[column], errors="coerce"),
    }).dropna()
    if working.empty:
        return None
    grouped = working.groupby("time", as_index=False, sort=True)["value"].mean()
    if len(grouped) < 8 or grouped["value"].nunique() <= 1:
        return None
    seconds = (grouped["time"] - grouped["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    differences = np.diff(seconds)
    positive = differences[differences > 0]
    if not len(positive):
        return None
    natural_interval = float(np.median(positive))
    interval = float(requested_interval) if requested_interval > 0 else natural_interval
    if not math.isfinite(interval) or interval <= 0:
        return None
    regular = np.allclose(differences, interval, rtol=0.01, atol=max(1e-6, interval * 1e-3))
    values = grouped["value"].to_numpy(dtype=float)
    if regular:
        return values, interval, False
    grid = np.arange(0.0, seconds[-1] + interval * 0.5, interval)
    if len(grid) < 8 or len(grid) > MAX_ROWS:
        return None
    return np.interp(grid, seconds, values), interval, True


def _spectrum(
    ordered: pd.DataFrame | None, time_column: str | None, target: str | None,
    columns: list[str], requested_interval: float = 0.0, detrend: bool = True,
) -> dict:
    column = target if target else (columns[0] if columns else None)
    uniform = _uniform_time_series(ordered, time_column, column, requested_interval)
    if uniform is None:
        return {"available": False, "algorithm": "FFT单边功率谱", "reason": "FFT需要至少8个具有有效时间跨度的数值点"}
    values, interval, resampled = uniform
    detrended = signal.detrend(values) if detrend else values
    frequencies = np.fft.rfftfreq(len(detrended), d=interval)
    power = np.abs(np.fft.rfft(detrended)) ** 2 / len(detrended)
    frequencies, power = frequencies[1:], power[1:]
    top_indices = np.argsort(power)[::-1][:10]
    stride = max(1, math.ceil(len(power) / 1000))
    total_power = float(power.sum())
    peaks = []
    for index in top_indices:
        frequency = frequencies[index]
        period = 1 / frequency if frequency > 0 else None
        peaks.append({
            "frequency_hz": _safe(frequency, 10), "power": _safe(power[index]),
            "period_seconds": _safe(period) if period else None,
            "period_label": _duration_label(period) if period else None,
            "power_share_percent": _safe(power[index] / total_power * 100) if total_power > 0 else 0.0,
        })
    return {
        "available": True, "algorithm": "快速傅里叶变换(FFT)单边功率谱",
        "column": column, "sample_size": int(len(values)),
        "sample_interval_seconds": _safe(interval), "resampled": resampled,
        "detrend": detrend, "nyquist_frequency_hz": _safe(1 / (2 * interval), 10),
        "frequency_resolution_hz": _safe(1 / (len(values) * interval), 10),
        "observation_duration_seconds": _safe((len(values) - 1) * interval),
        "note": "仅对等间隔序列计算FFT；主周期是能量最强的重复节律，不代表因果关系。频率高于奈奎斯特上限无法由当前采样率可靠识别。",
        "frequencies_hz": [_safe(value, 10) for value in frequencies[::stride]],
        "power": [_safe(value) for value in power[::stride]], "dominant_peaks": peaks,
    }


def _filtering(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    target: str | None,
    columns: list[str],
    method: str,
    window: int,
    cutoff_ratio: float,
    butterworth_order: int = 4,
) -> dict:
    column = target if target else (columns[0] if columns else None)
    values = _filled_series(ordered, column)
    names = {
        "moving_average": "滑动平均滤波",
        "median": "中值滤波",
        "butterworth": "Butterworth低通滤波",
    }
    if method not in names:
        raise ValueError("滤波算法仅支持 moving_average、median、butterworth")
    if values is None:
        return {"available": False, "algorithm": names[method], "reason": "请选择时间列和有效数值目标列"}
    window = max(3, min(int(window), 101))
    if window % 2 == 0:
        window += 1
    zero_phase = False
    if method == "moving_average":
        filtered = pd.Series(values).rolling(window, center=True, min_periods=1).mean().to_numpy()
        edge_note = "滑动平均在序列两端可用邻近点较少，端点结果仅供参考。"
    elif method == "median":
        filtered = signal.medfilt(values, kernel_size=min(window, len(values) if len(values) % 2 else len(values) - 1))
        edge_note = "中值滤波对孤立尖峰更稳健；序列边界会使用补零处理，端点结果仅供参考。"
    else:
        cutoff_ratio = min(.99, max(.01, float(cutoff_ratio)))
        sos = signal.butter(butterworth_order, cutoff_ratio, btype="lowpass", output="sos")
        zero_phase = len(values) > max(30, 3 * (2 * butterworth_order + 1))
        filtered = signal.sosfiltfilt(sos, values) if zero_phase else signal.sosfilt(sos, values)
        edge_note = "使用双向零相位滤波，不引入时间滞后。" if zero_phase else "样本较短，改用单向滤波；结果可能存在相位滞后，端点应谨慎解释。"
    residual = values - filtered
    stride = max(1, math.ceil(len(values) / 1000))
    timestamps = ordered[time_column].iloc[::stride] if ordered is not None and time_column else range(0, len(values), stride)
    return {
        "available": True, "algorithm": names[method], "method": method,
        "column": column, "window": window, "cutoff_ratio_to_nyquist": _safe(cutoff_ratio),
        "butterworth_order": butterworth_order, "zero_phase": zero_phase,
        "edge_note": edge_note, "sample_size": int(len(values)),
        "noise_std_before": _safe(np.std(np.diff(values))), "residual_std": _safe(np.std(residual)),
        "timestamps": [value.isoformat() if isinstance(value, pd.Timestamp) else str(value) for value in timestamps],
        "original": [_safe(value) for value in values[::stride]],
        "filtered": [_safe(value) for value in filtered[::stride]],
    }




def _hampel(ordered: pd.DataFrame | None, time_column: str | None, columns: list[str], window: int = 11, threshold: float = 3.0) -> dict:
    if ordered is None or not time_column:
        return {"available": False, "algorithm": "Hampel局部异常检测", "reason": "请选择可解析的时间列"}
    window = max(3, min(int(window), 101))
    if window % 2 == 0:
        window += 1
    items = []
    for column in columns[:50]:
        values = pd.to_numeric(ordered[column], errors="coerce")
        if values.notna().sum() < window:
            continue
        median = values.rolling(window, center=True, min_periods=max(3, window // 2)).median()
        deviation = (values - median).abs()
        mad = deviation.rolling(window, center=True, min_periods=max(3, window // 2)).median()
        flags = ((deviation > threshold * 1.4826 * mad) & values.notna() & median.notna()).fillna(False)
        indexes = np.flatnonzero(flags.to_numpy())[:100]
        items.append({"column": column, "count": int(flags.sum()), "rate": _safe(flags.mean() * 100), "points": [{"row": int(index), "time": ordered[time_column].iloc[index].isoformat(), "value": _safe(values.iloc[index]), "local_median": _safe(median.iloc[index])} for index in indexes]})
    return {"available": bool(items), "algorithm": "Hampel局部中位数 + MAD稳健异常检测", "window": window, "threshold": threshold, "note": "适合识别短时尖峰；不会把缓慢的真实工况变化直接判为异常。", "items": sorted(items, key=lambda item: item["count"], reverse=True)}


def _sensor_health(ordered: pd.DataFrame | None, time_column: str | None, columns: list[str], freeze_points: int = 20, jump_sigma: float = 6.0) -> dict:
    if ordered is None or not time_column:
        return {"available": False, "algorithm": "仪表健康分析", "reason": "请选择可解析的时间列"}
    freeze_points = max(3, min(int(freeze_points), 10000))
    items = []
    for column in columns[:50]:
        values = pd.to_numeric(ordered[column], errors="coerce")
        valid = values.dropna()
        if len(valid) < 3:
            continue
        iqr = float(valid.quantile(.75) - valid.quantile(.25))
        tolerance = max(abs(iqr) * 1e-6, 1e-12)
        same = values.diff().abs().le(tolerance).fillna(False).to_numpy()
        runs, start = [], None
        for index, is_same in enumerate(same):
            if is_same and start is None:
                start = index - 1
            if start is not None and (not is_same or index == len(same) - 1):
                end = index if is_same and index == len(same) - 1 else index - 1
                points = end - start + 1
                if points >= freeze_points:
                    runs.append({"start_row": int(start), "end_row": int(end), "points": int(points), "start_time": ordered[time_column].iloc[start].isoformat(), "end_time": ordered[time_column].iloc[end].isoformat()})
                start = None
        diffs = values.diff().abs().dropna()
        median_diff = float(diffs.median()) if len(diffs) else 0.0
        mad_diff = float((diffs - median_diff).abs().median()) if len(diffs) else 0.0
        jump_limit = median_diff + jump_sigma * 1.4826 * max(mad_diff, abs(iqr) * 1e-6, 1e-12)
        jump_flags = values.diff().abs().gt(jump_limit).fillna(False)
        jump_indexes = np.flatnonzero(jump_flags.to_numpy())[:50]
        health = "warning" if runs or len(jump_indexes) else "good"
        items.append({"column": column, "health": health, "freeze_count": len(runs), "freeze_points": int(sum(item["points"] for item in runs)), "freeze_runs": runs[:20], "jump_count": int(jump_flags.sum()), "jump_limit": _safe(jump_limit), "jumps": [{"row": int(index), "time": ordered[time_column].iloc[index].isoformat(), "change": _safe(values.diff().abs().iloc[index])} for index in jump_indexes]})
    return {"available": bool(items), "algorithm": "死值/冻结检测 + 鲁棒变化率跳变检测", "freeze_points": freeze_points, "jump_sigma": jump_sigma, "note": "这是仪表健康提示，不等同于工艺物理边界判定；物理上下限应通过项目规则配置。", "items": sorted(items, key=lambda item: (item["health"] != "good", item["freeze_points"] + item["jump_count"]), reverse=True)}


def _operating_segments(
    ordered: pd.DataFrame | None,
    time_column: str | None,
    columns: list[str],
    gap_seconds: float = 0.0,
    minimum_points: int = 10,
    condition_column: str | None = None,
    condition_min: float | None = None,
    condition_max: float | None = None,
    max_change: float = 0.0,
) -> dict:
    """Split usable operating periods by time continuity and optional process conditions."""
    if ordered is None or not time_column or len(ordered) < 2:
        return {"available": False, "algorithm": "连续运行段划分", "reason": "至少需要两条有序时间记录"}
    differences = ordered[time_column].diff().dt.total_seconds()
    positive = differences[differences > 0]
    natural = float(positive.median()) if len(positive) else 1.0
    cutoff = float(gap_seconds) if gap_seconds > 0 else max(natural * 3, natural + 1e-6)
    minimum_points = max(2, min(int(minimum_points), 100000))
    condition_column = str(condition_column or "").strip() or None
    try: condition_min = float(condition_min) if condition_min is not None else None
    except (TypeError, ValueError): condition_min = None
    try: condition_max = float(condition_max) if condition_max is not None else None
    except (TypeError, ValueError): condition_max = None
    max_change = max(0.0, float(max_change or 0.0))
    if condition_min is not None and condition_max is not None and condition_min > condition_max:
        raise ValueError("运行段工况下限不能大于上限")
    eligible = pd.Series(True, index=ordered.index)
    condition_values = None
    if condition_column:
        if condition_column not in ordered.columns:
            raise ValueError(f"运行段工况字段不存在: {condition_column}")
        condition_values = pd.to_numeric(ordered[condition_column], errors="coerce")
        eligible &= condition_values.notna()
        if condition_min is not None: eligible &= condition_values >= condition_min
        if condition_max is not None: eligible &= condition_values <= condition_max
    time_break = differences.ge(cutoff).fillna(False)
    change_break = pd.Series(False, index=ordered.index)
    if condition_values is not None and max_change > 0:
        change_break = condition_values.diff().abs().gt(max_change).fillna(False)
    starts, segments = [], []
    for index in range(len(ordered)):
        usable = bool(eligible.iloc[index])
        preceding_break = index == 0 or not bool(eligible.iloc[index - 1]) or bool(time_break.iloc[index]) or bool(change_break.iloc[index])
        if usable and preceding_break:
            starts.append(index)
        ending_break = index == len(ordered) - 1 or not bool(eligible.iloc[index + 1]) or bool(time_break.iloc[index + 1]) or bool(change_break.iloc[index + 1])
        if usable and ending_break and starts:
            start = starts[-1]; end = index; part = ordered.iloc[start:end + 1]
            complete_rate = part[columns].notna().all(axis=1).mean() if columns else 1.0
            duration = (part[time_column].iloc[-1] - part[time_column].iloc[0]).total_seconds()
            item = {"segment_id": len(segments) + 1, "start_row": start, "end_row": end, "points": int(len(part)), "start_time": part[time_column].iloc[0].isoformat(), "end_time": part[time_column].iloc[-1].isoformat(), "duration_seconds": _safe(duration), "duration_label": _duration_label(duration), "complete_row_rate": _safe(complete_rate * 100), "meets_minimum_points": bool(len(part) >= minimum_points)}
            if condition_values is not None:
                values = condition_values.iloc[start:end + 1].dropna()
                item["condition_min_value"] = _safe(values.min()) if len(values) else None
                item["condition_max_value"] = _safe(values.max()) if len(values) else None
            segments.append(item)
    condition = {"column": condition_column, "minimum": condition_min, "maximum": condition_max, "max_change": max_change} if condition_column else None
    return {"available": True, "algorithm": "时间断点 + 可选工况范围/突变切分", "natural_interval_seconds": _safe(natural), "gap_threshold_seconds": _safe(cutoff), "minimum_points": minimum_points, "condition": condition, "eligible_rows": int(eligible.sum()), "excluded_rows": int((~eligible).sum()), "segment_count": len(segments), "segments": segments[:500], "note": "默认仅按时间断点切分；选择工况字段后，超出取值范围、缺失或相邻变化超过阈值的记录会作为新的运行段边界。合格段可直接保存为数据集。"}


def _window_quality(ordered: pd.DataFrame | None, time_column: str | None, columns: list[str], window_seconds: int = 60, minimum_valid_ratio: float = .5) -> dict:
    if ordered is None or not time_column:
        return {"available": False, "algorithm": "窗口质量评分", "reason": "请选择可解析的时间列"}
    window_seconds = max(1, min(int(window_seconds), 86400))
    minimum_valid_ratio = max(.05, min(float(minimum_valid_ratio), 1.0))
    interval = max(_sample_interval_seconds(ordered, time_column), 1e-6)
    expected_points = max(1, int(math.ceil(window_seconds / interval)))
    working = ordered[[time_column, *columns]].copy()
    working["_window"] = working[time_column].dt.floor(f"{window_seconds}s")
    windows = []
    for stamp, part in working.groupby("_window", sort=True):
        observed_ratio = min(1.0, len(part) / expected_points)
        complete_ratio = float(part[columns].notna().all(axis=1).mean()) if columns else 1.0
        frozen = [column for column in columns if (lambda values: len(values) >= 3 and values.nunique() <= 1)(pd.to_numeric(part[column], errors="coerce").dropna())]
        reasons = []
        if observed_ratio < minimum_valid_ratio:
            reasons.append("点数不足")
        if complete_ratio < minimum_valid_ratio:
            reasons.append("有效字段不足")
        if frozen:
            reasons.append("窗口冻结")
        windows.append({"start_time": stamp.isoformat(), "points": int(len(part)), "observed_ratio": _safe(observed_ratio * 100), "complete_row_ratio": _safe(complete_ratio * 100), "frozen_columns": frozen, "status": "pass" if not reasons else "fail", "reasons": reasons})
    failed = [item for item in windows if item["status"] == "fail"]
    return {"available": bool(windows), "algorithm": "固定窗口有效率 + 完整行率 + 冻结复核", "window_seconds": window_seconds, "expected_points": expected_points, "minimum_valid_ratio": minimum_valid_ratio, "total_windows": len(windows), "passed_windows": len(windows) - len(failed), "failed_windows": len(failed), "pass_rate": _safe((len(windows) - len(failed)) / len(windows) * 100) if windows else 0, "failed_examples": failed[:300], "note": "用于判断时间窗口是否适合建模或聚合；阈值可按项目调整。"}


def _effective_excitation(ordered: pd.DataFrame | None, time_column: str | None, target: str | None, columns: list[str], minimum_active_ratio: float = .05, change_fraction: float = .01) -> dict:
    if ordered is None or not time_column:
        return {"available": False, "algorithm": "有效激励分析", "reason": "请选择可解析的时间列"}
    features = [column for column in columns if column != target][:50]
    if not features:
        return {"available": False, "algorithm": "有效激励分析", "reason": "请选择至少一个数值输入字段"}
    minimum_active_ratio = max(.001, min(float(minimum_active_ratio), .95))
    change_fraction = max(1e-6, min(float(change_fraction), 1.0))
    items = []
    for column in features:
        values = pd.to_numeric(ordered[column], errors="coerce").dropna().to_numpy(dtype=float)
        if len(values) < 3:
            continue
        q05, q95 = np.quantile(values, [.05, .95])
        span = float(q95 - q05)
        changes = np.abs(np.diff(values))
        threshold = max(abs(span) * change_fraction, 1e-12)
        active = changes > threshold
        events = int(np.sum(active & np.r_[True, ~active[:-1]]))
        ratio = float(active.mean()) if len(active) else 0.0
        unique = int(len(np.unique(values)))
        adequate = bool(ratio >= minimum_active_ratio and unique >= 10 and span > 0)
        items.append({"column": column, "status": "adequate" if adequate else "weak", "active_ratio": _safe(ratio * 100), "event_count": events, "robust_range": _safe(span), "change_threshold": _safe(threshold), "unique_values": unique})
    return {"available": bool(items), "algorithm": "相邻变化覆盖率 + 鲁棒变化范围", "target_column": target, "minimum_active_ratio": minimum_active_ratio, "change_fraction": change_fraction, "adequate_count": sum(item["status"] == "adequate" for item in items), "note": "用于判断输入信号是否具有足够变化；它是建模前检查，不代表已经证明因果关系。", "items": sorted(items, key=lambda item: (item["status"] == "adequate", item["active_ratio"] or 0), reverse=True)}

def analyze_frame(
    frame: pd.DataFrame,
    *,
    source_name: str,
    analyses: list[str] | None = None,
    numeric_columns: list[str] | None = None,
    time_column: str | None = None,
    target_column: str | None = None,
    max_lag: int = 24,
    filter_method: str = "moving_average",
    filter_window: int = 5,
    filter_cutoff_ratio: float = 0.1,
    algorithm_params: dict[str, dict[str, Any]] | None = None,
) -> dict:
    if frame.empty:
        raise ValueError("数据集没有可分析的数据行")
    frame = frame.copy()
    frame.columns = [str(column) for column in frame.columns]
    original_rows = len(frame)
    truncated = original_rows > MAX_ROWS
    if truncated:
        frame = frame.iloc[:MAX_ROWS].copy()
    selected = list(dict.fromkeys(analyses or SUPPORTED_ANALYSES))
    invalid = [item for item in selected if item not in SUPPORTED_ANALYSES]
    if invalid:
        raise ValueError("不支持的分析类型: " + "、".join(invalid))
    numeric, columns = _numeric_frame(frame, numeric_columns)
    ordered, detected_time = _time_frame(frame, time_column)
    if target_column and target_column not in frame.columns:
        raise ValueError(f"目标列不存在: {target_column}")
    if (
        target_column and target_column not in columns
        and pd.api.types.is_numeric_dtype(frame[target_column])
    ):
        numeric[target_column] = pd.to_numeric(frame[target_column], errors="coerce")
        columns.append(target_column)
    max_lag = max(1, min(int(max_lag or 24), 200))
    params = algorithm_params or {}
    def values_for(name: str) -> dict[str, Any]:
        value = params.get(name, {})
        return value if isinstance(value, dict) else {}
    distribution_params = values_for("distribution")
    anomaly_params = values_for("anomaly")
    trend_params = values_for("trend")
    lag_params = values_for("lag")
    change_params = values_for("change_point")
    pca_params = values_for("pca")
    normality_params = values_for("normality")
    acf_params = values_for("acf_pacf")
    cross_params = values_for("cross_correlation")
    spectrum_params = values_for("spectrum")
    filtering_params = values_for("filtering")
    mutual_params = values_for("mutual_information")
    drift_params = values_for("time_drift")
    hampel_params = values_for("hampel")
    health_params = values_for("sensor_health")
    segment_params = values_for("operating_segments")
    quality_params = values_for("window_quality")
    excitation_params = values_for("effective_excitation")
    cca_params = values_for("cca")
    dynamic_params = values_for("dynamic_regression")
    consistency_params = values_for("sensor_consistency")
    operation_impact_params = values_for("operation_impact")
    attribution_params = values_for("period_impact_attribution") or values_for("anomaly_attribution")
    lag_max = max(1, min(int(lag_params.get("max_lag", max_lag)), 200))
    acf_max = max(1, min(int(acf_params.get("max_lag", max_lag)), 200))
    cross_max = max(1, min(int(cross_params.get("max_lag", max_lag)), 200))
    lag_interval = max(0.0, float(lag_params.get("resample_interval_seconds", 0.0)))
    lag_interpolation = max(0, min(int(lag_params.get("interpolation_limit", 3)), 48))
    cross_interval = max(0.0, float(cross_params.get("resample_interval_seconds", 0.0)))
    cross_interpolation = max(0, min(int(cross_params.get("interpolation_limit", 3)), 48))
    result = {
        "source_name": source_name, "total_rows": original_rows, "analyzed_rows": len(frame),
        "truncated": truncated, "numeric_columns": columns,
        "time_column": detected_time, "target_column": target_column,
        "variable_roles": {
            "dependent": target_column,
            "independent": [column for column in columns if column != target_column],
            "time": detected_time,
            "excluded": [
                column for column in frame.columns
                if column not in columns and column != detected_time and column != target_column
            ],
        },
        "analyses": {},
    }
    if "overview" in selected:
        result["analyses"]["overview"] = profile_frame(frame, source_name=source_name, total_rows=original_rows, time_column=time_column, target_column=target_column)
    if "time_quality" in selected:
        result["analyses"]["time_quality"] = _time_quality(frame, time_column)
    if "distribution" in selected:
        result["analyses"]["distribution"] = _distribution(numeric, columns, max(0, min(int(distribution_params.get("bins", 0)), 100)))
    if "correlation" in selected:
        result["analyses"]["correlation"] = {"available": len(columns) >= 2, "pearson": _matrix(numeric.corr("pearson")), "spearman": _matrix(numeric.corr("spearman"))}
    if "cca" in selected:
        result["analyses"]["cca"] = _cca(
            numeric, columns,
            cca_params.get("x_columns"), cca_params.get("y_columns"),
            max(1, min(int(cca_params.get("components", 3)), 20)),
            max(100, min(int(cca_params.get("sample_size", 20_000)), 100_000)),
        )
    if "sensor_consistency" in selected:
        result["analyses"]["sensor_consistency"] = _sensor_consistency(
            ordered, detected_time, numeric, columns,
            consistency_params.get("sensor_columns"),
            str(consistency_params.get("reference_method", "median")),
            max(1.0, min(float(consistency_params.get("threshold", 3.0)), 10.0)),
        )
    if "period_impact_attribution" in selected:
        result["analyses"]["period_impact_attribution"] = _period_impact_attribution(
            ordered, detected_time, columns,
            attribution_params.get("target_columns"), attribution_params.get("candidate_columns"),
            max(1, min(int(attribution_params.get("max_lag", 24)), 120)),
            max(0.0, float(attribution_params.get("resample_interval_seconds", 0.0))),
            max(0, min(int(attribution_params.get("interpolation_limit", 3)), 48)),
            attribution_params.get("enabled_methods"),
            max(0.0001, min(float(attribution_params.get("ridge_alpha", 1.0)), 1000.0)),
            max(0.1, min(float(attribution_params.get("holdout_ratio", 0.2)), 0.4)),
            max(2, min(int(attribution_params.get("rolling_folds", 3)), 5)),
        )
    if "anomaly_attribution" in selected:
        result["analyses"]["anomaly_attribution"] = _anomaly_attribution(
            ordered, detected_time, columns,
            attribution_params.get("target_columns"), attribution_params.get("candidate_columns"),
            max(3, min(int(attribution_params.get("hampel_window", 11)), 101)),
            max(1.0, min(float(attribution_params.get("threshold", 3.0)), 10.0)),
            max(5, min(int(attribution_params.get("pre_window_seconds", 300)), 3600)),
            max(0, min(int(attribution_params.get("minimum_event_gap_seconds", 60)), 3600)),
            max(1, min(int(attribution_params.get("max_lag", 24)), 120)),
            max(0.0, float(attribution_params.get("resample_interval_seconds", 0.0))),
            max(0, min(int(attribution_params.get("interpolation_limit", 3)), 48)),
        )
    if "operation_impact" in selected:
        result["analyses"]["operation_impact"] = _operation_impact(
            ordered, detected_time, columns,
            operation_impact_params.get("operation_column"), operation_impact_params.get("response_columns"),
            max(1e-12, float(operation_impact_params.get("change_threshold", .02))),
            max(5, min(int(operation_impact_params.get("pre_window_seconds", 60)), 3600)),
            max(5, min(int(operation_impact_params.get("post_window_seconds", 300)), 3600)),
            max(0, min(int(operation_impact_params.get("minimum_event_gap_seconds", 60)), 3600)),
            max(1, min(int(operation_impact_params.get("max_lag", 60)), 120)),
            max(0.0, float(operation_impact_params.get("resample_interval_seconds", 0.0))),
            max(0, min(int(operation_impact_params.get("interpolation_limit", 3)), 48)),
        )
    if "dynamic_regression" in selected:
        result["analyses"]["dynamic_regression"] = _dynamic_regression(
            ordered, detected_time, target_column, columns,
            dynamic_params.get("driver_columns"),
            max(1, min(int(dynamic_params.get("max_lag", 12)), 120)),
            max(1e-6, min(float(dynamic_params.get("alpha", 1.0)), 1e6)),
            max(.1, min(float(dynamic_params.get("test_fraction", .2)), .4)),
            max(0.0, float(dynamic_params.get("resample_interval_seconds", 0.0))),
            max(0, min(int(dynamic_params.get("interpolation_limit", 3)), 48)),
            bool(dynamic_params.get("include_target_history", True)),
        )
    if "anomaly" in selected:
        result["analyses"]["anomaly"] = _anomaly(
            numeric, columns,
            max(.5, min(float(anomaly_params.get("iqr_multiplier", 1.5)), 5.0)),
            max(1.0, min(float(anomaly_params.get("threshold_sigma", 5.0)), 10.0)),
            max(50, min(int(anomaly_params.get("estimators", 100)), 500)),
            max(1.0, min(float(anomaly_params.get("sigma_multiplier", 3.0)), 6.0)),
            frame[detected_time] if detected_time and detected_time in frame.columns else None,
        )
    if "trend" in selected:
        result["analyses"]["trend"] = _trend(ordered, detected_time, columns, max(0, min(int(trend_params.get("window", 0)), 500)))
    if "lag" in selected:
        result["analyses"]["lag"] = _lag(
            ordered, detected_time, target_column, columns, lag_max,
            lag_interval, lag_interpolation,
        )
    if "change_point" in selected:
        result["analyses"]["change_point"] = _change_points(
            ordered, detected_time, target_column, columns,
            max(0, min(int(change_params.get("window", 0)), 500)),
            max(1.0, min(float(change_params.get("threshold_sigma", 3.0)), 10.0)),
        )
    if "pca" in selected:
        result["analyses"]["pca"] = _pca(numeric, columns, max(2, min(int(pca_params.get("components", 5)), 20)))
    if "normality" in selected:
        result["analyses"]["normality"] = _normality(
            numeric, columns,
            max(.001, min(float(normality_params.get("alpha", .05)), .2)),
            max(50, min(int(normality_params.get("sample_size", 5000)), 5000)),
        )
    if "acf_pacf" in selected:
        result["analyses"]["acf_pacf"] = _acf_pacf(ordered, detected_time, target_column, columns, acf_max)
    if "cross_correlation" in selected:
        result["analyses"]["cross_correlation"] = _cross_correlation(
            ordered, detected_time, target_column, columns, cross_max,
            cross_interval, cross_interpolation,
        )
    if "spectrum" in selected:
        result["analyses"]["spectrum"] = _spectrum(
            ordered, detected_time, target_column, columns,
            max(0.0, float(spectrum_params.get("resample_interval_seconds", 0.0))),
            bool(spectrum_params.get("detrend", True)),
        )
    if "filtering" in selected:
        result["analyses"]["filtering"] = _filtering(
            ordered, detected_time, target_column, columns,
            str(filtering_params.get("method", filter_method)),
            int(filtering_params.get("window", filter_window)),
            float(filtering_params.get("cutoff_ratio", filter_cutoff_ratio)),
            max(1, min(int(filtering_params.get("order", 4)), 10)),
        )
    if "mutual_information" in selected:
        result["analyses"]["mutual_information"] = _mutual_information(
            numeric, target_column, columns,
            max(2, min(int(mutual_params.get("neighbors", 3)), 20)),
            max(100, min(int(mutual_params.get("sample_size", 20_000)), 100_000)),
        )
    if "time_drift" in selected:
        result["analyses"]["time_drift"] = _time_drift(
            ordered, numeric, columns,
            max(.1, min(float(drift_params.get("window_fraction", .3)), .5)),
            max(.001, min(float(drift_params.get("alpha", .05)), .2)),
            max(5, min(int(drift_params.get("psi_bins", 10)), 50)),
        )
    if "hampel" in selected:
        result["analyses"]["hampel"] = _hampel(ordered, detected_time, columns, max(3, min(int(hampel_params.get("window", 11)), 101)), max(1.0, min(float(hampel_params.get("threshold", 3.0)), 10.0)))
    if "sensor_health" in selected:
        result["analyses"]["sensor_health"] = _sensor_health(ordered, detected_time, columns, max(3, min(int(health_params.get("freeze_points", 20)), 10000)), max(1.0, min(float(health_params.get("jump_sigma", 6.0)), 20.0)))
    if "operating_segments" in selected:
        result["analyses"]["operating_segments"] = _operating_segments(
            ordered, detected_time, columns,
            max(0.0, float(segment_params.get("gap_seconds", 0.0))),
            max(2, min(int(segment_params.get("minimum_points", 10)), 100000)),
            segment_params.get("condition_column"),
            segment_params.get("condition_min"), segment_params.get("condition_max"),
            max(0.0, float(segment_params.get("max_change", 0.0) or 0.0)),
        )
    if "window_quality" in selected:
        result["analyses"]["window_quality"] = _window_quality(ordered, detected_time, columns, max(1, min(int(quality_params.get("window_seconds", 60)), 86400)), max(.05, min(float(quality_params.get("minimum_valid_ratio", .5)), 1.0)))
    if "effective_excitation" in selected:
        result["analyses"]["effective_excitation"] = _effective_excitation(ordered, detected_time, target_column, columns, max(.001, min(float(excitation_params.get("minimum_active_ratio", .05)), .95)), max(1e-6, min(float(excitation_params.get("change_fraction", .01)), 1.0)))
    return result
