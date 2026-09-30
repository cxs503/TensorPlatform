"""
Observability domain — monitoring, alerting, logging, metrics.

Aggregates routers from monitoring, alert, notification, logging, and metrics_api modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["可观测性"])

# ── Infrastructure monitoring ──
from api.monitoring import router as monitoring_router          # no prefix (routes under /monitoring/*)

# ── Alert management ──
from api.alert import router as alert_router                    # prefix="/alerts"

# ── Notification channels ──
from api.notification import router as notification_router      # prefix="/notifications"

# ── Alertmanager internal receiver ──
from api.alertmanager_webhook import router as alertmanager_webhook_router

# ── Centralized logging ──
from api.logging import router as logging_router                # prefix="/logs"

# ── Prometheus metrics ──
from api.metrics_api import router as metrics_api_router        # prefix="/metrics"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(monitoring_router)
router.include_router(alert_router)
router.include_router(notification_router)
router.include_router(alertmanager_webhook_router)
router.include_router(logging_router)
router.include_router(metrics_api_router)
