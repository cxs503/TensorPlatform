"""
开放数字化底座 - 认证API - 安全加固版本
"""
import logging
import re
import time
from typing import Optional

from database.config import get_db
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, validator
from sqlalchemy import text
from sqlalchemy.orm import Session
from utils.jwt_utils import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_token_pair,
    decode_refresh_token,
    get_password_hash,
    needs_rehash,
    verify_password,
)
from utils.security_middleware import ErrorResponse
from utils.dependencies import get_current_user, security, is_token_blacklisted, add_token_to_blacklist

router = APIRouter(tags=["认证"])
logger = logging.getLogger(__name__)


def sanitize_for_log(value):
    if not value or len(value) < 4:
        return "***"
    return value[:2] + "***" + value[-1]


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[EmailStr] = None
    role: Optional[str] = "VIEWER"

    @validator("username")
    def validate_username(cls, v):
        if len(v) < 3 or len(v) > 50:
            raise ValueError("用户名长度必须在3-50个字符之间")
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("用户名只能包含字母、数字和下划线")
        return v

    @validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("密码长度至少8位")
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v)
        if not (has_upper and has_lower and has_digit):
            raise ValueError("密码必须包含大写字母、小写字母和数字")
        if not has_special:
            raise ValueError("密码必须包含至少一个特殊字符")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

    @validator("new_password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("密码长度至少8位")
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v)
        if not (has_upper and has_lower and has_digit):
            raise ValueError("密码必须包含大写字母、小写字母和数字")
        if not has_special:
            raise ValueError("密码必须包含至少一个特殊字符")
        return v


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.execute(
        text("SELECT id FROM users WHERE username = :u"), {"u": req.username}
    ).fetchone()

    if existing:
        raise HTTPException(status_code=409, detail="用户名已存在")

    hashed_password = get_password_hash(req.password)
    email = req.email or req.username + "@open-foundation.local"

    db.execute(
        text(
            "INSERT INTO users (username, email, hashed_password, is_active, role) "
            "VALUES (:u, :e, :p, true, :r)"
        ),
        {"u": req.username, "e": email, "p": hashed_password, "r": req.role},
    )
    db.commit()

    user_result = db.execute(
        text("SELECT id FROM users WHERE username = :u"), {"u": req.username}
    ).fetchone()

    tokens = create_token_pair(
        {"sub": req.username, "user_id": user_result[0], "role": req.role}
    )

    logger.info("用户注册成功: " + sanitize_for_log(req.username))
    return {
        "username": req.username,
        "email": email,
        "role": req.role,
        "user_id": user_result[0],
        **tokens,
    }


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    result = db.execute(
        text(
            "SELECT id, username, email, hashed_password, role, is_active, features FROM users WHERE username = :u"
        ),
        {"u": req.username},
    ).fetchone()

    if not result:
        time.sleep(0.5)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user_id, username, email, hashed_password, role, is_active, features = result

    if not verify_password(req.password, hashed_password):
        logger.warning("登录失败: " + sanitize_for_log(req.username))
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if is_active != 1:
        raise HTTPException(status_code=403, detail="用户已被禁用")

    if needs_rehash(hashed_password):
        new_hash = get_password_hash(req.password)
        db.execute(
            text("UPDATE users SET hashed_password = :p WHERE id = :id"),
            {"p": new_hash, "id": user_id},
        )
        db.commit()

    tokens = create_token_pair({"sub": username, "user_id": user_id, "role": role})
    logger.info("用户登录成功: " + sanitize_for_log(username))

    return {
        **tokens,
        "user": {
            "id": user_id,
            "username": username,
            "email": email,
            "role": role,
            "features": features,
            "is_admin": str(role).split(".")[-1].lower() == "admin",
        },
    }


@router.get("/me")
def get_current_user_info(
    current_user=Depends(get_current_user), db: Session = Depends(get_db)
):
    result = db.execute(
        text("SELECT id, username, email, full_name, role, is_active, features FROM users WHERE id = :id"),
        {"id": current_user["user_id"]},
    ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="用户不存在")

    role = result[4]
    role_code = str(role).split(".")[-1].lower()
    permissions = [
        row[0] for row in db.execute(
            text(
                "SELECT permission_code FROM rbac_role_permissions "
                "WHERE role_code = :role ORDER BY permission_code"
            ),
            {"role": role_code},
        ).fetchall()
    ]
    return {
        "id": result[0],
        "username": result[1],
        "email": result[2],
        "full_name": result[3],
        "role": role,
        "is_active": result[5],
        "features": result[6],
        "permissions": permissions,
        "is_admin": str(role).split(".")[-1].lower() == "admin",
    }


@router.post("/refresh")
def refresh_access_token(req: TokenRefreshRequest, db: Session = Depends(get_db)):
    payload = decode_refresh_token(req.refresh_token)
    user_id = payload.get("user_id")
    username = payload.get("sub")
    role = payload.get("role")

    result = db.execute(
        text("SELECT id, is_active FROM users WHERE id = :id"), {"id": user_id}
    ).fetchone()
    if not result or result[1] != 1:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    return create_token_pair({"sub": username, "user_id": user_id, "role": role})


