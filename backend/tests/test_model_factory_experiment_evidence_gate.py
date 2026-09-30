"""Fail-closed gates for model-factory Demo-to-Beta offline evidence."""

from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_model_factory_experiment_evidence import (
    EVIDENCE_CATEGORIES,
    MODULES,
    atomic_json,
    output_paths,
    run_offline_validation,
    update_inventory,
)
from scripts.maturity_gate import MaturityGate, load_json, sha256_file


ROOT = Path(__file__).resolve().parents[2]
POLICY = load_json(ROOT / "quality/maturity/policy.json")
INVENTORY = load_json(ROOT / "quality/maturity/modules.json")


class ModelFactoryExperimentEvidenceGateTest(unittest.TestCase):
    def test_committed_reports_are_machine_readable_and_checksum_locked(self):
        modules = {item["id"]: item for item in INVENTORY["modules"]}
        paths = output_paths(ROOT, Path("quality/maturity/evidence/model_factory_experiments"))
        for module_id in MODULES:
            with self.subTest(module=module_id):
                module = modules[module_id]
                self.assertEqual("demo", module["observed_status"])
                self.assertEqual("beta", module["target_status"])
                report = json.loads(paths[module_id].read_text(encoding="utf-8"))
                self.assertTrue(report["passed"])
                self.assertEqual(module_id, report["module_id"])
                self.assertEqual(set(EVIDENCE_CATEGORIES), set(report["evidence_categories"]))
                self.assertTrue(report["cleanup"]["completed"])
                self.assertFalse(report["cleanup"]["production_resources_touched"])
                self.assertIn("audit_persistence", report["scope"]["does_not_prove"])
                self.assertIn("observability", report["scope"]["does_not_prove"])
                self.assertIn("single-process offline run", report["scope"]["note"])
                self.assertEqual("in_memory_injected_writer", report["audit"]["mode"])
                self.assertFalse(report["audit"]["default_platform_writer_used"])
                self.assertIn("audit_logs persistence", report["audit"]["does_not_prove"])
                for category in EVIDENCE_CATEGORIES:
                    record = module["evidence"][category]
                    self.assertEqual("pass", record["status"])
                    self.assertEqual(sha256_file(paths[module_id]), record["sha256"])

    def test_real_metrics_artifacts_provenance_and_replay_are_bounded(self):
        paths = output_paths(ROOT, Path("quality/maturity/evidence/model_factory_experiments"))
        for module_id, (kind, _config) in MODULES.items():
            with self.subTest(kind=kind):
                report = json.loads(paths[module_id].read_text(encoding="utf-8"))
                experiment = report["experiment"]
                first, repeated = experiment["first_run"], experiment["repeat_run"]
                self.assertEqual("completed", first["status"])
                self.assertEqual(first["metrics"], repeated["metrics"])
                self.assertTrue(experiment["metrics_reproducible"])
                self.assertEqual("deterministic_fixture", first["data_source"]["type"])
                self.assertEqual(64, len(first["artifact"]["sha256"]))
                self.assertGreater(first["artifact"]["size_bytes"], 0)
                self.assertEqual(1, first["registered_model_count"])
                self.assertEqual(1, first["registered_version_count"])
                self.assertEqual(
                    [("create", "success"), ("execute", "completed")],
                    [(event["action"], event["outcome"]) for event in first["audit_events"]],
                )
                self.assertEqual(
                    {"actor_id", "organization_id", "job_id", "kind", "action", "outcome"},
                    set(first["audit_events"][0]),
                )
                for value in first["metrics"].values():
                    self.assertIsInstance(value, (int, float))
                    self.assertFalse(isinstance(value, bool))
                    self.assertTrue(math.isfinite(float(value)))

    def test_required_negative_paths_are_explicit_and_pass(self):
        required = {
            "reject-missing-explicit-fixture",
            "reject-cross-tenant-read",
            "reject-unknown-experiment-node",
            "beta-evidence-rejects-missing-artifact",
            "structured-audit-cancel-lifecycle",
            "structured-audit-failed-lifecycle",
        }
        paths = output_paths(ROOT, Path("quality/maturity/evidence/model_factory_experiments"))
        for module_id in MODULES:
            report = json.loads(paths[module_id].read_text(encoding="utf-8"))
            by_id = {item["id"]: item for item in report["negative_paths"]}
            self.assertTrue(required.issubset(by_id))
            self.assertTrue(all(by_id[item]["passed"] for item in required))
            no_artifact = by_id["beta-evidence-rejects-missing-artifact"]
            self.assertEqual("completed", no_artifact["runtime_status"])
            self.assertFalse(no_artifact["evidence_eligible"])
            for check_id in ("structured-audit-cancel-lifecycle", "structured-audit-failed-lifecycle"):
                self.assertEqual(2, by_id[check_id]["event_count"])
                self.assertEqual(
                    {"actor_id", "organization_id", "job_id", "kind", "action", "outcome"},
                    set(by_id[check_id]["events"][0]),
                )

    def test_partial_evidence_never_promotes_and_beta_gate_stays_closed(self):
        modules = {item["id"]: item for item in INVENTORY["modules"]}
        for module_id in MODULES:
            result = MaturityGate(POLICY, INVENTORY, ROOT).check(modules[module_id], "beta")
            self.assertFalse(result.passed)
            self.assertEqual("demo", result.qualified_level)
            blockers = {item.split(":", 1)[0] for item in result.blockers}
            self.assertEqual(
                {"persistence", "rbac_tenant", "observability", "e2e"},
                blockers,
            )

    def test_offline_validation_never_calls_default_platform_audit_writer(self):
        with patch(
            "services.experiment_audit.emit_experiment_audit",
            side_effect=AssertionError("default audit writer must not be used offline"),
        ) as default_writer:
            reports = run_offline_validation(repo_root=ROOT)
        default_writer.assert_not_called()
        for report in reports.values():
            self.assertIn("audit", report["evidence_categories"])
            self.assertNotIn("observability", report["evidence_categories"])
            self.assertTrue(report["passed"])

    def test_inventory_import_is_opt_in_and_does_not_change_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            inventory_path = repo / "quality/maturity/modules.json"
            isolated = copy.deepcopy(INVENTORY)
            for module in isolated["modules"]:
                if module["id"] in MODULES:
                    module["evidence"] = {}
            atomic_json(inventory_path, isolated)
            reports = run_offline_validation(repo_root=repo)
            paths = output_paths(repo, Path("quality/maturity/evidence/model_factory_experiments"))
            for module_id, report in reports.items():
                atomic_json(paths[module_id], report)
            update_inventory(
                repo_root=repo,
                inventory_path=inventory_path,
                paths=paths,
                reports=reports,
            )
            updated = load_json(inventory_path)
            modules = {item["id"]: item for item in updated["modules"]}
            for module_id in MODULES:
                self.assertEqual("demo", modules[module_id]["observed_status"])
                self.assertEqual("beta", modules[module_id]["target_status"])
                self.assertEqual(set(EVIDENCE_CATEGORIES), set(modules[module_id]["evidence"]))
                gaps = modules[module_id]["known_gaps"]
                self.assertTrue(any("audit_logs在线落库" in gap for gap in gaps))
                self.assertIn("缺少多进程可观测性和登录态E2E证据", gaps)


if __name__ == "__main__":
    unittest.main()
