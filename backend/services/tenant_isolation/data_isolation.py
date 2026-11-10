"""Data isolation: per-tenant artifact/report namespaces and marked event stream.

Isolation rules:

* storage layout is ``<root>/<tenant>/<relative>``; both traversal and absolute
  input are rejected, so a tenant cannot address another tenant's tree by path.
* reading a foreign artifact/report requires a live ``read`` grant; a shared
  listing never leaks foreign names.
* events are ``private`` by default; cross-tenant visibility needs the explicit
  ``shared`` mark, so an unmarked event is unreadable outside its tenant.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from .boundary import Grants, SCOPE_READ
from .store import Store, utc_now

class AccessResult(NamedTuple):
    allowed: bool
    reason: str
    tenant: str
    target: str
    owner: Optional[str] = None
    data: Optional[bytes] = None

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "allowed": self.allowed,
            "reason": self.reason,
            "tenant": self.tenant,
            "target": self.target,
            "owner": self.owner,
        }
        return out


def _split(rel: str) -> List[str]:
    return [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]


@dataclass
class ArtifactSpace:
    """Namespace-isolated artifact (or report) storage."""

    store: Store
    root: str | os.PathLike
    grants: Grants
    label: str = "artifact"

    def __post_init__(self) -> None:
        self.root_path = Path(self.root).resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    # -- resolution ------------------------------------------------------
    def _resolve(self, tenant: str, rel: str) -> tuple[Optional[Path], Optional[str], Optional[str]]:
        """Root-relative resolution: ``<owner>/<path>`` under the space root."""
        parts = _split(rel)
        if not parts:
            return None, None, "empty_resource"
        if Path(str(rel)).is_absolute() or any(p == ".." for p in parts):
            return None, parts[0], "path_traversal"
        target = (self.root_path / Path(*parts)).resolve()
        if not str(target).startswith(str(self.root_path) + os.sep):
            return None, parts[0], "path_traversal"
        return target, parts[0], None

    def owner_of(self, rel: str) -> Optional[str]:
        parts = _split(rel)
        return parts[0] if parts else None

    # -- operations ------------------------------------------------------
    def write(self, tenant: str, rel: str, data: bytes | str) -> AccessResult:
        target, owner, err = self._resolve(tenant, rel)
        if err:
            return self._deny(tenant, "write", rel, owner, err)
        if owner != tenant:
            return self._deny(tenant, "write", rel, owner, "cross_tenant_artifact_write")
        assert target is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = data.encode("utf-8") if isinstance(data, str) else data
        target.write_bytes(blob)
        self.store.log_access(tenant, f"{self.label}:write", str(target), "ACCEPT", "own_namespace")
        return AccessResult(True, "own_namespace", tenant, str(target), tenant)

    def read(self, tenant: str, rel: str) -> AccessResult:
        target, owner, err = self._resolve(tenant, rel)
        if err:
            return self._deny(tenant, "read", rel, owner, err)
        assert target is not None and owner is not None
        if owner != tenant:
            if not self.grants.allows(owner, tenant, str(target), SCOPE_READ):
                return self._deny(tenant, "read", rel, owner, "cross_tenant_artifact_denied")
            reason = "explicit_grant"
        else:
            reason = "own_namespace"
        if not target.is_file():
            return self._deny(tenant, "read", rel, owner, "not_found")
        self.store.log_access(tenant, f"{self.label}:read", str(target), "ACCEPT", reason)
        return AccessResult(True, reason, tenant, str(target), owner, target.read_bytes())

    def list_own(self, tenant: str) -> List[str]:
        base = (self.root_path / tenant).resolve()
        if not base.exists():
            return []
        out: List[str] = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out.append(str(Path(tenant) / p.relative_to(base)))
        return out

    def _deny(self, tenant: str, action: str, rel: str, owner: Optional[str], reason: str) -> AccessResult:
        self.store.log_access(tenant, f"{self.label}:{action}", str(rel), "REJECT", reason)
        return AccessResult(False, reason, tenant, str(rel), owner)


@dataclass
class EventStream:
    """Event log where cross-tenant visibility must be explicitly marked."""

    store: Store = field(default_factory=Store)

    def publish(
        self, tenant: str, kind: str, payload: Dict[str, Any], visibility: str = "private"
    ) -> str:
        if visibility not in ("private", "shared"):
            raise ValueError(f"unknown visibility {visibility!r}")
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        eid = hashlib.sha256(
            f"{tenant}|{kind}|{blob}|{utc_now()}|{os.urandom(4).hex()}".encode()
        ).hexdigest()[:20]
        self.store._exec(
            "INSERT INTO tenant_event (event_id, tenant, kind, visibility, payload, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (eid, tenant, kind, visibility, blob, utc_now()),
        )
        return eid

    def read(self, tenant: str, include_shared: bool = True) -> List[Dict[str, Any]]:
        rows = self.store.query(
            "SELECT * FROM tenant_event WHERE tenant=? ORDER BY created_at", (tenant,)
        )
        if include_shared:
            rows += self.store.query(
                "SELECT * FROM tenant_event WHERE tenant<>? AND visibility='shared'"
                " ORDER BY created_at",
                (tenant,),
            )
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            r["cross_tenant"] = r["tenant"] != tenant
        self.store.log_access(tenant, "event:read", "*", "ACCEPT", "filtered")
        return rows

    def read_event(self, tenant: str, event_id: str) -> AccessResult:
        rows = self.store.query("SELECT * FROM tenant_event WHERE event_id=?", (event_id,))
        if not rows:
            return self._deny(tenant, event_id, None, "not_found")
        row = rows[0]
        if row["tenant"] != tenant and row["visibility"] != "shared":
            return self._deny(tenant, event_id, row["tenant"], "cross_tenant_event_denied")
        self.store.log_access(tenant, "event:read_one", event_id, "ACCEPT", "own_or_shared")
        return AccessResult(True, "own_or_shared", tenant, event_id, row["tenant"])

    def _deny(self, tenant: str, target: str, owner: Optional[str], reason: str) -> AccessResult:
        self.store.log_access(tenant, "event:read_one", target, "REJECT", reason)
        return AccessResult(False, reason, tenant, target, owner)