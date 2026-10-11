"""
Deployment domain — aggregates all deployment & serving APIs.

Aggregates routers from deployment-related API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["部署与服务"])

# ── Deployment management ──
from api.deployment import router as deployment_router            # prefix="/deployments"
from api.deployment import strategy_router as deployment_strategy_router  # prefix="/deployment-strategy"
from api.model_deployment import router as model_deployment_router  # prefix="/model-deployment"
from api.inference import router as inference_router              # prefix="/inference"
from api.model_artifacts import router as model_artifacts_router  # prefix="/model-artifacts"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(deployment_router)
router.include_router(deployment_strategy_router)
router.include_router(model_deployment_router)
router.include_router(inference_router)
router.include_router(model_artifacts_router)
