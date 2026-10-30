"""
Ship Sample ORM Model — 船舶计算样本存储。
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, JSON, Index
from database.config import Base


class ShipSample(Base):
    """船舶计算样本记录。"""
    __tablename__ = "ship_samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    calc_type = Column(String(32), nullable=False, index=True)  # resistance / stability
    params = Column(JSON, nullable=True)  # 输入参数
    result = Column(JSON, nullable=True)  # 计算结果
    error = Column(Text, nullable=True)  # 错误信息（如有）
    error_pct = Column(Float, nullable=True)  # 误差百分比（基准验证时）
    passed = Column(Boolean, nullable=True)  # 是否通过基准
    source = Column(String(64), nullable=True)  # 来源：batch_sweep / benchmark / manual
    tags = Column(JSON, nullable=True)  # 标签
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_ship_samples_calc_type_created", "calc_type", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "calc_type": self.calc_type,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "error_pct": self.error_pct,
            "passed": self.passed,
            "source": self.source,
            "tags": self.tags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
