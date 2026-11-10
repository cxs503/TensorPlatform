"""合成训练数据生成器——Wigley/Series 60/KVLCC2 船型阻力/耐波数据库。

基于 ITTC-57 + Holtrop-Mennen 公式生成 ≥500 样本的训练集，
覆盖 Fr ∈ [0.1, 0.5] 主域 + Fr ∈ [0.5, 0.65] 外推域。
输出字段：
  输入: [L, B, T, Cb, Cp, Fn, beta_deg, rho, nu]
  输出: [R_T, RAO_roll, RAO_heave]
"""

from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 物理常数
G = 9.80665
RHO_SEA = 1025.0
RHO_FRESH = 998.2
NU_SEA = 1.188e-6
NU_FRESH = 1.004e-6


def ittc57_cf(re: float) -> float:
    """ITTC-57 平板摩擦阻力系数。"""
    if re <= 0:
        return 0.0
    lg = math.log10(re) - 2.0
    if lg <= 0:
        return 0.0
    return 0.075 / (lg * lg)


def roughness_allowance(lwl: float) -> float:
    """Holtrop 粗糙度补贴。"""
    return 0.006 * (lwl + 100.0) ** (-0.16) - 0.00205


def wetted_surface_wigley(l: float, b: float, t: float) -> float:
    """Wigley 船型湿表面积近似。"""
    return l * (2.0 * t + b) * 0.75


def wetted_surface_series60(cb: float, l: float, b: float, t: float) -> float:
    """Series 60 湿表面积近似 (Schneekluth)。"""
    return l * (b + 2.0 * t) * (0.45 + 0.45 * cb)


def wetted_surface_kvlcc2(l: float, b: float, t: float, cb: float) -> float:
    """KVLCC2 湿表面积近似。"""
    return l * (2.0 * t + b) * math.sqrt(cb) * 0.85


def form_factor_wigley() -> float:
    """Wigley 船型形状因子 (1+k)。"""
    return 1.15


def form_factor_series60(cb: float) -> float:
    """Series 60 形状因子。"""
    return 1.0 + 0.25 * cb


def form_factor_kvlcc2(cb: float) -> float:
    """KVLCC2 形状因子。"""
    return 1.0 + 0.30 * cb


def compute_resistance(
    l: float, b: float, t: float, cb: float, cp: float,
    fn: float, rho: float = RHO_SEA, nu: float = NU_SEA,
    ship_type: str = "wigley"
) -> float:
    """计算总阻力 R_T (N)。

    简化 Holtrop 风格：R_T = (1+k)·Cf·0.5·ρ·V²·S + Rw + Ca·0.5·ρ·V²·S
    兴波阻力 Rw 用简化模型：Rw ≈ Cw·ρ·g·∇·Fn^4·exp(-α/Fn²)
    """
    speed = fn * math.sqrt(G * l)
    re = speed * l / nu
    cf = ittc57_cf(re)
    ca = roughness_allowance(l)

    # 湿表面积
    if ship_type == "wigley":
        s = wetted_surface_wigley(l, b, t)
        ff = form_factor_wigley()
    elif ship_type == "series60":
        s = wetted_surface_series60(cb, l, b, t)
        ff = form_factor_series60(cb)
    else:  # kvlcc2
        s = wetted_surface_kvlcc2(l, b, t, cb)
        ff = form_factor_kvlcc2(cb)

    # 排水体积
    vol = cb * l * b * t

    # 兴波阻力系数简化模型
    # Cw ≈ k_w · Fn^4 · exp(-α/Fn²)
    alpha = 0.5 + 0.3 * (1.0 - cb)  # 瘦船 α 小，兴波强
    cw = 0.002 * fn ** 4 * math.exp(-alpha / (fn ** 2 + 0.01))

    q = 0.5 * rho * speed ** 2
    rt = (ff * cf + ca + cw) * q * s

    return max(0.0, rt)


def compute_rao_roll(
    l: float, b: float, t: float, cb: float, fn: float,
    beta_deg: float, rho: float = RHO_SEA
) -> float:
    """横摇 RAO 简化模型 (deg/deg)。

    β=0° 时 RAO=0（对称性约束）；
    随 β 增大而增大，随 Fn 增大而减小。
    """
    if abs(beta_deg) < 1e-6:
        return 0.0

    beta_rad = math.radians(beta_deg)
    # 横摇响应随浪向角增大
    directional_factor = abs(math.sin(beta_rad))

    # 随 Fn 增大，遭遇频率增大，横摇响应减小
    fn_factor = 1.0 / (1.0 + 2.0 * fn)

    # 船宽影响：宽船横摇响应小
    beam_factor = 1.0 / (1.0 + 0.5 * (b / t - 2.5))

    # 基础幅值
    base_amplitude = 0.8 * directional_factor * fn_factor * beam_factor

    # 共振峰在 Fn ≈ 0.2-0.3
    resonance = math.exp(-((fn - 0.25) ** 2) / 0.02)
    base_amplitude += 0.3 * resonance * directional_factor

    return max(0.0, base_amplitude)


