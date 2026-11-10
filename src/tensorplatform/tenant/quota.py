"""
Quota Manager — Resource budget enforcement per tenant per cycle.

Enforces:
- CPU core-hours
- Memory (GB)
- Storage (GB)
- Task count
- Artifact count

Claims that exceed budget are rejected with QuotaExceededError.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import time
import json

from tensorplatform.tenant.registry import TenantRegistry, ResourceQuota


class ResourceType(str, Enum):
    """Types of trackable resources."""
    CPU_CORE_HOURS = "cpu_core_hours"
    MEMORY_GB = "memory_gb"
    STORAGE_GB = "storage_gb"
    TASKS = "max_tasks"
    ARTIFACTS = "max_artifacts"


class QuotaExceededError(Exception):
    """Raised when a resource claim exceeds the tenant's quota."""
    def __init__(self, tenant_repo: str, resource: str, requested: float, limit: float, used: float):
        self.tenant_repo = tenant_repo
        self.resource = resource
        self.requested = requested
        self.limit = limit
        self.used = used
        self.remaining = max(0.0, limit - used)
        super().__init__(
            f"Quota exceeded for tenant '{tenant_repo}': "
            f"{resource} requested={requested}, used={used}, limit={limit}, remaining={self.remaining}"
        )


@dataclass
class ResourceUsage:
    """Current resource usage for a tenant in a cycle."""
    cpu_core_hours: float = 0.0
    memory_gb: float = 0.0
    storage_gb: float = 0.0
    tasks: int = 0
    artifacts: int = 0
    cycle_start: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_core_hours": self.cpu_core_hours,
            "memory_gb": self.memory_gb,
            "storage_gb": self.storage_gb,
            "tasks": self.tasks,
            "artifacts": self.artifacts,
            "cycle_start": self.cycle_start,
        }

    def get_usage(self, resource: str) -> float:
        """Get current usage for a resource type."""
        mapping = {
            "cpu_core_hours": self.cpu_core_hours,
            "memory_gb": self.memory_gb,
            "storage_gb": self.storage_gb,
            "max_tasks": float(self.tasks),
            "max_artifacts": float(self.artifacts),
        }
        return mapping.get(resource, 0.0)

    def add_usage(self, resource: str, amount: float) -> None:
        """Add usage for a resource type."""
        if resource == "cpu_core_hours":
            self.cpu_core_hours += amount
        elif resource == "memory_gb":
            self.memory_gb += amount
        elif resource == "storage_gb":
            self.storage_gb += amount
        elif resource == "max_tasks":
            self.tasks += int(amount)
        elif resource == "max_artifacts":
            self.artifacts += int(amount)

    def release_usage(self, resource: str, amount: float) -> None:
        """Release usage for a resource type (e.g., task completion)."""
        if resource == "cpu_core_hours":
            self.cpu_core_hours = max(0.0, self.cpu_core_hours - amount)
        elif resource == "memory_gb":
            self.memory_gb = max(0.0, self.memory_gb - amount)
        elif resource == "storage_gb":
            self.storage_gb = max(0.0, self.storage_gb - amount)
        elif resource == "max_tasks":
            self.tasks = max(0, self.tasks - int(amount))
        elif resource == "max_artifacts":
            self.artifacts = max(0, self.artifacts - int(amount))


@dataclass
class ClaimRecord:
    """Record of a resource claim (accepted or rejected)."""
    tenant_repo: str
    resource: str
    amount: float
    accepted: bool
    timestamp: float
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_repo": self.tenant_repo,
            "resource": self.resource,
            "amount": self.amount,
            "accepted": self.accepted,
            "timestamp": self.timestamp,
            "reason": self.reason,
        }


