"""Acceptance gate maturity — comprehensive test suite.

Validates all 6 machine acceptance criteria:
  C1: Inject 3 new failures → 100% detection, 0 false positives on pre-existing
  C2: Missing artifact → verdict=MISSING_ARTIFACT (fail-closed)
  C3: 3 types of baseline tampering → 100% rejection
  C4: Cross-arch separate keys, drift listed separately, not hidden
  C5: End-to-end latency < 60s
  C6: Zero domain semantics audit hits

All fixtures are synthetic and domain-neutral.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tools.acceptance_gate_maturity.model import (
    ArchKey,
    BaselineRecord,
    BaselineSnapshot,
    CriterionOutcome,
    GateVerdict,
    TamperType,
    TamperVerdict,
)
from tools.acceptance_gate_maturity.baseline_diff import (
    classify_failures,
    diff_against_baseline,
)
from tools.acceptance_gate_maturity.tamper_detect import (
    check_content_hash,
    check_structural_integrity,
    check_chain_of_trust,
    verify_baseline_integrity,
)
from tools.acceptance_gate_maturity.arch_separation import separate_by_arch
from tools.acceptance_gate_maturity.runner import run_acceptance_gate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_snapshot(
    arch: str = "loong64",
    records: list[dict] | None = None,
    with_digest: bool = True,
    with_chain: bool = False,
    prev_digest: str = "",
) -> BaselineSnapshot:
    """Helper to build a baseline snapshot for testing."""
    if records is None:
        records = [
            {"criterion_id": "C1", "expected_outcome": "PASS",
             "metric_path": "metrics.err_pct", "threshold": 5.0, "op": "le"},
            {"criterion_id": "C2", "expected_outcome": "PASS",
             "metric_path": "metrics.coverage", "threshold": 80.0, "op": "ge"},
            {"criterion_id": "C3", "expected_outcome": "FAIL_PRE_EXISTING",
             "failure_set_key": "C3_known_issue",
             "metric_path": "metrics.latency_ms", "threshold": 100.0, "op": "le"},
        ]
    recs = tuple(
        BaselineRecord(
            criterion_id=r["criterion_id"],
            expected_outcome=CriterionOutcome(r["expected_outcome"]),
            failure_set_key=r.get("failure_set_key", ""),
            metric_path=r.get("metric_path", ""),
            threshold=float(r.get("threshold", 0.0)),
            op=r.get("op", "le"),
        )
        for r in records
    )
    arch_key = ArchKey(arch=arch)
    snap = BaselineSnapshot(arch_key=arch_key, records=recs)
    if with_digest:
        digest = snap.compute_digest()
        chain_hash = snap.compute_chain_hash(prev_digest) if with_chain else ""
        snap = BaselineSnapshot(
            arch_key=arch_key, records=recs,
            digest=digest, chain_hash=chain_hash,
        )
    return snap


def _make_passing_measurements() -> dict:
    """Measurements where all criteria pass (except pre-existing C3)."""
    return {
        "metrics": {
            "err_pct": 2.1,      # C1: PASS (≤5.0)
            "coverage": 95.0,    # C2: PASS (≥80.0)
            "latency_ms": 150.0, # C3: FAIL (pre-existing, >100.0)
        }
    }


def _make_failing_measurements() -> dict:
    """Measurements with new failures injected."""
    return {
        "metrics": {
            "err_pct": 8.5,      # C1: FAIL_NEW (was PASS, now >5.0)
            "coverage": 95.0,    # C2: PASS
            "latency_ms": 150.0, # C3: FAIL_PRE_EXISTING
        }
    }


# ---------------------------------------------------------------------------
# C1: New failure detection — 100% detection, 0 false positives
# ---------------------------------------------------------------------------

class TestC1NewFailureDetection:
    """Inject 3 new failures → 100% detection, 0 false positives on pre-existing."""

    def test_detect_single_new_failure(self):
        """Inject one new failure (C1 regresses) → detected as FAIL_NEW."""
        snap = _make_snapshot()
        measurements = _make_failing_measurements()
        diff = diff_against_baseline(snap, measurements)

        assert "C1" in diff.new_failures, "C1 regression not detected"
        assert "C3" not in diff.new_failures, "C3 (pre-existing) falsely flagged as new"
        assert "C3" in diff.pre_existing_failures, "C3 not classified as pre-existing"

    def test_detect_three_new_failures(self):
        """Inject 3 new failures → all 3 detected, 0 false positives."""
        records = [
            {"criterion_id": "C1", "expected_outcome": "PASS",
             "metric_path": "m.a", "threshold": 1.0, "op": "le"},
            {"criterion_id": "C2", "expected_outcome": "PASS",
             "metric_path": "m.b", "threshold": 1.0, "op": "le"},
            {"criterion_id": "C3", "expected_outcome": "PASS",
             "metric_path": "m.c", "threshold": 1.0, "op": "le"},
            {"criterion_id": "C4", "expected_outcome": "FAIL_PRE_EXISTING",
             "failure_set_key": "C4_known",
             "metric_path": "m.d", "threshold": 1.0, "op": "le"},
        ]
        snap = _make_snapshot(records=records)
        measurements = {
            "m": {"a": 5.0, "b": 5.0, "c": 5.0, "d": 5.0}  # all fail
        }
        diff = diff_against_baseline(snap, measurements)

        # 3 new failures detected (C1, C2, C3 were PASS in baseline)
        assert set(diff.new_failures) == {"C1", "C2", "C3"}, \
            f"Expected 3 new failures, got {diff.new_failures}"
        # Pre-existing correctly classified (C4 was already failing)
        assert "C4" in diff.pre_existing_failures, \
            "C4 (pre-existing) not classified correctly"
        # 0 false positives: C4 NOT in new_failures
        assert "C4" not in diff.new_failures, \
            "False positive: pre-existing C4 flagged as new"

    def test_no_false_positives_on_pre_existing(self):
        """Pre-existing failures must never appear in new_failures."""
        snap = _make_snapshot()
        measurements = _make_passing_measurements()
        diff = diff_against_baseline(snap, measurements)

        assert len(diff.new_failures) == 0, \
            f"False positives detected: {diff.new_failures}"
        assert "C3" in diff.pre_existing_failures

    def test_detection_rate_100_percent(self):
        """Statistical: inject N failures, all must be detected."""
        n_criteria = 10
        records = [
            {"criterion_id": f"C{i}", "expected_outcome": "PASS",
             "metric_path": f"m.c{i}", "threshold": 1.0, "op": "le"}
            for i in range(n_criteria)
        ]
        snap = _make_snapshot(records=records)
        # All fail
        measurements = {"m": {f"c{i}": 5.0 for i in range(n_criteria)}}
        diff = diff_against_baseline(snap, measurements)

        assert len(diff.new_failures) == n_criteria, \
            f"Detection rate {len(diff.new_failures)}/{n_criteria} < 100%"


# ---------------------------------------------------------------------------
# C2: Missing artifact → MISSING_ARTIFACT (fail-closed)
# ---------------------------------------------------------------------------

class TestC2MissingArtifact:
    """Missing artifact → verdict=MISSING_ARTIFACT (fail-closed, no pass-through)."""

    def test_missing_metric_returns_missing_artifact(self):
        """When a required metric is absent, verdict must be MISSING_ARTIFACT."""
        snap = _make_snapshot()
        measurements = {"metrics": {"err_pct": 2.1}}  # coverage and latency missing
        diff = diff_against_baseline(snap, measurements)

        assert "C2" in diff.missing_artifacts
        assert "C3" in diff.missing_artifacts
        assert diff.gate_verdict() == GateVerdict.MISSING_ARTIFACT

    def test_all_metrics_missing(self):
        """Completely empty measurements → MISSING_ARTIFACT."""
        snap = _make_snapshot()
        measurements = {}
        diff = diff_against_baseline(snap, measurements)

        assert len(diff.missing_artifacts) == 3
        assert diff.gate_verdict() == GateVerdict.MISSING_ARTIFACT

    def test_fail_closed_no_pass_through(self):
        """Even if other criteria pass, missing artifact blocks the gate."""
        snap = _make_snapshot()
        measurements = {
            "metrics": {
                "err_pct": 2.1,   # C1 passes
                "coverage": 95.0, # C2 passes
                # C3 missing
            }
        }
        diff = diff_against_baseline(snap, measurements)

        assert "C3" in diff.missing_artifacts
        assert diff.gate_verdict() == GateVerdict.MISSING_ARTIFACT
        # Must NOT be PASS_WITH_KNOWN or PASS
        assert diff.gate_verdict() != GateVerdict.PASS
        assert diff.gate_verdict() != GateVerdict.PASS_WITH_KNOWN

    def test_runner_missing_artifact_verdict(self):
        """End-to-end: runner returns MISSING_ARTIFACT verdict."""
        snap = _make_snapshot()
        measurements = {"metrics": {"err_pct": 2.1}}  # partial
        result = run_acceptance_gate(snap, measurements)

        assert result.verdict == GateVerdict.MISSING_ARTIFACT


# ---------------------------------------------------------------------------
# C3: Baseline tampering — 3 types, 100% rejection
# ---------------------------------------------------------------------------

class TestC3TamperDetection:
    """3 types of baseline tampering → 100% rejection."""

    def test_type1_content_hash_tampering(self):
        """Type 1: modify records after digest was computed → detected."""
        snap = _make_snapshot(with_digest=True)
        # Tamper: modify a record's threshold
        tampered_records = list(snap.records)
        tampered_records[0] = BaselineRecord(
            criterion_id="C1",
            expected_outcome=CriterionOutcome.PASS,
            metric_path="metrics.err_pct",
            threshold=99.0,  # tampered!
            op="le",
        )
        tampered_snap = BaselineSnapshot(
            arch_key=snap.arch_key,
            records=tuple(tampered_records),
            digest=snap.digest,  # old digest doesn't match new content
        )

        finding = check_content_hash(tampered_snap)
        assert finding is not None, "Content hash tampering not detected"
        assert finding.tamper_type == TamperType.CONTENT_HASH

    def test_type2_structural_tampering_extra_field(self):
        """Type 2a: add unexpected field → detected."""
        raw = {
            "arch_key": "loong64",
            "records": [],
            "digest": "abc",
            "schema_version": "acceptance-gate-maturity/1.0",
            "backdoor": "injected",  # unexpected field!
        }
        findings = check_structural_integrity(raw)
        assert any(f.tamper_type == TamperType.STRUCTURAL for f in findings), \
            "Structural tampering (extra field) not detected"

    def test_type2_structural_tampering_missing_field(self):
        """Type 2b: remove required field → detected."""
        raw = {
            "arch_key": "loong64",
            "records": [],
            # "digest" missing!
            "schema_version": "acceptance-gate-maturity/1.0",
        }
        findings = check_structural_integrity(raw)
        assert any(f.tamper_type == TamperType.STRUCTURAL for f in findings), \
            "Structural tampering (missing field) not detected"

    def test_type2_structural_tampering_invalid_outcome(self):
        """Type 2c: invalid expected_outcome value → detected."""
        raw = {
            "arch_key": "loong64",
            "records": [
                {"criterion_id": "C1", "expected_outcome": "HACKED"}
            ],
            "digest": "abc",
            "schema_version": "acceptance-gate-maturity/1.0",
        }
        findings = check_structural_integrity(raw)
        assert any(f.tamper_type == TamperType.STRUCTURAL for f in findings), \
            "Structural tampering (invalid outcome) not detected"

    def test_type3_chain_of_trust_tampering(self):
        """Type 3: break chain-of-trust → detected."""
        snap = _make_snapshot(with_digest=True, with_chain=True, prev_digest="prev123")
        # Tamper: modify chain_hash
        tampered = BaselineSnapshot(
            arch_key=snap.arch_key,
            records=snap.records,
            digest=snap.digest,
            chain_hash="tampered_chain_hash",  # wrong!
        )
        finding = check_chain_of_trust(tampered, prev_digest="prev123")
        assert finding is not None, "Chain-of-trust tampering not detected"
        assert finding.tamper_type == TamperType.CHAIN_OF_TRUST

    def test_all_three_types_rejected_by_runner(self):
        """End-to-end: all 3 tamper types → BASELINE_TAMPERED verdict."""
        # Type 1: content hash mismatch
        snap1 = _make_snapshot(with_digest=True)
        tampered_records = list(snap1.records)
        tampered_records[0] = BaselineRecord(
            criterion_id="C1", expected_outcome=CriterionOutcome.PASS,
            metric_path="metrics.err_pct", threshold=99.0, op="le",
        )
        tampered_snap1 = BaselineSnapshot(
            arch_key=snap1.arch_key, records=tuple(tampered_records),
            digest=snap1.digest,
        )
        raw1 = tampered_snap1.to_dict()
        result1 = run_acceptance_gate(tampered_snap1, {}, raw_baseline=raw1)
        assert result1.verdict == GateVerdict.BASELINE_TAMPERED, \
            f"Type 1 not rejected: {result1.verdict}"

        # Type 2: structural tampering
        snap2 = _make_snapshot(with_digest=True)
        raw2 = snap2.to_dict()
        raw2["backdoor"] = "injected"
        result2 = run_acceptance_gate(snap2, {}, raw_baseline=raw2)
        assert result2.verdict == GateVerdict.BASELINE_TAMPERED, \
            f"Type 2 not rejected: {result2.verdict}"

        # Type 3: chain-of-trust
        snap3 = _make_snapshot(with_digest=True, with_chain=True, prev_digest="prev")
        tampered_snap3 = BaselineSnapshot(
            arch_key=snap3.arch_key, records=snap3.records,
            digest=snap3.digest, chain_hash="wrong_hash",
        )
        result3 = run_acceptance_gate(tampered_snap3, {}, prev_digest="prev")
        assert result3.verdict == GateVerdict.BASELINE_TAMPERED, \
            f"Type 3 not rejected: {result3.verdict}"

    def test_clean_baseline_passes_tamper_check(self):
        """Clean baseline → no tamper findings."""
        snap = _make_snapshot(with_digest=True)
        raw = snap.to_dict()
        report = verify_baseline_integrity(snap, raw=raw)
        assert report.verdict == TamperVerdict.CLEAN
        assert len(report.findings) == 0


# ---------------------------------------------------------------------------
# C4: Cross-architecture separation
# ---------------------------------------------------------------------------

class TestC4CrossArchSeparation:
    """Cross-arch separate keys, drift listed separately, not hidden."""

    def test_separate_baselines_per_arch(self):
        """Each architecture has its own baseline; evaluated independently."""
        snap_loong = _make_snapshot(arch="loong64")
        snap_x86 = _make_snapshot(arch="x86_64")

        measurements_loong = _make_passing_measurements()
        measurements_x86 = {
            "metrics": {
                "err_pct": 2.1,
                "coverage": 95.0,
                "latency_ms": 50.0,  # C3 passes on x86 (different baseline behaviour)
            }
        }

        baselines = {
            "loong64": snap_loong,
            "x86_64": snap_x86,
        }
        measurements_by_arch = {
            "loong64": measurements_loong,
            "x86_64": measurements_x86,
        }

        report = separate_by_arch(baselines, measurements_by_arch)

        assert len(report.per_arch) == 2
        # loong64: C3 is pre-existing failure
        loong_result = next(r for r in report.per_arch if r.arch_key.arch == "loong64")
        assert "C3" in loong_result.diff.pre_existing_failures
        # x86_64: C3 passes (different baseline behaviour)
        x86_result = next(r for r in report.per_arch if r.arch_key.arch == "x86_64")
        assert "C3" not in x86_result.diff.pre_existing_failures

    def test_drift_reported_separately(self):
        """Cross-arch drift is reported as drift items, not hidden."""
        snap_loong = _make_snapshot(arch="loong64")
        snap_x86 = _make_snapshot(arch="x86_64")

        # loong64: C1 passes
        measurements_loong = _make_passing_measurements()
        # x86_64: C1 fails (new failure)
        measurements_x86 = {
            "metrics": {
                "err_pct": 8.5,   # C1 FAIL_NEW on x86
                "coverage": 95.0,
                "latency_ms": 150.0,
            }
        }

        baselines = {"loong64": snap_loong, "x86_64": snap_x86}
        measurements_by_arch = {"loong64": measurements_loong, "x86_64": measurements_x86}

        report = separate_by_arch(baselines, measurements_by_arch)

        # Drift must be reported
        assert len(report.drift_items) > 0, "Cross-arch drift not reported"
        drift_c1 = next((d for d in report.drift_items if d.criterion_id == "C1"), None)
        assert drift_c1 is not None, "C1 drift not detected"
        assert "loong64" in drift_c1.passing_arches
        assert "x86_64" in drift_c1.failing_arches

    def test_drift_not_hidden_by_aggregation(self):
        """Even if one arch passes all, drift on another arch is not hidden."""
        snap_loong = _make_snapshot(arch="loong64")
        snap_x86 = _make_snapshot(arch="x86_64")

        measurements_loong = _make_passing_measurements()  # all good
        measurements_x86 = {
            "metrics": {
                "err_pct": 8.5,   # C1 fails
                "coverage": 95.0,
                "latency_ms": 150.0,
            }
        }

        baselines = {"loong64": snap_loong, "x86_64": snap_x86}
        measurements_by_arch = {"loong64": measurements_loong, "x86_64": measurements_x86}

        report = separate_by_arch(baselines, measurements_by_arch)

        # Overall verdict must reflect the failure, not be masked by loong64 passing
        assert report.overall_verdict == GateVerdict.FAIL_NEW, \
            f"Drift hidden by aggregation: overall={report.overall_verdict}"


# ---------------------------------------------------------------------------
# C5: End-to-end latency < 60s
# ---------------------------------------------------------------------------

class TestC5Latency:
    """End-to-end latency must be < 60 seconds."""

    def test_single_arch_latency(self):
        """Single-arch acceptance gate completes in < 60s."""
        snap = _make_snapshot(with_digest=True)
        raw = snap.to_dict()
        measurements = _make_passing_measurements()

        t0 = time.monotonic()
        result = run_acceptance_gate(snap, measurements, raw_baseline=raw)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 60_000, f"Latency {elapsed_ms:.1f}ms exceeds 60s"
        assert result.elapsed_ms < 60_000

    def test_multi_arch_latency(self):
        """Multi-arch acceptance gate completes in < 60s."""
        n_archs = 5
        baselines = {}
        measurements_by_arch = {}
        for i in range(n_archs):
            arch = f"arch{i}"
            baselines[arch] = _make_snapshot(arch=arch, with_digest=True)
            measurements_by_arch[arch] = _make_passing_measurements()

        primary_arch = "arch0"
        primary_snap = baselines.pop(primary_arch)
        primary_meas = measurements_by_arch.pop(primary_arch)

        t0 = time.monotonic()
        result = run_acceptance_gate(
            primary_snap, primary_meas,
            extra_baselines=baselines,
            extra_measurements=measurements_by_arch,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 60_000, f"Multi-arch latency {elapsed_ms:.1f}ms exceeds 60s"

    def test_large_baseline_latency(self):
        """100 criteria baseline completes in < 60s."""
        records = [
            {"criterion_id": f"C{i:03d}", "expected_outcome": "PASS",
             "metric_path": f"m.c{i:03d}", "threshold": 1.0, "op": "le"}
            for i in range(100)
        ]
        snap = _make_snapshot(records=records, with_digest=True)
        measurements = {"m": {f"c{i:03d}": 0.5 for i in range(100)}}

        t0 = time.monotonic()
        result = run_acceptance_gate(snap, measurements)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 60_000, f"Large baseline latency {elapsed_ms:.1f}ms exceeds 60s"
        assert result.verdict == GateVerdict.PASS


# ---------------------------------------------------------------------------
# C6: Zero domain semantics audit
# ---------------------------------------------------------------------------

class TestC6ZeroDomainSemantics:
    """Zero domain semantics in the acceptance gate maturity module."""

    def test_no_domain_terms_in_source(self):
        """Scan all source files for domain vocabulary."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from tools.evidence_domain_scan import scan_paths, load_lexicon

        lexicon = load_lexicon()
        module_dir = Path(__file__).resolve().parents[1] / "tools" / "acceptance_gate_maturity"
        result = scan_paths([module_dir], lexicon=lexicon)

        assert len(result["hits"]) == 0, \
            f"Domain terms found in acceptance_gate_maturity: {result['hits']}"

    def test_no_domain_terms_in_tests(self):
        """Scan this test file for domain vocabulary."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from tools.evidence_domain_scan import scan_paths, load_lexicon

        lexicon = load_lexicon()
        test_file = Path(__file__).resolve()
        result = scan_paths([test_file], lexicon=lexicon)

        assert len(result["hits"]) == 0, \
            f"Domain terms found in test file: {result['hits']}"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """End-to-end integration tests."""

    def test_full_pipeline_clean(self):
        """Full pipeline: clean baseline + passing measurements → PASS."""
        snap = _make_snapshot(with_digest=True)
        raw = snap.to_dict()
        measurements = _make_passing_measurements()

        result = run_acceptance_gate(snap, measurements, raw_baseline=raw)

        assert result.verdict == GateVerdict.PASS_WITH_KNOWN  # C3 is pre-existing
        assert result.tamper_report is not None
        assert result.tamper_report.is_clean

    def test_full_pipeline_regression(self):
        """Full pipeline: regression detected → FAIL_NEW."""
        snap = _make_snapshot(with_digest=True)
        raw = snap.to_dict()
        measurements = _make_failing_measurements()

        result = run_acceptance_gate(snap, measurements, raw_baseline=raw)

        assert result.verdict == GateVerdict.FAIL_NEW
        assert "C1" in result.diff.new_failures

    def test_full_pipeline_tampered_then_rejected(self):
        """Full pipeline: tampered baseline → rejected before diff."""
        snap = _make_snapshot(with_digest=True)
        raw = snap.to_dict()
        raw["backdoor"] = "injected"  # structural tampering

        measurements = _make_passing_measurements()
        result = run_acceptance_gate(snap, measurements, raw_baseline=raw)

        assert result.verdict == GateVerdict.BASELINE_TAMPERED
        # Diff should not even be computed
        assert result.diff is None

    def test_serialization_roundtrip(self):
        """Baseline snapshot can be serialized and deserialized."""
        snap = _make_snapshot(with_digest=True, with_chain=True, prev_digest="prev")
        d = snap.to_dict()
        restored = BaselineSnapshot.from_dict(d)

        assert restored.arch_key == snap.arch_key
        assert len(restored.records) == len(snap.records)
        assert restored.digest == snap.digest
        assert restored.chain_hash == snap.chain_hash

    def test_result_serialization(self):
        """AcceptanceGateResult can be serialized to dict."""
        snap = _make_snapshot(with_digest=True)
        raw = snap.to_dict()
        measurements = _make_passing_measurements()

        result = run_acceptance_gate(snap, measurements, raw_baseline=raw)
        d = result.to_dict()

        assert "verdict" in d
        assert "elapsed_ms" in d
        assert "tamper_report" in d
        assert "diff" in d
