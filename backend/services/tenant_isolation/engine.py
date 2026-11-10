"""Facade wiring the tenant isolation mechanism together (single entry point).

Consumers (API layer, campaign nodes, validation CLI) instantiate one engine and
get: registry + feature-ledger publication, budget admission, path boundary,
artifact/report namespaces, marked event stream and the pre-submission domain
gate -- all backed by one store so decisions can be replayed from ``access_log``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .boundary import Grants, PathBoundary
from .data_isolation import ArtifactSpace, EventStream
from .domain_gate import DomainTextGate
from .quota import BudgetLedger
from .registry import TenantRegistry
from .store import Store


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass
class IsolationEngine:
    workspace_root: str | os.PathLike = field(default_factory=_root)
    db_path: str | os.PathLike | None = None
    registry_path: str | os.PathLike | None = None
    publish_registry: bool = True

    def __post_init__(self) -> None:
        self.store = Store(self.db_path)
        self.registry: TenantRegistry = TenantRegistry.from_file(self.registry_path, store=self.store)
        if self.publish_registry:
            self.registry.register()
        self.grants = Grants(self.store)
        self.budget = BudgetLedger(self.store, self.registry)
        self.paths = PathBoundary(
            self.registry, self.store, self.workspace_root, grants=self.grants
        )
        policy = self.registry.policy
        self.artifacts = ArtifactSpace(
            self.store,
            Path(self.workspace_root) / policy.get("artifact_root", "artifacts/tenants"),
            self.grants,
            label="artifact",
        )
        self.reports = ArtifactSpace(
            self.store,
            Path(self.workspace_root) / policy.get("report_root", "tensor_reports/tenants"),
            self.grants,
            label="report",
        )
        self.events = EventStream(self.store)

    # -- domain gate is opt-in (loads the shared vocabulary data) ---------
    def domain_gate(self, mode: str = "enforce") -> DomainTextGate:
        return DomainTextGate.from_config(mode=mode)

    # -- convenience ----------------------------------------------------
    def tenant_names(self) -> List[str]:
        return [t.name for t in self.registry.tenants()]

    def self_check(self, cycle: str = "self-check") -> Dict[str, Any]:
        """Non-mutating-ish smoke check used by CLI/health surfaces."""
        names = self.tenant_names()
        budget_ok = False
        if names:
            d = self.budget.claim(names[0], "cpu_core_hours", 1.0, cycle=cycle)
            budget_ok = d.accepted
            self.budget.release(names[0], "cpu_core_hours", 1.0, cycle=cycle)
        return {
            "tenant_count": len(names),
            "registry_complete": self.registry.completeness()["complete"],
            "budget_admission": budget_ok,
            "ledger_entries": len(self.store.ledger_query()),
            "domain_gate_available": True,
        }