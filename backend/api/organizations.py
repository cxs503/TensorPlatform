"""组织/租户管理 API"""
from fastapi import APIRouter, Depends, HTTPException
from database.config import get_db
from sqlalchemy.orm import Session
from sqlalchemy import func
from models.core import Organization
from api.deps import require_admin, require_auth
from api.tenant_access import assert_organization_access, is_admin

router = APIRouter(prefix="/organizations", tags=["组织管理"])


@router.get("")
def list_orgs(workspace_type: str = None, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    q = db.query(Organization)
    if not is_admin(current_user):
        q = q.filter(Organization.id == current_user.get("organization_id"))
    if workspace_type:
        q = q.filter(Organization.workspace == workspace_type)
    orgs = q.order_by(Organization.id).all()
    return {"total": len(orgs), "items": [o.to_dict() for o in orgs]}


@router.get("/stats", dependencies=[Depends(require_admin)])
def stats(db: Session = Depends(get_db)):
    total = db.query(func.count(Organization.id)).scalar()
    active = db.query(func.count(Organization.id)).filter(Organization.status == "active").scalar()
    by_industry = db.query(
        Organization.industry, func.count(Organization.id)
    ).group_by(Organization.industry).all()
    return {
        "total": total, "active": active,
        "by_industry": [{"industry": r[0], "count": r[1]} for r in by_industry],
    }


@router.post("", dependencies=[Depends(require_admin)])
def create(req: dict, db: Session = Depends(get_db)):
    if not req.get("name"):
        raise HTTPException(400, "name required")
    org = Organization(
        name=req["name"],
        industry=req.get("industry", ""),
        plan=req.get("plan", "free"),
        contact_email=req.get("email", ""),
        workspace=req.get("workspace", "personal"),
        status="active",
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return {"id": org.id, "name": org.name, "status": org.status}


@router.get("/{org_id}")
def get(org_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_auth)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "not found")
    assert_organization_access(org.id, current_user)
    return org.to_dict()


@router.put("/{org_id}", dependencies=[Depends(require_admin)])
def update(org_id: int, req: dict, db: Session = Depends(get_db)):
    db.query(Organization).filter(Organization.id == org_id).update({
        Organization.plan: req.get("plan", "free"),
        Organization.status: req.get("status", "active"),
    })
    db.commit()
    return {"ok": True}
