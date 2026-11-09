"""campaign conditional DAG: edge conditions + node routing/convergence columns

Adds the storage layer of campaign conditional branching & adaptive research
loops (mission m7918, migration 109):

  campaign_nodes.condition           JSON  — inline gate of this node
                                           {type, metric, operator, threshold,
                                            on_true, on_false}
  campaign_nodes.convergence_check   JSON  — {metric, target, max_iterations,
                                              action_on_exhaust}
  campaign_nodes.iteration_count     INT   — failed rounds consumed by the loop
  campaign_nodes.parent_node_id      BIGINT— node this retry was derived from
  campaign_edge_conditions           table — explicit conditional edges
                                           UNIQUE(plan_id, source_node_id, priority)
                                           (the idempotency key of
                                            POST /plans/{id}/edges)

JSON is JSONB on PostgreSQL and TEXT on other dialects (SQLite test/runtime),
matching the column type the API layer self-heals when it meets an older DB.

Revision ID: 109_campaign_cond_dag
Revises: ca5e2b2ff9e3
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "109_campaign_cond_dag"
down_revision: Union[str, Sequence[str], None] = "ca5e2b2ff9e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.types.TypeEngine:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        return JSONB()
    return sa.Text()


def upgrade() -> None:
    json_type = _json_type()
    is_pg = op.get_bind().dialect.name == "postgresql"

    # ── campaign_nodes: routing / convergence columns (additive, nullable) ──
    op.add_column("campaign_nodes", sa.Column("condition", json_type, nullable=True))
    op.add_column("campaign_nodes", sa.Column("convergence_check", json_type, nullable=True))
    op.add_column(
        "campaign_nodes",
        sa.Column("iteration_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("campaign_nodes", sa.Column("parent_node_id", sa.Integer(), nullable=True))
    if is_pg:
        op.create_foreign_key(
            "fk_campaign_nodes_parent_node_id",
            "campaign_nodes", "campaign_nodes",
            ["parent_node_id"], ["id"],
        )

    # ── campaign_edge_conditions ──
    op.create_table(
        "campaign_edge_conditions",
        sa.Column("id", sa.BigInteger() if is_pg else sa.Integer(),
                  primary_key=True, autoincrement=True),
        sa.Column("plan_id", sa.BigInteger() if is_pg else sa.Integer(),
                  sa.ForeignKey("campaign_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_node_id", sa.BigInteger() if is_pg else sa.Integer(),
                  sa.ForeignKey("campaign_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("condition_expr", json_type, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plan_id", "source_node_id", "priority",
                            name="uq_campaign_edge_conditions_key"),
    )
    op.create_index(
        "ix_campaign_edge_conditions_plan", "campaign_edge_conditions", ["plan_id"]
    )
    op.create_index(
        "ix_campaign_edge_conditions_source", "campaign_edge_conditions",
        ["source_node_id", "enabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_edge_conditions_source", table_name="campaign_edge_conditions")
    op.drop_index("ix_campaign_edge_conditions_plan", table_name="campaign_edge_conditions")
    op.drop_table("campaign_edge_conditions")

    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "fk_campaign_nodes_parent_node_id", "campaign_nodes", type_="foreignkey"
        )
    op.drop_column("campaign_nodes", "parent_node_id")
    op.drop_column("campaign_nodes", "iteration_count")
    op.drop_column("campaign_nodes", "convergence_check")
    op.drop_column("campaign_nodes", "condition")