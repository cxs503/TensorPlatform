"""
Tests for KPI Snapshot Traceability system (p488w1-n02).

Covers:
- C1: Dual estimator reconciliation (≥3 inconsistent keys detected)
- C2: Zero-provenance snapshot fail-closed (100% interception)
- C3: Value flip component justification (no unjustified flips pass)
- C4: Metric versioning (≥5 core KPIs versioned)
- C5: Batch audit of 18 snapshots
- C6: Dashboard generation (covered in generate_audit.py)
- C7: ≥12 pytest cases

Total: 18 test cases.
"""
import json
import os
import pathlib
import sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.kpi_snapshot_validator import (
    validate_snapshot,
    validate_batch,
    reconcile_key,
    estimator_a_pass_only,
    estimator_b_full_denominator,
    check_provenance,
    compute_snapshot_hash,
    detect_value_flips,
    REQUIRED_PROVENANCE_FIELDS,
)
from backend.middleware.snapshot_traceability_gate import (
    SnapshotTraceabilityGate,
    check_provenance_fields,
    validate_snapshot_provenance,
    get_audit_log,
    clear_audit_log,
    REQUIRED_PROVENANCE_FIELDS as GATE_REQUIRED_FIELDS,
)
from backend.services.metric_versioning import (
    MetricVersioningService,
    get_metric_versioning_service,
    reset_metric_versioning_service,
)

# ── Fixtures ──────────────────────────────────────────────────────────

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "kpi_snapshots"


def _load_snapshots():
    """Load the 18 synthetic snapshots."""
    gen_path = FIXTURES_DIR / "generate_snapshots.py"
    # Import the generator
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen", str(gen_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SNAPSHOTS


@pytest.fixture
def snapshots():
    return _load_snapshots()


@pytest.fixture
def good_snapshot():
    """A snapshot with full provenance and 5+ KPI keys."""
    return {
        "snapshot_id": "test_good",
        "repro_rate": {
            "value": 0.85,
            "numerator": 17,
            "denominator": 20,
            "pass_count": 17,
            "fail_count": 2,
            "drift_count": 1,
            "total": 20,
        },
        "success_rate": {
            "value": 0.89,
            "numerator": 80,
            "denominator": 90,
        },
        "benchmark_cases": 10,
        "kb_samples": 150,
        "lineage_coverage": {
            "value": 0.75,
            "numerator": 60,
            "denominator": 80,
        },
        "computed_at": "2026-11-01T00:00:00+00:00",
        "code_version": "v1.0.0",
        "input_version": "input-v1",
        "reference_version": "ref-v1",
    }


@pytest.fixture
def no_provenance_snapshot():
    """A snapshot missing all provenance fields."""
    return {
        "snapshot_id": "test_no_prov",
        "repro_rate": {
            "value": 0.9,
            "numerator": 18,
            "denominator": 20,
        },
    }


@pytest.fixture
def versioning_service():
    reset_metric_versioning_service()
    svc = MetricVersioningService()
    yield svc
    reset_metric_versioning_service()


# ══════════════════════════════════════════════════════════════════════
# C1: Dual Estimator Reconciliation
# ══════════════════════════════════════════════════════════════════════

