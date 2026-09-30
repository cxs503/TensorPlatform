"""
开放数字化底座 - 认证服务
用户认证、授权管理、令牌刷新
"""
import json
import os
import smtplib
import uuid
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

import bcrypt
import jwt
from sqlalchemy import text
from sqlalchemy.orm import Session
from services.base import BaseService

# JWT配置
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "open-foundation-secret-key-2024")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class AuthService(BaseService):
    """认证服务"""

    def __init__(self, db: Session = None):
        super().__init__(db)
        self.jwt_secret = JWT_SECRET_KEY

    def set_db(self, db: Session):
        """设置数据库会话"""
        self.db = db

    # ============== 密码处理 ==============

    @staticmethod
    def hash_password(password: str) -> str:
        """使用bcrypt哈希密码"""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"), hashed_password.encode("utf-8")
            )
        except Exception:
            return False

    # ============== 令牌处理 ==============

    def create_access_token(
        self, data: Dict[str, Any], expires_delta: timedelta = None
    ) -> str:
        """创建访问令牌"""
        to_encode = data.copy()
        expire = datetime.utcnow() + (
            expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        to_encode.update({"exp": expire, "type": "access", "jti": str(uuid.uuid4())})
        return jwt.encode(to_encode, self.jwt_secret, algorithm=JWT_ALGORITHM)

    def create_refresh_token(self, data: Dict[str, Any]) -> str:
        """创建刷新令牌"""
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire, "type": "refresh", "jti": str(uuid.uuid4())})
        return jwt.encode(to_encode, self.jwt_secret, algorithm=JWT_ALGORITHM)

    def create_token_pair(self, user_data: Dict[str, Any]) -> Dict[str, str]:
        """创建令牌对"""
        return {
            "access_token": self.create_access_token(user_data),
            "refresh_token": self.create_refresh_token(user_data),
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

    def decode_token(self, token: str) -> Optional[Dict[str, Any]]:
        """解码令牌"""
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    # ============== 用户认证 ==============

    def register_user(
        self, username: str, password: str, email: str = None, role: str = "viewer"
    ) -> Dict[str, Any]:
        """
        用户注册

        Args:
            username: 用户名
            password: 密码
            email: 邮箱
            role: 角色

        Returns:
            注册结果，包含用户信息和令牌
        """
        if not self.db:
            raise ValueError("数据库连接未设置")

        # 检查用户名是否存在
        existing = self.db.execute(
            text("SELECT id FROM users WHERE username = :username"),
            {"username": username},
        ).fetchone()

        if existing:
            raise ValueError("用户名已存在")

        # 检查邮箱是否存在
        if email:
            existing_email = self.db.execute(
                text("SELECT id FROM users WHERE email = :email"), {"email": email}
            ).fetchone()
            if existing_email:
                raise ValueError("邮箱已被注册")

        # 哈希密码
        hashed_password = self.hash_password(password)
        email = email or f"{username}@open-foundation.local"

        # 插入用户
        self.db.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, role)
            VALUES (:username, :email, :password, 1, :role)
        """
            ),
            {
                "username": username,
                "email": email,
                "password": hashed_password,
                "role": role,
            },
        )
        self.db.commit()

        # 获取新用户ID
        user_result = self.db.execute(
            text("SELECT id FROM users WHERE username = :username"),
            {"username": username},
        ).fetchone()

        user_id = user_result[0]

        # 生成令牌
        tokens = self.create_token_pair(
            {"sub": username, "user_id": user_id, "role": role}
        )

        return {
            "user_id": user_id,
            "username": username,
            "email": email,
            "role": role,
            **tokens,
        }

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """
        用户登录

        Args:
            username: 用户名
            password: 密码

        Returns:
            登录结果，包含用户信息和令牌
        """
        if not self.db:
            raise ValueError("数据库连接未设置")

        # 获取用户
        result = self.db.execute(
            text(
                "SELECT id, username, email, hashed_password, role, is_active FROM users WHERE username = :username"
            ),
            {"username": username},
        ).fetchone()

        if not result:
            raise ValueError("用户名或密码错误")

        user_id, username, email, hashed_password, role, is_active = result

        # 验证密码
        if not self.verify_password(password, hashed_password):
            raise ValueError("用户名或密码错误")

        # 检查用户状态
        if is_active != 1:
            raise ValueError("用户已被禁用")

        # 生成令牌
        tokens = self.create_token_pair(
            {"sub": username, "user_id": user_id, "role": role}
        )

        return {
            "user": {"id": user_id, "username": username, "email": email, "role": role},
            **tokens,
        }

    def refresh_token(self, refresh_token: str) -> Dict[str, str]:
        """
        刷新令牌

        Args:
            refresh_token: 刷新令牌

        Returns:
            新的令牌对
        """
        # 解码令牌
        payload = self.decode_token(refresh_token)

        if not payload:
            raise ValueError("无效的刷新令牌")

        if payload.get("type") != "refresh":
            raise ValueError("令牌类型错误")

        user_id = payload.get("user_id")
        username = payload.get("sub")
        role = payload.get("role")

        if not self.db:
            raise ValueError("数据库连接未设置")

        # 验证用户存在且活跃
        result = self.db.execute(
            text("SELECT id, is_active FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        ).fetchone()

        if not result or result[1] != 1:
            raise ValueError("用户不存在或已禁用")

        # 生成新令牌对
        return self.create_token_pair(
            {"sub": username, "user_id": user_id, "role": role}
        )

    def logout(self, user_id: int) -> bool:
        """
        用户登出

        Args:
            user_id: 用户ID

        Returns:
            是否成功
        """
        # TODO: 实现令牌黑名单机制
        return True

    def change_password(
        self, user_id: int, old_password: str, new_password: str
    ) -> bool:
        """
        修改密码

        Args:
            user_id: 用户ID
            old_password: 旧密码
            new_password: 新密码

        Returns:
            是否成功
        """
        if not self.db:
            raise ValueError("数据库连接未设置")

        # 获取当前密码哈希
        result = self.db.execute(
            text("SELECT hashed_password FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        ).fetchone()

        if not result:
            raise ValueError("用户不存在")

        # 验证旧密码
        if not self.verify_password(old_password, result[0]):
            raise ValueError("旧密码错误")

        # 更新密码
        new_hash = self.hash_password(new_password)
        self.db.execute(
            text("UPDATE users SET hashed_password = :password WHERE id = :user_id"),
            {"password": new_hash, "user_id": user_id},
        )
        self.db.commit()

        return True

    def get_current_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        获取当前用户信息

        Args:
            user_id: 用户ID

        Returns:
            用户信息
        """
        if not self.db:
            raise ValueError("数据库连接未设置")

        result = self.db.execute(
            text(
                "SELECT id, username, email, role, is_active, created_at FROM users WHERE id = :user_id"
            ),
            {"user_id": user_id},
        ).fetchone()

        if not result:
            return None

        return {
            "id": result[0],
            "username": result[1],
            "email": result[2],
            "role": result[3],
            "is_active": result[4],
            "created_at": result[5],
        }


