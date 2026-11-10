"""Write/read path boundary: repo-scoped write authority with fail-closed reads.

Rules (mechanism level, tenant facts come from the injected registry):

* write  -> only inside the caller's own registered repo root. Traversal,
            symlink escape, shared roots and foreign repos are all denied;
            a read grant never upgrades to write authority.
* read   -> own repo or an explicitly configured shared root; a foreign repo
            needs a live grant (owner -> grantee, scope ``read``, prefix match).
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from .registry import TenantRegistry
from .store import Store, utc_now

SCOPE_READ = "read"


class PathDecision(NamedTuple):
    allowed: bool
    reason: str
    tenant: str
    target: str
    owner: Optional[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "tenant": self.tenant,
            "target": self.target,
            "owner": self.owner,
        }


@dataclass
class Grants:
    """Explicit cross-tenant authorization records with expiry."""

    store: Store

    def grant(
        self,
        owner: str,
        grantee: str,
        resource_prefix: str,
        scope: str = SCOPE_READ,
        ttl_seconds: float = 3600.0,
    ) -> str:
        gid = f"grant_{uuid.uuid4().hex[:12]}"
        self.store._exec(
            "INSERT INTO tenant_grant"
            " (grant_id, owner, grantee, scope, resource_prefix, expires_at, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                gid,
                owner,
                grantee,
                scope,
                resource_prefix,
                time.time() + float(ttl_seconds),
                utc_now(),
            ),
        )
        return gid

    def revoke(self, grant_id: str) -> None:
        self.store._exec("DELETE FROM tenant_grant WHERE grant_id=?", (grant_id,))

    def active(self, owner: str, grantee: str, scope: str) -> List[Dict[str, object]]:
        return self.store.query(
            "SELECT * FROM tenant_grant WHERE owner=? AND grantee=? AND scope=? AND expires_at>?",
            (owner, grantee, scope, time.time()),
        )

    def allows(
        self, owner: str, grantee: str, target: str, scope: str = SCOPE_READ
    ) -> bool:
        target_n = _norm(target)
        for row in self.active(owner, grantee, scope):
            prefix = _norm(str(row["resource_prefix"]))
            if not prefix:
                return True
            if target_n == prefix or target_n.startswith(prefix.rstrip(os.sep) + os.sep):
                return True
        return False


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


class PathBoundary:
    def __init__(
        self,
        registry: TenantRegistry,
        store: Store,
        workspace_root: str | os.PathLike,
        grants: Optional[Grants] = None,
    ):
        self.registry = registry
        self.store = store
        self.workspace_root = _norm(workspace_root)
        self.grants = grants or Grants(store)
        self._roots: Dict[str, str] = {}
        for spec in registry.tenants():
            self._roots[spec.name] = _norm(os.path.join(self.workspace_root, spec.repo))
        self._shared = [
            _norm(os.path.join(self.workspace_root, r))
            for r in registry.shared_read_roots()
        ]

    # -- path helpers ----------------------------------------------------
    def root_of(self, tenant: str) -> Optional[str]:
        return self._roots.get(tenant)

    def _candidate(self, path: str | os.PathLike) -> tuple[str, bool]:
        """Return (normalised absolute target, has_traversal_segment)."""
        raw = str(path)
        traversal = any(part == ".." for part in raw.replace("\\", "/").split("/"))
        p = Path(raw)
        abs_p = p if p.is_absolute() else Path(self.workspace_root) / p
        return _norm(abs_p), traversal

    def owner_of(self, target: str) -> Optional[str]:
        target_n = _norm(target)
        best: Optional[str] = None
        best_len = -1
        for tenant, root in self._roots.items():
            if target_n == root or target_n.startswith(root.rstrip(os.sep) + os.sep):
                if len(root) > best_len:
                    best, best_len = tenant, len(root)
        return best

    def _is_shared(self, target: str) -> bool:
        t = _norm(target)
        return any(t == s or t.startswith(s.rstrip(os.sep) + os.sep) for s in self._shared)

    def _log(self, tenant: str, action: str, target: str, decision: str, reason: str) -> None:
        self.store.log_access(tenant, action, target, decision, reason)

    # -- gates -----------------------------------------------------------
    def check_write(self, tenant: str, path: str | os.PathLike) -> PathDecision:
        target, traversal = self._candidate(path)
        spec = self.registry.get(tenant)
        if spec is None:
            return self._deny(tenant, "write", target, None, "unknown_tenant")
        if traversal:
            return self._deny(tenant, "write", target, self.owner_of(target), "path_traversal")
        owner = self.owner_of(target)
        if owner is None:
            reason = "shared_root_read_only" if self._is_shared(target) else "outside_tenant_namespace"
            return self._deny(tenant, "write", target, owner, reason)
        if owner != tenant:
            return self._deny(tenant, "write", target, owner, "cross_tenant_write")
        self._log(tenant, "write", target, "ACCEPT", "own_repo")
        return PathDecision(True, "own_repo", tenant, target, owner)

    def check_read(self, tenant: str, path: str | os.PathLike) -> PathDecision:
        target, traversal = self._candidate(path)
        if self.registry.get(tenant) is None:
            return self._deny(tenant, "read", target, None, "unknown_tenant")
        if traversal:
            return self._deny(tenant, "read", target, self.owner_of(target), "path_traversal")
        owner = self.owner_of(target)
        if owner == tenant:
            self._log(tenant, "read", target, "ACCEPT", "own_repo")
            return PathDecision(True, "own_repo", tenant, target, owner)
        if owner is None:
            if self._is_shared(target):
                self._log(tenant, "read", target, "ACCEPT", "shared_root")
                return PathDecision(True, "shared_root", tenant, target, None)
            return self._deny(tenant, "read", target, None, "outside_tenant_namespace")
        if self.grants.allows(owner, tenant, target, SCOPE_READ):
            self._log(tenant, "read", target, "ACCEPT", "explicit_grant")
            return PathDecision(True, "explicit_grant", tenant, target, owner)
        return self._deny(tenant, "read", target, owner, "cross_tenant_read_denied")

    def _deny(
        self, tenant: str, action: str, target: str, owner: Optional[str], reason: str
    ) -> PathDecision:
        self._log(tenant, action, target, "REJECT", reason)
        return PathDecision(False, reason, tenant, target, owner)