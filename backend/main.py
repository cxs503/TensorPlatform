from __future__ import annotations

import asyncio
import logging
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", "https://hf-mirror.com")

# MLflow 3.13+ 文件存储模式开关 — 必须在任何 mlflow import 之前设置
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.orm import Session

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from cors_config import CORS_CONFIG

# 导入数据库配置
from database.config import get_db
from sqlalchemy.orm import Session

# 导入安全模块
from utils.security_middleware import (
    ErrorResponse,
    add_request_id,
    add_security_headers,
    limiter,
    log_requests,
    rate_limit_exception_handler,
    validate_token_from_header,
)

# 导入认证守卫中间件
from middleware.auth_guard import auth_guard_middleware

# ============================================================
# 导入 API 路由 — 全部通过领域聚合层导入（12 个域模块）
# ============================================================

# ── 领域聚合层（10 个域，覆盖共性平台 API 模块）──
# 2026-10-16 架构收敛：移除能碳/新能源（energy）与 AI 行业应用（ai_app）两个
# 行业应用域及钢铁虚拟数据等模块，平台回归 domain-neutral 共性底座，
# 行业方案按 README 约定作为独立 adapter/plugin 交付。
from api.domain import (
    data, model, deployment,          # 数据 / 模型 / 部署
    iam, observability, platform,     # 认证 / 可观测 / 平台
    iot_edge,                         # IoT 与边缘
    ops, tools, misc,                 # 项目运维 / 工具 / 杂项
    tensor,                           # 科研运行台（场景/执行/审批/会话/健康）
)

# Prometheus 监控指标 (router 已在 observability 域中聚合；保留为独立导入用於 /metrics 空前缀端点)
from api import metrics_api
from api.dashboard_proxy import router as dashboard_router

