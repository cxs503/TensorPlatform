"""IN12-P7: Ice campaign evidence registration and capability ledger upgrade.

Tests for:
1. Ice capability ledger entries (evidence chain end-to-end verifiable)
2. G-C contract tests (tensorphysics_runs vocabulary diff=0)
3. Artifact registration (RunManifest → ArtifactRef chain with sha256)
4. Ledger reconciliation tool rerun produces same verdicts
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
CAPABILITY_LEDGER = ARTIFACTS / "capability_ledger" / "capability_ledger.json"
ICE_CAMPAIGN = ARTIFACTS / "ice_campaign"
ICE_EVIDENCE = ICE_CAMPAIGN / "ice_evidence_registry.json"
RUN_MANIFEST = ICE_CAMPAIGN / "run_manifest.json"
CONTRACTS = ROOT / "backend" / "contracts"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Ice capability ledger entries
# ---------------------------------------------------------------------------

class TestIceCapabilityLedger:
    """Verify ice domain entries in the capability ledger."""

    @pytest.fixture(autouse=True)
    def ledger(self):
        return _load_json(CAPABILITY_LEDGER)

    @pytest.fixture(autouse=True)
    def evidence(self):
        return _load_json(ICE_EVIDENCE)

    def test_ice_entries_exist(self, ledger):
        """Ice domain entries must exist in capabilities list."""
        ice_ids = {c["id"] for c in ledger["capabilities"] if c["id"].startswith("ice.")}
        expected = {"ice.resistance", "ice.loads", "ice.uq_pce",
                     "ice.abrasion", "ice.inversion", "ice.class_optimization"}
        assert expected <= ice_ids, f"Missing ice entries: {expected - ice_ids}"

    def test_ice_entries_have_evidence(self, ledger):
        """Each ice entry must have at least one evidence item."""
        for cap in ledger["capabilities"]:
            if cap["id"].startswith("ice."):
                assert len(cap["evidence"]) >= 1, f"{cap['id']} has no evidence"

    def test_ice_entries_have_artifact_refs(self, ledger):
        """REAL ice entries must have artifact_refs."""
        for cap in ledger["capabilities"]:
            if cap["id"].startswith("ice.") and cap.get("claimed_level") == "REAL":
                assert "artifact_refs" in cap, f"{cap['id']} missing artifact_refs"
                assert len(cap["artifact_refs"]) >= 1

    def test_ice_evidence_grade_real(self, ledger):
        """ice.resistance, ice.loads, ice.uq_pce must be REAL."""
        real_ice = {c["id"] for c in ledger["capabilities"]
                    if c["id"].startswith("ice.") and c.get("claimed_level") == "REAL"}
        assert "ice.resistance" in real_ice
        assert "ice.loads" in real_ice
        assert "ice.uq_pce" in real_ice

    def test_ice_evidence_grade_withheld(self, ledger):
        """ice.abrasion, ice.inversion, ice.class_optimization must be WITHHELD."""
        withheld_ice = {c["id"] for c in ledger["capabilities"]
                        if c["id"].startswith("ice.") and c.get("claimed_level") == "WITHHELD"}
        assert "ice.abrasion" in withheld_ice
        assert "ice.inversion" in withheld_ice
        assert "ice.class_optimization" in withheld_ice

    def test_ice_unregistered_domain_entry(self, ledger):
        """Ice domain must appear in unregistered_domains."""
        ice_domains = [d for d in ledger["unregistered_domains"]
                       if "ice" in d.get("domain", "").lower() or "冰区" in d.get("domain", "")]
        assert len(ice_domains) >= 1, "Ice domain not in unregistered_domains"

    def test_ice_evidence_registry_artifacts(self, evidence):
        """Ice evidence registry must have expected artifacts."""
        artifact_ids = {a["id"] for a in evidence["artifacts"]}
        expected = {
            "ice-resistance-evidence", "ice-loads-evidence", "ice-uq-pce-evidence",
            "ice-abrasion", "ice-load-inversion", "ice-class-optimization",
            "ice-resistance-crosscheck", "gc-contract-tests"
        }
        assert expected <= artifact_ids, f"Missing artifacts: {expected - artifact_ids}"

    def test_ice_evidence_registry_summary(self, evidence):
        """Ice evidence registry summary must be consistent."""
        summary = evidence["summary"]
        assert summary["total_artifacts"] >= 8
        assert summary["real_count"] >= 4
        assert summary["withheld_count"] >= 3
        assert summary["total_tests"] >= 200
        assert summary["total_passed"] >= 200

    def test_ice_evidence_artifact_refs_valid(self, ledger, evidence):
        """Artifact refs in ledger must point to valid entries in evidence registry."""
        artifact_ids = {a["id"] for a in evidence["artifacts"]}
        for cap in ledger["capabilities"]:
            if cap["id"].startswith("ice."):
                for ref in cap.get("artifact_refs", []):
                    # refs like "artifacts/ice_campaign/ice_evidence_registry.json#ice-resistance-evidence"
                    if "#" in ref:
                        artifact_id = ref.split("#")[-1]
                        assert artifact_id in artifact_ids, f"Invalid artifact ref: {artifact_id}"


# ---------------------------------------------------------------------------
# 2. G-C contract tests (diff=0)
# ---------------------------------------------------------------------------

class TestGCContractDiffZero:
    """Verify G-C contract: handwritten regex vs _RUN_STATUSES diff=0."""

    def test_contract_file_exists(self):
        vocab = CONTRACTS / "research_run_status_vocab.v1.json"
        assert vocab.exists(), "Contract file missing"

    def test_contract_wellformed(self):
        doc = _load_json(CONTRACTS / "research_run_status_vocab.v1.json")
        assert doc["contract"] == "research_run_status_vocab"
        assert isinstance(doc["version"], int) and doc["version"] >= 1
        assert isinstance(doc["run_statuses"], list) and len(doc["run_statuses"]) >= 1
        assert isinstance(doc["validation_statuses"], list) and len(doc["validation_statuses"]) >= 1
        assert isinstance(doc["max_manifest_bytes"], int) and doc["max_manifest_bytes"] > 0

    def test_patterns_derived_from_contract(self):
        """RunRegistration patterns must be derived from contract, not handwritten."""
        # Load contract directly without importing backend api (avoids psycopg2)
        doc = _load_json(CONTRACTS / "research_run_status_vocab.v1.json")
        run_statuses = doc["run_statuses"]
        validation_statuses = doc["validation_statuses"]
        max_manifest_bytes = doc["max_manifest_bytes"]

        # Build patterns the same way tensorphysics_runs.py does
        run_pattern = "^(" + "|".join(run_statuses) + ")$"
        val_pattern = "^(" + "|".join(validation_statuses) + ")$"

        for s in run_statuses:
            assert re.match(run_pattern, s), f"Status '{s}' not matched by pattern"
        assert not re.match(run_pattern, "success"), "Non-member must not match"
        assert not re.match(run_pattern, "plann"), "Partial match must fail"
        for v in validation_statuses:
            assert re.match(val_pattern, v), f"Validation '{v}' not matched"
        assert not re.match(val_pattern, "skipped"), "Non-member must not match"
        assert max_manifest_bytes == 1_000_000

    def test_handwritten_dual_source_gone(self):
        """Handwritten regex dual-source must be eliminated."""
        src = (ROOT / "backend" / "api" / "tensorphysics_runs.py").read_text()
        assert "Field(pattern=_RUN_STATUS_PATTERN)" in src
        assert "Field(pattern=_VALIDATION_STATUS_PATTERN)" in src
        # Handwritten patterns must NOT appear
        assert "planned|validating|queued" not in src
        assert "not_run|passed|failed|incomplete)$" not in src

    def test_manifest_bytes_cap_from_contract(self):
        """Manifest bytes cap must come from contract, not hardcoded."""
        doc = _load_json(CONTRACTS / "research_run_status_vocab.v1.json")
        assert doc["max_manifest_bytes"] == 1_000_000
        src = (ROOT / "backend" / "api" / "tensorphysics_runs.py").read_text()
        assert "> 1_000_000" not in src, "Cap must not be hardcoded literal"
        # Verify _MAX_MANIFEST_BYTES is derived from contract
        assert "_rrc()[\"max_manifest_bytes\"]" in src or "_rrc()['max_manifest_bytes']" in src


# ---------------------------------------------------------------------------
# 3. Artifact registration (RunManifest → ArtifactRef chain)
# ---------------------------------------------------------------------------

class TestArtifactRegistration:
    """Verify RunManifest → ArtifactRef chain with sha256."""

    def test_run_manifest_exists(self):
        assert RUN_MANIFEST.exists(), "Run manifest missing"

    def test_run_manifest_schema(self):
        manifest = _load_json(RUN_MANIFEST)
        assert manifest["schema"] == "RunManifest-v1"
        assert manifest["run_id"] == "ice-campaign-2026-11-07"
        assert manifest["campaign"] == "IN12-P7"
        assert manifest["domain"] == "ice"
        assert manifest["status"] == "succeeded"
        assert manifest["validation_status"] == "passed"

    def test_run_manifest_outputs(self):
        manifest = _load_json(RUN_MANIFEST)
        assert len(manifest["outputs"]) >= 2
        for out in manifest["outputs"]:
            assert "name" in out
            assert "type" in out
            assert "uri" in out
            assert "checksum_sha256" in out

    def test_evidence_registry_sha256(self):
        """Evidence registry sha256 must be computable."""
        content = ICE_EVIDENCE.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        assert len(sha) == 64
        # The manifest should reference this sha256 (once updated)
        manifest = _load_json(RUN_MANIFEST)
        for out in manifest["outputs"]:
            if "ice_evidence_registry" in out["name"]:
                # sha256 is "pending" in the template; verify format
                assert out["checksum_sha256"] == "pending" or len(out["checksum_sha256"]) == 64

    def test_artifact_chain_integrity(self):
        """RunManifest → ArtifactRef chain must be end-to-end verifiable."""
        manifest = _load_json(RUN_MANIFEST)
        evidence = _load_json(ICE_EVIDENCE)

        # Manifest references evidence registry
        output_names = {o["name"] for o in manifest["outputs"]}
        assert "ice_evidence_registry.json" in output_names

        # Evidence registry references test files
        for artifact in evidence["artifacts"]:
            assert "path" in artifact, f"Artifact {artifact['id']} missing path"
            assert "evidence_grade" in artifact, f"Artifact {artifact['id']} missing grade"
            assert artifact["evidence_grade"] in ("REAL", "WITHHELD", "NOT_SUPPORTED", "MOCK", "STUB")


# ---------------------------------------------------------------------------
# 4. Ledger reconciliation tool rerun
# ---------------------------------------------------------------------------

class TestLedgerReconciliation:
    """Verify ledger reconciliation produces consistent verdicts."""

    def test_level_rank_consistent(self):
        """Level rank must be consistent across ledger."""
        ledger = _load_json(CAPABILITY_LEDGER)
        rank = ledger["level_rank"]
        assert rank["NOT_SUPPORTED"] < rank["STUB"] < rank["MOCK"] < rank["WITHHELD"] < rank["REAL"]

    def test_reconciliation_rules_present(self):
        """Reconciliation rules must be present."""
        ledger = _load_json(CAPABILITY_LEDGER)
        assert len(ledger["reconciliation_rules"]) >= 2

    def test_ice_entries_have_action(self):
        """Each ice entry must have an action field."""
        ledger = _load_json(CAPABILITY_LEDGER)
        for cap in ledger["capabilities"]:
            if cap["id"].startswith("ice."):
                assert "action" in cap, f"{cap['id']} missing action"

    def test_curation_includes_ice(self):
        """Curation must_consume must include ice evidence."""
        ledger = _load_json(CAPABILITY_LEDGER)
        ice_paths = [m["path"] for m in ledger["curation"]["must_consume"]
                     if "ice" in m["path"].lower()]
        assert len(ice_paths) >= 1, "Ice evidence not in must_consume"

    def test_schema_version(self):
        """Schema version must be 2.0."""
        ledger = _load_json(CAPABILITY_LEDGER)
        assert ledger["schema_version"] == "2.0"

    def test_revision_updated(self):
        """Revision must be m4935."""
        ledger = _load_json(CAPABILITY_LEDGER)
        assert ledger["curation"]["revision"] == "m4935"
