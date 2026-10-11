"""
Ops domain — project management, feature engineering, reporting, remote ops, work orders.

Aggregates routers from 5 ops/management API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["项目管理与运维"])

# ── Project management ──
from api.projects import router as projects_router                # prefix="/projects"

# ── Feature engineering ──
from api.feature_engineering import router as feature_engineering_router  # prefix="/feature-engineering"

# ── Reporting ──
from api.report import router as report_router                    # prefix="/reports"

# ── Remote operations ──
from api.remote_ops import router as remote_ops_router            # prefix="/remote-ops"

# ── Work orders ──
from api.work_order import router as work_order_router            # prefix="/work-orders"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(projects_router)
router.include_router(feature_engineering_router)
router.include_router(report_router)
router.include_router(remote_ops_router)
router.include_router(work_order_router)
