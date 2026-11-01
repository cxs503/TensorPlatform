"""结构域独立解析参考实现（板格屈曲 / 极限强度 / 疲劳寿命）。

本模块 **不依赖** 船舶平台服务（:8001），只实现教科书闭式解，用于对服务端点做
独立复核。所有常数均标注出处，便于第三方复现。

参考出处
--------
[1] Timoshenko & Gere, *Theory of Elastic Stability*, 2nd ed., McGraw-Hill 1961,
    Ch.9 (plate buckling): 简支板单轴受压 k = min_m (m·b/a + a/(m·b))^2；
    四边固支方板 k = 6.97；四边简支板纯剪 k_s = 5.34 + 4(b/a)^2。
[2] DNV-RP-C201 *Buckling strength of plated structures* (2010) —— 板格柱状屈曲
    σ_cr = k·π²E/(12(1-ν²))·(t/b)²，Johnson-Ostenfeld 弹塑性修正。
[3] IACS CSR / DNV-RP-C201 App. —— Johnson-Ostenfeld:
    σ_u = σ_y·(1 - σ_y/(4σ_cr))  (σ_cr > σ_y/2)；否则 σ_u = σ_cr。
[4] DNV-RP-C203 *Fatigue design of offshore steel structures*, Table 2-1,
    curve C (in air): log10(a1)=12.192, m1=3 (N<=1e7)；
    log10(a2)=15.606, m2=5 (N>1e7)；拐点 N=1e7。
    自检：Δσ@2e6 = (10^12.192/2e6)^(1/3) = 91.93 MPa（与 C203 表值一致）。

单位约定：SI（Pa, m, N）。对外报告时换算为 MPa。
"""

from __future__ import annotations

import math

E_STEEL = 2.06e11       # Pa, 普通船用钢杨氏模量
NU_STEEL = 0.3          # 泊松比
RHO_STEEL = 7850.0      # kg/m^3

# DNV-RP-C203 曲线 C（in air）
DNV_C_LOG_A1, DNV_C_M1 = 12.192, 3.0
DNV_C_LOG_A2, DNV_C_M2 = 15.606, 5.0
DNV_C_KNEE_N = 1.0e7


# ───────────────────────── 板格屈曲（闭式） ─────────────────────────
def plate_stiffness(E: float, t: float, nu: float) -> float:
    """板弯曲刚度 D = E t^3 / (12(1-ν²))  [N·m]."""
    return E * t ** 3 / (12.0 * (1.0 - nu ** 2))


def k_uniaxial_simply_supported(a: float, b: float, m_max: int = 60) -> tuple[float, int]:
    """单轴受压、四边简支板的屈曲系数与半波数。

    a = 沿压力方向边长, b = 垂直压力方向板宽。
    k(a/b) = min_{m} (m·b/a + a/(m·b))²   [1] Table 9.2
    返回 (k, m)。
    """
    best_k, best_m = float("inf"), 1
    for m in range(1, m_max + 1):
        k = (m * b / a + a / (m * b)) ** 2
        if k < best_k:
            best_k, best_m = k, m
    return best_k, best_m


def k_uniaxial_clamped(a: float, b: float) -> float:
    """四边固支板单轴受压屈曲系数（[1] Table 9.6 的工程取值）。

    a/b = 1.0 → 6.97；a/b >= 1.4 之后基本稳定在 ~7.0（±1%），
    这里给保守下界 6.97（a/b=1 的确值），用于"固支必须显著高于简支"的判定。
    """
    return 6.97


def sigma_cr(k: float, E: float, t: float, b: float, nu: float) -> float:
    """弹性屈曲应力 σ_cr = k·π²E/(12(1-ν²))·(t/b)²  [Pa]."""
    return k * math.pi ** 2 * E / (12.0 * (1.0 - nu ** 2)) * (t / b) ** 2


