"""
Tests for Blind Benchmark Holdout system.

Tests cover:
  1. Crypto: seal/unseal roundtrip, tamper detection, key separation
  2. Sealer: dataset sealing, manifest generation, integrity verification
  3. Submission: prediction submission, hash verification, listing
  4. Adjudicator: scoring, pass/fail determination, report generation
  5. Adversarial: fixture injection, FAIL detection, test suite
  6. Backfill: guide writing, provenance, querying
  7. Integration: full pipeline from seal → submit → adjudicate → backfill
"""

import json
import tempfile
from pathlib import Path

import pytest

from backend.services.blind_benchmark.crypto import (
    seal_data,
    unseal_data,
    generate_master_key,
    SealedEnvelope,
)
from backend.services.blind_benchmark.sealer import DatasetSealer
from backend.services.blind_benchmark.submission import PredictionSubmitter
from backend.services.blind_benchmark.adjudicator import BlindAdjudicator
from backend.services.blind_benchmark.adversarial import (
    inject_adversarial,
    run_adversarial_suite,
)
from backend.services.blind_benchmark.backfill import BenchmarkBackfill


# ============================================================================
# Crypto Tests
# ============================================================================


class TestCrypto:
    """Test authenticated encryption."""

    def test_seal_unseal_roundtrip(self):
        """Seal and unseal should recover original plaintext."""
        key = generate_master_key()
        plaintext = b"Hello, blind benchmark!"

        envelope = seal_data(plaintext, key)
        recovered = unseal_data(envelope, key)

        assert recovered == plaintext

    def test_seal_unseal_json(self):
        """Test with JSON data (typical benchmark dataset)."""
        key = generate_master_key()
        dataset = {"cases": {"case_01": {"Ct": 0.0035}, "case_02": {"Ct": 0.0042}}}
        plaintext = json.dumps(dataset).encode("utf-8")

        envelope = seal_data(plaintext, key)
        recovered = unseal_data(envelope, key)

        assert json.loads(recovered.decode("utf-8")) == dataset

    def test_wrong_key_fails(self):
        """Unsealing with wrong key should fail."""
        key1 = generate_master_key()
        key2 = generate_master_key()
        plaintext = b"Secret data"

        envelope = seal_data(plaintext, key1)

        with pytest.raises(ValueError, match="MAC verification failed"):
            unseal_data(envelope, key2)

    def test_tamper_detection(self):
        """Tampering with ciphertext should be detected."""
        key = generate_master_key()
        plaintext = b"Important data"

        envelope = seal_data(plaintext, key)

        # Tamper with ciphertext
        tampered_ciphertext = bytearray(envelope.ciphertext)
        tampered_ciphertext[0] ^= 0xFF
        tampered_envelope = SealedEnvelope(
            nonce=envelope.nonce,
            ciphertext=bytes(tampered_ciphertext),
            tag=envelope.tag,
            metadata=envelope.metadata,
        )

        with pytest.raises(ValueError, match="MAC verification failed"):
            unseal_data(tampered_envelope, key)

    def test_metadata_preserved(self):
        """Metadata should be preserved through seal/unseal."""
        key = generate_master_key()
        plaintext = b"Test data"
        metadata = {"source": "test", "version": "1.0"}

        envelope = seal_data(plaintext, key, metadata=metadata)
        assert envelope.metadata["source"] == "test"
        assert envelope.metadata["version"] == "1.0"
        assert "plaintext_sha256" in envelope.metadata

    def test_key_too_short(self):
        """Key shorter than 32 bytes should be rejected."""
        short_key = b"too_short"
        plaintext = b"Test"

        with pytest.raises(ValueError, match="at least 32 bytes"):
            seal_data(plaintext, short_key)


# ============================================================================
# Sealer Tests
# ============================================================================


