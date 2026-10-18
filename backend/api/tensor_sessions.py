"""Tensor console: agent session timeline (append-only event stream)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from api.domain.tensor_common import EVENT_KINDS, append_event, resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/sessions", tags=["Agent会话"])


@router.get("", dependencies=[Depends(require_auth)])
def list_sessions(
    execution_id: int | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    limit = max(1, min(limit, 200))
    filters = "s.organization_id = :org"
    params: dict[str, Any] = {"org": org_id}
    if execution_id:
        filters += " AND s.execution_id = :eid"
        params["eid"] = execution_id
    rows = db.execute(
        text(f"""
            SELECT s.id, s.execution_id, s.agent_name, s.status, s.budget,
                   s.created_at, s.closed_at,
                   (SELECT count(*) FROM tensor_agent_session_events e
                    WHERE e.session_id = s.id) AS event_count
            FROM tensor_agent_sessions s WHERE {filters}
            ORDER BY s.id DESC LIMIT :limit
        """),
        {**params, "limit": limit},
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "execution_id": r["execution_id"],
                "agent_name": r["agent_name"],
                "status": r["status"],
                "budget": r["budget"] or {},
                "created_at": r["created_at"].isoformat(),
                "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
                "event_count": r["event_count"],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/{session_id}", dependencies=[Depends(require_auth)])
def get_session(
    session_id: int,
    after_seq: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    row = db.execute(
        text("""
            SELECT s.id, s.organization_id, s.execution_id, s.agent_name, s.status,
                   s.budget, s.created_at, s.closed_at
            FROM tensor_agent_sessions s WHERE s.id = :sid
        """),
        {"sid": session_id},
    ).mappings().first()
    if not row or int(row["organization_id"]) != int(org_id):
        raise HTTPException(status_code=404, detail="session not found")
    limit = max(1, min(limit, 2000))
    events = db.execute(
        text("""
            SELECT seq, kind, payload, created_at
            FROM tensor_agent_session_events
            WHERE session_id = :sid AND seq > :after
            ORDER BY seq LIMIT :limit
        """),
        {"sid": session_id, "after": after_seq, "limit": limit},
    ).mappings().all()
    return {
        "id": row["id"],
        "execution_id": row["execution_id"],
        "agent_name": row["agent_name"],
        "status": row["status"],
        "budget": row["budget"] or {},
        "created_at": row["created_at"].isoformat(),
        "closed_at": row["closed_at"].isoformat() if row["closed_at"] else None,
        "events": [
            {
                "seq": e["seq"],
                "kind": e["kind"],
                "payload": e["payload"] or {},
                "created_at": e["created_at"].isoformat(),
            }
            for e in events
        ],
        "latest_seq": events[-1]["seq"] if events else after_seq,
    }


class EventAppend(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/{session_id}/events", dependencies=[Depends(require_auth)])
def append_session_event(
    session_id: int,
    body: EventAppend,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    if body.kind not in EVENT_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {EVENT_KINDS}")
    exists = db.execute(
        text("SELECT id FROM tensor_agent_sessions WHERE id = :sid"),
        {"sid": session_id},
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="session not found")
    seq = append_event(db, session_id, body.kind, body.payload)
    db.commit()
    return {"session_id": session_id, "seq": seq, "kind": body.kind}


class EventBatch(BaseModel):
    events: list[EventAppend] = Field(min_length=1, max_length=64)


@router.post("/{session_id}/events/batch", dependencies=[Depends(require_auth)])
def append_session_events_batch(
    session_id: int,
    body: EventBatch,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Append N events in one HTTP round-trip and one transaction.

    The agent-side event bridge batches tool_call/tool_result pairs instead
    of posting each one; we take the advisory lock once and derive the seq
    range in a single pass rather than locking per event.
    """
    from sqlalchemy.exc import IntegrityError

    for ev in body.events:
        if ev.kind not in EVENT_KINDS:
            raise HTTPException(status_code=422, detail=f"kind must be one of {EVENT_KINDS}")
    exists = db.execute(
        text("SELECT id FROM tensor_agent_sessions WHERE id = :sid"),
        {"sid": session_id},
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(:sid)"), {"sid": session_id})
        seq = int(db.execute(
            text("SELECT COALESCE(MAX(seq), 0) FROM tensor_agent_session_events WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar())
        seqs: list[int] = []
        for ev in body.events:
            seq += 1
            db.execute(
                text("""
                    INSERT INTO tensor_agent_session_events (session_id, seq, kind, payload)
                    VALUES (:sid, :seq, :kind, CAST(:payload AS jsonb))
                """),
                {
                    "sid": session_id,
                    "seq": seq,
                    "kind": ev.kind,
                    "payload": json.dumps(ev.payload, ensure_ascii=False, default=str),
                },
            )
            seqs.append(seq)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="event seq conflict — retry the batch")
    return {
        "session_id": session_id,
        "appended": len(seqs),
        "first_seq": seqs[0],
        "last_seq": seqs[-1],
    }
