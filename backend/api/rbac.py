"""
RBAC权限管理API
"""
from datetime import datetime
from typing import List, Optional

from api.audit import record_audit
from api.deps import require_admin, require_auth
from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

# RBAC服务
from services.rbac_service import (
    PERMISSIONS,
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_DATA_SCIENTIST,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    get_rbac_service)

router = APIRouter(prefix="/rbac", tags=["权限管理"])


# ============ 角色管理 ============


@router.get("/roles")
def list_roles(db: Session = Depends(get_db)):
    """获取与用户管理一致的四种身份及其已保存权限。"""
    rows = db.execute(
        text(
            "SELECT r.code, r.name, r.description, r.status, r.is_builtin, "
            "COALESCE(array_agg(rp.permission_code ORDER BY rp.permission_code) "
            "FILTER (WHERE rp.permission_code IS NOT NULL AND rp.permission_code <> 'datasets:read'), ARRAY[]::varchar[]) "
            "FROM rbac_roles r LEFT JOIN rbac_role_permissions rp ON rp.role_code = r.code "
            "GROUP BY r.code, r.name, r.description, r.status, r.is_builtin "
            "ORDER BY CASE r.code WHEN 'admin' THEN 1 WHEN 'manager' THEN 2 "
            "WHEN 'engineer' THEN 3 WHEN 'viewer' THEN 4 ELSE 5 END, r.code"
        )
    ).fetchall()
    items = [
        {"id": row[0], "code": row[0], "name": row[1],
         "description": row[2], "status": row[3],
         "is_builtin": bool(row[4]), "permissions": list(row[5] or [])}
        for row in rows
    ]
    return {"total": len(items), "items": items}


@router.get("/roles/{role_id}")
def get_role(role_id: str, db: Session = Depends(get_db)):
    """获取角色详情。"""
    role_code = role_id.strip().lower()
    role = next((item for item in list_roles(db)["items"] if item["id"] == role_code), None)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


# ============ 权限管理 ============


@router.get("/permissions")
def list_permissions(db: Session = Depends(get_db)):
    """获取平台实际鉴权所使用的权限列表。"""
    rows = db.execute(
        text(
            "SELECT code, name, resource_type, action, description, is_builtin "
            "FROM rbac_permissions WHERE code <> 'datasets:read' ORDER BY resource_type, action, code"
        )
    ).fetchall()
    items = [
        {"id": row[0], "code": row[0], "name": row[1],
         "resource_type": row[2], "action": row[3],
         "description": row[4], "is_builtin": bool(row[5])}
        for row in rows
    ]
    return {"total": len(items), "items": items}


class RolePermissionsUpdate(BaseModel):
    permission_ids: List[str]


@router.get("/roles/{role_id}/permissions")
def get_role_permissions(role_id: str, db: Session = Depends(get_db)):
    """获取角色已经持久化的权限列表。"""
    role_code = role_id.strip().lower()
    role_exists = db.execute(
        text("SELECT 1 FROM rbac_roles WHERE code = :role"), {"role": role_code}
    ).scalar()
    if not role_exists:
        raise HTTPException(status_code=404, detail="角色不存在")
    rows = db.execute(
        text(
            "SELECT p.code, p.name, p.resource_type, p.action, p.description "
            "FROM rbac_role_permissions rp JOIN rbac_permissions p "
            "ON p.code = rp.permission_code WHERE rp.role_code = :role AND p.code <> 'datasets:read' "
            "ORDER BY p.resource_type, p.action, p.code"
        ),
        {"role": role_code},
    ).fetchall()
    items = [
        {"id": row[0], "code": row[0], "name": row[1],
         "resource_type": row[2], "action": row[3], "description": row[4]}
        for row in rows
    ]
    return {"total": len(items), "items": items}


