"""2D 剖面附加质量独立解析参考（零频 / 自由面镜像法）—— m1797 复核资产。

不含平台内核代码的任何调用，纯解析 + 经典极限自校验。

一、一般性定理（严格，与剖面形状无关）
    ν = ω²T/g → 0（长波极限）时自由面波动消失，线性自由面条件
    ∂φ/∂z - (ω²/g)φ = 0 退化为 ∂φ/∂z = 0 ⇒ 自由面是刚性壁。
    由镜像法，自由面剖面 S 的辐射问题等价于无限流体中双体 S ∪ S' 的一半：
        A33(0) = (1/2) · m_a(S ∪ S')                       ……(1)
    （附加质量是速度的二次型，两半部能量相加，自由面剖面只占一半流体域。）

二、半椭圆的闭式解（经典）
    剖面 = 半椭圆（水线半宽 a、吃水 T），双体 = 整椭圆。
    整椭圆在无限流体中沿 y 的 2D 附加质量 m_a = ρπa²（a 为垂直于运动方向的半轴），
    于是      A33(0) = ρπa²/2                               ……(2)
    剖面排水面积 A_s = πaT/2，故
        A33(0) / (ρ·A_s) = a / T                            ……(3)
    (2) 的两个经典极限自校验：
        · a = T（半圆）: A33(0) = ρπa²/2 = ρ·A_s → 比值 1                      ✔
        · T → 0（半宽 a 的平板，固定 a）: 双体=宽 2a 平板，m_a = ρπa²，
          剖面面积 → 0 ⇒ 比值 → ∞（平板附加质量为有限值而排水面积为零）          ✔
    物理含义：零频附加质量由**水线宽度**主导，与排水面积无固定比例；
    任何“A33 = ρ·剖面面积×形状无关因子”的构式只在半圆剖面（a=T）成立。

三、平台内核可检验的事实
    内核 section_coefficients() 给出 A33_i = ρ·A_i·f_a(ν)，f_a(0)=1 —— 与 a_i/T 无关。
    故内核对任意剖面在 ν→0 都取“半圆答案”，与本参考的偏差 = (1 - a/T)（相对）。

四、本文档不覆盖的量（如实声明）
    · ν = O(1) 的完整频响需要 Ursell/Lewis 级数或自由面 Green 函数 BEM（见 docs 局限节）；
    · B33（辐射阻尼）量级同样未独立复核，只核验了 ν→0 与 ν→∞ 的极限趋势；
    · 非椭圆真实剖面需数值双体解，本文件用“等价半椭圆”（同半宽、同剖面面积）作代理参考，
      并明确其近似性质。
"""
from __future__ import annotations

import math

RHO_DEFAULT = 1025.0


# --------------------------------------------------------------- 闭式解
def ellipse_added_mass_infinite(semi_axis_perp: float, rho: float = RHO_DEFAULT) -> float:
    """无限流体中椭圆沿 y 的 2D 附加质量 ρπa²（a = 垂直于运动方向的半轴）。"""
    return rho * math.pi * semi_axis_perp ** 2


def hemi_ellipse_zero_freq_added_mass(a: float, rho: float = RHO_DEFAULT) -> float:
    """半椭圆剖面 ν→0 垂荡附加质量 A33 = ρπa²/2（式 (2)，与吃水 T 无关）。"""
    return 0.5 * ellipse_added_mass_infinite(a, rho)


def zero_freq_ratio(a_over_T: float) -> float:
    """A33(0)/(ρ·剖面面积) = a/T（式 (3)）。"""
    return a_over_T


def equivalent_ellipse_draft(area: float, half_beam: float) -> float:
    """由（剖面面积, 水线半宽）反解等价半椭圆吃水：T_eq = 2A_s/(π a)。"""
    if half_beam <= 0.0:
        return 0.0
    return 2.0 * area / (math.pi * half_beam)


def kernel_ratio_zero_freq() -> float:
    """内核 f_a(ν→0) = 1（与形状无关）—— 供测试钉死。"""
    return 1.0


def kernel_fa(nu: float) -> float:
    """内核附加质量频率因子 f_a(ν) = 1 - 0.25ν²/(1+ν²)（形状无关）。"""
    return 1.0 - 0.25 * nu * nu / (1.0 + nu * nu)


# --------------------------------------------------- 长波 RAO 解析极限
def rao_long_wave_limits() -> dict:
    """kL→0、零航速的解析极限（quasi-static + 波面跟随）。

    · 垂荡：z → 波面垂向位移 ⇒ heave RAO = |z|/a → 1
    · 纵摇：船跟随波面倾角 ⇒ θ → k·a ⇒ pitch RAO = |θ|/(k a) → 1
    · 横摇：顶浪按对称性无横摇激励 ⇒ 与频率无关地趋于 0
    """
    return {"heave_rao": 1.0, "pitch_rao": 1.0, "roll_rao": 0.0}


def deep_water_dispersion(omega: float, g: float = 9.80665) -> float:
    """深水色散 k = ω²/g（精确）。"""
    return omega * omega / g