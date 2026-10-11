"""Tests for scoped, offline-only Stage 5 evidence generation."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.generate_stage5_offline_evidence import (
    EVIDENCE_CATEGORIES,
    MODULE_ID,
    atomic_json,
    main,
    run_offline_validation,
    update_inventory,
)
from scripts.maturity_gate import MaturityGate, load_json


ROOT = Path(__file__).resolve().parents[1]
POLICY = load_json(ROOT / "quality/maturity/policy.json")
SOURCE_INVENTORY = load_json(ROOT / "quality/maturity/modules.json")


class Stage5EvidenceAutomationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generated_report = run_offline_validation(repo_root=ROOT, rows=8)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.report_path = (
            self.repo
            / "quality/maturity/evidence/steel_energy_apps/stage5-offline.json"
        )
        self.inventory_path = self.repo / "quality/maturity/modules.json"
        isolated_inventory = copy.deepcopy(SOURCE_INVENTORY)
        module = next(
            item for item in isolated_inventory["modules"] if item["id"] == MODULE_ID
        )
        # Repository inventory may already contain the reviewed real artifact.
        # This temporary fixture exercises a first import without weakening the
        # production rule that refuses replacement by a different artifact.
        module["evidence"] = {}
        atomic_json(self.inventory_path, isolated_inventory)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_report(self) -> dict:
        report = copy.deepcopy(self.generated_report)
        atomic_json(self.report_path, report)
        return report

    def test_report_is_scoped_reproducible_and_inventory_is_unchanged_by_default(self) -> None:
        before = self.inventory_path.read_bytes()
        report = self.write_report()

        self.assertTrue(report["passed"])
        self.assertEqual(MODULE_ID, report["module_id"])
        self.assertEqual(set(EVIDENCE_CATEGORIES), set(report["evidence_categories"]))
        self.assertTrue(report["cleanup"]["completed"])
        self.assertRegex(report["run_id"], r"^val-.*-[a-f0-9]{8}$")
        self.assertEqual(6, len(report["scenarios"]))
        for scenario in report["scenarios"]:
            self.assertIn("version", scenario)
            self.assertIsInstance(scenario["seed"], int)
            self.assertEqual({"baseline", "drift", "fault"}, set(scenario["modes"]))
            for mode in scenario["modes"].values():
                self.assertEqual(64, len(mode["fingerprint"]))
        self.assertIn("real_service", report["scope"]["does_not_prove"])
        self.assertEqual(before, self.inventory_path.read_bytes())

        repeated = run_offline_validation(repo_root=ROOT, rows=8)
        first_modes = {
            (scenario["id"], mode): details["fingerprint"]
            for scenario in report["scenarios"]
            for mode, details in scenario["modes"].items()
        }
        repeated_modes = {
            (scenario["id"], mode): details["fingerprint"]
            for scenario in repeated["scenarios"]
            for mode, details in scenario["modes"].items()
        }
        self.assertNotEqual(report["run_id"], repeated["run_id"])
        self.assertEqual(first_modes, repeated_modes)
        self.assertEqual(
            report["replay"]["semantic_fingerprint"],
            repeated["replay"]["semantic_fingerprint"],
        )

    def test_explicit_inventory_update_does_not_promote_and_tamper_fails_gate(self) -> None:
        report = self.write_report()
        update_inventory(
            repo_root=self.repo,
            inventory_path=self.inventory_path,
            report_path=self.report_path,
            report=report,
        )
        inventory = load_json(self.inventory_path)
        module = next(item for item in inventory["modules"] if item["id"] == MODULE_ID)

        self.assertEqual("demo", module["observed_status"])
        self.assertEqual("beta", module["target_status"])
        self.assertEqual(set(EVIDENCE_CATEGORIES), set(module["evidence"]))
        before_tamper = MaturityGate(POLICY, inventory, self.repo).check(module, "beta")
        self.assertFalse(before_tamper.passed)
        self.assertEqual("demo", before_tamper.qualified_level)
        self.assertTrue(any(item.startswith("real_service:") for item in before_tamper.blockers))

        tampered = json.loads(self.report_path.read_text(encoding="utf-8"))
        tampered["scenarios"][0]["seed"] += 1
        atomic_json(self.report_path, tampered)
        after_tamper = MaturityGate(POLICY, inventory, self.repo).check(module, "beta")
        checksum_blockers = [
            item
            for item in after_tamper.blockers
            if item.startswith(tuple(f"{category}:" for category in EVIDENCE_CATEGORIES))
        ]
        self.assertEqual(3, len(checksum_blockers))
        self.assertTrue(all("checksum mismatch" in item for item in checksum_blockers))

    def test_failure_overwrites_stale_pass_and_never_updates_inventory(self) -> None:
        stale = self.write_report()
        before = self.inventory_path.read_bytes()
        with mock.patch(
            "scripts.generate_stage5_offline_evidence.generate",
            side_effect=RuntimeError("injected scenario failure"),
        ):
            exit_code = main(
                [
                    "--repo-root",
                    str(self.repo),
                    "--output",
                    str(self.report_path),
                    "--inventory",
                    str(self.inventory_path),
                    "--rows",
                    "8",
                    "--update-inventory",
                ]
            )

        self.assertTrue(stale["passed"])
        self.assertEqual(1, exit_code)
        failure = json.loads(self.report_path.read_text(encoding="utf-8"))
        self.assertFalse(failure["passed"])
        self.assertFalse(failure["checks"][0]["passed"])
        self.assertEqual(before, self.inventory_path.read_bytes())

    def test_inventory_refuses_report_changed_after_validation(self) -> None:
        report = self.write_report()
        tampered = copy.deepcopy(report)
        tampered["run_id"] = "val-tampered"
        atomic_json(self.report_path, tampered)
        with self.assertRaisesRegex(Exception, "differs from validated"):
            update_inventory(
                repo_root=self.repo,
                inventory_path=self.inventory_path,
                report_path=self.report_path,
                report=report,
            )

    def test_cli_refuses_to_overwrite_inventory_as_report(self) -> None:
        before = self.inventory_path.read_bytes()
        exit_code = main(
            [
                "--repo-root",
                str(self.repo),
                "--output",
                str(self.inventory_path),
                "--rows",
                "8",
            ]
        )
        self.assertEqual(1, exit_code)
        self.assertEqual(before, self.inventory_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
