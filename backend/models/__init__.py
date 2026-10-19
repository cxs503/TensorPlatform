"""
数据模型包
所有核心 ORM 模型定义在 core.py 中
"""
# 导入核心模型（唯一定义源）
# 平台模型保持领域中立。
from models.core import (
    User, Project, Dataset, Model, ModelVersion, TrainingJob,
    Device, Alert,
    Organization, ApiUsageLog, DataProduct,
    UserRole, ProjectStatus, ModelStatus, TrainingStatus,
    PricingModel, BillingRecord, PricingConfig,
)

# 数据目录模型（data_assets 等表；迁移 017-019 与 api/curated_datasets.py 依赖）
from models.data_catalog import (
    DataAsset, AssetMetadata, DataClassification, DataTag, DataLineage, DataCatalogStats,
)

# 数据血缘模型（lineage_nodes 等表；迁移 010/031/032 与 api/data_lineage.py 依赖）
from models.lineage import LineageNode, LineageEdge, LineageGraph, LineageChangeLog

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