class TestSealer:
    """Test dataset sealing."""

    def test_seal_dataset(self):
        """Seal a dataset and verify manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir)
            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}

            manifest = sealer.seal_dataset(
                dataset=dataset,
                dataset_name="test_benchmark",
                metadata={"source": "test"},
            )

            assert manifest.dataset_name == "test_benchmark"
            assert manifest.plaintext_size > 0
            assert manifest.sealed_size > manifest.plaintext_size
            assert manifest.encryption_algorithm == "SHA256-CTR-HMAC-SHA256"

            # Check files exist
            assert (Path(tmpdir) / f"{manifest.dataset_id}.sealed").exists()
            assert (Path(tmpdir) / f"{manifest.dataset_id}.manifest.json").exists()

    def test_load_sealed(self):
        """Load a sealed dataset."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir)
            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}

            manifest = sealer.seal_dataset(dataset, "test")
            envelope, loaded_manifest = sealer.load_sealed(manifest.dataset_id)

            assert loaded_manifest.dataset_id == manifest.dataset_id
            assert envelope.metadata["plaintext_sha256"] == manifest.plaintext_sha256

    def test_verify_seal_integrity(self):
        """Verify seal integrity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir)
            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}

            manifest = sealer.seal_dataset(dataset, "test")
            assert sealer.verify_seal_integrity(manifest.dataset_id)


# ============================================================================
# Submission Tests
# ============================================================================


class TestSubmission:
    """Test prediction submission."""

    def test_submit_prediction(self):
        """Submit a prediction and verify artifact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            submitter = PredictionSubmitter(tmpdir)

            artifact = submitter.submit(
                dataset_id="abc123",
                solver_name="test_solver",
                solver_version="v1.0",
                predictions={"case_01": {"Ct": 0.0035}},
                metadata={"description": "test"},
            )

            assert artifact.solver_name == "test_solver"
            assert artifact.predictions_hash
            assert artifact.submit_timestamp > 0

            # Check file exists
            assert (Path(tmpdir) / f"{artifact.submission_id}.submission.json").exists()

    def test_load_submission(self):
        """Load a submission."""
        with tempfile.TemporaryDirectory() as tmpdir:
            submitter = PredictionSubmitter(tmpdir)

            artifact = submitter.submit(
                dataset_id="abc123",
                solver_name="test_solver",
                solver_version="v1.0",
                predictions={"case_01": {"Ct": 0.0035}},
            )

            loaded = submitter.load_submission(artifact.submission_id)
            assert loaded.submission_id == artifact.submission_id

    def test_list_submissions(self):
        """List submissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            submitter = PredictionSubmitter(tmpdir)

            submitter.submit("ds1", "solver1", "v1", {"c1": {"x": 1}})
            submitter.submit("ds1", "solver2", "v1", {"c1": {"x": 2}})
            submitter.submit("ds2", "solver1", "v1", {"c1": {"x": 3}})

            all_subs = submitter.list_submissions()
            assert len(all_subs) == 3

            ds1_subs = submitter.list_submissions(dataset_id="ds1")
            assert len(ds1_subs) == 2

    def test_verify_submission_integrity(self):
        """Verify submission integrity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            submitter = PredictionSubmitter(tmpdir)

            artifact = submitter.submit(
                dataset_id="abc123",
                solver_name="test_solver",
                solver_version="v1.0",
                predictions={"case_01": {"Ct": 0.0035}},
            )

            assert submitter.verify_submission_integrity(artifact.submission_id)


# ============================================================================
# Adjudicator Tests
# ============================================================================


