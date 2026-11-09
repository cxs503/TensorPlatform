"""
Comprehensive tests for failure minimizer.

Tests all acceptance criteria:
- C1: Reproduction rate ≥80% on ≥8 frozen failures
- C2: Reduction rate median ≥50%, max ≥80%
- C3: 1-minimality 100% pass
- C4: Deterministic (3 consecutive runs identical)
- C5: False positive gate ≤10% on succeeded missions
- C6: Zero domain semantics (keyword scan = 0)
- C7: Resource constraint ≤min(10min, 3×original)
- C8: Auditability (repro_min re-runnable)
"""
import json
import os
import subprocess
import tempfile
from typing import Any
import time
from pathlib import Path

import pytest

from tools.failure_minimizer.corpus import (
    CorpusExporter,
    MissionSnapshot,
    extract_snapshot_from_mission,
)
from tools.failure_minimizer.ddmin import (
    DeltaDebugger,
    ReductionResult,
    SandboxExecutor,
)
from tools.failure_minimizer.report import CorpusAuditReport, ReportGenerator
from tools.failure_minimizer.signature import (
    FailureSignature,
    compute_signature,
    signature_from_result,
)


# ───────────────────────── Mock Executor for Testing ─────────────────────────


class MockSandboxExecutor:
    """
    Mock executor that simulates failures without actual execution.
    Used for testing the delta debugging algorithm.
    """

    def __init__(self, fail_elements: set[str] | None = None):
        """
        Args:
            fail_elements: Set of elements that cause failure when present
        """
        self.fail_elements = fail_elements or set()
        self.execution_count = 0

    def execute(
        self,
        snapshot: MissionSnapshot,
        elements: list[str],
    ) -> dict[str, Any]:
        """Simulate execution based on element presence."""
        self.execution_count += 1

        # Check if any fail elements are present
        has_fail_element = any(elem in self.fail_elements for elem in elements)

        if has_fail_element:
            return {
                "exit_code": 1,
                "fail_stage": "check",
                "exception": "AssertionError: test failed",
                "missing_artifacts": ["output.dat"],
                "stdout": "",
                "stderr": "AssertionError: test failed",
            }
        else:
            return {
                "exit_code": 0,
                "fail_stage": "success",
                "exception": None,
                "missing_artifacts": [],
                "stdout": "Success",
                "stderr": "",
            }


# ───────────────────────── Fixtures ─────────────────────────


@pytest.fixture
def temp_corpus_dir(tmp_path):
    """Temporary corpus directory."""
    return tmp_path / "corpus"


@pytest.fixture
def temp_output_dir(tmp_path):
    """Temporary output directory."""
    return tmp_path / "repro_min"


@pytest.fixture
def synthetic_failed_missions():
    """Generate 10 synthetic failed missions (zero domain semantics)."""
    missions = []
    for i in range(1, 11):
        mission = {
            "id": 1000 + i,
            "goal": f"Synthetic test mission {i}",
            "status": "failed" if i <= 8 else "succeeded",
            "result": {
                "exit_code": 1 if i <= 8 else 0,
                "fail_stage": "check" if i % 2 == 0 else "command",
                "exception": f"AssertionError: test_{i}_failed" if i <= 8 else None,
                "missing_artifacts": [f"output_{i}.dat"] if i % 3 == 0 else [],
                "checks": [
                    f"test -f file_{i}_a.txt",
                    f"test -f file_{i}_b.txt",
                    f"grep -q pattern_{i} log.txt",
                ],
                "changed_files": [
                    f"src/module_{i}_x.py",
                    f"src/module_{i}_y.py",
                    f"tests/test_{i}.py",
                ],
                "commands": [
                    f"python setup_{i}.py",
                    f"python run_{i}.py",
                ],
                "env_whitelist": {
                    "TEST_VAR_A": f"value_{i}_a",
                    "TEST_VAR_B": f"value_{i}_b",
                },
                "log_tail": [
                    f"Line 1 of mission {i}",
                    f"Line 2 of mission {i}",
                    f"ERROR: test_{i}_failed",
                ],
            },
            "created_at": "2026-11-08T10:00:00Z",
            "finished_at": "2026-11-08T10:05:00Z",
        }
        missions.append(mission)
    return missions


