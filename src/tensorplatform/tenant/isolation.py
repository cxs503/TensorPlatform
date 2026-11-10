"""
Isolation Manager — Per-tenant artifact/event/report isolation.

Enforces:
- Artifacts are stored per-tenant
- Events are scoped to tenant
- Reports are tenant-scoped
- Cross-tenant access requires explicit authorization
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple
from enum import Enum
import time
import json
import hashlib

from tensorplatform.tenant.registry import TenantRegistry


class UnauthorizedAccessError(Exception):
    """Raised when cross-tenant access is attempted without authorization."""
    def __init__(self, requesting_tenant: str, target_tenant: str, resource_type: str, resource_id: str):
        self.requesting_tenant = requesting_tenant
        self.target_tenant = target_tenant
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(
            f"Unauthorized access: tenant '{requesting_tenant}' cannot access "
            f"{resource_type} '{resource_id}' owned by tenant '{target_tenant}'"
        )


class ResourceTypes(str, Enum):
    """Types of isolatable resources."""
    ARTIFACT = "artifact"
    EVENT = "event"
    REPORT = "report"
    DATASET = "dataset"
    MODEL = "model"


@dataclass
class TenantResource:
    """A resource owned by a tenant."""
    resource_id: str
    resource_type: str
    owner_tenant: str
    name: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    size_bytes: int = 0
    content_hash: str = ""

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "owner_tenant": self.owner_tenant,
            "name": self.name,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
        }


@dataclass
class AccessGrant:
    """An authorization for cross-tenant access."""
    grant_id: str
    granting_tenant: str      # Tenant that owns the resource
    receiving_tenant: str     # Tenant granted access
    resource_type: str
    resource_id: str          # Specific resource or "*" for all
    access_level: str         # "read" | "write" | "admin"
    granted_at: float = 0.0
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.granted_at == 0.0:
            self.granted_at = time.time()

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "granting_tenant": self.granting_tenant,
            "receiving_tenant": self.receiving_tenant,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "access_level": self.access_level,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
        }


@dataclass
class AccessAuditRecord:
    """Audit record for an access attempt."""
    requesting_tenant: str
    target_tenant: str
    resource_type: str
    resource_id: str
    access_level: str
    allowed: bool
    reason: str
    grant_id: Optional[str] = None
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requesting_tenant": self.requesting_tenant,
            "target_tenant": self.target_tenant,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "access_level": self.access_level,
            "allowed": self.allowed,
            "reason": self.reason,
            "grant_id": self.grant_id,
            "timestamp": self.timestamp,
        }


class IsolationManager:
    """
    Manages data isolation between tenants.
    
    Provides:
    - Resource registration per tenant
    - Access control with explicit authorization
    - Cross-tenant access grants
    - Audit logging of all access attempts
    """

    def __init__(self, registry: TenantRegistry):
        """
        Initialize isolation manager.
        
        Args:
            registry: Tenant registry
        """
        self.registry = registry
        self._resources: Dict[str, TenantResource] = {}  # resource_id -> resource
        self._tenant_resources: Dict[str, Set[str]] = {}  # tenant -> set of resource_ids
        self._grants: Dict[str, AccessGrant] = {}  # grant_id -> grant
        self._audit_log: List[AccessAuditRecord] = []
        self._grant_counter = 0

    def _generate_grant_id(self) -> str:
        """Generate a unique grant ID."""
        self._grant_counter += 1
        return f"grant_{self._grant_counter:06d}"

    def _generate_resource_id(self, tenant: str, resource_type: str, name: str) -> str:
        """Generate a unique resource ID."""
        content = f"{tenant}:{resource_type}:{name}:{time.time()}"
        hash_suffix = hashlib.sha256(content.encode()).hexdigest()[:8]
        return f"{resource_type}_{tenant}_{hash_suffix}"

    def register_resource(
        self,
        tenant: str,
        resource_type: str,
        name: str,
        metadata: Optional[Dict[str, Any]] = None,
        size_bytes: int = 0,
        content: Optional[str] = None,
    ) -> TenantResource:
        """
        Register a new resource owned by a tenant.
        
        Args:
            tenant: Owner tenant repo name
            resource_type: Type of resource (artifact, event, report, etc.)
            name: Resource name
            metadata: Optional metadata
            size_bytes: Size in bytes
            content: Optional content for hashing
            
        Returns:
            The created resource
            
        Raises:
            KeyError: If tenant not registered
        """
        self.registry.get_or_raise(tenant)  # Validate tenant

        resource_id = self._generate_resource_id(tenant, resource_type, name)
        content_hash = ""
        if content:
            content_hash = hashlib.sha256(content.encode()).hexdigest()

        resource = TenantResource(
            resource_id=resource_id,
            resource_type=resource_type,
            owner_tenant=tenant,
            name=name,
            metadata=metadata or {},
            size_bytes=size_bytes,
            content_hash=content_hash,
        )

        self._resources[resource_id] = resource
        if tenant not in self._tenant_resources:
            self._tenant_resources[tenant] = set()
        self._tenant_resources[tenant].add(resource_id)

        return resource

    def get_resource(self, resource_id: str) -> Optional[TenantResource]:
        """Get a resource by ID."""
        return self._resources.get(resource_id)

    def list_tenant_resources(
        self,
        tenant: str,
        resource_type: Optional[str] = None,
    ) -> List[TenantResource]:
        """List resources owned by a tenant."""
        self.registry.get_or_raise(tenant)
        resource_ids = self._tenant_resources.get(tenant, set())
        resources = [self._resources[rid] for rid in resource_ids if rid in self._resources]

        if resource_type:
            resources = [r for r in resources if r.resource_type == resource_type]

        return resources

    def check_access(
        self,
        requesting_tenant: str,
        resource_id: str,
        access_level: str = "read",
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Check if a tenant can access a resource.
        
        Args:
            requesting_tenant: Tenant requesting access
            resource_id: Resource to access
            access_level: Required access level ("read", "write", "admin")
            
        Returns:
            Tuple of (allowed: bool, reason: str, grant_id: Optional[str])
        """
        resource = self._resources.get(resource_id)
        if resource is None:
            return False, f"Resource '{resource_id}' not found", None

        # Owner always has access
        if resource.owner_tenant == requesting_tenant:
            return True, "Owner access", None

        # Check for explicit grants
        access_hierarchy = {"read": 0, "write": 1, "admin": 2}
        required_level = access_hierarchy.get(access_level, 0)

        for grant in self._grants.values():
            if grant.is_expired():
                continue
            if grant.receiving_tenant != requesting_tenant:
                continue
            if grant.granting_tenant != resource.owner_tenant:
                continue
            # Check resource match (specific or wildcard)
            if grant.resource_id != "*" and grant.resource_id != resource_id:
                continue
            if grant.resource_type != resource.resource_type and grant.resource_id != "*":
                continue
            # Check access level
            grant_level = access_hierarchy.get(grant.access_level, 0)
            if grant_level >= required_level:
                return True, "Granted by explicit authorization", grant.grant_id

        return False, "No authorization for cross-tenant access", None

    def access_resource(
        self,
        requesting_tenant: str,
        resource_id: str,
        access_level: str = "read",
    ) -> TenantResource:
        """
        Access a resource with authorization check.
        
        Returns:
            The resource if access is allowed
            
        Raises:
            UnauthorizedAccessError: If access is denied
        """
        allowed, reason, grant_id = self.check_access(requesting_tenant, resource_id, access_level)

        resource = self._resources[resource_id]
        record = AccessAuditRecord(
            requesting_tenant=requesting_tenant,
            target_tenant=resource.owner_tenant,
            resource_type=resource.resource_type,
            resource_id=resource_id,
            access_level=access_level,
            allowed=allowed,
            reason=reason,
            grant_id=grant_id,
            timestamp=time.time(),
        )
        self._audit_log.append(record)

        if not allowed:
            raise UnauthorizedAccessError(
                requesting_tenant,
                resource.owner_tenant,
                resource.resource_type,
                resource_id,
            )

        return resource

    def grant_access(
        self,
        granting_tenant: str,
        receiving_tenant: str,
        resource_type: str,
        resource_id: str = "*",
        access_level: str = "read",
        expires_in_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AccessGrant:
        """
        Grant cross-tenant access to a resource.
        
        Args:
            granting_tenant: Tenant that owns the resource
            receiving_tenant: Tenant to grant access to
            resource_type: Type of resource
            resource_id: Specific resource ID or "*" for all
            access_level: Access level to grant
            expires_in_seconds: Optional expiration time
            metadata: Optional metadata
            
        Returns:
            The created grant
            
        Raises:
            KeyError: If either tenant not registered
        """
        self.registry.get_or_raise(granting_tenant)
        self.registry.get_or_raise(receiving_tenant)

        expires_at = None
        if expires_in_seconds is not None:
            expires_at = time.time() + expires_in_seconds

        grant = AccessGrant(
            grant_id=self._generate_grant_id(),
            granting_tenant=granting_tenant,
            receiving_tenant=receiving_tenant,
            resource_type=resource_type,
            resource_id=resource_id,
            access_level=access_level,
            expires_at=expires_at,
            metadata=metadata or {},
        )

        self._grants[grant.grant_id] = grant
        return grant

    def revoke_grant(self, grant_id: str) -> bool:
        """Revoke an access grant."""
        if grant_id in self._grants:
            del self._grants[grant_id]
            return True
        return False

    def list_grants(
        self,
        tenant: Optional[str] = None,
        active_only: bool = True,
    ) -> List[AccessGrant]:
        """List access grants."""
        grants = list(self._grants.values())

        if tenant:
            grants = [
                g for g in grants
                if g.granting_tenant == tenant or g.receiving_tenant == tenant
            ]

        if active_only:
            grants = [g for g in grants if not g.is_expired()]

        return grants

    def get_audit_log(
        self,
        tenant: Optional[str] = None,
        denied_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get access audit log."""
        records = self._audit_log

        if tenant:
            records = [
                r for r in records
                if r.requesting_tenant == tenant or r.target_tenant == tenant
            ]

        if denied_only:
            records = [r for r in records if not r.allowed]

        return [r.to_dict() for r in records[-limit:]]

    def get_isolation_summary(self) -> Dict[str, Any]:
        """Get isolation summary for all tenants."""
        summary = {}
        for repo in self.registry.list_repos():
            resources = self.list_tenant_resources(repo)
            grants = self.list_grants(repo)
            summary[repo] = {
                "resource_count": len(resources),
                "resources_by_type": {},
                "active_grants": len([g for g in grants if not g.is_expired()]),
                "total_grants": len(grants),
            }
            for r in resources:
                rtype = r.resource_type
                summary[repo]["resources_by_type"][rtype] = summary[repo]["resources_by_type"].get(rtype, 0) + 1

        return summary

    def to_dict(self) -> Dict[str, Any]:
        """Serialize isolation manager state."""
        return {
            "total_resources": len(self._resources),
            "total_grants": len(self._grants),
            "active_grants": len([g for g in self._grants.values() if not g.is_expired()]),
            "total_access_attempts": len(self._audit_log),
            "denied_attempts": len([r for r in self._audit_log if not r.allowed]),
            "isolation_summary": self.get_isolation_summary(),
        }


