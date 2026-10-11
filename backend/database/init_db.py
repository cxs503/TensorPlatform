"""
数据库初始化脚本 — 创建所有表
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.config import engine, Base
from models.core import (
    KnowledgeRule,
    User, Project, Dataset, Model, ModelVersion, TrainingJob,
    EmissionFactor, CarbonEmission,
    PhotovoltaicSystem, PhotovoltaicData,
    EnergyConsumption, EnergyEfficiencySystem,
    Device, Alert
)


def init_db():
    """创建所有表"""
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Done! Tables created:")

    from sqlalchemy import inspect
    inspector = inspect(engine)
    for table in sorted(inspector.get_table_names()):
        print(f"  ✓ {table}")


if __name__ == "__main__":
    init_db()
