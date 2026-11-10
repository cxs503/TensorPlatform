"""Per-tenant, per-cycle budget ledger with fail-closed ``claim`` semantics.

``claim`` is the single admission point for tenant resource consumption: a claim
either fits the injected quota (accept + charge) or is rejected with a machine
readable reason. Rejections are persisted in ``tenant_claim`` and mirrored into
``access_log`` so the rejection rate is auditable, not self-reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional

from .registry import TenantRegistry
from .store import Store, utc_now

# Resources that carry an in-flight gauge (charge on claim, release on finish).
GAUGE_RESOURCES = ("concurrent_tasks", "periodic_tasks")
EPS = 1e-9


class ClaimDecision(NamedTuple):
    accepted: bool
    reason: str
    tenant: str
    resource: str
    amount: float
    cycle: str
    used_after: float
    limit: float

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.used_after)

    def to_dict(self) -> Dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "tenant": self.tenant,
            "resource": self.resource,
            "amount": self.amount,
            "cycle": self.cycle,
            "used_after": self.used_after,
            "limit": self.limit,
            "remaining": self.remaining,
        }


@dataclass
class BudgetLedger:
    store: Store
    registry: TenantRegistry

    # -- cycle bookkeeping -----------------------------------------------
    def ensure_cycle(self, cycle: str) -> int:
        """Seed ``tenant_budget`` rows for a cycle from the injected quotas."""
        n = 0
        for spec in self.registry.tenants():
            for key in self.registry.resource_keys:
                limit = float(spec.resource_quota[key])
                cur = self.store.query(
                    "SELECT 1 FROM tenant_budget WHERE tenant=? AND cycle=? AND resource=?",
                    (spec.name, cycle, key),
                )
                if cur:
                    continue
                self.store._exec(
                    "INSERT INTO tenant_budget (tenant, cycle, resource, quota_limit, used)"
                    " VALUES (?,?,?,?,0.0)",
                    (spec.name, cycle, key, limit),
                )
                n += 1
        return n

    def limit_of(self, tenant: str, resource: str, cycle: str) -> float:
        rows = self.store.query(
            "SELECT quota_limit FROM tenant_budget WHERE tenant=? AND cycle=? AND resource=?",
            (tenant, cycle, resource),
        )
        if rows:
            return float(rows[0]["quota_limit"])
        spec = self.registry.get(tenant)
        if spec and resource in spec.resource_quota:
            return float(spec.resource_quota[resource])
        return 0.0

    def used_of(self, tenant: str, resource: str, cycle: str) -> float:
        rows = self.store.query(
            "SELECT used FROM tenant_budget WHERE tenant=? AND cycle=? AND resource=?",
            (tenant, cycle, resource),
        )
        return float(rows[0]["used"]) if rows else 0.0

    # -- admission -------------------------------------------------------
    def claim(
        self,
        tenant: str,
        resource: str,
        amount: float,
        cycle: str = "current",
    ) -> ClaimDecision:
        spec = self.registry.get(tenant)
        limit = self.limit_of(tenant, resource, cycle) if spec else 0.0
        used = self.used_of(tenant, resource, cycle) if spec else 0.0

        def reject(reason: str) -> ClaimDecision:
            self._record(tenant, resource, amount, cycle, "REJECT", reason)
            return ClaimDecision(False, reason, tenant, resource, float(amount or 0.0), cycle, used, limit)

        if spec is None:
            return reject("unknown_tenant")
        if resource not in self.registry.resource_keys:
            return reject("unknown_resource")
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            return reject("invalid_amount")
        if amt != amt or amt <= 0:  # NaN or non-positive
            return reject("invalid_amount")
        if resource in GAUGE_RESOURCES and amt != int(amt):
            return reject("fractional_gauge")

        self.ensure_cycle(cycle)
        limit = self.limit_of(tenant, resource, cycle)
        used = self.used_of(tenant, resource, cycle)
        if used + amt > limit + EPS:
            return reject("quota_exceeded")
        new_used = used + amt
        self.store._exec(
            "UPDATE tenant_budget SET used=? WHERE tenant=? AND cycle=? AND resource=?",
            (new_used, tenant, cycle, resource),
        )
        self._record(tenant, resource, amt, cycle, "ACCEPT", "within_quota")
        return ClaimDecision(True, "within_quota", tenant, resource, amt, cycle, new_used, limit)

    def release(self, tenant: str, resource: str, amount: float, cycle: str = "current") -> float:
        """Return gauge budget (or unused reservation) back to the cycle."""
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            return self.used_of(tenant, resource, cycle)
        if amt <= 0:
            return self.used_of(tenant, resource, cycle)
        used = self.used_of(tenant, resource, cycle)
        new_used = max(0.0, used - amt)
        self.store._exec(
            "UPDATE tenant_budget SET used=? WHERE tenant=? AND cycle=? AND resource=?",
            (new_used, tenant, cycle, resource),
        )
        return new_used

    # -- reporting -------------------------------------------------------
    def snapshot(self, tenant: str, cycle: str = "current") -> Dict[str, object]:
        spec = self.registry.require(tenant)
        rows = []
        for key in self.registry.resource_keys:
            rows.append(
                {
                    "resource": key,
                    "used": self.used_of(tenant, key, cycle),
                    "limit": self.limit_of(tenant, key, cycle),
                    "quota": float(spec.resource_quota[key]),
                }
            )
        return {"tenant": tenant, "cycle": cycle, "resources": rows}

    def claims(self, tenant: Optional[str] = None) -> List[Dict[str, object]]:
        if tenant is None:
            return self.store.query("SELECT * FROM tenant_claim ORDER BY claim_id")
        return self.store.query(
            "SELECT * FROM tenant_claim WHERE tenant=? ORDER BY claim_id", (tenant,)
        )

    def rejection_rate(self) -> Dict[str, object]:
        rows = self.store.query(
            "SELECT decision, COUNT(*) AS n FROM tenant_claim GROUP BY decision"
        )
        total = sum(int(r["n"]) for r in rows)
        rejected = sum(int(r["n"]) for r in rows if r["decision"] == "REJECT")
        return {
            "total": total,
            "rejected": rejected,
            "accepted": total - rejected,
            "rejection_rate": (rejected / total) if total else 0.0,
        }

    def _record(
        self, tenant: str, resource: str, amount: float, cycle: str, decision: str, reason: str
    ) -> None:
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            amt = 0.0
        if amt != amt:  # NaN cannot be stored as NOT NULL REAL by sqlite
            amt = 0.0
        self.store._exec(
            "INSERT INTO tenant_claim (tenant, cycle, resource, amount, decision, reason, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (tenant, cycle, resource, amt, decision, reason, utc_now()),
        )
        self.store.log_access(tenant, f"claim:{resource}", f"cycle:{cycle}", decision, reason)