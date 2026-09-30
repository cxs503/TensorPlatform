"""
核心 ORM 模型
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, LargeBinary,
    ForeignKey, Enum as SAEnum, JSON, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database.config import Base
import enum
import os


# ========== 枚举 ==========
class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    ENGINEER = "engineer"
    VIEWER = "viewer"

class ProjectStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"

class ModelStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    TRAINING = "TRAINING"
    READY = "READY"
    DEPLOYED = "DEPLOYED"
    DEPRECATED = "DEPRECATED"

class TrainingStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class EmissionScope(str, enum.Enum):
    SCOPE1 = "SCOPE1"
    SCOPE2 = "SCOPE2"
    SCOPE3 = "SCOPE3"

class ComponentStatus(str, enum.Enum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"

class PipelineRunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class PipelineTaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"




# ========== 用户与权限 ==========
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), unique=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    full_name = Column(String(128), nullable=True)
    role = Column(SAEnum(UserRole), default=UserRole.VIEWER, nullable=False)
    is_active = Column(Boolean, default=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    # 允许的功能路径白名单(null/空=不限制; 非空数组=仅允许列出的菜单 path)
    features = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    projects = relationship("Project", back_populates="owner")

    def to_dict(self):
        return {
            "id": self.id, "username": self.username, "email": self.email,
            "full_name": self.full_name, "role": self.role.value,
            "is_active": self.is_active, "organization_id": self.organization_id,
            "features": self.features, "is_admin": (self.role.value == "admin"),
            "created_at": self.created_at.isoformat()
        }


# ========== 项目 ==========
class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(SAEnum(ProjectStatus), default=ProjectStatus.ACTIVE)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="projects")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "status": self.status.value if self.status else None,
            "owner_id": self.owner_id, "created_at": self.created_at.isoformat()
        }


# ========== 数据集 ==========
class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    file_path = Column(String(512), nullable=True)
    size_bytes = Column(Integer, default=0)
    row_count = Column(Integer, default=0)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    stage = Column(String(32), default="raw")
    columns_info = Column(JSON, nullable=True)
    quality_score = Column(Float, nullable=True)
    governed_from = Column(Integer, ForeignKey("datasets.id"), nullable=True)
    data_blob = Column(LargeBinary, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "size_bytes": self.size_bytes, "row_count": self.row_count,
            "project_id": self.project_id, "stage": self.stage,
            "columns_info": self.columns_info,
            "quality_score": self.quality_score,
            "governed_from": self.governed_from,
            "organization_id": self.organization_id,
            # 既支持上传文件，也支持早期直接写入数据库的数据集。
            "has_file": bool((self.file_path and os.path.isfile(self.file_path)) or self.data_blob),
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ========== AI模型 ==========
class ModelFolder(Base):
    """用户创建的模型资产分类文件夹。"""
    __tablename__ = "model_folders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Model(Base):
    __tablename__ = "models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    model_type = Column(String(64), nullable=True)  # classification, regression, forecasting, etc.
    framework = Column(String(64), nullable=True)   # pytorch, tensorflow, sklearn
    status = Column(SAEnum(ModelStatus), default=ModelStatus.DRAFT)
    stage = Column(String(32), default="development")  # development | staging | production | archived
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    folder_id = Column(Integer, ForeignKey("model_folders.id", ondelete="SET NULL"), nullable=True)
    model_blob = Column(LargeBinary, nullable=True)
    # MLflow registered model name(命名约定 foundation_model_{id}),打通 models.id ↔ MLflow Registry 双向映射
    mlflow_name = Column(String(128), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = relationship("ModelVersion", back_populates="model", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "model_type": self.model_type, "framework": self.framework,
            "status": self.status.value if self.status else None,
            "stage": self.stage or "development",
            "project_id": self.project_id,
            "folder_id": self.folder_id,
            "mlflow_name": self.mlflow_name,
            "organization_id": self.organization_id,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("models.id"), nullable=False)
    version_number = Column(String(32), nullable=False)
    file_path = Column(String(512), nullable=True)
    metrics = Column(JSON, nullable=True)  # {"accuracy": 0.95, "f1": 0.93}
    parameters = Column(JSON, nullable=True)
    mlflow_run_id = Column(String(64), nullable=True)
    mlflow_model_version = Column(String(32), nullable=True)
    is_deployed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    model = relationship("Model", back_populates="versions")

    def to_dict(self):
        return {
            "id": self.id, "model_id": self.model_id,
            "version_number": self.version_number,
            "metrics": self.metrics,
            "mlflow_run_id": self.mlflow_run_id,
            "mlflow_model_version": self.mlflow_model_version,
            "is_deployed": self.is_deployed,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ========== 训练任务 ==========
class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    model_id = Column(Integer, ForeignKey("models.id"), nullable=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=True)
    training_job_id = Column(String(64), nullable=True, unique=True)  # Celery task ID
    mlflow_run_id = Column(String(64), nullable=True)
    registered_model_id = Column(Integer, ForeignKey("models.id"), nullable=True)
    status = Column(SAEnum(TrainingStatus), default=TrainingStatus.PENDING)
    hyperparameters = Column(JSONB, nullable=True)
    metrics = Column(JSONB, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "model_id": self.model_id,
            "dataset_id": self.dataset_id,
            "training_job_id": self.training_job_id,
            "mlflow_run_id": self.mlflow_run_id,
            "registered_model_id": self.registered_model_id,
            "status": self.status.value if self.status else None,
            "hyperparameters": self.hyperparameters, "metrics": self.metrics,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "organization_id": self.organization_id,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ========== 碳排放 ==========
class EmissionFactor(Base):
    __tablename__ = "emission_factors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, unique=True)
    factor_value = Column(Float, nullable=False)
    unit = Column(String(32), nullable=False)
    source = Column(String(128), default="IPCC")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "factor_value": self.factor_value, "unit": self.unit,
            "source": self.source
        }


class CarbonEmission(Base):
    __tablename__ = "carbon_emissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(64), nullable=True)
    scope = Column(SAEnum(EmissionScope), nullable=False)
    source = Column(String(64), nullable=False)
    emission_amount = Column(Float, nullable=False)
    unit = Column(String(16), default="tCO2e")
    factor_id = Column(Integer, ForeignKey("emission_factors.id"), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "scope": self.scope.value if self.scope else None,
            "source": self.source, "emission_amount": self.emission_amount,
            "unit": self.unit, "factor_id": self.factor_id,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None
        }


# ========== 光伏系统 ==========
class PhotovoltaicSystem(Base):
    __tablename__ = "photovoltaic_systems"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    capacity_kwp = Column(Float, nullable=False)
    location = Column(String(256), nullable=True)
    status = Column(String(32), default="active")
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    data_points = relationship("PhotovoltaicData", back_populates="system", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "capacity_kwp": self.capacity_kwp, "location": self.location,
            "status": self.status, "project_id": self.project_id,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class PhotovoltaicData(Base):
    __tablename__ = "photovoltaic_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    system_id = Column(Integer, ForeignKey("photovoltaic_systems.id"), nullable=False, index=True)
    power_kw = Column(Float, nullable=False)
    energy_kwh = Column(Float, default=0)
    irradiance_wm2 = Column(Float, nullable=True)
    temperature_c = Column(Float, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)

    system = relationship("PhotovoltaicSystem", back_populates="data_points")

    def to_dict(self):
        return {
            "id": self.id, "system_id": self.system_id,
            "power_kw": self.power_kw, "energy_kwh": self.energy_kwh,
            "irradiance_wm2": self.irradiance_wm2, "temperature_c": self.temperature_c,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None
        }


# ========== 能耗与能效 ==========
class EnergyConsumption(Base):
    __tablename__ = "energy_consumption"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(64), nullable=True)
    energy_type = Column(String(32), nullable=False)  # electricity, gas, coal
    consumption = Column(Float, nullable=False)
    unit = Column(String(16), default="kWh")
    cost_yuan = Column(Float, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "entity_id": self.entity_id,
            "entity_type": self.entity_type, "energy_type": self.energy_type,
            "consumption": self.consumption, "unit": self.unit,
            "cost_yuan": self.cost_yuan,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None
        }


class EnergyEfficiencySystem(Base):
    __tablename__ = "energy_efficiency_systems"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    optimization_type = Column(String(64), nullable=False)  # dust_collector, desulfurization, air_compressor
    location = Column(String(256), nullable=True)
    fan_speed = Column(Float, nullable=True)
    cleaning_interval = Column(Float, nullable=True)
    damper_opening = Column(Float, nullable=True)
    energy_saving_pct = Column(Float, nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "optimization_type": self.optimization_type,
            "location": self.location, "fan_speed": self.fan_speed,
            "cleaning_interval": self.cleaning_interval,
            "damper_opening": self.damper_opening,
            "energy_saving_pct": self.energy_saving_pct,
            "project_id": self.project_id
        }


# ========== 设备与传感器 ==========
class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    device_type = Column(String(64), nullable=False)  # sensor, actuator, controller, gateway
    protocol = Column(String(32), nullable=True)  # modbus, mqtt, opcua
    location = Column(String(256), nullable=True)
    status = Column(String(32), default="offline")
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "device_type": self.device_type,
            "protocol": self.protocol, "location": self.location,
            "status": self.status, "project_id": self.project_id,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None
        }


# ========== 除尘器数据 ==========
class DustCollectorData(Base):
    __tablename__ = "dust_collector_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False, index=True)
    production_load = Column(Float, nullable=True)
    dust_concentration = Column(Float, nullable=True)
    filter_pressure_diff = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    fan_speed = Column(Float, nullable=True)
    energy_consumption_kwh = Column(Float, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id, "entity_id": self.entity_id,
            "production_load": self.production_load,
            "dust_concentration": self.dust_concentration,
            "filter_pressure_diff": self.filter_pressure_diff,
            "temperature": self.temperature,
            "fan_speed": self.fan_speed,
            "energy_consumption_kwh": self.energy_consumption_kwh,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None
        }


# ========== 生产过程数据 ==========
class SinteringData(Base):
    __tablename__ = "sintering_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False, index=True)
    machine_speed = Column(Float, nullable=True)
    bed_thickness = Column(Float, nullable=True)
    permeability = Column(Float, nullable=True)
    pressure1 = Column(Float, nullable=True)
    pressure2 = Column(Float, nullable=True)
    temp15 = Column(Float, nullable=True)
    temp16 = Column(Float, nullable=True)
    temp19 = Column(Float, nullable=True)
    end_temp = Column(Float, nullable=True)
    carbon_ratio = Column(Float, nullable=True)
    cooling_slope = Column(Float, nullable=True)
    feo_content = Column(Float, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id, "entity_id": self.entity_id,
            "machine_speed": self.machine_speed,
            "bed_thickness": self.bed_thickness,
            "permeability": self.permeability,
            "pressure1": self.pressure1, "pressure2": self.pressure2,
            "temp15": self.temp15, "temp16": self.temp16,
            "temp19": self.temp19, "end_temp": self.end_temp,
            "carbon_ratio": self.carbon_ratio,
            "cooling_slope": self.cooling_slope,
            "feo_content": self.feo_content,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None
        }


# ========== API密钥 ==========
class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    key_hash = Column(String(256), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id,
            "key_hash": self.key_hash, "name": self.name,
            "is_active": self.is_active,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ========== 告警 ==========
class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    severity = Column(String(32), nullable=False)  # info, warning, critical
    source = Column(String(64), nullable=True)
    entity_id = Column(String(64), nullable=True)
    message = Column(Text, nullable=True)
    is_resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "severity": self.severity,
            "source": self.source, "entity_id": self.entity_id,
            "message": self.message, "is_resolved": self.is_resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ========== 告警规则 ==========
class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    metric = Column(String(64), nullable=False)
    threshold = Column(Float, nullable=False)
    operator = Column(String(16), nullable=False, default=">")
    severity = Column(String(32), nullable=False, default="warning")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "metric": self.metric,
            "threshold": self.threshold, "operator": self.operator,
            "severity": self.severity, "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ========== 钢铁设备 ==========
class SteelEquipment(Base):
    __tablename__ = "steel_equipment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    type = Column(String(64), nullable=False)
    process = Column(String(64), nullable=True)
    capacity = Column(Float, nullable=True)
    location = Column(String(128), nullable=True)
    health_score = Column(Float, default=100.0)
    status = Column(String(32), default="running")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "process": self.process, "capacity": self.capacity,
            "location": self.location, "health_score": self.health_score,
            "status": self.status,
        }


# ========== 能源介质 ==========
class EnergyMedia(Base):
    __tablename__ = "energy_media"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    type = Column(String(64), nullable=False)
    unit = Column(String(32), nullable=False)
    price_per_unit = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "unit": self.unit, "price_per_unit": self.price_per_unit,
        }


# ========== 知识图谱规则 ==========
class KnowledgeRule(Base):
    __tablename__ = "knowledge_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(128), nullable=False, index=True)
    rule = Column(String(256), nullable=False)
    cause = Column(String(512), nullable=True)
    action = Column(String(512), nullable=True)
    confidence = Column(Float, default=0.8)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "domain": self.domain,
            "rule": self.rule,
            "cause": self.cause,
            "action": self.action,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

# ========== 组织/租户 ==========
class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    industry = Column(String(64), nullable=True)
    plan = Column(String(32), default="free")
    contact_email = Column(String(128), nullable=True)
    status = Column(String(32), default="active")
    workspace = Column(String(32), default="personal", nullable=False)  # personal / team / public
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "industry": self.industry, "plan": self.plan,
            "contact_email": self.contact_email, "status": self.status,
            "workspace": self.workspace,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ========== API使用日志 ==========
class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_endpoint = Column(String(256), nullable=False)
    caller_id = Column(String(128), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    response_time_ms = Column(Float, default=0.0)
    status_code = Column(Integer, default=200)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "api_endpoint": self.api_endpoint,
            "caller_id": self.caller_id,
            "organization_id": self.organization_id,
            "response_time_ms": self.response_time_ms,
            "status_code": self.status_code,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ========== 数据产品 ==========
class DataProduct(Base):
    __tablename__ = "data_products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    category = Column(String(64), nullable=True)
    description = Column(Text, nullable=True)
    format = Column(String(32), nullable=True)
    size_desc = Column(String(64), nullable=True)
    price = Column(Float, default=0.0)
    status = Column(String(32), default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "category": self.category,
            "description": self.description,
            "format": self.format, "size_desc": self.size_desc,
            "price": self.price, "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

# ========== 算法组件 ==========
class Component(Base):
    __tablename__ = "components"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, index=True)
    type = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    input_schema = Column(JSON, nullable=True)
    output_schema = Column(JSON, nullable=True)
    parameters = Column(JSON, nullable=True)
    status = Column(SAEnum(ComponentStatus), default=ComponentStatus.DRAFT)
    workspace_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "parameters": self.parameters,
            "status": self.status.value if self.status else None,
            "workspace_id": self.workspace_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ========== 计费 / 定价 ==========
class PricingModel(str, enum.Enum):
    FREE = "free"
    SUBSCRIPTION = "subscription"
    PER_USE = "per_use"
    REVENUE_SHARE = "revenue_share"


class PricingConfig(Base):
    __tablename__ = "pricing_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    component_id = Column(Integer, ForeignKey("components.id"), unique=True, nullable=False)
    model_type = Column(String(64), nullable=False)
    price_per_call = Column(Float, default=0.0)
    subscription_monthly = Column(Float, default=0.0)
    revenue_share_pct = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "component_id": self.component_id,
            "model_type": self.model_type,
            "price_per_call": self.price_per_call,
            "subscription_monthly": self.subscription_monthly,
            "revenue_share_pct": self.revenue_share_pct,
        }


class BillingRecord(Base):
    __tablename__ = "billing_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    component_id = Column(Integer, ForeignKey("components.id"), nullable=True)
    pricing_model = Column(SAEnum(PricingModel), default=PricingModel.FREE, nullable=False)
    unit_price = Column(Float, default=0.0)
    quantity = Column(Float, default=1.0)
    total_amount = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "org_id": self.org_id,
            "component_id": self.component_id,
            "pricing_model": self.pricing_model.value if self.pricing_model else None,
            "unit_price": self.unit_price,
            "quantity": self.quantity,
            "total_amount": self.total_amount,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ==================== 算法商店 ====================

class AlgorithmModel(Base):
    __tablename__ = "algorithm_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="算法名称")
    category = Column(String(100), nullable=False, comment="分类")
    subcategory = Column(String(100), comment="子分类")
    description = Column(Text, comment="算法描述")
    algorithm = Column(String(100), comment="算法类型")
    framework = Column(String(50), default="PyTorch", comment="框架")
    difficulty = Column(String(20), default="beginner", comment="难度")
    version = Column(String(20), default="1.0.0", comment="版本")
    status = Column(String(20), default="published", comment="状态: draft/published/unpublished")
    downloads = Column(Integer, default=0, comment="下载量")
    input_features = Column(JSONB, default=list, comment="输入特征列表")
    output_type = Column(String(50), comment="输出类型")
    metrics = Column(JSONB, default=dict, comment="性能指标")
    use_case = Column(String(500), comment="应用场景")
    tags = Column(JSONB, default=list, comment="标签")
    entry_point = Column(String(200), comment="入口文件")
    params_schema = Column(Text, comment="参数schema JSON")
    estimated_training_time = Column(String(50), comment="预估训练时间")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "subcategory": self.subcategory,
            "description": self.description,
            "algorithm": self.algorithm,
            "framework": self.framework,
            "difficulty": self.difficulty,
            "version": self.version,
            "status": self.status,
            "downloads": self.downloads,
            "input_features": self.input_features or [],
            "output_type": self.output_type,
            "metrics": self.metrics or {},
            "use_case": self.use_case,
            "tags": self.tags or [],
            "entry_point": self.entry_point,
            "params_schema": self.params_schema,
            "estimated_training_time": self.estimated_training_time,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ========== AutoML 超参预设 ==========
class AlgorithmHyperparamPreset(Base):
    """AutoML 算法超参预设：把「内置算法 + 一套超参」存成命名档案，供对比复用。"""
    __tablename__ = "algorithm_hyperparam_presets"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", "algorithm", "task_type",
                         name="uq_preset_org_name_algo_task"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    description = Column(String(500), nullable=True)
    task_type = Column(String(32), nullable=False)  # classification/regression/time_series
    algorithm = Column(String(64), nullable=False)  # random_forest 等内置 key
    params = Column(JSONB, default=dict)            # {"n_estimators": 300, ...}
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_by = Column(Integer, nullable=True)
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "task_type": self.task_type, "algorithm": self.algorithm,
            "params": self.params or {}, "organization_id": self.organization_id,
            "created_by": self.created_by, "is_builtin": self.is_builtin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ========== Pipeline 流水线 ==========
class Pipeline(Base):
    __tablename__ = "pipelines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    trigger_type = Column(String(32), default="manual")
    cron_expression = Column(String(64), nullable=True)
    status = Column(String(32), default="draft")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tasks = relationship("PipelineTask", back_populates="pipeline", cascade="all, delete-orphan",
                         order_by="PipelineTask.order_index")
    runs = relationship("PipelineRun", back_populates="pipeline", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "trigger_type": self.trigger_type, "cron_expression": self.cron_expression,
            "status": self.status, "owner_id": self.owner_id,
            "organization_id": self.organization_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "tasks": [t.to_dict() for t in self.tasks] if self.tasks else [],
        }


class PipelineTask(Base):
    __tablename__ = "pipeline_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(Integer, ForeignKey("pipelines.id"), nullable=False)
    name = Column(String(255), nullable=False)
    task_type = Column(String(64), nullable=False)
    command = Column(Text, nullable=True)
    config = Column(JSON, default=dict)
    dependencies = Column(JSON, default=list)  # list of task names
    order_index = Column(Integer, default=0)
    timeout = Column(Integer, default=3600)
    retry = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    pipeline = relationship("Pipeline", back_populates="tasks")
    task_runs = relationship("PipelineTaskRun", back_populates="task", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "pipeline_id": self.pipeline_id,
            "name": self.name, "task_type": self.task_type,
            "command": self.command, "config": self.config or {},
            "dependencies": self.dependencies or [],
            "order_index": self.order_index, "timeout": self.timeout, "retry": self.retry,
        }


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_id = Column(Integer, ForeignKey("pipelines.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    status = Column(String(32), default="PENDING")
    triggered_by = Column(String(64), default="system")
    parameters = Column(JSON, default=dict)
    validation_context = Column(JSON, default=dict)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    pipeline = relationship("Pipeline", back_populates="runs")
    task_runs = relationship("PipelineTaskRun", back_populates="pipeline_run", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "pipeline_id": self.pipeline_id,
            "organization_id": self.organization_id,
            "status": self.status, "triggered_by": self.triggered_by,
            "parameters": self.parameters or {},
            "validation_context": self.validation_context or {},
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PipelineTaskRun(Base):
    __tablename__ = "pipeline_task_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("pipeline_tasks.id"), nullable=False)
    task_name = Column(String(255), nullable=False)
    task_type = Column(String(64), nullable=False)
    status = Column(String(32), default="PENDING")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    logs = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    outputs = Column(JSON, default=dict)
    celery_task_id = Column(String(128), nullable=True)

    pipeline_run = relationship("PipelineRun", back_populates="task_runs")
    task = relationship("PipelineTask", back_populates="task_runs")

    def to_dict(self):
        return {
            "id": self.id, "pipeline_run_id": self.pipeline_run_id,
            "task_id": self.task_id, "task_name": self.task_name,
            "task_type": self.task_type, "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "logs": self.logs, "error": self.error,
            "outputs": self.outputs or {},
        }
