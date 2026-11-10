"""Tests for the Evidence Chain Integrity Auditor (P190-N07).

Acceptance criteria:
(a) Known OVERCLAIM detection: propulsion=REAL but tests indicate MOCK
(b) 3 synthetic capabilities: has test / no test / test fails -> correct classification
(c) Fail-soft: missing mapping -> WITHHELD, not crash
(d) JSON report structure: capability_id/finding_type/evidence_path/suggested_fix
(e) Zero domain semantics: auditor doesn't depend on domain knowledge
(f) Idempotent: same state -> same output across 3 scans
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE))

from tools.evidence_chain_auditor.auditor import (
    AuditFinding,
    AuditReport,
    EvidenceChainAuditor,
    FindingType,
    TestResult,
)
from tools.evidence_chain_auditor.mapping_loader import (
    CapabilityTestMapping,
    MappingLoader,
)
from tools.evidence_chain_auditor.report_generator import AuditReportGenerator


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def synthetic_worktree(tmp_path):
    """Create a synthetic worktree with known capability states.

    Four synthetic capabilities:
      - synth.has_tests: has 4 passing tests, claims REAL -> consistent
      - synth.no_tests: no test files exist, claims REAL -> overclaim (missing_test)
      - synth.few_tests: has only 1 test, claims REAL -> insufficient_coverage
      - synth.consistent_mock: has 3 tests, claims MOCK -> consistent (underclaim info)
    """
    wt = tmp_path / "worktree"
    wt.mkdir()

    # Create ledger
    ledger_dir = wt / "artifacts" / "capability_ledger"
    ledger_dir.mkdir(parents=True)
    ledger = {
        "schema_version": "2.0",
        "capabilities": [
            {
                "id": "synth.has_tests",
                "domain": "synthetic",
                "claimed_level": "REAL",
                "evidence": [],
            },
            {
                "id": "synth.no_tests",
                "domain": "synthetic",
                "claimed_level": "REAL",  # OVERCLAIM: no tests but claims REAL
                "evidence": [],
            },
            {
                "id": "synth.few_tests",
                "domain": "synthetic",
                "claimed_level": "REAL",  # OVERCLAIM: only 1 test but claims REAL
                "evidence": [],
            },
            {
                "id": "synth.consistent_mock",
                "domain": "synthetic",
                "claimed_level": "MOCK",
                "evidence": [],
            },
        ],
    }
    (ledger_dir / "capability_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False)
    )

    # Create test directory
    tests_dir = wt / "tests"
    tests_dir.mkdir()

    # synth.has_tests: 4 passing test functions
    (tests_dir / "test_synth_has_tests.py").write_text(
        '''
"""Tests for synth.has_tests -- all pass."""
def test_case_1():
    assert 1 + 1 == 2

def test_case_2():
    assert 2 * 3 == 6

def test_case_3():
    assert "hello".upper() == "HELLO"

def test_case_4():
    assert len([1, 2, 3]) == 3
'''
    )

    # synth.few_tests: only 1 test function (insufficient coverage)
    (tests_dir / "test_synth_few.py").write_text(
        '''
"""Tests for synth.few_tests -- only 1 test."""
def test_only_one():
    assert True
'''
    )

    # synth.consistent_mock: 3 passing tests, consistent with MOCK claim
    (tests_dir / "test_synth_consistent.py").write_text(
        '''
"""Tests for synth.consistent_mock."""
def test_case_1():
    assert True

def test_case_2():
    assert 42 == 42

def test_case_3():
    assert None is None
'''
    )

    # Create mapping
    mapping_dir = wt / "tools" / "evidence_chain_auditor"
    mapping_dir.mkdir(parents=True)
    mapping = {
        "schema_version": "1.0",
        "mappings": [
            {
                "capability_id": "synth.has_tests",
                "test_modules": ["tests/test_synth_has_tests.py"],
                "min_test_cases": 3,
                "domain": "synthetic",
            },
            {
                "capability_id": "synth.no_tests",
                "test_modules": ["tests/test_synth_nonexistent.py"],
                "min_test_cases": 3,
                "domain": "synthetic",
            },
            {
                "capability_id": "synth.few_tests",
                "test_modules": ["tests/test_synth_few.py"],
                "min_test_cases": 3,
                "domain": "synthetic",
            },
            {
                "capability_id": "synth.consistent_mock",
                "test_modules": ["tests/test_synth_consistent.py"],
                "min_test_cases": 3,
                "domain": "synthetic",
            },
        ],
    }
    (mapping_dir / "capability_test_mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False)
    )

    return wt


@pytest.fixture
def auditor_with_synthetic(synthetic_worktree):
    """Create an auditor pointed at the synthetic worktree."""
    return EvidenceChainAuditor(
        worktree=synthetic_worktree,
        ledger_path=synthetic_worktree / "artifacts" / "capability_ledger" / "capability_ledger.json",
        mapping_path=synthetic_worktree / "tools" / "evidence_chain_auditor" / "capability_test_mapping.json",
        run_pytest=False,  # use static inspection for speed
    )


# ── Test: Mapping Loader ────────────────────────────────────────────────────


class TestMappingLoader:
    """Tests for the mapping loader with fail-soft semantics."""

    def test_load_valid_mapping(self, synthetic_worktree):
        """Loading a valid mapping file succeeds."""
        loader = MappingLoader(
            mapping_path=synthetic_worktree / "tools" / "evidence_chain_auditor" / "capability_test_mapping.json"
        )
        mappings = loader.load()
        assert loader.load_status == "loaded"
        assert len(mappings) == 4
        assert "synth.has_tests" in mappings

    def test_missing_file_fail_soft(self, tmp_path):
        """Missing mapping file -> empty mapping, not crash."""
        loader = MappingLoader(mapping_path=tmp_path / "nonexistent.json")
        mappings = loader.load()
        assert loader.load_status == "missing_file"
        assert mappings == {}
        assert loader.load_error is not None

    def test_malformed_json_fail_soft(self, tmp_path):
        """Malformed JSON -> empty mapping, not crash."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json content")
        loader = MappingLoader(mapping_path=bad_file)
        mappings = loader.load()
        assert loader.load_status == "parse_error"
        assert mappings == {}

    def test_missing_fields_skipped(self, tmp_path):
        """Entries with missing required fields are skipped."""
        mapping_file = tmp_path / "partial.json"
        mapping_file.write_text(json.dumps({
            "mappings": [
                {"capability_id": "good.one", "test_modules": ["t.py"]},
                {"test_modules": ["t.py"]},  # missing capability_id -> skip
                {"capability_id": "good.two"},  # missing test_modules -> defaults to []
            ]
        }))
        loader = MappingLoader(mapping_path=mapping_file)
        mappings = loader.load()
        assert loader.load_status == "loaded"
        assert "good.one" in mappings
        assert "good.two" in mappings
        assert len(mappings) == 2