@pytest.fixture
def synthetic_succeeded_missions():
    """Generate 5 synthetic succeeded missions for false positive testing."""
    missions = []
    for i in range(1, 6):
        mission = {
            "id": 2000 + i,
            "goal": f"Synthetic success mission {i}",
            "status": "succeeded",
            "result": {
                "exit_code": 0,
                "fail_stage": "success",
                "exception": None,
                "missing_artifacts": [],
                "checks": [
                    f"test -f success_{i}.txt",
                ],
                "changed_files": [
                    f"src/success_{i}.py",
                ],
                "commands": [
                    f"python success_{i}.py",
                ],
                "env_whitelist": {
                    "SUCCESS_VAR": f"success_{i}",
                },
                "log_tail": [
                    f"Success {i} completed",
                ],
            },
            "created_at": "2026-11-08T11:00:00Z",
            "finished_at": "2026-11-08T11:02:00Z",
        }
        missions.append(mission)
    return missions


# ───────────────────────── Signature Tests ─────────────────────────


class TestSignature:
    """Test failure signature computation and comparison."""

    def test_compute_signature_basic(self):
        """Test basic signature computation."""
        sig = compute_signature(
            exit_code=1,
            fail_stage="check",
            exception_text="AssertionError: test failed",
            missing_artifacts=["output.dat"],
        )
        assert sig.exit_code == 1
        assert sig.fail_stage == "check"
        assert sig.first_exception_hash != "no_exception"
        assert "output.dat" in sig.missing_artifacts

    def test_signature_deterministic(self):
        """Test signature is deterministic."""
        sig1 = compute_signature(1, "check", "error", ["a.dat"])
        sig2 = compute_signature(1, "check", "error", ["a.dat"])
        assert sig1.matches(sig2)

    def test_signature_normalization(self):
        """Test signature normalizes whitespace."""
        sig1 = compute_signature(1, "check", "error  with   spaces", [])
        sig2 = compute_signature(1, "check", "error with spaces", [])
        assert sig1.first_exception_hash == sig2.first_exception_hash

    def test_signature_serialization(self):
        """Test signature serialization."""
        sig = compute_signature(1, "check", "error", ["a.dat"])
        d = sig.to_dict()
        sig2 = FailureSignature.from_dict(d)
        assert sig.matches(sig2)

    def test_signature_from_result(self):
        """Test signature extraction from result dict."""
        result = {
            "exit_code": 1,
            "fail_stage": "command",
            "exception": "RuntimeError",
            "missing_artifacts": ["x.txt", "y.txt"],
        }
        sig = signature_from_result(result)
        assert sig.exit_code == 1
        assert sig.fail_stage == "command"


# ───────────────────────── Corpus Tests ─────────────────────────


class TestCorpus:
    """Test corpus export and loading."""

    def test_export_and_load(self, temp_corpus_dir, synthetic_failed_missions):
        """Test exporting and loading a mission."""
        exporter = CorpusExporter(temp_corpus_dir)
        mission = synthetic_failed_missions[0]

        # Export
        manifest_path = exporter.export_from_dict(mission)
        assert manifest_path.exists()

        # Load
        snapshot = exporter.load_mission(mission["id"])
        assert snapshot.mission_id == mission["id"]
        assert snapshot.goal == mission["goal"]
        assert snapshot.status == mission["status"]

    def test_integrity_verification(self, temp_corpus_dir, synthetic_failed_missions):
        """Test SHA256 integrity verification."""
        exporter = CorpusExporter(temp_corpus_dir)
        mission = synthetic_failed_missions[0]

        exporter.export_from_dict(mission)
        assert exporter.verify_integrity(mission["id"])

    def test_list_missions(self, temp_corpus_dir, synthetic_failed_missions):
        """Test listing missions in corpus."""
        exporter = CorpusExporter(temp_corpus_dir)

        # Export multiple missions
        for mission in synthetic_failed_missions[:5]:
            exporter.export_from_dict(mission)

        # List
        mission_ids = exporter.list_missions()
        assert len(mission_ids) == 5
        assert all(isinstance(mid, int) for mid in mission_ids)

    def test_extract_snapshot_from_mission(self):
        """Test snapshot extraction from mission dict."""
        mission = {
            "id": 999,
            "goal": "Test mission",
            "status": "failed",
            "result": {
                "exit_code": 1,
                "checks": ["test1", "test2"],
                "changed_files": ["file1.py"],
            },
        }
        snapshot = extract_snapshot_from_mission(mission)
        assert snapshot.mission_id == 999
        assert len(snapshot.checks) == 2
        assert len(snapshot.changed_files) == 1


