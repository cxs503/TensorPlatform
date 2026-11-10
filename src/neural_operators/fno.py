"""Fourier Neural Operator (FNO) 架构实现。

输入场 u(x)=[几何参数场, 工况参数] → 输出场 v(x)=[R_T, RAO_φ, RAO_z]
Fourier 层数=4，通道数=64。

架构：
  Input → Linear(9→64) → [SpectralConv1d + Linear]×4 → Linear(64→3) → Output
"""

from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class FNOConfig:
    """FNO 配置。"""
    input_dim: int = 9  # [L, B, T, Cb, Cp, Fn, beta, rho, nu]
    output_dim: int = 3  # [R_T, RAO_roll, RAO_heave]
    hidden_channels: int = 64
    n_fourier_layers: int = 4
    n_modes: int = 16  # Fourier 模式数
    learning_rate: float = 1e-3
    batch_size: int = 64
    epochs: int = 500
    early_stopping_patience: int = 20
    weight_decay: float = 1e-4


class SpectralConv1d(nn.Module):
    """1D 谱卷积层（Fourier 层核心）。

    在 Fourier 空间做线性变换，再逆变换回物理空间。
    """

    def __init__(self, in_channels: int, out_channels: int, n_modes: int):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.n_modes = n_modes

        # Fourier 空间的复数权重
        self.scale = 1.0 / (in_channels * out_channels)
        self.weights_real = nn.Parameter(
            self.scale * torch.randn(in_channels, out_channels, n_modes)
        )
        self.weights_imag = nn.Parameter(
            self.scale * torch.randn(in_channels, out_channels, n_modes)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (batch, channels, n_points)"""
        batch_size = x.shape[0]
        n_points = x.shape[2]

        # FFT
        x_ft = torch.fft.rfft(x, dim=-1)

        # 在 Fourier 空间做乘法
        out_ft = torch.zeros(
            batch_size, self.out_channels, x_ft.shape[-1],
            device=x.device, dtype=torch.cfloat
        )

        # 只处理前 n_modes 个模式
        n_modes_actual = min(self.n_modes, x_ft.shape[-1])
        for i in range(n_modes_actual):
            # 复数乘法: (a+bi)(c+di) = (ac-bd) + (ad+bc)i
            w_real = self.weights_real[:, :, i]  # (in, out)
            w_imag = self.weights_imag[:, :, i]
            x_real = x_ft[:, :, i].real  # (batch, in)
            x_imag = x_ft[:, :, i].imag

            out_real = torch.einsum("bi,io->bo", x_real, w_real) - \
                       torch.einsum("bi,io->bo", x_imag, w_imag)
            out_imag = torch.einsum("bi,io->bo", x_real, w_imag) + \
                       torch.einsum("bi,io->bo", x_imag, w_real)

            out_ft[:, :, i] = torch.complex(out_real, out_imag)

        # IFFT
        out = torch.fft.irfft(out_ft, n=n_points, dim=-1)
        return out


class FourierBlock(nn.Module):
    """单个 Fourier 层块。"""

    def __init__(self, channels: int, n_modes: int):
        super().__init__()
        self.spectral_conv = SpectralConv1d(channels, channels, n_modes)
        self.linear = nn.Linear(channels, channels)
        self.activation = nn.GELU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (batch, channels, n_points)"""
        # 谱卷积分支
        x1 = self.spectral_conv(x)
        # 线性分支
        x2 = self.linear(x.permute(0, 2, 1)).permute(0, 2, 1)
        return self.activation(x1 + x2)


class FNOModel(nn.Module):
    """Fourier Neural Operator 船体阻力/耐波代理模型。

    架构：
      Input(9) → Lift(9→64) → [FourierBlock]×4 → Project(64→3) → Output
    """

    def __init__(self, config: Optional[FNOConfig] = None):
        super().__init__()
        self.config = config or FNOConfig()

        # 提升层：输入 → 隐藏空间
        self.lift = nn.Linear(self.config.input_dim, self.config.hidden_channels)

        # Fourier 层
        self.fourier_layers = nn.ModuleList([
            FourierBlock(self.config.hidden_channels, self.config.n_modes)
            for _ in range(self.config.n_fourier_layers)
        ])

        # 投影层：隐藏空间 → 输出
        self.project = nn.Linear(self.config.hidden_channels, self.config.output_dim)

        # 物理约束：输出层 ReLU 确保 R_T ≥ 0
        self.relu_rt = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播。

        Args:
            x: (batch, input_dim) 或 (batch, n_points, input_dim)

        Returns:
            (batch, output_dim) [R_T, RAO_roll, RAO_heave]
        """
        if x.dim() == 2:
            # (batch, input_dim) → (batch, 1, input_dim) → (batch, channels, 1)
            x = x.unsqueeze(1)  # (batch, 1, input_dim)
            x = self.lift(x)  # (batch, 1, channels)
            x = x.permute(0, 2, 1)  # (batch, channels, 1)

            for layer in self.fourier_layers:
                x = layer(x)

            x = x.permute(0, 2, 1)  # (batch, 1, channels)
            x = x.squeeze(1)  # (batch, channels)
        else:
            # (batch, n_points, input_dim)
            x = self.lift(x)  # (batch, n_points, channels)
            x = x.permute(0, 2, 1)  # (batch, channels, n_points)

            for layer in self.fourier_layers:
                x = layer(x)

            x = x.permute(0, 2, 1)  # (batch, n_points, channels)
            x = x.mean(dim=1)  # (batch, channels) 池化

        # 投影到输出
        out = self.project(x)

        # 物理约束：R_T ≥ 0（对第一个输出应用 ReLU）
        out[:, 0] = self.relu_rt(out[:, 0])

        return out

    def predict(self, x: np.ndarray, device: str = "cpu") -> np.ndarray:
        """NumPy 接口预测。

        Args:
            x: (N, input_dim) numpy array
            device: "cpu" or "cuda"

        Returns:
            (N, output_dim) numpy array
        """
        self.eval()
        with torch.no_grad():
            x_tensor = torch.tensor(x, dtype=torch.float32).to(device)
            out = self(x_tensor)
            return out.cpu().numpy()

    def count_parameters(self) -> int:
        """返回模型参数量。"""
        return sum(p.numel() for p in self.parameters())


def create_fno_model(config: Optional[FNOConfig] = None) -> FNOModel:
    """创建 FNO 模型工厂函数。"""
    return FNOModel(config)