# ── Test: Acceptance Criterion (a) — OVERCLAIM Detection ───────────────────


class TestOverclaimDetection:
    """Criterion (a): Detect known OVERCLAIM (claims REAL but evidence insufficient)."""

    def test_overclaim_detected_for_no_tests(self, auditor_with_synthetic):
        """synth.no_tests claims REAL but has no tests -> overclaim detected."""
        result = auditor_with_synthetic.audit_capability("synth.no_tests")
        assert result.status == "inconsistent"

        finding_types = [f.finding_type for f in result.findings]
        assert FindingType.MISSING_TEST in finding_types

    def test_overclaim_detected_for_insufficient_coverage(self, auditor_with_synthetic):
        """synth.few_tests claims REAL but has only 1 test -> overclaim detected."""
        result = auditor_with_synthetic.audit_capability("synth.few_tests")
        assert result.status == "inconsistent"

        finding_types = [f.finding_type for f in result.findings]
        # Should detect overclaim (claims REAL but only WITHHELD-level evidence)
        assert FindingType.OVERCLAIM in finding_types or FindingType.INSUFFICIENT_COVERAGE in finding_types

    def test_overclaim_finding_has_required_fields(self, auditor_with_synthetic):
        """Overclaim findings have all required fields."""
        result = auditor_with_synthetic.audit_capability("synth.few_tests")
        overclaim_findings = [
            f for f in result.findings
            if f.finding_type in (FindingType.OVERCLAIM, FindingType.INSUFFICIENT_COVERAGE)
        ]
        assert len(overclaim_findings) > 0

        for finding in overclaim_findings:
            assert finding.capability_id == "synth.few_tests"
            assert finding.evidence_path  # non-empty
            assert finding.suggested_fix  # non-empty
            assert finding.severity in ("critical", "warning", "info")


