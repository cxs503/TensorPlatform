"""Tensor console: six-repo workspace snapshots (the Tensor family panel).

The host executor periodically publishes live git state for every repo in
/srv/tensor/shared/tensor_eco; the overview board renders the latest snapshot.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from database.config import get_db

router = APIRouter(prefix="/tensor/workspace", tags=["科研运行台·工作区"])


class WorkspaceRepo(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    branch: str | None = Field(default=None, max_length=200)
    head: str | None = Field(default=None, max_length=64)
    head_short: str | None = Field(default=None, max_length=16)
    commit_at: str | None = Field(default=None, max_length=40)
    subject: str | None = Field(default=None, max_length=200)
    dirty_files: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=120)


class WorkspacePost(BaseModel):
    executor_id: str = Field(min_length=1, max_length=120)
    repos: list[WorkspaceRepo] = Field(default_factory=list, max_length=16)


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def publish_snapshot(
    body: WorkspacePost,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    import json as _json

    # executor row first (FK), heartbeat included
    db.execute(
        text("""
            INSERT INTO tensor_executors (executor_id, heartbeat_at)
            VALUES (:eid, now())
            ON CONFLICT (executor_id) DO UPDATE SET heartbeat_at = now()
        """),
        {"eid": body.executor_id},
    )
    db.execute(
        text("""
            INSERT INTO tensor_workspace_state (executor_id, repos, captured_at)
            VALUES (:eid, CAST(:repos AS jsonb), now())
            ON CONFLICT (executor_id) DO UPDATE
            SET repos = EXCLUDED.repos, captured_at = now()
        """),
        {"eid": body.executor_id,
         "repos": _json.dumps([r.model_dump(exclude_none=True) for r in body.repos])},
    )
    db.commit()
    return {"ok": True, "repos": len(body.repos)}


@router.get("", dependencies=[Depends(require_auth)])
def latest_snapshot(
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row: Any = db.execute(text("""
        SELECT executor_id, repos, captured_at
        FROM tensor_workspace_state
        ORDER BY captured_at DESC LIMIT 1
    """)).mappings().first()
    if not row:
        return {"repos": [], "captured_at": None, "executor_id": None}
    return {
        "repos": row["repos"] or [],
        "captured_at": row["captured_at"].isoformat(),
        "executor_id": row["executor_id"],
    }
