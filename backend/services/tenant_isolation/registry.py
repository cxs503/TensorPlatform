"""Tenant registry: load, validate and publish the 12-repo tenant definitions.

Every tenant fact (repo binding, allowed domain tokens, data classification,
per-cycle resource quota) is read from an injected JSON document
(``config/tenant_registry.json`` by default). Validation is strict so a partial
registration cannot silently become a permissive one; registration also lands in
``feature_ledger`` so completeness is queryable (machine-checkable criterion C1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .store import Store

REQUIRED_TENANT_FIELDS = (
    "name",
    "repo",
    "allowed_domains",
    "data_classification",
    "resource_quota",
)
DEFAULT_RESOURCE_KEYS = (
    "cpu_core_hours",
    "memory_gb_hours",
    "storage_gb",
    "concurrent_tasks",
    "periodic_tasks",
)


class RegistryError(ValueError):
    """Raised when the injected registry document is incomplete/inconsistent."""


def default_registry_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / "config" / "tenant_registry.json"


@dataclass(frozen=True)
class TenantSpec:
    name: str
    repo: str
    allowed_domains: Sequence[str]
    data_classification: str
    resource_quota: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "repo": self.repo,
            "allowed_domains": list(self.allowed_domains),
            "data_classification": self.data_classification,
            "resource_quota": dict(self.resource_quota),
        }


class TenantRegistry:
    """Injected tenant metadata + completeness validation."""

    def __init__(
        self,
        doc: Dict[str, Any],
        store: Optional[Store] = None,
    ):
        self.doc = doc
        self.store = store
        self.policy: Dict[str, Any] = dict(doc.get("policy") or {})
        self.resource_keys: List[str] = list(
            doc.get("resource_keys") or DEFAULT_RESOURCE_KEYS
        )
        self.classifications: List[str] = list(
            doc.get("data_classifications") or ["public", "internal", "restricted"]
        )
        self._tenants: Dict[str, TenantSpec] = {}
        self._load(doc.get("tenants") or [])

    # -- construction ----------------------------------------------------
    @classmethod
    def from_file(
        cls, path: str | Path | None = None, store: Optional[Store] = None
    ) -> "TenantRegistry":
        p = Path(path) if path is not None else default_registry_path()
        with open(p, encoding="utf-8") as fh:
            return cls(json.load(fh), store=store)

    def _load(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            raise RegistryError("registry document carries no tenants")
        repos: set[str] = set()
        for row in rows:
            missing = [f for f in REQUIRED_TENANT_FIELDS if f not in row]
            if missing:
                raise RegistryError(f"tenant {row.get('name')!r} missing {missing}")
            name = str(row["name"]).strip()
            repo = str(row["repo"]).strip()
            if not name or not repo:
                raise RegistryError("tenant name/repo must be non-empty")
            if name in self._tenants:
                raise RegistryError(f"duplicate tenant {name!r}")
            if repo in repos:
                raise RegistryError(f"duplicate repo binding {repo!r}")
            repos.add(repo)
            domains = row["allowed_domains"]
            if not isinstance(domains, list) or not domains:
                raise RegistryError(f"tenant {name!r} has empty allowed_domains")
            classification = row["data_classification"]
            if classification not in self.classifications:
                raise RegistryError(
                    f"tenant {name!r} data_classification {classification!r} not allowed"
                )
            quota = row["resource_quota"]
            if not isinstance(quota, dict):
                raise RegistryError(f"tenant {name!r} resource_quota must be a mapping")
            missing_q = [k for k in self.resource_keys if k not in quota]
            if missing_q:
                raise RegistryError(
                    f"tenant {name!r} resource_quota missing {missing_q}"
                )
            clean_quota: Dict[str, float] = {}
            for k in self.resource_keys:
                try:
                    val = float(quota[k])
                except (TypeError, ValueError) as exc:
                    raise RegistryError(
                        f"tenant {name!r} quota {k!r} not numeric"
                    ) from exc
                if val < 0:
                    raise RegistryError(f"tenant {name!r} quota {k!r} negative")
                clean_quota[k] = val
            self._tenants[name] = TenantSpec(
                name=name,
                repo=repo,
                allowed_domains=tuple(str(d) for d in domains),
                data_classification=classification,
                resource_quota=clean_quota,
            )

    # -- queries ---------------------------------------------------------
    def tenants(self) -> List[TenantSpec]:
        return list(self._tenants.values())

    def get(self, name: str) -> Optional[TenantSpec]:
        return self._tenants.get(name)

    def require(self, name: str) -> TenantSpec:
        spec = self._tenants.get(name)
        if spec is None:
            raise KeyError(f"unregistered tenant {name!r}")
        return spec

    def repo_of(self, tenant: str) -> Optional[str]:
        spec = self.get(tenant)
        return spec.repo if spec else None

    def tenant_by_repo(self, repo: str) -> Optional[TenantSpec]:
        for spec in self._tenants.values():
            if spec.repo == repo:
                return spec
        return None

    def domain_allowed(self, tenant: str, token: str) -> bool:
        spec = self.get(tenant)
        return bool(spec and token in spec.allowed_domains)

    def shared_read_roots(self) -> List[str]:
        return list(self.policy.get("shared_read_roots") or [])

    def completeness(self) -> Dict[str, Any]:
        """Machine-checkable registration completeness (criterion C1)."""
        rows = []
        for spec in self._tenants.values():
            rows.append(
                {
                    "name": spec.name,
                    "repo": spec.repo,
                    "fields_present": all(
                        getattr(spec, f) not in (None, "", [], {})
                        for f in REQUIRED_TENANT_FIELDS
                    ),
                    "quota_keys": sorted(spec.resource_quota),
                    "quota_complete": all(
                        k in spec.resource_quota for k in self.resource_keys
                    ),
                    "data_classification": spec.data_classification,
                }
            )
        return {
            "tenant_count": len(rows),
            "resource_keys": list(self.resource_keys),
            "rows": rows,
            "complete": all(r["fields_present"] and r["quota_complete"] for r in rows),
        }

    def register(self) -> List[str]:
        """Publish every tenant definition into feature_ledger; return ledger ids."""
        if self.store is None:
            raise RegistryError("registry has no store to publish into")
        ids: List[str] = []
        for spec in self._tenants.values():
            ids.append(self.store.ledger_append(spec.name, "tenant_registration", spec.to_dict()))
        return ids