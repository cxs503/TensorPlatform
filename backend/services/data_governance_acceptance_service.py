"""Read-only governance acceptance checks for cleaned datasets."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd


PROFILES: Dict[str, Dict[str, Any]] = {
    "basic": {
        "id": "basic",
        "name": "基础治理验收",
        "description": "适用于一般训练数据：完整率不少于95%，整行重复率不超过5%",
        "min_rows": 1,
        "min_completeness": 0.95,
        "max_duplicate_rate": 0.05,
    },
    "strict": {
        "id": "strict",
        "name": "严格治理验收",
        "description": "适用于生产数据：完整率不少于99%，整行重复率不超过1%",
        "min_rows": 1,
        "min_completeness": 0.99,
        "max_duplicate_rate": 0.01,
    },
}


def profile_list() -> List[Dict[str, Any]]:
    return [dict(profile) for profile in PROFILES.values()]


def _check(
    check_id: str,
    name: str,
    passed: bool,
    score: float,
    expected: str,
    actual: str,
    *,
    severity: str = "critical",
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "id": check_id,
        "name": name,
        "passed": bool(passed),
        "score": round(float(max(0, min(100, score))), 2),
        "expected": expected,
        "actual": actual,
        "severity": severity,
        "details": details or {},
    }


def _threshold(raw: Any) -> Dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _quality_rule_check(frame: pd.DataFrame, rule: Dict[str, Any]) -> Dict[str, Any]:
    rule_id = int(rule["id"])
    name = str(rule.get("name") or f"质量规则#{rule_id}")
    rule_type = str(rule.get("rule_type") or "")
    field = str(rule.get("field") or "")
    threshold = _threshold(rule.get("threshold"))
    severity = str(rule.get("severity") or "warning")

    if field == "*" and rule_type == "completeness":
        cells = len(frame) * len(frame.columns)
        actual = 1.0 if cells == 0 else 1 - float(frame.isna().sum().sum()) / cells
        minimum = float(threshold.get("min", threshold.get("min_pct", 95) / 100))
        return _check(
            f"quality_rule_{rule_id}", name, actual >= minimum, actual * 100,
            f"整体完整率 ≥ {minimum * 100:.2f}%", f"{actual * 100:.2f}%",
            severity=severity,
        )
    if field not in frame.columns:
        return _check(
            f"quality_rule_{rule_id}", name, False, 0,
            f"存在字段 {field}", "字段不存在", severity=severity,
            details={"field": field, "error": "missing_column"},
        )

    series = frame[field]
    total = len(series)
    if rule_type == "completeness":
        actual = 1.0 if total == 0 else float(series.notna().sum()) / total
        if "min_pct" in threshold:
            minimum = float(threshold["min_pct"]) / 100
        elif "max_missing_pct" in threshold:
            minimum = 1 - float(threshold["max_missing_pct"]) / 100
        else:
            minimum = float(threshold.get("min", 0.9))
        return _check(
            f"quality_rule_{rule_id}", name, actual >= minimum, actual * 100,
            f"{field} 完整率 ≥ {minimum * 100:.2f}%", f"{actual * 100:.2f}%",
            severity=severity,
        )

    if rule_type == "uniqueness":
        duplicate_rate = 0.0 if total == 0 else float(series.duplicated().sum()) / total
        maximum = float(threshold.get("max_rate", 0.05))
        return _check(
            f"quality_rule_{rule_id}", name, duplicate_rate <= maximum,
            (1 - duplicate_rate) * 100,
            f"{field} 重复率 ≤ {maximum * 100:.2f}%", f"{duplicate_rate * 100:.2f}%",
            severity=severity,
        )

    if rule_type == "range":
        minimum, maximum = threshold.get("min"), threshold.get("max")
        numeric = pd.to_numeric(series, errors="coerce")
        valid = numeric.notna()
        if minimum is not None:
            valid &= numeric >= float(minimum)
        if maximum is not None:
            valid &= numeric <= float(maximum)
        valid_rate = 1.0 if total == 0 else float(valid.sum()) / total
        bounds = f"[{minimum if minimum is not None else '-∞'}, {maximum if maximum is not None else '+∞'}]"
        return _check(
            f"quality_rule_{rule_id}", name, bool(valid.all()), valid_rate * 100,
            f"{field} 全部位于 {bounds}", f"合格率 {valid_rate * 100:.2f}%",
            severity=severity,
        )

    if rule_type == "pattern":
        pattern = str(threshold.get("pattern") or rule.get("condition") or "")
        if not pattern:
            return _check(
                f"quality_rule_{rule_id}", name, False, 0,
                "配置有效正则表达式", "未配置表达式", severity=severity,
            )
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return _check(
                f"quality_rule_{rule_id}", name, False, 0,
                "配置有效正则表达式", f"表达式无效: {exc}", severity=severity,
            )
        valid = series.astype("string").apply(
            lambda value: bool(regex.fullmatch(str(value))) if pd.notna(value) else False
        )
        valid_rate = 1.0 if total == 0 else float(valid.sum()) / total
        return _check(
            f"quality_rule_{rule_id}", name, bool(valid.all()), valid_rate * 100,
            f"{field} 全部符合格式", f"合格率 {valid_rate * 100:.2f}%",
            severity=severity,
        )

    return _check(
        f"quality_rule_{rule_id}", name, False, 0,
        "使用支持的质量规则类型", f"暂不支持 {rule_type}", severity=severity,
        details={"error": "unsupported_rule_type"},
    )


def evaluate_governance(
    frame: pd.DataFrame,
    profile_id: str,
    quality_rules: Iterable[Dict[str, Any]] = (),
    *,
    thresholds: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if profile_id not in PROFILES:
        raise ValueError("治理验收标准不存在")
    profile = dict(PROFILES[profile_id])
    if thresholds is not None:
        try:
            min_completeness = float(thresholds.get("min_completeness", profile["min_completeness"]))
            max_duplicate_rate = float(thresholds.get("max_duplicate_rate", profile["max_duplicate_rate"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("检查阈值格式错误") from exc
        if not 0 <= min_completeness <= 1 or not 0 <= max_duplicate_rate <= 1:
            raise ValueError("检查阈值必须介于 0% 和 100% 之间")
        profile.update(
            {
                "id": "custom",
                "name": "自定义检查标准",
                "description": f"完整率不少于{min_completeness * 100:.0f}%，整行重复率不超过{max_duplicate_rate * 100:.0f}%",
                "min_completeness": min_completeness,
                "max_duplicate_rate": max_duplicate_rate,
            }
        )
        profile_id = "custom"
    rows, columns = len(frame), len(frame.columns)
    cells = rows * columns
    completeness = 1.0 if cells == 0 else 1 - float(frame.isna().sum().sum()) / cells
    duplicate_rows = int(frame.duplicated().sum()) if rows else 0
    duplicate_rate = 0.0 if rows == 0 else duplicate_rows / rows
    duplicate_columns = [
        str(name) for name in frame.columns[frame.columns.duplicated()].tolist()
    ]
    empty_columns = [str(column) for column in frame.columns if frame[column].isna().all()]
    numeric = frame.select_dtypes(include=[np.number])
    infinite_count = int(np.isinf(numeric.to_numpy()).sum()) if not numeric.empty else 0

    checks = [
        _check(
            "non_empty_dataset", "数据集非空", rows >= profile["min_rows"],
            100 if rows >= profile["min_rows"] else 0,
            f"至少 {profile['min_rows']} 行", f"{rows} 行",
        ),
        _check(
            "has_columns", "字段存在", columns > 0, 100 if columns > 0 else 0,
            "至少 1 个字段", f"{columns} 个字段",
        ),
        _check(
            "unique_column_names", "字段名唯一", not duplicate_columns,
            100 if not duplicate_columns else 0,
            "无重复字段名", "无" if not duplicate_columns else ", ".join(duplicate_columns),
        ),
        _check(
            "no_empty_columns", "无全空字段", not empty_columns,
            100 if not empty_columns else max(0, (columns - len(empty_columns)) / max(columns, 1) * 100),
            "不存在全部为空的字段", "无" if not empty_columns else ", ".join(empty_columns),
        ),
        _check(
            "completeness", "整体完整率",
            completeness >= profile["min_completeness"], completeness * 100,
            f"≥ {profile['min_completeness'] * 100:.2f}%", f"{completeness * 100:.2f}%",
        ),
        _check(
            "duplicate_rows", "整行重复率",
            duplicate_rate <= profile["max_duplicate_rate"], (1 - duplicate_rate) * 100,
            f"≤ {profile['max_duplicate_rate'] * 100:.2f}%",
            f"{duplicate_rate * 100:.2f}%（{duplicate_rows} 行）",
        ),
        _check(
            "finite_numeric_values", "数值有效性", infinite_count == 0,
            100 if infinite_count == 0 else 0,
            "不存在正负无穷值", f"{infinite_count} 个无穷值",
        ),
    ]
    checks.extend(_quality_rule_check(frame, rule) for rule in quality_rules)
    passed = all(check["passed"] for check in checks)
    score = round(sum(check["score"] for check in checks) / len(checks), 2) if checks else 0.0
    return {
        "profile": profile_id,
        "profile_name": profile["name"],
        "passed": passed,
        "status": "passed" if passed else "failed",
        "score": score,
        "rows": rows,
        "columns": columns,
        "checks_total": len(checks),
        "checks_passed": sum(1 for check in checks if check["passed"]),
        "checks_failed": sum(1 for check in checks if not check["passed"]),
        "checks": checks,
    }