class TestAdjudicator:
    """Test blind adjudication."""

    def _setup_pipeline(self, tmpdir):
        """Setup a complete pipeline for testing."""
        sealer = DatasetSealer(tmpdir + "/sealed")
        submitter = PredictionSubmitter(tmpdir + "/submissions")
        adjudicator = BlindAdjudicator(sealer, submitter, tmpdir + "/reports")
        return sealer, submitter, adjudicator

    def test_adjudicate_pass(self):
        """Adjudicate a passing submission."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer, submitter, adjudicator = self._setup_pipeline(tmpdir)

            # Seal dataset
            dataset = {
                "cases": {
                    "case_01": {"Ct": 0.0035},
                    "case_02": {"Ct": 0.0042},
                }
            }
            manifest = sealer.seal_dataset(dataset, "test_benchmark")

            # Submit predictions (within 15% threshold)
            artifact = submitter.submit(
                dataset_id=manifest.dataset_id,
                solver_name="test_solver",
                solver_version="v1.0",
                predictions={
                    "case_01": {"Ct": 0.0036},  # 2.8% error
                    "case_02": {"Ct": 0.0043},  # 2.4% error
                },
            )

            # Adjudicate
            report = adjudicator.adjudicate(artifact.submission_id)

            assert report.overall_passed
            assert report.overall_summary["passed_cases"] == 2
            assert report.overall_summary["failed_cases"] == 0

    def test_adjudicate_fail(self):
        """Adjudicate a failing submission."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer, submitter, adjudicator = self._setup_pipeline(tmpdir)

            # Seal dataset
            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}
            manifest = sealer.seal_dataset(dataset, "test_benchmark")

            # Submit predictions (exceeds 15% threshold)
            artifact = submitter.submit(
                dataset_id=manifest.dataset_id,
                solver_name="test_solver",
                solver_version="v1.0",
                predictions={"case_01": {"Ct": 0.0050}},  # 42.9% error
            )

            # Adjudicate
            report = adjudicator.adjudicate(artifact.submission_id)

            assert not report.overall_passed
            assert report.overall_summary["failed_cases"] == 1

    def test_adjudicate_missing_case(self):
        """Adjudicate with missing case in ground truth."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer, submitter, adjudicator = self._setup_pipeline(tmpdir)

            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}
            manifest = sealer.seal_dataset(dataset, "test_benchmark")

            artifact = submitter.submit(
                dataset_id=manifest.dataset_id,
                solver_name="test_solver",
                solver_version="v1.0",
                predictions={
                    "case_01": {"Ct": 0.0035},
                    "case_99": {"Ct": 0.0040},  # not in ground truth
                },
            )

            report = adjudicator.adjudicate(artifact.submission_id)

            # case_99 should fail
            case_99_score = next(cs for cs in report.case_scores if cs.case_id == "case_99")
            assert not case_99_score.passed
            assert "not found" in case_99_score.fail_reason


# ============================================================================
# Adversarial Tests
# ============================================================================


class TestAdversarial:
    """Test adversarial fixtures."""

    def test_gross_error_detected(self):
        """Gross error should be detected as FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir + "/sealed")
            submitter = PredictionSubmitter(tmpdir + "/submissions")
            adjudicator = BlindAdjudicator(sealer, submitter, tmpdir + "/reports")

            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}
            manifest = sealer.seal_dataset(dataset, "test")

            fixture = inject_adversarial(
                adjudicator, submitter, manifest.dataset_id, dataset,
                fixture_type="gross_error", seed=42
            )

            assert fixture.expected_fail
            assert fixture.actual_fail
            assert fixture.test_passed

    def test_perfect_prediction_passes(self):
        """Perfect prediction should PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir + "/sealed")
            submitter = PredictionSubmitter(tmpdir + "/submissions")
            adjudicator = BlindAdjudicator(sealer, submitter, tmpdir + "/reports")

            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}
            manifest = sealer.seal_dataset(dataset, "test")

            fixture = inject_adversarial(
                adjudicator, submitter, manifest.dataset_id, dataset,
                fixture_type="perfect_prediction", seed=44
            )

            assert not fixture.expected_fail
            assert not fixture.actual_fail
            assert fixture.test_passed

    def test_adversarial_suite(self):
        """Run full adversarial suite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir + "/sealed")
            submitter = PredictionSubmitter(tmpdir + "/submissions")
            adjudicator = BlindAdjudicator(sealer, submitter, tmpdir + "/reports")

            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}
            manifest = sealer.seal_dataset(dataset, "test")

            fixtures = run_adversarial_suite(
                adjudicator, submitter, manifest.dataset_id, dataset
            )

            assert len(fixtures) == 3
            assert all(f.test_passed for f in fixtures)


# ============================================================================
# Backfill Tests
# ============================================================================


