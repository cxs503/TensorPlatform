"""
Tools & Infrastructure domain — code generator, file upload, marketplace, compression, metadata, tdengine, streaming.

Aggregates routers from 7 tools/infrastructure API modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["工具与基础设施"])

# ── Code generator ──
from api.workspace import router as workspace_router
from api.code_generator import router as code_generator_router    # prefix="/code-generator"

# ── File upload ──
from api.file_upload import router as file_upload_router          # no prefix

# ── Marketplace ──
from api.marketplace import router as marketplace_router          # prefix="/marketplace"

# ── Model compression ──
from api.compression import router as compression_router          # prefix="/compression"

# ── Metadata management ──
from api.metadata import router as metadata_router                # prefix="/metadata"

# ── Time-series database (TDengine) ──
from api.tdengine import router as tdengine_router                # prefix="/tdengine"

# ── Data streaming ──
from api.streaming import router as streaming_router              # prefix="/streaming"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(workspace_router)
router.include_router(code_generator_router)
router.include_router(file_upload_router)
router.include_router(marketplace_router)
router.include_router(compression_router)
router.include_router(metadata_router)
router.include_router(tdengine_router)
router.include_router(streaming_router)
