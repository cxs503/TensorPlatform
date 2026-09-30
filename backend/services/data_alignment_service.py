"""Safe, time-aware alignment for two tabular time-series datasets."""

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd


MAX_ALIGNED_ROWS = 100_000
SUPPORTED_METHODS = {"nearest", "linear", "ffill", "bfill"}


class DataAlignmentError(ValueError):
    pass


@dataclass
class AlignmentOutput:
    frame: pd.DataFrame
    stats: dict[str, Any]
    warnings: list[str]


def _prepare_frame(frame: pd.DataFrame, time_column: str, label: str) -> pd.DataFrame:
    if frame.empty:
        raise DataAlignmentError(f"{label}数据集为空")
    if time_column not in frame.columns:
        raise DataAlignmentError(f"{label}数据集不存在时间列: {time_column!r}")
    if pd.api.types.is_numeric_dtype(frame[time_column]):
        raise DataAlignmentError(
            f"{label}时间列 {time_column!r} 是普通数值列；请先转换为明确的日期时间格式"
        )

    parsed = pd.to_datetime(frame[time_column], errors="coerce", utc=True)
    invalid = parsed.isna()
    if invalid.any():
        examples = ", ".join(repr(value) for value in frame.loc[invalid, time_column].astype(str).head(3))
        raise DataAlignmentError(
            f"{label}时间列 {time_column!r} 含 {int(invalid.sum())} 个无效或空时间"
            f"（例如 {examples}）"
        )

    prepared = frame.drop(columns=[time_column]).copy()
    if not len(prepared.columns):
        raise DataAlignmentError(f"{label}数据集除时间列外没有可对齐字段")
    prepared.index = pd.DatetimeIndex(parsed.dt.tz_convert(None), name="_time")
    duplicates = int(prepared.index.duplicated(keep=False).sum())
    if duplicates:
        raise DataAlignmentError(
            f"{label}时间列包含 {duplicates} 条重复时间记录；请先聚合或去重后再对齐"
        )
    return prepared.sort_index()


def _parse_tolerance(value: Optional[str]) -> Optional[pd.Timedelta]:
    if value is None or not str(value).strip():
        return None
    try:
        tolerance = pd.to_timedelta(str(value))
    except (TypeError, ValueError) as exc:
        raise DataAlignmentError(f"最大时间差格式无效: {value!r}") from exc
    if tolerance <= pd.Timedelta(0):
        raise DataAlignmentError("最大时间差必须大于 0")
    return tolerance


def _timeline_from_frequency(start: pd.Timestamp, end: pd.Timestamp, frequency: str) -> pd.DatetimeIndex:
    try:
        offset = pd.tseries.frequencies.to_offset(str(frequency))
        if offset.nanos <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise DataAlignmentError(f"重采样频率无效: {frequency!r}") from exc
    timeline = pd.date_range(start=start, end=end, freq=offset, name="_time")
    if timeline.empty:
        raise DataAlignmentError("指定频率在共同时间范围内没有生成任何时间点")
    if len(timeline) > MAX_ALIGNED_ROWS:
        raise DataAlignmentError(
            f"对齐结果预计超过 {MAX_ALIGNED_ROWS} 行，请增大重采样间隔"
        )
    return timeline


