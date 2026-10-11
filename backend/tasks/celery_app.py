"""
Celery异步任务配置
"""
import os

from celery import Celery

# Celery配置。默认主机与 docker-compose 的 Redis 服务名保持一致，
# 同时允许部署环境通过完整 URL 覆盖默认值。
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL", f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND", f"redis://{REDIS_HOST}:{REDIS_PORT}/1"
)

# 创建Celery应用
celery_app = Celery(
    "open_foundation", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND
)

# Celery配置
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1小时超时
    worker_prefetch_multiplier=4,
    broker_connection_retry_on_startup=True,
)
