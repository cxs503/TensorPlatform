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
# 平台采用领域中立的共性底座，行业方案按 README 约定作为独立 adapter/plugin 交付。
from api.domain import (
    data, model, deployment,          # 数据 / 模型 / 部署
    iam, observability, platform,     # 认证 / 可观测 / 平台
    iot_edge,                         # IoT 与边缘
    ops, tools, misc,                 # 项目运维 / 工具 / 杂项
    tensor,                           # 科研运行台（场景/执行/审批/会话/健康）
)

# 域物理供应商注册：核心审计层经 services.validation_providers 消费域物理，不直接
# import 域实现（边界终裁 2026-11-02，迁移清单见 docs/architecture/platform_physics_boundary_audit.md）
from api.domain.physics_providers import register_ship_resistance_providers  # noqa: E402

register_ship_resistance_providers()

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
    redoc_url=None,  # 禁用 ReDoc（依赖 jsdelivr CDN，受控环境不可达 → 改用 /docs 重定向到自托管控制台）
)

# ============================================================
# 注册 API 路由 — 全部通过 12 个领域聚合路由器
# ============================================================

# ── 本地控制台（零 CDN 依赖，供 /tp/console 内嵌使用）──────────────
# 说明: /docs 的 ReDoc 依赖 jsdelivr CDN，内网/受控环境不可达时是空白页，
# 因此提供自托管的轻量控制台，并把 /docs 重定向过来。
from fastapi.responses import RedirectResponse as _Redirect
from fastapi.staticfiles import StaticFiles as _StaticFiles

_CONSOLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "console")
if os.path.isdir(_CONSOLE_DIR):
    app.mount("/console", _StaticFiles(directory=_CONSOLE_DIR, html=True), name="console")

# ── 智能体协作平台前端（frontend-factory 构建产物，SPA）──────────────
# 构建: VITE_BASE_PATH=/tp/factory/ VITE_API_BASE_URL=/tp/api/v1 npm run build
_FACTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "factory")
if os.path.isdir(_FACTORY_DIR):
    from fastapi.responses import FileResponse as _File

    _FACTORY_ASSETS = os.path.join(_FACTORY_DIR, "assets")
    if os.path.isdir(_FACTORY_ASSETS):
        app.mount("/factory/assets", _StaticFiles(directory=_FACTORY_ASSETS), name="factory-assets")

    @app.get("/factory/{full_path:path}", include_in_schema=False)
    def _factory_spa(full_path: str):
        """SPA history 路由回退：非资源请求一律返回 index.html。"""
        candidate = os.path.join(_FACTORY_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return _File(candidate)
        return _File(os.path.join(_FACTORY_DIR, "index.html"))


@app.get("/docs", include_in_schema=False)
def _docs_redirect():
    # 相对跳转: /tp/docs → /tp/console/ ; 直连 :8010/docs → /console/（两种情况都对）
    return _Redirect("console/", status_code=307)


# ── 免登录入口（embedded 前端自动登录）─────────────────────────────
# 默认账号由 TP_AUTO_LOGIN_USER 指定（默认 eco_web），TP_AUTO_LOGIN=0 可整机关闭。
# 说明: 仅用于内嵌的大屏/工作台演示；它会为任何访问者签发该账号的令牌，
#       因此该账号权限应尽量收敛，公网环境请勿开启。
@app.api_route("/api/v1/auth/auto-login", methods=["GET", "POST"], include_in_schema=False)
def _auto_login():
    if os.getenv("TP_AUTO_LOGIN", "1") != "1":
        raise HTTPException(status_code=404, detail="auto-login disabled")
    from sqlalchemy import text as _text
    from database.config import SessionLocal as _SL
    from utils.jwt_utils import create_token_pair as _pair

    username = os.getenv("TP_AUTO_LOGIN_USER", "eco_web")
    db = _SL()
    try:
        row = db.execute(_text(
            'SELECT id, username, email, role, features FROM users WHERE username = :u AND is_active = true'
        ), {"u": username}).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"auto-login user {username} not found")
    uid, uname, email, role, features = row
    # 安全收敛（2026-11-02 公网实探：匿名可换 ADMIN 令牌）：auto-login 签发角色
    # 默认钳到 VIEWER；演示需提权时设 TP_AUTO_LOGIN_ROLE 显式指定，置
    # __DB__ 恢复签 DB 原角色（旧行为）。
    _clamp = os.getenv("TP_AUTO_LOGIN_ROLE", "VIEWER")
    issued = role if _clamp == "__DB__" else _clamp
    tokens = _pair({"sub": uname, "user_id": uid, "role": issued, "auto": True})
    return {
        **tokens,
        "user": {"id": uid, "username": uname, "email": email, "role": issued,
                 "features": features, "is_admin": str(issued).split(".")[-1].lower() == "admin"},
        "auto": True,
    }

