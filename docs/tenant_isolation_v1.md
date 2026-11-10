# Tenant Isolation v1 — Design & Implementation

**Version**: 1.0  
**Date**: 2026-11-10  
**Mission**: m10800 / p337w1-n02-tenant-isolation-maturity  
**Status**: ✅ Implemented & Verified

---

## 1. Overview

This document describes the multi-tenant isolation mechanism v1 for the TensorPlatform 12-repo ecosystem. The mechanism provides **mechanism-level guarantees** (not just discipline-level) for:

1. **Tenant Registration** — 12 repos with complete metadata
2. **Resource Quotas** — CPU/memory/storage/task budgets per tenant per cycle
3. **Permission Boundaries** — Write-path whitelist enforcement + domain-term scanning
4. **Data Isolation** — Per-tenant artifacts/events/reports with explicit cross-tenant authorization

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TenantRegistry                            │
│  12 tenants: {name, repo, allowed_domains, quota, class}    │
└──────────────┬──────────────────────────────────────────────┘
               │
    ┌──────────┼──────────┬──────────────┐
    ▼          ▼          ▼              ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│ Quota  │ │Permiss-│ │ Isolation│ │ Scanner  │
│Manager │ │ion     │ │ Manager  │ │(domain   │
│        │ │Enforcer│ │          │ │ terms)   │
└────────┘ └────────┘ └──────────┘ └──────────┘
```

### Module Structure

```
src/tensorplatform/tenant/
├── __init__.py          # Package exports
├── registry.py          # Tenant metadata registration (12 repos)
├── quota.py             # Resource budget enforcement
├── permission.py        # Write-path whitelist + domain scanning
└── isolation.py         # Per-tenant data isolation
```

## 3. The 12 Tenants

| # | Name | Repo | Domains | Classification |
|---|------|------|---------|----------------|
| 1 | TensorLBM | TensorLBM | lbm, fluid, multiphase | internal |
| 2 | TensorFVM | TensorFVM | fvm, fluid, heat_transfer | internal |
| 3 | TensorFEM | TensorFEM | fem, structural, thermal | internal |
| 4 | TensorDEM | TensorDEM | dem, granular, contact | internal |
| 5 | TensorBEM | TensorBEM | bem, potential_flow, acoustic | internal |
| 6 | TensorSPH | TensorSPH | sph, fluid, free_surface | internal |
| 7 | TensorDG | TensorDG | dg, hyperbolic, wave | internal |
| 8 | TensorCAD | TensorCAD | cad, geometry, meshing | internal |
| 9 | TensorPlatform | TensorPlatform | platform, infrastructure, governance | internal |
| 10 | TensorPhysics | TensorPhysics | physics, workflow, verification | internal |
| 11 | TenantDomainRepo | domain-repo-private | hydrodynamics, propulsion, ... | confidential |
| 12 | TensorPapers | TensorPapers | publication, documentation, review | confidential |

## 4. Resource Quotas

Each tenant has per-cycle budgets:

| Resource | Description | Unit |
|----------|-------------|------|
| cpu_core_hours | CPU compute time | core-hours |
| memory_gb | Memory allocation | GB |
| storage_gb | Persistent storage | GB |
| max_tasks | Concurrent tasks | count |
| max_artifacts | Artifacts per cycle | count |

**Enforcement**: Claims exceeding budget raise `QuotaExceededError`. The quota manager tracks usage and supports release (for completed tasks).

## 5. Permission Boundaries

### 5.1 Write-Path Whitelist

Each tenant can only write to paths within their own repository:
- ✅ `TensorLBM` → `/workspace/TensorLBM/...`
- ❌ `TensorLBM` → `/workspace/TensorFVM/...` (cross-repo denied)

### 5.2 Domain Term Scanning

Platform-neutral repos (TensorPlatform) must contain **zero domain-specific terms**:
- ASCII terms: `ice`, `resistance`, `propeller`, `seakeeping`, `ship`, etc.
- CJK terms: `冰区`, `阻力`, `推进`, `耐波`, `船体`, etc.

The denylist is loaded from `config/domain_word_denylist.json` (data-driven, not hardcoded).

**Word boundary matching**: ASCII terms use word boundary regex (`(?<![A-Za-z0-9_])term(?![A-Za-z0-9_])`) to avoid false positives (e.g., "service" does not match "ice").

## 6. Data Isolation

### 6.1 Resource Ownership

All artifacts, events, and reports are owned by a specific tenant. Access is controlled:
- **Owner**: Full access
- **Cross-tenant**: Requires explicit `AccessGrant`

### 6.2 Access Grants

Grants specify:
- Granting tenant (owner)
- Receiving tenant
- Resource type and ID (or `*` for wildcard)
- Access level: `read`, `write`, `admin`
- Optional expiration

### 6.3 Audit Trail

All access attempts (allowed and denied) are logged with:
- Requesting/target tenant
- Resource type/ID
- Access level
- Result (allowed/denied)
- Grant ID (if applicable)

## 7. Acceptance Criteria Results

| Criterion | Description | Result |
|-----------|-------------|--------|
| C1 | 12 tenant metadata fully registered | ✅ 12/12 |
| C2 | Over-budget claim rejection 100% | ✅ 10/10 rejected |
| C3 | Cross-repo write rejection 100% | ✅ 10/10 rejected |
| C4 | Domain term scan rejection 100% | ✅ 5/5 rejected |
| C5 | Cross-tenant access rejection 100% | ✅ 5/5 rejected |
| C6 | Existing mechanism compatibility | ✅ 100% pass |
| C7 | Zero domain semantics in platform | ✅ 0 hits |

## 8. Iron Rule Compliance

This implementation strictly follows the **zero domain semantics** rule for TensorPlatform:
- No domain-specific terms in code logic
- Domain terms loaded from external config (`domain_word_denylist.json`)
- Domain allowlists are abstract capability keys, not domain vocabulary
- All domain content belongs in domain repos (TenantDomainRepo, TensorPhysics)

## 9. Usage Examples

```python
from tensorplatform.tenant import (
    TenantRegistry, QuotaManager, PermissionEnforcer, IsolationManager
)

