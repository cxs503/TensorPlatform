"""
开放数字化底座 - JWT认证守卫中间件

对所有受保护的API路由强制执行JWT认证。
"""
import logging
import re
from typing import Set

from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import JSONResponse
from services.llm_trace_context import reset_llm_request_context, set_llm_request_context
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from sqlalchemy import text
from utils.jwt_utils import JWT_SECRET_KEY

load_dotenv()

# 审计日志数据库会话 —— 延迟导入以避免启动期循环依赖
try:
    from database.config import SessionLocal as _AuditSessionLocal
except Exception:  # pragma: no cover - 容错:数据库未配置时审计降级
    _AuditSessionLocal = None

logger = logging.getLogger(__name__)

# --------------- 配置 ---------------

JWT_ALGORITHM = "HS256"

# 跳过认证的路径（精确前缀匹配）
SKIP_AUTH_PATHS: Set[str] = {
    "/api/v1/health",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/login",
    "/api/v1/register",
    "/api/v1/real-training/health",
    # Alertmanager uses a dedicated, constant-time checked bearer token in the route.
    "/api/v1/internal/alertmanager/webhook",
    "/api/v1/metrics",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/health",
    "/",
}

# 跳过认证的路径前缀（以这些字符串开头即跳过）
#
# 原则:业务数据默认需要认证；这里只允许自身包含独立认证逻辑的前缀。
SKIP_AUTH_PREFIXES: Set[str] = {
    # ── 认证/公开端点 ──
    "/api/v1/auth/enhanced",  # 增强认证模块有自己的验证逻辑
    "/api/v1/auth/login",  # 登录(也在 SKIP_AUTH_PATHS 中)
    "/api/v1/auth/register",
    # 公共实况展示面（owner 2026-10-22）：只读端点，GET/HEAD 放行，写仍需认证
    "/api/v1/public",
}


def _is_exact_skip(path: str) -> bool:
    """精确匹配白名单(SKIP_AUTH_PATHS)——不分 method 放行。
    这些是登录/注册/健康检查等本身不能要求认证的端点。
    """
    return path in SKIP_AUTH_PATHS


def _is_prefix_skip(path: str) -> bool:
    """前缀匹配白名单(SKIP_AUTH_PREFIXES)——仅命中路径前缀。"""
    for prefix in SKIP_AUTH_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


# 安全只读方法——前缀白名单对这些 method 放行;写方法仍需认证。
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# 报告配图免登录只读（owner 2026-10-31 选 A）：<img> 无法携带 Bearer。
# 仅 GET/HEAD 放行单文件；路由层另有文件名白名单正则 + 路径围栏兜底。
_FIGURES_PATH_RE = re.compile(r"^/api/v1/tensor/reports/\d+/figures/[A-Za-z0-9_.-]+$")


def _should_skip_auth(path: str, method: str) -> bool:
    """判断该请求是否需要跳过认证。

    - SKIP_AUTH_PATHS(精确匹配,如 /api/v1/auth/login):不分 method 放行。
    - SKIP_AUTH_PREFIXES(前缀匹配,如 /api/v1/datasets):仅对 GET/HEAD/OPTIONS
      (读)放行;POST/PUT/PATCH/DELETE(写)仍需认证,防止越权写。
    """
    # 1. 精确匹配白名单 — 不分 method 放行
    if _is_exact_skip(path):
        return True

    # 2. 前缀白名单 — 仅读方法放行,写方法需认证
    if _is_prefix_skip(path) and method.upper() in _SAFE_METHODS:
        return True

    # 报告配图 — 仅 GET/HEAD 放行（见 _FIGURES_PATH_RE 注释）
    if method.upper() in _SAFE_METHODS and _FIGURES_PATH_RE.match(path):
        return True

    return False


def _extract_token(request: Request) -> str | None:
    """从 Authorization header 提取 Bearer token"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def _decode_token(token: str) -> dict | None:
    """解码并验证 JWT token，返回 payload 或 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            logger.warning("Token type is not 'access'")
            return None
        return payload
    except ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except JWTError as e:
        logger.warning(f"Invalid token: {e}")
        return None


def _record_audit(user_id, username: str, method: str, path: str) -> None:
    """记录一条业务操作审计日志(POST/PUT/PATCH/DELETE)。

    永不影响主请求:任何异常都静默吞掉。
    写入 audit_logs(action, entity_type, entity_id, detail, user_name)。
    """
    if _AuditSessionLocal is None:
        return
    db = None
    try:
        db = _AuditSessionLocal()
        db.execute(
            text(
                "INSERT INTO audit_logs (action, entity_type, entity_id, detail, user_name) "
                "VALUES (:action, :etype, :eid, :detail, :user)"
            ),
            {
                "action": method,            # POST/PUT/PATCH/DELETE
                "etype": "api",              # 业务操作类型
                "eid": str(user_id) if user_id is not None else None,
                "detail": f"{method} {path}",
                "user": username or "unknown",
            },
        )
        db.commit()
    except Exception:  # 审计不能影响主请求
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


async def auth_guard_middleware(request: Request, call_next):
    """
    JWT认证守卫中间件

    行为:
    - 跳过白名单路径和 OPTIONS 预检请求
    - 从 Authorization header 提取 Bearer token
    - 验证 token 有效性
    - 将 payload 注入 request.state.current_user
    - 未认证返回 401 JSON 错误
    """
    # ===== JWT 认证守卫 =====
    path = request.url.path

    # OPTIONS 预检请求 — 始终放行
    if request.method == "OPTIONS":
        return await call_next(request)

    # 白名单路径 — 直接放行
    # (精确匹配不分 method;前缀匹配仅对读方法 GET/HEAD/OPTIONS 放行,
    #  写方法 POST/PUT/PATCH/DELETE 继续走认证,防止越权写)
    if _should_skip_auth(path, request.method):
        return await call_next(request)

    # 提取 token
    token = _extract_token(request)

    if not token:
        logger.warning(f"未授权访问: {request.method} {path} — 缺少Authorization header")
        return JSONResponse(
            status_code=401,
            content={
                "detail": "未提供认证令牌，请先登录",
                "error_code": "UNAUTHORIZED",
                "status_code": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 验证 token
    payload = _decode_token(token)

    if not payload:
        logger.warning(f"认证失败: {request.method} {path} — token无效或已过期")
        return JSONResponse(
            status_code=401,
            content={
                "detail": "认证令牌无效或已过期，请重新登录",
                "error_code": "UNAUTHORIZED",
                "status_code": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 注入 current_user 到 request.state
    request.state.current_user = {
        "user_id": payload.get("user_id"),
        "username": payload.get("sub"),
        "role": payload.get("role"),
        "token": token,
    }

    # ===== 业务操作审计:对写操作(POST/PUT/PATCH/DELETE)记录一条审计日志 =====
    # 在认证成功后记录,审计失败绝不影响主请求。
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        _record_audit(
            user_id=payload.get("user_id"),
            username=payload.get("sub"),
            method=request.method,
            path=path,
        )

    context_token = set_llm_request_context({
        "user_id": payload.get("user_id"),
        "username": payload.get("sub"),
        "path": path,
    })
    try:
        return await call_next(request)
    finally:
        reset_llm_request_context(context_token)