from database.config import get_db as _get_db_  # noqa: E402
from api.deps import require_auth as _require_auth  # noqa: E402

# ── 智能体协作平台：配置 / 总览（原定义在 factory_app.py 的独立 app，主 app 缺失 → 前端任务中心拿不到数据）──
@app.get("/api/v1/tensor/factory", include_in_schema=False)
def _factory_config(user: dict = Depends(_require_auth)):
    """前端 factoryApi.config()；无 FACTORY_PROFILE 时返回可用的默认口径。"""
    try:
        from services.factory_profile import get_factory_profile
        prof = get_factory_profile()
    except Exception:
        prof = None
    if prof is not None:
        try:
            return prof.public()
        except Exception:
            pass
    # 仓库列表：扫本地生态目录（前端任务中心"仓库"下拉的数据源）
    _eco = "/root/tensor-ecosystem"
    _projects = []
    try:
        for _n in sorted(os.listdir(_eco)):
            _p = os.path.join(_eco, _n)
            if _n.startswith("Tensor") and os.path.isdir(os.path.join(_p, ".git")):
                _projects.append({"id": _n, "name": _n})
    except Exception:
        pass
    return {
        "name": "TensorPlatform 智能体协作平台",
        "projects": _projects,
        "roles": [
            {"id": "architect", "name": "架构师", "model_configured": True},
            {"id": "implementer", "name": "实现者", "model_configured": True},
            {"id": "verifier", "name": "验证者", "model_configured": True},
            {"id": "documenter", "name": "文档者", "model_configured": True},
        ],
        "execution": {
            "acceptance_required": os.getenv("TP_AUTO_APPROVE_ALL", "0") != "1",
            "isolation": "host-process",
            "autonomous_llm_configured": True,
        },
    }


@app.get("/api/v1/tensor/factory/overview", include_in_schema=False)
def _factory_overview(db=Depends(_get_db_), user: dict = Depends(_require_auth)):
    """前端 factoryApi.overview()：任务按状态计数 + 待验收数 + 在线 agent 数。"""
    from sqlalchemy import text as _t
    from api.domain.tensor_common import resolved_org as _res_org

    org = _res_org(None, user, db)
    counts = db.execute(_t(
        "SELECT status, count(*) AS count FROM tensor_dev_missions WHERE organization_id=:org GROUP BY status"
    ), {"org": org}).mappings().all()
    pending = db.execute(_t(
        "SELECT count(*) FROM tensor_approval_requests WHERE organization_id=:org AND status='pending'"
    ), {"org": org}).scalar()
    residents = db.execute(_t(
        "SELECT count(*) FROM factory_resident_workers WHERE organization_id=:org AND enabled "
        "AND heartbeat_at > now() - interval '120 seconds'"
    ), {"org": org}).scalar()
    return {
        "online_agent_sessions": residents or 0,
        "online_residents": residents or 0,
        "name": "TensorPlatform 智能体协作平台",
        "organization_id": org,
        "missions_by_status": {r["status"]: r["count"] for r in counts},
        "pending_approvals": pending or 0,
        "agents": [],
    }

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

