"""
标签管理与数据标注 ORM 模型
标签、标签组、标注任务、标注结果
"""
from datetime import datetime

from database.config import Base
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship


class Label(Base):
    """标签定义"""

    __tablename__ = "labels"

    id = Column(String(64), primary_key=True, comment="标签ID(label_xxx)")
    name = Column(String(100), nullable=False, comment="标签名称")
    category = Column(String(100), nullable=False, index=True, comment="标签分类")
    color = Column(String(20), default="#000000", comment="颜色")
    description = Column(Text, comment="描述")
    status = Column(
        String(20),
        default="draft",
        nullable=False,
        comment="状态: draft/active/deprecated",
    )
    usage_count = Column(Integer, default=0, nullable=False, comment="使用计数")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class LabelGroup(Base):
    """标签组"""

    __tablename__ = "label_groups"

    id = Column(String(64), primary_key=True, comment="标签组ID(group_xxx)")
    name = Column(String(100), nullable=False, comment="组名称")
    description = Column(Text, comment="组描述")
    created_at = Column(DateTime, default=datetime.now)


class LabelGroupItem(Base):
    """标签组-标签关联(多对多)"""

    __tablename__ = "label_group_items"
    __table_args__ = (
        UniqueConstraint("group_id", "label_id", name="uq_group_label"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(
        String(64), ForeignKey("label_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label_id = Column(
        String(64), ForeignKey("labels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position = Column(Integer, default=0, comment="组内顺序")


class AnnotationTask(Base):
    """标注任务"""

    __tablename__ = "annotation_tasks"

    id = Column(String(64), primary_key=True, comment="任务ID(task_xxx)")
    name = Column(String(200), nullable=False, comment="任务名称")
    dataset_id = Column(String(64), nullable=False, index=True, comment="数据集ID")
    annotation_type = Column(String(50), nullable=False, comment="标注类型")
    label_group_id = Column(String(64), comment="关联标签组ID")
    assignee_id = Column(String(64), index=True, comment="分配人ID")
    status = Column(
        String(30),
        default="pending",
        nullable=False,
        comment="状态: pending/in_progress/submitted/rejected/approved",
    )
    total_items = Column(Integer, default=0, nullable=False)
    completed_items = Column(Integer, default=0, nullable=False)
    deadline = Column(DateTime, comment="截止时间")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Annotation(Base):
    """标注结果"""

    __tablename__ = "annotations"

    id = Column(String(64), primary_key=True, comment="标注ID(ann_xxx)")
    task_id = Column(
        String(64), ForeignKey("annotation_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_id = Column(String(128), nullable=False, index=True, comment="数据项ID")
    annotator_id = Column(String(64), index=True, comment="标注人ID")
    labels = Column(JSON, default=list, comment="标签列表")
    annotation_metadata = Column("metadata", JSON, default=dict, comment="元数据")
    status = Column(
        String(30),
        default="submitted",
        nullable=False,
        comment="状态: submitted/approved/rejected",
    )
    created_at = Column(DateTime, default=datetime.now)
    reviewed_at = Column(DateTime, comment="审核时间")
    reviewer_id = Column(String(64), comment="审核人ID")
    review_comment = Column(Text, comment="审核意见")
