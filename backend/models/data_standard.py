"""
数据标准模型
定义数据标准管理和字段规范
"""
from datetime import datetime

from database.config import Base
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship


class DataStandard(Base):
    """数据标准"""

    __tablename__ = "data_standards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, comment="标准名称")
    version = Column(String(20), default="1.0.0", comment="版本号")
    description = Column(Text, comment="标准描述")
    fields = Column(JSON, default=list, comment="字段定义列表")
    status = Column(String(20), default="draft", comment="状态: draft/active/archived")
    is_active = Column(Integer, default=1, comment="是否启用")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
    created_by = Column(Integer, comment="创建者ID")

    # 关联字段定义
    field_definitions = relationship(
        "StandardField", back_populates="standard", cascade="all, delete-orphan"
    )


class StandardField(Base):
    """标准字段定义"""

    __tablename__ = "standard_fields"

    id = Column(Integer, primary_key=True, autoincrement=True)
    standard_id = Column(
        Integer,
        ForeignKey("data_standards.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属标准ID",
    )
    name = Column(String(100), nullable=False, comment="字段名称")
    display_name = Column(String(100), comment="显示名称")
    data_type = Column(
        String(50),
        nullable=False,
        comment="数据类型: string/integer/float/date/datetime/boolean/json",
    )
    length = Column(Integer, comment="长度限制")
    precision = Column(Integer, comment="精度(小数位数)")
    nullable = Column(Integer, default=1, comment="是否可空: 0否/1是")
    default_value = Column(String(500), comment="默认值")
    description = Column(Text, comment="字段描述")
    validation_rules = Column(JSON, default=dict, comment="验证规则")
    is_primary_key = Column(Integer, default=0, comment="是否主键")
    is_unique = Column(Integer, default=0, comment="是否唯一")
    is_indexed = Column(Integer, default=0, comment="是否索引")
    sort_order = Column(Integer, default=0, comment="排序顺序")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联
    standard = relationship("DataStandard", back_populates="field_definitions")


class DataValidationResult(Base):
    """数据验证结果"""

    __tablename__ = "data_validation_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    standard_id = Column(
        Integer, ForeignKey("data_standards.id", ondelete="CASCADE"), nullable=False
    )
    validation_type = Column(
        String(50), comment="验证类型: schema/data_type/constraint/custom"
    )
    total_records = Column(Integer, default=0, comment="总记录数")
    valid_records = Column(Integer, default=0, comment="有效记录数")
    invalid_records = Column(Integer, default=0, comment="无效记录数")
    validation_score = Column(Integer, comment="验证得分 0-100")
    errors = Column(JSON, default=list, comment="错误详情")
    warnings = Column(JSON, default=list, comment="警告详情")
    validated_at = Column(DateTime, default=datetime.now, comment="验证时间")
    validated_by = Column(Integer, comment="验证者ID")
    data_source = Column(String(255), comment="数据来源")
