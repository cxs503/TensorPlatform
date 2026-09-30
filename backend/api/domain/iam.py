"""
IAM domain — identity, access, audit, compliance.

Aggregates routers from auth, rbac, api_key, kms, audit, compliance, and tenant modules.
Each sub-module retains its own prefix; the domain router adds no extra prefix,
so all route paths are preserved exactly.
"""
from fastapi import APIRouter

router = APIRouter(tags=["身份与访问管理"])

# ── Authentication ──
from api.auth import router as auth_router                      # no prefix
from api.auth import enhanced_router as auth_enhanced_router    # prefix="/auth/enhanced"
from api.auth import router as auth_prefixed_router              # re-register with /auth prefix

# ── Authorization ──
from api.rbac import router as rbac_router                      # prefix="/rbac"

# ── API Key management ──
from api.api_key import router as api_key_router                # prefix="/api-keys"

# ── Key management (KMS) ──
from api.kms import router as kms_router                        # prefix="/kms"

# ── Audit logging ──
from api.audit import router as audit_router                    # prefix="/audit"

# ── Security compliance ──
from api.compliance import router as compliance_router          # prefix="/compliance"

# ── Multi-tenancy ──
from api.tenant import router as tenant_router                  # prefix="/tenants"

# ── User management (admin only) ──
from api.users import router as users_router                    # prefix="/users"

# ── Register all sub-routers (preserving their original prefixes) ──
router.include_router(auth_router)
router.include_router(auth_prefixed_router, prefix="/auth")
router.include_router(auth_enhanced_router)
router.include_router(rbac_router)
router.include_router(api_key_router)
router.include_router(kms_router)
router.include_router(audit_router)
router.include_router(compliance_router)
router.include_router(tenant_router)
router.include_router(users_router)
