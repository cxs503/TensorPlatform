"""
开放数字化底座 - Pydantic Schemas
Open Digital Foundation - Pydantic Schemas
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field

# ==================== 枚举定义 ====================


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModelType(str, Enum):
    """模型类型枚举"""

    ENERGY_OPTIMIZATION = "energy_optimization"
    FAULT_PREDICTION = "fault_prediction"
    INTELLIGENT_CONTROL = "intelligent_control"
    TIME_SERIES_FORECAST = "time_series_forecast"
    IMAGE_RECOGNITION = "image_recognition"
    CUSTOM = "custom"


class AlgorithmType(str, Enum):
    """算法类型枚举"""

    LSTM = "lstm"
    GRU = "gru"
    TRANSFORMER = "transformer"
    XGBOOST = "xgboost"
    LIGHTGBM = "lightgbm"
    RANDOM_FOREST = "random_forest"
    MPC = "mpc"
    FUZZY_CONTROL = "fuzzy_control"
    REINFORCEMENT_LEARNING = "reinforcement_learning"


# ==================== 用户 Schemas ====================


class UserBase(BaseModel):
    """用户基础Schema"""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    """用户创建Schema"""

    password: str = Field(..., min_length=6)


class UserCreateFull(UserBase):
    """用户创建Schema"""

    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    """用户更新Schema"""

    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    password: Optional[str] = Field(None, min_length=6)


class UserResponse(UserBase):
    """用户响应Schema"""

    id: str
    is_active: bool
    is_superuser: bool
    role: str
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    """Token响应Schema"""

    access_token: str
    token_type: str


class TokenData(BaseModel):
    """Token数据Schema"""

    username: Optional[str] = None


class LoginRequest(BaseModel):
    """登录请求Schema"""

    username: str
    password: str


# ==================== 项目 Schemas ====================


class ProjectBase(BaseModel):
    """项目基础Schema"""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    project_type: str
    tags: List[str] = []


class ProjectCreate(ProjectBase):
    """项目创建Schema"""

    pass


class ProjectUpdate(BaseModel):
    """项目更新Schema"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    project_type: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


class ProjectResponse(ProjectBase):
    """项目响应Schema"""

    id: str
    status: str
    owner_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    """项目列表响应Schema"""

    total: int
    page: int
    size: int
    items: List[ProjectResponse]


# ==================== 数据集 Schemas ====================


class DatasetBase(BaseModel):
    """数据集基础Schema"""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    data_type: str
    source_type: str
    schema: Optional[Dict[str, Any]] = {}


class DatasetCreate(DatasetBase):
    """数据集创建Schema"""

    project_id: str


class DatasetUpdate(BaseModel):
    """数据集更新Schema"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    schema: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


class DatasetResponse(DatasetBase):
    """数据集响应Schema"""

    id: str
    record_count: int
    storage_size: int
    status: str
    project_id: str
    owner_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DatasetListResponse(BaseModel):
    """数据集列表响应Schema"""

    total: int
    page: int
    size: int
    items: List[DatasetResponse]


class DatasetUploadResponse(BaseModel):
    """数据集上传响应Schema"""

    message: str
    dataset_id: str
    filename: str


class DatasetPreviewResponse(BaseModel):
    """数据集预览响应Schema"""

    data: List[Dict[str, Any]]
    total: int


# ==================== 模型 Schemas ====================


class ModelBase(BaseModel):
    """模型基础Schema"""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    model_type: ModelType
    algorithm_type: AlgorithmType
    hyperparameters: Dict[str, Any] = {}


class ModelCreate(ModelBase):
    """模型创建Schema"""

    project_id: str
    dataset_id: Optional[str] = None


class ModelUpdate(BaseModel):
    """模型更新Schema"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    hyperparameters: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


class ModelResponse(ModelBase):
    """模型响应Schema"""

    id: str
    version: str
    status: str
    metrics: Optional[Dict[str, Any]]
    project_id: str
    dataset_id: Optional[str]
    owner_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ModelListResponse(BaseModel):
    """模型列表响应Schema"""

    total: int
    page: int
    size: int
    items: List[ModelResponse]