class TestBackfill:
    """Test guide backfill."""

    def test_backfill_report(self):
        """Backfill a report to guide."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir + "/sealed")
            submitter = PredictionSubmitter(tmpdir + "/submissions")
            adjudicator = BlindAdjudicator(sealer, submitter, tmpdir + "/reports")
            backfill = BenchmarkBackfill(tmpdir + "/guide")

            dataset = {"cases": {"case_01": {"Ct": 0.0035}}}
            manifest = sealer.seal_dataset(dataset, "test")

            artifact = submitter.submit(
                manifest.dataset_id, "solver", "v1",
                {"case_01": {"Ct": 0.0036}}
            )

            report = adjudicator.adjudicate(artifact.submission_id)
            entries = backfill.backfill_report(report)

            assert len(entries) == 1
            assert entries[0].verdict == "PASS"
            assert entries[0].solver_name == "solver"

    def test_query_guide(self):
        """Query guide entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            backfill = BenchmarkBackfill(tmpdir)

            # Manually add entries
            guide = backfill._load_guide()
            guide["entries"].append({
                "case_id": "c1",
                "benchmark_name": "test",
                "solver_name": "solver1",
                "verdict": "PASS",
            })
            guide["entries"].append({
                "case_id": "c2",
                "benchmark_name": "test",
                "solver_name": "solver2",
                "verdict": "FAIL",
            })
            backfill._save_guide(guide)

            # Query
            pass_entries = backfill.query_entries(verdict="PASS")
            assert len(pass_entries) == 1

            solver1_entries = backfill.query_entries(solver_name="solver1")
            assert len(solver1_entries) == 1


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Full pipeline integration tests."""

    def test_full_pipeline(self):
        """Test complete pipeline: seal → submit → adjudicate → backfill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup
            sealer = DatasetSealer(tmpdir + "/sealed")
            submitter = PredictionSubmitter(tmpdir + "/submissions")
            adjudicator = BlindAdjudicator(sealer, submitter, tmpdir + "/reports")
            backfill = BenchmarkBackfill(tmpdir + "/guide")

            # 1. Seal dataset
            dataset = {
                "cases": {
                    "SUBOFF_01": {"Ct": 0.0035, "Rt": 92.52},
                    "SUBOFF_02": {"Ct": 0.0042, "Rt": 110.3},
                }
            }
            manifest = sealer.seal_dataset(
                dataset=dataset,
                dataset_name="SUBOFF_resistance",
                metadata={"source": "Roddy 1990"},
            )

            # 2. Submit predictions (good solver, within 15%)
            artifact = submitter.submit(
                dataset_id=manifest.dataset_id,
                solver_name="holtrop_method",
                solver_version="v1.0",
                predictions={
                    "SUBOFF_01": {"Ct": 0.0036, "Rt": 95.0},  # ~3% error
                    "SUBOFF_02": {"Ct": 0.0043, "Rt": 113.0},  # ~2% error
                },
                metadata={"description": "Holtrop-Mennen 1988"},
            )

            # 3. Adjudicate
            report = adjudicator.adjudicate(
                submission_id=artifact.submission_id,
                scoring_rules={"max_relative_error_pct": 15.0},
            )

            assert report.overall_passed
            assert report.solver_name == "holtrop_method"
            assert report.overall_summary["passed_cases"] == 2

            # 4. Backfill to guide
            entries = backfill.backfill_report(report)
            assert len(entries) == 2
            assert all(e.verdict == "PASS" for e in entries)

            # 5. Verify provenance
            guide = backfill.load_guide()
            assert len(guide["entries"]) == 2
            for entry in guide["entries"]:
                assert entry["dataset_hash"] == manifest.plaintext_sha256
                assert entry["submission_hash"] == artifact.predictions_hash
                assert entry["report_hash"]

    def test_blind_property(self):
        """Verify that solver cannot access ground truth without key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sealer = DatasetSealer(tmpdir + "/sealed")
            dataset = {"cases": {"case_01": {"secret_value": 12345}}}
            manifest = sealer.seal_dataset(dataset, "test")

            # Load sealed data (as solver would)
            sealed_path = Path(tmpdir + "/sealed") / f"{manifest.dataset_id}.sealed"
            sealed_bytes = sealed_path.read_bytes()

            # Solver cannot decrypt without key
            # (The sealed data is just bytes, not readable as JSON)
            with pytest.raises(Exception):
                json.loads(sealed_bytes.decode("utf-8"))

            # Only adjudicator with key can decrypt
            envelope, _ = sealer.load_sealed(manifest.dataset_id)
            from backend.services.blind_benchmark.crypto import unseal_data
            plaintext = unseal_data(envelope, sealer.master_key)
            recovered = json.loads(plaintext.decode("utf-8"))
            assert recovered["cases"]["case_01"]["secret_value"] == 12345
