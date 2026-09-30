"""Reproducible industrial virtual-factory scenarios for offline validation."""
from __future__ import annotations

import csv
import json
import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


class IndustrialSimulationService:
    scenarios = {
        "sintering_dust_efficiency": {
            "name": "生产过程/除尘能效优化",
            "domain": "工艺设备能效",
            "description": "能量守恒、风机曲线和工艺窗口驱动的生产过程除尘系统仿真。",
            "outputs": ["时序工况", "质量真值", "约束告警", "优化建议与回执"],
        },
        "park_multi_energy": {
            "name": "园区源网荷储峰谷调度",
            "domain": "能源管控",
            "description": "负荷、光伏、电价、储能 SOC 和需量约束驱动的调度回放。",
            "outputs": ["能流时序", "成本真值", "SOC/需量约束", "调度建议与回执"],
        },
    }
    config_defaults = {
        "sintering_dust_efficiency": {
            "production_base_tph": 260.0, "fan_curve_coefficient": 0.00000000032,
            "pressure_min_pa": 1100.0, "pressure_max_pa": 1450.0,
            "feo_min_pct": 7.5, "feo_max_pct": 8.8,
        },
        "park_multi_energy": {
            "pv_capacity_kw": 850.0, "storage_capacity_kwh": 1400.0,
            "storage_power_kw": 300.0, "soc_min_pct": 18.0, "soc_max_pct": 88.0,
            "demand_limit_kw": 1500.0, "valley_tariff": 0.42,
            "flat_tariff": 0.82, "peak_tariff": 1.18,
        },
    }
    topology = {
        "sintering_dust_efficiency": {"assets": [
            {"id": "sintering_line", "type": "process_line", "role": "产量与质量边界"},
            {"id": "dust_fan", "type": "variable_frequency_fan", "role": "可优化执行设备"},
            {"id": "dust_collector", "type": "dust_collector", "role": "压差约束"},
            {"id": "power_meter", "type": "electricity_meter", "role": "能耗计量"}],
            "flows": [["sintering_line", "dust_collector"], ["dust_fan", "dust_collector"], ["power_meter", "dust_fan"]]},
        "park_multi_energy": {"assets": [
            {"id": "grid", "type": "grid_connection", "role": "购电与需量边界"},
            {"id": "pv", "type": "photovoltaic", "role": "可再生出力"},
            {"id": "battery", "type": "energy_storage", "role": "削峰填谷"},
            {"id": "flexible_load", "type": "controllable_load", "role": "需求响应"},
            {"id": "park_load", "type": "load", "role": "基础负荷"}],
            "flows": [["pv", "park_load"], ["battery", "park_load"], ["grid", "park_load"], ["flexible_load", "park_load"]]},
    }
    point_contracts = {
        "sintering_dust_efficiency": [
            {"code": "sintering.production_tph", "field": "production_tph", "asset_id": "sintering_line", "unit": "t/h", "range": [100, 600], "frequency": "1h", "role": "context", "quality_rule": "required;range"},
            {"code": "dust_fan.frequency_hz", "field": "fan_frequency_hz", "asset_id": "dust_fan", "unit": "Hz", "range": [0, 60], "frequency": "1h", "role": "control_candidate", "quality_rule": "required;range"},
            {"code": "dust_collector.pressure_pa", "field": "pressure_pa", "asset_id": "dust_collector", "unit": "Pa", "range": [500, 2500], "frequency": "1h", "role": "safety_critical", "quality_rule": "required;range;continuous"},
            {"code": "power_meter.fan_power_kw", "field": "fan_power_kw", "asset_id": "power_meter", "unit": "kW", "range": [0, 10000], "frequency": "1h", "role": "target", "quality_rule": "required;nonnegative"},
            {"code": "sintering.feo_truth_pct", "field": "feo_truth_pct", "asset_id": "sintering_line", "unit": "%", "range": [0, 20], "frequency": "1h", "role": "quality_target", "quality_rule": "required;range"},
        ],
        "park_multi_energy": [
            {"code": "grid.import_kw", "field": "grid_import_kw", "asset_id": "grid", "unit": "kW", "range": [0, 100000], "frequency": "1h", "role": "safety_critical", "quality_rule": "required;nonnegative"},
            {"code": "pv.power_kw", "field": "pv_power_kw", "asset_id": "pv", "unit": "kW", "range": [0, 100000], "frequency": "1h", "role": "context", "quality_rule": "required;nonnegative"},
            {"code": "battery.soc_pct", "field": "storage_soc_pct", "asset_id": "battery", "unit": "%", "range": [0, 100], "frequency": "1h", "role": "safety_critical", "quality_rule": "required;range;continuous"},
            {"code": "battery.charge_kw", "field": "charge_kw", "asset_id": "battery", "unit": "kW", "range": [0, 50000], "frequency": "1h", "role": "control_candidate", "quality_rule": "required;nonnegative"},
            {"code": "battery.discharge_kw", "field": "discharge_kw", "asset_id": "battery", "unit": "kW", "range": [0, 50000], "frequency": "1h", "role": "control_candidate", "quality_rule": "required;nonnegative"},
            {"code": "market.tariff", "field": "tariff_cny_per_kwh", "asset_id": "grid", "unit": "CNY/kWh", "range": [0, 10], "frequency": "1h", "role": "context", "quality_rule": "required;range"},
        ],
    }
    analysis_profiles = {
        "sintering_dust_efficiency": {
            "id": "sintering_rule_baseline_v1", "version": "1.0.0",
            "method": "scenario_mean_plus_1_5_sigma_and_pressure_constraint",
            "purpose": "离线能耗基线与异常复核",
        },
        "park_multi_energy": {
            "id": "park_dispatch_baseline_v1", "version": "1.0.0",
            "method": "tariff_pv_storage_simulation_baseline",
            "purpose": "离线峰谷调度策略复核",
        },
    }

    def __init__(self, output_dir: str = "/app/uploads/virtual-simulations"):
        self.output_dir = Path(output_dir)
        self.runs: dict[str, dict] = {}

    def list_scenarios(self):
        return [{"key": key, **value, "topology": self.topology[key]} for key, value in self.scenarios.items()]

    def scenario_config(self, scenario_key: str):
        if scenario_key not in self.scenarios:
            raise ValueError("未知工业仿真场景")
        return {"scenario_key": scenario_key, "defaults": self.config_defaults[scenario_key]}

    def scenario_contract(self, scenario_key: str):
        if scenario_key not in self.scenarios:
            raise ValueError("未知工业仿真场景")
        return {
            "scenario_key": scenario_key, "topology": self.topology[scenario_key],
            "points": self.point_contracts[scenario_key],
            "integration_rule": "未来真实数据应映射到 field 与 code；单位换算必须在接入层完成。",
        }

    def list_analysis_profiles(self):
        """Stable identities for offline analysis methods, not deployable control models."""
        return [
            {"scenario_key": key, **profile,
             "scope": "离线虚拟回放；仅支持人工评审，不支持控制下发"}
            for key, profile in self.analysis_profiles.items()
        ]

    def validate_config(self, scenario_key: str, supplied: dict | None):
        if scenario_key not in self.scenarios:
            raise ValueError("未知工业仿真场景")
        config = dict(self.config_defaults[scenario_key])
        supplied = supplied or {}
        unknown = set(supplied) - set(config)
        if unknown:
            raise ValueError("不支持的配置项: " + ", ".join(sorted(unknown)))
        for key, value in supplied.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(key + " 必须为数值")
            config[key] = float(value)
        if scenario_key == "sintering_dust_efficiency":
            if not 100 <= config["production_base_tph"] <= 600 or not 1e-12 <= config["fan_curve_coefficient"] <= 1e-8:
                raise ValueError("产量或风机曲线参数超出安全仿真范围")
            if not 500 <= config["pressure_min_pa"] < config["pressure_max_pa"] <= 2500:
                raise ValueError("压力边界无效")
            if not 0 < config["feo_min_pct"] < config["feo_max_pct"] <= 20:
                raise ValueError("FeO 工艺窗口无效")
        else:
            if not 0 < config["pv_capacity_kw"] <= 100000 or not 1 <= config["storage_capacity_kwh"] <= 100000:
                raise ValueError("新能源资产参数超出安全仿真范围")
            if not 0 < config["storage_power_kw"] <= 50000 or not 0 <= config["soc_min_pct"] < config["soc_max_pct"] <= 100:
                raise ValueError("储能约束无效")
            if not 100 <= config["demand_limit_kw"] <= 100000:
                raise ValueError("需量边界无效")
            if not 0 < config["valley_tariff"] <= config["flat_tariff"] <= config["peak_tariff"] <= 10:
                raise ValueError("峰平谷电价层级无效")
        return config

    def generate(self, scenario_key: str, hours: int = 168, seed: int = 20260720,
                 anomaly_rate: float = 0.03, config: dict | None = None):
        config = self.validate_config(scenario_key, config)
        hours = max(24, min(int(hours), 24 * 366))
        anomaly_rate = max(0.0, min(float(anomaly_rate), 0.2))
        rng = random.Random(seed)
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)
        if scenario_key == "sintering_dust_efficiency":
            timeseries, events = self._sintering(start, hours, rng, anomaly_rate, config)
        else:
            timeseries, events = self._multi_energy(start, hours, rng, anomaly_rate, config)
        run_id = uuid.uuid4().hex[:16]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data_path = self.output_dir / (run_id + "_timeseries.csv")
        event_path = self.output_dir / (run_id + "_events.csv")
        self._write_csv(data_path, timeseries)
        self._write_csv(
            event_path, events,
            ["timestamp", "event_type", "recommendation", "approval_status", "rollback_reason", "constraint_pass"],
        )
        report = self._report(scenario_key, timeseries, events, seed, data_path, event_path, config)
        self._persist_report(report)
        self.runs[run_id] = report
        return report

    def get_run(self, run_id: str):
        if run_id in self.runs:
            return self.runs[run_id]
        try:
            report = json.loads((self.output_dir / (run_id + "_metadata.json")).read_text(encoding="utf-8"))
            self.runs[run_id] = report
            return report
        except (OSError, json.JSONDecodeError):
            return None

    def list_runs(self, limit: int = 50):
        reports = []
        for path in sorted(self.output_dir.glob("*_metadata.json"), reverse=True)[:limit]:
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                reports.append({key: report[key] for key in ("run_id", "scenario_key", "record_count", "event_count", "constraint_pass_rate", "generated_at", "configuration")})
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return reports

    def evaluate_run(self, run_id: str):
        """Evaluate a persisted run without re-generating data."""
        report = self.get_run(run_id)
        if not report:
            return None
        try:
            with open(report["timeseries_path"], "r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            return None
        try:
            with open(report["events_path"], "r", encoding="utf-8-sig", newline="") as handle:
                events = list(csv.DictReader(handle))
        except OSError:
            events = []
        columns = list(rows[0]) if rows else []
        total_cells = len(rows) * len(columns)
        missing_cells = sum(1 for row in rows for value in row.values() if value in ("", None))
        constraint_columns = [column for column in columns if column.endswith("_constraint_pass")]
        constraint_values = [
            str(row[column]).lower() == "true" for row in rows for column in constraint_columns
        ]
        anomalies = sum(str(row.get("anomaly_truth", "")).lower() == "true" for row in rows)
        approvals = {}
        for event in events:
            status = event.get("approval_status", "unknown")
            approvals[status] = approvals.get(status, 0) + 1
        business = self._business_metrics(report, rows)
        return {
            "run_id": run_id,
            "data_quality": {
                "row_count": len(rows), "column_count": len(columns),
                "missing_rate": round(missing_cells / total_cells, 6) if total_cells else 0,
                "anomaly_truth_count": anomalies,
                "truth_labels": report.get("truth_labels", []),
            },
            "constraint_audit": {
                "constraint_columns": constraint_columns,
                "pass_rate": round(sum(constraint_values) / len(constraint_values), 6) if constraint_values else 1,
                "violations": len(constraint_values) - sum(constraint_values),
            },
            "feedback_audit": {"event_count": len(events), "approval_status_counts": approvals},
            "simulation_baseline": business,
            "safety_note": report["safety_note"],
        }

    def validate_contract_run(self, run_id: str):
        report = self.get_run(run_id)
        if not report:
            return None
        try:
            with open(report["timeseries_path"], "r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            return None
        points = self.point_contracts[report["scenario_key"]]
        fields = set(rows[0]) if rows else set()
        checks, failures = [], []
        for point in points:
            field = point["field"]
            if field not in fields:
                checks.append({"code": point["code"], "status": "failed", "violations": len(rows)})
                failures.append({"code": point["code"], "reason": "missing_field"})
                continue
            lower, upper = point["range"]
            violations = 0
            for row in rows:
                try:
                    value = float(row[field])
                    violations += int(value < lower or value > upper)
                except (TypeError, ValueError):
                    violations += 1
            checks.append({"code": point["code"], "status": "passed" if violations == 0 else "failed", "violations": violations})
            if violations:
                failures.append({"code": point["code"], "reason": "range_or_parse", "violations": violations})
        passed = not failures
        return {
            "run_id": run_id, "scenario_key": report["scenario_key"], "checks": checks, "failures": failures,
            "applicability": {
                "prediction_ready": passed,
                "optimization_ready": passed and report.get("constraint_pass_rate", 0) >= 0.95,
                "control_ready": False,
                "control_reason": "虚拟数据仅用于离线验证；真实现场需完成只读校准、联锁评审和人工审批流程。",
            },
        }

    def analyze_run(self, run_id: str):
        """Offline rule-baseline analysis; outputs are never control commands."""
        report = self.get_run(run_id)
        if not report:
            return None
        try:
            with open(report["timeseries_path"], "r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            return None
        profile = self.analysis_profiles[report["scenario_key"]]
        if report["scenario_key"] == "sintering_dust_efficiency":
            energy = [float(row["specific_energy_kwh_per_t"]) for row in rows]
            mean = sum(energy) / len(energy)
            variance = sum((value - mean) ** 2 for value in energy) / len(energy)
            threshold = mean + 1.5 * variance ** 0.5
            predicted = [float(row["specific_energy_kwh_per_t"]) > threshold or str(row["pressure_constraint_pass"]).lower() != "true" for row in rows]
            truth = [str(row["anomaly_truth"]).lower() == "true" for row in rows]
            analysis = {
                "run_id": run_id, "analysis_type": "energy_baseline_and_anomaly_detection",
                "profile": profile,
                "baseline": {"method": "scenario_mean_plus_1_5_sigma", "specific_energy_kwh_per_t": round(mean, 5), "alert_threshold": round(threshold, 5)},
                "anomaly_evaluation": self._classification_metrics(predicted, truth),
                "opportunities": {"high_specific_energy_records": sum(value > threshold for value in energy), "recommendation": "仅供人工复核：优先检查压力越界和单位能耗超基线时段。"},
                "disclaimer": "这是虚拟回放的规则基线，不是生产模型精度或节能承诺。",
            }
        else:
            baseline = self._business_metrics(report, rows)
            total_load = sum(float(row["base_load_kw"]) + float(row["flexible_load_kw"]) for row in rows)
            grid_import = sum(float(row["grid_import_kw"]) for row in rows)
            analysis = {
                "run_id": run_id, "analysis_type": "multi_energy_strategy_evaluation",
                "profile": profile,
                "strategy": baseline,
                "renewable_and_storage_effect": {
                    "grid_import_reduction_kwh": round(max(0, total_load - grid_import), 3),
                    "grid_import_reduction_rate": round(max(0, total_load - grid_import) / total_load, 6) if total_load else 0,
                },
                "recommendation": "仅供人工审批：在约束全通过时进入策略复核；不得直接下发控制。",
            }
        report["analysis"] = analysis
        self._persist_report(report)
        return analysis

    @staticmethod
    def _classification_metrics(predicted: list[bool], truth: list[bool]):
        tp = sum(p and t for p, t in zip(predicted, truth))
        fp = sum(p and not t for p, t in zip(predicted, truth))
        fn = sum(not p and t for p, t in zip(predicted, truth))
        tn = len(predicted) - tp - fp - fn
        return {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(tp / (tp + fp), 6) if tp + fp else 0,
            "recall": round(tp / (tp + fn), 6) if tp + fn else 0,
            "accuracy": round((tp + tn) / len(predicted), 6) if predicted else 0,
        }

    def admission_report(self, run_id: str):
        contract = self.validate_contract_run(run_id)
        evaluation = self.evaluate_run(run_id)
        analysis = self.analyze_run(run_id)
        if not contract or not evaluation or not analysis:
            return None
        eligible = contract["applicability"]["optimization_ready"] and evaluation["constraint_audit"]["violations"] == 0
        admission = {
            "run_id": run_id, "decision": "review_ready" if eligible else "blocked",
            "reasons": [] if eligible else ["数据契约或运行约束未满足"],
            "evidence": {"contract": contract["applicability"], "constraint_pass_rate": evaluation["constraint_audit"]["pass_rate"], "analysis_type": analysis["analysis_type"]},
            "allowed_next_step": "人工审批建议评审" if eligible else "修复数据/约束后重新回放",
            "prohibited": ["自动控制下发", "客户节能收益承诺"],
        }
        persisted = self.get_run(run_id)
        persisted["admission"] = admission
        self._persist_report(persisted)
        return admission

    def compare_runs(self, baseline_run_id: str, candidate_run_id: str):
        """Compare two replays of one scenario without making a production decision."""
        baseline_report = self.get_run(baseline_run_id)
        candidate_report = self.get_run(candidate_run_id)
        if not baseline_report or not candidate_report:
            return None
        if baseline_report["scenario_key"] != candidate_report["scenario_key"]:
            raise ValueError("只能对比相同工业仿真场景的运行")
        baseline = self.analyze_run(baseline_run_id)
        candidate = self.analyze_run(candidate_run_id)
        baseline_admission = self.admission_report(baseline_run_id)
        candidate_admission = self.admission_report(candidate_run_id)
        scenario_key = baseline_report["scenario_key"]
        if scenario_key == "park_multi_energy":
            metrics = [
                ("simulated_cost_cny", "模拟成本（CNY）", candidate["strategy"]["simulated_cost_cny"], baseline["strategy"]["simulated_cost_cny"], "lower_is_better"),
                ("simulated_saving_rate", "模拟成本改善率", candidate["strategy"]["simulated_saving_rate"], baseline["strategy"]["simulated_saving_rate"], "higher_is_better"),
                ("grid_import_reduction_rate", "购电削减率", candidate["renewable_and_storage_effect"]["grid_import_reduction_rate"], baseline["renewable_and_storage_effect"]["grid_import_reduction_rate"], "higher_is_better"),
            ]
        else:
            metrics = [
                ("average_specific_energy_kwh_per_t", "平均单位能耗（kWh/t）", candidate["baseline"]["specific_energy_kwh_per_t"], baseline["baseline"]["specific_energy_kwh_per_t"], "lower_is_better"),
                ("precision", "异常检测精确率", candidate["anomaly_evaluation"]["precision"], baseline["anomaly_evaluation"]["precision"], "higher_is_better"),
                ("recall", "异常检测召回率", candidate["anomaly_evaluation"]["recall"], baseline["anomaly_evaluation"]["recall"], "higher_is_better"),
            ]
        deltas = [{"key": key, "label": label, "baseline": value0, "candidate": value1,
                   "delta": round(value1 - value0, 6), "direction": direction,
                   "improved": value1 < value0 if direction == "lower_is_better" else value1 > value0}
                  for key, label, value1, value0, direction in metrics]
        candidate_eligible = candidate_admission["decision"] == "review_ready"
        improved = any(item["improved"] for item in deltas)
        return {
            "scenario_key": scenario_key, "baseline_run_id": baseline_run_id, "candidate_run_id": candidate_run_id,
            "profile": candidate["profile"], "metric_deltas": deltas,
            "recommendation": {
                "candidate_for_manual_review": candidate_eligible and improved,
                "reason": "候选运行通过离线准入且至少一项指标改善" if candidate_eligible and improved else "仅保留对比记录；不满足离线准入或无指标改善",
                "prohibited": ["自动控制下发", "客户节能收益承诺"],
            },
            "admissions": {"baseline": baseline_admission["decision"], "candidate": candidate_admission["decision"]},
        }

    def create_strategy_experiment(self, baseline_run_id: str, candidate_run_id: str,
                                   name: str | None = None, review_note: str = ""):
        """Persist an offline comparison as an auditable, non-control experiment."""
        comparison = self.compare_runs(baseline_run_id, candidate_run_id)
        if not comparison:
            return None
        experiment_id = uuid.uuid4().hex[:16]
        experiment = {
            "experiment_id": experiment_id,
            "name": (name or "策略回放对比").strip()[:120] or "策略回放对比",
            "scenario_key": comparison["scenario_key"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "baseline_run_id": baseline_run_id, "candidate_run_id": candidate_run_id,
            "analysis_profile": comparison["profile"], "comparison": comparison,
            "review_note": review_note.strip()[:1000],
            "lifecycle": "pending_manual_review" if comparison["recommendation"]["candidate_for_manual_review"] else "recorded",
            "prohibited": ["自动控制下发", "客户节能收益承诺"],
        }
        directory = self.output_dir.parent / "strategy-experiments"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (experiment_id + ".json")).write_text(json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8")
        return experiment

    def list_strategy_experiments(self, limit: int = 50):
        directory = self.output_dir.parent / "strategy-experiments"
        if not directory.exists():
            return []
        items = []
        for path in sorted(directory.glob("*.json"), reverse=True)[:limit]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                items.append({key: item[key] for key in ("experiment_id", "name", "scenario_key", "created_at", "baseline_run_id", "candidate_run_id", "lifecycle", "analysis_profile")})
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return items

    def run_regression_suite(self, scenario_key: str, seeds: list[int], hours: int = 168,
                             configuration: dict | None = None):
        """Run reproducible offline replays and persist comparable experiment evidence."""
        if scenario_key not in self.scenarios:
            raise ValueError("未知工业仿真场景")
        seeds = [int(seed) for seed in seeds][:12]
        if not seeds:
            raise ValueError("至少需要一个回归种子")
        runs = [self.generate(scenario_key, hours=hours, seed=seed, config=configuration) for seed in seeds]
        gates = []
        for run in runs:
            contract = self.validate_contract_run(run["run_id"])
            evaluation = self.evaluate_run(run["run_id"])
            gates.append({"run_id": run["run_id"], "contract_ready": contract["applicability"]["optimization_ready"],
                          "constraint_violations": evaluation["constraint_audit"]["violations"]})
        experiments = [self.create_strategy_experiment(runs[0]["run_id"], run["run_id"], "批量策略回归")
                       for run in runs[1:]]
        return {"scenario_key": scenario_key, "run_count": len(runs), "run_ids": [run["run_id"] for run in runs],
                "gates": gates, "passed": all(item["contract_ready"] and item["constraint_violations"] == 0 for item in gates),
                "experiment_ids": [item["experiment_id"] for item in experiments if item]}

    @staticmethod
    def _business_metrics(report: dict, rows: list[dict]):
        if not rows:
            return {}
        if report["scenario_key"] == "park_multi_energy":
            baseline_cost = sum(
                float(row["base_load_kw"]) * float(row["tariff_cny_per_kwh"]) +
                float(row["flexible_load_kw"]) * float(row["tariff_cny_per_kwh"])
                for row in rows
            )
            optimized_cost = sum(float(row["energy_cost_cny"]) for row in rows)
            return {
                "baseline_cost_cny": round(baseline_cost, 3),
                "simulated_cost_cny": round(optimized_cost, 3),
                "simulated_saving_cny": round(baseline_cost - optimized_cost, 3),
                "simulated_saving_rate": round((baseline_cost - optimized_cost) / baseline_cost, 6) if baseline_cost else 0,
                "disclaimer": "仅为虚拟工厂情景基线，不代表客户节能收益。",
            }
        specific = [float(row["specific_energy_kwh_per_t"]) for row in rows]
        return {
            "average_specific_energy_kwh_per_t": round(sum(specific) / len(specific), 5),
            "min_specific_energy_kwh_per_t": round(min(specific), 5),
            "max_specific_energy_kwh_per_t": round(max(specific), 5),
            "disclaimer": "仅为虚拟工厂运行指标，不代表客户节能收益。",
        }

    def _persist_report(self, report: dict):
        (self.output_dir / (report["run_id"] + "_metadata.json")).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None):
        if not rows and not fieldnames:
            return
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames or list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def _sintering(self, start, hours, rng, anomaly_rate, config):
        rows, events = [], []
        for index in range(hours):
            ts = start + timedelta(hours=index)
            production = max(100.0, config["production_base_tph"] + 28 * math.sin(index / 11) + rng.gauss(0, 8))
            ambient = 18 + 10 * math.sin((ts.hour - 7) / 24 * math.tau) + rng.gauss(0, 1)
            vfd_hz = min(50.0, max(30.0, 40 + (production - 250) * 0.08 + rng.gauss(0, 1.1)))
            airflow = 620000 + vfd_hz * 7800 + rng.gauss(0, 8000)
            pressure = 1250 + (airflow - 930000) / 270 + rng.gauss(0, 20)
            anomaly = rng.random() < anomaly_rate
            if anomaly:
                pressure += rng.choice([-1, 1]) * rng.uniform(180, 320)
            fan_power = config["fan_curve_coefficient"] * airflow ** 2.55 * (1 + max(0, ambient - 30) * 0.004)
            specific_kwh = fan_power / production
            quality_feo = 8.1 + 0.004 * (pressure - 1250) + rng.gauss(0, 0.12)
            pressure_ok = config["pressure_min_pa"] <= pressure <= config["pressure_max_pa"]
            quality_ok = config["feo_min_pct"] <= quality_feo <= config["feo_max_pct"]
            recommendation = "保持当前设定"
            if pressure > config["pressure_max_pa"]:
                recommendation = "建议下调风机频率 1.0Hz，并人工确认"
            elif pressure < config["pressure_min_pa"]:
                recommendation = "建议上调风机频率 1.0Hz，并人工确认"
            status = "executed" if not anomaly and recommendation == "保持当前设定" else ("rejected_safety" if anomaly else "pending_approval")
            row = {
                "timestamp": ts.isoformat(), "production_tph": round(production, 3),
                "ambient_temperature_c": round(ambient, 3), "fan_frequency_hz": round(vfd_hz, 3),
                "airflow_m3h": round(airflow, 3), "pressure_pa": round(pressure, 3),
                "fan_power_kw": round(fan_power, 3), "specific_energy_kwh_per_t": round(specific_kwh, 4),
                "feo_truth_pct": round(quality_feo, 3), "pressure_constraint_pass": pressure_ok,
                "quality_constraint_pass": quality_ok, "anomaly_truth": anomaly,
            }
            rows.append(row)
            if recommendation != "保持当前设定" or anomaly:
                events.append({"timestamp": ts.isoformat(), "event_type": "optimization_feedback",
                    "recommendation": recommendation, "approval_status": status,
                    "rollback_reason": "传感器/工况异常" if anomaly else "",
                    "constraint_pass": pressure_ok and quality_ok})
        return rows, events

    def _multi_energy(self, start, hours, rng, anomaly_rate, config):
        rows, events, soc = [], [], 55.0
        for index in range(hours):
            ts = start + timedelta(hours=index)
            hour = ts.hour
            tariff = config["peak_tariff"] if hour in (18, 19, 20, 21) else (config["flat_tariff"] if 8 <= hour < 18 else config["valley_tariff"])
            solar_shape = max(0, math.sin((hour - 6) / 12 * math.pi))
            pv_kw = config["pv_capacity_kw"] * solar_shape * (0.75 + rng.random() * 0.2)
            base_load = 1180 + 220 * math.sin((hour - 9) / 24 * math.tau) + rng.gauss(0, 35)
            flexible_kw = 120 if 9 <= hour < 17 else 35
            anomaly = rng.random() < anomaly_rate
            if anomaly:
                base_load += rng.uniform(250, 420)
            charge_kw = 0.0
            discharge_kw = 0.0
            if tariff <= config["valley_tariff"] + 0.01 and soc < config["soc_max_pct"]:
                charge_kw = min(config["storage_power_kw"], (config["soc_max_pct"] - soc) * config["storage_capacity_kwh"] / 100)
            elif tariff >= config["peak_tariff"] - 0.01 and soc > config["soc_min_pct"]:
                discharge_kw = min(config["storage_power_kw"], (soc - config["soc_min_pct"]) * config["storage_capacity_kwh"] / 100)
            soc = min(config["soc_max_pct"] + 7, max(config["soc_min_pct"] - 8, soc + charge_kw / config["storage_capacity_kwh"] * 100 - discharge_kw / 0.95 / config["storage_capacity_kwh"] * 100))
            grid_kw = max(0, base_load + flexible_kw + charge_kw - pv_kw - discharge_kw)
            demand_limit = config["demand_limit_kw"]
            demand_ok = grid_kw <= demand_limit
            cost = grid_kw * tariff
            recommendation = "保持计划"
            if not demand_ok:
                recommendation = "建议削减可调负荷并优先放电，人工确认"
            status = "rejected_safety" if anomaly else ("pending_approval" if recommendation != "保持计划" else "executed")
            rows.append({
                "timestamp": ts.isoformat(), "tariff_cny_per_kwh": tariff, "pv_power_kw": round(pv_kw, 3),
                "base_load_kw": round(base_load, 3), "flexible_load_kw": flexible_kw,
                "storage_soc_pct": round(soc, 3), "charge_kw": round(charge_kw, 3),
                "discharge_kw": round(discharge_kw, 3), "grid_import_kw": round(grid_kw, 3),
                "energy_cost_cny": round(cost, 3), "demand_constraint_pass": demand_ok,
                "anomaly_truth": anomaly,
            })
            if recommendation != "保持计划" or anomaly:
                events.append({"timestamp": ts.isoformat(), "event_type": "dispatch_feedback",
                    "recommendation": recommendation, "approval_status": status,
                    "rollback_reason": "负荷突增/数据异常" if anomaly else "",
                    "constraint_pass": demand_ok})
        return rows, events

    def _report(self, scenario_key, rows, events, seed, data_path, event_path, config):
        constraint_columns = [key for key in rows[0] if key.endswith("_constraint_pass")]
        checks = [bool(row[col]) for row in rows for col in constraint_columns]
        return {
            "run_id": data_path.stem.replace("_timeseries", ""), "scenario_key": scenario_key,
            "scenario": self.scenarios[scenario_key], "seed": seed, "record_count": len(rows),
            "configuration": config,
            "topology": self.topology[scenario_key],
            "event_count": len(events), "constraint_pass_rate": round(sum(checks) / len(checks), 4) if checks else 1,
            "truth_labels": ["anomaly_truth"] + constraint_columns,
            "timeseries_path": str(data_path), "events_path": str(event_path),
            "sample": rows[:5], "event_sample": events[:5],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "safety_note": "仅用于离线仿真和人工审批建议验证；不得直接下发工业控制指令。",
        }


_service = IndustrialSimulationService()


def get_industrial_simulation_service():
    return _service