class QuotaManager:
    """
    Manages resource quotas for all tenants.
    
    Provides:
    - Claim resources (accepted or rejected based on budget)
    - Release resources (on task completion)
    - Query current usage vs. quota
    - Cycle management (reset usage per cycle)
    """

    def __init__(self, registry: TenantRegistry, cycle_duration_seconds: float = 3600.0):
        """
        Initialize quota manager.
        
        Args:
            registry: Tenant registry with quota definitions
            cycle_duration_seconds: Duration of one billing cycle in seconds
        """
        self.registry = registry
        self.cycle_duration = cycle_duration_seconds
        self._usage: Dict[str, ResourceUsage] = {}
        self._claim_log: List[ClaimRecord] = []

    def _get_usage(self, repo: str) -> ResourceUsage:
        """Get or create usage tracker for a tenant."""
        if repo not in self._usage:
            self._usage[repo] = ResourceUsage(cycle_start=time.time())
        return self._usage[repo]

    def _check_cycle_reset(self, repo: str) -> None:
        """Reset usage if cycle has expired."""
        usage = self._get_usage(repo)
        now = time.time()
        if now - usage.cycle_start >= self.cycle_duration:
            usage.cpu_core_hours = 0.0
            usage.memory_gb = 0.0
            usage.storage_gb = 0.0
            usage.tasks = 0
            usage.artifacts = 0
            usage.cycle_start = now

    def claim(self, repo: str, resource: str, amount: float, dry_run: bool = False) -> bool:
        """
        Claim resources for a tenant.
        
        Args:
            repo: Tenant repository name
            resource: Resource type (cpu_core_hours, memory_gb, storage_gb, max_tasks, max_artifacts)
            amount: Amount to claim
            dry_run: If True, check but don't actually consume
            
        Returns:
            True if claim accepted
            
        Raises:
            QuotaExceededError: If claim would exceed quota
            KeyError: If tenant not registered
        """
        meta = self.registry.get_or_raise(repo)
        self._check_cycle_reset(repo)
        usage = self._get_usage(repo)

        # Get quota limit
        quota_dict = meta.resource_quota.to_dict()
        limit = quota_dict.get(resource, 0.0)
        current_usage = usage.get_usage(resource)

        # Check if claim exceeds quota
        if current_usage + amount > limit:
            record = ClaimRecord(
                tenant_repo=repo,
                resource=resource,
                amount=amount,
                accepted=False,
                timestamp=time.time(),
                reason=f"Would exceed limit: {current_usage} + {amount} > {limit}",
            )
            self._claim_log.append(record)
            raise QuotaExceededError(repo, resource, amount, limit, current_usage)

        # Accept claim
        if not dry_run:
            usage.add_usage(resource, amount)

        record = ClaimRecord(
            tenant_repo=repo,
            resource=resource,
            amount=amount,
            accepted=True,
            timestamp=time.time(),
        )
        self._claim_log.append(record)
        return True

    def release(self, repo: str, resource: str, amount: float) -> None:
        """Release resources for a tenant (e.g., after task completion)."""
        if repo not in self.registry.list_repos():
            raise KeyError(f"Tenant '{repo}' not registered")
        usage = self._get_usage(repo)
        usage.release_usage(resource, amount)

    def get_usage(self, repo: str) -> Dict[str, Any]:
        """Get current usage for a tenant."""
        self.registry.get_or_raise(repo)  # Validate tenant exists
        self._check_cycle_reset(repo)
        usage = self._get_usage(repo)
        return usage.to_dict()

    def get_quota_status(self, repo: str) -> Dict[str, Any]:
        """Get quota status (usage vs. limit) for a tenant."""
        meta = self.registry.get_or_raise(repo)
        self._check_cycle_reset(repo)
        usage = self._get_usage(repo)
        quota_dict = meta.resource_quota.to_dict()

        status = {}
        for resource, limit in quota_dict.items():
            current = usage.get_usage(resource)
            status[resource] = {
                "used": current,
                "limit": limit,
                "remaining": max(0.0, limit - current),
                "utilization_pct": (current / limit * 100) if limit > 0 else 0.0,
            }
        return status

    def get_claim_log(self, repo: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get claim history, optionally filtered by tenant."""
        records = self._claim_log
        if repo:
            records = [r for r in records if r.tenant_repo == repo]
        return [r.to_dict() for r in records[-limit:]]

    def get_rejected_claims(self, repo: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all rejected claims."""
        records = [r for r in self._claim_log if not r.accepted]
        if repo:
            records = [r for r in records if r.tenant_repo == repo]
        return [r.to_dict() for r in records]

    def reset_cycle(self, repo: Optional[str] = None) -> None:
        """Reset usage for one or all tenants."""
        if repo:
            self.registry.get_or_raise(repo)
            usage = self._get_usage(repo)
            usage.cpu_core_hours = 0.0
            usage.memory_gb = 0.0
            usage.storage_gb = 0.0
            usage.tasks = 0
            usage.artifacts = 0
            usage.cycle_start = time.time()
        else:
            for r in self.registry.list_repos():
                self.reset_cycle(r)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize quota manager state."""
        return {
            "cycle_duration": self.cycle_duration,
            "tenants": {
                repo: {
                    "usage": self.get_usage(repo),
                    "status": self.get_quota_status(repo),
                }
                for repo in self.registry.list_repos()
            },
            "total_claims": len(self._claim_log),
            "rejected_claims": len([r for r in self._claim_log if not r.accepted]),
        }
