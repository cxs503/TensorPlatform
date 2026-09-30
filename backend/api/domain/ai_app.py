"""
AI Application domain — fault prediction, intelligent control, digital twin, steel industry.

Aggregates routers from 5 AI-application API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["AI行业应用"])

# ── Fault prediction ──
from api.fault_prediction import router as fault_prediction_router  # prefix="/fault-prediction"

# ── Intelligent control ──
from api.intelligent_control import router as intelligent_control_router  # prefix="/control"

# ── Digital twin ──
from api.digital_twin import router as digital_twin_router        # prefix="/digital-twin"

# ── Steel AI case teaching ──
from api.steel_ai_case import router as steel_ai_case_router      # prefix="/steel-ai-case"

# ── Steel energy management ──
from api.steel_energy import router as steel_energy_router        # prefix="/steel-energy"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(fault_prediction_router)
router.include_router(intelligent_control_router)
router.include_router(digital_twin_router)
router.include_router(steel_ai_case_router)
router.include_router(steel_energy_router)