def compute_rao_heave(
    l: float, b: float, t: float, cb: float, fn: float,
    beta_deg: float, rho: float = RHO_SEA
) -> float:
    """垂荡 RAO 简化模型 (m/m)。

    随 Fn 增大先增后减；β=0°（迎浪）时最大。
    """
    beta_rad = math.radians(beta_deg)
    # 迎浪时最大，斜浪减小
    directional_factor = 0.5 + 0.5 * abs(math.cos(beta_rad))

    # 垂荡响应在 Fn ≈ 0.3 附近有峰值
    fn_factor = fn * math.exp(-fn / 0.35)

    # 方形系数影响：肥大船垂荡响应大
    cb_factor = 0.5 + 0.5 * cb

    base_amplitude = 1.2 * directional_factor * fn_factor * cb_factor

    return max(0.0, base_amplitude)


@dataclass
class ShipHullParams:
    """船型主尺度参数。"""
    l: float  # 水线长 m
    b: float  # 船宽 m
    t: float  # 吃水 m
    cb: float  # 方形系数
    cp: float  # 棱形系数
    ship_type: str  # wigley/series60/kvlcc2


@dataclass
class SamplePoint:
    """单个训练样本。"""
    # 输入特征
    l: float
    b: float
    t: float
    cb: float
    cp: float
    fn: float
    beta_deg: float
    rho: float
    nu: float
    # 输出标签
    rt: float  # 总阻力 N
    rao_roll: float  # 横摇 RAO
    rao_heave: float  # 垂荡 RAO
    # 元数据
    ship_type: str = ""
    sample_id: int = 0


def generate_wigley_samples(n: int = 200, seed: int = 42) -> List[SamplePoint]:
    """生成 Wigley 船型样本。

    Wigley 数学船型：y = ±(b/2)·[1-(2x/L)²]·[1-(z/T)²]
    Cb ≈ 0.444, Cp ≈ 0.667
    """
    rng = np.random.RandomState(seed)
    samples = []

    for i in range(n):
        # Wigley 主尺度变化范围
        l = 50.0 + rng.uniform(-10, 30)  # 40-80 m
        b = l * (0.12 + rng.uniform(-0.02, 0.04))  # B/L ≈ 0.10-0.16
        t = b * (0.4 + rng.uniform(-0.05, 0.15))  # T/B ≈ 0.35-0.55
        cb = 0.444 + rng.uniform(-0.03, 0.03)
        cp = 0.667 + rng.uniform(-0.03, 0.03)

        # Fr 范围：主域 + 少量外推
        if i < n * 0.85:
            fn = 0.1 + rng.uniform(0, 0.4)  # [0.1, 0.5]
        else:
            fn = 0.5 + rng.uniform(0, 0.15)  # [0.5, 0.65] 外推

        beta = rng.uniform(-180, 180)
        rho = RHO_SEA + rng.uniform(-5, 5)
        nu = NU_SEA + rng.uniform(-0.05e-6, 0.05e-6)

        rt = compute_resistance(l, b, t, cb, cp, fn, rho, nu, "wigley")
        rao_roll = compute_rao_roll(l, b, t, cb, fn, beta, rho)
        rao_heave = compute_rao_heave(l, b, t, cb, fn, beta, rho)

        samples.append(SamplePoint(
            l=l, b=b, t=t, cb=cb, cp=cp, fn=fn, beta_deg=beta,
            rho=rho, nu=nu, rt=rt, rao_roll=rao_roll, rao_heave=rao_heave,
            ship_type="wigley", sample_id=i
        ))

    return samples


def generate_series60_samples(n: int = 200, seed: int = 43) -> List[SamplePoint]:
    """生成 Series 60 船型样本。

    Cb = 0.60, 0.65, 0.70, 0.75, 0.80 系列。
    """
    rng = np.random.RandomState(seed)
    samples = []
    cb_values = [0.60, 0.65, 0.70, 0.75, 0.80]

    for i in range(n):
        cb = cb_values[i % len(cb_values)] + rng.uniform(-0.02, 0.02)
        cp = cb + 0.05 + rng.uniform(-0.02, 0.02)

        l = 80.0 + rng.uniform(-20, 60)  # 60-140 m
        b = l * (0.14 + rng.uniform(-0.02, 0.04))
        t = b * (0.35 + rng.uniform(-0.05, 0.15))

        if i < n * 0.85:
            fn = 0.1 + rng.uniform(0, 0.4)
        else:
            fn = 0.5 + rng.uniform(0, 0.15)

        beta = rng.uniform(-180, 180)
        rho = RHO_SEA + rng.uniform(-5, 5)
        nu = NU_SEA + rng.uniform(-0.05e-6, 0.05e-6)

        rt = compute_resistance(l, b, t, cb, cp, fn, rho, nu, "series60")
        rao_roll = compute_rao_roll(l, b, t, cb, fn, beta, rho)
        rao_heave = compute_rao_heave(l, b, t, cb, fn, beta, rho)

        samples.append(SamplePoint(
            l=l, b=b, t=t, cb=cb, cp=cp, fn=fn, beta_deg=beta,
            rho=rho, nu=nu, rt=rt, rao_roll=rao_roll, rao_heave=rao_heave,
            ship_type="series60", sample_id=i
        ))

    return samples