@router.post("/logout")
def logout(current_user=Depends(get_current_user)):
    token = current_user.get("token")
    if token:
        add_token_to_blacklist(token, ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        username = current_user.get("username", "unknown")
        logger.info("用户登出: " + sanitize_for_log(username))
    return {"message": "登出成功"}


@router.post("/change-password")
def change_password(
    req: PasswordChangeRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = current_user["user_id"]

    result = db.execute(
        text("SELECT hashed_password FROM users WHERE id = :id"), {"id": user_id}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="用户不存在")

    if not verify_password(req.old_password, result[0]):
        raise HTTPException(status_code=400, detail="旧密码错误")

    new_hash = get_password_hash(req.new_password)
    db.execute(
        text("UPDATE users SET hashed_password = :p WHERE id = :id"),
        {"p": new_hash, "id": user_id},
    )
    db.commit()

    token = current_user.get("token")
    if token:
        add_token_to_blacklist(token, ACCESS_TOKEN_EXPIRE_MINUTES * 60)

    username = current_user.get("username", "unknown")
    logger.info("密码修改成功: " + sanitize_for_log(username))
    return {"message": "密码修改成功，请重新登录"}


@router.get("/verify")
def verify_token(current_user=Depends(get_current_user)):
    user_info = {k: v for k, v in current_user.items() if k != "token"}
    return {"valid": True, "user": user_info}


# ============================================================
# 增强认证路由 (merged from enhanced_auth.py)
# ============================================================
from typing import Optional as OptionalType

from pydantic import BaseModel as PydanticBaseModel
from services.auth_service import get_enhanced_auth_service

enhanced_router = APIRouter(prefix="/auth/enhanced", tags=["增强认证"])


class SendVerificationReq(PydanticBaseModel):
    email: str


class VerifyEmailReq(PydanticBaseModel):
    token: str


class ResetPasswordReq(PydanticBaseModel):
    token: str
    new_password: str


class ChangePasswordReq(PydanticBaseModel):
    old_password: str
    new_password: str


@enhanced_router.post("/send-verification")
def enhanced_send_verification_email(req: SendVerificationReq, db=Depends(get_db)):
    """发送验证邮件"""
    # 查找用户
    result = db.execute(
        text("SELECT id FROM users WHERE email = :email"), {"email": req.email}
    ).fetchone()

    if not result:
        # 用户不存在，返回成功以防止邮箱枚举
        return {"message": "如果邮箱存在，将收到验证邮件"}

    service = get_enhanced_auth_service()
    success = service.send_verification_email(result[0], req.email)

    if success:
        return {"message": "验证邮件已发送"}
    return {"message": "邮件发送失败，请稍后重试"}


@enhanced_router.post("/verify-email")
def enhanced_verify_email(req: VerifyEmailReq):
    """验证邮箱"""
    service = get_enhanced_auth_service()
    success = service.verify_email(req.token)

    if success:
        return {"message": "邮箱验证成功"}
    raise HTTPException(status_code=400, detail="无效或已过期的验证链接")


@enhanced_router.post("/forgot-password")
def enhanced_forgot_password(req: SendVerificationReq, db=Depends(get_db)):
    """忘记密码"""
    result = db.execute(
        text("SELECT id, email FROM users WHERE email = :email"), {"email": req.email}
    ).fetchone()

    if not result:
        return {"message": "如果邮箱存在，将收到重置链接"}

    service = get_enhanced_auth_service()
    service.send_password_reset_email(result[0], result[1])

    return {"message": "密码重置邮件已发送"}


@enhanced_router.post("/reset-password")
def enhanced_reset_password(req: ResetPasswordReq, db=Depends(get_db)):
    """重置密码"""
    service = get_enhanced_auth_service()
    token_data = service.verify_password_reset(req.token)

    if not token_data:
        raise HTTPException(status_code=400, detail="无效或已过期的重置链接")

    # 验证密码强度
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少6位")

    # 更新密码
    import bcrypt
    hashed = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    db.execute(
        text("UPDATE users SET hashed_password = :p WHERE id = :id"),
        {"p": hashed, "id": token_data["user_id"]},
    )
    db.commit()

    return {"message": "密码重置成功"}


@enhanced_router.post("/change-password")
def enhanced_change_password(
    req: ChangePasswordReq,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """修改密码"""
    user_id = current_user.get("user_id")

    # 获取当前密码
    result = db.execute(
        text("SELECT hashed_password FROM users WHERE id = :id"), {"id": user_id}
    ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 验证旧密码
    import bcrypt
    if not bcrypt.checkpw(req.old_password.encode(), result[0].encode()):
        raise HTTPException(status_code=400, detail="原密码错误")

    # 验证新密码强度
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度至少6位")

    # 更新密码
    hashed = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    db.execute(
        text("UPDATE users SET hashed_password = :p WHERE id = :id"),
        {"p": hashed, "id": user_id},
    )
    db.commit()

    return {"message": "密码修改成功"}


@enhanced_router.get("/oauth-url")
def enhanced_get_oauth_url(provider: str):
    """获取OAuth URL"""
    service = get_enhanced_auth_service()
    state = service.generate_oauth_state()
    url = service.get_oauth_url(provider, state)

    if not url:
        raise HTTPException(status_code=400, detail="不支持的OAuth provider")

    return {"url": url, "state": state}


@enhanced_router.get("/status")
def enhanced_get_auth_status():
    """获取认证服务状态"""
    service = get_enhanced_auth_service()
    return {
        "email_verification_enabled": service.email_service.enabled,
        "oauth_providers": ["github", "google"],
    }
