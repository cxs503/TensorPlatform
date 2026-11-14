"""Batch mission submission API.

Endpoints (mounted before the single-mission router):

* ``POST /tensor/missions/batch``      — accept a JSON **array** of mission specs,
  land them atomically (when the schema allows it) and return a ``batch_id``.
* ``GET  /tensor/missions/batch/{id}`` — progress summary for one batch.

Why this module exists
----------------------
The agent harness enqueues work in bursts (campaign phases, refill waves, shard
fan-out).  Today that is done with N sequential ``POST /tensor/missions`` calls
issued by whatever script happens to own the burst, which is not atomic, has no
grouping identity and no way to ask "how far is that wave?".

Design contract
---------------
The batch identity needs two pieces of storage: a ``batch_id`` column on
``tensor_dev_missions`` and a ``tensor_mission_batches`` registry table
(see ``backend/migrations/104_tensor_mission_batches.sql``).

Because a platform schema change needs owner approval, this module **probes the
database at request time** and reports which of two modes it used:

``atomic``      both schema objects present → one transaction, all-or-nothing.
                Evidence grade: REAL.

``degraded_sequential``
                schema not migrated → the API degrades to exactly what a bulk
                script would do (loop over single-row inserts, one commit each),
                with the batch registry kept in a sidecar SQLite file that this
                module creates itself (no DDL on the platform database).
                Evidence grade: MOCK — "batch submit script", *not* atomic.
                The response carries ``atomic: false`` and
                ``schema_migration_required`` so callers cannot mistake it for
                an atomic batch.

Idempotency
-----------
``Idempotency-Key`` header, or — when absent — a content hash of the submitted
array scoped to the organisation.  Re-submitting the same batch (same key)
returns **409** with the identity of the batch that already exists and inserts
nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
from typing import Any

from services.mission_acceptance import (
    AcceptanceCriteriaError,
    acceptance_criteria_sha256,
    canonicalize_acceptance_criteria,
)

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

# --- platform wiring ---------------------------------------------------------
# When the module is imported from inside the backend app (``api.routes...``)
# the real platform dependencies are used; standalone imports (docs, tooling,
# unit tests that only need the router object) fall back to an equivalent
# bearer-token guard so the auth contract never silently disappears.
try:  # pragma: no cover - import branch depends on deployment layout
    from api.deps import require_auth, require_role  # type: ignore
    from database.config import get_db  # type: ignore

    AUTH_PROVIDER = "api.deps"
except ModuleNotFoundError:  # pragma: no cover - standalone import
    from fastapi import Header as _Header

    def get_db():  # type: ignore[misc]
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database.config.get_db unavailable: mount the router inside the backend app",
        )

    def _fallback_require_auth(authorization: str | None = _Header(default=None)) -> dict:
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="需要登录认证",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {"role": "ADMIN", "auth": "fallback_bearer"}

    def require_auth(authorization: str | None = _Header(default=None)) -> dict:  # type: ignore[misc]
        return _fallback_require_auth(authorization)

    def require_role(*_roles: str):  # type: ignore[misc]
        return _fallback_require_auth

    AUTH_PROVIDER = "batch_missions.fallback_bearer"


MISSION_TABLE = "tensor_dev_missions"
BATCH_TABLE = "tensor_mission_batches"
MIGRATION_FILE = "docs/proposals/104_tensor_mission_batches.sql"
REGISTRY_ENV = "BATCH_MISSION_REGISTRY"
DEFAULT_REGISTRY = "artifacts/batch_missions/registry.sqlite3"

MISSION_STATUSES = (
    "queued",
    "claimed",
    "running",
    "awaiting_acceptance",
    "succeeded",
    "failed",
    "stopped",
)
TERMINAL_STATUSES = ("succeeded", "failed", "stopped")
MAX_BATCH = 500
DEFAULT_BUDGET = {"max_tool_calls": 80, "max_wall_seconds": 7200, "max_tokens": 3_000_000}

MODE_ATOMIC = "atomic"
MODE_DEGRADED = "degraded_sequential"
EVIDENCE = {MODE_ATOMIC: "REAL", MODE_DEGRADED: "MOCK"}

router = APIRouter(prefix="/tensor/missions", tags=["开发任务-批量"])


# --- request / response models ----------------------------------------------
class BatchMissionItem(BaseModel):
    """One mission specification inside the submitted array."""

    goal: str = Field(min_length=1, max_length=20_000)
    repo: str = Field(min_length=1, max_length=120)
    base_ref: str = Field(default="main", max_length=120)
    budget: dict[str, Any] | None = None
    model: str | None = Field(default=None, max_length=120)
    runner: str | None = Field(default=None, max_length=120)
    co_repos: list[str] | None = None
    parent_mission_id: int | None = None
    acceptance_criteria: list[dict[str, Any] | str] = Field(default_factory=list)

    @field_validator("acceptance_criteria")
    @classmethod
    def _canonical_criteria(cls, value: list[dict[str, Any] | str]) -> list[dict[str, Any]]:
        try:
            return canonicalize_acceptance_criteria(value)
        except AcceptanceCriteriaError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("goal", "repo", "base_ref")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


# --- schema capability probing ----------------------------------------------
def _dialect(db: Session) -> str:
    bind = db.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", "") or ""


def _table_columns(db: Session, table: str) -> set[str]:
    dialect = _dialect(db)
    try:
        if dialect == "sqlite":
            rows = db.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return {str(r[1]) for r in rows}
        rows = db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :t"
            ),
            {"t": table},
        ).fetchall()
        return {str(r[0]) for r in rows}
    except Exception:  # pragma: no cover - unknown dialect / missing table
        return set()


def capabilities(db: Session) -> dict[str, Any]:
    """Report which batch primitives the *live* schema provides."""
    columns = _table_columns(db, MISSION_TABLE)
    batch_columns = _table_columns(db, BATCH_TABLE)
    has_batch_column = "batch_id" in columns
    has_batch_table = "batch_id" in batch_columns and "idempotency_key" in batch_columns
    atomic = bool(columns) and has_batch_column and has_batch_table
    criteria_columns = {
        "acceptance_criteria", "acceptance_criteria_sha256",
        "acceptance_criteria_frozen_at", "acceptance_criteria_version",
        "acceptance_criteria_freeze_source",
    }
    return {
        "mission_table": MISSION_TABLE,
        "mission_table_present": bool(columns),
        "batch_id_column": has_batch_column,
        "batch_table": has_batch_table,
        "acceptance_criteria_contract": criteria_columns <= columns,
        "mode": MODE_ATOMIC if atomic else MODE_DEGRADED,
        "evidence": EVIDENCE[MODE_ATOMIC if atomic else MODE_DEGRADED],
        "schema_migration_required": None if atomic else MIGRATION_FILE,
    }


# --- degraded-mode sidecar registry -----------------------------------------
def _registry_path() -> Path:
    path = Path(os.getenv(REGISTRY_ENV) or DEFAULT_REGISTRY)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _registry_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_registry_path(), timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS batch_registry (
            batch_id        TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            requested       INTEGER NOT NULL,
            mission_ids     TEXT NOT NULL,
            mode            TEXT NOT NULL,
            created_by      TEXT,
            created_at      TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _registry_get(key: str) -> dict[str, Any] | None:
    with closing(_registry_conn()) as conn:
        row = conn.execute(
            "SELECT batch_id, idempotency_key, organization_id, requested, mission_ids, "
            "mode, created_by, created_at FROM batch_registry WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    return {
        "batch_id": row[0],
        "idempotency_key": row[1],
        "organization_id": row[2],
        "requested": row[3],
        "mission_ids": json.loads(row[4]),
        "mode": row[5],
        "created_by": row[6],
        "created_at": row[7],
    }


def _registry_put(record: dict[str, Any]) -> None:
    with closing(_registry_conn()) as conn, conn:
        conn.execute(
            "INSERT INTO batch_registry (batch_id, idempotency_key, organization_id, requested,"
            " mission_ids, mode, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                record["batch_id"],
                record["idempotency_key"],
                record["organization_id"],
                record["requested"],
                json.dumps(record["mission_ids"]),
                record["mode"],
                record.get("created_by"),
                record["created_at"],
            ),
        )


# --- helpers ----------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_key(items: list[BatchMissionItem], organization_id: int) -> str:
    payload = json.dumps(
        [_item_payload(i) for i in items], sort_keys=True, separators=(",", ":"), default=str
    )
    digest = hashlib.sha256(f"{organization_id}:{payload}".encode("utf-8")).hexdigest()
    return f"auto:{digest}"


def _item_payload(item: BatchMissionItem) -> dict[str, Any]:
    return item.model_dump(exclude_none=True)


def _json_expr(dialect: str, param: str) -> str:
    """JSON column expression that works on both PostgreSQL (jsonb) and SQLite."""
    return f"CAST({param} AS jsonb)" if dialect == "postgresql" else param


def _insert_mission(
    db: Session,
    *,
    org_id: int,
    item: BatchMissionItem,
    batch_id: str | None,
    created_by: str | None,
) -> int:
    dialect = _dialect(db)
    columns = [
        "organization_id",
        "goal",
        "repo",
        "base_ref",
        "budget",
        "status",
        "result",
        "created_by",
    ]
    values = [
        ":org_id",
        ":goal",
        ":repo",
        ":base_ref",
        _json_expr(dialect, ":budget"),
        "'queued'",
        _json_expr(dialect, ":result"),
        ":created_by",
    ]
    if batch_id is not None:
        columns.append("batch_id")
        values.append(":batch_id")

    criteria_columns = {
        "acceptance_criteria", "acceptance_criteria_sha256",
        "acceptance_criteria_frozen_at", "acceptance_criteria_version",
        "acceptance_criteria_freeze_source",
    }
    present_criteria_columns = criteria_columns & _table_columns(db, MISSION_TABLE)
    if present_criteria_columns and present_criteria_columns != criteria_columns:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="mission acceptance criteria schema is only partially migrated",
        )
    if present_criteria_columns == criteria_columns:
        criteria = canonicalize_acceptance_criteria(item.acceptance_criteria)
        criteria_digest = acceptance_criteria_sha256(criteria)
        columns.extend([
            "acceptance_criteria",
            "acceptance_criteria_freeze_source",
            "acceptance_criteria_frozen_at",
            "acceptance_criteria_sha256",
            "acceptance_criteria_version",
        ])
        values.extend([
            _json_expr(dialect, ":acceptance_criteria"),
            "'mission_created'",
            ":acceptance_criteria_frozen_at",
            ":acceptance_criteria_sha256",
            ":acceptance_criteria_version",
        ])
    elif item.acceptance_criteria:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="acceptance criteria require migration 111 before batch submission",
        )
    else:
        criteria = []
        criteria_digest = None

    for optional, value in (("model", item.model), ("runner", item.runner), ("parent_mission_id", item.parent_mission_id)):
        if value is not None:
            columns.append(optional)
            values.append(f":{optional}")
    params: dict[str, Any] = {
        "org_id": org_id,
        "goal": item.goal,
        "repo": item.repo,
        "base_ref": item.base_ref,
        "budget": json.dumps(item.budget or DEFAULT_BUDGET),
        "result": "{}",
        "created_by": created_by,
        "batch_id": batch_id,
        "model": item.model,
        "runner": item.runner,
        "parent_mission_id": item.parent_mission_id,
    }
    if present_criteria_columns == criteria_columns:
        params.update({
            "acceptance_criteria": json.dumps(criteria, ensure_ascii=False),
            "acceptance_criteria_sha256": criteria_digest,
            "acceptance_criteria_frozen_at": datetime.now(timezone.utc).isoformat(),
            "acceptance_criteria_version": 1,
        })
    sql = (
        f"INSERT INTO {MISSION_TABLE} ({', '.join(columns)}) "
        f"VALUES ({', '.join(values)}) RETURNING id"
    )
    row = db.execute(text(sql), params).fetchone()
    if row is None:  # pragma: no cover - dialect without RETURNING
        raise HTTPException(status_code=500, detail="insert did not return a mission id")
    return int(row[0])


def _existing_batch(db: Session, key: str, *, table: bool) -> dict[str, Any] | None:
    if not table:
        return _registry_get(key)
    row = db.execute(
        text(
            f"SELECT batch_id, idempotency_key, organization_id, requested_count, mission_ids,"
            f" mode, created_by, created_at FROM {BATCH_TABLE} WHERE idempotency_key = :k"
        ),
        {"k": key},
    ).fetchone()
    if not row:
        return None
    ids = row[4]
    if isinstance(ids, str):
        ids = json.loads(ids)
    return {
        "batch_id": str(row[0]),
        "idempotency_key": row[1],
        "organization_id": row[2],
        "requested": row[3],
        "mission_ids": [int(i) for i in (ids or [])],
        "mode": row[5],
        "created_by": row[6],
        "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7]),
    }


def _conflict(existing: dict[str, Any]) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "batch_already_submitted",
            "idempotency_key": existing["idempotency_key"],
            "batch_id": existing["batch_id"],
            "mission_ids": existing["mission_ids"],
            "message": "this batch was already accepted; nothing was inserted",
        },
    )


def _resolve_org(organization_id: int | None, user: dict[str, Any] | None) -> int:
    if organization_id is not None:
        return int(organization_id)
    if user:
        for key in ("organization_id", "org_id"):
            if user.get(key) is not None:
                return int(user[key])
    return int(os.getenv("BATCH_DEFAULT_ORG_ID", "1"))


def _created_by(user: dict[str, Any] | None) -> int | None:
    """Numeric author id (``tensor_dev_missions.created_by`` is a BIGINT FK).

    Usernames are deliberately not used: PostgreSQL rejects a string for a
    bigint column, which is exactly what a prod probe caught.
    """
    if not user:
        return None
    for key in ("user_id", "id"):
        value = user.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


# --- endpoints --------------------------------------------------------------
@router.post(
    "/batch",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))],
    summary="批量提交开发任务（JSON 数组，返回 batch_id）",
)
def create_batch(
    items: list[BatchMissionItem],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    organization_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    if not items:
        raise HTTPException(status_code=422, detail="batch must contain at least one mission")
    if len(items) > MAX_BATCH:
        raise HTTPException(
            status_code=422, detail=f"batch size {len(items)} exceeds limit {MAX_BATCH}"
        )

    caps = capabilities(db)
    org_id = _resolve_org(organization_id, user)
    key = idempotency_key or _content_key(items, org_id)
    atomic = caps["mode"] == MODE_ATOMIC

    existing = _existing_batch(db, key, table=atomic)
    if existing:
        raise _conflict(existing)

    batch_id = str(uuid.uuid4())
    creator = _created_by(user)

    if atomic:
        mission_ids: list[int] = []
        try:
            # single transaction: the batch row and every mission land together
            db.execute(
                text(
                    f"INSERT INTO {BATCH_TABLE} (batch_id, organization_id, idempotency_key,"
                    " requested_count, mission_ids, mode, created_by)"
                    " VALUES (:b, :o, :k, :n, CAST(:ids AS jsonb), :m, :c)"
                    if _dialect(db) == "postgresql"
                    else f"INSERT INTO {BATCH_TABLE} (batch_id, organization_id, idempotency_key,"
                    " requested_count, mission_ids, mode, created_by)"
                    " VALUES (:b, :o, :k, :n, :ids, :m, :c)"
                ),
                {
                    "b": batch_id,
                    "o": org_id,
                    "k": key,
                    "n": len(items),
                    "ids": json.dumps([]),
                    "m": MODE_ATOMIC,
                    "c": creator,
                },
            )
            for item in items:
                mission_ids.append(
                    _insert_mission(
                        db, org_id=org_id, item=item, batch_id=batch_id, created_by=creator
                    )
                )
            db.execute(
                text(
                    f"UPDATE {BATCH_TABLE} SET mission_ids = {_json_expr(_dialect(db), ':ids')}"
                    " WHERE batch_id = :b"
                ),
                {
                    "ids": json.dumps(mission_ids),
                    "b": batch_id,
                },
            )
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:  # integrity / connection errors → nothing persisted
            db.rollback()
            duplicate = _existing_batch(db, key, table=True)
            if duplicate:
                raise _conflict(duplicate) from exc
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "batch_insert_failed",
                    "mode": MODE_ATOMIC,
                    "atomic": True,
                    "applied": [],
                    "reason": f"{type(exc).__name__}: {exc}",
                    "message": "transaction rolled back; no mission was enqueued",
                },
            ) from exc
        return {
            "batch_id": batch_id,
            "status": "queued",
            "count": len(mission_ids),
            "requested": len(items),
            "mission_ids": mission_ids,
            "mode": MODE_ATOMIC,
            "evidence": EVIDENCE[MODE_ATOMIC],
            "atomic": True,
            "idempotency_key": key,
            "created_at": _now(),
            "schema": caps,
        }

    # ---- degraded path: sequential single-row submit (non-atomic, MOCK) ----
    applied: list[int] = []
    try:
        for index, item in enumerate(items):
            try:
                applied.append(
                    _insert_mission(db, org_id=org_id, item=item, batch_id=None, created_by=creator)
                )
                db.commit()
            except Exception as exc:
                db.rollback()
                if applied:
                    try:
                        _registry_put(
                            {
                                "batch_id": batch_id,
                                "idempotency_key": key,
                                "organization_id": org_id,
                                "requested": len(items),
                                "mission_ids": applied,
                                "mode": MODE_DEGRADED,
                                "created_by": creator,
                                "created_at": _now(),
                            }
                        )
                    except sqlite3.Error:  # pragma: no cover - registry unavailable
                        pass
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "error": "batch_insert_failed",
                        "mode": MODE_DEGRADED,
                        "atomic": False,
                        "batch_id": batch_id,
                        "applied": applied,
                        "failed_index": index,
                        "reason": f"{type(exc).__name__}: {exc}",
                        "message": "degraded mode cannot roll back already committed rows",
                    },
                ) from exc
    except HTTPException:
        raise

    _registry_put(
        {
            "batch_id": batch_id,
            "idempotency_key": key,
            "organization_id": org_id,
            "requested": len(items),
            "mission_ids": applied,
            "mode": MODE_DEGRADED,
            "created_by": creator,
            "created_at": _now(),
        }
    )
    return {
        "batch_id": batch_id,
        "status": "queued",
        "count": len(applied),
        "requested": len(items),
        "mission_ids": applied,
        "mode": MODE_DEGRADED,
        "evidence": EVIDENCE[MODE_DEGRADED],
        "atomic": False,
        "idempotency_key": key,
        "created_at": _now(),
        "schema": caps,
        "notice": (
            "platform schema lacks batch_id/tensor_mission_batches: batch ran as a "
            f"loop of single inserts (script-equivalent, MOCK). Apply {MIGRATION_FILE} "
            "for atomic batches."
        ),
    }


@router.get(
    "/batch/{batch_id}",
    dependencies=[Depends(require_auth)],
    summary="批量任务进度摘要",
)
def get_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    include_missions: bool = Query(default=True),
) -> dict[str, Any]:
    caps = capabilities(db)
    record: dict[str, Any] | None = None
    if caps["batch_table"]:
        row = db.execute(
            text(
                f"SELECT batch_id, idempotency_key, organization_id, requested_count,"
                f" mission_ids, mode, created_by, created_at FROM {BATCH_TABLE}"
                " WHERE batch_id = :b"
            ),
            {"b": batch_id},
        ).fetchone()
        if row:
            ids = row[4]
            if isinstance(ids, str):
                ids = json.loads(ids)
            record = {
                "batch_id": str(row[0]),
                "idempotency_key": row[1],
                "organization_id": row[2],
                "requested": row[3],
                "mission_ids": [int(i) for i in (ids or [])],
                "mode": row[5],
                "created_by": row[6],
                "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7]),
            }
    if record is None:
        with closing(_registry_conn()) as conn:
            row = conn.execute(
                "SELECT batch_id, idempotency_key, organization_id, requested, mission_ids,"
                " mode, created_by, created_at FROM batch_registry WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row:
            record = {
                "batch_id": row[0],
                "idempotency_key": row[1],
                "organization_id": row[2],
                "requested": row[3],
                "mission_ids": json.loads(row[4]),
                "mode": row[5],
                "created_by": row[6],
                "created_at": row[7],
            }
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "batch_not_found", "batch_id": batch_id})

    mission_ids: list[int] = [int(i) for i in record["mission_ids"]]
    rows: list[tuple[int, str]] = []
    if mission_ids:
        marks = ", ".join(f":p{i}" for i in range(len(mission_ids)))
        params = {f"p{i}": mid for i, mid in enumerate(mission_ids)}
        rows = [
            (int(r[0]), str(r[1]))
            for r in db.execute(
                text(f"SELECT id, status FROM {MISSION_TABLE} WHERE id IN ({marks}) ORDER BY id"),
                params,
            ).fetchall()
        ]

    counts = {name: 0 for name in MISSION_STATUSES}
    for _mid, st in rows:
        counts[st if st in counts else "queued"] += 1
    terminal = sum(counts[s] for s in TERMINAL_STATUSES)
    missing = len(mission_ids) - len(rows)
    finished = terminal + missing >= record["requested"] if record["requested"] else False

    summary: dict[str, Any] = {
        "batch_id": record["batch_id"],
        "batch_status": "finished" if finished else "in_progress",
        "mode": record["mode"],
        "evidence": EVIDENCE.get(record["mode"], "WITHHELD"),
        "atomic": record["mode"] == MODE_ATOMIC,
        "requested": record["requested"],
        "count": len(mission_ids),
        "organization_id": record["organization_id"],
        "created_by": record["created_by"],
        "created_at": record["created_at"],
        "idempotency_key": record["idempotency_key"],
        "status_counts": counts,
        "terminal": terminal,
        "missing_rows": missing,
        "progress_percent": round(100.0 * terminal / record["requested"], 2)
        if record["requested"]
        else 0.0,
    }
    if include_missions:
        summary["missions"] = [{"id": mid, "status": st} for mid, st in rows]
    return summary