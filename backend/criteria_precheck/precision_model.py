#!/usr/bin/env python3
"""精度模型 / 不可达成性判定（E1）。

对判据文本做静态分析，判定其「可达成性」：
* 不可达成（UNREACHABLE）：阈值超出物理/数值极限（如 float64 精度以下、负误差等）
* 可达成（REACHABLE）：阈值在合理范围内

判定规则（纯形状/数值分析，零领域语义）：
1. 阈值 < 1e-10 → UNREACHABLE（低于典型双精度有效精度）
2. 阈值要求「误差 == 0」且无「守恒/自洽」参考 → UNREACHABLE
3. 阈值要求「误差 < 0」→ UNREACHABLE（负误差无物理意义）
4. 阈值 > 1e6 且参考为「解析解」→ UNREACHABLE（解析解不可能差这么多）
5. 其余 → REACHABLE

纯函数、无 IO、无随机。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from backend.quality.judgeability import classifier as C
from backend.quality.judgeability import schema as S


@dataclass(frozen=True)
class Achievability:
    verdict: str                    # "REACHABLE" | "UNREACHABLE"
    reasons: Tuple[str, ...]
    threshold_value: Optional[float] = None
    confidence: float = 1.0

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "threshold_value": self.threshold_value,
            "confidence": round(self.confidence, 6),
        }


# 数值提取正则（与 schema P1 规则一致）
_NUM_PATTERN = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def _extract_thresholds(text: str) -> List[float]:
    """从判据文本中提取所有数值阈值。"""
    segment, _ = S.criteria_segment(text)
    patterns = [
        rf"[<>≤≥＜＞⩽⩾]\s*=?\s*({_NUM_PATTERN})",
        rf"±\s*({_NUM_PATTERN})",
        rf"==?\s*({_NUM_PATTERN})",
    ]
    values = []
    for pat in patterns:
        for m in re.finditer(pat, segment):
            try:
                values.append(float(m.group(1)))
            except (ValueError, IndexError):
                pass
    return values


def _has_positive_reference(text: str, kind: str) -> bool:
    """检查文本是否包含特定参考种类词（排除否定语境）。"""
    segment, _ = S.criteria_segment(text)
    # 简单否定检测：如果 kind 前面有「无」「不」「非」等否定词，则不算
    negation_pattern = rf"[无不非没缺缺欠]{0,2}{re.escape(kind)}"
    for m in re.finditer(negation_pattern, segment):
        # 检查是否是独立的否定用法
        start = m.start()
        # 如果否定词紧挨着 kind，说明是否定用法
        if start > 0 and segment[start:start+1] in "无不非没缺":
            continue
    # 直接检查 kind 是否在 segment 中，但排除明显的否定语境
    if kind in segment:
        # 检查是否有否定前缀
        idx = segment.find(kind)
        if idx > 0:
            prefix = segment[max(0, idx-2):idx]
            if any(neg in prefix for neg in ["无", "不", "非", "没", "缺"]):
                return False
        return True
    return False


def assess_achievability(text: str) -> Achievability:
    """判定判据的可达成性。

    规则（按优先级）：
    1. 无 P1 阈值 → REACHABLE（无法判定，默认可达）
    2. 阈值 < 1e-10 → UNREACHABLE（低于典型双精度有效精度）
    3. 阈值 < 0 → UNREACHABLE（负误差）
    4. 阈值 == 0 且无「守恒/自洽」参考 → UNREACHABLE
    5. 阈值 > 1e6 且有「解析解」→ UNREACHABLE
    6. 其余 → REACHABLE
    """
    thresholds = _extract_thresholds(text)
    reasons: List[str] = []

    if not thresholds:
        return Achievability(
            verdict="REACHABLE",
            reasons=("no_numeric_threshold_found",),
            confidence=0.5,
        )

    for val in thresholds:
        if val < 0:
            reasons.append(f"negative_threshold:{val}")
            return Achievability(
                verdict="UNREACHABLE",
                reasons=tuple(reasons),
                threshold_value=val,
            )

        if 0 < val < 1e-10:
            reasons.append(f"below_typical_precision:{val}")
            return Achievability(
                verdict="UNREACHABLE",
                reasons=tuple(reasons),
                threshold_value=val,
            )

        if val == 0.0:
            has_conservation = _has_positive_reference(text, "守恒")
            has_self_consistent = _has_positive_reference(text, "自洽")
            if not has_conservation and not has_self_consistent:
                reasons.append("zero_error_without_conservation")
                return Achievability(
                    verdict="UNREACHABLE",
                    reasons=tuple(reasons),
                    threshold_value=val,
                )

        if val > 1e6 and _has_positive_reference(text, "解析解"):
            reasons.append(f"excessive_threshold_for_analytic:{val}")
            return Achievability(
                verdict="UNREACHABLE",
                reasons=tuple(reasons),
                threshold_value=val,
            )

    return Achievability(
        verdict="REACHABLE",
        reasons=tuple(reasons) if reasons else ("within_reasonable_bounds",),
        threshold_value=thresholds[0] if thresholds else None,
    )


def parse_criteria_thresholds(text: str) -> List[float]:
    """公开接口：提取判据中的所有数值阈值。"""
    return _extract_thresholds(text)
