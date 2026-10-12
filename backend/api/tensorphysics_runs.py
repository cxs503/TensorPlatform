"""TensorPhysics run registry API.

Stores compact run manifests and artifact references, never bulk simulation fields.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_permission
from api.tenant_access import (
    assert_organization_access,
    create_organization_id,
    is_admin,
)
from database.config import get_db

router = APIRouter(
    prefix="/tensorphysics/runs",
    tags=["TensorPhysics运行登记"],
)


class RunRegistration(BaseModel):
    """Compact provenance record sent by TensorPhysics after a run."""

    run_id: str = Field(min_length=1, max_length=200)
    status: str = Field(pattern="^(planned|validating|queued|running|succeeded|failed|cancelled|preempted)$")
    validation_status: str = Field(pattern="^(not_run|passed|failed|incomplete)$")
    manifest: dict[str, Any]
    organization_id: int | None = Field(default=None, ge=1)

    @field_validator("run_id")
    @classmethod
    def run_id_must_be_trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("run_id must not be blank")
        return value

    @field_validator("manifest")
    @classmethod
    def manifest_must_be_compact_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("manifest must contain finite JSON-compatible values") from exc
        if len(encoded.encode("utf-8")) > 1_000_000:
            raise ValueError("manifest exceeds 1 MB; store large fields as artifacts")
        return value


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolved_org(requested_org_id: int | None, user: dict) -> int:
    organization_id = create_organization_id(requested_org_id, user)
    if organization_id is None:
        raise HTTPException(
            status_code=400,
            detail="organization_id is required for users without a default organization",
        )
    return int(organization_id)


@router.post(
    "",
    dependencies=[Depends(require_permission("datasets:upload"))],
)
def register_run(
    request: RunRegistration,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Register a run; retrying the same manifest is idempotent."""
    org_id = _resolved_org(request.organization_id, user)
    manifest = dict(request.manifest)
    if manifest.get("run_id") != request.run_id:
        raise HTTPException(status_code=422, detail="manifest.run_id must match run_id")
    if str(manifest.get("status", "")).lower() != request.status:
        raise HTTPException(status_code=422, detail="manifest.status must match status")
    if str(manifest.get("validation_status", "")).lower() != request.validation_status:
        raise HTTPException(status_code=422, detail="manifest.validation_status must match validation_status")

    manifest_json = _canonical_json(manifest)
    digest = _digest(manifest_json)
    existing = db.execute(
        text("SELECT manifest_sha256 FROM tensorphysics_runs WHERE organization_id=:org AND run_id=:run"),
        {"org": org_id, "run": request.run_id},
    ).fetchone()
    if existing:
        if existing[0] != digest:
            raise HTTPException(status_code=409, detail="run_id already exists with a different manifest")
        return {"run_id": request.run_id, "organization_id": org_id, "created": False, "manifest_sha256": digest}

    insert_result = db.execute(
        text("""
            INSERT INTO tensorphysics_runs
                (organization_id, run_id, status, validation_status, manifest,
                 manifest_sha256, created_by)
            VALUES
                (:org, :run, :status, :validation, CAST(:manifest AS JSONB),
                 :digest, :user_id)
            ON CONFLICT (organization_id, run_id) DO NOTHING
        """),
        {
            "org": org_id,
            "run": request.run_id,
            "status": request.status,
            "validation": request.validation_status,
            "manifest": manifest_json,
            "digest": digest,
            "user_id": user.get("id"),
        },
    )
    created = insert_result.rowcount == 1
    db.commit()
    # A concurrent request may have inserted the same key; compare after commit.
    row = db.execute(
        text("SELECT manifest_sha256 FROM tensorphysics_runs WHERE organization_id=:org AND run_id=:run"),
        {"org": org_id, "run": request.run_id},
    ).fetchone()
    if not row or row[0] != digest:
        raise HTTPException(status_code=409, detail="run_id already exists with a different manifest")
    return {"run_id": request.run_id, "organization_id": org_id, "created": created, "manifest_sha256": digest}


@router.get(
    "/{run_id}",
    dependencies=[Depends(require_permission("datasets:read"))],
)
def get_run(
    run_id: str,
    organization_id: int | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Retrieve a manifest only within the caller's organization boundary."""
    org_id = _resolved_org(organization_id, user)
    row = db.execute(
        text("""
            SELECT organization_id, run_id, status, validation_status, manifest,
                   manifest_sha256, created_at, updated_at
            FROM tensorphysics_runs
            WHERE organization_id=:org AND run_id=:run
        """),
        {"org": org_id, "run": run_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "organization_id": row["organization_id"],
        "run_id": row["run_id"],
        "status": row["status"],
        "validation_status": row["validation_status"],
        "manifest": row["manifest"],
        "manifest_sha256": row["manifest_sha256"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
