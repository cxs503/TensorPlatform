"""Shared helpers for the tensor console operation-plane modules."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.domain.status_contracts import session_event_contract
from api.tenant_access import (
    create_organization_id,
    is_admin,
    organization_id as user_organization_id,
)

LOG_TAIL_BYTES = 16 * 1024
# G-B 单一契约源：会话事件词表从 backend/contracts/session_event_kinds.v1.json 派生
# （此前为手写元组——加词要同时手改本文件与 TensorPhysics 任务级发射器，靠人脑同步）。
# 新增词项只许进契约文件；两侧任一单边加词都会由契约测试判红。
EVENT_KINDS = tuple(session_event_contract()["kinds"])
STAGES = ("S0", "S1", "S2", "S3", "S4")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


def resolved_org(requested_org_id: int | None, user: dict, db: Session) -> int:
    """Org scope with a platform-default fallback for org-less admins.

    Single-tenant fallback: when the user has no binding, no org was
    requested, and the whole platform has exactly one organization, bind
    to it implicitly — there is no second tenant to leak to. Keeps
    legacy single-tenant fleets (all agents sharing one service account)
    working after an upgrade introduces organization binding.
    """
    if requested_org_id is None and user_organization_id(user) is None:
        only = db.execute(
            text("SELECT MIN(id), COUNT(*) FROM organizations")
        ).first()
        if only and only[1] == 1:
            return int(only[0])
    organization_id = create_organization_id(requested_org_id, user)
    if organization_id is None:
        if is_admin(user):
            fallback = db.execute(text("SELECT MIN(id) FROM organizations")).scalar()
            if fallback is not None:
                return int(fallback)
        raise HTTPException(
            status_code=400,
            detail="organization_id is required for users without a default organization",
        )
    return int(organization_id)


def truncate_log_tail(log_tail: str | None) -> str | None:
    if log_tail is None:
        return None
    return log_tail[-LOG_TAIL_BYTES:]


def next_event_seq(db: Session, session_id: int) -> int:
    row = db.execute(
        text("SELECT COALESCE(MAX(seq), 0) FROM tensor_agent_session_events WHERE session_id = :sid"),
        {"sid": session_id},
    ).scalar()
    return int(row) + 1


def insert_event_row(
    db: Session, session_id: int, seq: int, kind: str, payload: dict[str, Any]
) -> None:
    """唯一事件行写手（G-B 一事实单源）。

    ``tensor_agent_session_events`` 的 INSERT 语句只许出现在这里：批量端点
    （api/tensor_sessions.py）与单条 append_event 共用同一行写入形态，
    守卫测试 ``test_status_contracts.TestGB_SingleWriter`` 扫全仓 SQL 字面量。
    """
    db.execute(
        text("""
            INSERT INTO tensor_agent_session_events (session_id, seq, kind, payload)
            VALUES (:sid, :seq, :kind, CAST(:payload AS jsonb))
        """),
        {
            "sid": session_id,
            "seq": seq,
            "kind": kind,
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
        },
    )


def append_event(db: Session, session_id: int, kind: str, payload: dict[str, Any]) -> int:
    if kind not in EVENT_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown event kind: {kind}")
    # Concurrent appenders (agent event bridge + lifecycle endpoints) both
    # derive seq from MAX(seq)+1 — serialize per session with a transaction
    # advisory lock so the unique (session_id, seq) can't collide.
    from sqlalchemy.exc import IntegrityError

    for _ in range(3):
        db.execute(text("SELECT pg_advisory_xact_lock(:sid)"), {"sid": session_id})
        seq = next_event_seq(db, session_id)
        try:
            insert_event_row(db, session_id, seq, kind, payload)
            return seq
        except IntegrityError:
            db.rollback()  # releases the advisory lock; retry re-derives seq
    raise HTTPException(status_code=500, detail="event seq conflict after retries")


def require_stage(stage: str) -> str:
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=f"stage must be one of {STAGES}")
    return stage