# 船舶总体性能·耐波域（m1433 交付的 calculations/seakeeping_* 三个端点，m1649 复算发现
# 该域模块从未被 import/挂载 → 线上 404；此处接线，证据见
# docs/audit/m1649_seakeeping_verification_debt.md 与 docs/audits/seakeeping_verification_m1433.md）
from api.domain import ship_seakeeping  # noqa: E402

app.include_router(ship_seakeeping.router, prefix="/api/v1")

# 船舶总体性能·振动噪声域（m1849 接线）：m1820 已实锤该模块 6 条
# ``calculations/{vibration,noise}/*`` 端点从未被 import/挂载 → 线上 404，
# 该域因此被判 NOT_SUPPORTED（"代码 REAL、能力不可达"）。此处补接线；
# 证据见 docs/audit/m1849_vibration_wiring.md 与 artifacts/wiring_census/census.json。
# 用 try/except 包住：任何 import 失败都不得阻断整个 app 启动（部署安全）。
try:
    from api.domain import ship_vibration as _ship_vibration  # noqa: E402
    app.include_router(_ship_vibration.router, prefix="/api/v1")
except Exception as _exc:  # pragma: no cover - 部署面兜底
    logging.getLogger(__name__).warning("ship_vibration router 接线跳过: %s", _exc)

# ── 智能体协作平台（常驻 agent / 战役 / 模型）──────────────────────
# LOCAL PATCH: 这三个 router 存在于 api/ 但 main.py 未挂载，导致
# /api/v1/tensor/factory/* 全部 404（前端 frontend-factory 的智能体页面依赖它们）。
from api import factory_residents as _factory_residents  # noqa: E402
from api import factory_strategy as _factory_strategy    # noqa: E402
from api import factory_models as _factory_models        # noqa: E402

app.include_router(_factory_residents.router, prefix="/api/v1")
app.include_router(_factory_strategy.router, prefix="/api/v1")
app.include_router(_factory_models.router, prefix="/api/v1")

# ── Soul Runtime 接线（PR-6，AOG SPEC M07 知识与上下文）──
# RFC #72 的 /tensor/soul* 端点此前只挂在 factory_app（独立进程）上，
# 生产入口 main:app 不可达（审计 #3554 M07「生产入口不可达」项）。
# 同 ship_vibration 部署安全模式：import 失败只告警，不阻断 app 启动。
try:
    from api import soul_runtime as _soul_runtime  # noqa: E402
    app.include_router(_soul_runtime.router, prefix="/api/v1")
except Exception as _exc:  # pragma: no cover - 部署面兜底
    logging.getLogger(__name__).warning("soul_runtime router 接线跳过: %s", _exc)

# ── 任务梯度与探索预算机制（IN19-Q6）──
# 平台当前处于冻结期（owner 2026-11-02）：本机制以「附加、默认关闭」方式落地，
# 只有 TP_MISSION_GRADIENT=1 时才挂载；未开启时线上派发路径一行未改。
# 详见 docs/mission_gradient_mechanism.md。
if os.getenv("TP_MISSION_GRADIENT", "0") == "1":
    try:
        from api import mission_gradient as _mission_gradient  # noqa: E402
        app.include_router(_mission_gradient.router, prefix="/api/v1")
    except Exception as _exc:  # pragma: no cover - 部署面兜底
        logging.getLogger(__name__).warning("mission_gradient router 接线跳过: %s", _exc)

# ── 成果评价 S×E（AOG SPEC v1.2 §7.1；m5018）──
# outcome_evaluations 实体写入/待评价记录流/分层统计，app 内可达；
# import 失败只告警不阻断启动（同 soul_runtime 部署安全模式）。
try:
    from api import outcome_evaluations as _outcome_evaluations  # noqa: E402
    app.include_router(_outcome_evaluations.router, prefix="/api/v1")
except Exception as _exc:  # pragma: no cover - 部署面兜底
    logging.getLogger(__name__).warning("outcome_evaluations router 接线跳过: %s", _exc)

# ── 公共实况展示面（免登录只读，owner 2026-10-22）──
from api import public  # noqa: E402

