"""Campaign DAG self-healing & elastic node scheduling API.

Endpoints (all under the existing ``/tensor/campaigns`` prefix):

======================  ======  =============================================
path                    verb    purpose
======================  ======  =============================================
``/plans/{id}/selfheal/report``      POST  record one node outcome; run
                                             retry/backoff/bypass/degrade
``/plans/{id}/selfheal/step``        POST  one healing tick (due retries +
                                             saturation-aware scheduling)
``/plans/{id}/selfheal/state``       GET   nodes, policy, audit integrity
``/plans/{id}/selfheal/audit``       GET   hash-chained healing audit trail
``/plans/{id}/selfheal/policy``      GET/PUT  per-campaign self-heal policy
``/plans/{id}/selfheal/validate``    POST  acyclicity + critical path (C7)
======================  ======  =============================================

The router never executes campaign payloads: a degraded fallback result is
either supplied by the reporting worker (``degraded_result``) or by an
in-process executor bound by tests/tools.  That keeps the platform free of
domain semantics.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db
from services.selfheal import (
    CycleError,
    LoadSample,
    SelfHealEngine,
    SelfHealPolicy,
    UnknownDependency,
)
from services.selfheal import store
from services.selfheal.engine import _UNSET

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor/campaigns", tags=["战役自愈调度"])

_ENGINE_ROLES = ("ADMIN", "MANAGER", "ENGINEER")
_schema_ready = False


# ───────────────────────── models ─────────────────────────

class OutcomeReportIn(BaseModel):
    node_id: int
    success: bool
    result: Any = None
    error: str = ""
    #: Optional degraded-precision fallback computed by the worker itself.
    degraded_result: Any = None
    now: float | None = None


class LoadIn(BaseModel):
    cpu_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    mem_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    queue_len: int = Field(default=0, ge=0)
    queue_capacity: int = Field(default=64, ge=1)


class StepIn(BaseModel):
    now: float | None = None
    load: LoadIn | None = None


class RetryPolicyIn(BaseModel):
    max_retries: int = 3
    base_delay_s: float = 2.0
    factor: float = 2.0
    max_delay_s: float = 300.0
    jitter_ratio: float = 0.0


class ResourcePolicyIn(BaseModel):
    saturation_threshold: float = 0.8
    max_defer_s: float = 30.0
    queue_capacity: int = 64


class DegradationPolicyIn(BaseModel):
    enabled: bool = True
    max_deviation: float = 0.15


class PolicyIn(BaseModel):
    retry: RetryPolicyIn = Field(default_factory=RetryPolicyIn)
    resources: ResourcePolicyIn = Field(default_factory=ResourcePolicyIn)
    degradation: DegradationPolicyIn = Field(default_factory=DegradationPolicyIn)
    bypass_on_exhaust: bool = True


# ───────────────────────── helpers ─────────────────────────

def _ensure_schema(db: Session) -> None:
    global _schema_ready
    if _schema_ready:
        return
    store.ensure_selfheal_schema(db)
    _schema_ready = True


def _require_plan(db: Session, plan_id: int) -> dict[str, Any]:
    plan = store.load_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"campaign plan {plan_id} not found")
    return plan


def _load_engine(
    db: Session, plan_id: int
) -> tuple[SelfHealEngine, dict[str, Any]]:
    plan = _require_plan(db, plan_id)
    org_id = int(plan["organization_id"]) if plan.get("organization_id") else 0
    policy = store.load_policy(db, plan_id)
    dag = store.load_dag(db, plan_id)
    if not dag.nodes:
        raise HTTPException(
            status_code=400, detail=f"campaign plan {plan_id} has no nodes"
        )
    audit = store.load_audit(db, plan_id)
    try:
        engine = SelfHealEngine(dag, policy=policy, audit=audit)
    except CycleError as exc:
        store.append_plan_event(
            db, plan_id, org_id, "cycle_rejected", time.time(),
            cycle=exc.cycle, source="engine_bootstrap",
        )
        raise HTTPException(
            status_code=409,
            detail={"error": "cyclic_dependency", "cycle": exc.cycle},
        ) from exc
    except UnknownDependency as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "unknown_dependency", "missing": exc.missing,
                    "node_id": exc.node_id},
        ) from exc
    return engine, plan


def _persist(
    db: Session,
    plan_id: int,
    org_id: int,
    engine: SelfHealEngine,
    audit_start: int,
) -> int:
    store.save_dag(db, engine.dag)
    new_events = engine.audit.events[audit_start:]
    return store.persist_events(db, plan_id, org_id, new_events)


def _audit_brief(engine: SelfHealEngine) -> dict[str, Any]:
    return {
        "events": len(engine.audit),
        "heal_events": len(engine.audit.heal_events()),
        "integrity": engine.audit.verify(),
        "last_hash": engine.audit.last_hash,
    }


# ───────────────────────── endpoints ─────────────────────────

@router.post(
    "/plans/{plan_id}/selfheal/report",
    dependencies=[Depends(require_role(*_ENGINE_ROLES))],
)
def report_outcome(
    plan_id: int,
    body: OutcomeReportIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Record a node outcome and apply the healing policy to it."""
    _ensure_schema(db)
    engine, plan = _load_engine(db, plan_id)
    org_id = int(plan["organization_id"]) if plan.get("organization_id") else resolved_org(
        None, user, db
    )
    if body.node_id not in engine.dag.nodes:
        raise HTTPException(
            status_code=404, detail=f"node {body.node_id} not in plan {plan_id}"
        )
    start = len(engine.audit)
    if body.success:
        action = engine.report_success(
            body.node_id,
            result=body.result if body.result is not None else _UNSET,
            now=body.now,
        )
    else:
        degraded = (
            body.degraded_result
            if "degraded_result" in body.model_fields_set
            else _UNSET
        )
        action = engine.report_failure(
            body.node_id, error=body.error, now=body.now, degraded_result=degraded
        )
    persisted = _persist(db, plan_id, org_id, engine, start)
    return {
        "plan_id": plan_id,
        "action": action,
        "persisted_events": persisted,
        "audit": _audit_brief(engine),
    }