def _asof_align(
    frame: pd.DataFrame,
    timeline: pd.DatetimeIndex,
    direction: str,
    tolerance: Optional[pd.Timedelta],
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    left = pd.DataFrame({"_time": timeline})
    right = frame.reset_index().rename(columns={"_time": "_matched_time"})
    merged = pd.merge_asof(
        left.sort_values("_time"),
        right.sort_values("_matched_time"),
        left_on="_time",
        right_on="_matched_time",
        direction=direction,
        tolerance=tolerance,
        allow_exact_matches=True,
    )
    matched_time = merged.pop("_matched_time")
    delta = (merged.pop("_time") - matched_time).abs()
    if len(merged.columns):
        has_values = merged.notna().any(axis=1)
        matched_time = matched_time.where(has_values)
        delta = delta.where(has_values)
    merged.index = timeline
    return merged, matched_time.set_axis(timeline), delta.set_axis(timeline)


def _linear_align(
    frame: pd.DataFrame,
    timeline: pd.DatetimeIndex,
    tolerance: Optional[pd.Timedelta],
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    expanded = frame.index.union(timeline).sort_values()
    result = pd.DataFrame(index=timeline)
    numeric_columns = frame.select_dtypes(include=[np.number]).columns.tolist()
    categorical_columns = [column for column in frame.columns if column not in numeric_columns]

    if numeric_columns:
        numeric = (
            frame[numeric_columns]
            .reindex(expanded)
            .interpolate(method="time", limit_area="inside")
            .reindex(timeline)
        )
        result[numeric_columns] = numeric
    if categorical_columns:
        # Categories cannot be interpolated; use the most recent known state.
        categorical = frame[categorical_columns].reindex(expanded).ffill().reindex(timeline)
        result[categorical_columns] = categorical

    _, matched_time, delta = _asof_align(frame.iloc[:, :0], timeline, "nearest", tolerance)
    value_columns = numeric_columns or categorical_columns
    has_values = result[value_columns].notna().any(axis=1)
    matched_time = matched_time.where(has_values)
    delta = delta.where(has_values)
    result.loc[~has_values, :] = np.nan
    if tolerance is not None:
        result.loc[matched_time.isna(), :] = np.nan
    return result.reindex(columns=frame.columns), matched_time, delta


def _align_to_timeline(
    frame: pd.DataFrame,
    timeline: pd.DatetimeIndex,
    method: str,
    tolerance: Optional[pd.Timedelta],
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    if method == "linear":
        return _linear_align(frame, timeline, tolerance)
    direction = {"nearest": "nearest", "ffill": "backward", "bfill": "forward"}[method]
    return _asof_align(frame, timeline, direction, tolerance)


def align_dataframes(
    source_frame: pd.DataFrame,
    target_frame: pd.DataFrame,
    *,
    source_time_column: str,
    target_time_column: str,
    method: str = "nearest",
    frequency: Optional[str] = None,
    tolerance: Optional[str] = "5min",
) -> AlignmentOutput:
    method = str(method or "").lower()
    if method not in SUPPORTED_METHODS:
        raise DataAlignmentError(
            f"不支持的对齐方式: {method!r}（可选 nearest/linear/ffill/bfill）"
        )

    source = _prepare_frame(source_frame, source_time_column, "源")
    target = _prepare_frame(target_frame, target_time_column, "目标")
    parsed_tolerance = _parse_tolerance(tolerance)
    actual_overlap_start = max(source.index.min(), target.index.min())
    actual_overlap_end = min(source.index.max(), target.index.max())

    if frequency:
        if actual_overlap_start > actual_overlap_end:
            raise DataAlignmentError("两个数据集不存在共同时间范围，无法按固定频率对齐")
        timeline = _timeline_from_frequency(actual_overlap_start, actual_overlap_end, frequency)
        source_aligned, source_match, source_delta = _align_to_timeline(
            source, timeline, method, parsed_tolerance
        )
    else:
        if parsed_tolerance is None:
            timeline = source.index
        else:
            lower = target.index.min() - parsed_tolerance
            upper = target.index.max() + parsed_tolerance
            timeline = source.index[(source.index >= lower) & (source.index <= upper)]
        if timeline.empty:
            raise DataAlignmentError("两个数据集在允许的最大时间差内没有可对齐记录")
        source_aligned = source.reindex(timeline)
        source_match = pd.Series(timeline, index=timeline)
        source_delta = pd.Series(pd.to_timedelta(np.zeros(len(timeline)), unit="s"), index=timeline)

    target_aligned, target_match, target_delta = _align_to_timeline(
        target, timeline, method, parsed_tolerance
    )

    source_matched = source_match.notna()
    target_matched = target_match.notna()
    fully_matched = source_matched & target_matched
    if not fully_matched.any():
        tolerance_text = str(parsed_tolerance) if parsed_tolerance is not None else "不限"
        raise DataAlignmentError(f"没有记录在最大时间差 {tolerance_text} 内成功匹配")

    aligned = pd.concat(
        [source_aligned.add_prefix("src_"), target_aligned.add_prefix("tgt_")],
        axis=1,
    )
    aligned.index = pd.DatetimeIndex(timeline, name="_time")

    matched_delta = target_delta[target_matched]
    total = int(len(aligned))
    fully_matched_count = int(fully_matched.sum())
    unmatched_count = total - fully_matched_count
    stats = {
        "aligned_rows": total,
        "source_matched_rows": int(source_matched.sum()),
        "target_matched_rows": int(target_matched.sum()),
        "fully_matched_rows": fully_matched_count,
        "unmatched_rows": unmatched_count,
        "match_rate": round(fully_matched_count / total, 6) if total else 0.0,
        "mean_time_delta_seconds": (
            round(float(matched_delta.dt.total_seconds().mean()), 6)
            if len(matched_delta) else None
        ),
        "max_time_delta_seconds": (
            round(float(matched_delta.dt.total_seconds().max()), 6)
            if len(matched_delta) else None
        ),
        "time_start": str(timeline.min()),
        "time_end": str(timeline.max()),
        "method": method,
        "frequency": frequency,
        "tolerance": str(parsed_tolerance) if parsed_tolerance is not None else None,
    }
    warnings: list[str] = []
    if unmatched_count:
        warnings.append(
            f"有 {unmatched_count}/{total} 个时间点未完整匹配，保存后相应字段将保留为空"
        )
    if parsed_tolerance is None:
        warnings.append("未设置最大时间差，距离较远的记录也可能被匹配")
    if method == "linear" and (
        len(source.select_dtypes(exclude=[np.number]).columns)
        or len(target.select_dtypes(exclude=[np.number]).columns)
    ):
        warnings.append("线性插值仅用于数值列；文本和状态列使用前一个已知值")
    return AlignmentOutput(frame=aligned, stats=stats, warnings=warnings)
