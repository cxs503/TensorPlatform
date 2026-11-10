"""Tests for capability_ledger_auto grader module."""
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capability_ledger_auto.registry import LEVELS
from capability_ledger_auto.scanner import ScanResult, CodeEvidence
from capability_ledger_auto.evidence_chain import EvidenceChain, FiveTuple, EvidenceItem
from capability_ledger_auto.grader import GradeDeterminer, GradeResult


def _make_chain(
    cap_id="test.cap",
    has_code=False, has_tests=False, has_reports=False,
    test_pass_rate=0.0, report_verdicts=None,
    five_present=0, chain_quality=0.0,
    scan_notes=None, code_evidence=None,
):
    """Helper to create a test EvidenceChain."""
    if report_verdicts is None:
        report_verdicts = []
    if scan_notes is None:
        scan_notes = []
    if code_evidence is None:
        code_evidence = []

    scan = ScanResult(
        capability_id=cap_id,
        code_evidence=code_evidence,
        code_count=len(code_evidence),
        test_count=1 if has_tests else 0,
        report_count=len(report_verdicts),
        has_implementation=has_code,
        has_tests=has_tests,
        has_reports=has_reports,
        test_pass_rate=test_pass_rate,
        report_verdicts=report_verdicts,
        scan_notes=scan_notes,
    )

    ft = FiveTuple()
    if five_present >= 1:
        ft.code = EvidenceItem(evidence_type="code", source_path="src/x.py",
                               content_hash="abc", description="code", is_present=True, quality_score=0.8)
    if five_present >= 2:
        ft.theory = EvidenceItem(evidence_type="theory", source_path="docs/x.md",
                                 content_hash="def", description="theory", is_present=True, quality_score=0.7)
    if five_present >= 3:
        ft.config = EvidenceItem(evidence_type="config", source_path="config/x.json",
                                 content_hash="ghi", description="config", is_present=True, quality_score=0.5)
    if five_present >= 4:
        ft.data = EvidenceItem(evidence_type="data", source_path="data/x.json",
                               content_hash="jkl", description="data", is_present=True, quality_score=0.6)
    if five_present >= 5:
        ft.report = EvidenceItem(evidence_type="report", source_path="reports/x.json",
                                 content_hash="mno", description="report", is_present=True, quality_score=1.0)

    return EvidenceChain(
        capability_id=cap_id,
        five_tuple=ft,
        scan_result=scan,
        chain_quality=chain_quality,
        notes=scan_notes,
    )


class TestGradeDeterminer:
    def test_not_supported(self):
        chain = _make_chain()
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "NOT_SUPPORTED"
        assert result.confidence == 0.95

    def test_stub(self):
        chain = _make_chain(
            has_code=True,
            scan_notes=["STUB markers in src/x.py: ['NotImplementedError']"],
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def stub_func", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "STUB"

    def test_mock_test_fail(self):
        chain = _make_chain(
            has_code=True, has_tests=True,
            test_pass_rate=0.5,
            five_present=2,
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def calc", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "MOCK"

    def test_mock_report_fail(self):
        chain = _make_chain(
            has_code=True, has_reports=True,
            report_verdicts=["FAIL"],
            five_present=2,
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def calc", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "MOCK"

    def test_mock_honest_fail(self):
        chain = _make_chain(
            has_code=True, has_reports=True,
            report_verdicts=["HONEST_FAIL"],
            five_present=2,
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def calc", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "MOCK"

    def test_real(self):
        chain = _make_chain(
            has_code=True, has_tests=True, has_reports=True,
            test_pass_rate=0.9,
            report_verdicts=["PASS"],
            five_present=5,
            chain_quality=0.8,
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def calc", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "REAL"
        assert result.confidence == 0.90

    def test_withheld_no_tests(self):
        chain = _make_chain(
            has_code=True,
            five_present=2,
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def calc", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "WITHHELD"

    def test_withheld_low_test_pass(self):
        """测试通过率 < 80% 但五元不足 → WITHHELD（不是 MOCK，因为 MOCK 需要有代码+验证失败）。

        注意：当有代码+测试但通过率<80%时，_is_mock 返回 True，所以这是 MOCK。
        但如果五元不足且没有报告 FAIL，则应该走 WITHHELD 路径。
        实际上 _is_mock 检查 test_pass_rate < 0.8 就返回 True，所以这个测试应该是 MOCK。
        """
        chain = _make_chain(
            has_code=True, has_tests=True,
            test_pass_rate=0.7,
            five_present=2,
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def calc", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        # 有代码+测试但通过率<80% → MOCK（验证失败）
        assert result.grade == "MOCK"

    def test_withheld_low_five_tuple(self):
        chain = _make_chain(
            has_code=True, has_tests=True, has_reports=True,
            test_pass_rate=0.9,
            report_verdicts=["PASS"],
            five_present=2,
            chain_quality=0.4,
            code_evidence=[CodeEvidence(
                file_path="src/x.py", line_number=1, match_type="function",
                matched_text="def calc", line_sha256="abc", file_sha256="def",
            )],
        )
        determiner = GradeDeterminer()
        result = determiner.determine(chain)
        assert result.grade == "WITHHELD"

    def test_determine_all(self):
        chains = {
            "cap1": _make_chain("cap1"),
            "cap2": _make_chain("cap2", has_code=True, has_tests=True, has_reports=True,
                                test_pass_rate=0.9, report_verdicts=["PASS"],
                                five_present=5, chain_quality=0.8,
                                code_evidence=[CodeEvidence(
                                    file_path="src/x.py", line_number=1, match_type="function",
                                    matched_text="def calc", line_sha256="abc", file_sha256="def",
                                )]),
        }
        determiner = GradeDeterminer()
        results = determiner.determine_all(chains)
        assert len(results) == 2
        assert results["cap1"].grade == "NOT_SUPPORTED"
        assert results["cap2"].grade == "REAL"

    def test_summary(self):
        results = {
            "cap1": GradeResult("cap1", "NOT_SUPPORTED", 0.95),
            "cap2": GradeResult("cap2", "REAL", 0.90),
            "cap3": GradeResult("cap3", "REAL", 0.85),
        }
        determiner = GradeDeterminer()
        summary = determiner.summary(results)
        assert summary["total_capabilities"] == 3
        assert summary["grade_tally"]["NOT_SUPPORTED"] == 1
        assert summary["grade_tally"]["REAL"] == 2
        assert summary["average_confidence"] > 0.8

    def test_grade_result_to_dict(self):
        result = GradeResult(
            capability_id="test.cap",
            grade="REAL",
            confidence=0.90,
            reasoning=["test"],
            evidence_summary={"code_count": 1},
        )
        d = result.to_dict()
        assert d["capability_id"] == "test.cap"
        assert d["grade"] == "REAL"
        assert d["confidence"] == 0.90
        assert d["reasoning"] == ["test"]