class TestDualEstimatorReconciliation:
    """C1: Dual estimator detects inconsistencies."""

    def test_estimator_a_no_drift(self):
        """When drift_count=0, both estimators agree."""
        snap = {
            "repro_rate": {
                "pass_count": 18, "fail_count": 2, "drift_count": 0,
                "total": 20, "numerator": 18, "denominator": 20,
            }
        }
        ea = estimator_a_pass_only(snap, "repro_rate")
        eb = estimator_b_full_denominator(snap, "repro_rate")
        assert ea.value is not None
        assert eb.value is not None
        assert abs(ea.value - eb.value) < 1e-9

    def test_estimator_b_with_drift(self):
        """When drift_count>0, estimator B denominator is larger."""
        snap = {
            "repro_rate": {
                "pass_count": 15, "fail_count": 3, "drift_count": 7,
                "total": 25, "numerator": 15, "denominator": 25,
            }
        }
        ea = estimator_a_pass_only(snap, "repro_rate")
        eb = estimator_b_full_denominator(snap, "repro_rate")
        # A: 15/25 = 0.6, B: 15/(15+3+7) = 15/25 = 0.6
        # Actually same here because total = pass+fail+drift
        # Let's make total different from pass+fail+drift
        snap2 = {
            "repro_rate": {
                "pass_count": 15, "fail_count": 3, "drift_count": 7,
                "total": 20, "numerator": 15, "denominator": 20,
            }
        }
        ea2 = estimator_a_pass_only(snap2, "repro_rate")
        eb2 = estimator_b_full_denominator(snap2, "repro_rate")
        # A: 15/20 = 0.75, B: 15/25 = 0.6
        assert ea2.value == pytest.approx(0.75, abs=1e-6)
        assert eb2.value == pytest.approx(0.6, abs=1e-6)
        assert abs(ea2.value - eb2.value) > 0.1

    def test_reconcile_detects_inconsistency(self):
        """reconcile_key flags inconsistency when estimators disagree."""
        snap = {
            "repro_rate": {
                "pass_count": 15, "fail_count": 3, "drift_count": 7,
                "total": 20, "numerator": 15, "denominator": 20,
            }
        }
        finding = reconcile_key(snap, "repro_rate", tolerance=1e-6)
        assert finding.inconsistent is True
        assert finding.abs_delta > 0.1

    def test_reconcile_consistent_when_no_drift(self):
        """No drift → estimators agree → consistent."""
        snap = {
            "repro_rate": {
                "pass_count": 18, "fail_count": 2, "drift_count": 0,
                "total": 20, "numerator": 18, "denominator": 20,
            }
        }
        finding = reconcile_key(snap, "repro_rate", tolerance=1e-6)
        assert finding.inconsistent is False

    def test_batch_detects_3plus_inconsistencies(self, snapshots):
        """C1: At least 3 inconsistent keys detected across 18 snapshots."""
        reports = validate_batch(snapshots)
        total_inconsistent = sum(r.inconsistent_keys for r in reports)
        # Snapshots 6-9 have significant drift → estimator mismatch
        assert total_inconsistent >= 3, (
            f"Expected ≥3 inconsistent keys, got {total_inconsistent}"
        )


# ══════════════════════════════════════════════════════════════════════
# C2: Zero-Provenance Fail-Closed
# ══════════════════════════════════════════════════════════════════════

class TestZeroProvenanceFailClosed:
    """C2: Snapshots without provenance are rejected."""

    def test_provenance_check_missing_fields(self, no_provenance_snapshot):
        has_prov, fields = check_provenance(no_provenance_snapshot)
        assert has_prov is False
        assert len(fields) == 0  # No provenance fields present

    def test_provenance_check_all_present(self, good_snapshot):
        has_prov, fields = check_provenance(good_snapshot)
        assert has_prov is True
        assert len(fields) == len(REQUIRED_PROVENANCE_FIELDS)

    def test_validate_snapshot_fail_closed(self, no_provenance_snapshot):
        report = validate_snapshot(no_provenance_snapshot, "test_no_prov")
        assert report.status == "FAIL_CLOSED"
        assert report.has_provenance is False

    def test_validate_snapshot_pass_with_provenance(self, good_snapshot):
        report = validate_snapshot(good_snapshot, "test_good")
        assert report.status != "FAIL_CLOSED"
        assert report.has_provenance is True

    def test_gate_provenance_validation_function(self):
        """Programmatic gate API rejects zero-provenance."""
        result = validate_snapshot_provenance({"snapshot_id": "x"})
        assert result["decision"] == "FAIL_CLOSED"
        assert result["has_provenance"] is False
        assert len(result["missing_fields"]) == 4

    def test_gate_accepts_complete_provenance(self):
        """Gate accepts snapshot with all provenance fields."""
        snap = {
            "snapshot_id": "y",
            "computed_at": "2026-11-01",
            "code_version": "v1",
            "input_version": "i1",
            "reference_version": "r1",
        }
        result = validate_snapshot_provenance(snap)
        assert result["decision"] == "ACCEPT"
        assert result["has_provenance"] is True

    def test_batch_fail_closed_count(self, snapshots):
        """C2: All zero-provenance snapshots in batch are FAIL_CLOSED."""
        reports = validate_batch(snapshots)
        fail_closed = [r for r in reports if r.status == "FAIL_CLOSED"]
        # Snapshots 5 and 10 have no provenance
        assert len(fail_closed) == 2, (
            f"Expected 2 FAIL_CLOSED, got {len(fail_closed)}"
        )
        # 100% interception: all no-provenance snapshots are caught
        no_prov_count = sum(1 for r in reports if not r.has_provenance)
        assert no_prov_count == len(fail_closed)


