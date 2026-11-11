"""KPI-QC-RECHECK 数据访问层：只读 SQL + 两种 Reader（psycopg / SQLAlchemy Session）。

纪律：
* **只读**：本模块只有 SELECT，任何写操作都不属于本计量能力。
* **零领域语义**：不认识任何学科词，只认平台自有表（mission_lifecycle_log /
  outcome_evaluations），仓名仅作分层键透传。
* Reader 抽象让聚合逻辑可脱库单测（tests/test_kpi_qc_recheck.py 用 FakeReader）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Protocol

# ── 只读 SQL（参数 :days / :mission_ids）───────────────────────────────

DENOMINATOR_SQL = """
SELECT mission_id,
       COALESCE(detail->>'repo', '') AS repo,
       COALESCE(detail->>'pr', '')   AS pr,
       MIN(created_at)               AS merged_at
  FROM mission_lifecycle_log
 WHERE action = 'pr_gate'
   AND detail->>'state' = 'merged'
   AND created_at > now() - make_interval(days => %(days)s)
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

# 复核记录：只取与分母 mission 相关的行（避免全表 7000+ 行无谓搬运）
EVALUATION_SQL = """
SELECT mission_id,
       COALESCE(reviewer_kind, '')            AS reviewer_kind,
       COALESCE(review_status, '')            AS review_status,
       COALESCE(status, '')                   AS status,
       COALESCE(cross_model_verified, false)  AS cross_model_verified,
       COALESCE(decided_at, created_at, '')   AS decided_at
  FROM outcome_evaluations
 WHERE mission_id = ANY(%(mission_ids)s)
 ORDER BY mission_id, decided_at
"""

# 供给侧：逐日新增复判行按状态堆叠（判断复核产能是否停产）
REVIEW_SUPPLY_SQL = """
SELECT substr(COALESCE(decided_at, created_at, ''), 1, 10) AS day,
       COALESCE(review_status, 'unknown')                  AS review_status,
       COALESCE(status, 'unknown')                         AS status,
       COUNT(*)                                            AS rows
  FROM outcome_evaluations
 WHERE COALESCE(decided_at, created_at, '') >= %(since)s
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

PENDING_BACKLOG_SQL = """
SELECT COUNT(*)                                        AS pending_rows,
       COUNT(DISTINCT mission_id)                      AS pending_missions,
       COUNT(DISTINCT mission_id) FILTER (
           WHERE mission_id IN (SELECT id FROM tensor_dev_missions WHERE status = 'succeeded')
       )                                               AS pending_succeeded_missions
  FROM outcome_evaluations
 WHERE status = 'pending'
"""

READ_ONLY_SQL = (
    DENOMINATOR_SQL, EVALUATION_SQL, REVIEW_SUPPLY_SQL, PENDING_BACKLOG_SQL,
)

# ── Reader 协议 ──────────────────────────────────────────────────────

class Reader(Protocol):
    def denominator(self, days: int) -> list[dict[str, Any]]: ...
    def evaluations(self, mission_ids: Iterable[int]) -> list[dict[str, Any]]: ...
    def review_supply(self, since_iso: str) -> list[dict[str, Any]]: ...
    def pending_backlog(self) -> dict[str, Any]: ...


class PgReader:
    """psycopg3 只读 Reader（真实数据源）。"""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn
        self._conn = None

    # -- 连接（惰性，避免 import 期即连库）--
    @property
    def conn(self):
        if self._conn is None:
            import psycopg  # 局部导入：无 psycopg 的环境仍可 import 本模块
            if self._dsn:
                self._conn = psycopg.connect(self._dsn)
            else:
                from tools.audit.repro_replay import default_db_url
                self._conn = psycopg.connect(default_db_url())
            # 只读事务：即使误写也被数据库拒绝（结构性保证"只计量不改数据"）
            self._conn.read_only = True
        return self._conn

    def _rows(self, sql: str, params: dict) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def denominator(self, days: int) -> list[dict[str, Any]]:
        return self._rows(DENOMINATOR_SQL, {"days": int(days)})

    def evaluations(self, mission_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = sorted({int(m) for m in mission_ids})
        if not ids:
            return []
        return self._rows(EVALUATION_SQL, {"mission_ids": ids})

    def review_supply(self, since_iso: str) -> list[dict[str, Any]]:
        return self._rows(REVIEW_SUPPLY_SQL, {"since": since_iso})

    def pending_backlog(self) -> dict[str, Any]:
        rows = self._rows(PENDING_BACKLOG_SQL, {})
        return rows[0] if rows else {}

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class SessionReader:
    """SQLAlchemy Session Reader —— 供平台 API（backend/services/platform_kpi.py）内联调用。

    复用调用方已有连接池，不新建连接；SQL 与 PgReader 同源（queries 常量），
    保证 API 上的读数与离线计量脚本读数口径一致。
    """

    def __init__(self, db: Any):
        self.db = db

    _NAMED = re.compile(r"%\((?P<k>\w+)\)s")

    def _rows(self, sql: str, params: dict) -> list[dict[str, Any]]:
        """SQLAlchemy 用 ``:name`` 占位；本模块 SQL 常量用 pyformat，就地转换（单一真源）。"""
        from sqlalchemy import text  # 局部导入：工具层不强依赖 sqlalchemy
        sa_sql = self._NAMED.sub(lambda m: ":" + m.group("k"), sql)
        res = self.db.execute(text(sa_sql), params)
        return [dict(r) for r in res.mappings().all()]

    def denominator(self, days: int) -> list[dict[str, Any]]:
        return self._rows(DENOMINATOR_SQL, {"days": int(days)})

    def evaluations(self, mission_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = sorted({int(m) for m in mission_ids})
        if not ids:
            return []
        return self._rows(EVALUATION_SQL, {"mission_ids": ids})

    def pending_backlog(self) -> dict[str, Any]:
        rows = self._rows(PENDING_BACKLOG_SQL, {})
        return rows[0] if rows else {}