"""裁定引擎：按预声明阈值自动判定 REAL/MOCK/STUB/WITHHELD/NOT_SUPPORTED。

裁定规则（顺序即优先级，全部显式、无默认放行）：

    R0  无证据              → NOT_SUPPORTED（无任何证据项）
    R1  仅接口证据           → STUB（只有 interface 类型证据）
    R2  有 MOCK 信号         → MOCK（硬编码/随机值信号）
    R3  证据不足             → WITHHELD（有证据但不满足 REAL 条件）
    R4  误差超 15%           → MOCK（精度不可接受）
    R5  误差 3-15%           → WITHHELD（有条件通过，需更多证据）
    R6  误差 ≤3% + 独立路径  → REAL（通过所有门）
    R7  自述等级 > 证据等级  → 降级（OVERCLAIM 门，由 overclaim_gate 处理）

设计原则：
- 纯函数：result = adjudicate(evidence_items, thresholds, claimed_level)
- 预声明阈值：容差、最小证据数等必须在裁定前声明，不可事后调整
- 审计追踪：每次裁定产生带 sha256 签名的 AdjudicationResult
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .evidence_collector import EvidenceItem

# ── 证据等级 ──────────────────────────────────────────────────────────────

LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_RANK: Dict[str, int] = {name: i for i, name in enumerate(LEVELS)}


# ── 预声明阈值 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdjudicationThresholds:
    """预声明的裁定阈值（不可事后调整）。"""
    # 误差门
    real_error_pct: float = 3.0       # REAL 要求误差 ≤ 3%
    withheld_error_pct: float = 15.0  # WITHHELD 上限 15%
    mock_error_pct: float = 15.0      # 超过 15% 判 MOCK

    # 证据量门
    min_evidence_count: int = 1       # 最少证据项数
    min_independent_paths: int = 1    # 最少独立路径数
    min_passed_tests: int = 1         # 最少通过测试数

    # 负控门
    require_negative_control: bool = False  # 是否要求负控证据

    # 时间门
    max_evidence_age_days: int = 365  # 证据最大有效期（天）

    def sha256(self) -> str:
        """阈值指纹（用于审计追踪）。"""
        blob = json.dumps({
            "real_error_pct": self.real_error_pct,
            "withheld_error_pct": self.withheld_error_pct,
            "mock_error_pct": self.mock_error_pct,
            "min_evidence_count": self.min_evidence_count,
            "min_independent_paths": self.min_independent_paths,
            "min_passed_tests": self.min_passed_tests,
            "require_negative_control": self.require_negative_control,
            "max_evidence_age_days": self.max_evidence_age_days,
        }, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# 默认阈值
DEFAULT_THRESHOLDS = AdjudicationThresholds()


# ── 裁定结果 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdjudicationResult:
    """单次裁定结果。"""
    capability_id: str
    derived_level: str
    claimed_level: Optional[str]
    confidence: float  # 0.0 - 1.0
    rule_fired: str  # 触发裁定的规则 ID
    evidence_count: int
    passed_count: int
    failed_count: int
    max_error_pct: Optional[float]
    min_error_pct: Optional[float]
    thresholds_sha256: str
    evidence_sha256: str  # 证据集合指纹
    trace: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def is_overclaim(self) -> bool:
        """自述等级是否高于证据等级。"""
        if self.claimed_level is None:
            return False
        return LEVEL_RANK.get(self.claimed_level, 0) > LEVEL_RANK.get(self.derived_level, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "derived_level": self.derived_level,
            "claimed_level": self.claimed_level,
            "confidence": self.confidence,
            "rule_fired": self.rule_fired,
            "evidence_count": self.evidence_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "max_error_pct": self.max_error_pct,
            "min_error_pct": self.min_error_pct,
            "thresholds_sha256": self.thresholds_sha256,
            "evidence_sha256": self.evidence_sha256,
            "trace": self.trace,
            "is_overclaim": self.is_overclaim,
            "timestamp": self.timestamp,
        }


# ── 裁定引擎 ──────────────────────────────────────────────────────────────

class AdjudicationEngine:
    """自动化裁定引擎。

    Parameters
    ----------
    thresholds : AdjudicationThresholds, optional
        预声明阈值。默认使用 DEFAULT_THRESHOLDS。
    """

    def __init__(self, thresholds: Optional[AdjudicationThresholds] = None):
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self._results: Dict[str, AdjudicationResult] = {}

    def adjudicate(self, capability_id: str, evidence_items: List[EvidenceItem],
                   claimed_level: Optional[str] = None) -> AdjudicationResult:
        """对单个能力进行裁定。

        Parameters
        ----------
        capability_id : str
            能力 ID。
        evidence_items : list[EvidenceItem]
            该能力的证据项列表。
        claimed_level : str, optional
            自述等级（用于 OVERCLAIM 检测）。

        Returns
        -------
        AdjudicationResult
        """
        trace = []
        t = self.thresholds

        # R0: 无证据
        if not evidence_items:
            result = AdjudicationResult(
                capability_id=capability_id,
                derived_level="NOT_SUPPORTED",
                claimed_level=claimed_level,
                confidence=1.0,
                rule_fired="R0_NO_EVIDENCE",
                evidence_count=0,
                passed_count=0,
                failed_count=0,
                max_error_pct=None,
                min_error_pct=None,
                thresholds_sha256=t.sha256(),
                evidence_sha256=self._evidence_hash([]),
                trace=[{"rule": "R0_NO_EVIDENCE", "status": "triggered",
                        "detail": "无任何证据项"}],
            )
            self._results[capability_id] = result
            return result

        # 分析证据
        passed_items = [e for e in evidence_items if e.passed]
        failed_items = [e for e in evidence_items if not e.passed]
        error_values = [e.error_pct for e in evidence_items if e.error_pct is not None]
        grade_hints = [e.grade_hint for e in evidence_items]

        max_error = max(error_values) if error_values else None
        min_error = min(error_values) if error_values else None

        # R1: 仅接口证据
        if all(e.source_type == "contract" and "interface" in e.metric_name
               for e in evidence_items):
            result = AdjudicationResult(
                capability_id=capability_id,
                derived_level="STUB",
                claimed_level=claimed_level,
                confidence=0.8,
                rule_fired="R1_INTERFACE_ONLY",
                evidence_count=len(evidence_items),
                passed_count=len(passed_items),
                failed_count=len(failed_items),
                max_error_pct=max_error,
                min_error_pct=min_error,
                thresholds_sha256=t.sha256(),
                evidence_sha256=self._evidence_hash(evidence_items),
                trace=[{"rule": "R1_INTERFACE_ONLY", "status": "triggered",
                        "detail": "仅有接口类型证据"}],
            )
            self._results[capability_id] = result
            return result

        # R2: 有 MOCK 信号
        mock_hints = [h for h in grade_hints if h == "MOCK"]
        if mock_hints and len(mock_hints) > len(evidence_items) * 0.5:
            result = AdjudicationResult(
                capability_id=capability_id,
                derived_level="MOCK",
                claimed_level=claimed_level,
                confidence=0.9,
                rule_fired="R2_MOCK_SIGNAL",
                evidence_count=len(evidence_items),
                passed_count=len(passed_items),
                failed_count=len(failed_items),
                max_error_pct=max_error,
                min_error_pct=min_error,
                thresholds_sha256=t.sha256(),
                evidence_sha256=self._evidence_hash(evidence_items),
                trace=[{"rule": "R2_MOCK_SIGNAL", "status": "triggered",
                        "detail": f"MOCK 信号占比 {len(mock_hints)}/{len(evidence_items)}"}],
            )
            self._results[capability_id] = result
            return result

        # R4: 误差超 15%
        if max_error is not None and max_error > t.mock_error_pct:
            result = AdjudicationResult(
                capability_id=capability_id,
                derived_level="MOCK",
                claimed_level=claimed_level,
                confidence=0.85,
                rule_fired="R4_ERROR_EXCEEDS_MOCK",
                evidence_count=len(evidence_items),
                passed_count=len(passed_items),
                failed_count=len(failed_items),
                max_error_pct=max_error,
                min_error_pct=min_error,
                thresholds_sha256=t.sha256(),
                evidence_sha256=self._evidence_hash(evidence_items),
                trace=[
                    {"rule": "R4_ERROR_EXCEEDS_MOCK", "status": "triggered",
                     "detail": f"最大误差 {max_error:.1f}% > {t.mock_error_pct}%"},
                ],
            )
            self._results[capability_id] = result
            return result

        # R5: 误差 3-15%
        if max_error is not None and max_error > t.real_error_pct:
            result = AdjudicationResult(
                capability_id=capability_id,
                derived_level="WITHHELD",
                claimed_level=claimed_level,
                confidence=0.6,
                rule_fired="R5_ERROR_IN_WITHHELD_RANGE",
                evidence_count=len(evidence_items),
                passed_count=len(passed_items),
                failed_count=len(failed_items),
                max_error_pct=max_error,
                min_error_pct=min_error,
                thresholds_sha256=t.sha256(),
                evidence_sha256=self._evidence_hash(evidence_items),
                trace=[
                    {"rule": "R5_ERROR_IN_WITHHELD_RANGE", "status": "triggered",
                     "detail": f"最大误差 {max_error:.1f}% 在 ({t.real_error_pct}, {t.withheld_error_pct}] 区间"},
                ],
            )
            self._results[capability_id] = result
            return result

        # R6: 误差 ≤3% + 足够证据 → REAL
        if (max_error is not None and max_error <= t.real_error_pct and
                len(evidence_items) >= t.min_evidence_count and
                len(passed_items) >= t.min_passed_tests):
            confidence = min(1.0, 0.7 + 0.1 * len(passed_items))
            result = AdjudicationResult(
                capability_id=capability_id,
                derived_level="REAL",
                claimed_level=claimed_level,
                confidence=confidence,
                rule_fired="R6_REAL_ACHIEVED",
                evidence_count=len(evidence_items),
                passed_count=len(passed_items),
                failed_count=len(failed_items),
                max_error_pct=max_error,
                min_error_pct=min_error,
                thresholds_sha256=t.sha256(),
                evidence_sha256=self._evidence_hash(evidence_items),
                trace=[
                    {"rule": "R6_REAL_ACHIEVED", "status": "triggered",
                     "detail": f"误差 {max_error:.1f}% ≤ {t.real_error_pct}%, "
                               f"通过 {len(passed_items)}/{len(evidence_items)} 项"},
                ],
            )
            self._results[capability_id] = result
            return result

        # R3: 证据不足（默认降级）
        result = AdjudicationResult(
            capability_id=capability_id,
            derived_level="WITHHELD",
            claimed_level=claimed_level,
            confidence=0.5,
            rule_fired="R3_INSUFFICIENT_EVIDENCE",
            evidence_count=len(evidence_items),
            passed_count=len(passed_items),
            failed_count=len(failed_items),
            max_error_pct=max_error,
            min_error_pct=min_error,
            thresholds_sha256=t.sha256(),
            evidence_sha256=self._evidence_hash(evidence_items),
            trace=[
                {"rule": "R3_INSUFFICIENT_EVIDENCE", "status": "triggered",
                 "detail": f"有 {len(evidence_items)} 项证据但不满足 REAL 条件"},
            ],
        )
        self._results[capability_id] = result
        return result

    def adjudicate_all(self, evidence_by_cap: Dict[str, List[EvidenceItem]],
                       claimed_levels: Optional[Dict[str, str]] = None) -> Dict[str, AdjudicationResult]:
        """批量裁定所有能力。"""
        claimed_levels = claimed_levels or {}
        results = {}
        for cap_id, items in evidence_by_cap.items():
            results[cap_id] = self.adjudicate(cap_id, items, claimed_levels.get(cap_id))
        self._results.update(results)
        return results

    def get_result(self, capability_id: str) -> Optional[AdjudicationResult]:
        return self._results.get(capability_id)

    def all_results(self) -> Dict[str, AdjudicationResult]:
        return dict(self._results)

    def consistency_rate(self, manual_audit: Dict[str, str]) -> float:
        """计算与人工审计的一致率。

        Parameters
        ----------
        manual_audit : dict
            {capability_id: manual_grade}

        Returns
        -------
        float
            一致率 (0.0 - 1.0)
        """
        if not manual_audit:
            return 0.0
        matches = 0
        total = 0
        for cap_id, manual_grade in manual_audit.items():
            result = self._results.get(cap_id)
            if result is None:
                continue
            total += 1
            if result.derived_level == manual_grade:
                matches += 1
        return matches / total if total > 0 else 0.0

    @staticmethod
    def _evidence_hash(items: List[EvidenceItem]) -> str:
        """证据集合指纹。"""
        if not items:
            return hashlib.sha256(b"[]").hexdigest()
        blob = json.dumps([i.to_dict() for i in sorted(items, key=lambda x: x.sha256)],
                          sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
