"""
Platform domain — gateway, service mesh, config center, CI/CD, caching, backup, GPU scheduling, billing.

Aggregates routers from gateway, service_mesh, config_center, cicd, cache, backup, gpu_scheduler and billing modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["平台管理"])

# ── API Gateway ──
from api.gateway import router as gateway_router                # prefix="/gateway"

# ── Service Mesh ──
from api.service_mesh import router as service_mesh_router      # prefix="/mesh"

# ── Configuration Center ──
from api.config_center import router as config_center_router    # prefix="/config"

# ── CI/CD Pipelines ──
from api.cicd import router as cicd_router                      # prefix="/cicd"

# ── Data Caching ──
from api.cache import router as cache_router                    # prefix="/cache"

# ── Backup & Recovery ──
from api.backup import router as backup_router                  # prefix="/backup"

# ── GPU Resource Scheduling ──
from api.gpu_scheduler import router as gpu_scheduler_router    # prefix="/gpu"

# ── Billing & Pricing ──
from api.billing import router as billing_router                # prefix="/billing"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(gateway_router)
router.include_router(service_mesh_router)
router.include_router(config_center_router)
router.include_router(cicd_router)
router.include_router(cache_router)
router.include_router(backup_router)
router.include_router(gpu_scheduler_router)
router.include_router(billing_router)
