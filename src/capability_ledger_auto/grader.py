"""等级判定器（grade determiner）。

等级判定规则：
- REAL       = 五元齐全 + 测试通过(>80%) + 报告 verdict=PASS
- MOCK       = 有代码但测试失败或报告 FAIL
- STUB       = 仅有接口声明无实现（NotImplementedError/TODO 等）
- WITHHELD   = 有实现但无验证证据
- NOT_SUPPORTED = 无任何痕迹

零领域语义：仅通过证据链和扫描结果判定，不依赖任何域词条。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence_chain import EvidenceChain
from .registry import LEVEL_RANK, LEVELS
from .scanner import ScanResult

TEST_PASS_THRESHOLD = 0.80
REAL_FIVE_TUPLE_MIN = 4
REAL_CHAIN_QUALITY_MIN = 0.6


@dataclass
class GradeResult:
    capability_id: str
    grade: str
    confidence: float
    reasoning: list[str] = field(default_factory=list)
    evidence_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "grade": self.grade,
            "confidence": round(self.confidence, 4),
            "reasoning": self.reasoning,
            "evidence_summary": self.evidence_summary,
        }


class GradeDeterminer:
    def determine(self, chain: EvidenceChain) -> GradeResult:
        scan = chain.scan_result
        five = chain.five_tuple
        reasoning = []

        # Rule 1: NOT_SUPPORTED
        if (not scan.has_implementation and not scan.has_tests and
                not scan.has_reports and five.present_count == 0):
            reasoning.append("无任何代码/测试/报告痕迹")
            return GradeResult(
                capability_id=chain.capability_id, grade="NOT_SUPPORTED",
                confidence=0.95, reasoning=reasoning,
                evidence_summary={"code_count": scan.code_count, "test_count": scan.test_count,
                                  "report_count": scan.report_count, "five_tuple_present": five.present_count},
            )

        # Rule 2: STUB
        if self._is_stub(scan, chain):
            reasoning.append("检测到 STUB 标记（NotImplementedError/TODO/placeholder）")
            reasoning.append("无实质实现代码")
            return GradeResult(
                capability_id=chain.capability_id, grade="STUB", confidence=0.85,
                reasoning=reasoning,
                evidence_summary={"code_count": scan.code_count,
                                  "stub_markers": [n for n in scan.scan_notes if "STUB" in n],
                                  "five_tuple_present": five.present_count},
            )

        # Rule 3: MOCK
        if self._is_mock(scan, chain):
            reasoning.append("有代码实现但验证失败")
            if scan.test_pass_rate < TEST_PASS_THRESHOLD:
                reasoning.append(f"测试通过率 {scan.test_pass_rate:.1%} < {TEST_PASS_THRESHOLD:.0%}")
            fail_v = [v for v in scan.report_verdicts if v in ("FAIL", "HONEST_FAIL")]
            if fail_v:
                reasoning.append(f"报告 verdict 包含 FAIL: {fail_v}")
            mock_m = [n for n in scan.scan_notes if "MOCK" in n]
            if mock_m:
                reasoning.append(f"检测到 MOCK 标记: {mock_m}")
            return GradeResult(
                capability_id=chain.capability_id, grade="MOCK", confidence=0.80,
                reasoning=reasoning,
                evidence_summary={"code_count": scan.code_count, "test_pass_rate": scan.test_pass_rate,
                                  "report_verdicts": scan.report_verdicts, "mock_markers": mock_m},
            )

        # Rule 5: REAL
        if self._is_real(scan, chain):
            reasoning.append("五元证据齐全")
            reasoning.append(f"测试通过率 {scan.test_pass_rate:.1%} >= {TEST_PASS_THRESHOLD:.0%}")
            reasoning.append("报告 verdict 为 PASS")
            reasoning.append(f"证据链质量 {chain.chain_quality:.2f} >= {REAL_CHAIN_QUALITY_MIN}")
            return GradeResult(
                capability_id=chain.capability_id, grade="REAL", confidence=0.90,
                reasoning=reasoning,
                evidence_summary={"code_count": scan.code_count, "test_count": scan.test_count,
                                  "report_count": scan.report_count, "test_pass_rate": scan.test_pass_rate,
                                  "report_verdicts": scan.report_verdicts,
                                  "five_tuple_present": five.present_count, "chain_quality": chain.chain_quality},
            )

        # Rule 4: WITHHELD
        reasoning.append("有代码实现但验证证据不足")
        if not scan.has_tests:
            reasoning.append("无测试覆盖")
        if not scan.has_reports:
            reasoning.append("无报告验证")
        if scan.test_pass_rate < TEST_PASS_THRESHOLD:
            reasoning.append(f"测试通过率 {scan.test_pass_rate:.1%} < {TEST_PASS_THRESHOLD:.0%}")
        if five.present_count < REAL_FIVE_TUPLE_MIN:
            reasoning.append(f"五元证据仅 {five.present_count}/5，不足 {REAL_FIVE_TUPLE_MIN}")
        return GradeResult(
            capability_id=chain.capability_id, grade="WITHHELD", confidence=0.70,
            reasoning=reasoning,
            evidence_summary={"code_count": scan.code_count, "test_count": scan.test_count,
                              "report_count": scan.report_count, "test_pass_rate": scan.test_pass_rate,
                              "report_verdicts": scan.report_verdicts,
                              "five_tuple_present": five.present_count, "chain_quality": chain.chain_quality},
        )

    def _is_stub(self, scan: ScanResult, chain: EvidenceChain) -> bool:
        stub_notes = [n for n in scan.scan_notes if "STUB" in n]
        if stub_notes:
            return True
        if scan.code_count > 0 and not scan.has_tests and not scan.has_reports:
            for ev in scan.code_evidence:
                if ev.match_type in ("function", "class"):
                    try:
                        from pathlib import Path
                        content = Path(ev.file_path).read_text(encoding="utf-8", errors="replace")
                        if "NotImplementedError" in content:
                            return True
                    except Exception:
                        pass
        return False

    def _is_mock(self, scan: ScanResult, chain: EvidenceChain) -> bool:
        mock_notes = [n for n in scan.scan_notes if "MOCK" in n]
        if mock_notes:
            return True
        if scan.has_tests and scan.test_pass_rate < TEST_PASS_THRESHOLD:
            return True
        fail_v = [v for v in scan.report_verdicts if v in ("FAIL", "HONEST_FAIL")]
        if fail_v:
            return True
        return False

    def _is_real(self, scan: ScanResult, chain: EvidenceChain) -> bool:
        if chain.five_tuple.present_count < REAL_FIVE_TUPLE_MIN:
            return False
        if scan.has_tests and scan.test_pass_rate < TEST_PASS_THRESHOLD:
            return False
        if scan.has_reports:
            fail_v = [v for v in scan.report_verdicts if v in ("FAIL", "HONEST_FAIL")]
            if fail_v:
                return False
            pass_v = [v for v in scan.report_verdicts if v == "PASS"]
            if not pass_v:
                return False
        if chain.chain_quality < REAL_CHAIN_QUALITY_MIN:
            return False
        return True

    def determine_all(self, chains: dict[str, EvidenceChain]) -> dict[str, GradeResult]:
        return {cid: self.determine(chain) for cid, chain in chains.items()}

    def summary(self, results: dict[str, GradeResult]) -> dict[str, Any]:
        tally = {lv: 0 for lv in LEVELS}
        for r in results.values():
            tally[r.grade] = tally.get(r.grade, 0) + 1
        return {
            "total_capabilities": len(results),
            "grade_tally": tally,
            "average_confidence": round(sum(r.confidence for r in results.values()) / len(results), 4) if results else 0.0,
        }
