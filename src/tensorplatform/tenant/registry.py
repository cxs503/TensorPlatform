"""
Tenant Registry — 12-repo tenant metadata registration.

Each tenant represents one repository in the Tensor ecosystem.
Metadata includes: name, repo, allowed_domains, resource_quota, data_classification.

IRON RULE: This module contains ZERO domain-specific terms.
Domain allowlists are data-driven via configuration.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import json
import time


@dataclass
class ResourceQuota:
    """Resource quota for a tenant per cycle."""
    cpu_core_hours: float = 100.0       # CPU core-hours per cycle
    memory_gb: float = 32.0             # Memory in GB
    storage_gb: float = 50.0            # Storage in GB
    max_tasks: int = 50                 # Max concurrent tasks
    max_artifacts: int = 200            # Max artifacts per cycle

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_core_hours": self.cpu_core_hours,
            "memory_gb": self.memory_gb,
            "storage_gb": self.storage_gb,
            "max_tasks": self.max_tasks,
            "max_artifacts": self.max_artifacts,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ResourceQuota":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TenantMeta:
    """Tenant metadata for one repository in the 12-repo ecosystem."""
    name: str                           # Human-readable tenant name
    repo: str                           # Repository identifier (slug)
    allowed_domains: List[str]          # List of allowed domain keys
    resource_quota: ResourceQuota       # Resource budget
    data_classification: str            # "public" | "internal" | "confidential"
    description: str = ""               # Optional description
    enabled: bool = True                # Whether tenant is active
    created_at: float = 0.0             # Registration timestamp
    metadata: Dict[str, Any] = field(default_factory=dict)  # Extensible metadata

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "repo": self.repo,
            "allowed_domains": self.allowed_domains,
            "resource_quota": self.resource_quota.to_dict(),
            "data_classification": self.data_classification,
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TenantMeta":
        quota_data = d.get("resource_quota", {})
        quota = ResourceQuota.from_dict(quota_data) if isinstance(quota_data, dict) else quota_data
        return cls(
            name=d["name"],
            repo=d["repo"],
            allowed_domains=d.get("allowed_domains", []),
            resource_quota=quota,
            data_classification=d.get("data_classification", "internal"),
            description=d.get("description", ""),
            enabled=d.get("enabled", True),
            created_at=d.get("created_at", 0.0),
            metadata=d.get("metadata", {}),
        )


# Default 12-repo tenant definitions
# Domain keys are abstract capability categories, NOT domain-specific terms.
# Each repo is assigned domains based on its computational role.
DEFAULT_TENANTS: List[Dict[str, Any]] = [
    {
        "name": "TensorLBM",
        "repo": "TensorLBM",
        "allowed_domains": ["lbm", "fluid", "multiphase"],
        "resource_quota": {"cpu_core_hours": 200, "memory_gb": 64, "storage_gb": 100, "max_tasks": 80, "max_artifacts": 300},
        "data_classification": "internal",
        "description": "Lattice Boltzmann Method solver library",
    },
    {
        "name": "TensorFVM",
        "repo": "TensorFVM",
        "allowed_domains": ["fvm", "fluid", "heat_transfer"],
        "resource_quota": {"cpu_core_hours": 200, "memory_gb": 64, "storage_gb": 100, "max_tasks": 80, "max_artifacts": 300},
        "data_classification": "internal",
        "description": "Finite Volume Method solver library",
    },
    {
        "name": "TensorFEM",
        "repo": "TensorFEM",
        "allowed_domains": ["fem", "structural", "thermal"],
        "resource_quota": {"cpu_core_hours": 200, "memory_gb": 64, "storage_gb": 100, "max_tasks": 80, "max_artifacts": 300},
        "data_classification": "internal",
        "description": "Finite Element Method solver library",
    },
    {
        "name": "TensorDEM",
        "repo": "TensorDEM",
        "allowed_domains": ["dem", "granular", "contact"],
        "resource_quota": {"cpu_core_hours": 150, "memory_gb": 48, "storage_gb": 80, "max_tasks": 60, "max_artifacts": 250},
        "data_classification": "internal",
        "description": "Discrete Element Method solver library",
    },
    {
        "name": "TensorBEM",
        "repo": "TensorBEM",
        "allowed_domains": ["bem", "potential_flow", "acoustic"],
        "resource_quota": {"cpu_core_hours": 150, "memory_gb": 48, "storage_gb": 80, "max_tasks": 60, "max_artifacts": 250},
        "data_classification": "internal",
        "description": "Boundary Element Method solver library",
    },
    {
        "name": "TensorSPH",
        "repo": "TensorSPH",
        "allowed_domains": ["sph", "fluid", "free_surface"],
        "resource_quota": {"cpu_core_hours": 200, "memory_gb": 64, "storage_gb": 100, "max_tasks": 80, "max_artifacts": 300},
        "data_classification": "internal",
        "description": "Smoothed Particle Hydrodynamics solver library",
    },
    {
        "name": "TensorDG",
        "repo": "TensorDG",
        "allowed_domains": ["dg", "hyperbolic", "wave"],
        "resource_quota": {"cpu_core_hours": 150, "memory_gb": 48, "storage_gb": 80, "max_tasks": 60, "max_artifacts": 250},
        "data_classification": "internal",
        "description": "Discontinuous Galerkin solver library",
    },
    {
        "name": "TensorCAD",
        "repo": "TensorCAD",
        "allowed_domains": ["cad", "geometry", "meshing"],
        "resource_quota": {"cpu_core_hours": 100, "memory_gb": 32, "storage_gb": 50, "max_tasks": 40, "max_artifacts": 200},
        "data_classification": "internal",
        "description": "Computer-Aided Design geometry tools",
    },
    {
        "name": "TensorPlatform",
        "repo": "TensorPlatform",
        "allowed_domains": ["platform", "infrastructure", "governance"],
        "resource_quota": {"cpu_core_hours": 300, "memory_gb": 96, "storage_gb": 200, "max_tasks": 100, "max_artifacts": 500},
        "data_classification": "internal",
        "description": "Agent runtime platform (task machine, campaigns, evidence, ledger)",
    },
    {
        "name": "TensorPhysics",
        "repo": "TensorPhysics",
        "allowed_domains": ["physics", "workflow", "verification"],
        "resource_quota": {"cpu_core_hours": 200, "memory_gb": 64, "storage_gb": 100, "max_tasks": 80, "max_artifacts": 300},
        "data_classification": "internal",
        "description": "Scientific computing workflow orchestration",
    },
    {
        "name": "TenantDomainRepo",
        "repo": "domain-repo-private",
        "allowed_domains": ["domain_hydro", "domain_propul", "domain_seak", "domain_manoeuv", "domain_struct", "domain_polar"],
        "resource_quota": {"cpu_core_hours": 400, "memory_gb": 128, "storage_gb": 300, "max_tasks": 120, "max_artifacts": 600},
        "data_classification": "confidential",
        "description": "Performance prediction platform (private domain repo)",
    },
    {
        "name": "TensorPapers",
        "repo": "TensorPapers",
        "allowed_domains": ["publication", "documentation", "review"],
        "resource_quota": {"cpu_core_hours": 50, "memory_gb": 16, "storage_gb": 30, "max_tasks": 20, "max_artifacts": 100},
        "data_classification": "confidential",
        "description": "Paper production pipeline (private repo)",
    },
]


class TenantRegistry:
    """
    Registry for the 12-repo tenant system.
    
    Provides:
    - Registration of tenant metadata
    - Lookup by repo name
    - Validation of tenant existence
    - Domain allowlist queries
    """

    def __init__(self):
        self._tenants: Dict[str, TenantMeta] = {}
        self._initialized = False

    def initialize_defaults(self) -> int:
        """Register all 12 default tenants. Returns count registered."""
        now = time.time()
        count = 0
        for tdef in DEFAULT_TENANTS:
            meta = TenantMeta.from_dict(tdef)
            meta.created_at = now
            self._tenants[meta.repo] = meta
            count += 1
        self._initialized = True
        return count

    def register(self, meta: TenantMeta) -> None:
        """Register a single tenant. Raises ValueError if repo already exists."""
        if meta.repo in self._tenants:
            raise ValueError(f"Tenant with repo '{meta.repo}' already registered")
        if not meta.name or not meta.repo:
            raise ValueError("Tenant must have non-empty name and repo")
        if not meta.allowed_domains:
            raise ValueError("Tenant must have at least one allowed domain")
        if meta.data_classification not in ("public", "internal", "confidential"):
            raise ValueError(f"Invalid data_classification: {meta.data_classification}")
        meta.created_at = meta.created_at or time.time()
        self._tenants[meta.repo] = meta

    def unregister(self, repo: str) -> bool:
        """Remove a tenant. Returns True if removed."""
        if repo in self._tenants:
            del self._tenants[repo]
            return True
        return False

    def get(self, repo: str) -> Optional[TenantMeta]:
        """Get tenant metadata by repo name."""
        return self._tenants.get(repo)

    def get_or_raise(self, repo: str) -> TenantMeta:
        """Get tenant metadata or raise KeyError."""
        meta = self._tenants.get(repo)
        if meta is None:
            raise KeyError(f"Tenant '{repo}' not registered")
        return meta

    def list_all(self) -> List[TenantMeta]:
        """List all registered tenants."""
        return list(self._tenants.values())

    def list_repos(self) -> List[str]:
        """List all registered repo names."""
        return list(self._tenants.keys())

    def count(self) -> int:
        """Return number of registered tenants."""
        return len(self._tenants)

    def is_registered(self, repo: str) -> bool:
        """Check if a repo is registered as a tenant."""
        return repo in self._tenants

    def get_allowed_domains(self, repo: str) -> List[str]:
        """Get allowed domain keys for a tenant."""
        meta = self.get_or_raise(repo)
        return meta.allowed_domains

    def is_domain_allowed(self, repo: str, domain: str) -> bool:
        """Check if a domain key is allowed for a tenant."""
        meta = self.get_or_raise(repo)
        return domain in meta.allowed_domains

    def to_dict(self) -> Dict[str, Any]:
        """Serialize registry to dict."""
        return {
            "tenant_count": self.count(),
            "tenants": {repo: meta.to_dict() for repo, meta in self._tenants.items()},
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize registry to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TenantRegistry":
        """Deserialize registry from dict."""
        reg = cls()
        for repo, tdata in data.get("tenants", {}).items():
            meta = TenantMeta.from_dict(tdata)
            reg._tenants[repo] = meta
        reg._initialized = True
        return reg