# ── 战役成熟化整合门（判据→证据→台账→契约 的通用裁判，p54w3-n08 交付）──
from api import maturity_gate as _maturity_gate  # noqa: E402

app.include_router(_maturity_gate.router, prefix="/api/v1")
app.include_router(public.router, prefix="/api/v1")

# SPEC §6.5 审查预算账本（测量先行）：四类审查记账 + 周聚合查询
from api import review_budget as _review_budget  # noqa: E402

app.include_router(_review_budget.router, prefix="/api/v1")

# ── 提案→战役播种供给链机器门（契约 / 去重 / 覆盖率账本，零领域语义）──
try:
    from api import proposal_gate as _proposal_gate  # noqa: E402

    app.include_router(_proposal_gate.router, prefix="/api/v1")
except Exception as _exc:  # pragma: no cover - 部署面兜底
    logging.getLogger(__name__).warning("proposal_gate router 接线跳过: %s", _exc)


# ── 多保真级联打分器（IN59-N10，m6604）──
# Low→Medium→High fidelity cascade with physics-consistency gates and
# budget-adaptive sampling.  Addresses wall_exhausted failure mode
# (plan 41 / IN25-T3) by eliminating ≥80% candidates at low+medium stages.
# 部署安全模式：import 失败只告警，不阻断 app 启动。
try:
    from api import cascade_scorer as _cascade_scorer  # noqa: E402
    app.include_router(_cascade_scorer.router, prefix="/api/v1")
except Exception as _exc:  # pragma: no cover - 部署面兜底
    logging.getLogger(__name__).warning("cascade_scorer router 接线跳过: %s", _exc)

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
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers[
        "Content-Security-Policy"
    ] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; frame-ancestors 'self'"
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


def _capability_ledger_field() -> dict:
    """消费者 #3：/health 的能力台账体检字段（永不抛异常，失败即 DEGRADED）。"""
    try:
        from api.capability_health import ledger_field
        return ledger_field()
    except Exception as exc:  # 后端启动顺序/路径问题不能把健康端点打成 500
        return {"status": "DEGRADED",
                "reasons": [f"HEALTH_CONSUMER_IMPORT_FAILED:{type(exc).__name__}"],
                "path": None, "sha256": None, "schema_version": None,
                "capabilities": 0, "unverified": 0, "overclaim": 0,
                "contract_version": "unknown"}


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
        },
        # 证据台账体检：path/sha256/capabilities/unverified/overclaim（m5352 消费者契约门）
        "capability_ledger": _capability_ledger_field(),
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


# ── 能力证据自动升级管线（P218-N01）─────────────────────────────────────────
# 证据入库 → 同调用内自动重判 → 台账自愈（WITHHELD→REAL）。域无关：能力位与
# 验收判据全部来自配置/工件注入。挂载失败不得拖垮主应用。
try:  # pragma: no cover - 环境相关
    from tools.capability_upgrade.api import build_router as _capability_upgrade_router
    from tools.capability_upgrade.pipeline import EvidencePipeline as _CapabilityPipeline
    from tools.capability_upgrade.store import Store as _CapabilityStore

    _capability_store = _CapabilityStore()
    _capability_pipeline = _CapabilityPipeline(_capability_store)
    app.include_router(
        _capability_upgrade_router(_capability_pipeline, _capability_store),
        prefix="/api/v1/capability-upgrade",
    )
    logger.info("capability-upgrade API mounted at /api/v1/capability-upgrade")
except Exception:  # pragma: no cover
    logger.warning("capability-upgrade API not mounted", exc_info=True)

# ── 能力台账语义三元分层 + NOT_SUPPORTED 误用治理（P266W1-N10）─────────────
# 只读 artifacts/capability_semantics/ 工件；判定逻辑在 tools/capability_semantics
#（零领域语义：能力 id/依赖边来自 config/capability_semantics.json）。挂载失败不拖垮主应用。
try:  # pragma: no cover - 环境相关
    from api.capability_semantics import router as _capability_semantics_router

    app.include_router(_capability_semantics_router, prefix="/api/v1")
    logger.info("capability-semantics API mounted at /api/v1/tensor/capabilities/semantics")