# ── Test: Acceptance Criterion (b) — 3 Synthetic Capabilities ──────────────


class TestSyntheticClassification:
    """Criterion (b): Correctly classify 3 synthetic capabilities."""

    def test_has_tests_classified_as_consistent(self, auditor_with_synthetic):
        """synth.has_tests: has 4 passing tests, claims REAL -> consistent."""
        result = auditor_with_synthetic.audit_capability("synth.has_tests")
        assert result.test_inferred_level == "REAL"
        assert result.ledger_claimed_level == "REAL"
        assert result.status == "consistent"
        assert len(result.findings) == 0

    def test_no_tests_classified_as_inconsistent(self, auditor_with_synthetic):
        """synth.no_tests: no test files, claims REAL -> inconsistent."""
        result = auditor_with_synthetic.audit_capability("synth.no_tests")
        assert result.test_inferred_level == "NOT_SUPPORTED"
        assert result.ledger_claimed_level == "REAL"
        assert result.status == "inconsistent"
        assert len(result.findings) > 0

    def test_few_tests_classified_as_inconsistent(self, auditor_with_synthetic):
        """synth.few_tests: only 1 test, claims REAL -> inconsistent."""
        result = auditor_with_synthetic.audit_capability("synth.few_tests")
        # Static inspection sees 1 test function
        total_cases = sum(tr.total_cases for tr in result.test_results if tr.exists)
        assert total_cases == 1
        # With only 1 test, inferred level is WITHHELD (not enough for REAL)
        assert result.test_inferred_level == "WITHHELD"
        assert result.status == "inconsistent"


# ── Test: Acceptance Criterion (c) — Fail-Soft ─────────────────────────────


class TestFailSoft:
    """Criterion (c): Missing mapping -> WITHHELD, not crash."""

    def test_missing_mapping_file_no_crash(self, tmp_path):
        """Auditor doesn't crash when mapping file is missing."""
        # Create minimal ledger
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        ledger = {
            "capabilities": [
                {"id": "test.cap", "claimed_level": "REAL", "evidence": []}
            ]
        }
        (ledger_dir / "ledger.json").write_text(json.dumps(ledger))

        auditor = EvidenceChainAuditor(
            worktree=tmp_path,
            ledger_path=ledger_dir / "ledger.json",
            mapping_path=tmp_path / "nonexistent_mapping.json",
        )
        # Should not raise
        report = auditor.run_audit()
        assert report.mapping_status == "missing_file"
        assert report.total_capabilities >= 1

    def test_unmapped_capability_gets_withheld(self, tmp_path):
        """Capability not in mapping -> inferred level = WITHHELD."""
        # Create ledger with capability not in mapping
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        ledger = {
            "capabilities": [
                {"id": "unknown.cap", "claimed_level": "REAL", "evidence": []}
            ]
        }
        (ledger_dir / "ledger.json").write_text(json.dumps(ledger))

        # Create empty mapping
        mapping_dir = tmp_path / "mapping"
        mapping_dir.mkdir()
        mapping = {"mappings": []}
        (mapping_dir / "mapping.json").write_text(json.dumps(mapping))

        auditor = EvidenceChainAuditor(
            worktree=tmp_path,
            ledger_path=ledger_dir / "ledger.json",
            mapping_path=mapping_dir / "mapping.json",
        )
        result = auditor.audit_capability("unknown.cap")
        assert result.test_inferred_level == "WITHHELD"
        assert result.status == "unknown"
        # Should have a missing_test finding
        assert any(f.finding_type == FindingType.MISSING_TEST for f in result.findings)


# ── Test: Acceptance Criterion (d) — JSON Report Structure ─────────────────


