"""
Evidently AI 模型监控与漂移检测 v0.7.21
"""
import json
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

from evidently import Report
from evidently.presets import DataDriftPreset, DataSummaryPreset, RegressionPreset
from evidently.metrics import (
    ValueDrift, DriftedColumnsCount, RowCount, MissingValueCount,
    MeanValue, StdValue, MinValue, MaxValue, UniqueValueCount,
)


class EvidentlyMonitor:
    """模型监控与漂移检测"""

    REPORTS_DIR = "/app/uploads/evidently_reports"

    def __init__(self):
        Path(self.REPORTS_DIR).mkdir(parents=True, exist_ok=True)

    def data_quality_report(self, df: pd.DataFrame, columns: list = None):
        """数据质量报告"""
        target = df[columns] if columns else df
        report = Report(metrics=[
            DataSummaryPreset(),
        ])
        snapshot = report.run(reference_data=target, current_data=target)
        result = snapshot.dict()

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        json_path = Path(self.REPORTS_DIR) / f"quality_{timestamp}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, default=str))

        metrics = result.get("metrics", [])
        return {
            "total_features": len(target.columns),
            "total_rows": len(target),
            "report_path": str(json_path),
            "timestamp": timestamp,
            "metrics": [
                {"name": m.get("metric", "unknown"), "result": m.get("result", {})}
                for m in metrics
            ],
        }

    def detect_data_drift(self, reference_df: pd.DataFrame, current_df: pd.DataFrame,
                          columns: list = None):
        """检测数据漂移"""
        if columns:
            reference_df = reference_df[columns]
            current_df = current_df[columns]

        report = Report(metrics=[
            DataDriftPreset(),
            DriftedColumnsCount(),
        ])
        snapshot = report.run(reference_data=reference_df, current_data=current_df)
        result = snapshot.dict()

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        json_path = Path(self.REPORTS_DIR) / f"drift_{timestamp}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, default=str))

        # Extract drift summary
        drifted = 0
        for m in result.get("metrics", []):
            r = m.get("result", {})
            if isinstance(r, dict):
                drifted = r.get("number_of_drifted_columns", drifted) or drifted

        return {
            "drift_detected": drifted > 0,
            "drifted_columns": drifted,
            "total_columns": len(columns or reference_df.columns),
            "report_path": str(json_path),
            "timestamp": timestamp,
        }

    def regression_performance(self, reference_df: pd.DataFrame, current_df: pd.DataFrame,
                               target_col: str, prediction_col: str = None):
        """回归模型性能"""
        report = Report(metrics=[RegressionPreset()])
        snapshot = report.run(
            reference_data=reference_df,
            current_data=current_df,
            column_mapping={"target": target_col, "prediction": prediction_col}
        )
        result = snapshot.dict()

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        json_path = Path(self.REPORTS_DIR) / f"regression_{timestamp}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, default=str))

        return {
            "report_path": str(json_path),
            "timestamp": timestamp,
            "target": target_col,
        }
