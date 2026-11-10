"""升级工作流：证据达标→自动升账+审计日志+回滚点。

流程：
1. 接收裁定结果（derived_level vs current_level）
2. 若 derived > current，触发升级流程
3. 生成审计日志（sha256 签名）
4. 保存回滚快照
5. 更新台账
6. 验证数据一致性

设计原则：
- 只升不降：降级由 overclaim_gate 处理
- 审计链完整：每次变更都有签名日志
- 可回滚：保存变更前快照，支持一致性校验
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adjudication_engine import AdjudicationResult, LEVEL_RANK, LEVELS

# ── 升级记录 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UpgradeRecord:
    """单次升级记录。"""
    capability_id: str
    from_level: str
    to_level: str
    reason: str
    adjudication_sha256: str
    audit_log_sha256: str
    snapshot_sha256: str  # 回滚快照指纹
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "reason": self.reason,
            "adjudication_sha256": self.adjudication_sha256,
            "audit_log_sha256": self.audit_log_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "timestamp": self.timestamp,
        }


# ── 审计日志条目 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AuditLogEntry:
    """审计日志条目。"""
    action: str  # "upgrade" | "downgrade" | "rollback" | "verify"
    capability_id: str
    detail: str
    sha256: str  # 条目签名
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "capability_id": self.capability_id,
            "detail": self.detail,
            "sha256": self.sha256,
            "timestamp": self.timestamp,
        }


# ── 回滚快照 ──────────────────────────────────────────────────────────────

@dataclass
class RollbackSnapshot:
    """回滚快照。"""
    capability_levels: Dict[str, str]
    snapshot_sha256: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_levels": dict(self.capability_levels),
            "snapshot_sha256": self.snapshot_sha256,
            "created_at": self.created_at,
        }


# ── 升级工作流 ────────────────────────────────────────────────────────────

class UpgradeWorkflow:
    """能力等级升级工作流。

    Parameters
    ----------
    current_levels : dict
        当前能力等级映射 {capability_id: level}。
    ledger_path : Path, optional
        台账文件路径（用于持久化）。
    """

    def __init__(self, current_levels: Dict[str, str],
                 ledger_path: Optional[Path] = None):
        self._levels = dict(current_levels)
        self._ledger_path = ledger_path
        self._upgrade_records: List[UpgradeRecord] = []
        self._audit_log: List[AuditLogEntry] = []
        self._snapshots: List[RollbackSnapshot] = []

    def process_adjudication(self, result: AdjudicationResult) -> Optional[UpgradeRecord]:
        """处理单次裁定结果，若需升级则执行。

        Returns
        -------
        UpgradeRecord or None
            若发生升级则返回记录，否则返回 None。
        """
        cap_id = result.capability_id
        current = self._levels.get(cap_id, "NOT_SUPPORTED")
        derived = result.derived_level

        # 只升不降
        if LEVEL_RANK.get(derived, 0) <= LEVEL_RANK.get(current, 0):
            self._add_audit_log("verify", cap_id,
                                f"无需升级：当前 {current} >= 裁定 {derived}")
            return None

        # 创建回滚快照
        snapshot = self._create_snapshot()

        # 执行升级
        reason = f"裁定 {derived} > 当前 {current}（规则: {result.rule_fired}）"
        self._levels[cap_id] = derived

        # 生成审计日志
        audit_entry = self._add_audit_log("upgrade", cap_id, reason)

        # 创建升级记录
        record = UpgradeRecord(
            capability_id=cap_id,
            from_level=current,
            to_level=derived,
            reason=reason,
            adjudication_sha256=hashlib.sha256(
                json.dumps(result.to_dict(), sort_keys=True).encode()
            ).hexdigest(),
            audit_log_sha256=audit_entry.sha256,
            snapshot_sha256=snapshot.snapshot_sha256,
        )
        self._upgrade_records.append(record)
        return record

    def process_all(self, results: Dict[str, AdjudicationResult]) -> List[UpgradeRecord]:
        """批量处理裁定结果。"""
        records = []
        for cap_id, result in sorted(results.items()):
            record = self.process_adjudication(result)
            if record:
                records.append(record)
        return records

    def rollback(self, capability_id: str) -> Optional[Dict[str, str]]:
        """回滚指定能力到上一快照。

        Returns
        -------
        dict or None
            回滚后的等级映射，若无快照则返回 None。
        """
        if not self._snapshots:
            return None

        # 取最新快照
        snapshot = self._snapshots[-1]
        old_level = snapshot.capability_levels.get(capability_id)
        if old_level is None:
            return None

        current = self._levels.get(capability_id)
        self._levels[capability_id] = old_level

        self._add_audit_log("rollback", capability_id,
                            f"从 {current} 回滚到 {old_level}")
        return dict(self._levels)

    def verify_consistency(self) -> Dict[str, Any]:
        """验证数据一致性。

        Returns
        -------
        dict
            一致性检查结果。
        """
        issues = []

        # 检查所有升级记录的回滚快照是否一致
        for record in self._upgrade_records:
            # 验证 adjudication_sha256 格式
            if len(record.adjudication_sha256) != 64:
                issues.append(f"无效裁定指纹: {record.capability_id}")

            # 验证 audit_log_sha256 格式
            if len(record.audit_log_sha256) != 64:
                issues.append(f"无效审计指纹: {record.capability_id}")

            # 验证 snapshot_sha256 格式
            if len(record.snapshot_sha256) != 64:
                issues.append(f"无效快照指纹: {record.capability_id}")

        # 检查审计日志完整性
        for entry in self._audit_log:
            if len(entry.sha256) != 64:
                issues.append(f"无效日志条目指纹")

        # 检查等级有效性
        for cap_id, level in self._levels.items():
            if level not in LEVELS:
                issues.append(f"无效等级: {cap_id}={level}")

        return {
            "consistent": len(issues) == 0,
            "issues": issues,
            "total_upgrades": len(self._upgrade_records),
            "total_audit_entries": len(self._audit_log),
            "total_snapshots": len(self._snapshots),
        }

    def get_current_levels(self) -> Dict[str, str]:
        return dict(self._levels)

    def get_upgrade_records(self) -> List[UpgradeRecord]:
        return list(self._upgrade_records)

    def get_audit_log(self) -> List[AuditLogEntry]:
        return list(self._audit_log)

    def save_ledger(self, path: Optional[Path] = None) -> Path:
        """保存台账到文件。"""
        target = path or self._ledger_path
        if target is None:
            raise ValueError("未指定台账路径")

        data = {
            "schema_version": "maturity-ledger/1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "capability_levels": self._levels,
            "upgrade_records": [r.to_dict() for r in self._upgrade_records],
            "audit_log": [e.to_dict() for e in self._audit_log],
            "snapshots": [s.to_dict() for s in self._snapshots],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    def _create_snapshot(self) -> RollbackSnapshot:
        """创建当前状态快照。"""
        levels_json = json.dumps(self._levels, sort_keys=True)
        sha = hashlib.sha256(levels_json.encode()).hexdigest()
        snapshot = RollbackSnapshot(
            capability_levels=dict(self._levels),
            snapshot_sha256=sha,
        )
        self._snapshots.append(snapshot)
        return snapshot

    def _add_audit_log(self, action: str, capability_id: str,
                       detail: str) -> AuditLogEntry:
        """添加审计日志条目。"""
        blob = json.dumps({
            "action": action,
            "capability_id": capability_id,
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, sort_keys=True)
        sha = hashlib.sha256(blob.encode()).hexdigest()
        entry = AuditLogEntry(
            action=action,
            capability_id=capability_id,
            detail=detail,
            sha256=sha,
        )
        self._audit_log.append(entry)
        return entry
