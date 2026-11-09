"""Campaign Routing API — conditional DAG branching & adaptive research loops.

Implements:
  (a) Condition edge model: condition JSONB on campaign_nodes
  (b) Gate router: evaluates conditions when node status → done/failed
  (c) Adaptive loop: on_false → self (with parameter modification)
  (d) Convergence detection: max_iterations guard with escalation
  (e) API endpoints: POST/GET condition edge management

Evidence: all routing evaluations logged to campaign_events.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import require_auth, require_role
from api.domain.tensor_common import resolved_org
from database.config import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tensor/campaigns", tags=["战役条件路由"])

# ───────────────────────── Concurrency guard ─────────────────────────
# Per-plan advisory lock to prevent race conditions in concurrent routing
_routing_locks: dict[int, threading.Lock] = {}
_routing_locks_guard = threading.Lock()


def _get_plan_lock(plan_id: int) -> threading.Lock:
    """Get or create a per-plan lock for routing evaluation."""
    with _routing_locks_guard:
        if plan_id not in _routing_locks:
            _routing_locks[plan_id] = threading.Lock()
        return _routing_locks[plan_id]


# ───────────────────────── Helpers ─────────────────────────

def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _insert_returning_id(db: Session, sql_insert: str, params: dict) -> int:
    """Insert a row and return its primary key.

    ``SELECT last_insert_rowid()`` is connection-scoped and therefore wrong as
    soon as the session's connection is reused (observed: an edge created as
    row 3 reported id 50). PostgreSQL gets ``RETURNING id``; SQLite/others use
    the cursor's ``lastrowid`` from the very statement that inserted the row.
    """
    if db.get_bind().dialect.name == "postgresql":
        result = db.execute(text(f"{sql_insert} RETURNING id"), params)
        return int(result.scalar())
    result = db.execute(text(sql_insert), params)
    return int(result.lastrowid)


def _row_to_dict(r: Any) -> dict:
    out = {}
    for k, v in dict(r).items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, str):
            # Try to parse JSON fields
            parsed = _loads(v)
            if parsed is not None and isinstance(parsed, (dict, list)):
                out[k] = parsed
            else:
                out[k] = v
        else:
            out[k] = v
    return out


# ───────────────────────── Dialect shims ─────────────────────────
# The routing layer bootstraps its own storage on first use so that an agent
# can enable conditional routing on a database whose migrations have not been
# applied yet. That bootstrap must speak the dialect of the *platform*
# database (PostgreSQL in production, SQLite in the embedded/test runtime):
# emitting SQLite-only DDL (PRAGMA / AUTOINCREMENT / datetime('now')) on
# PostgreSQL aborts the transaction and takes every routing endpoint down.

_ROUTING_EVENT_TYPES = (
    "edge_condition_created",
    "edge_condition_deleted",
    "routing_evaluated",
    "retry_node_created",
)


def _dialect_name(db: Session) -> str:
    try:
        bind = db.get_bind()
    except Exception:  # pragma: no cover - defensive
        return "sqlite"
    return getattr(getattr(bind, "dialect", None), "name", "sqlite")


def _is_pg(db: Session) -> bool:
    return _dialect_name(db) == "postgresql"


def _json_sql(db: Session, placeholder: str) -> str:
    """Bind a JSON *string* into a JSON/JSONB column of the current dialect."""
    return f"CAST({placeholder} AS jsonb)" if _is_pg(db) else placeholder


def _json_value(value: Any) -> Any:
    """Normalise a (possibly already-parsed) JSON value for binding."""
    if value is None or isinstance(value, str):
        return value
    return _dumps(value)


def _ensure_routing_columns(db: Session) -> None:
    """Ensure routing columns exist on campaign_nodes (dialect-aware)."""
    if _is_pg(db):
        db.execute(text("""
            ALTER TABLE campaign_nodes
                ADD COLUMN IF NOT EXISTS condition JSONB,
                ADD COLUMN IF NOT EXISTS convergence_check JSONB,
                ADD COLUMN IF NOT EXISTS iteration_count INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS parent_node_id BIGINT
                    REFERENCES campaign_nodes(id)
        """))
        db.commit()
        return

    cols = db.execute(text("PRAGMA table_info(campaign_nodes)")).fetchall()
    col_names = {row[1] for row in cols}
    if "condition" not in col_names:
        db.execute(text("ALTER TABLE campaign_nodes ADD COLUMN condition TEXT"))
    if "convergence_check" not in col_names:
        db.execute(text("ALTER TABLE campaign_nodes ADD COLUMN convergence_check TEXT"))
    if "iteration_count" not in col_names:
        db.execute(text("ALTER TABLE campaign_nodes ADD COLUMN iteration_count INTEGER NOT NULL DEFAULT 0"))
    if "parent_node_id" not in col_names:
        db.execute(text("ALTER TABLE campaign_nodes ADD COLUMN parent_node_id INTEGER"))
    db.commit()


def _ensure_edge_conditions_table(db: Session) -> None:
    """Ensure campaign_edge_conditions table exists (dialect-aware)."""
    if _is_pg(db):
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS campaign_edge_conditions (
                id BIGSERIAL PRIMARY KEY,
                plan_id BIGINT NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
                source_node_id BIGINT NOT NULL REFERENCES campaign_nodes(id) ON DELETE CASCADE,
                condition_expr JSONB NOT NULL DEFAULT '{}'::jsonb,
                priority INTEGER NOT NULL DEFAULT 0,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(plan_id, source_node_id, priority)
            )
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_campaign_edge_conditions_plan
                ON campaign_edge_conditions (plan_id)
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_campaign_edge_conditions_source
                ON campaign_edge_conditions (source_node_id, enabled)
        """))
        db.commit()
        return

    db.execute(text("""
        CREATE TABLE IF NOT EXISTS campaign_edge_conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES campaign_plans(id) ON DELETE CASCADE,
            source_node_id INTEGER NOT NULL REFERENCES campaign_nodes(id) ON DELETE CASCADE,
            condition_expr TEXT NOT NULL DEFAULT '{}',
            priority INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(plan_id, source_node_id, priority)
        )
    """))
    db.commit()