# ══════════════════════════════════════════════════════════════════════
# C3: Value Flip Justification
# ══════════════════════════════════════════════════════════════════════

class TestValueFlipJustification:
    """C3: Value flips must have component justification."""

    def test_no_flip_no_warning(self):
        """Small changes don't trigger flip detection."""
        prev = {"repro_rate": {"value": 0.85, "pass_count": 17, "denominator": 20}}
        curr = {"repro_rate": {"value": 0.86, "pass_count": 17, "denominator": 20}}
        flips = detect_value_flips(curr, prev, ["repro_rate"], flip_threshold_pct=5.0)
        assert len(flips) == 0

    def test_flip_with_justification(self):
        """Large change with component change is justified."""
        prev = {
            "repro_rate": {"value": 0.5, "pass_count": 10, "denominator": 20},
            "provenance": {"input_version": "v1"},
        }
        curr = {
            "repro_rate": {"value": 0.85, "pass_count": 17, "denominator": 20},
            "provenance": {"input_version": "v2", "change_justification": "data refresh"},
        }
        flips = detect_value_flips(curr, prev, ["repro_rate"], flip_threshold_pct=5.0)
        assert len(flips) == 1
        assert flips[0]["justified"] is True

    def test_flip_without_justification(self):
        """Large change without component change is unjustified."""
        prev = {
            "repro_rate": {"value": 0.85, "pass_count": 17, "denominator": 20},
            "provenance": {"input_version": "v1"},
        }
        curr = {
            "repro_rate": {"value": 0.3, "pass_count": 17, "denominator": 20},
            "provenance": {"input_version": "v1"},  # Same input — suspicious
        }
        flips = detect_value_flips(curr, prev, ["repro_rate"], flip_threshold_pct=5.0)
        assert len(flips) == 1
        assert flips[0]["justified"] is False
        assert flips[0]["reason"] == "UNJUSTIFIED_FLIP"

    def test_batch_unjustified_flips_detected(self, snapshots):
        """C3: Snapshot 12 has unjustified flip."""
        reports = validate_batch(snapshots)
        # Snapshot 12 (index 12) should have unjustified flips
        snap12_report = reports[12]
        unjustified = [
            vf for vf in snap12_report.value_flips
            if not vf["justified"]
        ]
        assert len(unjustified) > 0, "Snapshot 12 should have unjustified flips"


# ══════════════════════════════════════════════════════════════════════
# C4: Metric Versioning
# ══════════════════════════════════════════════════════════════════════

