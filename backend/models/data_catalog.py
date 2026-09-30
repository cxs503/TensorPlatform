"""
数据目录模块 - 数据模型
支持元数据发现、分类标签、血缘追踪
"""
from datetime import datetime

from database.config import Base
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship

# 数据资产与标签的多对多关联表
asset_tags = Table(
    "asset_tags",
    Base.metadata,
    Column(
        "asset_id",
        Integer,
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        Integer,
        ForeignKey("data_tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# 数据资产与分类的多对多关联表
asset_classifications = Table(
    "asset_classifications",
    Base.metadata,
    Column(
        "asset_id",
        Integer,
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "classification_id",
        Integer,
        ForeignKey("data_classifications.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class DataAsset(Base):
    """数据资产表"""

    __tablename__ = "data_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, comment="资产名称")
    type = Column(
        String(50),
        nullable=False,
        comment="资产类型: table, file, api, model, dataset, stream",
    )
    description = Column(Text, comment="资产描述")
    owner = Column(String(100), comment="资产所有者")
    tags = Column(JSON, default=list, comment="标签列表")
    sensitivity_level = Column(
        String(20),
        default="normal",
        comment="敏感级别: public, internal, confidential, restricted",
    )
    quality_score = Column(Integer, default=0, comment="数据质量得分 0-100")
    schema_info = Column(JSON, comment="数据结构信息")
    storage_info = Column(JSON, comment="存储信息: location, format, size")
    source = Column(String(255), comment="数据来源")
    status = Column(
        String(20), default="active", comment="状态: active, archived, deprecated"
    )
    version = Column(String(20), default="1.0.0", comment="版本号")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
    user_id = Column(Integer, comment="创建者ID")

    # 关联关系
    metadata_entries = relationship(
        "AssetMetadata", back_populates="asset", cascade="all, delete-orphan"
    )
    lineage_upstream = relationship(
        "DataLineage",
        foreign_keys="DataLineage.target_id",
        back_populates="target_asset",
    )
    lineage_downstream = relationship(
        "DataLineage",
        foreign_keys="DataLineage.source_id",
        back_populates="source_asset",
    )
    tag_objects = relationship("DataTag", secondary=asset_tags, back_populates="assets")
    classification_objects = relationship(
        "DataClassification", secondary=asset_classifications, back_populates="assets"
    )


class AssetMetadata(Base):
    """元数据表"""

    __tablename__ = "asset_metadata"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(
        Integer,
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        nullable=False,
        comment="资产ID",
    )
    key = Column(String(100), nullable=False, comment="元数据键")
    value = Column(Text, comment="元数据值")
    source = Column(String(50), default="manual", comment="来源: manual, auto, system")
    confidence = Column(Float, default=1.0, comment="置信度 0-1")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    # 关联关系
    asset = relationship("DataAsset", back_populates="metadata_entries")


class DataClassification(Base):
    """数据分类表"""

    __tablename__ = "data_classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, comment="分类名称")
    description = Column(Text, comment="分类描述")
    color = Column(String(20), default="#409EFF", comment="显示颜色")
    icon = Column(String(50), comment="图标名称")
    rules = Column(JSON, default=list, comment="分类规则")
    parent_id = Column(Integer, ForeignKey("data_classifications.id"), comment="父分类ID")
    sort_order = Column(Integer, default=0, comment="排序顺序")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    # 关联关系
    assets = relationship(
        "DataAsset",
        secondary=asset_classifications,
        back_populates="classification_objects",
    )
    children = relationship("DataClassification", backref="parent", remote_side=[id])


class DataTag(Base):
    """数据标签表"""

    __tablename__ = "data_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, comment="标签名称")
    color = Column(String(20), default="#909399", comment="显示颜色")
    category = Column(
        String(50),
        default="general",
        comment="标签类别: general, business, technical, security",
    )
    description = Column(Text, comment="标签描述")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    # 关联关系
    assets = relationship(
        "DataAsset", secondary=asset_tags, back_populates="tag_objects"
    )


# 注：血缘关系已独立到 models/lineage.py (LineageNode, LineageEdge)
# DataLineage 类在此保留用于 data_catalog 模块的资产关联血缘


class DataLineage(Base):
    """数据资产血缘关系表（简化版，用于资产间关系）"""

    __tablename__ = "data_asset_lineage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(
        Integer,
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        nullable=False,
        comment="源资产ID",
    )
    target_id = Column(
        Integer,
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        nullable=False,
        comment="目标资产ID",
    )
    lineage_type = Column(
        String(50),
        default="derives",
        comment="关系类型: derives, transforms, joins, filters",
    )
    transform_logic = Column(Text, comment="转换逻辑描述")
    column_mapping = Column(JSON, comment="字段映射关系")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    created_by = Column(Integer, comment="创建者ID")

    # 关联关系
    source_asset = relationship(
        "DataAsset", foreign_keys=[source_id], back_populates="lineage_downstream"
    )
    target_asset = relationship(
        "DataAsset", foreign_keys=[target_id], back_populates="lineage_upstream"
    )


class DataCatalogStats(Base):
    """数据目录统计表"""

    __tablename__ = "data_catalog_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, nullable=False, comment="统计日期")
    total_assets = Column(Integer, default=0, comment="总资产数")
    assets_by_type = Column(JSON, default=dict, comment="按类型统计")
    assets_by_status = Column(JSON, default=dict, comment="按状态统计")
    assets_by_sensitivity = Column(JSON, default=dict, comment="按敏感级别统计")
    total_tags = Column(Integer, default=0, comment="总标签数")
    total_classifications = Column(Integer, default=0, comment="总分类数")
    avg_quality_score = Column(Float, default=0, comment="平均质量得分")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