_EVENT_TYPE_CONSTRAINT_SQL = """
    SELECT c.conname AS conname, pg_get_constraintdef(c.oid) AS cdef
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'campaign_events'
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%event_type%'
"""


def _ensure_event_types(db: Session, required: tuple[str, ...] = _ROUTING_EVENT_TYPES) -> None:
    """Widen the campaign_events.event_type CHECK to accept routing events.

    Production ``campaign_events`` carries a CHECK whitelist (migration 095)
    that predates conditional routing, so inserting ``routing_evaluated`` /
    ``edge_condition_created`` would violate it and abort the routing
    transaction. PostgreSQL cannot ALTER a CHECK in place, so the constraint is
    re-created with the union of its existing literals and the required types.
    Idempotent: nothing is touched once every required type is whitelisted.
    """
    if not _is_pg(db):
        return  # SQLite schema used by tests/embedded runtime has no whitelist
    try:
        rows = db.execute(text(_EVENT_TYPE_CONSTRAINT_SQL)).mappings().all()
    except Exception:  # pragma: no cover - table absent / no permission
        logger.warning("campaign_events event_type introspection failed", exc_info=True)
        return

    wanted = set(required)
    for row in rows:
        existing = set(re.findall(r"'([^']*)'", row["cdef"] or ""))
        if wanted <= existing:
            continue
        allowed = sorted(existing | wanted)
        values = ", ".join(f"'{v}'" for v in allowed)
        db.execute(text(f'ALTER TABLE campaign_events DROP CONSTRAINT "{row["conname"]}"'))
        db.execute(text(
            "ALTER TABLE campaign_events "
            "ADD CONSTRAINT campaign_events_event_type_check "
            f"CHECK (event_type IN ({values}))"
        ))
    db.commit()


def _log_routing_event(
    db: Session,
    org_id: int,
    plan_id: int,
    event_type: str,
    detail: dict,
) -> None:
    """Log a routing evaluation event to campaign_events (audit trail)."""
    try:
        _ensure_event_types(db, (event_type,))
    except Exception:  # pragma: no cover - audit must not be blocked by DDL
        logger.warning("event type whitelist widening failed", exc_info=True)

    detail_sql = _json_sql(db, ":detail")
    db.execute(
        text(f"""
            INSERT INTO campaign_events (organization_id, plan_id, event_type, detail, created_at)
            VALUES (:org, :pid, :etype, {detail_sql}, :ts)
        """),
        {
            "org": org_id,
            "pid": plan_id,
            "etype": event_type,
            "detail": _dumps(detail),
            "ts": datetime.now(timezone.utc),
        },
    )
    db.commit()


# ───────────────────────── Pydantic schemas ─────────────────────────

class ConditionExpr(BaseModel):
    """Condition expression for gate routing."""
    type: str = Field(default="gate", pattern="^(gate|convergence)$")
    metric: str = Field(min_length=1, max_length=100)
    operator: str = Field(pattern="^(<|<=|>|>=|==|!=)$")
    threshold: float
    on_true: int = Field(ge=1, description="Node ID to activate when condition is true")
    on_false: int = Field(ge=1, description="Node ID to activate when condition is false")
    auto_retry: bool = Field(
        default=False,
        description="When on_false loops back to this node, materialise the retry "
                    "node (grid refinement 2x) automatically on the failed round",
    )

    @validator("on_false")
    def validate_on_false(cls, v, values):
        # on_false can equal on_true (equivalent to unconditional) — allowed
        return v


class EdgeConditionCreate(BaseModel):
    plan_id: int = Field(ge=1)
    source_node_id: int = Field(ge=1)
    condition: ConditionExpr
    priority: int = Field(default=0, ge=0)
    enabled: bool = True


class EdgeConditionResponse(BaseModel):
    id: int
    plan_id: int
    source_node_id: int
    condition: dict
    priority: int
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None


class RoutingEvaluationRequest(BaseModel):
    """Request to evaluate routing for a completed node."""
    node_id: int
    metric_value: float
    metric_name: str = "max_rel_err"


class RoutingEvaluationResponse(BaseModel):
    evaluated: bool
    condition_met: bool | None = None
    activated_node_id: int | None = None
    action: str  # "activated", "loop_retry", "escalated", "no_condition", "skipped"
    detail: dict = Field(default_factory=dict)


