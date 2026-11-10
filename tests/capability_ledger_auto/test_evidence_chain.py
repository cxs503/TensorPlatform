"""Tests for capability_ledger_auto evidence_chain module."""
import json
import pytest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capability_ledger_auto.registry import CapabilityDef
from capability_ledger_auto.scanner import ScanResult, CodeEvidence, ReportEvidence
from capability_ledger_auto.evidence_chain import (
    EvidenceChain, FiveTuple, EvidenceItem, build_evidence_chain,
    FIVE_TUPLE_TYPES,
)


class TestFiveTuple:
    def test_empty(self):
        ft = FiveTuple()
        assert ft.present_count == 0
        assert ft.completeness == 0.0

    def test_full(self):
        item = EvidenceItem(
            evidence_type="code", source_path="test.py",
            content_hash="abc", description="test", is_present=True, quality_score=1.0,
        )
        ft = FiveTuple(
            theory=item, code=item, config=item, data=item, report=item,
        )
        assert ft.present_count == 5
        assert ft.completeness == 1.0

    def test_partial(self):
        item = EvidenceItem(
            evidence_type="code", source_path="test.py",
            content_hash="abc", description="test", is_present=True, quality_score=0.8,
        )
        ft = FiveTuple(code=item, report=item)
        assert ft.present_count == 2
        assert ft.completeness == 0.4

    def test_to_dict(self):
        item = EvidenceItem(
            evidence_type="code", source_path="test.py",
            content_hash="abc", description="test", is_present=True, quality_score=0.8,
        )
        ft = FiveTuple(code=item)
        d = ft.to_dict()
        assert d["present_count"] == 1
        assert d["completeness"] == 0.2
        assert d["code"] is not None
        assert d["theory"] is None


class TestEvidenceChain:
    def test_to_dict(self):
        ft = FiveTuple()
        scan = ScanResult(capability_id="test.cap")
        chain = EvidenceChain(
            capability_id="test.cap",
            five_tuple=ft,
            scan_result=scan,
            missing_items=["theory", "config", "data", "report"],
            chain_quality=0.2,
            notes=["test note"],
        )
        d = chain.to_dict()
        assert d["capability_id"] == "test.cap"
        assert d["missing_items"] == ["theory", "config", "data", "report"]
        assert d["chain_quality"] == 0.2
        assert d["notes"] == ["test note"]


class TestBuildEvidenceChain:
    def test_build_with_code_only(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "resistance.py").write_text("def calc_resistance(): pass\n")

        cap = CapabilityDef(
            capability_id="test.resistance",
            label="Test",
            description="Test",
            code_patterns=["resistance"],
        )
        scan = ScanResult(
            capability_id="test.resistance",
            code_evidence=[CodeEvidence(
                file_path="src/resistance.py", line_number=1,
                match_type="function", matched_text="def calc_resistance",
                line_sha256="abc", file_sha256="def",
            )],
            code_count=1,
            has_implementation=True,
        )

        chain = build_evidence_chain(cap, scan, tmp_path)
        assert chain.capability_id == "test.resistance"
        assert chain.five_tuple.code is not None
        assert chain.five_tuple.code.is_present is True

    def test_build_with_report(self, tmp_path):
        reports_dir = tmp_path / "tensor_reports"
        reports_dir.mkdir()
        report = {"scenario_key": "m1", "verdict": "PASS", "capability": "resistance"}
        (reports_dir / "m1_report.json").write_text(json.dumps(report))

        cap = CapabilityDef(
            capability_id="test.resistance",
            label="Test",
            description="Test",
            code_patterns=["resistance"],
            report_patterns=["resistance"],
        )
        scan = ScanResult(
            capability_id="test.resistance",
            code_evidence=[CodeEvidence(
                file_path="src/resistance.py", line_number=1,
                match_type="function", matched_text="def calc_resistance",
                line_sha256="abc", file_sha256="def",
            )],
            report_evidence=[ReportEvidence(
                file_path="tensor_reports/m1_report.json",
                scenario_key="m1", verdict="PASS",
                capability_match="resistance", file_sha256="ghi",
            )],
            code_count=1,
            report_count=1,
            has_implementation=True,
            has_reports=True,
            report_verdicts=["PASS"],
        )

        chain = build_evidence_chain(cap, scan, tmp_path)
        assert chain.five_tuple.report is not None
        assert chain.five_tuple.report.is_present is True
        assert chain.five_tuple.report.quality_score == 1.0  # PASS = 1.0

    def test_build_with_fail_report(self, tmp_path):
        reports_dir = tmp_path / "tensor_reports"
        reports_dir.mkdir()
        report = {"scenario_key": "m2", "verdict": "FAIL", "capability": "resistance"}
        (reports_dir / "m2_report.json").write_text(json.dumps(report))

        cap = CapabilityDef(
            capability_id="test.resistance",
            label="Test",
            description="Test",
            code_patterns=["resistance"],
            report_patterns=["resistance"],
        )
        scan = ScanResult(
            capability_id="test.resistance",
            code_evidence=[CodeEvidence(
                file_path="src/resistance.py", line_number=1,
                match_type="function", matched_text="def calc_resistance",
                line_sha256="abc", file_sha256="def",
            )],
            report_evidence=[ReportEvidence(
                file_path="tensor_reports/m2_report.json",
                scenario_key="m2", verdict="FAIL",
                capability_match="resistance", file_sha256="ghi",
            )],
            code_count=1,
            report_count=1,
            has_implementation=True,
            has_reports=True,
            report_verdicts=["FAIL"],
        )

        chain = build_evidence_chain(cap, scan, tmp_path)
        assert chain.five_tuple.report is not None
        assert chain.five_tuple.report.quality_score == 0.0  # FAIL = 0.0
