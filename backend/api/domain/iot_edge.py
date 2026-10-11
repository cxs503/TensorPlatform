"""
IoT & Edge domain — IoT devices, edge gateway, industrial protocols.

Aggregates routers from 3 IoT/edge API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["IoT与边缘计算"])

# ── IoT device management ──
from api.iot import router as iot_router                          # prefix="/iot"

# ── Edge gateway ──
from api.edge_gateway import router as edge_gateway_router        # prefix="/edge"

# ── Industrial protocol ──
from api.industrial_protocol import router as industrial_protocol_router  # prefix="/industrial"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(iot_router)
router.include_router(edge_gateway_router)
router.include_router(industrial_protocol_router)
