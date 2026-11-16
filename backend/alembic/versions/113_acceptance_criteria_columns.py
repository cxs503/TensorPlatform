"""acceptance criteria freeze columns on tensor_dev_missions

Revision ID: 113_acceptance_criteria_columns
Revises: 112_tensorbench_eval_loop
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "113_acceptance_criteria_columns"
down_revision = "112_tensorbench_eval_loop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tensor_dev_missions", sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=True))
    op.add_column("tensor_dev_missions", sa.Column("acceptance_criteria_sha256", sa.Text(), nullable=True))
    op.add_column("tensor_dev_missions", sa.Column("acceptance_criteria_frozen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tensor_dev_missions", sa.Column("acceptance_criteria_freeze_source", sa.Text(), nullable=True))
    op.add_column("tensor_dev_missions", sa.Column("acceptance_criteria_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("tensor_dev_missions", "acceptance_criteria_version")
    op.drop_column("tensor_dev_missions", "acceptance_criteria_freeze_source")
    op.drop_column("tensor_dev_missions", "acceptance_criteria_frozen_at")
    op.drop_column("tensor_dev_missions", "acceptance_criteria_sha256")
    op.drop_column("tensor_dev_missions", "acceptance_criteria")
