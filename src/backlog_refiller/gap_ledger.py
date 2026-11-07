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


CAPABILITY_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS capability_ledger ("
    " capability_name text PRIMARY KEY,"
    " evidence_level text NOT NULL,"
    " repo text,"
    " description text,"
    " metadata jsonb NOT NULL DEFAULT '{}'::jsonb,"
    " updated_at timestamptz NOT NULL DEFAULT now())"
)

CAPABILITY_LEDGER_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS capability_ledger_evidence_idx"
    " ON capability_ledger (evidence_level)"
)


def ensure_capability_ledger(db_session) -> bool:
    """确保缺口源表 ``capability_ledger`` 存在（幂等 DDL，返回是否成功）。

    背景：R1 的补给器把「能力台账 WITHHELD 项」当作第一缺口源，但平台库里
    从来没有这张表（2026-11-07 现场核对：tensorplatform / foundation 两库均无
    ``capability_ledger``）。缺表时 ``collect_gaps`` 直接抛 psycopg 错误，
    daemon 一轮都跑不完——回放测试能过、真库跑不通。这里把建表做成显式、
    幂等的一步，由 daemon 启动时调用；建不出来也不致命（源级容错兜底）。
    """
    from sqlalchemy import text

    try:
        db_session.execute(text(CAPABILITY_LEDGER_DDL))
        db_session.execute(text(CAPABILITY_LEDGER_INDEX_DDL))
        db_session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - 建表失败降级为源不可用
        db_session.rollback()
        logger.warning("[gap_ledger] capability_ledger 建表失败: %s", exc)
        return False


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

    #: 各缺口源的健康状态（ok / unavailable:<原因>），审计与门禁据此判读
    source_status: dict[str, str] = {}

    def _safe_source(self, name: str, fn) -> list[dict[str, Any]]:
        """源级容错：单源缺表/报错不再拖死整轮补给（记状态后跳过）。"""
        try:
            rows = fn()
            self.source_status[name] = "ok"
            return rows
        except Exception as exc:  # noqa: BLE001 - 缺表/权限均可降级
            self.db.rollback()
            self.source_status[name] = f"unavailable:{type(exc).__name__}"
            logger.warning("[gap_ledger] 缺口源 %s 不可用: %s", name, exc)
            return []

    def collect_gaps(self) -> list[dict[str, Any]]:
        """聚合所有缺口源，返回标准化缺口列表。"""
        gaps: list[dict[str, Any]] = []
        gaps.extend(self._safe_source("capability_ledger", self._collect_withheld_capabilities))
        gaps.extend(self._safe_source("campaign_nodes", self._collect_campaign_gaps))
        gaps.extend(self._safe_source("mission_gap", self._collect_mission_gaps))
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
