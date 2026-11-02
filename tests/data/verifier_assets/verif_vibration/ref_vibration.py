"""振动噪声域**独立**参考实现（不 import 被测模块，全部从第一性原理重写）。

用途：给 backend/api/domain/ship_vibration.py ∪ 的每个量造一条**独立算路**，
使"通过"意味着两条互不共享代码的路径在同一物理定义下一致，而不是自证。

独立算路一览
------------
V1  梁特征值      ：平台用 cos(l)cosh(l)∓1 扫符号+二分；本文件用等价形式
                    cos(l) = ±1/cosh(l) + Brent 求根（不同方程形式 + 不同算法），
                    并另备 Blevins/教科书的通用常数（4.73004… 等）作第二来源。
V2  梁频率        ：量纲闭式 + 幂律标度（f ∝ λ²/L², f ∝ sqrt(EI/ρA)），标度为
                    精确恒等式，与实现无关。
V3  梁 FEM        ：本文件用**闭式固有频率**当基准，测平台的 Hermite 单元误差与
                    收敛阶（GCI 口径），不用平台解析式自证。
V4  板弯曲刚度    ：D = E h³ / (12(1-ν²)) 直接闭式。
V5  SSSS 板频率   ：本文件用**解析求导 + 高阶 Gauss–Legendre 积分**的 Rayleigh 商
                    （平台用 np.gradient + 梯形法，误差来源不同），另用 Navier
                    闭式解二次核对。
V6  A 计权        ：IEC 61672-1 解析式独立重写 + 标准表值（圆整到 0.1 dB）核对，
                    双重来源；1 kHz 处必须精确为 0.00 dB。
V7  吻合频率      ：本文件由弯曲波速 c_B(ω) = (ω²D/ρh)^(1/4) 与声速相等的**求根**
                    得到，不经平台那条闭式代数式；另测 f_c ∝ 1/h 等标度。
V8  质量定律      ：本文件用**复阻抗**算透射系数 τ(θ) = |2Z/(2Z+jωm cosθ)|²，
                    与平台的实数闭式互不共享代码。
V9  场入射 -5 dB  ：本文件对 τ(θ) 在 0≤θ≤78° 上按 cosθ sinθ 权重做**数值积分**
                    （扩散场定义），量化 "-5 dB 经验约定" 的偏差（诚实局限项）。
V10 隔振传递率    ：SDOF 精确解 + 与 ζ 无关的恒等式 T(r=√2)=1、T(r=1)=1/(2ζ)。
V11 dB 能量代数   ：N 个同值级叠加 = L+10log10 N，顺序无关，倍频程能量守恒。
V12 激励与裕度    ：BPF/点火频定义式 + 裕度判据边界。
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq

# --------------------------------------------------------------------------
# 物理常数（SI）
# --------------------------------------------------------------------------
AIR_RHO = 1.225      # kg/m^3  (20 degC)
AIR_C = 343.0        # m/s


# ==========================================================================
# V1  梁特征值：独立方程形式 + Brent
# ==========================================================================
def beam_lambda_ref(bc: str, n_modes: int = 5) -> List[float]:
    """均匀 Euler–Bernoulli 梁无量纲特征值（独立求根）。

    用乘开前的等价形式（右侧为 1/cosh，不出现乘积相消）：
        C-C / F-F : cos(l) = 1/cosh(l)
        C-F       : cos(l) = -1/cosh(l)
        C-P       : sin(l) = cos(l) tanh(l)
        P-P       : l = n*pi
    根区间按各 BC 的渐近位置给出，Brent 求根，与平台的 5e-3 网格扫符号无关。
    """
    bc = bc.lower()
    if bc in ("pinned-pinned", "pp", "simply-supported", "ss"):
        return [n * math.pi for n in range(1, n_modes + 1)]

    if bc in ("clamped-clamped", "cc", "fixed-fixed", "free-free", "ff"):
        f = lambda x: math.cos(x) - 1.0 / math.cosh(x)
        centers = [(k + 0.5) * math.pi for k in range(0, n_modes + 4)]
        half = 0.62 * math.pi
    elif bc in ("clamped-free", "cf", "cantilever", "fixed-free"):
        f = lambda x: math.cos(x) + 1.0 / math.cosh(x)
        centers = [(k + 0.5) * math.pi for k in range(0, n_modes + 4)]
        half = 0.62 * math.pi
    elif bc in ("clamped-pinned", "cp", "fixed-pinned", "propped"):
        f = lambda x: math.sin(x) - math.cos(x) * math.tanh(x)
        # tan(l)=tanh(l) 的根收敛到 pi/4 + k*pi；极点位于 pi/2 + k*pi，避开
        centers = [math.pi / 4.0 + k * math.pi for k in range(1, n_modes + 4)]
        half = 0.30 * math.pi
    else:
        raise ValueError(f"unsupported bc: {bc!r}")

    roots: List[float] = []
    for c in centers:
        a, b = c - half, c + half
        try:
            fa, fb = f(a), f(b)
        except (ValueError, ZeroDivisionError):
            continue
        if fa * fb > 0:
            continue
        r = brentq(f, a, b, xtol=1e-15, rtol=4 * np.finfo(float).eps, maxiter=200)
        if r > 1.0 and all(abs(r - o) > 1e-6 for o in roots):
            roots.append(r)
        if len(roots) >= n_modes:
            break
    if len(roots) < n_modes:
        raise RuntimeError(f"ref root finding failed for {bc}: {len(roots)} roots")
    return roots[:n_modes]


# 第二来源：教科书通用常数（Blevins, Formulas for Natural Frequency and Mode Shape,
# 均匀梁前 4 阶无量纲 lambda；亦见任何振动手册）。用于交叉核对数值求根本身。
BEAM_LAMBDA_TABLE: Dict[str, Tuple[float, ...]] = {
    "cc": (4.730040744862704, 7.853204624095838, 10.995607838001671, 14.137165491257464),
    "ff": (4.730040744862704, 7.853204624095838, 10.995607838001671, 14.137165491257464),
    "cf": (1.875104068711961, 4.694091132974175, 7.854757438237613, 10.995540734875467),
    "cp": (3.926602312047919, 7.068582745628732, 10.210176124166829, 13.351768777756623),
    "pp": (math.pi, 2 * math.pi, 3 * math.pi, 4 * math.pi),
}


def beam_freq_ref(bc: str, E: float, I: float, rho: float, A: float, L: float,
                  n_modes: int = 5) -> List[float]:
    """f_n = λ_n²/(2π L²) * sqrt(EI/(ρA))，用独立求根的 λ。"""
    lams = beam_lambda_ref(bc, n_modes)
    k = math.sqrt(E * I / (rho * A)) / (2.0 * math.pi * L * L)
    return [l * l * k for l in lams]


# ==========================================================================
# V4/V5  板：刚度、SSSS 板 Rayleign 商（解析导数 + Gauss–Legendre）
# ==========================================================================
def plate_rigidity_ref(E: float, nu: float, h: float) -> float:
    return E * h ** 3 / (12.0 * (1.0 - nu ** 2))


def plate_navier_freq(m: int, n: int, a: float, b: float, E: float, nu: float,
                      rho: float, h: float) -> float:
    """SSSS 矩形 Kirchhoff 板 Navier 闭式解 f_mn = (π/2) sqrt(D/ρh) ((m/a)²+(n/b)²)."""
    D = plate_rigidity_ref(E, nu, h)
    return (math.pi / 2.0) * math.sqrt(D / (rho * h)) * ((m / a) ** 2 + (n / b) ** 2)


def plate_rayleigh_ref(m: int, n: int, a: float, b: float, E: float, nu: float,
                       rho: float, h: float, n_gauss: int = 40) -> float:
    """Rayleigh 商用**解析二阶导** + Gauss–Legendre 张量积分（平台用梯度差分）。

    w = sin(mπx/a) sin(nπy/b)，∇²w = -π²((m/a)²+(n/b)²) w  →  商 = D κ⁴/(ρh)，
    数值积分只用来验证 ∫ 的分量，误差应到机器精度（用作 V5 的独立算路）。
    """
    D = plate_rigidity_ref(E, nu, h)
    xg, wg = np.polynomial.legendre.leggauss(n_gauss)
    x = 0.5 * a * (xg + 1.0)
    wx = 0.5 * a * wg
    y = 0.5 * b * (xg + 1.0)
    wy = 0.5 * b * wg
    ky, kx = n * math.pi / b, m * math.pi / a
    W = np.outer(np.sin(kx * x), np.sin(ky * y))
    Wxx = np.outer(-kx ** 2 * np.sin(kx * x), np.sin(ky * y))
    Wyy = np.outer(np.sin(kx * x), -ky ** 2 * np.sin(ky * y))
    lap = Wxx + Wyy
    num = np.einsum("i,j,ij->", wx, wy, lap ** 2)
    den = np.einsum("i,j,ij->", wx, wy, W ** 2)
    omega2 = D * num / (rho * h * den)
    return math.sqrt(omega2) / (2.0 * math.pi)


# ==========================================================================
# V6  A 计权（IEC 61672-1 解析式独立重写）
# ==========================================================================
# 标准表值（IEC 61672-1 Table 1，圆整到 0.1 dB）——第二来源，防止"同一公式抄两遍"
IEC_A_TABLE: Dict[float, float] = {
    31.5: -39.4, 63.0: -26.2, 125.0: -16.1, 250.0: -8.6, 500.0: -3.2,
    1000.0: 0.0, 2000.0: 1.2, 4000.0: 1.0, 8000.0: -1.1, 16000.0: -6.6,
}


def a_weighting_ref(f: float) -> float:
    """IEC 61672-1 A 计权：20 log10 R_A(f) + 2.00，极/零取 20.6/107.7/737.9/12194 Hz。"""
    f2 = f * f
    ra = (12194.0 ** 2) * f2 ** 2
    ra /= (f2 + 20.6 ** 2)
    ra /= math.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
    ra /= (f2 + 12194.0 ** 2)
    return 20.0 * math.log10(ra) + 2.00


# ==========================================================================
# V7  吻合频率：由弯曲波速与声速相等求根（不经平台闭式）
# ==========================================================================
def coincidence_freq_ref(E: float, nu: float, rho: float, h: float,
                         c0: float = AIR_C) -> float:
    D = plate_rigidity_ref(E, nu, h)

    def g(w):
        # c_B(w) = (w^2 D / rho h)^(1/4)
        return (w * w * D / (rho * h)) ** 0.25 - c0

    wc = brentq(g, 1e-6, 1e7, xtol=1e-12, rtol=1e-14, maxiter=200)
    return wc / (2.0 * math.pi)


# ==========================================================================
# V8  质量定律：复阻抗算透射系数
# ==========================================================================
def panel_tl_normal_ref(f: float, surface_density: float, rho0: float = AIR_RHO,
                        c0: float = AIR_C) -> float:
    """正入射单层板隔声量：Z0 = rho0 c0，τ = |2Z0 / (2Z0 + j ω m)|²，TL = -10 log10 τ."""
    Z0 = rho0 * c0
    wm = 2.0 * math.pi * f * surface_density
    tau = abs(2.0 * Z0 / complex(2.0 * Z0, wm)) ** 2
    return -10.0 * math.log10(tau)


def panel_tl_diffuse_ref(f: float, surface_density: float, rho0: float = AIR_RHO,
                         c0: float = AIR_C, theta_max_deg: float = 78.0,
                         n_quad: int = 4001) -> float:
    """扩散场（场入射）隔声量：τ(θ)=1/(1+(ω m cosθ/(2ρc))²)，按 cosθ sinθ 加权积分。

    TL = -10 log10 ∫τ cosθ sinθ dθ / ∫cosθ sinθ dθ，θ∈[0, θ_max]（78° 截断为
    混响室/工程惯例的掠入射排除角）。用于量化平台 "-5 dB 常数" 约定的偏差。
    """
    Z0 = 2.0 * rho0 * c0
    wm = 2.0 * math.pi * f * surface_density
    th = np.radians(np.linspace(0.0, theta_max_deg, n_quad))
    tau = 1.0 / (1.0 + (wm * np.cos(th) / Z0) ** 2)
    num = np.trapezoid(tau * np.cos(th) * np.sin(th), th) if hasattr(np, "trapezoid") \
        else np.trapz(tau * np.cos(th) * np.sin(th), th)
    den = np.trapezoid(np.cos(th) * np.sin(th), th) if hasattr(np, "trapezoid") \
        else np.trapz(np.cos(th) * np.sin(th), th)
    return -10.0 * math.log10(num / den)


# ==========================================================================
# V10 隔振传递率（SDOF 精确解）
# ==========================================================================
def transmissibility_ref(f: float, f_n: float, zeta: float = 0.05) -> float:
    r = f / f_n
    return math.sqrt((1.0 + (2.0 * zeta * r) ** 2) /
                     ((1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2))


# ==========================================================================
# V11 dB 能量代数
# ==========================================================================
def combine_db_ref(levels: Sequence[float]) -> float:
    return 10.0 * math.log10(sum(10.0 ** (float(v) / 10.0) for v in levels))


# ==========================================================================
# 工具
# ==========================================================================
def rel_err(got: float, ref: float, floor: float = 1e-12) -> float:
    denom = max(abs(ref), floor)
    return abs(got - ref) / denom