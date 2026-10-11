"""Structured audit adapter for model-factory experiments.

The platform already exposes the shared ``audit_logs`` table.  Experiment
events reuse that table without changing its schema: stable searchable fields
use the existing columns and the complete, deliberately small event is stored
as canonical JSON in ``detail``.

Audit writes use an independent database session.  This keeps an audit outage
from rolling back a truthful experiment state transition and, conversely,
prevents an audit insert from committing an unfinished experiment transaction.
Failure is explicit through the boolean return value and a structured warning;
the experiment state remains the source of truth and is never rewritten to
pretend success.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)

ENTITY_TYPE = "model_factory_experiment"
_ACTIONS = {"create", "cancel", "dispatch", "execute"}
_OUTCOMES = {"success", "failed", "completed", "cancelled"}


def _event(*, actor_id, organization_id, job_id, kind, action, outcome) -> dict:
    action = str(action).strip().lower()
    outcome = str(outcome).strip().lower()
    if action not in _ACTIONS:
        raise ValueError(f"unsupported experiment audit action: {action}")
    if outcome not in _OUTCOMES:
        raise ValueError(f"unsupported experiment audit outcome: {outcome}")
    return {
        "actor_id": str(actor_id or "system/unknown")[:128],
        "organization_id": int(organization_id),
        "job_id": int(job_id),
        "kind": str(kind)[:32],
        "action": action,
        "outcome": outcome,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def emit_experiment_audit(
    *,
    actor_id,
    organization_id,
    job_id,
    kind,
    action,
    outcome,
    session_factory: Callable | None = None,
) -> bool:
    """Best-effort independent insert into the existing ``audit_logs`` table.

    No configuration, dataset content, metrics, error text, or artifact content
    is accepted by this interface, which makes accidental secret logging
    structurally impossible.
    """
    event = _event(
        actor_id=actor_id,
        organization_id=organization_id,
        job_id=job_id,
        kind=kind,
        action=action,
        outcome=outcome,
    )
    if session_factory is None:
        from database.config import SessionLocal

        session_factory = SessionLocal

    db = None
    try:
        db = session_factory()
        db.execute(
            text(
                "INSERT INTO audit_logs "
                "(action, entity_type, entity_id, detail, user_name) "
                "VALUES (:action, :entity_type, :entity_id, :detail, :user_name)"
            ),
            {
                "action": f"experiment.{event['action']}",
                "entity_type": ENTITY_TYPE,
                "entity_id": str(event["job_id"]),
                "detail": json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "user_name": event["actor_id"][:64],
            },
        )
        db.commit()
        return True
    except Exception as exc:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        logger.warning(
            "experiment_audit_write_failed action=%s outcome=%s organization_id=%s job_id=%s error_type=%s",
            event["action"],
            event["outcome"],
            event["organization_id"],
            event["job_id"],
            type(exc).__name__,
        )
        return False
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


__all__ = ["ENTITY_TYPE", "emit_experiment_audit"]
