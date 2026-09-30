"""
开放数字化底座 - JWT认证工具模块
提供安全的JWT token生成、验证、刷新功能
"""
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

# 加载环境变量
load_dotenv()

# JWT配置 - 从环境变量读取
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()


def _load_secret(name: str) -> str:
    configured = os.getenv(name)
    if configured:
        return configured
    if ENVIRONMENT == "production":
        raise RuntimeError(f"生产环境必须配置 {name}")

    default_dir = Path("/app/uploads/.secrets") if Path("/app/uploads").is_dir() else Path.home() / ".cache" / "open-foundation"
    secret_dir = Path(os.getenv("ODF_SECRET_DIR", str(default_dir)))
    secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_path = secret_dir / name.lower()
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()

    generated = secrets.token_urlsafe(64)
    try:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(generated)
        return generated
    except FileExistsError:
        return secret_path.read_text(encoding="utf-8").strip()


JWT_SECRET_KEY = _load_secret("JWT_SECRET_KEY")
REFRESH_SECRET_KEY = _load_secret("REFRESH_SECRET_KEY")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# 密码哈希配置 - 使用bcrypt
pwd_context = CryptContext(
    schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12  # bcrypt工作因子，越大越安全但越慢
)

# HTTPBearer认证
security = HTTPBearer()


# ============== 密码哈希函数 ==============


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证密码是否匹配哈希值
    支持bcrypt格式和旧版SHA256格式的向后兼容
    """
    # bcrypt格式 (以$2b$或$2a$开头)
    if hashed_password.startswith("$2"):
        return pwd_context.verify(plain_password, hashed_password)

    # 旧版SHA256格式 - 向后兼容
    import hashlib

    sha256_hash = hashlib.sha256(plain_password.encode()).hexdigest()
    if sha256_hash == hashed_password:
        return True

    return False


def get_password_hash(password: str) -> str:
    """
    生成密码的bcrypt哈希值
    """
    return pwd_context.hash(password)


def needs_rehash(hashed_password: str) -> bool:
    """
    检查密码哈希是否需要升级
    用于从SHA256迁移到bcrypt
    """
    # 不是bcrypt格式，需要重新哈希
    return not hashed_password.startswith("$2")


# ============== JWT Token函数 ==============


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """
    创建JWT访问令牌

    Args:
        data: 要编码的数据（通常包含sub, user_id, role等）
        expires_delta: 过期时间增量，默认30分钟

    Returns:
        JWT字符串
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "type": "access", "iat": datetime.utcnow()})

    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """
    创建JWT刷新令牌

    Args:
        data: 要编码的数据
        expires_delta: 过期时间增量，默认7天

    Returns:
        JWT字符串
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update({"exp": expire, "type": "refresh", "iat": datetime.utcnow()})

    encoded_jwt = jwt.encode(to_encode, REFRESH_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    解码并验证访问令牌

    Raises:
        HTTPException: token无效或过期
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的访问令牌类型",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="访问令牌已过期，请刷新或重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"无效的访问令牌: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def decode_refresh_token(token: str) -> Dict[str, Any]:
    """
    解码并验证刷新令牌

    Raises:
        HTTPException: token无效或过期
    """
    try:
        payload = jwt.decode(token, REFRESH_SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的刷新令牌类型",
            )

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="刷新令牌已过期，请重新登录",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"无效的刷新令牌: {str(e)}",
        )


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """
    FastAPI依赖函数：验证Bearer token

    Usage:
        @router.get("/protected")
        def protected_route(payload: dict = Depends(verify_token)):
            user_id = payload.get("user_id")
    """
    token = credentials.credentials
    return decode_access_token(token)


def create_token_pair(data: Dict[str, Any]) -> Dict[str, str]:
    """
    创建访问令牌和刷新令牌对

    Returns:
        {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}
    """
    access_token = create_access_token(data)
    refresh_token = create_refresh_token(data)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # 秒
    }


def refresh_tokens(refresh_token: str) -> Dict[str, str]:
    """
    使用刷新令牌获取新的令牌对

    Args:
        refresh_token: 有效的刷新令牌

    Returns:
        新的令牌对
    """
    payload = decode_refresh_token(refresh_token)

    # 创建新的令牌对，只保留必要信息
    token_data = {
        "sub": payload.get("sub"),
        "user_id": payload.get("user_id"),
        "role": payload.get("role"),
    }

    return create_token_pair(token_data)
