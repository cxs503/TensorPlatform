"""Stage 5 offline path: scenario -> registry -> fault replay -> cleanup."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from quality.offline_validation import RunRegistry
from scenarios.lib.core import generate, load_manifest, write_csv
from tools.industrial_replay.replay import (
    FaultPlan,
    FreezeRule,
    ReplayEngine,
    SequenceRange,
    build_sink,
    load_records,
)


class Stage5OfflineIntegrationTest(unittest.TestCase):
    def test_synthetic_fault_replay_is_registered_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            previous = os.environ.get("ODF_VALIDATION_ALLOWED_ROOTS")
            os.environ["ODF_VALIDATION_ALLOWED_ROOTS"] = str(root)
            try:
                registry = RunRegistry(root / "registry")
                manifest = load_manifest("photovoltaic_power")
                run = registry.create(
                    "photovoltaic_power",
                    scenario_version=manifest["version"],
                    environment="test",
                    asset_type="synthetic",
                    lease_seconds=60,
                    metadata={"scenario_mode": "fault", "seed": manifest["seed"]},
                )
                run_id = run["run_id"]
                run_dir = root / "registry" / run_id

                source = write_csv(
                    generate("photovoltaic_power", "fault", rows=32),
                    run_dir / "photovoltaic-fault.csv",
                )
                source_asset = registry.register_asset(
                    run_id, kind="scenario-csv", uri=str(source)
                )
                self.assertEqual(64, len(source_asset["checksum"]))

                records = load_records(source)
                plan = FaultPlan(
                    disconnect=(SequenceRange(5, 6),),
                    duplicate_every=7,
                    reorder_window=3,
                    latency_ms=25,
                    latency_every=4,
                    freezes=(
                        FreezeRule("global_irradiance_w_m2", SequenceRange(8, 10)),
                    ),
                )
                output = run_dir / "replayed.jsonl"
                sink = build_sink(
                    f"file:{output}", headers={}, timeout=1, retries=0
                )
                summary = ReplayEngine(
                    records, run_id=run_id, fault_plan=plan
                ).run(sink, speed=0)

                self.assertEqual(
                    {
                        "source_records": 32,
                        "dropped": 2,
                        "duplicates": 4,
                        "output_records": 34,
                    },
                    summary,
                )
                envelopes = [
                    json.loads(line)
                    for line in output.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(34, len(envelopes))
                self.assertTrue(all(item["replay"]["run_id"] == run_id for item in envelopes))
                fault_names = {
                    fault
                    for item in envelopes
                    for fault in item["replay"]["faults"]
                }
                self.assertIn("duplicate", fault_names)
                self.assertIn("reorder:3", fault_names)
                self.assertIn("sensor_freeze:global_irradiance_w_m2", fault_names)

                replay_asset = registry.register_asset(
                    run_id, kind="replay-jsonl", uri=str(output)
                )
                self.assertEqual(64, len(replay_asset["checksum"]))
                finished = registry.finish(run_id, "passed", summary=summary)
                self.assertEqual("passed", finished["status"])
                self.assertEqual(2, len(finished["assets"]))
                self.assertTrue(registry.cleanup(run_id))
                self.assertFalse(run_dir.exists())
            finally:
                if previous is None:
                    os.environ.pop("ODF_VALIDATION_ALLOWED_ROOTS", None)
                else:
                    os.environ["ODF_VALIDATION_ALLOWED_ROOTS"] = previous


if __name__ == "__main__":
    unittest.main()