# 全局实例
_auth_service: Optional[AuthService] = None


def get_auth_service(db: Session = None) -> AuthService:
    """获取认证服务实例"""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(db)
    elif db:
        _auth_service.set_db(db)
    return _auth_service


# Token存储
TOKEN_DIR = Path("tokens")
TOKEN_DIR.mkdir(parents=True, exist_ok=True)


class EmailService:
    """邮件服务"""

    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.from_email = os.getenv("FROM_EMAIL", "noreply@open-foundation.com")
        self.enabled = bool(self.smtp_host and self.smtp_user)

    def send_email(self, to_email: str, subject: str, body: str, html: str = None):
        """发送邮件"""
        if not self.enabled:
            print(f"Mock email to {to_email}: {subject}")
            return True

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_email
            msg["To"] = to_email
            msg["Subject"] = subject

            # 添加纯文本和HTML版本
            msg.attach(MIMEText(body, "plain"))
            if html:
                msg.attach(MIMEText(html, "html"))

            # 发送邮件
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()

            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            return False

    def send_verification_email(self, to_email: str, token: str):
        """发送验证邮件"""
        verification_url = f"http://localhost:3000/verify-email?token={token}"
        subject = "验证您的邮箱地址"
        body = f"""
        欢迎注册开放数字化底座！

        请点击以下链接验证您的邮箱地址：
        {verification_url}

        此链接有效期为24小时。

        如果您没有注册，请忽略此邮件。
        """

        html = f"""
        <html>
        <body>
            <h2>欢迎注册开放数字化底座！</h2>
            <p>请点击以下链接验证您的邮箱地址：</p>
            <p><a href="{verification_url}">验证邮箱</a></p>
            <p>或复制以下链接到浏览器：</p>
            <p>{verification_url}</p>
            <p>此链接有效期为24小时。</p>
            <p>如果您没有注册，请忽略此邮件。</p>
        </body>
        </html>
        """

        return self.send_email(to_email, subject, body, html)

    def send_password_reset_email(self, to_email: str, token: str):
        """发送密码重置邮件"""
        reset_url = f"http://localhost:3000/reset-password?token={token}"
        subject = "重置您的密码"
        body = f"""
        您正在请求重置密码。

        请点击以下链接重置密码：
        {reset_url}

        此链接有效期为1小时。

        如果您没有请求重置密码，请忽略此邮件。
        """

        html = f"""
        <html>
        <body>
            <h2>密码重置请求</h2>
            <p>请点击以下链接重置密码：</p>
            <p><a href="{reset_url}">重置密码</a></p>
            <p>或复制以下链接到浏览器：</p>
            <p>{reset_url}</p>
            <p>此链接有效期为1小时。</p>
            <p>如果您没有请求重置密码，请忽略此邮件。</p>
        </body>
        </html>
        """

        return self.send_email(to_email, subject, body, html)