class TestReportStructure:
    """Criterion (d): JSON report has required structure."""

    def test_report_json_structure(self, auditor_with_synthetic):
        """Report JSON contains all required fields."""
        report = auditor_with_synthetic.run_audit(scan_id="test-scan-001")
        report_dict = report.to_dict()

        # Top-level fields
        assert "scan_id" in report_dict
        assert "scanned_at" in report_dict
        assert "mapping_status" in report_dict
        assert "total_capabilities" in report_dict
        assert "consistent_count" in report_dict
        assert "inconsistent_count" in report_dict
        assert "unknown_count" in report_dict
        assert "findings" in report_dict
        assert "capability_results" in report_dict
        assert "summary" in report_dict

    def test_finding_json_structure(self, auditor_with_synthetic):
        """Each finding has required fields."""
        report = auditor_with_synthetic.run_audit()

        for finding_dict in report.to_dict()["findings"]:
            assert "capability_id" in finding_dict
            assert "finding_type" in finding_dict
            assert finding_dict["finding_type"] in [ft.value for ft in FindingType]
            assert "evidence_path" in finding_dict
            assert "suggested_fix" in finding_dict
            assert "severity" in finding_dict
            assert "description" in finding_dict

    def test_report_writable_as_json(self, auditor_with_synthetic, tmp_path):
        """Report can be serialized to valid JSON."""
        report = auditor_with_synthetic.run_audit()
        json_str = report.to_json()
        parsed = json.loads(json_str)
        assert parsed["scan_id"] == report.scan_id


# ── Test: Acceptance Criterion (e) — Zero Domain Semantics ─────────────────


class TestZeroDomainSemantics:
    """Criterion (e): Auditor doesn't depend on domain-specific knowledge."""

    def test_no_domain_strings_in_auditor_logic(self):
        """Auditor source code contains no domain-specific strings in logic.

        Note: Comments/docstrings may mention domains for illustration,
        but the actual logic (function bodies, variable names, string literals
        used in computation) must not contain domain terms.
        """
        auditor_path = WORKTREE / "tools" / "evidence_chain_auditor" / "auditor.py"
        source = auditor_path.read_text()

        # Extract only function bodies (skip docstrings and comments)
        import ast
        tree = ast.parse(source)

        domain_terms = [
            "resistance", "propulsion", "stability", "seakeeping",
            "maneuvering", "structure", "vibration", "ice", "green",
            "holtrop", "mittelmeyer", "lindqvist", "hull",
            "propeller", "rudder",
        ]

        # Check string literals in the code (not comments/docstrings)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for term in domain_terms:
                    assert term.lower() not in node.value.lower(), (
                        f"Auditor contains domain term '{term}' in string literal "
                        f"at line {node.lineno} — violates zero domain semantics"
                    )

    def test_auditor_works_with_arbitrary_capability_names(self, tmp_path):
        """Auditor works with completely arbitrary capability names."""
        # Create ledger with arbitrary names
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        ledger = {
            "capabilities": [
                {"id": "alpha.beta.gamma", "claimed_level": "REAL", "evidence": []},
                {"id": "xyz.123", "claimed_level": "MOCK", "evidence": []},
            ]
        }
        (ledger_dir / "ledger.json").write_text(json.dumps(ledger))

        # Create mapping with both capabilities
        mapping_dir = tmp_path / "mapping"
        mapping_dir.mkdir()
        mapping = {
            "mappings": [
                {
                    "capability_id": "alpha.beta.gamma",
                    "test_modules": ["tests/test_alpha.py"],
                    "min_test_cases": 3,
                },
                {
                    "capability_id": "xyz.123",
                    "test_modules": ["tests/test_xyz.py"],
                    "min_test_cases": 3,
                },
            ]
        }
        (mapping_dir / "mapping.json").write_text(json.dumps(mapping))

        # Create test files
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_alpha.py").write_text(
            "def test_1(): pass\ndef test_2(): pass\ndef test_3(): pass\n"
        )
        (tests_dir / "test_xyz.py").write_text(
            "def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\n"
        )

        auditor = EvidenceChainAuditor(
            worktree=tmp_path,
            ledger_path=ledger_dir / "ledger.json",
            mapping_path=mapping_dir / "mapping.json",
        )
        report = auditor.run_audit()
        assert report.total_capabilities == 2


# ── Test: Acceptance Criterion (f) — Idempotency ───────────────────────────


