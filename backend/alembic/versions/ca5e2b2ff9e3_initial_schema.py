"""initial_schema

Revision ID: ca5e2b2ff9e3
Revises: 
Create Date: 2026-06-16 15:21:17.918625

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

    # ========== 碳排放因子 ==========
    op.create_table(
        'emission_factors',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('factor_value', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(32), nullable=False),
        sa.Column('source', sa.String(128), default='IPCC'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    # ========== 碳排放记录 ==========
    op.create_table(
        'carbon_emissions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.String(64), nullable=False),
        sa.Column('entity_type', sa.String(64), nullable=True),
        sa.Column('scope', sa.Enum('SCOPE1', 'SCOPE2', 'SCOPE3', name='emissionscope'), nullable=False),
        sa.Column('source', sa.String(64), nullable=False),
        sa.Column('emission_amount', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(16), default='tCO2e'),
        sa.Column('factor_id', sa.Integer(), sa.ForeignKey('emission_factors.id'), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_carbon_emissions_entity_id'), 'carbon_emissions', ['entity_id'], unique=False)

    # ========== 光伏系统 ==========
    op.create_table(
        'photovoltaic_systems',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('capacity_kwp', sa.Float(), nullable=False),
        sa.Column('location', sa.String(256), nullable=True),
        sa.Column('status', sa.String(32), default='active'),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ========== 光伏数据 ==========
    op.create_table(
        'photovoltaic_data',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('system_id', sa.Integer(), sa.ForeignKey('photovoltaic_systems.id'), nullable=False),
        sa.Column('power_kw', sa.Float(), nullable=False),
        sa.Column('energy_kwh', sa.Float(), default=0),
        sa.Column('irradiance_wm2', sa.Float(), nullable=True),
        sa.Column('temperature_c', sa.Float(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_photovoltaic_data_recorded_at'), 'photovoltaic_data', ['recorded_at'], unique=False)
    op.create_index(op.f('ix_photovoltaic_data_system_id'), 'photovoltaic_data', ['system_id'], unique=False)

    # ========== 能耗 ==========
    op.create_table(
        'energy_consumption',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.String(64), nullable=False),
        sa.Column('entity_type', sa.String(64), nullable=True),
        sa.Column('energy_type', sa.String(32), nullable=False),
        sa.Column('consumption', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(16), default='kWh'),
        sa.Column('cost_yuan', sa.Float(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_energy_consumption_entity_id'), 'energy_consumption', ['entity_id'], unique=False)
    op.create_index(op.f('ix_energy_consumption_recorded_at'), 'energy_consumption', ['recorded_at'], unique=False)

    # ========== 能效系统 ==========
    op.create_table(
        'energy_efficiency_systems',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('optimization_type', sa.String(64), nullable=False),
        sa.Column('location', sa.String(256), nullable=True),
        sa.Column('fan_speed', sa.Float(), nullable=True),
        sa.Column('cleaning_interval', sa.Float(), nullable=True),
        sa.Column('damper_opening', sa.Float(), nullable=True),
        sa.Column('energy_saving_pct', sa.Float(), nullable=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
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

    # ========== 除尘器数据 ==========
    op.create_table(
        'dust_collector_data',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.String(64), nullable=False),
        sa.Column('production_load', sa.Float(), nullable=True),
        sa.Column('dust_concentration', sa.Float(), nullable=True),
        sa.Column('filter_pressure_diff', sa.Float(), nullable=True),
        sa.Column('temperature', sa.Float(), nullable=True),
        sa.Column('fan_speed', sa.Float(), nullable=True),
        sa.Column('energy_consumption_kwh', sa.Float(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_dust_collector_data_entity_id'), 'dust_collector_data', ['entity_id'], unique=False)
    op.create_index(op.f('ix_dust_collector_data_recorded_at'), 'dust_collector_data', ['recorded_at'], unique=False)

    # ========== 生产过程数据 ==========
    op.create_table(
        'sintering_data',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.String(64), nullable=False),
        sa.Column('machine_speed', sa.Float(), nullable=True),
        sa.Column('bed_thickness', sa.Float(), nullable=True),
        sa.Column('permeability', sa.Float(), nullable=True),
        sa.Column('pressure1', sa.Float(), nullable=True),
        sa.Column('pressure2', sa.Float(), nullable=True),
        sa.Column('temp15', sa.Float(), nullable=True),
        sa.Column('temp16', sa.Float(), nullable=True),
        sa.Column('temp19', sa.Float(), nullable=True),
        sa.Column('end_temp', sa.Float(), nullable=True),
        sa.Column('carbon_ratio', sa.Float(), nullable=True),
        sa.Column('cooling_slope', sa.Float(), nullable=True),
        sa.Column('feo_content', sa.Float(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sintering_data_entity_id'), 'sintering_data', ['entity_id'], unique=False)
    op.create_index(op.f('ix_sintering_data_recorded_at'), 'sintering_data', ['recorded_at'], unique=False)

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
    op.drop_index(op.f('ix_sintering_data_recorded_at'), table_name='sintering_data')
    op.drop_index(op.f('ix_sintering_data_entity_id'), table_name='sintering_data')
    op.drop_table('sintering_data')
    op.drop_index(op.f('ix_dust_collector_data_recorded_at'), table_name='dust_collector_data')
    op.drop_index(op.f('ix_dust_collector_data_entity_id'), table_name='dust_collector_data')
    op.drop_table('dust_collector_data')
    op.drop_table('devices')
    op.drop_table('energy_efficiency_systems')
    op.drop_index(op.f('ix_energy_consumption_recorded_at'), table_name='energy_consumption')
    op.drop_index(op.f('ix_energy_consumption_entity_id'), table_name='energy_consumption')
    op.drop_table('energy_consumption')
    op.drop_index(op.f('ix_photovoltaic_data_system_id'), table_name='photovoltaic_data')
    op.drop_index(op.f('ix_photovoltaic_data_recorded_at'), table_name='photovoltaic_data')
    op.drop_table('photovoltaic_data')
    op.drop_table('photovoltaic_systems')
    op.drop_index(op.f('ix_carbon_emissions_entity_id'), table_name='carbon_emissions')
    op.drop_table('carbon_emissions')
    op.drop_table('emission_factors')
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
    op.execute('DROP TYPE IF EXISTS emissionscope')