def k_shear_simply_supported(a: float, b: float) -> float:
    """四边简支板纯剪屈曲系数 [1] Table 9.3：k_s = 5.34 + 4(b/a)²（a>=b）。

    a < b 时按长边/短边互换：k_s = 5.34·(a/b)² + 4。
    """
    if a >= b:
        return 5.34 + 4.0 * (b / a) ** 2
    return 5.34 * (a / b) ** 2 + 4.0


def tau_cr(a: float, b: float, E: float, t: float, nu: float) -> float:
    """纯剪屈曲应力 τ_cr = k_s·π²D/(b_short²·t)  [Pa]（b_short = min(a,b)）。"""
    b_short = min(a, b)
    return k_shear_simply_supported(a, b) * math.pi ** 2 * plate_stiffness(E, t, nu) / (b_short ** 2 * t)


# ───────────────────────── 弹塑性极限强度 ─────────────────────────
def johnson_ostenfeld(sigma_cr: float, sigma_y: float) -> float:
    """Johnson-Ostenfeld 弹塑性修正后的极限强度 [3]。

    σ_u = σ_y(1 - σ_y/(4σ_cr))  若 σ_cr > σ_y/2；否则 σ_u = σ_cr。
    """
    if sigma_cr <= sigma_y / 2.0:
        return sigma_cr
    return sigma_y * (1.0 - sigma_y / (4.0 * sigma_cr))


# ───────────────────────── 疲劳（DNV-RP-C203 曲线 C） ─────────────────────────
def dnv_c_life(dsigma: float) -> float:
    """DNV-RP-C203 曲线 C（in air）在应力范围 dsigma[Pa] 下的循环寿命 [cycles]."""
    if dsigma <= 0:
        return float("inf")
    d_mpa = dsigma / 1e6
    n_low = 10.0 ** DNV_C_LOG_A1 / d_mpa ** DNV_C_M1
    if n_low <= DNV_C_KNEE_N:
        return n_low
    # 双斜率：超过拐点后 m=5
    dsigma_knee = (10.0 ** DNV_C_LOG_A1 / DNV_C_KNEE_N) ** (1.0 / DNV_C_M1)  # MPa
    return DNV_C_KNEE_N * (dsigma_knee / d_mpa) ** DNV_C_M2


def dnv_c_design_stress(n_cycles: float) -> float:
    """曲线 C 在给定循环数下的许用应力范围 [MPa]（逆解，用于自检）。"""
    if n_cycles <= DNV_C_KNEE_N:
        return (10.0 ** DNV_C_LOG_A1 / n_cycles) ** (1.0 / DNV_C_M1)
    dsigma_knee = (10.0 ** DNV_C_LOG_A1 / DNV_C_KNEE_N) ** (1.0 / DNV_C_M1)
    return dsigma_knee * (DNV_C_KNEE_N / n_cycles) ** (1.0 / DNV_C_M2)


# ───────────────────────── 板格几何（纵骨间距/肋距） ─────────────────────────
def panel_critical_stress(longitudinal_spacing: float, frame_spacing: float,
                          plate_thickness: float, E: float = E_STEEL,
                          nu: float = NU_STEEL) -> dict:
    """典型船底板格（纵骨间距 × 肋距）单轴受压弹性屈曲应力。

    保守取 b = 纵骨间距（长边受压时 k→4 的下界），返回含 k 与半波数的字典。
    """
    a, b = frame_spacing, longitudinal_spacing
    k, m = k_uniaxial_simply_supported(a, b)
    return {
        "k": k,
        "m": m,
        "sigma_cr": sigma_cr(k, E, plate_thickness, b, nu),
        "tau_cr": tau_cr(a, b, E, plate_thickness, nu),
        "D": plate_stiffness(E, plate_thickness, nu),
    }


def rel_err(x: float, ref: float) -> float:
    """相对误差 |x-ref|/|ref|（ref=0 时返回 abs(x)）。"""
    if ref == 0:
        return abs(x)
    return abs(x - ref) / abs(ref)