class TestIdempotency:
    """Criterion (f): Same state -> same output across 3 scans."""

    def test_three_scans_produce_same_output(self, auditor_with_synthetic):
        """Running audit 3 times produces identical results (except scan_id/time)."""
        reports = []
        for i in range(3):
            report = auditor_with_synthetic.run_audit(scan_id=f"idempotent-scan-{i}")
            reports.append(report)

        # Compare everything except scan_id and scanned_at
        for i in range(1, 3):
            r0 = reports[0].to_dict()
            ri = reports[i].to_dict()

            # Remove variable fields
            for r in (r0, ri):
                r.pop("scan_id", None)
                r.pop("scanned_at", None)

            assert r0 == ri, f"Scan {i} differs from scan 0"

    def test_findings_order_is_deterministic(self, auditor_with_synthetic):
        """Findings are in deterministic order (sorted by capability_id)."""
        report = auditor_with_synthetic.run_audit()
        cap_ids = [cr.capability_id for cr in report.capability_results]
        assert cap_ids == sorted(cap_ids), "Capability results not sorted"


# ── Test: Report Generator ──────────────────────────────────────────────────


class TestReportGenerator:
    """Tests for the report generator."""

    def test_generate_all_formats(self, auditor_with_synthetic, tmp_path):
        """Report generator produces all output formats."""
        report = auditor_with_synthetic.run_audit(scan_id="gen-test")
        generator = AuditReportGenerator(output_dir=tmp_path)
        paths = generator.generate_all(report)

        assert "json_report" in paths
        assert "markdown_summary" in paths
        assert "audit_events" in paths

        assert paths["json_report"].exists()
        assert paths["markdown_summary"].exists()
        assert paths["audit_events"].exists()

    def test_json_report_is_valid(self, auditor_with_synthetic, tmp_path):
        """Generated JSON report is valid and parseable."""
        report = auditor_with_synthetic.run_audit(scan_id="json-test")
        generator = AuditReportGenerator(output_dir=tmp_path)
        json_path = generator.write_json_report(report)

        with open(json_path) as f:
            parsed = json.load(f)
        assert parsed["scan_id"] == "json-test"

    def test_markdown_contains_findings(self, auditor_with_synthetic, tmp_path):
        """Markdown summary contains finding details."""
        report = auditor_with_synthetic.run_audit(scan_id="md-test")
        generator = AuditReportGenerator(output_dir=tmp_path)
        md_path = generator.write_markdown_summary(report)

        content = md_path.read_text()
        assert "# Evidence Chain Integrity Audit Report" in content
        assert "Findings" in content
        assert "synth." in content  # synthetic capability IDs

    def test_audit_events_jsonl_format(self, auditor_with_synthetic, tmp_path):
        """Audit events are written as valid JSONL."""
        report = auditor_with_synthetic.run_audit(scan_id="events-test")
        generator = AuditReportGenerator(output_dir=tmp_path)
        events_path = generator.write_audit_events(report)

        lines = events_path.read_text().strip().split("\n")
        assert len(lines) > 0
        for line in lines:
            event = json.loads(line)
            assert "event_type" in event
            assert event["event_type"] == "evidence_chain_break"
            assert "capability_id" in event
            assert "finding_type" in event


# ── Test: Full Audit Integration ────────────────────────────────────────────


class TestFullAuditIntegration:
    """Integration tests for the full audit pipeline."""

    def test_full_audit_produces_report(self, auditor_with_synthetic):
        """Full audit produces a complete report."""
        report = auditor_with_synthetic.run_audit(scan_id="integration-test")

        assert report.total_capabilities == 4
        assert report.consistent_count + report.inconsistent_count + report.unknown_count == 4
        assert len(report.findings) > 0
        assert report.summary  # non-empty

    def test_audit_counts_are_correct(self, auditor_with_synthetic):
        """Audit counts match actual results."""
        report = auditor_with_synthetic.run_audit()

        consistent = sum(1 for cr in report.capability_results if cr.status == "consistent")
        inconsistent = sum(1 for cr in report.capability_results if cr.status == "inconsistent")
        unknown = sum(1 for cr in report.capability_results if cr.status == "unknown")

        assert report.consistent_count == consistent
        assert report.inconsistent_count == inconsistent
        assert report.unknown_count == unknown

    def test_real_worktree_audit_runs(self):
        """Audit runs against the real worktree without crashing.

        This is a smoke test — we don't assert specific results since
        the real worktree state changes over time.
        """
        auditor = EvidenceChainAuditor(worktree=WORKTREE)
        report = auditor.run_audit(scan_id="real-worktree-smoke")
        assert report.total_capabilities > 0
        assert report.mapping_status in ("loaded", "missing_file", "parse_error")
