"""
数据质量检测服务
支持数据完整性、一致性、时效性检测
"""
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd


class QualityDimension(str, Enum):
    """质量维度"""

    COMPLETENESS = "completeness"  # 完整性
    ACCURACY = "accuracy"  # 准确性
    CONSISTENCY = "consistency"  # 一致性
    TIMELINESS = "timeliness"  # 时效性
    UNIQUENESS = "uniqueness"  # 唯一性
    VALIDITY = "validity"  # 有效性


class QualityRule:
    """质量规则"""

    def __init__(
        self, name: str, dimension: str, check_fn: Callable, threshold: float = 1.0
    ):
        self.name = name
        self.dimension = dimension
        self.check_fn = check_fn
        self.threshold = threshold


class DataQualityService:
    """数据质量服务"""

    def __init__(self):
        self.rules: Dict[str, List[QualityRule]] = {}
        self.quality_reports: Dict[str, Dict] = {}

    def add_rule(self, dataset_id: str, rule: QualityRule):
        """添加质量规则"""
        if dataset_id not in self.rules:
            self.rules[dataset_id] = []
        self.rules[dataset_id].append(rule)

    def check_completeness(self, df: pd.DataFrame) -> Dict[str, Any]:
        """检查数据完整性"""
        total_cells = df.shape[0] * df.shape[1]
        missing_cells = df.isnull().sum().sum()
        missing_rate = missing_cells / total_cells if total_cells > 0 else 0

        # 各列缺失率
        column_missing = {}
        for col in df.columns:
            missing = df[col].isnull().sum()
            column_missing[col] = {
                "missing_count": int(missing),
                "missing_rate": float(missing / len(df)) if len(df) > 0 else 0,
            }

        return {
            "dimension": "completeness",
            "total_rows": len(df),
            "total_columns": len(df.columns),
            "total_cells": int(total_cells),
            "missing_cells": int(missing_cells),
            "missing_rate": float(missing_rate),
            "score": float(1 - missing_rate),
            "column_details": column_missing,
            "status": "pass"
            if missing_rate < 0.1
            else "warning"
            if missing_rate < 0.3
            else "fail",
        }

    def check_uniqueness(self, df: pd.DataFrame) -> Dict[str, Any]:
        """检查数据唯一性"""
        # 行重复
        duplicate_rows = df.duplicated().sum()
        duplicate_rate = duplicate_rows / len(df) if len(df) > 0 else 0

        # 列唯一性
        column_unique = {}
        for col in df.columns:
            unique_count = df[col].nunique()
            column_unique[col] = {
                "unique_count": int(unique_count),
                "unique_rate": float(unique_count / len(df)) if len(df) > 0 else 0,
                "is_unique": unique_count == len(df),
            }

        return {
            "dimension": "uniqueness",
            "total_rows": len(df),
            "duplicate_rows": int(duplicate_rows),
            "duplicate_rate": float(duplicate_rate),
            "score": float(1 - duplicate_rate),
            "column_details": column_unique,
            "status": "pass"
            if duplicate_rate < 0.05
            else "warning"
            if duplicate_rate < 0.2
            else "fail",
        }

    def check_validity(self, df: pd.DataFrame, schema: Dict = None) -> Dict[str, Any]:
        """检查数据有效性"""
        issues = []

        # 检查数据类型
        for col in df.columns:
            dtype = str(df[col].dtype)
            # 数值列检查
            if df[col].dtype in ["int64", "float64"]:
                # 检查无穷值
                inf_count = np.isinf(df[col]).sum()
                if inf_count > 0:
                    issues.append(
                        {
                            "column": col,
                            "type": "infinite_values",
                            "count": int(inf_count),
                        }
                    )

        # 如果提供了schema，检查schema匹配
        if schema:
            for col, expected_type in schema.items():
                if col not in df.columns:
                    issues.append(
                        {
                            "column": col,
                            "type": "missing_column",
                            "expected_type": expected_type,
                        }
                    )
                elif not self._check_dtype_match(df[col].dtype, expected_type):
                    issues.append(
                        {
                            "column": col,
                            "type": "dtype_mismatch",
                            "expected_type": expected_type,
                            "actual_type": str(df[col].dtype),
                        }
                    )

        validity_rate = (
            1 - len(issues) / (len(df.columns) + 1) if df.shape[1] > 0 else 1
        )

        return {
            "dimension": "validity",
            "total_issues": len(issues),
            "issues": issues,
            "score": float(validity_rate),
            "status": "pass"
            if len(issues) == 0
            else "warning"
            if len(issues) < 5
            else "fail",
        }

    def _check_dtype_match(self, actual: str, expected: str) -> bool:
        """检查数据类型匹配"""
        dtype_map = {
            "int": ["int64", "int32"],
            "float": ["float64", "float32"],
            "string": ["object", "string"],
            "datetime": ["datetime64"],
        }
        expected_types = dtype_map.get(expected, [expected])
        return actual in expected_types

    def check_consistency(self, df: pd.DataFrame) -> Dict[str, Any]:
        """检查数据一致性"""
        issues = []

        # 检查数值列的范围一致性
        for col in df.select_dtypes(include=[np.number]).columns:
            q1 = df[col].quantile(0.25)
            q3 = df[col].quantile(0.75)
            iqr = q3 - q1
            outliers = ((df[col] < q1 - 1.5 * iqr) | (df[col] > q3 + 1.5 * iqr)).sum()

            if outliers > 0:
                issues.append(
                    {
                        "column": col,
                        "type": "outliers",
                        "count": int(outliers),
                        "percentage": float(outliers / len(df) * 100),
                    }
                )

        # 检查字符串列格式一致性
        for col in df.select_dtypes(include=["object"]).columns:
            # 检查空字符串
            empty_count = (df[col] == "").sum()
            if empty_count > 0:
                issues.append(
                    {"column": col, "type": "empty_strings", "count": int(empty_count)}
                )

            # 检查前后空格
            whitespace_count = df[col].str.strip().ne(df[col]).sum()
            if whitespace_count > 0:
                issues.append(
                    {
                        "column": col,
                        "type": "whitespace",
                        "count": int(whitespace_count),
                    }
                )

        consistency_rate = (
            1 - len(issues) / (len(df.columns) + 1) if df.shape[1] > 0 else 1
        )

        return {
            "dimension": "consistency",
            "total_issues": len(issues),
            "issues": issues,
            "score": float(consistency_rate),
            "status": "pass"
            if len(issues) == 0
            else "warning"
            if len(issues) < 5
            else "fail",
        }

    def check_timeliness(
        self, df: pd.DataFrame, timestamp_col: str = None
    ) -> Dict[str, Any]:
        """检查数据时效性"""
        issues = []

        # 如果有时间列
        if timestamp_col and timestamp_col in df.columns:
            try:
                timestamps = pd.to_datetime(df[timestamp_col], errors="coerce")
                now = datetime.now()

                # 检查最新数据时间
                latest = timestamps.max()
                if pd.notna(latest):
                    age = (now - latest).total_seconds() / 3600  # 小时

                    if age > 24:
                        issues.append(
                            {
                                "type": "stale_data",
                                "message": f"数据最新时间为{age:.1f}小时前",
                                "latest_timestamp": str(latest),
                            }
                        )

                # 检查时间间隔异常
                time_diffs = timestamps.diff().dt.total_seconds()
                large_gaps = (time_diffs > time_diffs.quantile(0.95)).sum()
                if large_gaps > 0:
                    issues.append(
                        {
                            "type": "time_gaps",
                            "count": int(large_gaps),
                            "message": f"发现{large_gaps}个大时间间隔",
                        }
                    )

            except Exception as e:
                issues.append({"type": "timestamp_error", "message": str(e)})

        timeliness_score = 1 - len(issues) / 2  # 简化评分

        return {
            "dimension": "timeliness",
            "total_issues": len(issues),
            "issues": issues,
            "score": float(max(0, timeliness_score)),
            "status": "pass" if len(issues) == 0 else "warning",
        }

    def run_quality_check(
        self, df: pd.DataFrame, dataset_id: str, schema: Dict = None
    ) -> Dict[str, Any]:
        """运行完整的数据质量检查"""
        results = {}

        # 各维度检查
        results["completeness"] = self.check_completeness(df)
        results["uniqueness"] = self.check_uniqueness(df)
        results["validity"] = self.check_validity(df, schema)
        results["consistency"] = self.check_consistency(df)
        results["timeliness"] = self.check_timeliness(df)

        # 计算总体评分
        scores = [r["score"] for r in results.values()]
        overall_score = sum(scores) / len(scores) if scores else 0

        # 确定状态
        if overall_score >= 0.9:
            status = "excellent"
        elif overall_score >= 0.8:
            status = "good"
        elif overall_score >= 0.7:
            status = "fair"
        else:
            status = "poor"

        report = {
            "dataset_id": dataset_id,
            "timestamp": datetime.now().isoformat(),
            "overall_score": float(overall_score),
            "status": status,
            "dimensions": results,
            "row_count": len(df),
            "column_count": len(df.columns),
        }

        # 保存报告
        self.quality_reports[dataset_id] = report

        return report

    def get_quality_report(self, dataset_id: str) -> Optional[Dict]:
        """获取质量报告"""
        return self.quality_reports.get(dataset_id)


# 全局实例
_quality_service: Optional[DataQualityService] = None


def get_data_quality_service() -> DataQualityService:
    """获取数据质量服务"""
    global _quality_service
    if _quality_service is None:
        _quality_service = DataQualityService()
    return _quality_service
