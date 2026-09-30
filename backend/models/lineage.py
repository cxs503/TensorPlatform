"""
数据血缘模型定义
"""
import uuid
from datetime import datetime

from database.config import Base
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text


class LineageNode(Base):
    """血缘节点"""

    __tablename__ = "lineage_nodes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, comment="节点名称")
    display_name = Column(String(255), comment="显示名称")
    node_type = Column(String(50), nullable=False, comment="节点类型")
    system = Column(String(100), comment="所属系统")
    database = Column(String(100), comment="所属数据库")
    schema_name = Column(String(100), comment="所属Schema")
    full_path = Column(String(500), comment="完整路径")
    description = Column(Text, comment="描述")
    extra_metadata = Column(JSON, default=dict, comment="扩展元数据")
    tags = Column(JSON, default=list, comment="标签列表")
    owner = Column(String(100), comment="责任人")
    quality_score = Column(Integer, default=0, comment="数据质量评分")
    is_active = Column(Integer, default=1, comment="是否活跃")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
    created_by = Column(String(100), comment="创建人")
    resource_type = Column(String(50), comment="关联资源类型: dataset/training_job/model")
    resource_id = Column(Integer, comment="关联资源ID")
    organization_id = Column(Integer, ForeignKey("organizations.id"), comment="所属组织")

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name or self.name,
            "node_type": self.node_type,
            "system": self.system,
            "database": self.database,
            "schema_name": self.schema_name,
            "full_path": self.full_path,
            "description": self.description,
            "metadata": self.extra_metadata or {},
            "tags": self.tags or [],
            "owner": self.owner,
            "quality_score": self.quality_score,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": self.created_by,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "organization_id": self.organization_id,
        }


class LineageEdge(Base):
    """血缘关系边"""

    __tablename__ = "lineage_edges"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(
        String(36),
        ForeignKey("lineage_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="源节点ID",
    )
    target_id = Column(
        String(36),
        ForeignKey("lineage_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="目标节点ID",
    )
    edge_type = Column(String(50), default="derived", comment="关系类型")
    transformation = Column(Text, comment="转换逻辑")
    transformation_type = Column(String(50), default="sql", comment="转换类型")
    column_mapping = Column(JSON, default=list, comment="列映射关系")
    impact_weight = Column(Integer, default=1, comment="影响权重")
    description = Column(Text, comment="关系描述")
    extra_metadata = Column(JSON, default=dict, comment="扩展元数据")
    is_active = Column(Integer, default=1, comment="是否活跃")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
    created_by = Column(String(100), comment="创建人")
    organization_id = Column(Integer, ForeignKey("organizations.id"), comment="所属组织")

    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type,
            "transformation": self.transformation,
            "transformation_type": self.transformation_type,
            "column_mapping": self.column_mapping or [],
            "impact_weight": self.impact_weight,
            "description": self.description,
            "metadata": self.extra_metadata or {},
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": self.created_by,
            "organization_id": self.organization_id,
        }


class LineageGraph(Base):
    """血缘图谱"""

    __tablename__ = "lineage_graphs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, comment="图谱名称")
    description = Column(Text, comment="图谱描述")
    graph_data = Column(JSON, default=dict, comment="图数据")
    version = Column(String(50), default="1.0.0", comment="版本号")
    is_active = Column(Integer, default=1, comment="是否活跃")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
    created_by = Column(String(100), comment="创建人")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "graph_data": self.graph_data or {},
            "version": self.version,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": self.created_by,
        }


class LineageChangeLog(Base):
    """血缘变更日志"""

    __tablename__ = "lineage_change_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    node_id = Column(String(36), comment="关联节点ID")
    edge_id = Column(String(36), comment="关联边ID")
    change_type = Column(String(50), nullable=False, comment="变更类型")
    change_detail = Column(JSON, default=dict, comment="变更详情")
    old_value = Column(JSON, comment="旧值")
    new_value = Column(JSON, comment="新值")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    created_by = Column(String(100), comment="操作人")
    organization_id = Column(Integer, ForeignKey("organizations.id"), comment="所属组织")

    def to_dict(self):
        return {
            "id": self.id,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "change_type": self.change_type,
            "change_detail": self.change_detail or {},
            "old_value": self.old_value,
            "new_value": self.new_value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "organization_id": self.organization_id,
        }