class EnhancedAuthService:
    """增强认证服务"""

    def __init__(self):
        self.email_service = EmailService()
        self.jwt_secret = os.getenv("JWT_SECRET_KEY", "open-foundation-secret")
        self.verification_tokens: Dict[str, Dict] = {}
        self.password_reset_tokens: Dict[str, Dict] = {}

    def generate_verification_token(self, user_id: int, email: str) -> str:
        """生成邮箱验证token"""
        token = str(uuid.uuid4())
        self.verification_tokens[token] = {
            "user_id": user_id,
            "email": email,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat(),
        }
        return token

    def generate_password_reset_token(self, user_id: int, email: str) -> str:
        """生成密码重置token"""
        token = str(uuid.uuid4())
        self.password_reset_tokens[token] = {
            "user_id": user_id,
            "email": email,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
        }
        return token

    def verify_token(self, token: str, token_type: str) -> Optional[Dict]:
        """验证token"""
        if token_type == "verification":
            token_data = self.verification_tokens.get(token)
        elif token_type == "password_reset":
            token_data = self.password_reset_tokens.get(token)
        else:
            return None

        if not token_data:
            return None

        # 检查是否过期
        expires = datetime.fromisoformat(token_data["expires_at"])
        if datetime.now() > expires:
            return None

        return token_data

    def send_verification_email(self, user_id: int, email: str) -> bool:
        """发送验证邮件"""
        token = self.generate_verification_token(user_id, email)
        return self.email_service.send_verification_email(email, token)

    def send_password_reset_email(self, user_id: int, email: str) -> bool:
        """发送密码重置邮件"""
        token = self.generate_password_reset_token(user_id, email)
        return self.email_service.send_password_reset_email(email, token)

    def verify_email(self, token: str) -> bool:
        """验证邮箱"""
        token_data = self.verify_token(token, "verification")
        if token_data:
            # 删除token
            self.verification_tokens.pop(token, None)
            return True
        return False

    def verify_password_reset(self, token: str) -> Optional[Dict]:
        """验证密码重置token"""
        return self.verify_token(token, "password_reset")

    def generate_oauth_state(self) -> str:
        """生成OAuth状态码"""
        return str(uuid.uuid4())

    def get_oauth_url(self, provider: str, state: str) -> str:
        """获取OAuth URL"""
        client_id = os.getenv(f"{provider.upper()}_CLIENT_ID", "")
        redirect_uri = os.getenv(
            f"{provider.upper()}_REDIRECT_URI", "http://localhost:3000/oauth/callback"
        )

        if provider == "github":
            return f"https://github.com/login/oauth/authorize?client_id={client_id}&redirect_uri={redirect_uri}&state={state}&scope=read:user"
        elif provider == "google":
            return f"https://accounts.google.com/o/oauth2/v2/auth?client_id={client_id}&redirect_uri={redirect_uri}&state={state}&scope=openid email profile&response_type=code"
        else:
            return ""


# 全局实例
_enhanced_auth_service: Optional[EnhancedAuthService] = None


def get_enhanced_auth_service() -> EnhancedAuthService:
    """获取增强认证服务"""
    global _enhanced_auth_service
    if _enhanced_auth_service is None:
        _enhanced_auth_service = EnhancedAuthService()
    return _enhanced_auth_service
