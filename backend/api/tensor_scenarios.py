"""Tensor console: scenario registry (stage ladder S0..S4)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db

router = APIRouter(prefix="/tensor/scenarios", tags=["科研场景"])


def _row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "scenario_key": r["scenario_key"],
        "display_name": r["display_name"],
        "description": r["description"],
        "current_stage": r["current_stage"],
        "stage_status": r["stage_status"] or {},
        "organization_id": r["organization_id"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


@router.get("", dependencies=[Depends(require_auth)])
def list_scenarios(
    scenario_key: str | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    filters = "organization_id = :org"
    params: dict[str, Any] = {"org": org_id}
    if scenario_key:
        filters += " AND scenario_key = :key"
        params["key"] = scenario_key
    rows = db.execute(
        text(f"""
            SELECT id, organization_id, scenario_key, display_name, description,
                   current_stage, stage_status, created_at, updated_at
            FROM tensor_scenarios WHERE {filters} ORDER BY scenario_key
        """),
        params,
    ).mappings().all()
    return {"items": [_row_to_dict(r) for r in rows], "total": len(rows)}


@router.get("/{scenario_key}", dependencies=[Depends(require_auth)])
def get_scenario(
    scenario_key: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(None, user, db)
    row = db.execute(
        text("""
            SELECT id, organization_id, scenario_key, display_name, description,
                   current_stage, stage_status, created_at, updated_at
            FROM tensor_scenarios
            WHERE organization_id = :org AND scenario_key = :key
        """),
        {"org": org_id, "key": scenario_key},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="scenario not found")
    return _row_to_dict(row)


class ScenarioCreate(BaseModel):
    scenario_key: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    organization_id: int | None = Field(default=None, ge=1)


@router.post("", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_scenario(
    body: ScenarioCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id = resolved_org(body.organization_id, user, db)
    exists = db.execute(
        text("SELECT id FROM tensor_scenarios WHERE organization_id=:org AND scenario_key=:key"),
        {"org": org_id, "key": body.scenario_key},
    ).scalar()
    if exists:
        raise HTTPException(status_code=409, detail="scenario already exists")
    row = db.execute(
        text("""
            INSERT INTO tensor_scenarios
                (organization_id, scenario_key, display_name, description, current_stage, stage_status)
            VALUES (:org, :key, :name, :desc, 'S0',
                    CAST(:stage_status AS jsonb))
            RETURNING id, organization_id, scenario_key, display_name, description,
                      current_stage, stage_status, created_at, updated_at
        """),
        {
            "org": org_id,
            "key": body.scenario_key,
            "name": body.display_name or body.scenario_key,
            "desc": body.description,
            "stage_status": '{"S0": {"state": "pending"}}',
        },
    ).mappings().first()
    db.commit()
    return _row_to_dict(row)