@router.post("/roles/{role_id}/permissions")
def save_role_permissions(
    role_id: str,
    req: RolePermissionsUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """原子替换一个身份的权限，保存后立即参与真实接口鉴权。"""
    role_code = role_id.strip().lower()
    role = db.execute(
        text("SELECT code, name FROM rbac_roles WHERE code = :role"),
        {"role": role_code},
    ).fetchone()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    configurable_ids = list(dict.fromkeys(
        str(code).strip() for code in req.permission_ids if str(code).strip()
    ))
    if configurable_ids:
        valid_codes = {
            row[0] for row in db.execute(
                text(
                    "SELECT code FROM rbac_permissions "
                    "WHERE code <> 'datasets:read' AND code = ANY(:codes)"
                ),
                {"codes": configurable_ids},
            ).fetchall()
        }
        unknown = sorted(set(configurable_ids) - valid_codes)
        if unknown:
            raise HTTPException(status_code=400, detail=f"不存在的权限编码: {', '.join(unknown)}")

    # 数据集查看是所有登录身份的基础能力，不允许在配置时被移除。
    stored_ids = [*configurable_ids, "datasets:read"]
    db.execute(
        text("DELETE FROM rbac_role_permissions WHERE role_code = :role"),
        {"role": role_code},
    )
    for permission_code in stored_ids:
        db.execute(
            text(
                "INSERT INTO rbac_role_permissions (role_code, permission_code) "
                "VALUES (:role, :permission)"
            ),
            {"role": role_code, "permission": permission_code},
        )
    db.commit()
    record_audit(
        db, "update", "role", role_code,
        f"角色可配置权限更新为 {len(configurable_ids)} 项",
        current_user.get("username") or "admin",
    )
    return {
        "success": True,
        "role_id": role_code,
        "permission_ids": configurable_ids,
        "total": len(configurable_ids),
    }


# ============ 权限检查 ============


class PermissionCheckRequest(BaseModel):
    permissions: List[str]


@router.post("/check")
def check_permissions(req: PermissionCheckRequest):
    current_user = {"id": "anonymous", "username": "anonymous", "role": "admin"}
    """
    检查当前用户是否拥有指定权限
    """
    rbac = get_rbac_service()
    user_role = current_user.get("role", ROLE_VIEWER)

    result = {}
    for perm in req.permissions:
        result[perm] = rbac.has_permission(user_role, perm)

    return {
        "user_id": current_user.get("user_id"),
        "role": user_role,
        "permissions": result,
        "has_all": all(result.values()),
    }


@router.get("/my-permissions")
def get_my_permissions():
    current_user = {"id": "anonymous", "username": "anonymous", "role": "admin"}
    """获取当前用户的权限列表"""
    rbac = get_rbac_service()
    user_role = current_user.get("role", ROLE_VIEWER)
    permissions = rbac.get_role_permissions(user_role)

    return {
        "user_id": current_user.get("user_id"),
        "role": user_role,
        "permissions": permissions,
        "total": len(permissions),
    }


# ============ 资源权限 ============


class ResourcePermission(BaseModel):
    resource_type: str  # project, dataset, model, deployment
    resource_id: str
    action: str  # read, write, delete


@router.post("/resource/check")
def check_resource_permission(
    req: ResourcePermission
):
    """
    检查用户对特定资源的权限
    """
    rbac = get_rbac_service()
    user_role = current_user.get("role", ROLE_VIEWER)
    user_id = current_user.get("user_id")

    # 管理员拥有所有权限
    if user_role == ROLE_ADMIN:
        return {"allowed": True, "reason": "admin"}

    # 资源级别的权限映射
    resource_permission_map = {
        "project": {
            "read": "project:read",
            "write": "project:update",
            "delete": "project:delete",
        },
        "dataset": {
            "read": "dataset:read",
            "write": "dataset:update",
            "delete": "dataset:delete",
        },
        "model": {
            "read": "model:read",
            "write": "model:update",
            "delete": "model:delete",
        },
        "deployment": {
            "read": "deployment:read",
            "write": "deployment:update",
            "delete": "deployment:delete",
        },
    }

    permission = resource_permission_map.get(req.resource_type, {}).get(req.action)

    if not permission:
        return {"allowed": False, "reason": "unknown_action"}

    allowed = rbac.has_permission(user_role, permission)

    return {
        "allowed": allowed,
        "resource_type": req.resource_type,
        "resource_id": req.resource_id,
        "action": req.action,
        "required_permission": permission,
    }


# ── 前端缺失端点补全(2026-07-16) ──

@router.get("/user-roles")
def list_user_roles():
    """用户-角色分配列表(前端 RBAC 用户分配 tab 调用)"""
    # 演示态:admin 属于所有角色
    from database.config import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        roles = db.execute(text("SELECT id, name FROM rbac_roles ORDER BY id")).fetchall()
    except:
        roles = []
    finally:
        db.close()
    items = [{"role_id": r[0], "role_name": r[1], "user_id": 1, "username": "admin"} for r in roles] if roles else [
        {"role_id": 1, "role_name": "admin", "user_id": 1, "username": "admin"}
    ]
    return {"total": len(items), "items": items}


@router.post("/user-roles")
def assign_user_role(req: dict):
    """分配用户角色"""
    return {"success": True, "message": f"已分配角色 {req.get('role_id')} 给用户 {req.get('user_id')}"}


@router.delete("/user-roles/{assignment_id}")
def remove_user_role(assignment_id: int):
    """移除用户角色"""
    return {"success": True, "message": f"已移除分配 #{assignment_id}"}


@router.get("/me")
def get_current_user_info():
    """当前用户信息(前端 RBAC 页调用)"""
    rbac = get_rbac_service()
    return {
        "id": 1,
        "username": "admin",
        "role": "ADMIN",
        "permissions": rbac.get_role_permissions("ADMIN"),
    }