def generate_kvlcc2_samples(n: int = 150, seed: int = 44) -> List[SamplePoint]:
    """生成 KVLCC2 船型样本。

    KVLCC2: L=230m, B=40.8m, T=14.5m, Cb=0.8098, Cp=0.8098
    围绕基准值做 ±10% 扰动。
    """
    rng = np.random.RandomState(seed)
    samples = []

    # KVLCC2 基准
    l_base, b_base, t_base, cb_base, cp_base = 230.0, 40.8, 14.5, 0.8098, 0.8098

    for i in range(n):
        l = l_base * (1.0 + rng.uniform(-0.1, 0.1))
        b = b_base * (1.0 + rng.uniform(-0.1, 0.1))
        t = t_base * (1.0 + rng.uniform(-0.1, 0.1))
        cb = cb_base + rng.uniform(-0.03, 0.03)
        cp = cp_base + rng.uniform(-0.03, 0.03)

        # KVLCC2 通常 Fr ∈ [0.1, 0.2]，但为覆盖训练域扩展到 0.5
        if i < n * 0.85:
            fn = 0.1 + rng.uniform(0, 0.4)
        else:
            fn = 0.5 + rng.uniform(0, 0.15)

        beta = rng.uniform(-180, 180)
        rho = RHO_SEA + rng.uniform(-5, 5)
        nu = NU_SEA + rng.uniform(-0.05e-6, 0.05e-6)

        rt = compute_resistance(l, b, t, cb, cp, fn, rho, nu, "kvlcc2")
        rao_roll = compute_rao_roll(l, b, t, cb, fn, beta, rho)
        rao_heave = compute_rao_heave(l, b, t, cb, fn, beta, rho)

        samples.append(SamplePoint(
            l=l, b=b, t=t, cb=cb, cp=cp, fn=fn, beta_deg=beta,
            rho=rho, nu=nu, rt=rt, rao_roll=rao_roll, rao_heave=rao_heave,
            ship_type="kvlcc2", sample_id=i
        ))

    return samples


def generate_training_dataset(
    n_wigley: int = 200,
    n_series60: int = 200,
    n_kvlcc2: int = 150,
    seed: int = 42
) -> Tuple[np.ndarray, np.ndarray, List[SamplePoint]]:
    """生成完整训练数据集。

    Returns:
        X: (N, 9) 输入特征 [L, B, T, Cb, Cp, Fn, beta, rho, nu]
        Y: (N, 3) 输出标签 [R_T, RAO_roll, RAO_heave]
        samples: 原始样本列表
    """
    all_samples = []
    all_samples.extend(generate_wigley_samples(n_wigley, seed))
    all_samples.extend(generate_series60_samples(n_series60, seed + 1))
    all_samples.extend(generate_kvlcc2_samples(n_kvlcc2, seed + 2))

    # 更新 sample_id
    for i, s in enumerate(all_samples):
        s.sample_id = i

    # 构建特征矩阵
    input_features = ["l", "b", "t", "cb", "cp", "fn", "beta_deg", "rho", "nu"]
    output_features = ["rt", "rao_roll", "rao_heave"]

    X = np.array([[getattr(s, f) for f in input_features] for s in all_samples], dtype=np.float64)
    Y = np.array([[getattr(s, f) for f in output_features] for s in all_samples], dtype=np.float64)

    return X, Y, all_samples


class ShipHullDataset:
    """PyTorch 兼容的船型数据集。"""

    def __init__(self, X: np.ndarray, Y: np.ndarray, samples: Optional[List[SamplePoint]] = None):
        self.X = X
        self.Y = Y
        self.samples = samples or []
        self.n_samples = len(X)

    def __len__(self) -> int:
        return self.n_samples

    def __getitem__(self, idx: int) -> Tuple[np.ndarray, np.ndarray]:
        return self.X[idx], self.Y[idx]

    def get_statistics(self) -> Dict[str, Dict[str, float]]:
        """返回输入/输出的均值和标准差（用于归一化）。"""
        stats = {
            "input": {
                "mean": self.X.mean(axis=0).tolist(),
                "std": self.X.std(axis=0).tolist(),
            },
            "output": {
                "mean": self.Y.mean(axis=0).tolist(),
                "std": self.Y.std(axis=0).tolist(),
            }
        }
        return stats

    def normalize(self) -> Tuple[np.ndarray, np.ndarray, Dict]:
        """标准化输入输出。"""
        stats = self.get_statistics()
        X_mean = np.array(stats["input"]["mean"])
        X_std = np.array(stats["input"]["std"])
        Y_mean = np.array(stats["output"]["mean"])
        Y_std = np.array(stats["output"]["std"])

        # 防止除零
        X_std[X_std < 1e-10] = 1.0
        Y_std[Y_std < 1e-10] = 1.0

        X_norm = (self.X - X_mean) / X_std
        Y_norm = (self.Y - Y_mean) / Y_std

        return X_norm, Y_norm, stats
