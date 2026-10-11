"""
Energy domain — carbon accounting, photovoltaic, storage optimization, load forecasting, energy analysis, energy efficiency, dashboard.

Aggregates routers from 7 energy-related API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["能碳与新能源"])

# ── Carbon accounting ──
from api.carbon import router as carbon_router                    # prefix="/carbon"

# ── Photovoltaic management ──
from api.photovoltaic import router as photovoltaic_router        # prefix="/photovoltaic"

# ── Storage optimization ──
from api.storage_optimization import router as storage_optimization_router  # prefix="/storage"

# ── Load forecasting ──
from api.load_forecasting import router as load_forecasting_router  # prefix="/load-forecasting"

# ── Energy analysis ──
from api.energy_analysis import router as energy_analysis_router  # prefix="/energy"

# ── Energy efficiency ──
from api.energy_efficiency import router as energy_efficiency_router  # prefix="/energy-efficiency"

# ── Dashboard ──
from api.dashboard import router as dashboard_router              # prefix="/dashboard"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(carbon_router)
router.include_router(photovoltaic_router)
router.include_router(storage_optimization_router)
router.include_router(load_forecasting_router)
router.include_router(energy_analysis_router)
router.include_router(energy_efficiency_router)
router.include_router(dashboard_router)
