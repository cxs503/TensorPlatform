"""
Prometheus 监控指标 API

提供 /api/v1/metrics 端点，暴露 Prometheus 文本格式指标。
指标包括:
  - HTTP 请求计数 (http_requests_total)
  - 活跃训练任务数 (training_jobs_active)
  - 预测服务总数 (predictions_total)
  - 数据库连接池状态 (db_pool_size, db_pool_checked_out, db_pool_overflow)
"""

import logging
import os

from fastapi import APIRouter, Response
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    REGISTRY,
)
from sqlalchemy import create_engine, text

from utils.experiment_metrics import EXPERIMENT_KINDS, metric_kind

logger = logging.getLogger(__name__)

# ========== 指标注册表 ==========
# 使用默认 REGISTRY，平台收集器 (process/gc) 会自动注册

# HTTP 请求计数器 — 按 path, method, status 标签
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    labelnames=["path", "method", "status"],
)

# HTTP 请求耗时 — 与请求计数使用相同标签，可用于按路由定位慢请求。
# 桶覆盖常见 API 请求以及较慢的推理/数据处理请求。
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["path", "method", "status"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

# 训练任务活跃数
training_jobs_active = Gauge(
    "training_jobs_active",
    "Number of currently active training jobs",
)

# 预测服务总数
predictions_total = Counter(
    "predictions_total",
    "Total number of predictions served",
    labelnames=["model_type", "status"],
)

# 数据库连接池状态
db_pool_size = Gauge(
    "db_pool_size",
    "Configured maximum database connection pool size",
)

db_pool_checked_out = Gauge(
    "db_pool_checked_out",
    "Number of database connections currently checked out",
)

db_pool_overflow = Gauge(
    "db_pool_overflow",
    "Current overflow count beyond pool size",
)

# Durable cross-process truth. Completion/failure events happen in Celery
# workers, so API-process counters alone cannot represent the online state.
experiment_persisted_jobs = Gauge(
    "model_factory_experiment_persisted_jobs",
    "Persisted experiment jobs grouped by bounded kind and durable status.",
    labelnames=["kind", "status"],
)
experiment_persisted_snapshot_up = Gauge(
    "model_factory_experiment_persisted_snapshot_up",
    "Whether the latest persisted experiment snapshot refresh succeeded.",
)

_EXPERIMENT_STATUSES = (
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"
)

# ========== Router ==========

router = APIRouter(prefix="/metrics", tags=["监控指标"])


@router.get("")
async def get_metrics():
    """
    暴露 Prometheus 文本格式的指标数据。

    返回内容类型: text/plain; version=0.0.4; charset=utf-8
    """
    # 尝试更新数据库连接池指标
    try:
        _update_db_pool_metrics()
    except Exception as e:
        logger.debug(f"Failed to update db pool metrics: {e}")

    try:
        _update_experiment_persisted_metrics()
    except Exception as exc:
        experiment_persisted_snapshot_up.set(0)
        logger.debug(
            "Failed to refresh persisted experiment metrics: %s",
            type(exc).__name__,
        )

    # 生成最新指标
    metrics_data = generate_latest(REGISTRY)

    return Response(
        content=metrics_data,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ========== 辅助函数 ==========


def _update_db_pool_metrics():
    """从 SQLAlchemy 引擎连接池获取当前状态并更新 Gauge"""
    from database.config import engine

    pool = engine.pool
    try:
        db_pool_size.set(pool.size())
        db_pool_checked_out.set(pool.checkedout())
        db_pool_overflow.set(max(0, pool.overflow()))
    except Exception:
        # 某些池实现可能不支持上述方法，忽略
        pass


def _update_experiment_persisted_metrics(session_factory=None):
    """Refresh a DB-backed snapshot visible from the API scrape process."""
    if session_factory is None:
        from database.config import SessionLocal

        session_factory = SessionLocal
    db = session_factory()
    try:
        rows = db.execute(text("""
            SELECT
                hyperparameters -> '_experiment_runtime' ->> 'kind' AS kind,
                CAST(status AS TEXT) AS status,
                COUNT(*) AS job_count
            FROM training_jobs
            WHERE training_job_id LIKE 'experiment-%'
              AND hyperparameters -> '_experiment_runtime' IS NOT NULL
            GROUP BY
                hyperparameters -> '_experiment_runtime' ->> 'kind',
                CAST(status AS TEXT)
        """)).all()
    except Exception:
        experiment_persisted_snapshot_up.set(0)
        raise
    finally:
        db.close()

    kinds = tuple(sorted(EXPERIMENT_KINDS)) + ("other",)
    snapshot = {(kind, status): 0 for kind in kinds for status in _EXPERIMENT_STATUSES}
    for raw_kind, raw_status, raw_count in rows:
        status = str(getattr(raw_status, "value", raw_status) or "").upper()
        if status not in _EXPERIMENT_STATUSES:
            continue
        snapshot[(metric_kind(raw_kind), status)] += max(0, int(raw_count))
    for (kind, status), count in snapshot.items():
        experiment_persisted_jobs.labels(kind=kind, status=status).set(count)
    experiment_persisted_snapshot_up.set(1)


def record_prediction(model_type: str = "unknown", status: str = "success"):
    """记录一次预测请求（可供推理 API 调用）"""
    predictions_total.labels(model_type=model_type, status=status).inc()


def set_training_jobs_active(count: int):
    """设置当前活跃训练任务数"""
    training_jobs_active.set(count)


def increment_training_jobs_active():
    """活跃训练任务数 +1"""
    training_jobs_active.inc()


def decrement_training_jobs_active():
    """活跃训练任务数 -1"""
    training_jobs_active.dec()
