"""mission_generator — Mission 生成器。

负责从缺口条目生成符合规范的 mission：
  - goal ≥ 80 字 + 判据 + honest FAIL 纪律 + 零域语义句
  - ext_id 去重（防止重复生成）
  - 仓白名单校验
  - 生成回滚 SQL
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Goal 模板：确保 ≥80 字 + 判据 + honest FAIL + 零域语义
GOAL_TEMPLATE = """【常设补给器自动生成】{title}

源缺口: {source} (ext_id={ext_id})
目标仓库: {repo}

任务目标:
{description}

验收判据:
1. 产出物必须可机器验证（pytest/测试脚本/参考值比对）
2. 关键指标误差 <15%（对标实验/参考值）
3. 几何验证必须先验证 Cb/L/B（如涉及船舶）
4. 所有测试必须通过 pytest ≥ 1 个用例

纪律:
- honest FAIL 不可覆盖：若无法达成目标，如实报告 FAIL 并说明原因
- 零领域语义：本任务为平台基础设施任务，不涉及具体领域知识
- 只改工作树内文件，不 push，不改 .git 配置
- 改动要最小、可验证

若无法完成，请在 finish 中写明：
- 做了什么
- 怎么验证的
- 没做什么（honest FAIL）
""".strip()


class MissionGenerator:
    """Mission 生成器。

    参数:
        db_session: SQLAlchemy Session
        repo_whitelist: 允许的仓库集合
    """

    def __init__(self, db_session, repo_whitelist: frozenset | None = None):
        self.db = db_session
        self.repo_whitelist = repo_whitelist or frozenset({
            "TensorLBM", "TensorPhysics", "TensorDEM", "TensorFEM", "TensorFVM",
            "TensorPlatform", "TensorSPH", "TensorDG", "TensorCAD", "TensorBEM",
            "TensorPapers", "ship-performance-platform",
        })

    def generate_goal(self, gap: dict[str, Any]) -> str:
        """从缺口条目生成符合规范的 goal（≥80 字）。"""
        goal = GOAL_TEMPLATE.format(
            title=gap.get("title", "未命名缺口"),
            source=gap.get("source", "unknown"),
            ext_id=gap.get("ext_id", "unknown"),
            repo=gap.get("repo", "TensorPlatform"),
            description=gap.get("description", "无描述"),
        )
        # 验证长度
        if len(goal) < 80:
            logger.warning("生成的 goal 长度不足 80 字（%d），补充说明", len(goal))
            goal += f"\n\n补充：本任务由常设补给器于 {datetime.now(timezone.utc).isoformat()} 自动生成。"
        return goal

    def compute_ext_id_hash(self, ext_id: str) -> str:
        """计算 ext_id 的哈希（用于去重）。"""
        return hashlib.sha256(ext_id.encode("utf-8")).hexdigest()[:16]

    def is_duplicate(self, ext_id: str) -> bool:
        """检查 ext_id 是否已存在（去重）。"""
        from sqlalchemy import text
        ext_hash = self.compute_ext_id_hash(ext_id)
        row = self.db.execute(text("""
            SELECT 1 FROM tensor_dev_missions
            WHERE origin = 'backlog_refiller'
              AND (result->>'ext_id_hash') = :ext_hash
                 OR goal LIKE :ext_id_pattern
            LIMIT 1
        """), {
            "ext_hash": ext_hash,
            "ext_id_pattern": f"%ext_id={ext_id}%",
        }).first()
        return row is not None

    def validate_repo(self, repo: str) -> bool:
        """验证仓库是否在白名单中。"""
        return repo in self.repo_whitelist

    def generate_mission_params(self, gap: dict[str, Any]) -> dict[str, Any] | None:
        """生成 mission 创建参数。

        返回:
            mission 参数字典，若验证失败则返回 None
        """
        repo = gap.get("repo", "TensorPlatform")
        if not self.validate_repo(repo):
            logger.warning("仓库 %s 不在白名单中，跳过", repo)
            return None

        ext_id = gap.get("ext_id", "")
        if self.is_duplicate(ext_id):
            logger.info("ext_id=%s 已存在，跳过重复生成", ext_id)
            return None

        goal = self.generate_goal(gap)
        ext_hash = self.compute_ext_id_hash(ext_id)

        return {
            "goal": goal,
            "repo": repo,
            "origin": "backlog_refiller",
            "runner": "native",
            "budget": {
                "max_tool_calls": 80,
                "max_wall_seconds": 7200,
                "max_tokens": 3_000_000,
            },
            "ext_id": ext_id,
            "ext_id_hash": ext_hash,
            "gap_source": gap.get("source", "unknown"),
            "gap_priority": gap.get("priority", 99),
        }

    def build_rollback_sql(self, mission_id: int) -> str:
        """构建回滚 SQL（删除指定 mission 及其会话）。"""
        return (
            f"DELETE FROM tensor_agent_sessions "
            f"WHERE id IN (SELECT session_id FROM tensor_dev_missions WHERE id = {mission_id}); "
            f"DELETE FROM tensor_dev_missions WHERE id = {mission_id};"
        )
