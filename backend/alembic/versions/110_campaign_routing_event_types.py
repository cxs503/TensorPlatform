"""campaign routing event types: widen campaign_events.event_type whitelist

Migration 095 pinned ``campaign_events.event_type`` to a CHECK whitelist that
predates conditional DAG routing. Writing the routing audit events
(``routing_evaluated`` / ``edge_condition_created`` / ``edge_condition_deleted``
/ ``retry_node_created``) therefore violated the constraint and aborted the
transaction, i.e. the gate router could not record its decisions on a migrated
database.

The API layer already widens the whitelist lazily on first use
(``api.campaign_routing._ensure_event_types``); this migration does the same at
schema level so a freshly migrated database is correct before the first routing
call. Both paths are idempotent and only ever *add* literals — no existing event
type can be dropped.

Revision ID: 110_campaign_routing_events
Revises: 109_campaign_cond_dag
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "110_campaign_routing_events"
down_revision: Union[str, Sequence[str], None] = "109_campaign_cond_dag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROUTING_EVENT_TYPES = (
    "edge_condition_created",
    "edge_condition_deleted",
    "routing_evaluated",
    "retry_node_created",
)

_CONSTRAINT_SQL = """
    SELECT c.conname AS conname, pg_get_constraintdef(c.oid) AS cdef
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'campaign_events'
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%event_type%'
"""


def widen_event_type_check(bind) -> list[str]:
    """Re-create the event_type CHECK with the routing types added.

    Returns the list of constraints that were rewritten (empty = nothing to do).
    Dialect-aware: SQLite has no named CHECK rewrite, so it is a no-op there.
    """
    if bind.dialect.name != "postgresql":
        return []

    import re

    rewritten: list[str] = []
    rows = bind.execute(sa.text(_CONSTRAINT_SQL)).mappings().all()
    wanted = set(ROUTING_EVENT_TYPES)
    for row in rows:
        existing = set(re.findall(r"'([^']*)'", row["cdef"] or ""))
        if wanted <= existing:
            continue
        allowed = sorted(existing | wanted)
        values = ", ".join(f"'{v}'" for v in allowed)
        bind.execute(sa.text(f'ALTER TABLE campaign_events DROP CONSTRAINT "{row["conname"]}"'))
        bind.execute(sa.text(
            "ALTER TABLE campaign_events "
            "ADD CONSTRAINT campaign_events_event_type_check "
            f"CHECK (event_type IN ({values}))"
        ))
        rewritten.append(row["conname"])
    return rewritten


def upgrade() -> None:
    widen_event_type_check(op.get_bind())


def downgrade() -> None:
    """Irreversible by design: dropping literals could orphan audit rows."""
    pass
