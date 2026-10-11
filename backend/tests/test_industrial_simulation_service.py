import tempfile
import unittest
from pathlib import Path

from services.industrial_simulation_service import IndustrialSimulationService
from services.read_only_ingestion_sandbox import ReadOnlyIngestionSandbox


class IndustrialSimulationServiceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = IndustrialSimulationService(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_sintering_run_persists_contract_and_evaluation(self):
        run = self.service.generate("sintering_dust_efficiency", hours=24, seed=7)
        self.assertTrue(Path(run["timeseries_path"]).exists())
        self.assertTrue(Path(run["events_path"]).exists())
        self.assertEqual(len(run["topology"]["assets"]), 4)
        self.assertEqual(run["record_count"], 24)

        restarted = IndustrialSimulationService(self.tempdir.name)
        restored = restarted.get_run(run["run_id"])
        self.assertEqual(restored["configuration"], run["configuration"])
        self.assertEqual(restarted.evaluate_run(run["run_id"])["data_quality"]["row_count"], 24)
        contract = restarted.validate_contract_run(run["run_id"])
        self.assertEqual(len(contract["checks"]), 5)
        self.assertFalse(contract["applicability"]["control_ready"])

    def test_multi_energy_contract_and_cost_baseline(self):
        run = self.service.generate("park_multi_energy", hours=48, seed=11)
        evaluation = self.service.evaluate_run(run["run_id"])
        self.assertGreater(evaluation["simulation_baseline"]["baseline_cost_cny"], 0)
        self.assertIn("simulated_saving_rate", evaluation["simulation_baseline"])
        contract = self.service.validate_contract_run(run["run_id"])
        self.assertEqual(len(contract["checks"]), 6)
        self.assertTrue(contract["applicability"]["prediction_ready"])

    def test_analysis_profile_admission_and_same_scenario_comparison(self):
        baseline = self.service.generate("park_multi_energy", hours=48, seed=17)
        candidate = self.service.generate(
            "park_multi_energy", hours=48, seed=17,
            config={"pv_capacity_kw": 1100, "storage_capacity_kwh": 1800},
        )
        analysis = self.service.analyze_run(candidate["run_id"])
        self.assertEqual(analysis["profile"]["id"], "park_dispatch_baseline_v1")
        self.assertEqual(self.service.admission_report(candidate["run_id"])["decision"], "review_ready")
        comparison = self.service.compare_runs(baseline["run_id"], candidate["run_id"])
        self.assertEqual(comparison["scenario_key"], "park_multi_energy")
        self.assertEqual(len(comparison["metric_deltas"]), 3)
        self.assertIn("自动控制下发", comparison["recommendation"]["prohibited"])
        experiment = self.service.create_strategy_experiment(
            baseline["run_id"], candidate["run_id"], "光伏容量策略验证", "等待人工复核"
        )
        self.assertEqual(experiment["lifecycle"], "pending_manual_review")
        self.assertEqual(self.service.list_strategy_experiments()[0]["experiment_id"], experiment["experiment_id"])
        suite = self.service.run_regression_suite("park_multi_energy", [31, 32], hours=24, configuration={"demand_limit_kw": 100000})
        self.assertTrue(suite["passed"])
        self.assertEqual(len(suite["experiment_ids"]), 1)

    def test_rejects_invalid_safety_configuration(self):
        with self.assertRaises(ValueError):
            self.service.generate(
                "park_multi_energy",
                config={"soc_min_pct": 95, "soc_max_pct": 20},
            )

    def _legacy_csv_literal_newline_fixture(self):
        sandbox = ReadOnlyIngestionSandbox(self.service)
        content = (
            "ts,grid_w,pv_w,soc,charge,discharge,tariff\\n"
            "2026-01-01T00:00:00+00:00,900000,100000,55,0,0,0.42\\n"
            "2026-01-01T01:00:00+00:00,920000,120000,56,0,0,0.42\\n"
        ).encode()
        report = sandbox.validate_csv(
            "park_multi_energy", content,
            {"timestamp": "ts", "grid_import_kw": "grid_w", "pv_power_kw": "pv_w",
             "storage_soc_pct": "soc", "charge_kw": "charge", "discharge_kw": "discharge",
             "tariff_cny_per_kwh": "tariff"},
            {"grid_import_kw": "W", "pv_power_kw": "W"},
        )
        self.assertTrue(report["quality_gate"]["passed"])
        self.assertEqual(report["normalized_sample"][0]["grid_import_kw"], 900.0)
        run = self.service.generate("park_multi_energy", hours=24, seed=5, config={"demand_limit_kw": 100000})
        self.assertTrue(sandbox.benchmark_gate(run["run_id"])["passed"])


if __name__ == "__main__":
    unittest.main()
