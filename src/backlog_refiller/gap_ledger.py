"""gap_ledger — 缺口账本聚合器。

从多个已文档化缺口源聚合待补给任务：
  1. 能力台账 WITHHELD 项（audit_capabilities API）
  2. 战役收尾 GAP 账本（campaign_nodes 中未完成的节点）
  3. m605 与 G 账本条目（mission result 中的 gap 标记）

每个缺口条目标准化为：
  {
      "source": str,          # 来源标识
      "ext_id": str,          # 外部去重标识
      "repo": str,            # 目标仓库
      "title": str,           # 缺口标题
      "description": str,     # 缺口描述
      "priority": int,        # 优先级（1=最高）
      "metadata": dict,       # 附加元数据
  }
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class GapLedgerSource:
    """缺口账本聚合器。

    参数:
        db_session: SQLAlchemy Session
        api_base: 平台 API 基础 URL（用于调用 audit_capabilities）
    """

    # 仓白名单：只允许为这些仓生成补给 mission
    REPO_WHITELIST = frozenset({
        "TensorLBM", "TensorPhysics", "TensorDEM", "TensorFEM", "TensorFVM",
        "TensorPlatform", "TensorSPH", "TensorDG", "TensorCAD", "TensorBEM",
        "TensorPapers", "ship-performance-platform",
    })

    def __init__(self, db_session, api_base: str = "http://localhost:8001"):
        self.db = db_session
        self.api_base = api_base.rstrip("/")

    def collect_gaps(self) -> list[dict[str, Any]]:
        """聚合所有缺口源，返回标准化缺口列表。"""
        gaps: list[dict[str, Any]] = []
        gaps.extend(self._collect_withheld_capabilities())
        gaps.extend(self._collect_campaign_gaps())
        gaps.extend(self._collect_mission_gaps())
        # 按优先级排序（1=最高）
        gaps.sort(key=lambda g: g.get("priority", 99))
        return gaps

    def _collect_withheld_capabilities(self) -> list[dict[str, Any]]:
        """从能力台账收集 WITHHELD 项。"""
        from sqlalchemy import text
        # 查询能力台账中证据等级为 WITHHELD 的条目
        rows = self.db.execute(text("""
            SELECT capability_name, evidence_level, repo, description, metadata
            FROM capability_ledger
            WHERE evidence_level = 'WITHHELD'
               OR evidence_level = 'NOT_SUPPORTED'
            ORDER BY capability_name
        """)).mappings().all()

        gaps = []
        for row in rows:
            repo = row.get("repo") or "TensorPlatform"
            if repo not in self.REPO_WHITELIST:
                continue
            ext_id = f"withheld:{row['capability_name']}"
            gaps.append({
                "source": "capability_ledger",
                "ext_id": ext_id,
                "repo": repo,
                "title": f"能力缺口: {row['capability_name']}",
                "description": row.get("description") or f"能力 {row['capability_name']} 证据等级={row['evidence_level']}",
                "priority": 3,
                "metadata": {
                    "capability_name": row["capability_name"],
                    "evidence_level": row["evidence_level"],
                    "metadata": row.get("metadata"),
                },
            })
        return gaps

    def _collect_campaign_gaps(self) -> list[dict[str, Any]]:
        """从战役节点收集中未完成的 GAP 节点。"""
        from sqlalchemy import text
        # 查询 campaign_nodes 中状态为 pending/blocked 且 metadata 含 gap 标记的节点
        rows = self.db.execute(text("""
            SELECT n.id, n.name, n.plan_id, n.metadata, n.node_type,
                   p.campaign_id, p.name as plan_name
            FROM campaign_nodes n
            JOIN campaign_plans p ON n.plan_id = p.id
            WHERE n.metadata::text LIKE '%gap%'
               OR n.metadata::text LIKE '%WITHHELD%'
               OR n.node_type = 'gate'
            ORDER BY n.id
        """)).mappings().all()

        gaps = []
        for row in rows:
            meta = row.get("metadata") or {}
            if isinstance(meta, str):
                import json
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            repo = meta.get("repo") or "TensorPlatform"
            if repo not in self.REPO_WHITELIST:
                continue
            ext_id = f"campaign_gap:node_{row['id']}"
            gaps.append({
                "source": "campaign_nodes",
                "ext_id": ext_id,
                "repo": repo,
                "title": f"战役缺口: {row['name']}",
                "description": f"战役计划 {row.get('plan_name', '')} 节点 {row['name']}（类型={row['node_type']}）",
                "priority": 2,
                "metadata": {
                    "node_id": row["id"],
                    "plan_id": row["plan_id"],
                    "campaign_id": row["campaign_id"],
                    "node_type": row["node_type"],
                },
            })
        return gaps

    def _collect_mission_gaps(self) -> list[dict[str, Any]]:
        """从已完成的 mission 中收集标记为 gap 的条目（m605/G 账本）。"""
        from sqlalchemy import text
        # 查询 result 中包含 gap 标记的已完成 mission
        rows = self.db.execute(text("""
            SELECT id, goal, repo, result
            FROM tensor_dev_missions
            WHERE status IN ('finished', 'awaiting_acceptance', 'failed')
              AND result::text LIKE '%gap%'
            ORDER BY id DESC
            LIMIT 50
        """)).mappings().all()

        gaps = []
        for row in rows:
            result = row.get("result") or {}
            if isinstance(result, str):
                import json
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    result = {}
            gap_info = result.get("gap") or result.get("follow_up")
            if not gap_info:
                continue
            repo = row.get("repo") or "TensorPlatform"
            if repo not in self.REPO_WHITELIST:
                continue
            ext_id = f"mission_gap:m{row['id']}"
            gaps.append({
                "source": "mission_gap",
                "ext_id": ext_id,
                "repo": repo,
                "title": f"任务缺口: m{row['id']} 后续",
                "description": f"任务 m{row['id']} 标记的后续缺口: {gap_info}",
                "priority": 1,
                "metadata": {
                    "parent_mission_id": row["id"],
                    "gap_info": gap_info,
                },
            })
        return gaps

    def filter_by_whitelist(self, gaps: list[dict]) -> list[dict]:
        """按仓白名单过滤缺口。"""
        return [g for g in gaps if g.get("repo") in self.REPO_WHITELIST]
