"""
Shared FastAPI dependencies.

Provides get_current_user and related auth utilities that are needed
across multiple API modules, avoiding tight coupling to api.auth.
"""
import time

from database.config import get_db
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session
from utils.jwt_utils import decode_access_token

security = HTTPBearer()

_token_blacklist = {}


def add_token_to_blacklist(token, expires_in):
    _token_blacklist[token[:32]] = time.time() + expires_in


def is_token_blacklisted(token):
    key = token[:32]
    if key in _token_blacklist:
        if time.time() < _token_blacklist[key]:
            return True
        del _token_blacklist[key]
    return False


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    token = credentials.credentials

    if is_token_blacklisted(token):
        raise HTTPException(status_code=401, detail="Token已失效，请重新登录")

    payload = decode_access_token(token)
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="令牌中缺少用户信息")

    result = db.execute(
        text("SELECT id, username, email, role, is_active, organization_id, features FROM users WHERE id = :id"),
        {"id": user_id},
    ).fetchone()

    if not result:
        raise HTTPException(status_code=401, detail="用户不存在")
    if result[4] != 1:
        raise HTTPException(status_code=403, detail="用户已被禁用")

    return {
        "user_id": result[0],
        "username": result[1],
        "email": result[2],
        "role": result[3],
        "organization_id": result[5],
        "features": result[6],
        "token": token,
    }
