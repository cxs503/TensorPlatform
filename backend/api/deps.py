"""
API 依赖项 - 认证与权限验证
"""
import os
from typing import Optional

import jwt
from database.config import get_db
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

security = HTTPBearer(auto_error=False)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "open-digital-foundation-secret-key-2024")
ALGORITHM = "HS256"

# 角色权限定义
ROLE_PERMISSIONS = {
    "admin": ["*"],  # 管理员拥有所有权限
    "data_scientist": [
        "projects:read",
        "projects:write",
        "datasets:read",
        "datasets:upload",
        "datasets:connect",
        "datasets:download",
        "datasets:edit",
        "datasets:process",
        "datasets:analyze",
        "datasets:delete",
        "models:read",
        "models:write",
        "training:read",
        "training:write",
        "deployment:read",
        "deployment:write",
        "experiments:read",
        "experiments:write",
    ],
    "engineer": [
        "projects:read", "projects:write", "datasets:read",
        "datasets:upload", "datasets:connect", "datasets:download", "datasets:edit",
        "datasets:process", "datasets:analyze", "datasets:delete",
        "models:read", "models:write", "training:read", "training:write",
        "deployment:read", "deployment:write", "experiments:read", "experiments:write",
    ],
    "manager": ["*"],
    "analyst": [
        "projects:read",
        "datasets:read",
        "models:read",
        "training:read",
        "deployment:read",
        "reports:read",
        "reports:write",
        "dashboards:read",
    ],
    "operator": [
        "projects:read",
        "models:read",
        "deployment:read",
        "deployment:write",
        "monitoring:read",
        "monitoring:write",
        "alerts:read",
        "alerts:write",
    ],
    "viewer": [
        "projects:read",
        "datasets:read",
        "models:read",
        "dashboards:read",
    ],
}

# 不需要认证的公开路由
PUBLIC_ROUTES = [
    "/auth/login",
    "/auth/register",
    "/auth/refresh",
    "/health",
    "/docs",
    "/openapi.json",
    "/",
]


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> dict:
    """获取当前用户，可选认证"""
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")

        # 验证用户存在
        result = db.execute(
            text(
                "SELECT id, username, email, role, is_active, organization_id, features FROM users WHERE id = :id"
            ),
            {"id": user_id},
        ).fetchone()

        if not result or result[4] != 1:
            return None

        return {
            "id": result[0],
            "username": result[1],
            "email": result[2],
            "role": result[3],
            "organization_id": result[5],
            "features": result[6],
        }
    except:
        return None


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> dict:
    """要求认证"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录认证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")

        # 验证用户存在且活跃
        result = db.execute(
            text(
                "SELECT id, username, email, role, is_active, organization_id, features FROM users WHERE id = :id"
            ),
            {"id": user_id},
        ).fetchone()

        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在"
            )

        if result[4] != 1:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已被禁用")

        return {
            "id": result[0],
            "username": result[1],
            "email": result[2],
            "role": result[3],
            "organization_id": result[5],
            "features": result[6],
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token已过期，请刷新或重新登录"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的Token")


def require_role(*allowed_roles: str):
    """要求特定角色"""

    normalized_roles = {str(role).lower() for role in allowed_roles}

    async def role_checker(current_user: dict = Depends(require_auth)) -> dict:
        current_role = str(current_user.get("role") or "").split(".")[-1].lower()
        if (
            current_role not in normalized_roles
            and current_role != "admin"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {', '.join(allowed_roles)}",
            )
        return current_user

    return role_checker


def require_permission(permission: str):
    """要求特定权限"""

    async def permission_checker(
        current_user: dict = Depends(require_auth),
        db: Session = Depends(get_db),
    ) -> dict:
        role = str(current_user.get("role") or "").split(".")[-1].lower()

        # 保存后的角色权限直接参与下一次接口鉴权。
        try:
            rows = db.execute(
                text(
                    "SELECT rp.permission_code FROM rbac_role_permissions rp "
                    "JOIN rbac_roles r ON r.code = rp.role_code "
                    "WHERE rp.role_code = :role AND r.status = 'active'"
                ),
                {"role": role},
            ).fetchall()
            role_exists = db.execute(
                text("SELECT 1 FROM rbac_roles WHERE code = :role"), {"role": role}
            ).scalar()
            permissions = [row[0] for row in rows] if role_exists else ROLE_PERMISSIONS.get(role, [])
        except Exception:
            db.rollback()
            permissions = ROLE_PERMISSIONS.get(role, [])

        if "*" not in permissions and permission not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限: {permission}"
            )
        return current_user

    return permission_checker


# 便捷依赖
require_admin = require_role("admin")
require_data_scientist = require_role("admin", "data_scientist")
require_analyst = require_role("admin", "data_scientist", "analyst")
require_operator = require_role("admin", "operator")