# ==================== 训练任务 Schemas ====================


class TrainingJobBase(BaseModel):
    """训练任务基础Schema"""

    training_config: Dict[str, Any]
    resource_config: Optional[Dict[str, Any]] = {}


class TrainingJobCreate(TrainingJobBase):
    """训练任务创建Schema"""

    model_id: str
    dataset_id: str


class TrainingJobResponse(TrainingJobBase):
    """训练任务响应Schema"""

    id: str
    model_id: str
    dataset_id: str
    status: TaskStatus
    progress: float
    metrics: Optional[Dict[str, Any]]
    logs: List[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    owner_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class TrainingJobListResponse(BaseModel):
    """训练任务列表响应Schema"""

    total: int
    page: int
    size: int
    items: List[TrainingJobResponse]


class TrainingLogsResponse(BaseModel):
    """训练日志响应Schema"""

    logs: List[str]


# ==================== 部署 Schemas ====================


class DeploymentBase(BaseModel):
    """部署基础Schema"""

    deployment_type: str
    resource_config: Dict[str, Any]
    scaling_config: Optional[Dict[str, Any]] = {}


class DeploymentCreate(DeploymentBase):
    """部署创建Schema"""

    model_id: str
    version: str


class DeploymentUpdate(BaseModel):
    """部署更新Schema"""

    status: Optional[str] = None
    resource_config: Optional[Dict[str, Any]] = None
    scaling_config: Optional[Dict[str, Any]] = None


class DeploymentResponse(DeploymentBase):
    """部署响应Schema"""

    id: str
    model_id: str
    version: str
    status: str
    endpoint: Optional[str]
    resource_usage: Dict[str, Any]
    owner_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DeploymentListResponse(BaseModel):
    """部署列表响应Schema"""

    total: int
    page: int
    size: int
    items: List[DeploymentResponse]


# ==================== 推理 Schemas ====================


class InferenceRequest(BaseModel):
    """推理请求Schema"""

    data: Any = Field(..., description="输入数据")
    return_explanation: bool = False


class InferenceResponse(BaseModel):
    """推理响应Schema"""

    deployment_id: str
    predictions: List[Dict[str, Any]]
    explanations: Optional[List[Dict[str, Any]]]
    inference_time: float
    timestamp: datetime


# ==================== Pipeline Schemas ====================


class PipelineBase(BaseModel):
    """Pipeline基础Schema"""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []


class PipelineCreate(PipelineBase):
    """Pipeline创建Schema"""

    project_id: str


class PipelineUpdate(BaseModel):
    """Pipeline更新Schema"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    status: Optional[str] = None


class PipelineResponse(PipelineBase):
    """Pipeline响应Schema"""

    id: str
    status: str
    project_id: str
    owner_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PipelineListResponse(BaseModel):
    """Pipeline列表响应Schema"""

    total: int
    page: int
    size: int
    items: List[PipelineResponse]


class PipelineRunResponse(BaseModel):
    """Pipeline运行响应Schema"""

    message: str
    pipeline_id: str


# ==================== 仪表盘 Schemas ====================


class DashboardStatistics(BaseModel):
    """仪表盘统计Schema"""

    projects: Dict[str, int]
    datasets: Dict[str, Any]
    models: Dict[str, int]
    training_jobs: Dict[str, int]
    inference: Dict[str, Any]


class ActivityItem(BaseModel):
    """活动项Schema"""

    type: str
    title: str
    timestamp: datetime
    user: str


class RecentActivitiesResponse(BaseModel):
    """最近活动响应Schema"""

    activities: List[ActivityItem]


# ==================== 通用响应 Schemas ====================


class MessageResponse(BaseModel):
    """消息响应Schema"""

    message: str


class ErrorResponse(BaseModel):
    """错误响应Schema"""

    detail: str


class HealthCheckResponse(BaseModel):
    """健康检查响应Schema"""

    status: str
    timestamp: str
    services: Dict[str, str]


class RootResponse(BaseModel):
    """根路径响应Schema"""

    message: str
    version: str
    docs: str
    features: List[str]
