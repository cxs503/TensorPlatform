"""
Miscellaneous domain — Jupyter, data pipeline, frontend compat, WebSocket, ML experiments, notebooks, virtual data, ONNX, scheduler.

Aggregates routers from ~10 miscellaneous API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["其他功能"])

# ── Agent-led research factory ──
from api.research_factory import router as research_factory_router
router.include_router(research_factory_router)

# ── Ship performance calculations (resistance, stability, timing) ──
from api.calculations import router as calculations_router         # prefix="/calculations"
router.include_router(calculations_router)

# ── Ice Resistance UQ (PCE + Smolyak + Sobol) ──
from api.domain.ice_uq import router as ice_uq_router             # prefix="/ice-uq"
router.include_router(ice_uq_router)

# ── Ice-class hull form multi-objective optimization ──
from api.ice_optimization import router as ice_optimization_router  # prefix="/ice_optimization"
router.include_router(ice_optimization_router)

# ── JupyterLab ──
from api.jupyter import router as jupyter_router                  # prefix="/jupyter"

# ── Data pipeline (end-to-end orchestration) ──
from api.data_pipeline_api import router as data_pipeline_router  # prefix="/pipeline"
# ── Frontend compatibility layer ──
from api.frontend_compat import router as frontend_compat_router  # no prefix

# ── WebSocket ──
from api.websocket import router as websocket_router              # no prefix

# ── A/B testing ──
from api.abtest import router as abtest_router                    # prefix="/abtest"

# ── Advanced features (NAS, etc.) ──
from api.advanced_features import router as advanced_features_router  # prefix="/advanced"

# ── MLOps closed-loop ──
from api.mlops import router as mlops_router                      # prefix="/mlops"
from api.knowledge_graph import router as kg_router               # prefix="/knowledge-graph"
from api.evaluation_center import router as eval_router             # prefix="/evaluation-center"
from api.organizations import router as org_router                # prefix="/organizations"
from api.metering import router as metering_router                # prefix="/metering"
from api.data_assets import router as asset_router                # prefix="/data-assets"
from api.agent import router as agent_router                    # prefix="/agent"
router.include_router(kg_router)
router.include_router(eval_router)
router.include_router(org_router)
router.include_router(metering_router)
router.include_router(asset_router)
router.include_router(agent_router)

# ── Notebook dev environment ──
from api.notebook import router as notebook_router                # prefix="/notebooks"

# ── Feature engineering ──
from api.feature_engineering import router as feature_engineering_router  # prefix="/feature-engineering"

# ── Virtual data generation ──

# ── ONNX export & inference ──
from api.onnx_api import router as onnx_api_router                # prefix="/onnx"

# ── Scheduler (optional dependency: croniter) ──
try:
    from api.scheduler import router as _scheduler_router         # prefix="/schedules"
    router.include_router(_scheduler_router)
    from api.dvc_version import router as _dvc_router              # prefix="/dvc"
    router.include_router(_dvc_router)
except ImportError:
    pass

# ── DataHub 集成 ──
from api.datahub_integration import router as datahub_router      # prefix="/datahub"

# ── DataHub Metadata (catalog/search/profile/glossary) ──
from api.datahub_metadata import router as datahub_metadata_router  # prefix="/datahub"
from api.dashboard import router as dashboard_router              # prefix="/dashboard"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(jupyter_router)
router.include_router(data_pipeline_router)
router.include_router(frontend_compat_router)
router.include_router(websocket_router)
router.include_router(abtest_router)
router.include_router(advanced_features_router)
router.include_router(mlops_router)
router.include_router(notebook_router)
router.include_router(feature_engineering_router)
router.include_router(onnx_api_router)
router.include_router(datahub_router)
router.include_router(datahub_metadata_router)
router.include_router(dashboard_router)
