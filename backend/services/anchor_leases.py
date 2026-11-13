"""锚级租约（anchor lease）—— 供单侧共享的「锚占用表」单源。

为什么存在
----------
任务榜（feature_ledger）里的开放锚此前只能由 dispatcher 侧守护进程造单
（``case_feeder`` 供算例、``selfstarter`` 钉席自驱）。两位供给者若各自记账，
同一个锚会被供两单 = 双供（同一基准被两个 agent 各做一遍，且互相把对方的
结论当噪声）。本模块把「锚当前被谁占用」这件事收敛到**一张表 + 三个函数**：

    anchor_leases(anchor_slug, holder, source, mission_id, expires_at, released_at, ...)

任何供单方（``POST /tensor/board/claim``、``anchor_feeder``、``case_feeder``、
dispatcher 兜底）都先 :func:`acquire` 再落 mission，写完 mission 回填
``mission_id``；完单 :func:`release`。表上的**部分唯一索引**
``(anchor_slug) WHERE released_at IS NULL`` 让并发抢占在数据库里分出唯一赢家，
不依赖任何进程内锁——进程崩了也不会有幽灵占用（租约到期即自动可抢）。

设计约束
--------
* 零领域语义：本模块只认识 anchor_slug / holder / source 这些治理词。
* 只做占用，不做派单：goal 组装在调用方（见 ``api/tensor_board.py``），
  本模块不 import 任何 API 层模块，可被后端进程与离线守护脚本同时复用。
* 幂等与可回收：同一 holder 重复领取自己持有的锚返回 ``already_held``；
  过期租约由下一次 :func:`acquire` 顺带回收（不需要常驻 reaper 也能自愈），
  另有 :func:`reap_expired` 供巡检调用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

DEFAULT_TTL_SECONDS = 6 * 3600
MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 24 * 3600

#: 供单方标识（写进 anchor_leases.source，用于审计「谁供的这单」）
SOURCE_BOARD_CLAIM = "board-claim"
SOURCE_ANCHOR_FEEDER = "anchor-feeder"
SOURCE_CASE_FEEDER = "case-feeder"
SOURCE_DISPATCHER = "dispatcher"

_DDL = """
CREATE TABLE IF NOT EXISTS anchor_leases (
    id              BIGSERIAL PRIMARY KEY,
    anchor_slug     TEXT NOT NULL,
    holder          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'board-claim',
    organization_id BIGINT,
    mission_id      BIGINT,
    acquired_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    released_at     TIMESTAMPTZ,
    release_reason  TEXT,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb
)
"""
_INDEXES = (
    # 活租约（released_at IS NULL）在同一锚上唯一 —— 并发抢锚的数据库级裁判。
    "CREATE UNIQUE INDEX IF NOT EXISTS anchor_leases_live_uq "
    "ON anchor_leases (anchor_slug) WHERE released_at IS NULL",
    "CREATE INDEX IF NOT EXISTS anchor_leases_expiry_idx "
    "ON anchor_leases (expires_at) WHERE released_at IS NULL",
)

#: 建表 advisory lock（两个进程同时首调时不会互相拿到半成品表）
_DDL_LOCK_KEY = 0x414E4348  # 'ANCH'


@dataclass
class Lease:
    """一次租约尝试的结果。``acquired=False`` 时 ``reason`` 说明为什么没拿到。"""

    acquired: bool
    slug: str
    lease_id: int | None = None
    holder: str | None = None
    expires_at: datetime | None = None
    reason: str | None = None
    reclaimed_lease_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "acquired": self.acquired,
            "anchor": self.slug,
            "lease_id": self.lease_id,
            "holder": self.holder,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reason": self.reason,
            "reclaimed_lease_id": self.reclaimed_lease_id,
        }


def ensure_schema(db: Session) -> None:
    """幂等建表（PostgreSQL）。生产库无 alembic 迁移面依赖，首调自愈。"""
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _DDL_LOCK_KEY})
    db.execute(text(_DDL))
    for stmt in _INDEXES:
        db.execute(text(stmt))
    db.commit()


def clamp_ttl(ttl_seconds: int | None) -> int:
    if ttl_seconds is None:
        return DEFAULT_TTL_SECONDS
    try:
        v = int(ttl_seconds)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, v))


def acquire(
    db: Session,
    slug: str,
    *,
    holder: str,
    source: str = SOURCE_BOARD_CLAIM,
    organization_id: int | None = None,
    ttl_seconds: int | None = None,
    meta: dict[str, Any] | None = None,
) -> Lease:
    """抢占锚级租约（在调用方事务内执行，不 commit）。

    语义：
    * 已有**未过期活租约** → ``acquired=False``（并发时由部分唯一索引裁决，只有一个赢家）；
    * 同一 holder 已持有 → ``acquired=True, reason='already_held'``（幂等重试）；
    * 过期活租约 → 先标记 released（reason='expired'）再插新租约；返回体里带
      ``reclaimed_lease_id`` 供审计。
    """
    ttl = clamp_ttl(ttl_seconds)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)

    # ① 过期回收：本锚上已过期的活租约先释放（过期不再占用，回收不需要常驻巡检）
    expired = db.execute(
        text(
            "UPDATE anchor_leases SET released_at = :now, release_reason = 'expired' "
            "WHERE anchor_slug = :slug AND released_at IS NULL AND expires_at <= :now "
            "RETURNING id"
        ),
        {"slug": slug, "now": now},
    ).first()

    # ② 幂等：同一 holder 的有效活租约直接复用，不重复插行
    held = db.execute(
        text(
            "SELECT id, holder, expires_at FROM anchor_leases "
            "WHERE anchor_slug = :slug AND released_at IS NULL AND expires_at > :now"
        ),
        {"slug": slug, "now": now},
    ).first()
    if held is not None:
        if held[1] == holder:
            return Lease(acquired=True, slug=slug, lease_id=int(held[0]), holder=holder,
                         expires_at=held[2], reason="already_held")
        return Lease(acquired=False, slug=slug, holder=held[1], expires_at=held[2],
                     reason="held_by_other")

    # ③ 插新租约：并发对手插进同一锚时此处被部分唯一索引挡住 → ON CONFLICT 静默失败
    row = db.execute(
        text(
            """
            INSERT INTO anchor_leases
                (anchor_slug, holder, source, organization_id, expires_at, meta)
            VALUES (:slug, :holder, :source, :org, :expires_at, CAST(:meta AS jsonb))
            ON CONFLICT (anchor_slug) WHERE released_at IS NULL DO NOTHING
            RETURNING id, expires_at
            """
        ),
        {"slug": slug, "holder": holder, "source": source, "org": organization_id,
         "expires_at": expires_at, "meta": json.dumps(meta or {}, ensure_ascii=False)},
    ).first()
    if row is None:
        return Lease(acquired=False, slug=slug, reason="lost_race")
    return Lease(acquired=True, slug=slug, lease_id=int(row[0]), holder=holder,
                 expires_at=row[1], reclaimed_lease_id=int(expired[0]) if expired else None)


def bind_mission(db: Session, lease_id: int, mission_id: int) -> None:
    """落 mission 后回填租约（占用表与在飞单双向可查）。"""
    db.execute(
        text("UPDATE anchor_leases SET mission_id = :mid WHERE id = :lid"),
        {"mid": mission_id, "lid": lease_id},
    )


def release(
    db: Session,
    slug: str,
    *,
    holder: str | None = None,
    reason: str = "released",
    lease_id: int | None = None,
) -> int:
    """释放租约。``holder`` 非空时只允许持有者自己释放；返回释放行数。"""
    sql = (
        "UPDATE anchor_leases SET released_at = now(), release_reason = :reason "
        "WHERE released_at IS NULL AND ("
        "  (:lease_id IS NOT NULL AND id = :lease_id) OR "
        "  (:lease_id IS NULL AND anchor_slug = :slug)"
        ") "
    )
    params: dict[str, Any] = {"slug": slug, "reason": reason, "lease_id": lease_id}
    if holder is not None:
        sql += "AND holder = :holder "
        params["holder"] = holder
    res = db.execute(text(sql), params)
    return int(res.rowcount or 0)


def reap_expired(db: Session, *, limit: int = 500) -> list[dict[str, Any]]:
    """巡检式回收（可选）：把过期活租约标 released，返回被回收的锚清单。"""
    rows = db.execute(
        text(
            """
            UPDATE anchor_leases
            SET released_at = now(), release_reason = 'expired'
            WHERE id IN (
                SELECT id FROM anchor_leases
                WHERE released_at IS NULL AND expires_at <= now()
                ORDER BY expires_at
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, anchor_slug, holder, mission_id
            """
        ),
        {"limit": limit},
    ).all()
    return [
        {"lease_id": int(r[0]), "anchor": r[1], "holder": r[2], "mission_id": r[3]}
        for r in rows
    ]


def live_leases(db: Session, slug: str | None = None) -> list[dict[str, Any]]:
    """当前活租约视图（诊断面；供 board 看板与巡检脚本共用）。"""
    sql = (
        "SELECT id, anchor_slug, holder, source, mission_id, acquired_at, expires_at, "
        "       (expires_at <= now()) AS expired, meta "
        "FROM anchor_leases WHERE released_at IS NULL "
    )
    params: dict[str, Any] = {}
    if slug:
        sql += "AND anchor_slug = :slug "
        params["slug"] = slug
    sql += "ORDER BY anchor_slug"
    return [
        {
            "lease_id": int(r[0]),
            "anchor": r[1],
            "holder": r[2],
            "source": r[3],
            "mission_id": r[4],
            "acquired_at": r[5].isoformat() if r[5] else None,
            "expires_at": r[6].isoformat() if r[6] else None,
            "expired": bool(r[7]),
            "meta": r[8] or {},
        }
        for r in db.execute(text(sql), params).all()
    ]