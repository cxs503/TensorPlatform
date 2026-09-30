from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scenarios.lib.core import MAX_ROWS, MODES, SCENARIO_IDS, fingerprint, generate, load_manifest, write_csv


ROOT = Path(__file__).resolve().parents[2]


class ManifestTests(unittest.TestCase):
    def test_schema_and_all_manifests_are_self_consistent(self) -> None:
        schema = json.loads((ROOT / "scenarios" / "manifest.schema.json").read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertEqual(6, len(SCENARIO_IDS))
        for scenario_id in SCENARIO_IDS:
            with self.subTest(scenario=scenario_id):
                manifest = load_manifest(scenario_id)
                self.assertFalse(required.difference(manifest))
                self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
                self.assertGreater(manifest["frequency_seconds"], 0)
                self.assertLessEqual(manifest["default_rows"], MAX_ROWS)
                self.assertTrue((ROOT / "scenarios" / scenario_id / "data_card.md").is_file())
                for field in manifest["fields"]:
                    self.assertTrue({"name", "type", "role", "unit", "description"}.issubset(field))
                    if field["type"] in {"number", "integer"}:
                        self.assertLess(field["minimum"], field["maximum"])


class GenerationTests(unittest.TestCase):
    def test_every_scenario_and_mode_generates_valid_records(self) -> None:
        for scenario_id in SCENARIO_IDS:
            manifest = load_manifest(scenario_id)
            expected_fields = [field["name"] for field in manifest["fields"]]
            ground_truth = next(field["name"] for field in manifest["fields"] if field["role"] == "ground_truth")
            for mode in MODES:
                with self.subTest(scenario=scenario_id, mode=mode):
                    records = generate(scenario_id, mode, rows=128)
                    self.assertEqual(128, len(records))
                    self.assertEqual(expected_fields, list(records[0]))
                    self.assertTrue(all(record["scenario_mode"] == mode for record in records))
                    labels = {record[ground_truth] for record in records}
                    if mode == "baseline":
                        self.assertEqual(1, len(labels))
                    else:
                        self.assertGreater(len(labels), 1)

    def test_fingerprint_is_reproducible_and_seed_sensitive(self) -> None:
        for scenario_id in SCENARIO_IDS:
            with self.subTest(scenario=scenario_id):
                first = generate(scenario_id, "fault", rows=48, seed=1234)
                second = generate(scenario_id, "fault", rows=48, seed=1234)
                changed = generate(scenario_id, "fault", rows=48, seed=1235)
                self.assertEqual(first, second)
                self.assertEqual(fingerprint(first), fingerprint(second))
                self.assertNotEqual(fingerprint(first), fingerprint(changed))

    def test_modes_are_content_distinct(self) -> None:
        for scenario_id in SCENARIO_IDS:
            with self.subTest(scenario=scenario_id):
                hashes = {fingerprint(generate(scenario_id, mode, rows=96)) for mode in MODES}
                self.assertEqual(3, len(hashes))

    def test_modes_share_a_controlled_pre_injection_prefix(self) -> None:
        for scenario_id in SCENARIO_IDS:
            with self.subTest(scenario=scenario_id):
                datasets = [generate(scenario_id, mode, rows=100) for mode in MODES]
                normalized = [
                    [{key: value for key, value in row.items() if key != "scenario_mode"} for row in data[:55]]
                    for data in datasets
                ]
                self.assertEqual(normalized[0], normalized[1])
                self.assertEqual(normalized[0], normalized[2])

    def test_fault_signals_move_in_expected_industry_direction(self) -> None:
        def tail_mean(scenario: str, mode: str, field: str) -> float:
            values = [row[field] for row in generate(scenario, mode, rows=128)[96:]]
            return sum(values) / len(values)

        self.assertLess(
            tail_mean("sintering_endpoint_temperature", "fault", "windbox_pressure_kpa"),
            tail_mean("sintering_endpoint_temperature", "baseline", "windbox_pressure_kpa") - 2,
        )
        self.assertGreater(
            tail_mean("sintering_feo", "fault", "feo_pct"),
            tail_mean("sintering_feo", "baseline", "feo_pct") + 0.5,
        )
        self.assertGreater(
            tail_mean("dust_collection_efficiency", "fault", "specific_energy_kwh_per_1000m3"),
            tail_mean("dust_collection_efficiency", "baseline", "specific_energy_kwh_per_1000m3") + 0.2,
        )
        self.assertLess(
            tail_mean("photovoltaic_power", "fault", "inverter_availability"),
            tail_mean("photovoltaic_power", "baseline", "inverter_availability") - 0.3,
        )
        self.assertLess(
            tail_mean("rotating_equipment_fault", "fault", "remaining_useful_life_h"),
            tail_mean("rotating_equipment_fault", "baseline", "remaining_useful_life_h") - 300,
        )
        peak_rows = [row for row in generate("plant_load_forecast", "fault", rows=128) if row["event_label"] == "unplanned_peak"]
        self.assertTrue(peak_rows)
        self.assertGreater(min(row["plant_load_mw"] for row in peak_rows), 180)

    def test_row_and_seed_guards(self) -> None:
        with self.assertRaises(ValueError):
            generate(SCENARIO_IDS[0], rows=0)
        with self.assertRaises(ValueError):
            generate(SCENARIO_IDS[0], rows=MAX_ROWS + 1)
        with self.assertRaises(ValueError):
            generate(SCENARIO_IDS[0], seed=True)
        with self.assertRaises(ValueError):
            generate("not_a_scenario")


class OutputTests(unittest.TestCase):
    def test_explicit_csv_output_is_small_and_parseable(self) -> None:
        records = generate("photovoltaic_power", "fault", rows=12)
        with tempfile.TemporaryDirectory(prefix="foundation-scenario-") as temp_dir:
            output = write_csv(records, Path(temp_dir) / "pv.csv")
            self.assertLess(output.stat().st_size, 32_000)
            with output.open(newline="", encoding="utf-8") as handle:
                loaded = list(csv.DictReader(handle))
            self.assertEqual(12, len(loaded))

    def test_csv_output_cannot_target_production_paths(self) -> None:
        records = generate("photovoltaic_power", "baseline", rows=4)
        with self.assertRaisesRegex(ValueError, "approved validation root"):
            write_csv(records, "/var/lib/foundation-production/pv.csv")

    def test_cli_without_output_only_prints_summary(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scenarios", "sintering_feo", "--mode", "drift", "--rows", "8"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        summary = json.loads(result.stdout)
        self.assertEqual(8, summary["rows"])
        self.assertIsNone(summary["output"])
        self.assertEqual(64, len(summary["fingerprint"]))


if __name__ == "__main__":
    unittest.main()
