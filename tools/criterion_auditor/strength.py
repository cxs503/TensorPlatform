"""判据强度审计器：检出结构性恒等式/恒真判据。

自动检出以下类型的弱判据：
- 恒真式（always true, always satisfied）
- 无数字判据（没有具体数值阈值）
- 模糊阈值（small, large, reasonable）
- 自指式（this method is correct）
- 循环验证（validate by itself）

并生成可执行见证用例。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .config_loader import AuditorConfig


@dataclass(frozen=True)
class TautologyHit:
    """检出的恒真式命中。"""

    pattern_id: str
    description: str
    matched_text: str
    severity: str = "high"  # high/medium/low

    def as_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "matched_text": self.matched_text,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class WitnessCase:
    """可执行见证用例。"""

    case_id: str
    description: str
    input_spec: dict = field(default_factory=dict)
    expected_behavior: str = ""

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "input_spec": self.input_spec,
            "expected_behavior": self.expected_behavior,
        }


@dataclass
class StrengthAudit:
    """判据强度审计结果。"""

    criterion_text: str
    tautology_hits: list[TautologyHit] = field(default_factory=list)
    has_numeric_criterion: bool = False
    numeric_values: list[str] = field(default_factory=list)
    strength_score: float = 1.0  # 1.0=强, 0.0=弱
    witness_cases: list[WitnessCase] = field(default_factory=list)
    verdict: str = "strong"  # strong/weak/tautological

    def as_dict(self) -> dict:
        return {
            "criterion_text": self.criterion_text[:200],
            "tautology_hits": [h.as_dict() for h in self.tautology_hits],
            "has_numeric_criterion": self.has_numeric_criterion,
            "numeric_values": self.numeric_values,
            "strength_score": round(self.strength_score, 4),
            "witness_cases": [w.as_dict() for w in self.witness_cases],
            "verdict": self.verdict,
        }


def _detect_tautologies(
    text: str,
    cfg: AuditorConfig,
) -> list[TautologyHit]:
    """检出恒真式。"""
    patterns = cfg.tautology_patterns()
    hits = []
    flags = re.IGNORECASE

    for entry in patterns:
        pid = entry.get("id", "")
        pattern = entry.get("pattern", "")
        desc = entry.get("description", "")
        requires_no_digit = entry.get("requires_no_digit", False)

        if not pattern:
            continue

        try:
            m = re.search(pattern, text, flags)
            if m:
                # 特殊处理：requires_no_digit 要求整个文本没有数字
                if requires_no_digit:
                    # 检查是否真的没有数字
                    if re.search(r"\d", text):
                        continue
                hits.append(TautologyHit(
                    pattern_id=pid,
                    description=desc,
                    matched_text=m.group(0),
                    severity="high" if pid in ("identity_trivial", "always_true", "circular") else "medium",
                ))
        except re.error:
            continue

    return hits


def _extract_numeric_values(text: str, cfg: AuditorConfig) -> list[str]:
    """抽取文本中的数值。"""
    pattern = cfg.get("contract.numeric_pattern", r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
    return re.findall(pattern, text)


def _compute_strength_score(
    text: str,
    tautology_hits: list[TautologyHit],
    has_numeric: bool,
    numeric_values: list[str],
) -> float:
    """计算判据强度分数。

    1.0 = 强判据（有具体数值、无恒真式）
    0.0 = 弱判据（恒真式、无数字）
    """
    score = 1.0

    # 恒真式惩罚
    for hit in tautology_hits:
        if hit.severity == "high":
            score -= 0.5
        elif hit.severity == "medium":
            score -= 0.3
        else:
            score -= 0.1

    # 无数字惩罚
    if not has_numeric:
        score -= 0.4

    # 数值数量奖励（有多个数值更可信）
    if len(numeric_values) >= 2:
        score += 0.1
    elif len(numeric_values) == 1:
        score += 0.05

    return max(0.0, min(1.0, score))


def _generate_witness_cases(
    text: str,
    tautology_hits: list[TautologyHit],
    cfg: AuditorConfig,
) -> list[WitnessCase]:
    """生成可执行见证用例。"""
    if not cfg.get("strength_audit.witness_generation.enabled", True):
        return []

    max_witnesses = cfg.get("strength_audit.witness_generation.max_witnesses_per_criterion", 3)
    cases = []

    # 根据检出的恒真式类型生成见证用例
    for hit in tautology_hits[:max_witnesses]:
        if hit.pattern_id == "always_true":
            cases.append(WitnessCase(
                case_id=f"witness_{hit.pattern_id}_001",
                description=f"Test that criterion is NOT always true: provide counterexample",
                input_spec={"test_type": "counterexample_search"},
                expected_behavior="criterion should fail for at least one valid input",
            ))
        elif hit.pattern_id == "no_numeric":
            cases.append(WitnessCase(
                case_id=f"witness_{hit.pattern_id}_001",
                description=f"Test that criterion has numeric threshold",
                input_spec={"test_type": "numeric_threshold_check"},
                expected_behavior="criterion should specify numeric value with comparator",
            ))
        elif hit.pattern_id == "vague_threshold":
            cases.append(WitnessCase(
                case_id=f"witness_{hit.pattern_id}_001",
                description=f"Test that criterion has precise threshold",
                input_spec={"test_type": "precision_check"},
                expected_behavior="criterion should use numeric value instead of vague term",
            ))
        elif hit.pattern_id == "self_referential":
            cases.append(WitnessCase(
                case_id=f"witness_{hit.pattern_id}_001",
                description=f"Test that criterion is not self-referential",
                input_spec={"test_type": "independence_check"},
                expected_behavior="criterion should reference external benchmark or analytic solution",
            ))

    return cases


def audit_criterion_strength(
    text: str,
    cfg: AuditorConfig,
) -> StrengthAudit:
    """审计判据强度。"""
    if not text or not text.strip():
        return StrengthAudit(
            criterion_text="",
            strength_score=0.0,
            verdict="tautological",
        )

    # 检出恒真式
    tautology_hits = _detect_tautologies(text, cfg)

    # 抽取数值
    numeric_values = _extract_numeric_values(text, cfg)
    has_numeric = len(numeric_values) > 0

    # 计算强度分数
    strength_score = _compute_strength_score(text, tautology_hits, has_numeric, numeric_values)

    # 生成见证用例
    witness_cases = _generate_witness_cases(text, tautology_hits, cfg)

    # 判定
    if strength_score < 0.3:
        verdict = "tautological"
    elif strength_score < 0.6:
        verdict = "weak"
    else:
        verdict = "strong"

    return StrengthAudit(
        criterion_text=text,
        tautology_hits=tautology_hits,
        has_numeric_criterion=has_numeric,
        numeric_values=numeric_values,
        strength_score=strength_score,
        witness_cases=witness_cases,
        verdict=verdict,
    )
