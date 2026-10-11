"""
CORS配置
生产环境应严格限制允许的来源
"""
import os
from typing import List

# 从环境变量获取允许的来源
CORS_ORIGINS_STR = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8001,http://localhost:5173"
)
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


def get_cors_origins() -> List[str]:
    """
    获取CORS允许的来源列表

    生产环境：仅使用环境变量配置的来源
    开发环境：允许常用本地开发端口
    """
    # 解析环境变量中的来源
    origins = [
        origin.strip() for origin in CORS_ORIGINS_STR.split(",") if origin.strip()
    ]

    # 开发环境添加本地端口
    if ENVIRONMENT == "development":
        dev_origins = [
            "http://localhost:3000",
            "http://localhost:8001",
            "http://localhost:8000",
            "http://localhost:5173",  # Vite
            "http://localhost:4200",  # Angular
            "http://localhost:8080",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8001",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:4200",
            "http://127.0.0.1:8080",
        ]
        origins.extend(dev_origins)

    # HTTPS 支持 - 添加 HTTPS 版本的来源
    https_origins = []
    for origin in origins:
        if origin.startswith("http://"):
            https_origins.append(origin.replace("http://", "https://"))
    origins.extend(https_origins)

    # 添加不带端口的 HTTPS 来源（通过 Nginx 代理）
    origins.extend(
        [
            "https://localhost",
            "https://127.0.0.1",
        ]
    )

    # 去重
    return list(set(origins))


# CORS配置
CORS_CONFIG = {
    "allow_origins": get_cors_origins(),
    "allow_credentials": True,
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    "allow_headers": [
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Request-ID",
        "X-User-ID",
        "X-Forwarded-For",
    ],
    "expose_headers": [
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-Process-Time",
    ],
    "max_age": 3600,  # 预检请求缓存1小时
}


def get_cors_config() -> dict:
    """
    获取CORS配置
    用于动态更新配置或调试
    """
    return {
        "environment": ENVIRONMENT,
        "origins_count": len(CORS_CONFIG["allow_origins"]),
        "origins": CORS_CONFIG["allow_origins"],
        "methods": CORS_CONFIG["allow_methods"],
    }
