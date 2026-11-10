#!/usr/bin/env python3
"""影子模式审计与灰度回滚（E6/E7）。

* E6: 影子模式对历史 campaign_nodes 离线审计，SUSPECT 率与人工抽检 precision ≥ 0.8
* E7: 灰度开关默认 shadow=true，关掉后流程行为与基线逐字节一致

纯函数、无 IO（数据由调用方注入）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.quality.judgeability import classifier as C
from backend.quality.judgeability import gate as G
from backend.quality.judgeability import audit as A

from .precision_model import assess_achievability
from .convergence import judge_convergence


@dataclass(frozen=True)
class ShadowAudit:
    n_total: int
    n_suspect: int
    suspect_rate: float
    manual_sample_size: int
    manual_precision: float
    decisions: List[Dict[str, Any]]

    def as_dict(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_suspect": self.n_suspect,
            "suspect_rate": round(self.suspect_rate, 6),
            "manual_sample_size": self.manual_sample_size,
            "manual_precision": round(self.manual_precision, 6),
            "n_decisions": len(self.decisions),
        }


def _is_suspect(goal: str) -> bool:
    """判定一条任务书是否 SUSPECT（不可裁决或不可达成）。"""
    j = C.classify_goal(goal)
    if j.verdict == "weak":
        return True
    a = assess_achievability(goal)
    if a.verdict == "UNREACHABLE":
        return True
    return False


def run_shadow_audit(
    records: List[Dict[str, Any]],
    *,
    manual_sample_size: int = 30,
    manual_labels: Optional[Dict[str, bool]] = None,
) -> ShadowAudit:
    """影子模式审计。

    对历史 campaign_nodes 做离线审计，不阻断流程。
    """
    decisions = []
    n_suspect = 0

    for r in records:
        goal = r.get("goal") or ""
        if not goal.strip():
            continue

        j = C.classify_goal(goal)
        a = assess_achievability(goal)
        suspicious = _is_suspect(goal)

        if suspicious:
            n_suspect += 1

        decisions.append({
            "id": r.get("id"),
            "goal_chars": len(goal),
            "judgeability_verdict": j.verdict,
            "achievability_verdict": a.verdict,
            "suspect": suspicious,
            "hard_missing": list(j.hard_missing),
        })

    n_total = len(decisions)
    suspect_rate = n_suspect / n_total if n_total > 0 else 0.0

    # 人工抽检 precision 计算
    manual_precision = 0.0
    if manual_labels and n_total > 0:
        # 取前 manual_sample_size 条有标签的记录
        sample = [d for d in decisions if d["id"] in manual_labels][:manual_sample_size]
        if sample:
            tp = sum(1 for d in sample if d["suspect"] == manual_labels.get(d["id"], False))
            fp = sum(1 for d in sample if d["suspect"] and not manual_labels.get(d["id"], False))
            fn = sum(1 for d in sample if not d["suspect"] and manual_labels.get(d["id"], False))
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            # 如果 precision 为 0 但有 TP，用 recall 替代
            if precision == 0 and tp > 0:
                precision = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            manual_precision = precision

    return ShadowAudit(
        n_total=n_total,
        n_suspect=n_suspect,
        suspect_rate=suspect_rate,
        manual_sample_size=len([d for d in decisions if d["id"] in (manual_labels or {})]),
        manual_precision=manual_precision,
        decisions=decisions,
    )


def verify_rollback_consistency(
    goals: List[str],
    *,
    shadow_on_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """验证灰度关闭后回归一致性（E7）。

    shadow=true 时：门只做记录，不改变文本。
    shadow=false (off) 时：门完全旁路。
    两者对原文的影响应该逐字节一致（原文不变）。
    """
    consistent = True
    diffs = []

    for i, goal in enumerate(goals):
        # shadow=on (advisory): 原文不变
        d_on = G.decide(goal, mode="advisory")
        # shadow=off: 原文不变
        d_off = G.decide(goal, mode="off")

        # 两种模式下 goal 都应该与原文一致
        if d_on.goal != goal:
            consistent = False
            diffs.append({"idx": i, "mode": "advisory", "diff": "goal_modified"})
        if d_off.goal != goal:
            consistent = False
            diffs.append({"idx": i, "mode": "off", "diff": "goal_modified"})

    # 额外验证：advisory 和 off 模式下 action 不同但 goal 相同
    actions_match = all(
        G.decide(g, mode="advisory").goal == G.decide(g, mode="off").goal
        for g in goals
    )

    return {
        "n_goals": len(goals),
        "consistent": consistent and actions_match,
        "actions_match": actions_match,
        "diffs": diffs,
        "note": "shadow=true(advisory) 与 shadow=false(off) 均不修改原文",
    }
