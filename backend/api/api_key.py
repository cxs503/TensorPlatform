"""
API密钥管理API

真实实现：使用 raw SQL(text) + get_db，密钥以 sha256 哈希存储，
明文密钥仅在创建时返回一次。表结构通过 CREATE TABLE IF NOT EXISTS 自动建表。
"""
import hashlib
import secrets
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database.config import get_db
from utils.dependencies import get_current_user

router = APIRouter(prefix="/api-keys", tags=["API密钥管理"])

# 表名常量
API_KEYS_TABLE = "api_keys"

# 建表 DDL：id(自增主键)、name、key_hash(sha256)、key_prefix(前8位用于展示)、
# created_at、last_used_at、is_active
_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {API_KEYS_TABLE} (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    key_prefix VARCHAR(16) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
)
"""


def _ensure_table(db: Session) -> None:
    """确保 api_keys 表存在（幂等）。"""
    db.execute(text(_CREATE_TABLE_SQL))
    db.commit()


def _hash_key(raw_key: str) -> str:
    """对明文密钥做 sha256 哈希，返回十六进制摘要。"""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _generate_raw_key() -> str:
    """生成新的明文密钥：sk- + 32 字节 urlsafe 随机串。"""
    return "sk-" + secrets.token_urlsafe(32)


class CreateAPIKeyReq(BaseModel):
    name: str


def _require_key_admin(current_user: dict) -> None:
    # The current SQL schema stores platform-wide keys, not per-user ownership.
    if not isinstance(current_user, dict) or current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可管理平台密钥")


@router.post("")
def create_api_key(
    req: CreateAPIKeyReq,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """创建API密钥：生成安全随机密钥，仅本次返回明文。"""
    _require_key_admin(current_user)
    _ensure_table(db)

    raw_key = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:8]  # 如 "sk-XXXXX"
    now = datetime.now()

    try:
        result = db.execute(
            text(
                f"""
                INSERT INTO {API_KEYS_TABLE} (name, key_hash, key_prefix, created_at, is_active)
                VALUES (:name, :key_hash, :key_prefix, :created_at, TRUE)
                RETURNING id, created_at
                """
            ),
            {
                "name": req.name,
                "key_hash": key_hash,
                "key_prefix": key_prefix,
                "created_at": now,
            },
        )
        row = result.fetchone()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建密钥失败: {e}")

    return {
        "id": row.id,
        "name": req.name,
        "key": raw_key,  # 明文密钥，只返回这一次
        "key_prefix": key_prefix,
        "created_at": (row.created_at if row.created_at else now).isoformat(),
        "is_active": True,
        "message": "请妥善保存密钥，此密钥只会显示一次",
    }


@router.get("")
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """列出API密钥（仅展示前缀，永不返回完整密钥/哈希）。"""
    _require_key_admin(current_user)
    _ensure_table(db)

    rows = db.execute(
        text(
            f"""
            SELECT id, name, key_prefix, created_at, last_used_at, is_active
            FROM {API_KEYS_TABLE}
            ORDER BY created_at DESC
            """
        )
    ).fetchall()

    items = [
        {
            "id": r.id,
            "name": r.name,
            "key_prefix": r.key_prefix,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "is_active": r.is_active,
        }
        for r in rows
    ]
    return {"total": len(items), "items": items}


@router.get("/{key_id}")
def get_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """读取平台密钥元数据，绝不返回明文或哈希。"""
    _require_key_admin(current_user)
    _ensure_table(db)
    row = db.execute(
        text(
            f"SELECT id, name, key_prefix, created_at, last_used_at, is_active "
            f"FROM {API_KEYS_TABLE} WHERE id = :id"
        ),
        {"id": key_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="密钥不存在")
    return {
        "id": row.id,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "is_active": row.is_active,
    }


@router.post("/{key_id}/revoke")
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """停用密钥而不删除或轮换其哈希。"""
    _require_key_admin(current_user)
    _ensure_table(db)
    try:
        row = db.execute(
            text(
                f"UPDATE {API_KEYS_TABLE} SET is_active = FALSE "
                "WHERE id = :id RETURNING id"
            ),
            {"id": key_id},
        ).fetchone()
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="撤销密钥失败") from exc
    if not row:
        raise HTTPException(status_code=404, detail="密钥不存在")
    return {"id": row.id, "message": "密钥已撤销"}


@router.get("/{key_id}/usage")
def get_key_usage(
    key_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """当前存储仅记录最近使用时间，不提供不存在的逐次调用日志。"""
    key = get_api_key(key_id, db=db, current_user=current_user)
    return {
        "id": key_id,
        "last_used_at": key["last_used_at"],
        "history_available": False,
    }


@router.delete("/{key_id}")
def delete_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """删除API密钥（物理删除）。"""
    _require_key_admin(current_user)
    _ensure_table(db)

    try:
        result = db.execute(
            text(f"DELETE FROM {API_KEYS_TABLE} WHERE id = :id RETURNING id"),
            {"id": key_id},
        )
        row = result.fetchone()
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="删除密钥失败") from exc

    if not row:
        raise HTTPException(status_code=404, detail="密钥不存在")

    return {"message": "密钥已删除", "id": key_id}


@router.post("/{key_id}/rotate")
def rotate_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """轮换API密钥：为指定名称生成新密钥，旧哈希失效（直接替换）。"""
    _require_key_admin(current_user)
    _ensure_table(db)

    existing = db.execute(
        text(f"SELECT id, name FROM {API_KEYS_TABLE} WHERE id = :id"),
        {"id": key_id},
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="密钥不存在")

    raw_key = _generate_raw_key()
    new_hash = _hash_key(raw_key)
    new_prefix = raw_key[:8]

    try:
        result = db.execute(
            text(
                f"""
                UPDATE {API_KEYS_TABLE}
                SET key_hash = :key_hash,
                    key_prefix = :key_prefix,
                    is_active = TRUE,
                    last_used_at = NULL
                WHERE id = :id
                RETURNING name, created_at
                """
            ),
            {"key_hash": new_hash, "key_prefix": new_prefix, "id": key_id},
        )
        row = result.fetchone()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"轮换密钥失败: {e}")

    return {
        "id": key_id,
        "name": row.name if row else existing.name,
        "key": raw_key,  # 新明文密钥，只返回这一次
        "key_prefix": new_prefix,
        "created_at": row.created_at.isoformat() if row and row.created_at else None,
        "is_active": True,
        "message": "密钥已轮换，旧密钥立即失效，请妥善保存新密钥",
    }


# ============ 工具函数：供其他端点复用 ============


def validate_api_key(raw_key: str, db: Session) -> bool:
    """
    验证 API 密钥是否有效。

    对输入的明文密钥做 sha256，匹配表中存在且 is_active=TRUE 的记录。
    验证成功时更新 last_used_at。返回 True 表示有效，False 表示无效/已停用。
    可被其他端点作为依赖或显式调用来鉴权。
    """
    if not raw_key:
        return False

    _ensure_table(db)
    key_hash = _hash_key(raw_key)

    row = db.execute(
        text(
            f"""
            SELECT id FROM {API_KEYS_TABLE}
            WHERE key_hash = :key_hash AND is_active = TRUE
            """
        ),
        {"key_hash": key_hash},
    ).fetchone()

    if not row:
        return False

    # 更新最近使用时间（失败不影响验证结果）
    try:
        db.execute(
            text(
                f"UPDATE {API_KEYS_TABLE} SET last_used_at = NOW() WHERE id = :id"
            ),
            {"id": row.id},
        )
        db.commit()
    except Exception:
        db.rollback()

    return True


# 兼容旧引用：保留一个 FastAPI 依赖形式的鉴权函数
async def verify_api_key(x_api_key: str, db: Session = Depends(get_db)) -> dict:
    """作为 FastAPI 依赖使用的 API Key 鉴权（通过 X-API-Key 头传入）。"""
    if not validate_api_key(x_api_key, db):
        raise HTTPException(status_code=401, detail="无效或已停用的API密钥")
    return {"authenticated": True}
