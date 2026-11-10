"""防 OVERCLAIM 门：自述等级>证据等级时自动降级+告警。

功能：
1. 检测自述等级与证据等级的分歧
2. 自动降级（当自述 > 证据时）
3. 生成告警记录
4. 审计日志签名

设计原则：
- 自动降级不可逆（需人工复核才能恢复）
- 每次降级都有完整审计链
- 告警可被下游系统消费
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .adjudication_engine import AdjudicationResult, LEVEL_RANK, LEVELS

# ── 告警记录 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OverclaimAlert:
    """OVERCLAIM 告警记录。"""
    capability_id: str
    claimed_level: str
    evidence_level: str
    gap: int  # 等级差距
    action: str  # "downgraded" | "flagged" | "no_action"
    reason: str
    sha256: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "claimed_level": self.claimed_level,
            "evidence_level": self.evidence_level,
            "gap": self.gap,
            "action": self.action,
            "reason": self.reason,
            "sha256": self.sha256,
            "timestamp": self.timestamp,
        }


# ── 防 OVERCLAIM 门 ──────────────────────────────────────────────────────

class OverclaimGate:
    """防 OVERCLAIM 门。

    Parameters
    ----------
    auto_downgrade : bool
        是否自动降级（默认 True）。
    """

    def __init__(self, auto_downgrade: bool = True):
        self.auto_downgrade = auto_downgrade
        self._alerts: List[OverclaimAlert] = []
        self._downgrades: Dict[str, str] = {}  # cap_id -> new_level

    def check(self, result: AdjudicationResult) -> Optional[OverclaimAlert]:
        """检查单次裁定是否存在 OVERCLAIM。

        Returns
        -------
        OverclaimAlert or None
            若存在 OVERCLAIM 则返回告警，否则返回 None。
        """
        if not result.is_overclaim:
            return None

        claimed = result.claimed_level
        evidence = result.derived_level
        gap = LEVEL_RANK.get(claimed, 0) - LEVEL_RANK.get(evidence, 0)

        if self.auto_downgrade:
            action = "downgraded"
            self._downgrades[result.capability_id] = evidence
            reason = f"自动降级：自述 {claimed} > 证据 {evidence}（差距 {gap} 级）"
        else:
            action = "flagged"
            reason = f"标记 OVERCLAIM：自述 {claimed} > 证据 {evidence}（差距 {gap} 级）"

        alert = self._create_alert(
            capability_id=result.capability_id,
            claimed_level=claimed,
            evidence_level=evidence,
            gap=gap,
            action=action,
            reason=reason,
        )
        self._alerts.append(alert)
        return alert

    def check_all(self, results: Dict[str, AdjudicationResult]) -> List[OverclaimAlert]:
        """批量检查所有裁定结果。"""
        alerts = []
        for cap_id, result in sorted(results.items()):
            alert = self.check(result)
            if alert:
                alerts.append(alert)
        return alerts

    def get_downgraded_levels(self) -> Dict[str, str]:
        """获取降级后的等级映射。"""
        return dict(self._downgrades)

    def get_alerts(self) -> List[OverclaimAlert]:
        return list(self._alerts)

    def summary(self) -> Dict[str, Any]:
        """告警摘要。"""
        by_action: Dict[str, int] = {}
        for alert in self._alerts:
            by_action[alert.action] = by_action.get(alert.action, 0) + 1

        return {
            "total_alerts": len(self._alerts),
            "by_action": by_action,
            "downgraded_capabilities": list(self._downgrades.keys()),
            "max_gap": max((a.gap for a in self._alerts), default=0),
        }

    def _create_alert(self, capability_id: str, claimed_level: str,
                      evidence_level: str, gap: int, action: str,
                      reason: str) -> OverclaimAlert:
        """创建告警记录（带 sha256 签名）。"""
        blob = json.dumps({
            "capability_id": capability_id,
            "claimed_level": claimed_level,
            "evidence_level": evidence_level,
            "gap": gap,
            "action": action,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, sort_keys=True)
        sha = hashlib.sha256(blob.encode()).hexdigest()
        return OverclaimAlert(
            capability_id=capability_id,
            claimed_level=claimed_level,
            evidence_level=evidence_level,
            gap=gap,
            action=action,
            reason=reason,
            sha256=sha,
        )
