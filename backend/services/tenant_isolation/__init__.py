"""Tenant isolation mechanism (platform-generic, zero domain semantics).

Exports the facade plus the individual gates so call sites can depend on the
narrow piece they need.
"""
from .boundary import Grants, PathBoundary, PathDecision, SCOPE_READ
from .data_isolation import AccessResult, ArtifactSpace, EventStream
from .domain_gate import DomainTextGate, GateVerdict
from .engine import IsolationEngine
from .quota import BudgetLedger, ClaimDecision, GAUGE_RESOURCES
from .registry import RegistryError, TenantRegistry, TenantSpec
from .store import Store

__all__ = [
    "AccessResult",
    "ArtifactSpace",
    "BudgetLedger",
    "ClaimDecision",
    "DomainTextGate",
    "EventStream",
    "GAUGE_RESOURCES",
    "GateVerdict",
    "Grants",
    "IsolationEngine",
    "PathBoundary",
    "PathDecision",
    "RegistryError",
    "SCOPE_READ",
    "Store",
    "TenantRegistry",
    "TenantSpec",
]