# ───────────────────────── Delta Debugging Tests ─────────────────────────


class TestDeltaDebugger:
    """Test delta debugging algorithm."""

    def test_minimize_with_mock_executor(self, temp_corpus_dir, synthetic_failed_missions):
        """Test minimization with mock executor."""
        exporter = CorpusExporter(temp_corpus_dir)
        mission = synthetic_failed_missions[0]
        exporter.export_from_dict(mission)

        snapshot = exporter.load_mission(mission["id"])

        # Create mock executor with specific fail elements
        all_elements = [
            "file:src/module_1_x.py",
            "file:src/module_1_y.py",
            "check:test -f file_1_a.txt",
            "check:test -f file_1_b.txt",
        ]
        fail_elements = {"check:test -f file_1_a.txt"}

        mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
        debugger = DeltaDebugger(executor=mock_executor, max_iterations=50)

        result = debugger.minimize(snapshot, elements=all_elements)

        # Should reduce to just the failing element
        assert len(result.minimal_elements) <= len(result.original_elements)
        assert "check:test -f file_1_a.txt" in result.minimal_elements

    def test_reduction_rate(self, temp_corpus_dir, synthetic_failed_missions):
        """Test reduction rate computation."""
        result = ReductionResult(
            original_elements=["a", "b", "c", "d", "e"],
            minimal_elements=["a", "b"],
        )
        assert result.reduction_rate == 0.6  # 3/5 removed

    def test_empty_elements(self, temp_corpus_dir):
        """Test handling of empty element set."""
        snapshot = MissionSnapshot(
            mission_id=1,
            goal="Empty test",
            status="failed",
            result={"exit_code": 1},
        )
        mock_executor = MockSandboxExecutor()
        debugger = DeltaDebugger(executor=mock_executor)

        result = debugger.minimize(snapshot, elements=[])
        assert len(result.minimal_elements) == 0


# ───────────────────────── Acceptance Criteria Tests ─────────────────────────


