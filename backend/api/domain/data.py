"""
Data domain — aggregates all data management APIs.

Aggregates routers from 13 data_* API modules into a single data router.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["数据中台"])

# ── Data source & dataset management ──
from api.datasource import router as datasource_router          # prefix="/datasources"
from api.datasets import router as datasets_router
from api.tensorphysics_runs import router as tensorphysics_runs_router  # scientific run manifests and artifact references              # prefix="/datasets"
from api.curated_datasets import router as curated_datasets_router  # prefix="/curated-datasets"
from api.data_query import router as data_query_router          # prefix="/data-query"

# ── Data governance ──
from api.data_catalog import router as data_catalog_router      # prefix="/catalog"
from api.data_collection import router as data_collection_router  # prefix="/collection"
from api.data_cleaning import router as data_cleaning_router      # prefix="/data-cleaning"
from api.data_governance import router as data_governance_router  # prefix="/data-governance"
from api.dataset_governance import router as dataset_governance_router  # prefix="/dataset-governance"
from api.data_lineage import router as data_lineage_router      # prefix="/lineage"
from api.data_masking import router as data_masking_router      # prefix="/masking"
from api.data_quality import router as data_quality_router      # prefix="/quality"
from api.data_profiling import router as data_profiling_router  # prefix="/datasets" (profiling, editor, align)
from api.data_standard import router as data_standard_router    # prefix="/standards"
from api.data_standard_frontend import router as data_standard_frontend_router  # prefix="/data-standard"
from api.data_subscription import router as data_subscription_router  # prefix="/data-subscription"
from api.data_sync import router as data_sync_router            # prefix="/sync"
from api.data_validation import router as data_validation_router  # prefix="/data-validation" (训练前数据校验)
from api.intelligent_data_analysis import router as intelligent_data_analysis_router  # prefix="/intelligent-analysis"
from api.data_analysis import router as data_analysis_router  # prefix="/data-analysis"
from api.realtime_sensor_monitor import router as realtime_sensor_monitor_router  # prefix="/realtime-sensor-monitors"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(datasource_router)
router.include_router(datasets_router)
router.include_router(tensorphysics_runs_router)
router.include_router(curated_datasets_router)
router.include_router(data_query_router)
router.include_router(data_catalog_router)
router.include_router(data_collection_router)
router.include_router(data_cleaning_router)
router.include_router(data_governance_router)
router.include_router(dataset_governance_router)
router.include_router(data_lineage_router)
router.include_router(data_masking_router)
router.include_router(data_quality_router)
router.include_router(data_profiling_router)
router.include_router(data_standard_router)
router.include_router(data_standard_frontend_router)
router.include_router(data_subscription_router)
router.include_router(data_sync_router)
router.include_router(data_validation_router)
router.include_router(intelligent_data_analysis_router)
router.include_router(data_analysis_router)
router.include_router(realtime_sensor_monitor_router)
