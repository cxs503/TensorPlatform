"""
数据模型包
所有核心 ORM 模型定义在 core.py 中
"""
# 导入核心模型（唯一定义源）
from models.core import (
    User, Project, Dataset, Model, ModelVersion, TrainingJob,
    EmissionFactor, CarbonEmission,
    PhotovoltaicSystem, PhotovoltaicData,
    EnergyConsumption, EnergyEfficiencySystem,
    Device, Alert,
    Organization, ApiUsageLog, DataProduct,
    UserRole, ProjectStatus, ModelStatus, TrainingStatus, EmissionScope,
    PricingModel, BillingRecord, PricingConfig,
)

# 可选：数据安全模型
try:
    from models.data_security import (
        AccessAudit, AccessPolicy, DataSecurityPolicy, MaskingRule, PolicyRule,
    )
except ImportError:
    pass

# 可选：数据标准模型
try:
    from models.data_standard import DataStandard, DataValidationResult, StandardField
except ImportError:
    pass

# 可选：标签管理与数据标注模型
try:
    from models.labeling import (
        Annotation,
        AnnotationTask,
        Label,
        LabelGroup,
        LabelGroupItem,
    )
except ImportError:
    pass
