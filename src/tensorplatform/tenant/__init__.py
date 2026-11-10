"""
TensorPlatform Multi-Tenant Isolation v1

12-repo tenant system with:
- Registry: tenant metadata registration
- Quota: resource budget enforcement
- Permission: write-path whitelist + domain-term scanning
- Isolation: per-tenant artifact/event/report isolation
"""

from tensorplatform.tenant.registry import TenantRegistry, TenantMeta
from tensorplatform.tenant.quota import QuotaManager, QuotaExceededError
from tensorplatform.tenant.permission import PermissionEnforcer, WritePermissionError, DomainTermViolation
from tensorplatform.tenant.isolation import IsolationManager, UnauthorizedAccessError

__all__ = [
    "TenantRegistry",
    "TenantMeta",
    "QuotaManager",
    "QuotaExceededError",
    "PermissionEnforcer",
    "WritePermissionError",
    "DomainTermViolation",
    "IsolationManager",
    "UnauthorizedAccessError",
]
