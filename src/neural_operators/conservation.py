"""与领域无关的守恒/物理约束残差工具。

提供给神经算子训练/评测的物理项：

* :func:`divergence` / :func:`divergence_penalty` —— 离散散度 ``∇·u`` 与其平方
  惩罚（不可压/质量守恒的弱形式，``L_phys = λ‖∇·u‖²``）
* :func:`momentum_deficit_integral` —— 尾流动量亏缺积分 ``∫u(1-u)dA``（动量通量
  亏损，等价于动量守恒下的阻力）
* :func:`momentum_balance_residual` —— 阻力密度积分与动量亏缺积分之差（归一化
  动量平衡残差）
* :func:`symmetry_gate` —— 反对称工况门（用于对称性硬约束）
* :func:`ct_to_rt` —— 无量纲阻力系数 → 有量纲阻力（量纲一致性，硬约束）
* :func:`relative_error` —— 相对误差（验收判据口径）

这些函数只吃张量/数组，不含任何领域词条。
"""

from __future__ import annotations

from typing import Tuple

import torch


# ---------------------------------------------------------------------------
# 散度（不可压约束）
# ---------------------------------------------------------------------------


def divergence(u_x: torch.Tensor, u_z: torch.Tensor,
               dx: float, dz: float) -> torch.Tensor:
    """中心差分散度 ``∂u_x/∂x + ∂u_z/∂z``。

    边界采用单侧差分（一阶精度）。输入形状 ``(B, Hx, Hz)``。
    """
    if u_x.shape != u_z.shape:
        raise ValueError("u_x and u_z must share shape")
    # 内部中心差分
    dux = torch.zeros_like(u_x)
    duz = torch.zeros_like(u_z)
    dux[:, 1:-1, :] = (u_x[:, 2:, :] - u_x[:, :-2, :]) / (2.0 * dx)
    duz[:, :, 1:-1] = (u_z[:, :, 2:] - u_z[:, :, :-2]) / (2.0 * dz)
    # 边界单侧差分
    dux[:, 0, :] = (u_x[:, 1, :] - u_x[:, 0, :]) / dx
    dux[:, -1, :] = (u_x[:, -1, :] - u_x[:, -2, :]) / dx
    duz[:, :, 0] = (u_z[:, :, 1] - u_z[:, :, 0]) / dz
    duz[:, :, -1] = (u_z[:, :, -1] - u_z[:, :, -2]) / dz
    return dux + duz


def divergence_penalty(u_x: torch.Tensor, u_z: torch.Tensor, dx: float, dz: float,
                       weights: torch.Tensor) -> torch.Tensor:
    """``‖∇·u‖²`` 的加权均方（与网格无关的无量纲量）。"""
    div = divergence(u_x, u_z, dx, dz)
    return ((div ** 2) * weights).sum(dim=(-2, -1)).mean()


# ---------------------------------------------------------------------------
# 动量守恒
# ---------------------------------------------------------------------------


def momentum_deficit_integral(u_x: torch.Tensor,
                              weights: torch.Tensor) -> torch.Tensor:
    """尾流动量亏缺 ``∫ u_x(1-u_x) dA``（u_x 为与来流之比，取值 (0,1)）。"""
    u = u_x.clamp(0.0, 1.0)
    return (u * (1.0 - u) * weights).sum(dim=(-2, -1))


def momentum_balance_residual(u_x: torch.Tensor, resistance_density: torch.Tensor,
                              weights: torch.Tensor) -> torch.Tensor:
    """归一化动量平衡残差 ``(∫u(1-u) - ∫r dA) / |∫r dA|``。

    物理含义：尾流测量平面上的动量通量亏损必须等于船体阻力（动量守恒）。
    """
    mom = momentum_deficit_integral(u_x, weights)
    res = (resistance_density * weights).sum(dim=(-2, -1))
    denom = res.abs().clamp_min(1e-12)
    return (mom - res) / denom


def momentum_balance_relative(u_x: torch.Tensor, resistance_density: torch.Tensor,
                              weights: torch.Tensor) -> torch.Tensor:
    """尺度无关的对移动量平衡残差 ``(∫u(1-u)dA - ∫r dA)/(|·|+|·|)`` ∈ [-1,1]。

    与 :func:`momentum_balance_residual` 同物理含义，但对 ``C_T`` 的量级不敏感，
    适合作为损失项（避免因阻力系数 1e-3 量级导致的项间失衡）。
    """
    mom = momentum_deficit_integral(u_x, weights)
    res = (resistance_density * weights).sum(dim=(-2, -1))
    denom = (mom.abs() + res.abs()).clamp_min(1e-12)
    return (mom - res) / denom


# ---------------------------------------------------------------------------
# 硬约束与量纲
# ---------------------------------------------------------------------------


def symmetry_gate(heading_deg: torch.Tensor) -> torch.Tensor:
    """反对称工况门 ``|sin β|``：β=0/180 时侧向激励为零（对称性硬约束）。

    以 float64 计算正弦后回落到原 dtype：float32 的 ``sin(π)≈8.7e-8`` 会把
    「精确为零」的对称性约束变成 ~1e-7 的残余，double 计算后残差 ~1e-16。
    """
    work = heading_deg.double() if heading_deg.dtype == torch.float32 else heading_deg
    gate = torch.sin(torch.deg2rad(work)).abs()
    return gate.to(heading_deg.dtype) if gate.dtype != heading_deg.dtype else gate


def ct_to_rt(ct: torch.Tensor, rho: float, speed: float,
             wetted_surface: torch.Tensor) -> torch.Tensor:
    """``R_T = ½ ρ V² S C_T``（量纲一致性硬约束，逐样本 S 可不同）。"""
    return 0.5 * rho * speed ** 2 * wetted_surface * ct


# ---------------------------------------------------------------------------
# 误差口径
# ---------------------------------------------------------------------------


def relative_error(pred: torch.Tensor, truth: torch.Tensor) -> torch.Tensor:
    """相对误差 ``|pred-truth|/|truth|``（验收判据口径）。"""
    return (pred - truth).abs() / truth.abs().clamp_min(1e-12)


def summarize_error(pred: torch.Tensor, truth: torch.Tensor) -> Tuple[float, float]:
    """返回 (MAPE, p95 相对误差)，单位 %。"""
    err = relative_error(torch.as_tensor(pred).double(),
                         torch.as_tensor(truth).double()) * 100.0
    return float(err.mean()), float(torch.quantile(err, 0.95))