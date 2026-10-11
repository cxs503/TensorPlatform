"""Safe, declarative data-cleaning rule engine.

Uploaded Python files are parsed with ast.literal_eval and are never executed.
This keeps code-defined rules reproducible without creating a code-execution
surface.
"""
from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd


MAX_RULE_FILE_BYTES = 256 * 1024
MAX_OPERATIONS = 50
MAX_DATASET_BYTES = 512 * 1024 * 1024
MAX_REGEX_LENGTH = 500


class CleaningRuleError(ValueError):
    """A user-facing validation or execution error."""


OPERATION_TYPES: List[Dict[str, Any]] = [
    {"type": "drop_empty_columns", "name": "删除全空字段", "description": "删除所有值都为空的字段"},
    {"type": "drop_sparse_rows", "name": "删除严重缺失行", "description": "删除有效字段比例不足阈值的记录"},
    {"type": "drop_missing", "name": "删除缺失行", "description": "指定字段为空时删除整行"},
    {"type": "fill_missing", "name": "填充缺失值", "description": "按固定值、均值、中位数、众数或前后值填充"},
    {"type": "drop_duplicates", "name": "删除重复行", "description": "按指定字段识别并删除重复记录"},
    {"type": "filter_rows", "name": "条件筛选", "description": "按字段、运算符和值保留或删除记录"},
    {"type": "replace_values", "name": "替换值", "description": "按映射表批量替换字段值"},
    {"type": "clip", "name": "数值截断", "description": "把超出上下限的数值压到边界"},
    {"type": "convert_type", "name": "类型转换", "description": "转换为数值、整数、文本、日期或布尔值"},
    {"type": "trim", "name": "清理空格", "description": "清理文本字段两侧、左侧或右侧空格"},
    {"type": "rename_columns", "name": "重命名字段", "description": "修改一个或多个字段名称"},
    {"type": "drop_columns", "name": "删除字段", "description": "删除不再需要的字段"},
]
OPERATION_NAMES = {item["type"]: item["name"] for item in OPERATION_TYPES}

DEFAULT_CLEANING_DEFINITION: Dict[str, Any] = {
    "version": 1,
    "operations": [
        {
            "type": "drop_empty_columns",
            "name": "删除全部为空的字段",
            "enabled": True,
        },
        {
            "type": "drop_sparse_rows",
            "name": "删除有效字段不足一半的记录",
            "min_non_null_ratio": 0.5,
            "enabled": True,
        },
    ],
}


def _as_columns(value: Any, *, required: bool = True) -> List[str]:
    if value is None:
        columns: List[str] = []
    elif isinstance(value, str):
        columns = [value.strip()] if value.strip() else []
    elif isinstance(value, (list, tuple)):
        columns = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise CleaningRuleError("字段配置必须是字段名或字段名数组")
    if required and not columns:
        raise CleaningRuleError("至少需要选择一个字段")
    if len(columns) != len(set(columns)):
        raise CleaningRuleError("字段列表中存在重复项")
    return columns


def normalize_definition(raw: Any) -> Dict[str, Any]:
    """Validate and normalize a cleaning rule definition."""
    if isinstance(raw, list):
        raw = {"version": 1, "operations": raw}
    if not isinstance(raw, dict):
        raise CleaningRuleError("规则必须是对象，或由操作对象组成的数组")
    operations = raw.get("operations")
    if not isinstance(operations, list) or not operations:
        raise CleaningRuleError("规则至少需要包含一个清洗操作")
    if len(operations) > MAX_OPERATIONS:
        raise CleaningRuleError(f"单条规则最多包含 {MAX_OPERATIONS} 个操作")

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(operations, start=1):
        if not isinstance(item, dict):
            raise CleaningRuleError(f"第 {index} 个操作必须是对象")
        operation_type = str(item.get("type") or "").strip()
        if operation_type not in OPERATION_NAMES:
            raise CleaningRuleError(
                f"第 {index} 个操作类型不支持: {operation_type or '空'}"
            )
        operation = dict(item)
        operation["type"] = operation_type
        operation["enabled"] = bool(operation.get("enabled", True))
        operation["name"] = str(
            operation.get("name") or OPERATION_NAMES[operation_type]
        )[:100]
        normalized.append(operation)

    return {"version": 1, "operations": normalized}


