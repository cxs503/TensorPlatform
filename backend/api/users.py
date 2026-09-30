"""
用户管理 API（仅管理员）

提供用户列表 / 详情 / 更新（功能权限白名单 features、角色、启用状态、姓名）。
- features: 允许的菜单 path 白名单(null/空=不限制); 配合前端菜单过滤 + 路由守卫。
- role: 原生 PG enum(userrole), 统一存大写名(与 UserRole 枚举名一致)。
- 防自锁: 禁止管理员修改自己的 role / is_active。
"""
import json
from typing import List, Optional

from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_admin, require_auth

router = APIRouter(prefix="/users", tags=["用户管理"], dependencies=[Depends(require_admin)])

VALID_ROLES = {"admin", "manager", "engineer", "viewer"}

# 与 _row_to_dict 的列顺序保持一致
_SELECT_COLS = "id, username, email, full_name, role, is_active, features, created_at"


def _row_to_dict(row) -> dict:
    role = row[4]
    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "full_name": row[3],
        "role": role,
        "is_active": row[5],
        "features": row[6],  # psycopg2 自动把 JSONB 解为 list/None
        "is_admin": str(role).split(".")[-1].lower() == "admin",
        "created_at": row[7].isoformat() if row[7] else None,
    }


@router.get("")
def list_users(db: Session = Depends(get_db)):
    """列出全部用户（不含密码哈希）"""
    rows = db.execute(
        text(f"SELECT {_SELECT_COLS} FROM users ORDER BY created_at NULLS LAST, id")
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.get("/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    row = db.execute(
        text(f"SELECT {_SELECT_COLS} FROM users WHERE id = :id"), {"id": user_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _row_to_dict(row)


class UserUpdate(BaseModel):
    features: Optional[List[str]] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    full_name: Optional[str] = None


@router.put("/{user_id}")
def update_user(
    user_id: int,
    req: UserUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_auth),
):
    """更新单个用户。防自锁: 禁止改自己的 role / is_active(仅当实际变化时拦截)。"""
    cur = db.execute(
        text("SELECT id, role, is_active FROM users WHERE id = :id"), {"id": user_id}
    ).fetchone()
    if not cur:
        raise HTTPException(status_code=404, detail="用户不存在")

    is_self = current_user.get("id") == user_id
    cur_role_norm = str(cur[1]).split(".")[-1].lower()
    cur_active = bool(cur[2])
    sets, params = [], {}

    if req.role is not None:
        norm = str(req.role).strip().lower()
        if norm not in VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"非法角色: {req.role}")
        if is_self and norm != cur_role_norm:
            raise HTTPException(status_code=400, detail="不能修改自己的角色")
        sets.append("role = :role")
        params["role"] = norm.upper()  # 与 SAEnum(UserRole) 名称一致

    if req.is_active is not None:
        if is_self and bool(req.is_active) != cur_active:
            raise HTTPException(status_code=400, detail="不能停用自己的账号")
        sets.append("is_active = :is_active")
        params["is_active"] = bool(req.is_active)

    if req.full_name is not None:
        sets.append("full_name = :full_name")
        params["full_name"] = req.full_name

    if req.features is not None:
        # 规范化: 仅保留非空字符串
        feats = [f for f in req.features if isinstance(f, str) and f.strip()]
        sets.append("features = CAST(:features AS jsonb)")
        params["features"] = json.dumps(feats)

    if not sets:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    params["id"] = user_id
    db.execute(text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id"), params)
    db.commit()

    row = db.execute(
        text(f"SELECT {_SELECT_COLS} FROM users WHERE id = :id"), {"id": user_id}
    ).fetchone()
    return _row_to_dict(row)