# ============== 应用生命周期 ==============


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    startup_time = time.time()

    # 启动时执行
    logger.info("Application starting...")

    # 检查数据库连接
    try:
        with get_db().__next__() as db:
            db.execute(text("SELECT 1"))
            logger.info("Database connection established")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")

    # 初始化数据库表
    try:
        from database.config import engine, Base
        from models import core as _core_models
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified")
        from migrations.run_migration import run_pending_migrations
        applied_migrations = run_pending_migrations()
        if applied_migrations:
            logger.info("Database migrations applied: %s", applied_migrations)
        # 播种标准化组件数据
        try:
            from migrations.seed_components import seed
            seed()
        except Exception as e:
            logger.warning(f"组件种子数据初始化跳过: {e}")

    except Exception as e:
        logger.exception("Database schema verification/migration failed: %s", e)
        if os.getenv("ENVIRONMENT", "development").lower() == "production":
            raise

    # 初始化MLflow追踪URI(2026-07-15 切到 PostgreSQL 后端,启用 Model Registry)
    try:
        import mlflow
        import config
        _database_uri = os.environ.get("DATABASE_URL", "postgresql://foundation:foundation123@localhost:5432/open_digital_foundation")
        _default_mlflow_uri = (
            _database_uri.replace("postgresql://", "postgresql+psycopg2://", 1).rsplit("/", 1)[0]
            + "/mlflow"
        )
        _mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", _default_mlflow_uri)
        # artifact 根目录(模型文件等)仍存本地 FS,与 backend store 分离
        _artifact_root = str(config.BACKEND_DIR / "uploads" / "mlartifacts")
        mlflow.set_tracking_uri(_mlflow_uri)
        mlflow.set_experiment("foundation_training")
        logger.info(f"MLflow tracking URI set to {_mlflow_uri}, artifacts -> {_artifact_root}")
    except Exception as e:
        logger.warning(f"MLflow initialization skipped: {e}")

    # Jupyter默认关闭；显式启用时必须提供访问令牌。
    if os.getenv("ENABLE_JUPYTER", "false").lower() == "true":
        jupyter_token = os.getenv("JUPYTER_TOKEN")
        if not jupyter_token:
            logger.warning("JupyterLab未启动: ENABLE_JUPYTER=true 时必须配置 JUPYTER_TOKEN")
        else:
            try:
                import subprocess

                env = os.environ.copy()
                subprocess.Popen(
                    [
                        "/opt/venv/bin/jupyter",
                        "lab",
                        "--ip=0.0.0.0",
                        "--port=8888",
                        "--no-browser",
                        "--allow-root",
                        "--notebook-dir=/app/uploads",
                        "--ServerApp.base_url=/jupyter",
                        f"--IdentityProvider.token={jupyter_token}",
                    ],
                    stdout=open("/tmp/jupyter.log", "w"),
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )
                logger.info("JupyterLab started on port 8888")
            except Exception as e:
                logger.warning(f"JupyterLab startup failed: {e}")

    scheduler_task = None
    realtime_monitor_task = None
    if os.getenv("SCHEDULER_ENABLED", "true").lower() == "true":
        try:
            from services.scheduler_service import get_scheduler_service

            scheduler = get_scheduler_service()
            poll_seconds = max(5, int(os.getenv("SCHEDULER_POLL_SECONDS", "15")))

            async def _scheduler_loop() -> None:
                while True:
                    try:
                        triggered = scheduler.dispatch_due_schedules()
                        if triggered:
                            logger.info("Triggered %s due scheduled task(s)", triggered)
                    except Exception:
                        logger.exception("Scheduled-task dispatch failed")
                    await asyncio.sleep(poll_seconds)

            scheduler_task = asyncio.create_task(_scheduler_loop(), name="foundation-scheduler")
            logger.info("Persistent scheduler started (poll interval: %ss)", poll_seconds)
        except Exception as e:
            logger.exception("Persistent scheduler initialization failed: %s", e)

    # Real sensor monitoring polls configured production data sources, rather
    # than the demonstration collection buffer.
    if os.getenv("REALTIME_SENSOR_MONITOR_ENABLED", "true").lower() == "true":
        try:
            from services.realtime_sensor_monitor_service import get_realtime_sensor_monitor_service
            realtime_monitor = get_realtime_sensor_monitor_service()
            realtime_monitor.ensure_schema()

            async def _realtime_monitor_loop() -> None:
                while True:
                    try:
                        checked = realtime_monitor.run_due()
                        if checked:
                            logger.info("Checked %s realtime sensor monitor(s)", checked)
                    except Exception:
                        logger.exception("Realtime sensor monitoring dispatch failed")
                    await asyncio.sleep(5)

            realtime_monitor_task = asyncio.create_task(_realtime_monitor_loop(), name="foundation-realtime-sensor-monitor")
            logger.info("Realtime sensor monitor started")
        except Exception as e:
            logger.exception("Realtime sensor monitor initialization failed: %s", e)

    yield

    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
    if realtime_monitor_task:
        realtime_monitor_task.cancel()
        try:
            await realtime_monitor_task
        except asyncio.CancelledError:
            pass

    # shutdown 时执行
    logger.info(f"Application shutdown. Uptime: {time.time() - startup_time:.2f}s")


app: FastAPI = FastAPI(
    title="开放底座AI算法技术开发平台 API",
    description="面向新能源和工业互联网的全栈式AI研发平台",
    version="2.2.0",
    lifespan=lifespan,
    docs_url=None,  # 禁用 Swagger UI
    redoc_url="/docs",  # 使用 ReDoc
)

# ============================================================
# 注册 API 路由 — 全部通过 12 个领域聚合路由器
# ============================================================

# ── 能碳与新能源 ──
# ── 认证与权限 (IAM) ──
app.include_router(iam.router, prefix="/api/v1")

# ── 项目与运维 ──
app.include_router(ops.router, prefix="/api/v1")

# ── 数据 / 模型 / 部署 ──
app.include_router(data.router, prefix="/api/v1")
app.include_router(model.router, prefix="/api/v1")
app.include_router(deployment.router, prefix="/api/v1")

# ── AI 行业应用 ──
# ── IoT 与边缘 ──
app.include_router(iot_edge.router, prefix="/api/v1")

# ── 可观测性 ──
app.include_router(observability.router, prefix="/api/v1")

# ── 平台管理 ──
app.include_router(platform.router, prefix="/api/v1")

# ── 工具与基础设施 ──
app.include_router(tools.router, prefix="/api/v1")

# ── 其他功能（Jupyter, 数据管道, WebSocket, A/B测试, MLOps, Notebook, ONNX 等）──
app.include_router(misc.router, prefix="/api/v1")

# ── 科研运行台（HPC + AI agent 操作面）──
app.include_router(tensor.router, prefix="/api/v1")

# ── Prometheus /metrics 端点（空前缀，供 Prometheus 采集）──
app.include_router(metrics_api.router, prefix="")
app.include_router(dashboard_router)

# ============== 中间件 ==============

app.add_middleware(SlowAPIMiddleware)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_CONFIG["allow_origins"],
    allow_credentials=CORS_CONFIG["allow_credentials"],
    allow_methods=CORS_CONFIG["allow_methods"],
    allow_headers=CORS_CONFIG["allow_headers"],
)


# 安全头
@app.middleware("http")
async def add_security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers[
        "Content-Security-Policy"
    ] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; frame-ancestors 'none'"
    return response


# 请求 ID
@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# 访问日志
@app.middleware("http")
async def add_request_logging(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time

    log_data = {
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_ms": round(process_time * 1000, 2),
    }
    if request.url.path == "/api/v1/algorithm-store/models/upload":
        log_data["content_type"] = request.headers.get("content-type", "")
        log_data["content_length"] = request.headers.get("content-length", "")

    if response.status_code >= 500:
        logger.error(f"Request failed: {log_data}")
    elif response.status_code >= 400:
        logger.warning(f"Request error: {log_data}")
    else:
        logger.info(f"Request: {log_data}")

    response.headers["X-Process-Time"] = f"{process_time:.3f}s"
    return response


# Prometheus HTTP 请求计数
@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    """对每个 HTTP 请求按 path/method/status 指标计数"""
    from api.metrics_api import http_request_duration_seconds, http_requests_total

    started_at = time.monotonic()
    def record_request(status_code: str) -> None:
        # 路由匹配完成后优先使用参数化路由模式，避免资源 ID 造成高基数。
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        metric_labels = {
            "path": path,
            "method": request.method,
            "status": status_code,
        }
        http_requests_total.labels(**metric_labels).inc()
        http_request_duration_seconds.labels(**metric_labels).observe(
            time.monotonic() - started_at
        )

    try:
        response = await call_next(request)
    except Exception:
        # 未处理异常由外层错误处理器返回 500；在重新抛出前也必须计入监控。
        record_request("500")
        raise
    record_request(str(response.status_code))
    return response


# JWT 认证守卫 — 最外层中间件，在所有其他中间件之前检查认证
@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    return await auth_guard_middleware(request, call_next)


# ============== 健康检查 ==============


@app.get("/health", include_in_schema=False)
@app.get("/api/v1/health")
async def health_check(db: Session = Depends(get_db)):
    """健康检查端点"""
    try:
        db.execute(text("SELECT 1"))
        db_status = "up"
    except Exception:
        db_status = "down"
    return {
        "status": "healthy",
        "version": "2.2.0",
        "services": {
            "api": "up",
            "database": db_status
        }
    }


# ============== 主页 ==============


@app.get("/")
async def root():
    """API 首页"""
    return {
        "message": "开放底座AI算法技术开发平台API",
        "version": "2.2.0",
        "docs": "/docs",
        "active_modules": 12,
    }


# ============== 统一异常处理 ==============


def _sanitize_errors(errors: list) -> list:
    """Strip non-JSON-serializable values (e.g. ValueError in ctx) from Pydantic error dicts."""
    sanitized = []
    for err in errors:
        clean = {
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        sanitized.append(clean)
    return sanitized


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log invalid request fields so browser upload errors are diagnosable."""
    raw_errors = exc.errors()
    logger.warning("Request validation failed: %s %s errors=%s", request.method, request.url.path, raw_errors)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": _sanitize_errors(raw_errors)},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Keep client-facing validation detail and record it for upload diagnostics."""
    logger.warning("HTTP error: %s %s status=%s detail=%s", request.method, request.url.path, exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Record multipart/parser errors that are raised before route validation."""
    logger.warning(
        "HTTP parser error: %s %s status=%s detail=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.detail,
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理"""
    logger.error(f"Global exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": str(exc)}
    )


# ============== 启动应用 ==============

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
