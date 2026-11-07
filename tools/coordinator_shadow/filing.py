#!/usr/bin/env python3
"""coordinator_shadow v1 —— 立单通道（IN27-H1）：只立单，不动手。

写入面只有两处，都由机器门把守
------------------------------
1. **诊断单入口**（本模块）：``INSERT INTO tensor_dev_missions (...) SELECT ... WHERE NOT EXISTS``
   ——幂等靠库里的唯一索引 ``(organization_id, task_key)``，``task_key = shadowdiag-<fingerprint>``。
   重复立单在 SQL 层就被拦掉，不依赖进程内状态（进程重启也不会重复立单）。
2. **影子账本**（``ledger.py``）：自己的拍记录，append-only。

``WriteGuard`` 只放行这两条语句模板：凡是别名的 INSERT/UPDATE/DELETE 一律拒。这样即使
上层逻辑被改坏，也越不过「影子不许手术」的边界。

领取与处置的读取
----------------
``open_task_keys()`` 给立单去重用（还开着的诊断单）；``fetch_filings()`` 给验收算
「被舰队领取执行 ≥3 件」与「误报率」用（状态/领取者/result 里的 ``diagnosis_disposition``）。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .diagnose import FilingProposal, REPO, TASK_KEY_PREFIX
from .pulse import iso, parse_ts

TASK_KEY_LIKE = TASK_KEY_PREFIX + "-%"
OPEN_STATUSES = ("queued", "claimed", "running", "awaiting_pr_merge", "suspended")

#: 立单唯一写语句。``origin`` 只能取 ``owner``/``agent``（库里 CHECK 约束
#: ``tensor_dev_missions_origin_check``，实跑用 'mechanism' 会被拒）——影子立单记 ``agent``。
INSERT_MISSION_SQL = (
    "INSERT INTO tensor_dev_missions (organization_id, goal, repo, runner, origin, model, "
    "task_key, status, report_owed, budget) SELECT :org, :goal, :repo, 'native', 'agent', NULL, "
    ":task_key, 'queued', false, {budget} "
    "WHERE NOT EXISTS (SELECT 1 FROM tensor_dev_missions WHERE organization_id = :org "
    "AND task_key = :task_key) RETURNING id"
)

DEFAULT_BUDGET = {"max_tool_calls": 80, "max_wall_seconds": 7200, "max_tokens": 2000000}


class BoundaryViolation(RuntimeError):
    """试图在立单通道上执行未经批准的写语句。"""


class WriteGuard:
    """只放行「立单」这一条写语句模板。"""

    def __init__(self, inner, allowed_prefix: str = "INSERT INTO TENSOR_DEV_MISSIONS") -> None:
        self._inner = inner
        self.allowed_prefix = allowed_prefix.upper()
        self.audit: list[str] = []

    def write(self, sql: str, params: Mapping[str, Any]) -> Sequence[Sequence[Any]]:
        body = " ".join(sql.split())
        if not body.upper().startswith(self.allowed_prefix):
            raise BoundaryViolation(f"立单通道只放行 {self.allowed_prefix}，实际：{body[:48]!r}")
        if ";" in body.rstrip(";"):
            raise BoundaryViolation("立单通道禁多语句")
        self.audit.append(body)
        return self._inner.write(body, params)


class Intake(Protocol):  # pragma: no cover
    def file(self, proposal: FilingProposal, org_id: int) -> dict[str, Any]:
        ...


@dataclass
class FilingOutcome:
    task_key: str
    mission_id: int | None
    status: str            # filed | duplicate | error
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"task_key": self.task_key, "mission_id": self.mission_id,
                "status": self.status, "reason": self.reason}


class DryRunIntake:
    """演练/测试用：不写库，返回递增的假 mission id，记录被「立」的单。"""

    def __init__(self, existing: Iterable[str] = ()) -> None:
        self.existing = set(existing)
        self.filed: list[dict[str, Any]] = []
        self._next = 900000

    def file(self, proposal: FilingProposal, org_id: int = 1) -> FilingOutcome:
        if proposal.task_key in self.existing:
            return FilingOutcome(proposal.task_key, None, "duplicate", "task_key-exists")
        self.existing.add(proposal.task_key)
        self._next += 1
        self.filed.append({"mission_id": self._next, "task_key": proposal.task_key,
                           "goal": proposal.goal, "organ": proposal.organ,
                           "metric": proposal.metric, "severity": proposal.severity})
        return FilingOutcome(proposal.task_key, self._next, "filed")


class PgIntake:
    """生产立单通道：psycopg 连接（写权限），语句经 ``WriteGuard``。"""

    def __init__(self, dsn: str | None = None, budget: Mapping[str, Any] | None = None) -> None:
        import os

        self.dsn = dsn or os.environ.get(
            "TP_DSN", "dbname=tensorplatform user=foundation host=/var/run/postgresql"
        )
        self.budget = dict(budget or DEFAULT_BUDGET)
        self._conn = None
        self.guard = WriteGuard(self)

    @property
    def conn(self):  # pragma: no cover
        if self._conn is None:
            try:
                import psycopg  # type: ignore

                self._conn = psycopg.connect(self.dsn, autocommit=True)
            except ImportError:
                import psycopg2  # type: ignore

                self._conn = psycopg2.connect(self.dsn)
                self._conn.autocommit = True
        return self._conn

    def write(self, sql: str, params: Mapping[str, Any]):  # pragma: no cover
        from .pulse import to_psycopg_placeholders

        cur = self.conn.cursor()
        cur.execute(to_psycopg_placeholders(sql), dict(params))
        rows = cur.fetchall() if cur.description else []
        cur.close()
        return rows

    def file(self, proposal: FilingProposal, org_id: int) -> FilingOutcome:
        sql = INSERT_MISSION_SQL.format(budget="CAST(:budget AS jsonb)")
        params = {"org": org_id, "goal": proposal.goal, "repo": proposal.repo,
                  "task_key": proposal.task_key, "budget": json.dumps(self.budget)}
        try:
            rows = self.guard.write(sql, params)
        except BoundaryViolation:
            raise
        except Exception as exc:  # 唯一索引竞态 → 视作已存在
            msg = f"{type(exc).__name__}:{str(exc)[:160]}"
            if "duplicate key" in msg or "unique" in msg.lower():
                return FilingOutcome(proposal.task_key, None, "duplicate", "unique-index")
            return FilingOutcome(proposal.task_key, None, "error", msg)
        if not rows:
            return FilingOutcome(proposal.task_key, None, "duplicate", "not-exists-guard")
        return FilingOutcome(proposal.task_key, int(rows[0][0]), "filed")


class SqliteIntake:
    """sqlite 版立单通道（测试用；同一条幂等 SQL，方言差异只在 budget 转换）。"""

    def __init__(self, conn: sqlite3.Connection, budget: Mapping[str, Any] | None = None) -> None:
        self.conn = conn
        self.budget = dict(budget or DEFAULT_BUDGET)
        self.guard = WriteGuard(self)

    def write(self, sql: str, params: Mapping[str, Any]):
        cur = self.conn.execute(sql, dict(params))
        rows = cur.fetchall()
        self.conn.commit()
        return rows

    def file(self, proposal: FilingProposal, org_id: int) -> FilingOutcome:
        sql = INSERT_MISSION_SQL.format(budget=":budget")
        params = {"org": org_id, "goal": proposal.goal, "repo": proposal.repo,
                  "task_key": proposal.task_key, "budget": json.dumps(self.budget)}
        try:
            rows = self.guard.write(sql, params)
        except Exception as exc:
            msg = f"{type(exc).__name__}:{str(exc)[:160]}"
            if "unique" in msg.lower():
                return FilingOutcome(proposal.task_key, None, "duplicate", "unique-index")
            return FilingOutcome(proposal.task_key, None, "error", msg)
        if not rows:
            return FilingOutcome(proposal.task_key, None, "duplicate", "not-exists-guard")
        return FilingOutcome(proposal.task_key, int(rows[0][0]), "filed")


# ─────────────────────────── 读侧（去重与验收） ───────────────────────────


def open_task_keys(fetchall) -> list[str]:
    """还开着的诊断单 task_key（去重用）。``fetchall(sql, params)`` 可注入。"""
    sql = ("SELECT task_key FROM tensor_dev_missions WHERE task_key LIKE :like "
           "AND status IN (" + ",".join(f"'{s}'" for s in OPEN_STATUSES) + ")")
    rows = fetchall(sql, {"like": TASK_KEY_LIKE})
    return [str(r[0]) for r in rows if r and r[0]]


def latest_org_id(fetchall) -> int:
    rows = fetchall("SELECT organization_id FROM tensor_dev_missions ORDER BY id DESC LIMIT 1", {})
    if rows and rows[0] and rows[0][0] is not None:
        return int(rows[0][0])
    raise RuntimeError("无法确定 organization_id（任务表为空）")


DISPOSITION_KEYS = ("diagnosis_disposition", "disposition", "shadow_disposition")


def infer_disposition(row: Mapping[str, Any], *, now: datetime | None = None,
                      stale_hours: float = 48.0) -> str:
    """把一张诊断单的**事后处置**判成一类（供误报率统计）。

    判定优先级：``result`` 里显式 ``diagnosis_disposition`` > 明显未修的终态 > 领取执行。
    ``unclaimed_expired`` 单独成一类：它既不是误报也不是有效诊断，是「没人理」——
    单列出来，避免用「没人领」去稀释误报率。
    """
    status = str(row.get("status") or "")
    result = row.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {}
    result = result if isinstance(result, Mapping) else {}
    for key in DISPOSITION_KEYS:
        val = str(result.get(key) or "").strip().lower()
        if val:
            if val in ("not_a_defect", "duplicate", "no_repro", "false_positive", "wontfix", "invalid"):
                return "false_positive"
            if val in ("fixed", "confirmed", "repaired", "true_positive"):
                return "confirmed"
            if val in ("inconclusive", "partial"):
                return "inconclusive"
    if status in ("succeeded",):
        honest_fail = bool(result.get("honest_fail"))
        return "inconclusive" if honest_fail else "confirmed"
    if status in ("failed", "revoked", "stopped", "cancelled"):
        return "inconclusive"
    if status in ("queued", "claimed", "running", "suspended"):
        created = parse_ts(row.get("created_at"))
        ref = now or datetime.now(timezone.utc)
        if created is not None and (ref - created).total_seconds() / 3600.0 >= stale_hours:
            return "unclaimed_expired" if status == "queued" else "inconclusive"
        return "pending"
    return "inconclusive"


def fetch_filings(fetchall, *, limit: int = 500) -> list[dict[str, Any]]:
    """读出所有影子诊断单的现状（验收与误报率用）。"""
    sql = ("SELECT id, task_key, status, claimed_by, session_id, result, created_at, finished_at "
           "FROM tensor_dev_missions WHERE task_key LIKE :like ORDER BY id DESC")
    rows = fetchall(sql, {"like": TASK_KEY_LIKE})[:limit]
    out = []
    for r in rows:
        out.append({
            "mission_id": int(r[0]),
            "task_key": r[1],
            "status": r[2],
            "claimed_by": r[3],
            "session_id": r[4],
            "result": r[5],
            "created_at": None if r[6] is None else str(r[6]),
            "finished_at": None if r[7] is None else str(r[7]),
        })
    return out