except Exception:  # pragma: no cover
    logger.warning("capability-semantics API not mounted", exc_info=True)

# ── 能力证据审计（既有 api/capability_audit.py，此前**从未挂载**）──────────────
# P266W1-N10 接线时发现：该 router 在仓内存在但 main.py 无 include → 线上
# /tensor/capabilities/audit 一直 404（文档与审计脚本引用了它）。此处一并接线。
try:  # pragma: no cover - 环境相关
    from api.capability_audit import router as _capability_audit_router

    app.include_router(_capability_audit_router, prefix="/api/v1")
    logger.info("capability-audit API mounted at /api/v1/tensor/capabilities/audit")
except Exception:  # pragma: no cover
    logger.warning("capability-audit API not mounted", exc_info=True)


# ── 跨仓能力依赖图谱 + 级联失效预警（p285w2b-n03）───────────────────────────
# 只读台账/配置/工件：节点=能力台账条目，边=依赖关系，属性=证据等级/置信度；
# 等级变化 → 级联重算 → 预警。零领域语义：能力 id 与依赖边全经配置注入。
# 挂载失败不得拖垮主应用。
try:  # pragma: no cover - 环境相关
    from api.capability_graph import router as _capability_graph_router

    app.include_router(_capability_graph_router, prefix="/api/v1")
    logger.info("capability-graph API mounted at /api/v1/capability-graph")
except Exception:  # pragma: no cover
    logger.warning("capability-graph API not mounted", exc_info=True)


# ── 能力台账自动升账（p262w1-N07）──────────────────────────────────────────
# 证据链完整 → 升 REAL；回归/证据断裂 → 降 MOCK/WITHHELD；每次操作落 append-only
# sha256 链审计日志。规格由 config/capability_chain_spec.json 注入（零领域语义），
# 能力 id 是不透明路径参数。挂载失败不得拖垮主应用。
try:  # pragma: no cover - 环境相关
    from pathlib import Path as _Path

    from tools.capability_auto_audit.api import build_router as _auto_audit_build_router
    from tools.capability_auto_audit.auto_audit_engine import AutoAuditEngine as _AutoAuditEngine
    from tools.capability_auto_audit.evidence_chain import ChainSpec as _ChainSpec

    _repo_root = _Path(__file__).resolve().parents[1]
    _auto_audit_root = _repo_root / "artifacts" / "capability_auto_audit" / "runtime"
    _auto_audit_spec_path = _repo_root / "config" / "capability_chain_spec.json"
    _auto_audit_engine = _AutoAuditEngine(
        verified_dir=_auto_audit_root / "verified",
        state_path=_auto_audit_root / "ledger_state.json",
        log_path=_auto_audit_root / "audit_log.jsonl",
        spec=(_ChainSpec.from_file(_auto_audit_spec_path)
              if _auto_audit_spec_path.is_file() else _ChainSpec()),
    )
    app.include_router(
        _auto_audit_build_router(_auto_audit_engine, _require_auth),
        prefix="/api/v1",
    )
    logger.info("capability auto-audit API mounted at /api/v1/capabilities/{id}/auto-audit")
except Exception:  # pragma: no cover
    logger.warning("capability auto-audit API not mounted", exc_info=True)


# ── 多租户隔离机制 v1 的 HTTP 面（P337W1-N02）───────────────────────────────
# 只做服务层透传：拒绝一律 403/409，调用方不会把拒绝误当成功。租户事实由
# config/tenant_registry.json 注入（零领域语义）；挂载失败不得拖垮主应用。
try:  # pragma: no cover - 环境相关
    from api.tenant_isolation import router as _tenant_isolation_router

    app.include_router(_tenant_isolation_router, prefix="/api/v1")
    logger.info("tenant-isolation API mounted at /api/v1/tenant-isolation")
except Exception:  # pragma: no cover
    logger.warning("tenant-isolation API not mounted", exc_info=True)


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
