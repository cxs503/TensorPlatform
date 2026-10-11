"""
开放数字化底座 - 安全中间件
提供请求验证、安全头、错误处理等功能
"""
import logging
import os
import time
import uuid
from typing import Callable

from dotenv import load_dotenv
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv()

logger = logging.getLogger(__name__)

# ============== Rate Limiting ==============


def get_rate_limit_key(request: Request) -> str:
    """
    生成限流键
    优先使用用户ID，其次使用IP地址
    """
    # 尝试从Authorization header获取用户信息
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # 使用token的一部分作为键（避免每次解码）
        token_part = auth_header[7:27]  # 取token前20个字符
        return f"user:{token_part}"

    # 使用IP地址
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()

    return get_remote_address(request)


# 创建限流器实例
limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=[os.getenv("RATE_LIMIT_DEFAULT", "100/minute")],
)


async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
    """限流异常处理"""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": "请求过于频繁，请稍后再试",
            "error_code": "RATE_LIMIT_EXCEEDED",
            "retry_after": exc.detail,
        },
    )


# ============== 安全头中间件 ==============


async def add_security_headers(request: Request, call_next: Callable):
    """
    添加安全响应头
    """
    response = await call_next(request)

    # 防止点击劫持
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # 内容安全策略
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self'; "
        "frame-ancestors 'none'"
    )

    # HSTS (仅在生产环境启用HTTPS时)
    if os.getenv("ENVIRONMENT") == "production":
        response.headers[
            "Strict-Transport-Security"
        ] = "max-age=31536000; includeSubDomains"

    return response


# ============== 请求ID中间件 ==============


async def add_request_id(request: Request, call_next: Callable):
    """
    为每个请求添加唯一ID，便于日志追踪
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    return response


# ============== 请求日志中间件 ==============


async def log_requests(request: Request, call_next: Callable):
    """
    记录请求日志
    """
    start_time = time.time()

    # 处理请求
    response = await call_next(request)

    # 计算处理时间
    process_time = time.time() - start_time

    # 记录日志
    log_data = {
        "method": request.method,
        "path": request.url.path,
        "query": str(request.query_params),
        "status": response.status_code,
        "duration_ms": round(process_time * 1000, 2),
        "request_id": getattr(request.state, "request_id", "unknown"),
        "client_ip": get_rate_limit_key(request),
    }

    # 根据状态码选择日志级别
    if response.status_code >= 500:
        logger.error(f"Request failed: {log_data}")
    elif response.status_code >= 400:
        logger.warning(f"Request error: {log_data}")
    else:
        logger.info(f"Request: {log_data}")

    response.headers["X-Process-Time"] = f"{process_time:.3f}s"

    return response


# ============== 统一错误响应 ==============


class ErrorResponse:
    """统一错误响应格式"""

    @staticmethod
    def make_response(
        status_code: int,
        detail: str,
        error_code: str = None,
        request_id: str = None,
        **extra,
    ):
        """
        创建统一格式的错误响应

        Args:
            status_code: HTTP状态码
            detail: 错误详情
            error_code: 应用内部错误码
            request_id: 请求ID
            **extra: 其他附加信息
        """
        content = {"detail": detail, "status_code": status_code}

        if error_code:
            content["error_code"] = error_code
        if request_id:
            content["request_id"] = request_id

        content.update(extra)

        return JSONResponse(status_code=status_code, content=content)

    @staticmethod
    def bad_request(detail: str, **extra):
        return ErrorResponse.make_response(
            status.HTTP_400_BAD_REQUEST, detail, "BAD_REQUEST", **extra
        )

    @staticmethod
    def unauthorized(detail: str = "未授权访问", **extra):
        return ErrorResponse.make_response(
            status.HTTP_401_UNAUTHORIZED, detail, "UNAUTHORIZED", **extra
        )

    @staticmethod
    def forbidden(detail: str = "权限不足", **extra):
        return ErrorResponse.make_response(
            status.HTTP_403_FORBIDDEN, detail, "FORBIDDEN", **extra
        )

    @staticmethod
    def not_found(detail: str = "资源不存在", **extra):
        return ErrorResponse.make_response(
            status.HTTP_404_NOT_FOUND, detail, "NOT_FOUND", **extra
        )

    @staticmethod
    def conflict(detail: str, **extra):
        return ErrorResponse.make_response(
            status.HTTP_409_CONFLICT, detail, "CONFLICT", **extra
        )

    @staticmethod
    def validation_error(detail: str, errors: list = None, **extra):
        response_extra = extra.copy()
        if errors:
            response_extra["errors"] = errors
        return ErrorResponse.make_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail,
            "VALIDATION_ERROR",
            **response_extra,
        )

    @staticmethod
    def internal_error(detail: str = "服务器内部错误", **extra):
        return ErrorResponse.make_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail, "INTERNAL_ERROR", **extra
        )


# ============== JWT Token验证 ==============

def validate_token_from_header(token: str) -> dict | None:
    """
    验证JWT token并返回payload
    
    Args:
        token: Bearer token字符串
    
    Returns:
        payload字典 或 None（验证失败）
    """
    import os
    import secrets
    from dotenv import load_dotenv
    from jose import JWTError, jwt
    from jose.exceptions import ExpiredSignatureError
    
    load_dotenv()
    
    jwt_secret = os.getenv("JWT_SECRET_KEY")
    if not jwt_secret:
        jwt_secret = secrets.token_urlsafe(64)
        logging.getLogger(__name__).warning("JWT_SECRET_KEY 未配置，使用临时密钥")
    
    try:
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        return payload
    except (ExpiredSignatureError, JWTError):
        return None


# ============== 请求验证装饰器 ==============


def validate_request_content_type(allowed_types: list = ["application/json"]):
    """
    验证请求Content-Type的装饰器
    """

    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            content_type = request.headers.get("content-type", "")
            if not any(allowed in content_type for allowed in allowed_types):
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"不支持的媒体类型，期望: {allowed_types}",
                )
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator
