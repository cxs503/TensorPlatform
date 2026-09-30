"""
项目 API — 数据库持久化版本
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.config import get_db
from models.core import Project, ProjectStatus

router = APIRouter(prefix="/projects", tags=["项目管理"])


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.id.desc()).limit(50).all()
    return {"total": len(projects), "items": [p.to_dict() for p in projects]}


@router.post("")
def create_project(req: dict, db: Session = Depends(get_db)):
    project = Project(
        name=req.get("name", "新项目"),
        description=req.get("description"),
        status=ProjectStatus.ACTIVE,
        owner_id=req.get("owner_id"),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project.to_dict()


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project.to_dict()


@router.put("/{project_id}")
def update_project(project_id: int, req: dict, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    for k, v in req.items():
        if hasattr(project, k) and k != "id":
            setattr(project, k, v)
    db.commit()
    return project.to_dict()


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    db.delete(project)
    db.commit()
    return {"status": "deleted"}
