"""Small, shared tenant-boundary helpers for production core resources."""

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth
from database.config import get_db


def normalized_role(user: dict) -> str:
    return str((user or {}).get("role") or "").split(".")[-1].lower()


def is_admin(user: dict) -> bool:
    return normalized_role(user) == "admin"


def organization_id(user: dict):
    return (user or {}).get("organization_id")


def require_organization(user: dict) -> int:
    org_id = organization_id(user)
    if org_id is None:
        raise HTTPException(status_code=403, detail="当前用户尚未绑定组织")
    return int(org_id)


def scope_query(query, column, user: dict):
    """Administrators see all tenants; other roles only see their organization."""
    if is_admin(user):
        return query
    return query.filter(column == require_organization(user))


def create_organization_id(requested_org_id, user: dict):
    """Prevent a non-admin from assigning a new resource to another tenant."""
    own_org_id = organization_id(user)
    if is_admin(user):
        return requested_org_id if requested_org_id is not None else own_org_id
    own_org_id = require_organization(user)
    if requested_org_id is not None and int(requested_org_id) != own_org_id:
        raise HTTPException(status_code=403, detail="不能在其他组织中创建资源")
    return own_org_id


def assert_organization_access(resource_org_id, user: dict):
    """Use 404 for foreign objects to avoid leaking cross-tenant identifiers."""
    if not is_admin(user) and resource_org_id != require_organization(user):
        raise HTTPException(status_code=404, detail="资源不存在")


async def require_dataset_access(
    dataset_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = db.execute(
        text("SELECT organization_id FROM datasets WHERE id=:id"), {"id": dataset_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="数据集不存在")
    assert_organization_access(row[0], user)
    return user


async def require_model_access(
    model_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = db.execute(
        text("SELECT organization_id FROM models WHERE id=:id"), {"id": model_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    assert_organization_access(row[0], user)
    return user


async def require_deployment_access(
    dep_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = db.execute(
        text(
            "SELECT m.organization_id FROM deployments d "
            "JOIN models m ON m.id=d.model_id WHERE d.id=:id"
        ),
        {"id": dep_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="部署不存在")
    assert_organization_access(row[0], user)
    return user


async def require_training_job_access(
    job_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    row = db.execute(
        text("SELECT organization_id FROM training_jobs WHERE training_job_id=:id"),
        {"id": job_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    assert_organization_access(row[0], user)
    return user