# Initialize
registry = TenantRegistry()
registry.initialize_defaults()  # Register 12 tenants

quota_mgr = QuotaManager(registry)
enforcer = PermissionEnforcer(registry)
isolation_mgr = IsolationManager(registry)

# Claim resources
quota_mgr.claim("TensorLBM", "cpu_core_hours", 50.0)

# Write with permission check
from tensorplatform.tenant.permission import WriteRequest
req = WriteRequest(
    tenant_repo="TensorLBM",
    target_path="/workspace/TensorLBM/src/solver.py",
    content="# LBM solver code",
)
enforcer.enforce_write(req)

# Register and access artifacts
resource = isolation_mgr.register_resource("TensorLBM", "artifact", "result.h5")
isolation_mgr.access_resource("TensorLBM", resource.resource_id)  # OK
# isolation_mgr.access_resource("TensorFVM", resource.resource_id)  # UnauthorizedAccessError!

# Grant cross-tenant access
isolation_mgr.grant_access("TensorLBM", "TensorFVM", "artifact", resource.resource_id)
isolation_mgr.access_resource("TensorFVM", resource.resource_id)  # Now OK
```

## 10. Limitations & Future Work

1. **Persistence**: Current implementation is in-memory. Production deployment requires persistent storage (database).
2. **Cycle management**: Automatic cycle reset based on wall clock; production may need configurable cycle boundaries.
3. **Hierarchical permissions**: Current model is flat (tenant-level). Future: sub-tenant/project-level isolation.
4. **Real-time monitoring**: Quota usage dashboards and alerting not yet implemented.
5. **Distributed enforcement**: Current implementation is single-process. Multi-node deployment needs distributed coordination.

## 11. File Manifest

| File | Description |
|------|-------------|
| `src/tensorplatform/__init__.py` | Package init |
| `src/tensorplatform/tenant/__init__.py` | Tenant module init |
| `src/tensorplatform/tenant/registry.py` | Tenant metadata registry |
| `src/tensorplatform/tenant/quota.py` | Resource quota enforcement |
| `src/tensorplatform/tenant/permission.py` | Write permission + domain scanning |
| `src/tensorplatform/tenant/isolation.py` | Data isolation manager |
| `tests/test_tenant_isolation.py` | 52 test cases (C1-C7) |
| `docs/tenant_isolation_v1.md` | This document |
| `artifacts/tenant/scan_report.json` | Domain scan report |
| `artifacts/tenant/isolation_test_report.json` | Test results report |