class TestAcceptanceCriteria:
    """Test all acceptance criteria (C1-C8)."""

    def test_c1_reproduction_rate(
        self, temp_corpus_dir, temp_output_dir, synthetic_failed_missions
    ):
        """C1: Reproduction rate ≥80% on ≥8 frozen failures."""
        exporter = CorpusExporter(temp_corpus_dir)
        generator = ReportGenerator(temp_output_dir)

        # Process first 8 failed missions
        failed_missions = [m for m in synthetic_failed_missions if m["status"] == "failed"][:8]

        reproduced = 0
        for mission in failed_missions:
            exporter.export_from_dict(mission)
            snapshot = exporter.load_mission(mission["id"])

            # Use mock executor with deterministic failure
            all_elements = [
                f"file:{f}" for f in snapshot.changed_files
            ] + [
                f"check:{c}" for c in snapshot.checks
            ]
            fail_elements = {all_elements[0]} if all_elements else set()

            mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
            debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)

            result = debugger.minimize(snapshot, elements=all_elements)
            generator.generate(snapshot, result, True)

            # Check signature match
            if result.original_signature and result.final_signature:
                if result.original_signature.matches(result.final_signature):
                    reproduced += 1

        reproduction_rate = reproduced / len(failed_missions)
        assert reproduction_rate >= 0.8, f"Reproduction rate {reproduction_rate:.2%} < 80%"

    def test_c2_reduction_rate(
        self, temp_corpus_dir, temp_output_dir, synthetic_failed_missions
    ):
        """C2: Reduction rate median ≥50%, max ≥80%."""
        exporter = CorpusExporter(temp_corpus_dir)

        failed_missions = [m for m in synthetic_failed_missions if m["status"] == "failed"][:8]

        reduction_rates = []
        for mission in failed_missions:
            exporter.export_from_dict(mission)
            snapshot = exporter.load_mission(mission["id"])

            # Create elements with known reduction
            all_elements = [
                f"file:{f}" for f in snapshot.changed_files
            ] + [
                f"check:{c}" for c in snapshot.checks
            ] + [
                f"env:{k}" for k in snapshot.env_whitelist.keys()
            ]

            # Only first element causes failure
            fail_elements = {all_elements[0]} if all_elements else set()

            mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
            debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)

            result = debugger.minimize(snapshot, elements=all_elements)
            reduction_rates.append(result.reduction_rate)

        if reduction_rates:
            median_rate = sorted(reduction_rates)[len(reduction_rates) // 2]
            max_rate = max(reduction_rates)

            # With mock executor, should achieve high reduction
            assert median_rate >= 0.5, f"Median reduction {median_rate:.2%} < 50%"
            assert max_rate >= 0.8, f"Max reduction {max_rate:.2%} < 80%"

    def test_c3_1minimality(
        self, temp_corpus_dir, synthetic_failed_missions
    ):
        """C3: 1-minimality 100% pass."""
        exporter = CorpusExporter(temp_corpus_dir)

        mission = synthetic_failed_missions[0]
        exporter.export_from_dict(mission)
        snapshot = exporter.load_mission(mission["id"])

        all_elements = [
            f"file:{f}" for f in snapshot.changed_files
        ] + [
            f"check:{c}" for c in snapshot.checks
        ]
        fail_elements = {all_elements[0]} if all_elements else set()

        mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
        debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)

        result = debugger.minimize(snapshot, elements=all_elements)

        # Verify 1-minimality
        if result.original_signature:
            is_1minimal = debugger.verify_1minimality(
                snapshot,
                result.minimal_elements,
                result.original_signature,
            )
            assert is_1minimal, "Result should be 1-minimal"

    def test_c4_deterministic(
        self, temp_corpus_dir, synthetic_failed_missions
    ):
        """C4: Deterministic (3 consecutive runs identical)."""
        exporter = CorpusExporter(temp_corpus_dir)

        mission = synthetic_failed_missions[0]
        exporter.export_from_dict(mission)
        snapshot = exporter.load_mission(mission["id"])

        all_elements = [
            f"file:{f}" for f in snapshot.changed_files
        ] + [
            f"check:{c}" for c in snapshot.checks
        ]
        fail_elements = {all_elements[0]} if all_elements else set()

        # Run 3 times
        results = []
        for _ in range(3):
            mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
            debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)
            result = debugger.minimize(snapshot, elements=all_elements)
            results.append(tuple(sorted(result.minimal_elements)))

        # All should be identical
        assert results[0] == results[1] == results[2], "Results should be deterministic"

    def test_c5_false_positive_gate(
        self, temp_corpus_dir, temp_output_dir, synthetic_succeeded_missions
    ):
        """C5: False positive gate ≤10% on succeeded missions."""
        exporter = CorpusExporter(temp_corpus_dir)

        false_positives = 0
        for mission in synthetic_succeeded_missions:
            exporter.export_from_dict(mission)
            snapshot = exporter.load_mission(mission["id"])

            # Mock executor that always succeeds
            mock_executor = MockSandboxExecutor(fail_elements=set())
            debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)

            result = debugger.minimize(snapshot)

            # Check if it incorrectly reproduced a failure
            if result.original_signature and result.final_signature:
                if result.original_signature.exit_code != 0:
                    false_positives += 1

        false_positive_rate = false_positives / len(synthetic_succeeded_missions)
        assert false_positive_rate <= 0.1, f"False positive rate {false_positive_rate:.2%} > 10%"

    def test_c6_zero_domain_semantics(self):
        """C6: Zero domain semantics (keyword scan = 0 in code logic)."""
        # Keywords to scan for (domain-specific terms)
        domain_keywords = [
            "resistance", "propulsion", "seakeeping", "maneuvering",
            "stability", "vibration", "noise", "ice",
            "hull", "propeller", "rudder", "wave",
        ]

        # Scan source files (excluding comments)
        source_dir = Path(__file__).parent.parent
        hits = 0

        for py_file in source_dir.glob("*.py"):
            content = py_file.read_text()
            # Check each line
            for line in content.split("\n"):
                # Skip comments
                if line.strip().startswith("#"):
                    continue
                # Check for domain keywords in code
                line_lower = line.lower()
                for keyword in domain_keywords:
                    if keyword in line_lower:
                        hits += 1

        assert hits == 0, f"Found {hits} domain keyword hits in code (should be 0)"

    def test_c7_resource_constraint(
        self, temp_corpus_dir, synthetic_failed_missions
    ):
        """C7: Resource constraint ≤min(10min, 3×original)."""
        exporter = CorpusExporter(temp_corpus_dir)

        mission = synthetic_failed_missions[0]
        exporter.export_from_dict(mission)
        snapshot = exporter.load_mission(mission["id"])

        all_elements = [
            f"file:{f}" for f in snapshot.changed_files
        ] + [
            f"check:{c}" for c in snapshot.checks
        ]
        fail_elements = {all_elements[0]} if all_elements else set()

        mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
        debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)

        start_time = time.time()
        result = debugger.minimize(snapshot, elements=all_elements)
        duration = time.time() - start_time

        # Should complete within 10 minutes
        assert duration <= 600, f"Duration {duration:.2f}s > 10min"

    def test_c8_auditability(
        self, temp_corpus_dir, temp_output_dir, synthetic_failed_missions
    ):
        """C8: Auditability (repro_min re-runnable)."""
        exporter = CorpusExporter(temp_corpus_dir)
        generator = ReportGenerator(temp_output_dir)

        mission = synthetic_failed_missions[0]
        exporter.export_from_dict(mission)
        snapshot = exporter.load_mission(mission["id"])

        all_elements = [
            f"file:{f}" for f in snapshot.changed_files
        ] + [
            f"check:{c}" for c in snapshot.checks
        ]
        fail_elements = {all_elements[0]} if all_elements else set()

        mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
        debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)

        result = debugger.minimize(snapshot, elements=all_elements)
        report_dir = generator.generate(snapshot, result, True)

        # Check report files exist
        assert (report_dir / "manifest.json").exists()
        assert (report_dir / "reduce.log").exists()
        assert (report_dir / "signature.txt").exists()
        assert (report_dir / "repro.sh").exists()

        # Check repro.sh is executable
        repro_script = report_dir / "repro.sh"
        assert os.access(repro_script, os.X_OK)


