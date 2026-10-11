from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.maturity_gate import ConfigurationError, MaturityGate, load_json, sha256_file


ROOT = Path(__file__).resolve().parents[1]
POLICY = load_json(ROOT / "quality/maturity/policy.json")


def module_record(observed: str = "beta", target: str = "beta") -> dict:
    return {
        "id": "sample_module",
        "name": "Sample module",
        "owner": "test",
        "frontend_label": "beta",
        "observed_status": observed,
        "target_status": target,
        "basis": "Unit-test fixture",
        "known_gaps": [],
        "evidence": {},
    }


def inventory(module: dict) -> dict:
    return {"schema_version": 1, "modules": [module]}


class MaturityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.artifact = self.root / "evidence" / "result.json"
        self.artifact.parent.mkdir()
        report = {
            "schema_version": 1,
            "module_id": "sample_module",
            "evidence_categories": POLICY["levels"]["beta"]["required_evidence"],
            "passed": True,
            "checks": [{"id": "integrated-check", "passed": True}],
            "environment": "isolated-unit-test",
            "data_classification": "synthetic",
            "started_at": "2026-07-18T00:00:00Z",
            "completed_at": "2026-07-18T00:01:00Z",
        }
        self.artifact.write_text(json.dumps(report) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def passing_record(self, **overrides: str) -> dict:
        record = {
            "status": "pass",
            "artifact_type": "test_report",
            "artifact": "evidence/result.json",
            "sha256": sha256_file(self.artifact),
            "observed_at": "2026-07-18T00:00:00Z",
            "summary": "The declared check passed.",
        }
        record.update(overrides)
        return record

    def test_repository_inventory_is_structurally_valid(self) -> None:
        gate = MaturityGate(
            POLICY,
            load_json(ROOT / "quality/maturity/modules.json"),
            ROOT,
        )
        self.assertGreaterEqual(len(gate.modules), 10)

    def test_missing_required_evidence_fails_closed(self) -> None:
        module = module_record()
        result = MaturityGate(POLICY, inventory(module), self.root).check(module, "beta")
        self.assertFalse(result.passed)
        self.assertEqual(result.qualified_level, "demo")
        self.assertTrue(any(item.startswith("real_service:") for item in result.blockers))

    def test_complete_beta_evidence_passes(self) -> None:
        module = module_record()
        module["evidence"] = {
            category: self.passing_record()
            for category in POLICY["levels"]["beta"]["required_evidence"]
        }
        result = MaturityGate(POLICY, inventory(module), self.root).check(module, "beta")
        self.assertTrue(result.passed)
        self.assertEqual(result.qualified_level, "beta")

    def test_checksum_tampering_fails(self) -> None:
        module = module_record()
        module["evidence"] = {
            category: self.passing_record()
            for category in POLICY["levels"]["beta"]["required_evidence"]
        }
        gate = MaturityGate(POLICY, inventory(module), self.root)
        self.artifact.write_text('{"passed": false}\n', encoding="utf-8")
        result = gate.check(module, "beta")
        self.assertFalse(result.passed)
        self.assertTrue(all("checksum mismatch" in item for item in result.blockers))

    def test_artifact_content_must_match_module(self) -> None:
        module = module_record()
        module["evidence"] = {
            category: self.passing_record()
            for category in POLICY["levels"]["beta"]["required_evidence"]
        }
        report = json.loads(self.artifact.read_text(encoding="utf-8"))
        report["module_id"] = "another_module"
        self.artifact.write_text(json.dumps(report) + "\n", encoding="utf-8")
        digest = sha256_file(self.artifact)
        for record in module["evidence"].values():
            record["sha256"] = digest
        result = MaturityGate(POLICY, inventory(module), self.root).check(module, "beta")
        self.assertFalse(result.passed)
        self.assertTrue(all("artifact.module_id" in item for item in result.blockers))

    def test_failed_check_inside_report_fails(self) -> None:
        module = module_record()
        module["evidence"] = {
            category: self.passing_record()
            for category in POLICY["levels"]["beta"]["required_evidence"]
        }
        report = json.loads(self.artifact.read_text(encoding="utf-8"))
        report["checks"][0]["passed"] = False
        self.artifact.write_text(json.dumps(report) + "\n", encoding="utf-8")
        digest = sha256_file(self.artifact)
        for record in module["evidence"].values():
            record["sha256"] = digest
        result = MaturityGate(POLICY, inventory(module), self.root).check(module, "beta")
        self.assertFalse(result.passed)
        self.assertTrue(all("did not pass" in item for item in result.blockers))

    def test_expired_evidence_fails(self) -> None:
        module = module_record()
        module["evidence"] = {
            category: self.passing_record(valid_until="2020-01-01T00:00:00Z")
            for category in POLICY["levels"]["beta"]["required_evidence"]
        }
        result = MaturityGate(POLICY, inventory(module), self.root).check(module, "beta")
        self.assertFalse(result.passed)
        self.assertTrue(all("evidence expired" in item for item in result.blockers))

    def test_artifact_must_stay_inside_repository(self) -> None:
        module = module_record()
        module["evidence"] = {
            category: self.passing_record(artifact="../result.json")
            for category in POLICY["levels"]["beta"]["required_evidence"]
        }
        result = MaturityGate(POLICY, inventory(module), self.root).check(module, "beta")
        self.assertFalse(result.passed)
        self.assertTrue(all("outside repository" in item for item in result.blockers))

    def test_unknown_selected_module_is_configuration_error(self) -> None:
        gate = MaturityGate(POLICY, inventory(module_record()), self.root)
        with self.assertRaisesRegex(ConfigurationError, "unknown module"):
            gate.check_all("target", ["does_not_exist"])

    def test_invalid_pass_record_is_rejected_during_lint(self) -> None:
        module = module_record()
        module["evidence"] = {"real_service": {"status": "pass"}}
        with self.assertRaisesRegex(ConfigurationError, "missing pass fields"):
            MaturityGate(copy.deepcopy(POLICY), inventory(module), self.root)

    def test_json_policy_round_trip_is_stable(self) -> None:
        encoded = json.dumps(POLICY, ensure_ascii=False, sort_keys=True)
        self.assertEqual(json.loads(encoded), POLICY)


if __name__ == "__main__":
    unittest.main()
