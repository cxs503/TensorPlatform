import tempfile
import unittest

from services.industrial_simulation_service import IndustrialSimulationService
from services.read_only_ingestion_sandbox import ReadOnlyIngestionSandbox


class ReadOnlyIngestionSandboxTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = IndustrialSimulationService(self.tempdir.name)
        self.sandbox = ReadOnlyIngestionSandbox(self.service)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_converts_units_and_checks_benchmark(self):
        content = chr(10).join([
            "ts,grid_w,pv_w,soc,charge,discharge,tariff",
            "2026-01-01T00:00:00+00:00,900000,100000,55,0,0,0.42",
            "2026-01-01T01:00:00+00:00,920000,120000,56,0,0,0.42",
        ]).encode()
        report = self.sandbox.validate_csv(
            "park_multi_energy", content,
            {"timestamp": "ts", "grid_import_kw": "grid_w", "pv_power_kw": "pv_w",
             "storage_soc_pct": "soc", "charge_kw": "charge", "discharge_kw": "discharge",
             "tariff_cny_per_kwh": "tariff"},
            {"grid_import_kw": "W", "pv_power_kw": "W"},
        )
        self.assertTrue(report["quality_gate"]["passed"])
        self.assertEqual(report["normalized_sample"][0]["grid_import_kw"], 900.0)
        run = self.service.generate("park_multi_energy", hours=24, seed=5, config={"demand_limit_kw": 100000})
        self.assertTrue(self.sandbox.benchmark_gate(run["run_id"])["passed"])

    def test_rejects_out_of_order_or_unknown_unit(self):
        content = chr(10).join([
            "timestamp,grid_import_kw,pv_power_kw,storage_soc_pct,charge_kw,discharge_kw,tariff_cny_per_kwh",
            "2026-01-01T01:00:00+00:00,900,100,55,0,0,0.42",
            "2026-01-01T00:00:00+00:00,900,100,55,0,0,0.42",
        ]).encode()
        report = self.sandbox.validate_csv("park_multi_energy", content, source_units={"grid_import_kw": "amps"})
        self.assertFalse(report["quality_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