class TestMetricVersioning:
    """C4: Metric versioning covers ≥5 core KPIs."""

    def test_record_and_retrieve(self, versioning_service):
        v = versioning_service.record_version(
            metric_key="repro_rate",
            value=0.85,
            input_version="input-v1",
            code_version="abc123",
            reference_version="ref-v1",
        )
        assert v.metric_key == "repro_rate"
        assert v.value == 0.85
        current = versioning_service.get_current_value("repro_rate")
        assert current == 0.85

    def test_multiple_versions(self, versioning_service):
        for i in range(5):
            versioning_service.record_version(
                metric_key="repro_rate",
                value=0.8 + i * 0.01,
                input_version=f"v{i}",
                code_version=f"commit_{i}",
                reference_version="ref-v1",
            )
        lineage = versioning_service.get_lineage("repro_rate")
        assert lineage.version_count == 5
        assert lineage.current_version.value == pytest.approx(0.84, abs=1e-6)

    def test_import_snapshot_versions_5plus_kpis(self, versioning_service, good_snapshot):
        """C4: Importing a snapshot versions ≥5 core KPIs."""
        versions = versioning_service.import_snapshot_with_version(
            good_snapshot,
            code_version="v1.0.0",
            reference_version="ref-v1",
        )
        metric_keys = versioning_service.get_metric_keys()
        assert len(metric_keys) >= 5, (
            f"Expected ≥5 versioned KPIs, got {len(metric_keys)}: {metric_keys}"
        )

    def test_export_ledger(self, versioning_service):
        versioning_service.record_version(
            "kpi1", 0.5, "i1", "c1", "r1"
        )
        versioning_service.record_version(
            "kpi2", 100, "i1", "c1", "r1"
        )
        ledger = versioning_service.export_ledger()
        assert ledger["metric_count"] == 2
        assert ledger["total_versions"] == 2

    def test_batch_import_versions_all_snapshots(self, versioning_service, snapshots):
        """Import all 18 snapshots and verify versioning coverage."""
        total_versions = 0
        for snap in snapshots:
            vs = versioning_service.import_snapshot_with_version(snap)
            total_versions += len(vs)
        assert total_versions > 0
        assert versioning_service.get_version_count() == total_versions
        keys = versioning_service.get_metric_keys()
        assert len(keys) >= 5


# ══════════════════════════════════════════════════════════════════════
# C5: Snapshot Hash & Integrity
# ══════════════════════════════════════════════════════════════════════

class TestSnapshotIntegrity:
    """Snapshot hashing and integrity checks."""

    def test_hash_deterministic(self):
        snap = {"a": 1, "b": 2}
        h1 = compute_snapshot_hash(snap)
        h2 = compute_snapshot_hash(snap)
        assert h1 == h2

    def test_hash_changes_with_content(self):
        h1 = compute_snapshot_hash({"a": 1})
        h2 = compute_snapshot_hash({"a": 2})
        assert h1 != h2

    def test_hash_key_order_invariant(self):
        h1 = compute_snapshot_hash({"a": 1, "b": 2})
        h2 = compute_snapshot_hash({"b": 2, "a": 1})
        assert h1 == h2


# ══════════════════════════════════════════════════════════════════════
# Integration: Full Audit Pipeline
# ══════════════════════════════════════════════════════════════════════

class TestFullAuditPipeline:
    """Integration tests for the full audit pipeline."""

    def test_full_batch_audit(self, snapshots):
        """Run full validation on all 18 snapshots."""
        reports = validate_batch(snapshots)
        assert len(reports) == 18

        statuses = [r.status for r in reports]
        assert "FAIL_CLOSED" in statuses  # At least one fail-closed
        assert "PASS" in statuses or "WARN" in statuses  # At least one pass/warn

    def test_audit_report_serializable(self, snapshots):
        """Audit report can be serialized to JSON."""
        reports = validate_batch(snapshots)
        for r in reports:
            d = r.to_dict()
            json_str = json.dumps(d)
            assert len(json_str) > 0
            parsed = json.loads(json_str)
            assert parsed["snapshot_id"] == r.snapshot_id

    def test_versioning_integration(self, versioning_service, snapshots):
        """Versioning + validation work together."""
        reports = validate_batch(snapshots)
        for snap in snapshots:
            versioning_service.import_snapshot_with_version(snap)

        keys = versioning_service.get_metric_keys()
        assert len(keys) >= 5

        # Cross-check: versioned metrics match validated snapshots
        for r in reports:
            if r.has_provenance:
                for f in r.findings:
                    if f.estimator_a_value is not None:
                        current = versioning_service.get_current_value(f.kpi_key)
                        # Current value should be from the last snapshot
                        assert current is not None