class ConvergenceCheckConfig(BaseModel):
    """Convergence detection configuration for adaptive loops."""
    metric: str = Field(min_length=1)
    target: float
    max_iterations: int = Field(ge=1, le=100)
    action_on_exhaust: str = Field(
        default="escalate",
        pattern="^(escalate|fail|warn)$"
    )


class NodeConditionUpdate(BaseModel):
    """Update condition and convergence check on a node."""
    condition: ConditionExpr | None = None
    convergence_check: ConvergenceCheckConfig | None = None


class PlanEdgeCreateRequest(BaseModel):
    """Body of POST /plans/{plan_id}/edges (plan id comes from the path)."""
    source_node_id: int = Field(ge=1)
    condition: ConditionExpr
    priority: int = Field(default=0, ge=0)
    enabled: bool = True


class NodeEvaluateRequest(BaseModel):
    """Body of POST /nodes/{node_id}/evaluate (node id comes from the path)."""
    metric_value: float
    metric_name: str = Field(default="max_rel_err", min_length=1, max_length=100)


class RetryNodeRequest(BaseModel):
    """Optional body of POST /nodes/{node_id}/create-retry.

    Adaptive loop parameter adjustment: the retry node inherits the original
    node's parameters with the grid refinement multiplier scaled by
    ``grid_refine_factor`` (default 2x) and any explicit ``parameter_adjust``
    overrides applied on top.
    """
    parameter_adjust: dict[str, Any] | None = None
    grid_refine_factor: float = Field(default=2.0, gt=1.0, le=8.0)


# ───────────────────────── Edge Condition CRUD ─────────────────────────

