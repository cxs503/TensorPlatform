"""
Tensor domain — the research console operation plane.

Aggregates scenario / execution / approval / session / system routers.
Sub-modules keep their own prefixes under /api/v1/tensor/*.
"""
from fastapi import APIRouter

router = APIRouter(tags=["科研运行台"])

from api.tensor_scenarios import router as tensor_scenarios_router          # prefix="/tensor/scenarios"
from api.tensor_executions import router as tensor_executions_router       # prefix="/tensor/executions"
from api.tensor_approvals import router as tensor_approvals_router         # prefix="/tensor/approvals"
from api.tensor_sessions import router as tensor_sessions_router           # prefix="/tensor/sessions"
from api.tensor_system import router as tensor_system_router               # prefix="/tensor/system"
from api.tensor_activity import router as tensor_activity_router           # prefix="/tensor/activity"

router.include_router(tensor_scenarios_router)
router.include_router(tensor_executions_router)
router.include_router(tensor_approvals_router)
router.include_router(tensor_sessions_router)
router.include_router(tensor_system_router)
router.include_router(tensor_activity_router)
