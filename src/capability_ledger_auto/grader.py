"""等级判定器（grade determiner）。

等级判定规则（与战役判据 AC1–AC2 对齐，且**每个分支都写清证据强度**）：

- NOT_SUPPORTED = 无任何代码/测试/报告/五元痕迹
- MOCK          = 有代码但证据反过来：报告最新裁决为 MOCK/FAIL/HONEST_FAIL，或匹配到的
                  实现节点体内出现 MOCK 标记（随机数/假数据），或测试执行失败
- STUB          = 匹配到的实现全是"只有签名没有实现"（docstring/pass/.../NotImplementedError）
- REAL          = 五元齐全 + 测试 ≥80%（**且为真实执行证据**）+ 报告最新裁决 PASS/REAL
- WITHHELD      = 有实现但验证证据不足/证据互相矛盾未定论

零领域语义：仅通过证据链和扫描结果判定，不依赖任何域词条。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence_chain import EvidenceChain
from .registry import LEVEL_RANK, LEVELS
from .scanner import FAILING_LEVELS, PASSING_LEVELS, ScanResult

# 判级策略（可外置覆盖；默认值 = 战役判据口径）
DEFAULT_POLICY: dict[str, Any] = {
    "test_pass_threshold": 0.80,
    "real_five_tuple_min": 5,          # 五元齐全（战役判据：五元齐全 + 测试 + 报告 PASS）
    "real_chain_quality_min": 0.60,
    "require_executed_tests_for_real": True,  # 静态推断不得冒充验证证据
    "mock_from_static_failures": True,        # 静态推断失败也可降级 MOCK（但记 strength=static）
}
TEST_PASS_THRESHOLD = DEFAULT_POLICY["test_pass_threshold"]
REAL_FIVE_TUPLE_MIN = DEFAULT_POLICY["real_five_tuple_min"]
REAL_CHAIN_QUALITY_MIN = DEFAULT_POLICY["real_chain_quality_min"]


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
    def __init__(self, root: Path | str | None = None, policy: dict[str, Any] | None = None):
        self.root = Path(root) if root is not None else None
        self.policy = dict(DEFAULT_POLICY)
        if policy:
            self.policy.update(policy)

    # -- 摘要 ------------------------------------------------------------
    @staticmethod
    def _summary(scan: ScanResult, chain: EvidenceChain | None = None) -> dict[str, Any]:
        out = {
            "code_count": scan.code_count,
            "substantive_code_count": scan.substantive_code_count,
            "stub_only_code_count": scan.stub_only_code_count,
            "test_count": scan.test_count,
            "report_count": scan.report_count,
            "test_pass_rate": scan.test_pass_rate,
            "test_evidence_source": scan.test_evidence_source,
            "executed_test_files": scan.executed_test_files,
            "executed_passed": scan.executed_passed,
            "executed_failed": scan.executed_failed,
            "mock_markers": scan.mock_markers[:10],
            "adjudicated_level": scan.adjudicated_level,
            "adjudicated_as_of": scan.adjudicated_as_of,
            "adjudicated_source": scan.adjudicated_source,
            "report_verdicts": scan.report_verdicts,
        }
        if chain is not None:
            out["five_tuple_present"] = chain.five_tuple.present_count
            out["five_tuple_missing"] = list(chain.missing_items)
            out["chain_quality"] = chain.chain_quality
        return out

    # -- 主判定 ----------------------------------------------------------
    def determine(self, chain: EvidenceChain) -> GradeResult:
        scan = chain.scan_result
        five = chain.five_tuple
        reasoning: list[str] = []

        has_any_code = any(
            e.match_type in ("function", "class", "module", "text") for e in scan.code_evidence
        )

        # Rule 0: NOT_SUPPORTED
        if (not has_any_code and not scan.has_tests and not scan.has_reports
                and five.present_count == 0):
            reasoning.append("无任何代码/测试/报告/五元痕迹")
            return GradeResult(
                chain.capability_id, "NOT_SUPPORTED", 0.95, reasoning, self._summary(scan, chain),
            )

        # 证据强度归一口径：未标注来源但有测试 → 视为静态推断（不得冒充执行证据）
        if not scan.test_evidence_source and scan.has_tests:
            scan.test_evidence_source = "static"
        # 报告裁决归一口径：无显式裁决字段时用报告 verdict 列表末项（按 as_of 排序后的最新）
        if scan.has_reports and not scan.adjudicated_level and scan.report_verdicts:
            scan.adjudicated_level = scan.report_verdicts[-1]

        # Rule 1: MOCK（证据反向）
        mock_reasons = self._mock_reasons(scan)
        if mock_reasons:
            reasoning.extend(mock_reasons)
            return GradeResult(
                chain.capability_id, "MOCK", 0.85, reasoning, self._summary(scan, chain),
            )

        # Rule 2: STUB（只有签名，没有实现）
        subst, stub_only, mock_marks = self._node_counts(scan)
        if subst == 0 and stub_only > 0:
            reasoning.append(
                f"匹配到的 {stub_only} 处实现全为接口声明"
                "（docstring/pass/.../NotImplementedError），实质实现 0 处"
            )
            return GradeResult(
                chain.capability_id, "STUB", 0.85, reasoning, self._summary(scan, chain),
            )

        # Rule 3: REAL（五元齐全 + 真实执行测试通过 + 报告最新裁决 PASS）
        real_blockers = self._real_blockers(scan, chain)
        if not real_blockers:
            reasoning.append("五元齐全（5/5）")
            reasoning.append(
                f"测试真实执行通过率 {scan.test_pass_rate:.1%} >= "
                f"{self.policy['test_pass_threshold']:.0%}"
                f"（{scan.executed_test_files} 个文件，{scan.executed_passed} 通过）"
            )
            reasoning.append(f"报告最新裁决 {scan.adjudicated_level}（as_of={scan.adjudicated_as_of}）")
            reasoning.append(f"证据链质量 {chain.chain_quality:.2f} >= {self.policy['real_chain_quality_min']}")
            return GradeResult(
                chain.capability_id, "REAL", 0.90, reasoning, self._summary(scan, chain),
            )

        # Rule 4: WITHHELD（有实现/有部分证据，但不足以判 REAL）
        reasoning.append("有痕迹但验证证据不足，不足以判 REAL")
        reasoning.extend(real_blockers)
        return GradeResult(
            chain.capability_id, "WITHHELD", 0.70, reasoning, self._summary(scan, chain),
        )

    # -- 规则实现 --------------------------------------------------------
    @staticmethod
    def _node_counts(scan: ScanResult) -> tuple[int, int, list[str]]:
        """节点级实现的 (实质, 仅接口, MOCK标记)；手工构造的 ScanResult 也能判定。"""
        subst = scan.substantive_code_count
        stub_only = scan.stub_only_code_count
        mock_marks = list(scan.mock_markers)
        if not subst and not stub_only:
            for e in scan.code_evidence:
                if e.match_type in ("function", "class", "text"):
                    if e.is_stub_like:
                        stub_only += 1
                    else:
                        subst += 1
        if not mock_marks:
            for e in scan.code_evidence:
                for m in e.mock_markers:
                    mock_marks.append(f"{e.file_path}:{e.matched_text}:{m}")
        return subst, stub_only, mock_marks

    def _mock_reasons(self, scan: ScanResult) -> list[str]:
        reasons: list[str] = []
        adj = scan.adjudicated_level
        if adj in FAILING_LEVELS:
            reasons.append(
                f"报告最新裁决为 {adj}（{scan.adjudicated_source}，as_of={scan.adjudicated_as_of}）："
                "实现存在但证据反向"
            )
        _, _, mock_marks = self._node_counts(scan)
        if mock_marks:
            reasons.append(f"匹配节点体内出现 MOCK 标记 {len(mock_marks)} 处: {mock_marks[:3]}")
        if scan.test_evidence_source == "executed" and scan.test_pass_rate < self.policy["test_pass_threshold"]:
            reasons.append(
                f"测试真实执行通过率 {scan.test_pass_rate:.1%} < "
                f"{self.policy['test_pass_threshold']:.0%}"
                f"（{scan.executed_failed} 失败）"
            )
        if (self.policy["mock_from_static_failures"]
                and scan.test_evidence_source == "static"
                and scan.test_pass_rate < self.policy["test_pass_threshold"]):
            reasons.append(
                f"测试静态推断通过率 {scan.test_pass_rate:.1%} < "
                f"{self.policy['test_pass_threshold']:.0%}（strength=static，未执行）"
            )
        return reasons

    def _real_blockers(self, scan: ScanResult, chain: EvidenceChain) -> list[str]:
        blockers: list[str] = []
        if chain.five_tuple.present_count < self.policy["real_five_tuple_min"]:
            blockers.append(
                f"五元不全：{chain.five_tuple.present_count}/5，缺 {chain.missing_items}"
            )
        strength = scan.test_evidence_source
        if strength == "executed":
            if scan.test_pass_rate < self.policy["test_pass_threshold"]:
                blockers.append(
                    f"测试真实执行通过率 {scan.test_pass_rate:.1%} < "
                    f"{self.policy['test_pass_threshold']:.0%}"
                )
        elif strength == "static":
            if self.policy["require_executed_tests_for_real"]:
                blockers.append("仅有静态推断的测试信号（未真实执行），不得据此判 REAL")
            elif scan.test_pass_rate < self.policy["test_pass_threshold"]:
                blockers.append(
                    f"测试静态通过率 {scan.test_pass_rate:.1%} < "
                    f"{self.policy['test_pass_threshold']:.0%}"
                )
        else:
            blockers.append("无测试证据")
        if not scan.has_reports:
            blockers.append("无可关联报告")
        elif scan.adjudicated_level not in PASSING_LEVELS:
            blockers.append(
                f"报告最新裁决 {scan.adjudicated_level or 'UNKNOWN'} 非 PASS/REAL"
                f"（{scan.adjudicated_source or 'n/a'}）"
            )
        if chain.chain_quality < self.policy["real_chain_quality_min"]:
            blockers.append(
                f"证据链质量 {chain.chain_quality:.2f} < {self.policy['real_chain_quality_min']}"
            )
        return blockers

    # -- 批量 ------------------------------------------------------------
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
            "policy": dict(self.policy),
        }