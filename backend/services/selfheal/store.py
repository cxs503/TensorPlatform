"""Persistence adapter: campaign tables ⇄ self-healing engine.

The engine itself is storage-agnostic; this module is the thin bridge to
``campaign_nodes`` (state) and ``campaign_events`` (audit chain).  It speaks
both dialects used by the platform:

* PostgreSQL — production, where ``campaign_nodes.status`` and
  ``campaign_events.event_type`` carry CHECK whitelists that must be widened
  once (``ensure_selfheal_schema`` / ``alembic 111``);
* SQLite — embedded runtime and the API test harness.

No new columns are required: retry bookkeeping lives in the node's existing
``metadata`` JSON, which keeps the migration surface minimal.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from sqlalchemy import text

from .audit import AuditChain, AuditEvent
from .dag import DagNode, NodeStatus, SelfHealingDag
from .policy import SelfHealPolicy

logger = logging.getLogger(__name__)

_EVENT_PREFIX = "selfheal_"
#: Every audit action is persisted as its own event type for SQL filtering.
AUDIT_ACTIONS = (
    "node_succeeded",
    "node_failed",
    "retry_scheduled",
    "retry_dispatched",
    "recovered",
    "degraded",
    "degrade_rejected",
    "bypassed",
    "escalated",
    "dependency_blocked",
    "saturation_detected",
    "schedule_deferred",
    "schedule_dispatched",
    "cycle_rejected",
    "policy_updated",
)
SELFHEAL_EVENT_TYPES = tuple(_EVENT_PREFIX + a for a in AUDIT_ACTIONS)

#: DB status literal ⇄ engine enum.  ``orphan`` is a legacy platform status
#: meaning "no owner" — treated as blocked, never auto-dispatched.
_STATUS_FROM_DB = {
    "pending": NodeStatus.PENDING,
    "in-flight": NodeStatus.IN_FLIGHT,
    "done": NodeStatus.DONE,
    "failed": NodeStatus.FAILED,
    "retry_pending": NodeStatus.RETRY_PENDING,
    "skipped": NodeStatus.SKIPPED,
    "degraded": NodeStatus.DEGRADED,
    "blocked": NodeStatus.BLOCKED,
    "orphan": NodeStatus.BLOCKED,
}
_STATUS_TO_DB = {
    NodeStatus.PENDING: "pending",
    NodeStatus.IN_FLIGHT: "in-flight",
    NodeStatus.DONE: "done",
    NodeStatus.FAILED: "failed",
    NodeStatus.RETRY_PENDING: "retry_pending",
    NodeStatus.SKIPPED: "skipped",
    NodeStatus.DEGRADED: "degraded",
    NodeStatus.BLOCKED: "blocked",
}


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _dialect(db: Any) -> str:
    try:
        bind = db.get_bind()
    except AttributeError:
        bind = db
    return getattr(getattr(bind, "dialect", None), "name", "sqlite")


def _is_pg(db: Any) -> bool:
    return _dialect(db) == "postgresql"


def _now_sql(db: Any) -> str:
    return "now()" if _is_pg(db) else "datetime('now')"


def _rows(db: Any, sql: str, params: dict | None = None) -> list[dict]:
    result = db.execute(text(sql), params or {})
    mappings = getattr(result, "mappings", None)
    if callable(mappings):
        return [dict(r) for r in mappings().all()]
    return [dict(r._mapping) for r in result.fetchall()]


def _first(db: Any, sql: str, params: dict | None = None) -> dict | None:
    rows = _rows(db, sql, params)
    return rows[0] if rows else None


# ───────────────────────── schema widening (PostgreSQL) ─────────────────────

def _widen_check(
    db: Any, table: str, column: str, extra: Iterable[str]
) -> list[str]:
    """Union new literals into a CHECK constraint listing ``column`` values.

    PostgreSQL cannot ALTER a CHECK in place, so each matching constraint is
    dropped and re-created with ``existing ∪ extra``.  Idempotent; SQLite is a
    no-op here because test schemas carry no whitelist.
    """
    if not _is_pg(db):
        return []
    extra = [str(x) for x in extra]
    rows = _rows(
        db,
        """
        SELECT c.conname AS conname, pg_get_constraintdef(c.oid) AS cdef
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = :table
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) LIKE :pat
        """,
        {"table": table, "pat": f"%{column}%"},
    )
    rewritten: list[str] = []
    for row in rows:
        existing = re.findall(r"'([^']*)'", row["cdef"] or "")
        if not existing:
            continue
        if set(extra) <= set(existing):
            continue
        literals = list(dict.fromkeys(existing + extra))
        values = ", ".join("'" + v.replace("'", "''") + "'" for v in literals)
        name = row["conname"]
        db.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"'))
        db.execute(
            text(
                f'ALTER TABLE {table} ADD CONSTRAINT "{name}" '
                f"CHECK ({column} IN ({values}))"
            )
        )
        rewritten.append(name)
    return rewritten


def ensure_selfheal_schema(db: Any) -> dict[str, Any]:
    """Widen the status/event whitelists so healing states can be persisted."""
    if not _is_pg(db):
        return {"dialect": "sqlite", "widened": []}
    try:
        status = _widen_check(
            db,
            "campaign_nodes",
            "status",
            ["skipped", "degraded", "retry_pending"],
        )
        events = _widen_check(
            db,
            "campaign_events",
            "event_type",
            SELFHEAL_EVENT_TYPES,
        )
        db.commit()
        return {"dialect": "postgresql", "widened": status + events}
    except Exception as exc:  # pragma: no cover - permission/DDL edge cases
        db.rollback()
        logger.warning("selfheal schema widening failed: %s", exc)
        return {"dialect": "postgresql", "widened": [], "error": str(exc)}


# ───────────────────────── plans / policy ─────────────────────────

def load_plan(db: Any, plan_id: int) -> dict[str, Any] | None:
    return _first(
        db,
        "SELECT id, organization_id, name, metadata FROM campaign_plans WHERE id = :pid",
        {"pid": plan_id},
    )


def load_policy(db: Any, plan_id: int) -> SelfHealPolicy:
    plan = load_plan(db, plan_id)
    if not plan:
        return SelfHealPolicy()
    meta = _loads(plan.get("metadata"), {})
    return SelfHealPolicy.from_dict(meta.get("selfheal") if isinstance(meta, dict) else None)


def save_policy(db: Any, plan_id: int, policy: SelfHealPolicy) -> SelfHealPolicy:
    plan = load_plan(db, plan_id)
    if not plan:
        raise KeyError(f"campaign plan {plan_id} not found")
    meta = _loads(plan.get("metadata"), {})
    if not isinstance(meta, dict):
        meta = {}
    meta["selfheal"] = policy.to_dict()
    db.execute(
        text("UPDATE campaign_plans SET metadata = :m WHERE id = :pid"),
        {"m": _dumps(meta), "pid": plan_id},
    )
    db.commit()
    return policy


# ───────────────────────── nodes ⇄ DAG ─────────────────────────

def _node_from_row(row: dict) -> DagNode:
    deps_raw = _loads(row.get("dependencies"), [])
    if isinstance(deps_raw, str):  # e.g. "{1,2}" from an int[] rendering
        deps_raw = [d for d in re.split(r"[,\s{}]+", deps_raw) if d]
    deps = []
    for d in deps_raw:
        try:
            deps.append(int(d))
        except (TypeError, ValueError):
            deps.append(d)
    meta = _loads(row.get("metadata"), {})
    if not isinstance(meta, dict):
        meta = {}
    status = _STATUS_FROM_DB.get(str(row.get("status") or "pending"), NodeStatus.PENDING)
    return DagNode(
        node_id=row["id"],
        name=row.get("name") or str(row["id"]),
        deps=tuple(deps),
        critical=bool(meta.get("critical", False)),
        degradable=bool(meta.get("degradable", False)),
        max_retries=meta.get("max_retries"),
        status=status,
        attempts=int(meta.get("attempts") or 0),
        consecutive_fails=int(row.get("consecutive_fails") or 0),
        next_attempt_at=meta.get("next_attempt_at"),
        defer_until=meta.get("defer_until"),
        result=meta.get("result"),
        baseline_result=meta.get("baseline_result"),
        quality=str(meta.get("quality") or "normal"),
        last_error=row.get("block_reason") or meta.get("last_error"),
        metadata=meta,
    )


def load_dag(db: Any, plan_id: int, name: str | None = None) -> SelfHealingDag:
    rows = _rows(
        db,
        """
        SELECT id, name, dependencies, status, consecutive_fails, block_reason, metadata
        FROM campaign_nodes WHERE plan_id = :pid ORDER BY id
        """,
        {"pid": plan_id},
    )
    dag = SelfHealingDag(name or f"plan-{plan_id}")
    for row in rows:
        node = _node_from_row(row)
        dag.add_node(
            node.node_id,
            node.name,
            node.deps,
            critical=node.critical,
            degradable=node.degradable,
            max_retries=node.max_retries,
            status=node.status,
            consecutive_fails=node.consecutive_fails,
            attempts=node.attempts,
            next_attempt_at=node.next_attempt_at,
            defer_until=node.defer_until,
            result=node.result,
            baseline_result=node.baseline_result,
            quality=node.quality,
            last_error=node.last_error,
            metadata=node.metadata,
        )
    return dag


def save_node(db: Any, node: DagNode) -> None:
    meta = dict(node.metadata or {})
    meta.update(
        {
            "attempts": node.attempts,
            "next_attempt_at": node.next_attempt_at,
            "defer_until": node.defer_until,
            "quality": node.quality,
            "critical": node.critical,
            "degradable": node.degradable,
            "max_retries": node.max_retries,
            "baseline_result": node.baseline_result,
            "result": node.result,
            "last_error": node.last_error,
        }
    )
    db.execute(
        text(
            f"UPDATE campaign_nodes SET status = :s, consecutive_fails = :cf, "
            f"block_reason = :br, metadata = :m, updated_at = {_now_sql(db)} "
            f"WHERE id = :nid"
        ),
        {
            "s": _STATUS_TO_DB[node.status],
            "cf": node.consecutive_fails,
            "br": node.last_error,
            "m": _dumps(meta),
            "nid": node.node_id,
        },
    )


def save_dag(db: Any, dag: SelfHealingDag) -> int:
    for node in dag.nodes.values():
        save_node(db, node)
    db.commit()
    return len(dag.nodes)


# ───────────────────────── audit chain ⇄ campaign_events ─────────────────────

def persist_events(
    db: Any, plan_id: int, organization_id: int, events: Iterable[AuditEvent]
) -> int:
    count = 0
    for ev in events:
        db.execute(
            text(
                "INSERT INTO campaign_events "
                "(organization_id, plan_id, event_type, detail, created_at) "
                "VALUES (:org, :pid, :etype, :detail, :ts)"
            ),
            {
                "org": organization_id,
                "pid": plan_id,
                "etype": _EVENT_PREFIX + ev.action,
                "detail": _dumps(ev.to_dict()),
                "ts": _ts_iso(ev.ts),
            },
        )
        count += 1
    db.commit()
    return count


def _ts_iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def load_audit(db: Any, plan_id: int) -> AuditChain:
    rows = _rows(
        db,
        """
        SELECT detail FROM campaign_events
        WHERE plan_id = :pid AND event_type LIKE 'selfheal\\_%' ESCAPE '\\'
        ORDER BY id
        """,
        {"pid": plan_id},
    )
    events = []
    for row in rows:
        payload = _loads(row.get("detail"), None)
        if not isinstance(payload, dict) or "seq" not in payload or "action" not in payload:
            continue
        events.append(AuditEvent.from_dict(payload))
    return AuditChain(events)


def append_plan_event(
    db: Any, plan_id: int, organization_id: int, action: str, ts: float, **detail: Any
) -> dict[str, Any]:
    """Append a single plan-scoped audit event (e.g. ``cycle_rejected``)."""
    chain = load_audit(db, plan_id)
    ev = chain.append(ts, None, action, "rejected", **detail)
    persist_events(db, plan_id, organization_id, [ev])
    return ev.to_dict()