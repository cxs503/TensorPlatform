"""capability registry: current-state snapshot table

owner directive 2026-11-16: dynamic capability registration (microservice-style,
code-registered, live-queryable). Complements tensor_capability_deltas (change
history): registry = current state, deltas = per-mission change log.
"""
revision = "114_capability_registry"
down_revision = "113_acceptance_criteria_columns"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "tensor_capability_registry",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="module"),
        sa.Column("maturity", sa.Text(), nullable=False, server_default="developing"),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("module_path", sa.Text(), nullable=False),
        sa.Column("entry", sa.Text(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("wired", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("test_path", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_commit", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("repo", "capability_id", name="uq_capreg_repo_capability"),
    )
    op.create_index("ix_capreg_repo_kind", "tensor_capability_registry", ["repo", "kind"])
    op.create_index("ix_capreg_repo_maturity", "tensor_capability_registry", ["repo", "maturity"])
    op.create_check_constraint(
        "ck_capreg_maturity", "tensor_capability_registry",
        "maturity IN ('mature','developing','planned')")
    op.create_index("ix_capreg_repo_active", "tensor_capability_registry", ["repo", "active"])


def downgrade() -> None:
    op.drop_index("ix_capreg_repo_active", table_name="tensor_capability_registry")
    op.drop_constraint("ck_capreg_maturity", "tensor_capability_registry", type_="check")
    op.drop_index("ix_capreg_repo_maturity", table_name="tensor_capability_registry")
    op.drop_index("ix_capreg_repo_kind", table_name="tensor_capability_registry")
    op.drop_table("tensor_capability_registry")
