"""
项目 CRUD 操作
"""
from datetime import datetime
from uuid import uuid4

from models.project import Project
from schemas.schemas import ProjectCreate, ProjectUpdate
from sqlalchemy.orm import Session


def get_projects(
    db: Session,
    user_id: str,
    skip: int = 0,
    limit: int = 10,
    project_type: str = None,
    status: str = None,
):
    """获取项目列表"""
    query = db.query(Project).filter(Project.owner_id == user_id)
    if project_type:
        query = query.filter(Project.project_type == project_type)
    if status:
        query = query.filter(Project.status == status)

    total = query.count()
    projects = query.offset(skip).limit(limit).all()
    return projects, total


def get_project(db: Session, project_id: str, user_id: str):
    """获取单个项目"""
    return (
        db.query(Project)
        .filter(Project.id == project_id, Project.owner_id == user_id)
        .first()
    )


def create_project(db: Session, project: ProjectCreate, user_id: str):
    """创建项目"""
    db_project = Project(
        id=str(uuid4()),
        name=project.name,
        description=project.description,
        project_type=project.project_type,
        tags=project.tags,
        owner_id=user_id,
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


def update_project(db: Session, project_id: str, project: ProjectUpdate, user_id: str):
    """更新项目"""
    db_project = get_project(db, project_id, user_id)
    if not db_project:
        return None

    update_data = project.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_project, key, value)

    db_project.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_project)
    return db_project


def delete_project(db: Session, project_id: str, user_id: str):
    """删除项目"""
    db_project = get_project(db, project_id, user_id)
    if not db_project:
        return False
    db.delete(db_project)
    db.commit()
    return True