def parse_rule_file(filename: str, content: bytes) -> Tuple[Dict[str, Any], str]:
    """Parse JSON or a declarative Python rule file without executing it."""
    if len(content) > MAX_RULE_FILE_BYTES:
        raise CleaningRuleError("规则文件不能超过 256KB")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".py", ".json"}:
        raise CleaningRuleError("仅支持 .py 或 .json 规则文件")
    try:
        source = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CleaningRuleError("规则文件必须使用 UTF-8 编码") from exc

    if suffix == ".json":
        try:
            raw = json.loads(source)
        except json.JSONDecodeError as exc:
            raise CleaningRuleError(f"JSON 解析失败（第 {exc.lineno} 行）") from exc
        return normalize_definition(raw), source

    try:
        tree = ast.parse(source, filename=filename or "cleaning_rule.py", mode="exec")
    except SyntaxError as exc:
        raise CleaningRuleError(f"Python 语法错误（第 {exc.lineno} 行）") from exc

    raw = None
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise CleaningRuleError("Python规则只允许给 CLEANING_RULES 赋值")
            if node.targets[0].id not in {"CLEANING_RULES", "RULES"}:
                raise CleaningRuleError("Python规则只允许定义 CLEANING_RULES 或 RULES")
            if raw is not None:
                raise CleaningRuleError("规则文件只能定义一次 CLEANING_RULES")
            try:
                raw = ast.literal_eval(node.value)
            except (ValueError, TypeError) as exc:
                raise CleaningRuleError(
                    "CLEANING_RULES 只能包含字典、数组、字符串、数字、布尔值和空值"
                ) from exc
            continue
        raise CleaningRuleError(
            "为保证安全，Python规则不允许 import、函数、循环或函数调用"
        )
    if raw is None:
        raise CleaningRuleError("未找到 CLEANING_RULES 或 RULES 定义")
    return normalize_definition(raw), source


def load_dataset_frame(file_path: str, *, limit: int | None = None) -> pd.DataFrame:
    path = Path(file_path or "")
    if not path.is_file():
        raise CleaningRuleError("数据集文件不存在或不可读取")
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise CleaningRuleError("数据集超过 512MB，请先分片后再执行清洗")
    suffix = path.suffix.lower()
    try:
        if suffix in {".csv", ".txt"}:
            return pd.read_csv(path, nrows=limit)
        if suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path)
        elif suffix == ".json":
            frame = pd.read_json(path)
        elif suffix == ".parquet":
            frame = pd.read_parquet(path)
        else:
            raise CleaningRuleError(f"暂不支持清洗 {suffix or '未知'} 格式的数据集")
    except CleaningRuleError:
        raise
    except Exception as exc:
        raise CleaningRuleError(f"读取数据集失败: {exc}") from exc
    return frame.head(limit).copy() if limit else frame


def _require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise CleaningRuleError(f"数据集中不存在字段: {', '.join(missing)}")


def _compare_mask(series: pd.Series, operator: str, value: Any) -> pd.Series:
    if operator in {"is_null", "not_null"}:
        return series.isna() if operator == "is_null" else series.notna()
    if operator in {"in", "not_in"}:
        values = value if isinstance(value, list) else [value]
        mask = series.isin(values)
        return ~mask if operator == "not_in" else mask
    if operator in {"contains", "not_contains", "starts_with", "ends_with", "regex"}:
        text = "" if value is None else str(value)
        string_series = series.astype("string")
        if operator == "contains":
            return string_series.str.contains(text, regex=False, na=False)
        if operator == "not_contains":
            return ~string_series.str.contains(text, regex=False, na=False)
        if operator == "starts_with":
            return string_series.str.startswith(text, na=False)
        if operator == "ends_with":
            return string_series.str.endswith(text, na=False)
        if len(text) > MAX_REGEX_LENGTH:
            raise CleaningRuleError("正则表达式不能超过 500 个字符")
        try:
            re.compile(text)
        except re.error as exc:
            raise CleaningRuleError(f"正则表达式无效: {exc}") from exc
        return string_series.str.contains(text, regex=True, na=False)

    left = series
    right = value
    if operator in {"gt", "gte", "lt", "lte"}:
        left = pd.to_numeric(series, errors="coerce")
        try:
            right = float(value)
        except (TypeError, ValueError) as exc:
            raise CleaningRuleError("数值比较的条件值必须是数字") from exc
    operations = {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "gt": lambda: left > right,
        "gte": lambda: left >= right,
        "lt": lambda: left < right,
        "lte": lambda: left <= right,
    }
    if operator not in operations:
        raise CleaningRuleError(f"不支持的条件运算符: {operator}")
    return operations[operator]().fillna(False)


