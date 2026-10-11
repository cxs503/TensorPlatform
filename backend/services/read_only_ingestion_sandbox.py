"""Read-only CSV validation sandbox for future industrial data connections.

It deliberately does not create datasource connections, write raw records, or emit
control instructions.  Its job is to prove that a proposed feed meets the existing
digital-twin point contract before any real integration is approved.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from services.industrial_simulation_service import IndustrialSimulationService


class ReadOnlyIngestionSandbox:
    max_bytes = 10 * 1024 * 1024
    _unit_factors = {
        ("W", "kW"): 0.001, ("MW", "kW"): 1000.0,
        ("kPa", "Pa"): 1000.0, ("MPa", "Pa"): 1_000_000.0,
        ("fraction", "%"): 100.0, ("MWh", "kWh"): 1000.0,
    }

    def __init__(self, simulator: IndustrialSimulationService):
        self.simulator = simulator

    def validate_csv(self, scenario_key: str, content: bytes, field_mapping: dict | None = None,
                     source_units: dict | None = None):
        if scenario_key not in self.simulator.scenarios:
            raise ValueError("未知工业仿真场景")
        if not content:
            raise ValueError("CSV 文件为空")
        if len(content) > self.max_bytes:
            raise ValueError("CSV 沙箱单次最多 10MB")
        try:
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValueError("CSV 必须为 UTF-8 编码且格式有效") from exc
        if not rows or not rows[0]:
            raise ValueError("CSV 必须包含表头和至少一条数据")
        field_mapping, source_units = field_mapping or {}, source_units or {}
        expected = self.simulator.point_contracts[scenario_key]
        headers = set(rows[0])
        timestamp_column = field_mapping.get("timestamp", "timestamp")
        checks, normalized_sample, total_violations = [], [], 0
        if timestamp_column not in headers:
            checks.append({"field": "timestamp", "status": "failed", "reason": "missing_timestamp"})
            total_violations += len(rows)
        timestamps = self._timestamps(rows, timestamp_column) if timestamp_column in headers else []
        for point in expected:
            field = point["field"]
            source_column = field_mapping.get(field, field)
            if source_column not in headers:
                checks.append({"field": field, "code": point["code"], "status": "failed", "reason": "missing_column"})
                total_violations += len(rows)
                continue
            factor, unit_error = self._factor(source_units.get(field, point["unit"]), point["unit"])
            violations, missing = 0, 0
            lower, upper = point["range"]
            for row_index, row in enumerate(rows):
                try:
                    value = float(row[source_column]) * factor
                    if value < lower or value > upper:
                        violations += 1
                    if row_index < 3:
                        while len(normalized_sample) <= row_index:
                            normalized_sample.append({})
                        normalized_sample[row_index][field] = round(value, 6)
                except (TypeError, ValueError):
                    missing += 1
            status = "passed" if not unit_error and not violations and not missing else "failed"
            checks.append({"field": field, "code": point["code"], "source_column": source_column,
                           "expected_unit": point["unit"], "source_unit": source_units.get(field, point["unit"]),
                           "status": status, "range_violations": violations, "missing_or_invalid": missing,
                           "reason": unit_error})
            total_violations += violations + missing + int(bool(unit_error)) * len(rows)
        alignment = self._alignment(timestamps, len(rows))
        total_violations += alignment["invalid_timestamp_count"]
        passed = total_violations == 0 and alignment["ordered"] and alignment["hourly_alignment_rate"] == 1.0
        return {
            "mode": "read_only_csv_sandbox", "scenario_key": scenario_key,
            "row_count": len(rows), "checks": checks, "time_alignment": alignment,
            "quality_gate": {"passed": passed, "total_violations": total_violations,
                             "next_step": "可申请只读历史数据接入评审" if passed else "修复字段映射、单位、量程或时间戳后重试"},
            "normalized_sample": normalized_sample,
            "safety_boundary": "不保存原始上传数据，不建立现场连接，不产生控制指令。",
        }

    def benchmark_gate(self, run_id: str):
        evaluation = self.simulator.evaluate_run(run_id)
        contract = self.simulator.validate_contract_run(run_id)
        analysis = self.simulator.analyze_run(run_id)
        admission = self.simulator.admission_report(run_id)
        if not all((evaluation, contract, analysis, admission)):
            return None
        checks = [
            {"name": "missing_rate", "value": evaluation["data_quality"]["missing_rate"], "threshold": 0.01, "passed": evaluation["data_quality"]["missing_rate"] <= 0.01},
            {"name": "constraint_violations", "value": evaluation["constraint_audit"]["violations"], "threshold": 0, "passed": evaluation["constraint_audit"]["violations"] == 0},
            {"name": "optimization_contract", "value": contract["applicability"]["optimization_ready"], "threshold": True, "passed": contract["applicability"]["optimization_ready"]},
            {"name": "manual_review_admission", "value": admission["decision"], "threshold": "review_ready", "passed": admission["decision"] == "review_ready"},
        ]
        return {"run_id": run_id, "profile": analysis["profile"], "checks": checks,
                "passed": all(item["passed"] for item in checks),
                "scope": "仅作为虚拟回放与后续只读数据接入的基准门槛；不是生产模型准入。"}

    def _factor(self, source_unit: str, expected_unit: str):
        if source_unit == expected_unit:
            return 1.0, None
        factor = self._unit_factors.get((source_unit, expected_unit))
        return (factor, None) if factor is not None else (1.0, "unsupported_unit_conversion")

    @staticmethod
    def _timestamps(rows: list[dict], column: str):
        values = []
        for row in rows:
            try:
                values.append(datetime.fromisoformat(str(row[column]).replace("Z", "+00:00")))
            except (TypeError, ValueError):
                values.append(None)
        return values

    @staticmethod
    def _alignment(timestamps: list, total_rows: int):
        valid = [item for item in timestamps if item is not None]
        intervals = [(right - left).total_seconds() for left, right in zip(valid, valid[1:])]
        hourly = sum(abs(value - 3600) < 1 for value in intervals)
        return {"expected_frequency": "1h", "valid_timestamp_count": len(valid),
                "invalid_timestamp_count": total_rows - len(valid), "ordered": valid == sorted(valid),
                "hourly_alignment_rate": round(hourly / len(intervals), 6) if intervals else 1.0}
