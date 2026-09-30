"""
数据安全模型
访问策略、脱敏规则和审计日志
"""
from datetime import datetime

from database.config import Base
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship


class AccessPolicy(Base):
    """访问策略"""

    __tablename__ = "access_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, comment="策略名称")
    description = Column(Text, comment="策略描述")
    resource_type = Column(
        String(50), nullable=False, comment="资源类型: table/column/api/file"
    )
    resource_pattern = Column(String(255), nullable=False, comment="资源匹配模式(正则)")
    action = Column(String(50), nullable=False, comment="操作: read/write/delete/admin")
    effect = Column(
        String(20), nullable=False, default="deny", comment="效果: allow/deny"
    )
    conditions = Column(JSON, default=dict, comment="条件(如时间、IP等)")
    priority = Column(Integer, default=100, comment="优先级(数字越小优先级越高)")
    is_active = Column(Integer, default=1, comment="是否启用")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_by = Column(Integer, comment="创建者ID")

    # 关联
    policy_rules = relationship(
        "PolicyRule", back_populates="policy", cascade="all, delete-orphan"
    )


class PolicyRule(Base):
    """策略规则(细粒度控制)"""

    __tablename__ = "policy_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_id = Column(
        Integer, ForeignKey("access_policies.id", ondelete="CASCADE"), nullable=False
    )
    rule_name = Column(String(100), comment="规则名称")
    rule_type = Column(String(50), comment="规则类型: role/user/group/department")
    rule_value = Column(String(255), comment="规则值(角色ID、用户ID等)")
    condition_expression = Column(Text, comment="条件表达式")
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now)

    # 关联
    policy = relationship("AccessPolicy", back_populates="policy_rules")


class MaskingRule(Base):
    """脱敏规则"""

    __tablename__ = "masking_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, comment="规则名称")
    description = Column(Text, comment="规则描述")
    field_pattern = Column(String(255), nullable=False, comment="字段匹配模式(正则)")
    masking_type = Column(
        String(50),
        nullable=False,
        comment="脱敏类型: phone/id_card/bank_card/email/name/address/custom",
    )
    masking_strategy = Column(
        String(30),
        default="mask",
        comment="脱敏策略: mask/hash/encrypt/replace/shuffle/truncate/fpe",
    )
    parameters = Column(JSON, default=dict, comment="脱敏参数")
    is_active = Column(Integer, default=1, comment="是否启用")
    priority = Column(Integer, default=100, comment="优先级")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_by = Column(Integer, comment="创建者ID")


class AccessAudit(Base):
    """访问审计日志"""

    __tablename__ = "access_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, comment="用户ID")
    username = Column(String(100), comment="用户名")
    resource_type = Column(String(50), comment="资源类型")
    resource = Column(String(255), comment="资源标识")
    action = Column(String(50), comment="操作类型")
    result = Column(String(20), comment="结果: success/denied/failed")
    ip_address = Column(String(50), comment="IP地址")
    user_agent = Column(String(500), comment="用户代理")
    request_method = Column(String(10), comment="请求方法")
    request_path = Column(String(500), comment="请求路径")
    request_params = Column(JSON, comment="请求参数(脱敏后)")
    response_code = Column(Integer, comment="响应码")
    duration_ms = Column(Integer, comment="耗时(毫秒)")
    policy_matched = Column(String(100), comment="匹配的策略")
    masking_applied = Column(Integer, default=0, comment="是否应用了脱敏")
    timestamp = Column(DateTime, default=datetime.now, index=True, comment="时间戳")
    extra_info = Column(JSON, comment="额外信息")


class DataSecurityPolicy(Base):
    """数据安全策略(表级别)"""

    __tablename__ = "data_security_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="策略名称")
    table_name = Column(String(100), nullable=False, comment="表名")
    column_name = Column(String(100), comment="列名(为空则表示整表)")
    security_level = Column(
        String(20),
        default="confidential",
        comment="安全级别: public/internal/confidential/secret",
    )
    masking_rule_id = Column(
        Integer, ForeignKey("masking_rules.id", ondelete="SET NULL"), comment="关联的脱敏规则"
    )
    access_policy_id = Column(
        Integer,
        ForeignKey("access_policies.id", ondelete="SET NULL"),
        comment="关联的访问策略",
    )
    is_encrypted = Column(Integer, default=0, comment="是否加密存储")
    encryption_algorithm = Column(String(50), comment="加密算法")
    retention_days = Column(Integer, comment="数据保留天数")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_by = Column(Integer)

    # 关联
    masking_rule = relationship("MaskingRule", foreign_keys=[masking_rule_id])
    access_policy = relationship("AccessPolicy", foreign_keys=[access_policy_id])
