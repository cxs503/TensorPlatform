"""Domain aggregation layer — groups related API routers for cleaner main.py."""
from api.domain.data import router as data_router
from api.domain.model import router as model_router
from api.domain.deployment import router as deployment_router
from api.domain.iam import router as iam_router
from api.domain.observability import router as observability_router
from api.domain.platform import router as platform_router
from api.domain.energy import router as energy_router
from api.domain.ai_app import router as ai_app_router
from api.domain.iot_edge import router as iot_edge_router
from api.domain.ops import router as ops_router
from api.domain.tools import router as tools_router
from api.domain.misc import router as misc_router

__all__ = [
    "data_router",
    "model_router",
    "deployment_router",
    "iam_router",
    "observability_router",
    "platform_router",
    "energy_router",
    "ai_app_router",
    "iot_edge_router",
    "ops_router",
    "tools_router",
    "misc_router",
]
