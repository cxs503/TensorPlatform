"""audit — 审计日志（可回滚）。

记录 backlog_refiller 的每个动作，支持撤销最近一次补给操作。
审计条目落库到 `backlog_refiller_audit` 表，包含：
  - action: refill / rollback / liveness
  - mission_id: 生成的 mission ID（refill 时）
  - ext_id: 外部去重标识
  - details: JSON 详情
  - created_at: 时间戳
  - rollback_sql: 回滚 SQL（refill 时记录，用于撤销）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class AuditLogger:
    """审计日志记录器，支持追加与回滚。

    参数:
        db_session: SQLAlchemy Session
        table_name: 审计表名，默认 'backlog_refiller_audit'
    """

    def __init__(self, db_session, table_name: str = "backlog_refiller_audit"):
        self.db = db_session
        self.table = table_name

    def _ensure_table(self):
        """确保审计表存在（幂等）。"""
        from sqlalchemy import text
        self.db.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {self.table} (
                id SERIAL PRIMARY KEY,
                action TEXT NOT NULL,
                mission_id INTEGER,
                ext_id TEXT,
                details JSONB,
                rollback_sql TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        self.db.commit()

    def log(self, action: str, mission_id: int | None = None,
            ext_id: str | None = None, details: dict | None = None,
            rollback_sql: str | None = None) -> int:
        """记录一条审计日志。

        返回:
            审计条目 ID
        """
        from sqlalchemy import text
        self._ensure_table()
        row = self.db.execute(text(f"""
            INSERT INTO {self.table} (action, mission_id, ext_id, details, rollback_sql)
            VALUES (:action, :mid, :ext_id, CAST(:details AS jsonb), :rollback_sql)
            RETURNING id
        """), {
            "action": action,
            "mid": mission_id,
            "ext_id": ext_id,
            "details": json.dumps(details, ensure_ascii=False, default=str) if details else None,
            "rollback_sql": rollback_sql,
        }).scalar()
        self.db.commit()
        logger.info("[audit] action=%s mission_id=%s ext_id=%s audit_id=%s",
                     action, mission_id, ext_id, row)
        return row

    def get_latest_refill(self) -> dict | None:
        """获取最近一次 refill 审计条目（用于回滚）。"""
        from sqlalchemy import text
        self._ensure_table()
        row = self.db.execute(text(f"""
            SELECT id, action, mission_id, ext_id, details, rollback_sql, created_at
            FROM {self.table}
            WHERE action = 'refill'
            ORDER BY created_at DESC LIMIT 1
        """)).mappings().first()
        if row:
            return dict(row)
        return None

    def rollback_latest(self) -> dict | None:
        """回滚最近一次 refill 操作。

        返回:
            回滚审计条目详情，若无最近 refill 则返回 None
        """
        latest = self.get_latest_refill()
        if not latest:
            logger.warning("[audit] 无最近 refill 记录，无法回滚")
            return None

        rollback_sql = latest.get("rollback_sql")
        if not rollback_sql:
            logger.error("[audit] 最近 refill 无 rollback_sql，无法回滚")
            return None

        from sqlalchemy import text
        # 执行回滚 SQL
        self.db.execute(text(rollback_sql))
        # 记录回滚审计
        audit_id = self.log(
            action="rollback",
            mission_id=latest["mission_id"],
            ext_id=latest["ext_id"],
            details={"rolled_back_audit_id": latest["id"],
                     "original_created_at": str(latest["created_at"])},
        )
        logger.info("[audit] 回滚完成 audit_id=%s", audit_id)
        return {"audit_id": audit_id, "original": latest}

    def get_audit_trail(self, limit: int = 50) -> list[dict]:
        """获取审计轨迹（最近 N 条）。"""
        from sqlalchemy import text
        self._ensure_table()
        rows = self.db.execute(text(f"""
            SELECT id, action, mission_id, ext_id, details, rollback_sql, created_at
            FROM {self.table}
            ORDER BY created_at DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
        return [dict(r) for r in rows]

    def log_liveness(self, daemon_id: str, uptime_seconds: float) -> int:
        """记录 daemon 存活心跳。"""
        return self.log(
            action="liveness",
            details={"daemon_id": daemon_id, "uptime_seconds": uptime_seconds},
        )