def _operation_summary(
    operation: Dict[str, Any],
    before_rows: int,
    after_rows: int,
    before_columns: int,
    after_columns: int,
    changed_cells: int = 0,
) -> Dict[str, Any]:
    return {
        "type": operation["type"],
        "name": operation.get("name") or OPERATION_NAMES[operation["type"]],
        "rows_before": before_rows,
        "rows_after": after_rows,
        "rows_removed": max(0, before_rows - after_rows),
        "columns_before": before_columns,
        "columns_after": after_columns,
        "columns_removed": max(0, before_columns - after_columns),
        "changed_cells": int(changed_cells),
    }


def apply_definition(
    frame: pd.DataFrame, definition: Dict[str, Any]
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Apply an allow-listed cleaning definition in deterministic order."""
    definition = normalize_definition(definition)
    result = frame.copy()
    initial_rows, initial_columns = len(result), len(result.columns)
    operation_summaries: List[Dict[str, Any]] = []

    for index, operation in enumerate(definition["operations"], start=1):
        if not operation.get("enabled", True):
            continue
        before_rows, before_columns = len(result), len(result.columns)
        changed_cells = 0
        operation_type = operation["type"]
        try:
            if operation_type == "drop_empty_columns":
                empty_columns = [
                    column for column in result.columns if result[column].isna().all()
                ]
                if len(empty_columns) >= len(result.columns):
                    raise CleaningRuleError("不能删除数据集的全部字段")
                if empty_columns:
                    result = result.drop(columns=empty_columns)

            elif operation_type == "drop_sparse_rows":
                ratio = float(operation.get("min_non_null_ratio", 0.5))
                if not 0 < ratio <= 1:
                    raise CleaningRuleError("有效字段比例必须大于0且不超过1")
                minimum = max(1, math.ceil(len(result.columns) * ratio))
                result = result.dropna(thresh=minimum)

            elif operation_type == "drop_missing":
                columns = _as_columns(operation.get("columns"), required=False)
                if columns:
                    _require_columns(result, columns)
                how = operation.get("how", "any")
                if how not in {"any", "all"}:
                    raise CleaningRuleError("缺失行删除方式只能是 any 或 all")
                result = result.dropna(subset=columns or None, how=how)

            elif operation_type == "fill_missing":
                columns = _as_columns(
                    operation.get("columns") or operation.get("column")
                )
                _require_columns(result, columns)
                method = operation.get("method", "constant")
                for column in columns:
                    before_missing = int(result[column].isna().sum())
                    if method == "constant":
                        result[column] = result[column].fillna(operation.get("value"))
                    elif method == "mean":
                        numeric = pd.to_numeric(result[column], errors="coerce")
                        result[column] = numeric.fillna(numeric.mean())
                    elif method == "median":
                        numeric = pd.to_numeric(result[column], errors="coerce")
                        result[column] = numeric.fillna(numeric.median())
                    elif method == "mode":
                        mode = result[column].mode(dropna=True)
                        if not mode.empty:
                            result[column] = result[column].fillna(mode.iloc[0])
                    elif method == "forward":
                        result[column] = result[column].ffill()
                    elif method == "backward":
                        result[column] = result[column].bfill()
                    else:
                        raise CleaningRuleError(f"不支持的填充方式: {method}")
                    changed_cells += before_missing - int(result[column].isna().sum())

            elif operation_type == "drop_duplicates":
                columns = _as_columns(operation.get("columns"), required=False)
                if columns:
                    _require_columns(result, columns)
                keep = operation.get("keep", "first")
                if keep == "none":
                    keep = False
                if keep not in {"first", "last", False}:
                    raise CleaningRuleError("重复值保留方式只能是 first、last 或 none")
                result = result.drop_duplicates(subset=columns or None, keep=keep)

            elif operation_type == "filter_rows":
                column = str(operation.get("column") or "").strip()
                _require_columns(result, [column])
                mask = _compare_mask(
                    result[column],
                    str(operation.get("operator") or "eq"),
                    operation.get("value"),
                )
                if not bool(operation.get("keep_matching", True)):
                    mask = ~mask
                result = result.loc[mask]

            elif operation_type == "replace_values":
                column = str(operation.get("column") or "").strip()
                _require_columns(result, [column])
                mapping = operation.get("mapping")
                if not isinstance(mapping, dict) or not mapping:
                    raise CleaningRuleError("替换值操作需要非空 mapping 对象")
                before = result[column].copy()
                result[column] = result[column].replace(mapping)
                changed_cells = int(
                    (before.astype("string") != result[column].astype("string"))
                    .fillna(False)
                    .sum()
                )

            elif operation_type == "clip":
                column = str(operation.get("column") or "").strip()
                _require_columns(result, [column])
                lower, upper = operation.get("min"), operation.get("max")
                if lower is None and upper is None:
                    raise CleaningRuleError("数值截断至少需要一个上限或下限")
                numeric = pd.to_numeric(result[column], errors="coerce")
                clipped = numeric.clip(lower=lower, upper=upper)
                changed_cells = int((numeric != clipped).fillna(False).sum())
                result[column] = clipped

            elif operation_type == "convert_type":
                column = str(operation.get("column") or "").strip()
                _require_columns(result, [column])
                dtype = operation.get("dtype")
                errors = operation.get("errors", "coerce")
                if errors not in {"coerce", "raise", "ignore"}:
                    raise CleaningRuleError("类型转换错误策略不支持")
                before = result[column].copy()
                if dtype == "numeric":
                    result[column] = pd.to_numeric(result[column], errors=errors)
                elif dtype == "integer":
                    converted = pd.to_numeric(result[column], errors=errors)
                    result[column] = converted.astype("Int64")
                elif dtype == "string":
                    result[column] = result[column].astype("string")
                elif dtype == "datetime":
                    result[column] = pd.to_datetime(result[column], errors=errors)
                elif dtype == "boolean":
                    mapping = {
                        "true": True, "1": True, "yes": True, "是": True,
                        "false": False, "0": False, "no": False, "否": False,
                    }
                    result[column] = (
                        result[column].astype("string").str.lower().map(mapping).astype("boolean")
                    )
                else:
                    raise CleaningRuleError(f"不支持的目标类型: {dtype}")
                changed_cells = int(
                    (before.astype("string") != result[column].astype("string"))
                    .fillna(False)
                    .sum()
                )

            elif operation_type == "trim":
                columns = _as_columns(
                    operation.get("columns") or operation.get("column")
                )
                _require_columns(result, columns)
                mode = operation.get("mode", "both")
                for column in columns:
                    before = result[column].astype("string")
                    if mode == "left":
                        after = before.str.lstrip()
                    elif mode == "right":
                        after = before.str.rstrip()
                    elif mode == "both":
                        after = before.str.strip()
                    else:
                        raise CleaningRuleError("空格清理方式只能是 both、left 或 right")
                    changed_cells += int((before != after).fillna(False).sum())
                    result[column] = after

            elif operation_type == "rename_columns":
                mapping = operation.get("mapping")
                if not mapping:
                    old_name = str(operation.get("column") or "").strip()
                    new_name = str(operation.get("new_name") or "").strip()
                    mapping = {old_name: new_name} if old_name and new_name else {}
                if not isinstance(mapping, dict) or not mapping:
                    raise CleaningRuleError("重命名操作需要旧字段名和新字段名")
                _require_columns(result, mapping.keys())
                if any(not str(value).strip() for value in mapping.values()):
                    raise CleaningRuleError("新字段名不能为空")
                result = result.rename(columns={str(k): str(v) for k, v in mapping.items()})
                if len(result.columns) != len(set(result.columns)):
                    raise CleaningRuleError("重命名后出现重复字段名")

            elif operation_type == "drop_columns":
                columns = _as_columns(operation.get("columns"))
                _require_columns(result, columns)
                if len(columns) >= len(result.columns):
                    raise CleaningRuleError("不能删除数据集的全部字段")
                result = result.drop(columns=columns)

        except CleaningRuleError as exc:
            raise CleaningRuleError(f"第 {index} 步“{operation['name']}”失败: {exc}") from exc
        except Exception as exc:
            raise CleaningRuleError(
                f"第 {index} 步“{operation['name']}”执行异常: {exc}"
            ) from exc

        result = result.reset_index(drop=True)
        operation_summaries.append(
            _operation_summary(
                operation,
                before_rows,
                len(result),
                before_columns,
                len(result.columns),
                changed_cells,
            )
        )

    summary = {
        "rows_before": initial_rows,
        "rows_after": len(result),
        "rows_removed": max(0, initial_rows - len(result)),
        "columns_before": initial_columns,
        "columns_after": len(result.columns),
        "columns_removed": max(0, initial_columns - len(result.columns)),
        "operations_executed": len(operation_summaries),
        "operations": operation_summaries,
    }
    return result, summary


def frame_records(frame: pd.DataFrame, limit: int = 20) -> List[Dict[str, Any]]:
    """Return JSON-safe preview rows while preserving column names."""
    sample = frame.head(limit).replace([np.inf, -np.inf], np.nan)
    return json.loads(
        sample.to_json(orient="records", force_ascii=False, date_format="iso")
    )


def frame_columns_info(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    return [
        {
            "name": str(column),
            "dtype": str(frame[column].dtype),
            "derived": False,
            "missing_count": int(frame[column].isna().sum()),
        }
        for column in frame.columns
    ]


def completeness_score(frame: pd.DataFrame) -> float:
    cells = len(frame) * len(frame.columns)
    if cells == 0:
        return 100.0
    missing = int(frame.isna().sum().sum())
    return round(max(0.0, (1 - missing / cells) * 100), 2)


def resolve_active_rule_for_pipeline(
    db: Any, rule_id: Any, organization_id: Any
) -> Dict[str, Any]:
    """Resolve one active, tenant-scoped cleaning rule for a pipeline run.

    Pipelines persist only the rule identifier. The definition is read when a
    node starts, so a deleted, disabled, or cross-tenant rule can never be
    replayed from a saved workflow configuration.
    """
    try:
        normalized_rule_id = int(rule_id)
        normalized_organization_id = int(organization_id)
    except (TypeError, ValueError) as exc:
        raise CleaningRuleError("流水线清洗节点必须选择有效的清洗规则") from exc

    from sqlalchemy import text

    row = db.execute(
        text(
            """
            SELECT id, name, definition
            FROM data_cleaning_rules
            WHERE id=:id
              AND organization_id=:organization_id
              AND is_active=TRUE
            """
        ),
        {
            "id": normalized_rule_id,
            "organization_id": normalized_organization_id,
        },
    ).mappings().first()
    if not row:
        raise CleaningRuleError("清洗规则不存在、已停用或不属于当前组织")

    definition = row["definition"]
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except json.JSONDecodeError as exc:
            raise CleaningRuleError("清洗规则内容无效") from exc
    return {
        "cleaning_rule_id": int(row["id"]),
        "cleaning_rule_name": str(row["name"]),
        "cleaning_definition": normalize_definition(definition),
    }


PYTHON_TEMPLATE = '''"""数字化底座数据清洗规则模板。

仅允许定义 CLEANING_RULES；平台会静态解析，不会执行本文件。
"""
CLEANING_RULES = {
    "version": 1,
    "operations": [
        {
            "type": "drop_missing",
            "name": "删除目标值为空的记录",
            "columns": ["temperature"],
            "how": "any",
        },
        {
            "type": "fill_missing",
            "name": "用中位数填充压力",
            "columns": ["pressure"],
            "method": "median",
        },
        {
            "type": "filter_rows",
            "name": "保留合理温度",
            "column": "temperature",
            "operator": "gte",
            "value": 0,
            "keep_matching": True,
        },
        {
            "type": "drop_duplicates",
            "name": "按时间和炉号去重",
            "columns": ["ts", "furnace_id"],
            "keep": "first",
        },
    ],
}
'''


def recommend_cleaning_definition(frame: pd.DataFrame) -> Dict[str, Any]:
    """Build a conservative, reviewable cleaning proposal from a data sample.

    The result only contains allow-listed operations.  It deliberately avoids
    automatic deletion or filling for high-missing columns, because that
    requires domain confirmation from the user.
    """
    if frame.empty:
        raise CleaningRuleError("数据集没有可分析的数据行")

    rows, columns = len(frame), len(frame.columns)
    recommendations: List[Dict[str, Any]] = []
    manual_attention: List[str] = []
    empty_columns = [str(column) for column in frame.columns if frame[column].isna().all()]
    if empty_columns:
        if len(empty_columns) < columns:
            recommendations.append({
                "id": "drop-empty-columns",
                "title": "删除全空字段",
                "reason": f"发现 {len(empty_columns)} 个全空字段：" + "、".join(empty_columns[:6]),
                "operation": {"type": "drop_empty_columns", "name": "删除全部为空的字段", "enabled": True},
                "selected": True,
            })
        else:
            manual_attention.append("全部字段均为空，无法生成安全的自动清洗方案")

    working = frame.drop(columns=empty_columns, errors="ignore")
    if not working.empty and len(working.columns):

        sparse_mask = working.notna().mean(axis=1) < 0.5
        sparse_rows = int(sparse_mask.sum())
        if sparse_rows:
            recommendations.append({
                "id": "drop-sparse-rows",
                "title": "删除严重缺失记录",
                "reason": f"发现 {sparse_rows} 条记录的有效字段不足一半",
                "operation": {"type": "drop_sparse_rows", "name": "删除有效字段不足一半的记录", "min_non_null_ratio": 0.5, "enabled": True},
                "selected": True,
            })

        trim_columns: List[str] = []
        numeric_fill_columns: List[str] = []
        text_fill_columns: List[str] = []
        for column in working.columns:
            series = working[column]
            missing = int(series.isna().sum())
            missing_rate = missing / max(rows, 1)
            column_name = str(column)
            if missing_rate > 0.2:
                manual_attention.append(f"“{column_name}”缺失率 {missing_rate:.1%}，建议人工确认后再处理")
            elif missing:
                if pd.api.types.is_numeric_dtype(series):
                    numeric_fill_columns.append(column_name)
                elif pd.api.types.is_string_dtype(series) or series.dtype == object:
                    if not series.dropna().mode().empty:
                        text_fill_columns.append(column_name)
            if pd.api.types.is_string_dtype(series) or series.dtype == object:
                values = series.dropna().astype("string")
                if not values.empty and bool((values != values.str.strip()).any()):
                    trim_columns.append(column_name)

        if trim_columns:
            recommendations.append({
                "id": "trim-text",
                "title": "清理文本两侧空格",
                "reason": f"发现 {len(trim_columns)} 个文本字段存在前后空格",
                "operation": {"type": "trim", "name": "清理文本两侧空格", "columns": trim_columns, "mode": "both", "enabled": True},
                "selected": True,
            })
        if numeric_fill_columns:
            recommendations.append({
                "id": "fill-numeric-median",
                "title": "填充数值缺失值",
                "reason": f"{len(numeric_fill_columns)} 个数值字段缺失率不高于 20%，建议用中位数填充",
                "operation": {"type": "fill_missing", "name": "用中位数填充数值缺失", "columns": numeric_fill_columns, "method": "median", "enabled": True},
                "selected": True,
            })
        if text_fill_columns:
            recommendations.append({
                "id": "fill-text-mode",
                "title": "填充文本缺失值",
                "reason": f"{len(text_fill_columns)} 个文本字段缺失率不高于 20%，建议用众数填充",
                "operation": {"type": "fill_missing", "name": "用众数填充文本缺失", "columns": text_fill_columns, "method": "mode", "enabled": True},
                "selected": True,
            })
        duplicate_rows = int(working.duplicated().sum())
        if duplicate_rows:
            recommendations.append({
                "id": "drop-duplicates",
                "title": "删除重复记录",
                "reason": f"发现 {duplicate_rows} 条完全重复的记录",
                "operation": {"type": "drop_duplicates", "name": "删除完全重复记录", "columns": [], "keep": "first", "enabled": True},
                "selected": True,
            })

    return {
        "profile": {
            "rows": rows,
            "columns": columns,
            "missing_cells": int(frame.isna().sum().sum()),
            "duplicate_rows": int(frame.duplicated().sum()),
        },
        "recommendations": recommendations,
        "manual_attention": manual_attention[:20],
    }
