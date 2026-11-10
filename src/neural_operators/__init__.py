"""物理信息神经算子 (FNO) 模块——船体阻力/耐波实时代理模型。

以 LBM/BEM 验证数据库为训练源，Fourier Neural Operator 架构 + 物理守恒硬约束，
实现 <100ms 实时阻力/耐波预报。

能力登记：ai.predict (阻力/耐波实时代理)
"""

from .fno import FNOModel, FNOConfig
from .physics_constraints import (
    enforce_non_negative_resistance,
    enforce_symmetry_zero_heading,
    enforce_dimensional_consistency,
    PhysicsConstrainedOutput,
)
from .data_gen import generate_training_dataset, ShipHullDataset

__all__ = [
    "FNOModel",
    "FNOConfig",
    "enforce_non_negative_resistance",
    "enforce_symmetry_zero_heading",
    "enforce_dimensional_consistency",
    "PhysicsConstrainedOutput",
    "generate_training_dataset",
    "ShipHullDataset",
]
