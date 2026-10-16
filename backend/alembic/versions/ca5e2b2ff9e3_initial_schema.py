"""initial_schema

Revision ID: ca5e2b2ff9e3
Revises: 
Create Date: 2026-06-16 15:21:17.918625

注：2026-10 共性平台收敛——移除了行业专用表（碳排放因子/碳排放记录/光伏系统/
光伏数据/能耗/能效系统/除尘器数据/烧结生产过程数据）及其枚举；保留的均为通用
平台基表。平台的正式 schema 演进以 migrations/ 目录为准，本 alembic 树为并行
快照机制。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ca5e2b2ff9e3'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ========== 用户与权限 ==========
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('email', sa.String(128), nullable=False),
        sa.Column('hashed_password', sa.String(256), nullable=False),
        sa.Column('full_name', sa.String(128), nullable=True),
        sa.Column('role', sa.Enum('ADMIN', 'MANAGER', 'ENGINEER', 'VIEWER', name='userrole'), nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username'),
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    # ========== 项目 ==========
    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'PAUSED', 'COMPLETED', 'ARCHIVED', name='projectstatus'), nullable=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ========== 数据集 ==========
    op.create_table(
        'datasets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('file_path', sa.String(512), nullable=True),
        sa.Column('size_bytes', sa.Integer(), default=0),
        sa.Column('row_count', sa.Integer(), default=0),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('stage', sa.String(32), default='raw'),
        sa.Column('columns_info', sa.JSON(), nullable=True),
        sa.Column('quality_score', sa.Float(), nullable=True),
        sa.Column('governed_from', sa.Integer(), sa.ForeignKey('datasets.id'), nullable=True),
        sa.Column('data_blob', sa.LargeBinary(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ========== API密钥 ==========
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('key_hash', sa.String(256), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash'),
    )

    # ========== AI模型 ==========
    op.create_table(
        'models',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('model_type', sa.String(64), nullable=True),
        sa.Column('framework', sa.String(64), nullable=True),
        sa.Column('status', sa.Enum('DRAFT', 'TRAINING', 'READY', 'DEPLOYED', 'DEPRECATED', name='modelstatus'), nullable=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('model_blob', sa.LargeBinary(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ========== 模型版本 ==========
    op.create_table(
        'model_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('model_id', sa.Integer(), sa.ForeignKey('models.id'), nullable=False),
        sa.Column('version_number', sa.String(32), nullable=False),
        sa.Column('file_path', sa.String(512), nullable=True),
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.Column('parameters', sa.JSON(), nullable=True),
        sa.Column('is_deployed', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ========== 训练任务 ==========
    op.create_table(
        'training_jobs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('model_id', sa.Integer(), sa.ForeignKey('models.id'), nullable=True),
        sa.Column('dataset_id', sa.Integer(), sa.ForeignKey('datasets.id'), nullable=True),
        sa.Column('training_job_id', sa.String(64), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', name='trainingstatus'), nullable=True),
        sa.Column('hyperparameters', sa.JSON(), nullable=True),
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('training_job_id'),
    )

    # ========== 设备与传感器 ==========
    op.create_table(
        'devices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('device_type', sa.String(64), nullable=False),
        sa.Column('protocol', sa.String(32), nullable=True),
        sa.Column('location', sa.String(256), nullable=True),
        sa.Column('status', sa.String(32), default='offline'),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ========== 告警 ==========
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(256), nullable=False),
        sa.Column('severity', sa.String(32), nullable=False),
        sa.Column('source', sa.String(64), nullable=True),
        sa.Column('entity_id', sa.String(64), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('is_resolved', sa.Boolean(), default=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('alerts')
    op.drop_table('devices')
    op.drop_table('training_jobs')
    op.drop_table('model_versions')
    op.drop_table('models')
    op.drop_table('api_keys')
    op.drop_table('datasets')
    op.drop_table('projects')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')

    # Drop enum types
    op.execute('DROP TYPE IF EXISTS userrole')
    op.execute('DROP TYPE IF EXISTS projectstatus')
    op.execute('DROP TYPE IF EXISTS modelstatus')
    op.execute('DROP TYPE IF EXISTS trainingstatus')