@router.post(
    "/plans/{plan_id}/selfheal/step",
    dependencies=[Depends(require_role(*_ENGINE_ROLES))],
)
def healing_step(
    plan_id: int,
    body: StepIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """One healing tick: unblock, dispatch due retries, elastic scheduling."""
    _ensure_schema(db)
    engine, plan = _load_engine(db, plan_id)
    org_id = int(plan["organization_id"]) if plan.get("organization_id") else resolved_org(
        None, user, db
    )
    load = LoadSample.from_dict(body.load.model_dump()) if body.load else LoadSample()
    start = len(engine.audit)
    result = engine.step(now=body.now, load=load)
    persisted = _persist(db, plan_id, org_id, engine, start)
    return {
        "plan_id": plan_id,
        "step": result,
        "persisted_events": persisted,
        "policy": engine.policy.to_dict(),
        "audit": _audit_brief(engine),
    }


@router.get("/plans/{plan_id}/selfheal/state", dependencies=[Depends(require_auth)])
def healing_state(plan_id: int, db: Session = Depends(get_db)):
    engine, _plan = _load_engine(db, plan_id)
    return {"plan_id": plan_id, **engine.state()}


@router.get("/plans/{plan_id}/selfheal/audit", dependencies=[Depends(require_auth)])
def healing_audit(
    plan_id: int,
    limit: int = Query(default=200, ge=1, le=2000),
    only_heal: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    _require_plan(db, plan_id)
    chain = store.load_audit(db, plan_id)
    selected = chain.heal_events() if only_heal else list(chain.events)
    return {
        "plan_id": plan_id,
        "total_events": len(chain),
        "heal_events": len(chain.heal_events()),
        "integrity": chain.verify(),
        "events": [ev.to_dict() for ev in selected[-limit:]],
    }


@router.get("/plans/{plan_id}/selfheal/policy", dependencies=[Depends(require_auth)])
def get_policy(plan_id: int, db: Session = Depends(get_db)):
    _require_plan(db, plan_id)
    policy = store.load_policy(db, plan_id)
    return {"plan_id": plan_id, "policy": policy.to_dict()}


@router.put(
    "/plans/{plan_id}/selfheal/policy",
    dependencies=[Depends(require_role(*_ENGINE_ROLES))],
)
def put_policy(
    plan_id: int,
    body: PolicyIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    _ensure_schema(db)
    plan = _require_plan(db, plan_id)
    try:
        policy = SelfHealPolicy.from_dict(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.save_policy(db, plan_id, policy)
    org_id = int(plan["organization_id"]) if plan.get("organization_id") else resolved_org(
        None, user, db
    )
    store.append_plan_event(
        db, plan_id, org_id, "policy_updated", time.time(), policy=policy.to_dict()
    )
    return {"plan_id": plan_id, "policy": policy.to_dict()}


@router.post(
    "/plans/{plan_id}/selfheal/validate",
    dependencies=[Depends(require_role(*_ENGINE_ROLES))],
)
def validate_dag(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Acyclicity / dangling-dependency check (acceptance C7)."""
    _ensure_schema(db)
    plan = _require_plan(db, plan_id)
    org_id = int(plan["organization_id"]) if plan.get("organization_id") else resolved_org(
        None, user, db
    )
    dag = store.load_dag(db, plan_id)
    try:
        order = dag.validate()
    except CycleError as exc:
        store.append_plan_event(
            db, plan_id, org_id, "cycle_rejected", time.time(),
            cycle=exc.cycle, source="validate_endpoint",
        )
        raise HTTPException(
            status_code=409,
            detail={"acyclic": False, "cycle": exc.cycle},
        ) from exc
    except UnknownDependency as exc:
        raise HTTPException(
            status_code=409,
            detail={"acyclic": False, "unknown_dependency": exc.missing,
                    "node_id": exc.node_id},
        ) from exc
    return {
        "plan_id": plan_id,
        "acyclic": True,
        "node_count": len(dag.nodes),
        "edge_count": len(dag.edges),
        "topological_order": order,
        "critical_path": dag.critical_path(),
    }


# Sentinel shared with the engine (avoid importing a private name).
def _unset():
    from services.selfheal.engine import _UNSET

    return _UNSET