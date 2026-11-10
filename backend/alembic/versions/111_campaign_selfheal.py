"""campaign DAG self-healing: widen status & event-type CHECK whitelists

The self-healing state machine (``services.selfheal``) persists three new node
statuses (``skipped`` / ``degraded`` / ``retry_pending``) and a family of
``selfheal_*`` audit events.  On a migrated PostgreSQL database the CHECK
constraints created by migration 095 reject both, which would abort every
healing transaction.  This migration unions the new literals into the existing
constraints (idempotent; only ever *adds* allowed values).

Revision ID: 111_campaign_selfheal
Revises: 110_campaign_routing_events
Create Date: 2026-11-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "111_campaign_selfheal"
down_revision: Union[str, Sequence[str], None] = "110_campaign_routing_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_NODE_STATUSES = ("skipped", "degraded", "retry_pending")

SELFHEAL_EVENT_TYPES = tuple(
    "selfheal_" + action
    for action in (
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
)

_CONSTRAINT_SQL = """
    SELECT c.conname AS conname, pg_get_constraintdef(c.oid) AS cdef
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = :table
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE :pattern
"""


def widen_check(bind, table: str, column: str, extra: Sequence[str]) -> list[str]:
    """Union ``extra`` literals into CHECK constraints mentioning ``column``."""
    if bind.dialect.name != "postgresql":
        return []
    import re

    rewritten: list[str] = []
    rows = bind.execute(
        sa.text(_CONSTRAINT_SQL), {"table": table, "pattern": f"%{column}%"}
    ).mappings().all()
    wanted = set(extra)
    for row in rows:
        existing = re.findall(r"'([^']*)'", row["cdef"] or "")
        if not existing or wanted <= set(existing):
            continue
        allowed = list(dict.fromkeys(list(existing) + list(extra)))
        values = ", ".join("'" + v.replace("'", "''") + "'" for v in allowed)
        name = row["conname"]
        bind.execute(sa.text(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"'))
        bind.execute(
            sa.text(
                f'ALTER TABLE {table} ADD CONSTRAINT "{name}" '
                f"CHECK ({column} IN ({values}))"
            )
        )
        rewritten.append(name)
    return rewritten


def upgrade() -> None:
    bind = op.get_bind()
    widen_check(bind, "campaign_nodes", "status", NEW_NODE_STATUSES)
    widen_check(bind, "campaign_events", "event_type", SELFHEAL_EVENT_TYPES)


def downgrade() -> None:
    """Irreversible by design: dropping literals could orphan audit rows."""
    pass