"""Nomoto 一阶（K-T）响应模型：闭式解与精确参考量.

模型（Nomoto 1957，单输入单输出一阶近似）：

    T * dpsi/dt + psi = K * delta          （ψ = 首向角，δ = 舵角，均为弧度）
    T * dr/dt + r = K * delta              （r = 首向角速度）

对初始静止、舵角阶跃 δ（t ≥ 0）的精确解：

    r(t)   = K δ (1 - e^{-t/T})
    ψ(t)   = K δ [ t - T (1 - e^{-t/T}) ]
    r_ss   = K δ                    （t → ∞，稳态回转角速度）
    R      = U / r_ss                （稳态回转半径，U 为合速度）
    D_T    = 2 R                     （稳态回转直径 ≈ 战术直径）

这些量都是**闭式精确**的，可作为 1e-9 级判据使用；本模块不含任何经验系数。

单位约定：K [1/s]，T [s]，δ [rad]，U [m/s]，输出单量按调用者指定换算。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class NomotoResponse:
    """K-T 响应模型参数（K [1/s], T [s]）。"""

    K: float
    T: float

    def __post_init__(self) -> None:
        if not (self.K > 0.0):
            raise ValueError(f"K 必须为正（K={self.K}）")
        if not (self.T > 0.0):
            raise ValueError(f"T 必须为正（T={self.T}）")

    # ---- 精确解析解 ---------------------------------------------------
    def r_of_t(self, delta_rad: float, t):
        """首向角速度 r(t) [1/s]，闭式精确。"""
        t = np.asarray(t, dtype=float)
        return self.K * delta_rad * (1.0 - np.exp(-t / self.T))

    def psi_of_t(self, delta_rad: float, t):
        """首向角 ψ(t) [rad]，闭式精确。"""
        t = np.asarray(t, dtype=float)
        return self.K * delta_rad * (t - self.T * (1.0 - np.exp(-t / self.T)))

    def r_steady(self, delta_rad: float) -> float:
        """稳态回转角速度 r_ss = K δ [1/s]。"""
        return self.K * delta_rad

    def radius(self, delta_rad: float, U: float) -> float:
        """稳态回转半径 R = U / (K δ) [m]。"""
        if delta_rad <= 0.0:
            raise ValueError("delta_rad 必须为正（本函数按右舵约定）")
        return U / self.r_steady(delta_rad)

    def diameter(self, delta_rad: float, U: float) -> float:
        """稳态回转直径 D_T = 2R [m]。"""
        return 2.0 * self.radius(delta_rad, U)

    def time_to_fraction(self, frac: float = 0.632) -> float:
        """r(t)/r_ss 达到 frac 的时间：t = -T ln(1-frac)。"""
        if not 0.0 < frac < 1.0:
            raise ValueError("frac 必须在 (0,1)")
        return -self.T * math.log(1.0 - frac)

    # ---- 轨迹 ---------------------------------------------------------
    def trajectory(self, delta_rad: float, U: float, t_end: float, dt: float = 0.02):
        """数值积分回转轨迹（对照闭式 ψ(t) 逐点校验，用于作图）。

        假设合速度恒为 U 且速度方向与首向夹角（漂角）为 0 —— 这是 K-T 响应
        模型的内建简化，**只用于运动学形状比对，不作为绝对基准**。

        返回 dict: t, x, y, psi(闭式), r(闭式), r_num(数值)。
        """
        t = np.arange(0.0, t_end + 0.5 * dt, dt)
        psi = self.psi_of_t(delta_rad, t)
        r_ana = self.r_of_t(delta_rad, t)
        # RK4 数值积分 dx/dt = U cos(psi), dy/dt = U sin(psi)
        x = np.zeros_like(t)
        y = np.zeros_like(t)
        f = lambda tt: np.array([U * math.cos(float(self.psi_of_t(delta_rad, tt))),
                                 U * math.sin(float(self.psi_of_t(delta_rad, tt)))])
        for i in range(len(t) - 1):
            h = t[i + 1] - t[i]
            k1 = f(t[i])
            k2 = f(t[i] + 0.5 * h)
            k4 = f(t[i] + h)
            x[i + 1] = x[i] + (h / 6.0) * (k1[0] + 4.0 * k2[0] + k4[0])
            y[i + 1] = y[i] + (h / 6.0) * (k1[1] + 4.0 * k2[1] + k4[1])
        # 数值 r 由 ψ 差分反演，用于校验解析式
        r_num = np.gradient(psi, t)
        return {"t": t, "x": x, "y": y, "psi": psi, "r": r_ana, "r_num": r_num}


# ---- 便捷函数（无状态接口）--------------------------------------------
def r_of_t(K: float, T: float, delta_rad: float, t):
    return NomotoResponse(K, T).r_of_t(delta_rad, t)


def psi_of_t(K: float, T: float, delta_rad: float, t):
    return NomotoResponse(K, T).psi_of_t(delta_rad, t)


def steady_turning_radius(K: float, delta_deg: float, U: float) -> float:
    """R = U / (K δ)，δ 以度输入。"""
    return NomotoResponse(K, 1.0).radius(math.radians(delta_deg), U)


def tactical_diameter(K: float, delta_deg: float, U: float) -> float:
    """D_T = 2 U / (K δ)，δ 以度输入。"""
    return NomotoResponse(K, 1.0).diameter(math.radians(delta_deg), U)


def identify_K_from_steady_rate(r_steady_deg_s: float, delta_deg: float) -> float:
    """由稳态回转角速度反演增益 K = r_ss / δ [1/s，输入为度]. """
    if delta_deg == 0.0:
        raise ValueError("delta_deg 不能为 0")
    return math.radians(r_steady_deg_s) / math.radians(delta_deg)


def identify_T_from_overshoot_time(t_63pct: float) -> float:
    """由 r 达到 63.2% r_ss 的时间反演时间常数 T = t_63 / ln(...) = t_63（因 1-e^-1=0.632）."""
    return float(t_63pct)


def trajectory(K: float, T: float, delta_deg: float, U: float, t_end: float, dt: float = 0.02):
    return NomotoResponse(K, T).trajectory(math.radians(delta_deg), U, t_end, dt)