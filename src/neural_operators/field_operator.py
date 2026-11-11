"""通用二维 Fourier Neural Operator（field→field 算符）与汇聚读出组件。

本模块只包含**与领域无关**的算子内核：

* :class:`SpectralConv2d` —— Fourier 层（截断模态的复权重谱乘）
* :class:`FieldFNO2d` —— 提升 → n_layers × (谱卷积 + 逐点线性 + 激活) → 投影
* :func:`area_weights` / :func:`integrate` / :func:`mean_pool` —— 归一化面积权与
  积分／均值泛函读出（用于把输出场汇聚为标量功能量）
* :func:`nonneg` —— softplus 非负化（硬约束用）

设计要点（与领域数据解耦）：输入/输出通道数、网格尺寸、模态数全部来自配置，
本模块不出现任何领域词条；领域语义（船型参数、工况、标签）由调用方经
``in_channels`` / 配置注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Fourier 层
# ---------------------------------------------------------------------------


class SpectralConv2d(nn.Module):
    """二维谱卷积：对最低 ``modes1×modes2`` 个模态做复线性变换。

    参数量 = ``in_channels × out_channels × modes1 × modes2 × 2``（实/虚部）。
    """

    def __init__(self, in_channels: int, out_channels: int,
                 modes1: int, modes2: int):
        super().__init__()
        self.in_channels = int(in_channels)
        self.out_channels = int(out_channels)
        self.modes1 = int(modes1)
        self.modes2 = int(modes2)
        scale = 1.0 / max(1, in_channels * out_channels)
        self.w_re = nn.Parameter(scale * torch.randn(in_channels, out_channels,
                                                     self.modes1, self.modes2))
        self.w_im = nn.Parameter(scale * torch.randn(in_channels, out_channels,
                                                     self.modes1, self.modes2))

    def _mul(self, x_ft: torch.Tensor) -> torch.Tensor:
        # x_ft: (B, Cin, m1, m2) 复数；权重按实际可用模态数切片（网格过小时截断）
        m1, m2 = x_ft.shape[-2], x_ft.shape[-1]
        w = torch.complex(self.w_re[..., :m1, :m2], self.w_im[..., :m1, :m2])
        return torch.einsum("bimn,iomn->bomn", x_ft, w)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, Cin, H, W) → (B, Cout, H, W)。"""
        batch, _, h, w_dim = x.shape
        x_ft = torch.fft.rfft2(x, dim=(-2, -1))
        m1 = min(self.modes1, x_ft.shape[-2])
        m2 = min(self.modes2, x_ft.shape[-1])
        out_ft = torch.zeros(batch, self.out_channels, x_ft.shape[-2],
                             x_ft.shape[-1], dtype=torch.cfloat, device=x.device)
        out_ft[:, :, :m1, :m2] = self._mul(x_ft[:, :, :m1, :m2])
        return torch.fft.irfft2(out_ft, s=(h, w_dim), dim=(-2, -1))


@dataclass
class FieldFNOConfig:
    """二维 FNO 配置（全部为无领域语义的结构超参）。"""
    in_channels: int
    out_channels: int
    hidden_channels: int = 32
    n_layers: int = 4          # Fourier 层数 L
    n_modes_x: int = 12        # 纵向截断模态数
    n_modes_z: int = 6         # 横向截断模态数
    grid_x: int = 32
    grid_z: int = 16
    lifting_channels: Optional[int] = None
    activation: str = "gelu"
    dtype: torch.dtype = torch.float32


class FieldFNO2d(nn.Module):
    """二维 FNO：``(B, Cin, Hx, Hz) -> (B, Cout, Hx, Hz)``。"""

    def __init__(self, config: FieldFNOConfig):
        super().__init__()
        self.config = config
        hid = int(config.hidden_channels)
        lift = int(config.lifting_channels or hid)
        self.lift = nn.Sequential(
            nn.Linear(config.in_channels, lift),
            nn.GELU(),
            nn.Linear(lift, hid),
        )
        self.spectral = nn.ModuleList([
            SpectralConv2d(hid, hid, config.n_modes_x, config.n_modes_z)
            for _ in range(config.n_layers)
        ])
        self.pointwise = nn.ModuleList([
            nn.Conv2d(hid, hid, 1) for _ in range(config.n_layers)
        ])
        self.proj = nn.Sequential(
            nn.Conv2d(hid, max(hid // 2, 8), 1),
            nn.GELU(),
            nn.Conv2d(max(hid // 2, 8), config.out_channels, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # (B, Cin, H, W) -> (B, H, W, Cin) 以复用逐点线性
        h = x.permute(0, 2, 3, 1)
        h = self.lift(h).permute(0, 3, 1, 2)                        # (B, hid, H, W)
        for spec, pw in zip(self.spectral, self.pointwise):
            h = F.gelu(spec(h) + pw(h))
        return self.proj(h)


# ---------------------------------------------------------------------------
# 泛函读出（积分 / 均值）与硬约束变换
# ---------------------------------------------------------------------------


def area_weights(nx: int, nz: int, dtype: torch.dtype = torch.float32,
                 device: Optional[torch.device] = None) -> torch.Tensor:
    """归一化单元权（和为 1），形状 ``(1, 1, nx, nz)``；用于离散积分/均值。"""
    w = torch.full((1, 1, nx, nz), 1.0 / (nx * nz), dtype=dtype,
                   device=device)
    return w


def integrate(field: torch.Tensor, weights: torch.Tensor) -> torch.Tensor:
    """对场做归一化积分：``∫ f dA ≈ Σ w·f``，返回 ``(B, C)``。"""
    return (field * weights).sum(dim=(-2, -1))


def mean_pool(field: torch.Tensor, weights: torch.Tensor) -> torch.Tensor:
    """归一化均值（权已归一）：``(B, C)``。"""
    return integrate(field, weights)


def nonneg(raw: torch.Tensor, scale: float = 1.0) -> torch.Tensor:
    """softplus 非负化并乘以固定尺度（保证物理量非负，硬约束）。"""
    return scale * F.softplus(raw)