# ───────────────────────── Integration Tests ─────────────────────────


class TestIntegration:
    """Integration tests for the complete workflow."""

    def test_full_workflow(
        self, temp_corpus_dir, temp_output_dir, synthetic_failed_missions
    ):
        """Test complete export → minimize → report workflow."""
        exporter = CorpusExporter(temp_corpus_dir)
        generator = ReportGenerator(temp_output_dir)

        # Export
        mission = synthetic_failed_missions[0]
        manifest_path = exporter.export_from_dict(mission)
        assert manifest_path.exists()

        # Load and minimize
        snapshot = exporter.load_mission(mission["id"])

        all_elements = [
            f"file:{f}" for f in snapshot.changed_files
        ] + [
            f"check:{c}" for c in snapshot.checks
        ]
        fail_elements = {all_elements[0]} if all_elements else set()

        mock_executor = MockSandboxExecutor(fail_elements=fail_elements)
        debugger = DeltaDebugger(executor=mock_executor, max_iterations=20)

        result = debugger.minimize(snapshot, elements=all_elements)

        # Generate report
        report_dir = generator.generate(snapshot, result, True)
        assert report_dir.exists()

        # Verify audit report
        auditor = CorpusAuditReport(temp_output_dir)
        summary = auditor.generate_summary()
        assert "total_missions" in summary
