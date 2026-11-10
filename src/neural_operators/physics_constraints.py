"""物理守恒硬约束模块。

三大物理约束：
1. R_T ≥ 0（阻力非负）—— 输出层 ReLU
2. β=0° 时横摇 RAO = 0（对称性）—— 条件掩码
3. 量纲一致性：R_T 量纲 = ρ·L²·U² —— 量纲检查
"""

from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class PhysicsConstrainedOutput:
    """物理约束后的输出。"""
    rt: np.ndarray  # 总阻力 N, shape=(N,)
    rao_roll: np.ndarray  # 横摇 RAO, shape=(N,)
    rao_heave: np.ndarray  # 垂荡 RAO, shape=(N,)
    constraints_applied: list  # 应用的约束列表


def enforce_non_negative_resistance(rt: np.ndarray) -> np.ndarray:
    """约束1：R_T ≥ 0（ReLU）。

    物理上阻力不可能为负，负值表示模型外推失效。
    """
    return np.maximum(rt, 0.0)


def enforce_symmetry_zero_heading(
    rao_roll: np.ndarray,
    beta_deg: np.ndarray,
    threshold: float = 1e-6
) -> np.ndarray:
    """约束2：β=0°（或 180°）时横摇 RAO = 0。

    对称船型在迎浪/随浪时不应产生横摇力矩。
    """
    # β ≈ 0° 或 β ≈ 180° 或 β ≈ -180°
    beta_rad = np.radians(beta_deg)
    # 对称条件：sin(β) ≈ 0
    symmetric = np.abs(np.sin(beta_rad)) < threshold

    rao_roll_constrained = rao_roll.copy()
    rao_roll_constrained[symmetric] = 0.0

    return rao_roll_constrained


def enforce_dimensional_consistency(
    rt: np.ndarray,
    l: np.ndarray,
    rho: np.ndarray,
    fn: np.ndarray,
    g: float = 9.80665,
    tolerance: float = 1e-6
) -> Tuple[np.ndarray, np.ndarray]:
    """约束3：量纲一致性检查。

    R_T 量纲应为 [M·L/T²] = ρ·L²·U²
    即 R_T / (ρ·L²·U²) 应为无量纲数（阻力系数量级 ~0.001-0.01）

    返回：(修正后的R_T, 量纲误差)
    """
    # U = Fn · √(gL)
    u = fn * np.sqrt(g * l)

    # 量纲参考值：ρ·L²·U²
    dimensional_ref = rho * l ** 2 * u ** 2

    # 防止除零
    dimensional_ref = np.maximum(dimensional_ref, 1e-30)

    # 计算无量纲阻力系数
    ct_predicted = rt / dimensional_ref

    # 物理合理的 Ct 范围：[0.001, 0.05]
    # 超出范围的值标记为量纲不一致
    ct_min, ct_max = 0.001, 0.05
    dimensional_error = np.zeros_like(rt)

    # 计算量纲误差：如果 Ct 超出合理范围，误差为偏离程度
    below = ct_predicted < ct_min
    above = ct_predicted > ct_max

    dimensional_error[below] = (ct_min - ct_predicted[below]) / ct_min
    dimensional_error[above] = (ct_predicted[above] - ct_max) / ct_max

    # 修正：将超出范围的 Ct 截断到合理范围
    ct_clipped = np.clip(ct_predicted, ct_min, ct_max)
    rt_corrected = ct_clipped * dimensional_ref

    return rt_corrected, dimensional_error


def apply_all_physics_constraints(
    raw_output: np.ndarray,
    input_features: np.ndarray,
    g: float = 9.80665
) -> PhysicsConstrainedOutput:
    """应用全部物理约束。

    Args:
        raw_output: (N, 3) [R_T, RAO_roll, RAO_heave]
        input_features: (N, 9) [L, B, T, Cb, Cp, Fn, beta, rho, nu]

    Returns:
        PhysicsConstrainedOutput
    """
    rt = raw_output[:, 0].copy()
    rao_roll = raw_output[:, 1].copy()
    rao_heave = raw_output[:, 2].copy()

    constraints_applied = []

    # 约束1：R_T ≥ 0
    rt = enforce_non_negative_resistance(rt)
    constraints_applied.append("non_negative_resistance")

    # 约束2：对称性
    beta_deg = input_features[:, 6]  # beta 在第6列
    rao_roll = enforce_symmetry_zero_heading(rao_roll, beta_deg)
    constraints_applied.append("symmetry_zero_heading")

    # 约束3：量纲一致性
    l = input_features[:, 0]  # L 在第0列
    rho = input_features[:, 7]  # rho 在第7列
    fn = input_features[:, 5]  # Fn 在第5列
    rt, dim_error = enforce_dimensional_consistency(rt, l, rho, fn, g)
    constraints_applied.append("dimensional_consistency")

    return PhysicsConstrainedOutput(
        rt=rt,
        rao_roll=rao_roll,
        rao_heave=rao_heave,
        constraints_applied=constraints_applied
    )


def check_dimensional_consistency_error(
    rt: np.ndarray,
    l: np.ndarray,
    rho: np.ndarray,
    fn: np.ndarray,
    g: float = 9.80665
) -> float:
    """检查量纲一致性误差（返回最大相对误差）。"""
    u = fn * np.sqrt(g * l)
    dimensional_ref = rho * l ** 2 * u ** 2
    dimensional_ref = np.maximum(dimensional_ref, 1e-30)

    ct = rt / dimensional_ref
    # 合理 Ct 范围中心值
    ct_center = 0.01
    relative_error = np.abs(ct - ct_center) / ct_center

    return float(np.max(relative_error))
