"""Shared helpers for the tensor console operation-plane modules."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.tenant_access import create_organization_id, is_admin

LOG_TAIL_BYTES = 16 * 1024
EVENT_KINDS = (
    "plan",
    "execution_started",
    "run_registered",
    "validation_seen",
    "decision",
    "approval_requested",
    "approval_decided",
    "note",
    "error",
    "tool_call",
    "tool_result",
    "web_search",
    "code_commit",
)
STAGES = ("S0", "S1", "S2", "S3", "S4")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


def resolved_org(requested_org_id: int | None, user: dict, db: Session) -> int:
    """Org scope with a platform-default fallback for org-less admins."""
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


def append_event(db: Session, session_id: int, kind: str, payload: dict[str, Any]) -> int:
    if kind not in EVENT_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown event kind: {kind}")
    seq = next_event_seq(db, session_id)
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
    return seq


def require_stage(stage: str) -> str:
    if stage not in STAGES:
        raise HTTPException(status_code=422, detail=f"stage must be one of {STAGES}")
    return stage