@router.post("/edge-conditions", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_edge_condition(
    body: EdgeConditionCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Create a conditional edge (idempotent: same plan+source+priority returns existing)."""
    org_id = resolved_org(None, user, db)
    _ensure_edge_conditions_table(db)

    # Verify plan exists
    plan = db.execute(
        text("SELECT id, organization_id FROM campaign_plans WHERE id = :pid"),
        {"pid": body.plan_id},
    ).mappings().first()
    if not plan:
        raise HTTPException(404, "plan not found")

    # Verify source node exists and belongs to plan
    node = db.execute(
        text("SELECT id, plan_id FROM campaign_nodes WHERE id = :nid AND plan_id = :pid"),
        {"nid": body.source_node_id, "pid": body.plan_id},
    ).mappings().first()
    if not node:
        raise HTTPException(404, "source node not found in this plan")

    # Verify target nodes exist
    for target_id, label in [(body.condition.on_true, "on_true"), (body.condition.on_false, "on_false")]:
        target = db.execute(
            text("SELECT id FROM campaign_nodes WHERE id = :nid AND plan_id = :pid"),
            {"nid": target_id, "pid": body.plan_id},
        ).mappings().first()
        if not target:
            raise HTTPException(404, f"{label} node {target_id} not found in this plan")

    # Idempotency check: same plan+source+priority → return existing
    existing = db.execute(
        text("""
            SELECT id FROM campaign_edge_conditions
            WHERE plan_id = :pid AND source_node_id = :snid AND priority = :pri
        """),
        {"pid": body.plan_id, "snid": body.source_node_id, "pri": body.priority},
    ).mappings().first()

    expr_sql = _json_sql(db, ":expr")
    if existing:
        # Update existing (idempotent)
        db.execute(
            text(f"""
                UPDATE campaign_edge_conditions
                SET condition_expr = {expr_sql}, enabled = :en, updated_at = :ts
                WHERE id = :id
            """),
            {
                "id": existing["id"],
                "expr": _dumps(body.condition.dict()),
                "en": bool(body.enabled),
                "ts": datetime.now(timezone.utc),
            },
        )
        db.commit()
        return {"id": existing["id"], "status": "updated", "message": "idempotent update"}

    # Create new
    new_id = _insert_returning_id(
        db,
        f"""
            INSERT INTO campaign_edge_conditions
                (plan_id, source_node_id, condition_expr, priority, enabled, created_at, updated_at)
            VALUES (:pid, :snid, {expr_sql}, :pri, :en, :ts, :ts)
        """,
        {
            "pid": body.plan_id,
            "snid": body.source_node_id,
            "expr": _dumps(body.condition.dict()),
            "pri": body.priority,
            "en": bool(body.enabled),
            "ts": datetime.now(timezone.utc),
        },
    )
    db.commit()

    # Log event
    _log_routing_event(db, org_id, body.plan_id, "edge_condition_created", {
        "edge_condition_id": new_id,
        "source_node_id": body.source_node_id,
        "condition": body.condition.dict(),
    })

    return {"id": new_id, "status": "created"}


@router.get("/edge-conditions", dependencies=[Depends(require_auth)])
def list_edge_conditions(
    plan_id: int | None = None,
    source_node_id: int | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """List edge conditions, optionally filtered by plan or source node."""
    _ensure_edge_conditions_table(db)

    filters = ["1=1"]
    params: dict[str, Any] = {}
    if plan_id:
        filters.append("ec.plan_id = :pid")
        params["pid"] = plan_id
    if source_node_id:
        filters.append("ec.source_node_id = :snid")
        params["snid"] = source_node_id

    rows = db.execute(
        text(f"""
            SELECT ec.* FROM campaign_edge_conditions ec
            WHERE {' AND '.join(filters)}
            ORDER BY ec.plan_id, ec.source_node_id, ec.priority
        """),
        params,
    ).mappings().all()

    items = []
    for r in rows:
        item = dict(r)
        item["condition"] = _loads(item.get("condition_expr", "{}"))
        item["enabled"] = bool(item.get("enabled", True))
        items.append(item)

    return {"items": items, "total": len(items)}


@router.get("/edge-conditions/{edge_id}", dependencies=[Depends(require_auth)])
def get_edge_condition(
    edge_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get a specific edge condition."""
    _ensure_edge_conditions_table(db)
    row = db.execute(
        text("SELECT * FROM campaign_edge_conditions WHERE id = :eid"),
        {"eid": edge_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "edge condition not found")
    item = dict(row)
    item["condition"] = _loads(item.get("condition_expr", "{}"))
    item["enabled"] = bool(item.get("enabled", True))
    return item


@router.delete("/edge-conditions/{edge_id}", dependencies=[Depends(require_role("ADMIN", "MANAGER"))])
def delete_edge_condition(
    edge_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Delete an edge condition."""
    _ensure_edge_conditions_table(db)
    row = db.execute(
        text("SELECT id, plan_id FROM campaign_edge_conditions WHERE id = :eid"),
        {"eid": edge_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "edge condition not found")

    db.execute(text("DELETE FROM campaign_edge_conditions WHERE id = :eid"), {"eid": edge_id})
    db.commit()
    return {"status": "deleted", "id": edge_id}


# ───────────────────────── Node Condition Update ─────────────────────────

@router.put("/nodes/{node_id}/condition", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def update_node_condition(
    node_id: int,
    body: NodeConditionUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Update inline condition and/or convergence check on a node."""
    _ensure_routing_columns(db)

    node = db.execute(
        text("SELECT id, plan_id FROM campaign_nodes WHERE id = :nid"),
        {"nid": node_id},
    ).mappings().first()
    if not node:
        raise HTTPException(404, "node not found")

    updates = []
    params: dict[str, Any] = {"nid": node_id}

    if body.condition is not None:
        updates.append("condition = " + _json_sql(db, ":cond"))
        params["cond"] = _dumps(body.condition.dict())

    if body.convergence_check is not None:
        updates.append("convergence_check = " + _json_sql(db, ":conv"))
        params["conv"] = _dumps(body.convergence_check.dict())

    if not updates:
        raise HTTPException(400, "no fields to update")

    updates.append("updated_at = :ts")
    params["ts"] = datetime.now(timezone.utc)

    db.execute(
        text(f"UPDATE campaign_nodes SET {', '.join(updates)} WHERE id = :nid"),
        params,
    )
    db.commit()
    return {"status": "updated", "node_id": node_id}


# ───────────────────────── Routing Evaluation ─────────────────────────

def _evaluate_condition(condition: dict, metric_value: float, metric_name: str) -> bool:
    """Evaluate a gate condition against a metric value.

    Returns True if condition is met (on_true path), False otherwise.
    """
    if condition.get("metric") != metric_name:
        # Metric mismatch — treat as condition not applicable
        return False

    op = condition.get("operator", "<")
    threshold = condition.get("threshold", 0.0)

    if op == "<":
        return metric_value < threshold
    elif op == "<=":
        return metric_value <= threshold
    elif op == ">":
        return metric_value > threshold
    elif op == ">=":
        return metric_value >= threshold
    elif op == "==":
        return abs(metric_value - threshold) < 1e-9
    elif op == "!=":
        return abs(metric_value - threshold) >= 1e-9
    return False


def _check_convergence(
    db: Session,
    node_id: int,
    metric_value: float,
) -> dict:
    """Check convergence for an adaptive loop node.

    Returns:
        {"converged": bool, "iterations": int, "max_iterations": int, "action": str}
    """
    node = db.execute(
        text("SELECT convergence_check, iteration_count FROM campaign_nodes WHERE id = :nid"),
        {"nid": node_id},
    ).mappings().first()

    if not node:
        return {"converged": False, "iterations": 0, "max_iterations": 0, "action": "no_node"}

    conv_raw = node.get("convergence_check")
    conv = _loads(conv_raw) if conv_raw else None

    if not conv:
        return {"converged": True, "iterations": 0, "max_iterations": 0, "action": "no_check"}

    current_iter = node.get("iteration_count", 0)
    max_iter = conv.get("max_iterations", 3)
    target = conv.get("target", 0.01)
    action_on_exhaust = conv.get("action_on_exhaust", "escalate")

    converged = abs(metric_value) <= target

    if not converged and current_iter >= max_iter:
        return {
            "converged": False,
            "iterations": current_iter,
            "max_iterations": max_iter,
            "action": action_on_exhaust,
            "escalated": action_on_exhaust == "escalate",
        }

    return {
        "converged": converged,
        "iterations": current_iter,
        "max_iterations": max_iter,
        "action": "continue" if not converged else "converged",
    }


@router.post("/routing/evaluate", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def evaluate_routing(
    body: RoutingEvaluationRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Evaluate routing conditions for a completed node.

    This is the core gate router: when a node completes (done/failed),
    evaluate its conditions and activate the appropriate next node.

    Thread-safe: uses per-plan lock to prevent race conditions.
    """
    _ensure_routing_columns(db)
    _ensure_edge_conditions_table(db)

    # Get the node
    node = db.execute(
        text("""
            SELECT n.id, n.plan_id, n.status, n.condition, n.convergence_check,
                   n.iteration_count, n.parent_node_id, p.organization_id
            FROM campaign_nodes n
            JOIN campaign_plans p ON n.plan_id = p.id
            WHERE n.id = :nid
        """),
        {"nid": body.node_id},
    ).mappings().first()

    if not node:
        raise HTTPException(404, "node not found")

    plan_id = node["plan_id"]
    org_id = node["organization_id"]

    # Acquire per-plan lock for concurrent safety
    lock = _get_plan_lock(plan_id)
    with lock:
        return _evaluate_routing_inner(db, node, body, org_id)


def _evaluate_routing_inner(
    db: Session,
    node: Any,
    body: RoutingEvaluationRequest,
    org_id: int,
) -> dict:
    """Inner routing evaluation (called under lock)."""
    plan_id = node["plan_id"]
    node_id = node["id"]

    # 1. Check inline condition on node
    cond_raw = node.get("condition")
    condition = _loads(cond_raw) if cond_raw else None

    # 2. Check edge conditions table
    edge_conditions = db.execute(
        text("""
            SELECT * FROM campaign_edge_conditions
            WHERE source_node_id = :nid AND enabled = TRUE
            ORDER BY priority DESC
        """),
        {"nid": node_id},
    ).mappings().all()

    # Negative control: no conditions at all → unconditional routing
    if not condition and not edge_conditions:
        _log_routing_event(db, org_id, plan_id, "routing_evaluated", {
            "node_id": node_id,
            "action": "no_condition",
            "metric_value": body.metric_value,
            "metric_name": body.metric_name,
        })
        return RoutingEvaluationResponse(
            evaluated=False,
            condition_met=None,
            activated_node_id=None,
            action="no_condition",
            detail={"reason": "no conditions defined"},
        ).dict()

    # 3. Evaluate inline condition
    activated_node_id = None
    condition_met = None
    action = "skipped"

    if condition:
        condition_met = _evaluate_condition(condition, body.metric_value, body.metric_name)

        if condition_met:
            activated_node_id = condition.get("on_true")
            action = "activated"
        else:
            # Check if on_false == on_true (equivalent to unconditional)
            if condition.get("on_true") == condition.get("on_false"):
                activated_node_id = condition.get("on_true")
                action = "activated"
            else:
                # Check convergence for adaptive loop
                conv_result = _check_convergence(db, node_id, body.metric_value)

                if conv_result.get("escalated"):
                    action = "escalated"
                    # Mark node as escalated
                    db.execute(
                        text("""
                            UPDATE campaign_nodes
                            SET status = 'blocked', block_reason = :reason,
                                updated_at = :ts
                            WHERE id = :nid
                        """),
                        {
                            "nid": node_id,
                            "reason": f"convergence exhausted after {conv_result['max_iterations']} iterations",
                            "ts": datetime.now(timezone.utc),
                        },
                    )
                    db.commit()
                elif conv_result.get("action") == "continue":
                    # Adaptive loop: increment iteration and activate on_false (retry)
                    activated_node_id = condition.get("on_false")
                    action = "loop_retry"

                    # Increment iteration count
                    db.execute(
                        text("""
                            UPDATE campaign_nodes
                            SET iteration_count = iteration_count + 1, updated_at = :ts
                            WHERE id = :nid
                        """),
                        {"nid": node_id, "ts": datetime.now(timezone.utc)},
                    )
                    db.commit()
                elif conv_result.get("action") == "converged":
                    # Adaptive loop reached its convergence target: stop looping
                    # instead of re-activating the retry branch (an activated
                    # retry would re-enter the loop forever).
                    action = "converged"
                    activated_node_id = None
                    db.execute(
                        text("""
                            UPDATE campaign_nodes
                            SET status = 'done', updated_at = :ts
                            WHERE id = :nid
                        """),
                        {"nid": node_id, "ts": datetime.now(timezone.utc)},
                    )
                    db.commit()
                else:
                    activated_node_id = condition.get("on_false")
                    action = "activated"

    # 4. If no inline condition, evaluate edge conditions
    if not condition and edge_conditions:
        for ec in edge_conditions:
            ec_cond = _loads(ec.get("condition_expr", "{}"))
            if ec_cond:
                condition_met = _evaluate_condition(ec_cond, body.metric_value, body.metric_name)
                if condition_met:
                    activated_node_id = ec_cond.get("on_true")
                    action = "activated"
                    break
                else:
                    activated_node_id = ec_cond.get("on_false")
                    action = "activated"

    # 5. Activate the target node
    if activated_node_id:
        # Verify target node exists
        target = db.execute(
            text("SELECT id, status FROM campaign_nodes WHERE id = :nid"),
            {"nid": activated_node_id},
        ).mappings().first()

        if target and target["status"] == "pending":
            db.execute(
                text("""
                    UPDATE campaign_nodes
                    SET status = 'pending', updated_at = :ts
                    WHERE id = :nid
                """),
                {"nid": activated_node_id, "ts": datetime.now(timezone.utc)},
            )
            db.commit()

    # 6. Log routing event
    _log_routing_event(db, org_id, plan_id, "routing_evaluated", {
        "node_id": node_id,
        "condition_met": condition_met,
        "activated_node_id": activated_node_id,
        "action": action,
        "metric_value": body.metric_value,
        "metric_name": body.metric_name,
    })

    return RoutingEvaluationResponse(
        evaluated=True,
        condition_met=condition_met,
        activated_node_id=activated_node_id,
        action=action,
        detail={
            "plan_id": plan_id,
            "metric_value": body.metric_value,
            "metric_name": body.metric_name,
        },
    ).dict()


# ───────────────────────── Verdict-driven routing hook ─────────────────────────

def has_conditional_routing(db: Session, node_id: int) -> bool:
    """True when this node participates in conditional routing at all."""
    try:
        _ensure_routing_columns(db)
        _ensure_edge_conditions_table(db)
        row = db.execute(
            text("SELECT condition FROM campaign_nodes WHERE id = :nid"),
            {"nid": node_id},
        ).mappings().first()
        if row and row.get("condition"):
            return True
        edge = db.execute(
            text("""
                SELECT 1 FROM campaign_edge_conditions
                WHERE source_node_id = :nid AND enabled = TRUE LIMIT 1
            """),
            {"nid": node_id},
        ).first()
        return edge is not None
    except Exception:  # pragma: no cover - never block the verdict path
        logger.warning("conditional-routing probe failed for node %s", node_id, exc_info=True)
        return False


def route_after_verdict(
    db: Session,
    node_id: int,
    metric_value: float,
    metric_name: str = "max_rel_err",
    auto_retry: bool = True,
) -> dict:
    """Gate router entry point for a ``done``/``failed`` status transition.

    This is what closes the self-adaptive research loop without a human (or an
    extra agent turn) in the middle: a mission reporting its verification
    verdict also carries the measured metric, and the router immediately
    evaluates the node's gate, activates the winning branch, and — when the
    gate fails into a self-loop whose condition sets ``auto_retry`` — creates
    the refined retry node.

    Legacy DAGs (no inline condition, no enabled edge condition) return
    ``{"evaluated": False, "action": "no_condition"}`` *without* writing any
    event, so unconditional plans keep their exact previous behaviour.
    """
    node = db.execute(
        text("""
            SELECT n.id, n.plan_id, n.status, n.condition, n.convergence_check,
                   n.iteration_count, n.parent_node_id, p.organization_id
            FROM campaign_nodes n
            JOIN campaign_plans p ON n.plan_id = p.id
            WHERE n.id = :nid
        """),
        {"nid": node_id},
    ).mappings().first()
    if not node:
        return {"evaluated": False, "action": "skipped", "detail": {"reason": "node not found"}}

    if not has_conditional_routing(db, node_id):
        return {
            "evaluated": False,
            "action": "no_condition",
            "detail": {"reason": "unconditional DAG — legacy routing untouched"},
        }

    body = RoutingEvaluationRequest(
        node_id=node_id, metric_value=metric_value, metric_name=metric_name,
    )
    lock = _get_plan_lock(int(node["plan_id"]))
    with lock:
        result = _evaluate_routing_inner(db, node, body, int(node["organization_id"]))
        detail = result.setdefault("detail", {})
        detail["trigger"] = "verdict"

        condition = _loads(node.get("condition")) if node.get("condition") else None
        if (auto_retry and result.get("action") == "loop_retry"
                and isinstance(condition, dict) and condition.get("auto_retry")):
            retry = create_retry_node(node_id, None, db=db, user=None)
            detail["retry_node"] = retry
    return result


# ───────────────────────── Adaptive Loop Management ─────────────────────────

@router.post("/nodes/{node_id}/create-retry", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_retry_node(
    node_id: int,
    body: RetryNodeRequest | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Create a retry node for adaptive loop (copies original with incremented iteration).

    The retry node references the original via parent_node_id.
    """
    _ensure_routing_columns(db)

    original = db.execute(
        text("""
            SELECT n.*, p.organization_id FROM campaign_nodes n
            JOIN campaign_plans p ON n.plan_id = p.id
            WHERE n.id = :nid
        """),
        {"nid": node_id},
    ).mappings().first()

    if not original:
        raise HTTPException(404, "node not found")

    # Create retry node.
    # Iteration accounting: iteration_count = number of failed rounds already
    # consumed for this verification chain, and it is incremented exactly once
    # per failed round at routing evaluation time. The retry node therefore
    # inherits the parent's (already incremented) count verbatim — incrementing
    # again here double-counted and made max_iterations guard fire a round early.
    parent_iteration = original.get("iteration_count", 0) or 0
    retry_name = f"{original['name']}_retry_{parent_iteration + 1}"

    # Adaptive-loop parameter adjustment (grid refinement 2x by default).
    req = body or RetryNodeRequest()
    orig_meta = _loads(original.get("metadata")) or {}
    if not isinstance(orig_meta, dict):
        orig_meta = {}
    base_multiplier = float(orig_meta.get("grid_multiplier", 1.0) or 1.0)
    params = dict(orig_meta.get("params") or {}) if isinstance(orig_meta.get("params"), dict) else {}
    if req.parameter_adjust:
        params.update(req.parameter_adjust)
    grid_multiplier = base_multiplier * float(req.grid_refine_factor)
    params["grid_multiplier"] = grid_multiplier
    for key in ("nx", "ny", "nz", "n_grid", "grid_size", "grid_points"):
        if key in params:
            try:
                params[key] = int(round(float(params[key]) * float(req.grid_refine_factor)))
            except (TypeError, ValueError):
                pass

    # PostgreSQL stores dependencies as INTEGER[] and the routing/meta columns
    # as JSONB; the SQLite runtime keeps them as TEXT. Bind each column in the
    # representation its dialect accepts (a JSON string is not an array, and a
    # bare str is not JSONB).
    deps_value = [node_id] if _is_pg(db) else _dumps([node_id])
    new_id = _insert_returning_id(
        db,
        f"""
            INSERT INTO campaign_nodes
                (plan_id, milestone_id, mission_id, name, node_type,
                 dependencies, status, condition, convergence_check,
                 iteration_count, parent_node_id, metadata, created_at, updated_at)
            VALUES (:pid, :mid, :moid, :name, :ntype, :deps, 'pending',
                    {_json_sql(db, ':cond')}, {_json_sql(db, ':conv')},
                    :iter, :parent, {_json_sql(db, ':meta')}, :ts, :ts)
        """,
        {
            "pid": original["plan_id"],
            "mid": original.get("milestone_id"),
            "moid": original.get("mission_id"),
            "name": retry_name,
            "ntype": original["node_type"],
            "deps": deps_value,  # Depends on original
            "cond": _json_value(original.get("condition")),
            "conv": _json_value(original.get("convergence_check")),
            "iter": parent_iteration,
            "parent": node_id,
            "meta": _dumps({
                "is_retry": True,
                "parent_node_id": node_id,
                "grid_multiplier": grid_multiplier,
                "grid_refine_factor": float(req.grid_refine_factor),
                "params": params,
            }),
            "ts": datetime.now(timezone.utc),
        },
    )
    db.commit()

    # Log event
    org_id = original.get("organization_id") or 1
    _log_routing_event(db, org_id, original["plan_id"], "retry_node_created", {
        "original_node_id": node_id,
        "retry_node_id": new_id,
        "iteration": parent_iteration,
        "grid_multiplier": grid_multiplier,
        "grid_refine_factor": float(req.grid_refine_factor),
        "parameter_adjust": req.parameter_adjust,
    })

    return {"id": new_id, "name": retry_name, "iteration": parent_iteration}


# ───────────────────────── Routing Status Query ─────────────────────────

@router.get("/plans/{plan_id}/routing-status", dependencies=[Depends(require_auth)])
def get_plan_routing_status(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Get routing status for all nodes in a plan.

    Shows which nodes have conditions, their iteration counts, and routing state.
    """
    _ensure_routing_columns(db)
    _ensure_edge_conditions_table(db)

    nodes = db.execute(
        text("""
            SELECT n.id, n.name, n.status, n.condition, n.convergence_check,
                   n.iteration_count, n.parent_node_id, n.block_reason
            FROM campaign_nodes n
            WHERE n.plan_id = :pid
            ORDER BY n.id
        """),
        {"pid": plan_id},
    ).mappings().all()

    edge_conditions = db.execute(
        text("""
            SELECT ec.* FROM campaign_edge_conditions ec
            WHERE ec.plan_id = :pid
            ORDER BY ec.source_node_id, ec.priority
        """),
        {"pid": plan_id},
    ).mappings().all()

    node_routing = []
    for n in nodes:
        cond = _loads(n.get("condition")) if n.get("condition") else None
        conv = _loads(n.get("convergence_check")) if n.get("convergence_check") else None
        node_routing.append({
            "node_id": n["id"],
            "name": n["name"],
            "status": n["status"],
            "has_condition": cond is not None,
            "condition": cond,
            "convergence_check": conv,
            "iteration_count": n.get("iteration_count", 0),
            "parent_node_id": n.get("parent_node_id"),
            "block_reason": n.get("block_reason"),
        })

    edges = []
    for ec in edge_conditions:
        edges.append({
            "id": ec["id"],
            "source_node_id": ec["source_node_id"],
            "condition": _loads(ec.get("condition_expr", "{}")),
            "priority": ec["priority"],
            "enabled": bool(ec.get("enabled", True)),
        })

    return {
        "plan_id": plan_id,
        "nodes": node_routing,
        "edge_conditions": edges,
        "total_nodes": len(node_routing),
        "nodes_with_conditions": sum(1 for n in node_routing if n["has_condition"]),
    }


# ───────────────────────── Backward Compatibility ─────────────────────────

@router.get("/plans/{plan_id}/validate-dag", dependencies=[Depends(require_auth)])
def validate_dag(
    plan_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Validate DAG structure including conditional edges.

    Checks:
      - No cycles (including through conditional edges)
      - All referenced nodes exist
      - Conditions are well-formed
    """
    _ensure_routing_columns(db)
    _ensure_edge_conditions_table(db)

    nodes = db.execute(
        text("SELECT id, name, dependencies, condition FROM campaign_nodes WHERE plan_id = :pid"),
        {"pid": plan_id},
    ).mappings().all()

    if not nodes:
        return {"valid": True, "errors": [], "warnings": ["no nodes in plan"]}

    node_ids = {n["id"] for n in nodes}
    errors = []
    warnings = []

    # Check dependencies reference valid nodes
    for n in nodes:
        deps = _loads(n.get("dependencies")) or []
        if isinstance(deps, str):
            try:
                deps = json.loads(deps)
            except (json.JSONDecodeError, TypeError):
                deps = []
        for dep_id in deps:
            if dep_id not in node_ids:
                errors.append(f"Node {n['id']} depends on non-existent node {dep_id}")

    # Check conditions reference valid nodes
    for n in nodes:
        cond = _loads(n.get("condition")) if n.get("condition") else None
        if cond:
            for key in ("on_true", "on_false"):
                target = cond.get(key)
                if target and target not in node_ids:
                    errors.append(f"Node {n['id']} condition {key} references non-existent node {target}")

    # Check edge conditions
    edge_conditions = db.execute(
        text("SELECT * FROM campaign_edge_conditions WHERE plan_id = :pid"),
        {"pid": plan_id},
    ).mappings().all()

    for ec in edge_conditions:
        cond = _loads(ec.get("condition_expr", "{}"))
        if cond:
            for key in ("on_true", "on_false"):
                target = cond.get(key)
                if target and target not in node_ids:
                    errors.append(f"Edge condition {ec['id']} {key} references non-existent node {target}")

    # Simple cycle detection via DFS
    adj: dict[int, list[int]] = {n["id"]: [] for n in nodes}
    for n in nodes:
        deps = _loads(n.get("dependencies")) or []
        if isinstance(deps, str):
            try:
                deps = json.loads(deps)
            except (json.JSONDecodeError, TypeError):
                deps = []
        for dep_id in deps:
            if dep_id in node_ids:
                adj[dep_id].append(n["id"])

        # Add conditional edges
        cond = _loads(n.get("condition")) if n.get("condition") else None
        if cond:
            for key in ("on_true", "on_false"):
                target = cond.get(key)
                if target and target in node_ids:
                    adj[n["id"]].append(target)

    # DFS cycle detection
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}
    has_cycle = False

    def dfs(u: int) -> bool:
        nonlocal has_cycle
        color[u] = GRAY
        for v in adj.get(u, []):
            if color[v] == GRAY:
                has_cycle = True
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    for nid in node_ids:
        if color[nid] == WHITE:
            dfs(nid)

    if has_cycle:
        warnings.append("potential cycle detected in conditional DAG")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "node_count": len(nodes),
        "edge_condition_count": len(edge_conditions),
    }


# ───────────────────────── Spec-aligned endpoints ─────────────────────────
# The endpoints below are the contractual surface of the campaign conditional
# DAG (mission spec (e)). They are thin adapters: argument translation plus
# audit summary only, so the routing semantics stay in exactly one place
# (create_edge_condition / evaluate_routing / get_plan_routing_status).

@router.post("/plans/{plan_id}/edges", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def create_plan_edge(
    plan_id: int,
    body: PlanEdgeCreateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Create (or idempotently update) a conditional edge of a plan.

    Idempotency key = (plan_id, source_node_id, priority): repeating the same
    POST returns ``status="updated"`` with the same edge id instead of creating
    a duplicate row.
    """
    payload = EdgeConditionCreate(
        plan_id=plan_id,
        source_node_id=body.source_node_id,
        condition=body.condition,
        priority=body.priority,
        enabled=body.enabled,
    )
    result = create_edge_condition(body=payload, db=db, user=user)
    out = dict(result)
    out["plan_id"] = plan_id
    return out


@router.get("/plans/{plan_id}/routing", dependencies=[Depends(require_auth)])
def get_plan_routing(
    plan_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Routing view of a plan: node conditions, edge conditions and the audit tail.

    ``events`` is the routing audit trail (campaign_events) restricted to
    routing-related event types, newest first — this is what makes every
    routing decision traceable after the fact.
    """
    if not isinstance(limit, int):  # called programmatically: Query object leaked
        limit = 50
    view = get_plan_routing_status(plan_id=plan_id, db=db, user=user)
    try:
        rows = db.execute(
            text("""
                SELECT id, event_type, detail, created_at
                FROM campaign_events
                WHERE plan_id = :pid
                  AND event_type IN ('routing_evaluated', 'edge_condition_created',
                                     'retry_node_created', 'edge_condition_deleted')
                ORDER BY id DESC
                LIMIT :lim
            """),
            {"pid": plan_id, "lim": limit},
        ).mappings().all()
        events = [
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "detail": _loads(r["detail"]),
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]
    except Exception:  # pragma: no cover - audit table absent → view still usable
        logger.warning("campaign_events audit query failed for plan %s", plan_id, exc_info=True)
        events = []

    out = dict(view)
    out["events"] = events
    out["event_count"] = len(events)
    return out


@router.post("/nodes/{node_id}/evaluate", dependencies=[Depends(require_role("ADMIN", "MANAGER", "ENGINEER"))])
def evaluate_node_routing(
    node_id: int,
    body: NodeEvaluateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Evaluate the routing condition of one node (path-parameter form).

    Semantics are identical to POST /routing/evaluate; this form exists so a
    dispatching agent that already knows the node id does not have to restate
    it in the body (and cannot restate it wrongly).
    """
    payload = RoutingEvaluationRequest(
        node_id=node_id,
        metric_value=body.metric_value,
        metric_name=body.metric_name,
    )
    return evaluate_routing(body=payload, db=db, user=user)
