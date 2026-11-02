"""海浪谱 / 短期统计独立参考核（verifier 席，m1801）。

原则：本文件不 import 被复核实现（`api.wave_spectrum`、`api.seakeeping_advanced`），
只用 numpy 标准库 + 闭式解析式，因此可作为独立基准。

三层参考
--------
L1 精确解析矩（无自由参数）
    两参数谱  S(w) = A w^-5 exp(-B w^-4)
        ∫0^inf w^n S dw = (A/4) B^((n-4)/4) Γ((4-n)/4),  n < 4
    故
        m0 = A/(4B)
        m2 = (A/4) B^(-1/2) Γ(1/2) = (A/4) sqrt(pi/B)
    ⇒  Tz = 2π sqrt(m0/m2) = 2π^(3/4) B^(-1/4)
    ⇒  Tz/Tp 与 A 无关，只由 B·Tp 决定（ITTC/Bretschneider: B=1.25/ωp^4 ⇒ 0.7102）
    m4 对该谱**对数发散**（w^4 S ~ A/w），故谱宽 ε 依赖积分上限 —— 见 docs。

L2 短期统计 Rayleigh（Longuet-Higgins 1952 / Cartwright 1956）
        E[Hmax]/Hs = sqrt(ln N0 / 2),  N0 = 跨零循环数
    密度法：P(H > h) = exp(-2 h^2 / Hs^2)；1/10 大波均值 = Hs·1.2719(≈)
L3 教科书锚点：ITTC/Bretschneider 谱 Tz = 0.710 Tp（Journée & Massie,
    Offshore Hydromechanics §3.2.2；ITTC 1978 推荐谱），PM 谱同族。
"""
from __future__ import annotations

import math

import numpy as np

G = 9.81
ALPHA_PM = 0.0081  # Phillips 常数（ITTC/PM 共用）
B_ITTC = 1.25      # ITTC/Bretschneider 高频衰减系数（=5/4；峰位条件 ωp=(0.8B)^{1/4}）
B_PM = 1.25        # PM 同族（α=0.0081 必须配 β=1.25 才能让谱峰落在 Tp）


# ---------------------------------------------------------------- 谱（独立实现）
def spectrum_two_param(omega, A: float, B: float):
    """S(w) = A w^-5 exp(-B w^-4)，纯解析式（不做任何归一化）。"""
    w = np.asarray(omega, dtype=float)
    S = np.zeros_like(w)
    m = w > 0
    S[m] = A * w[m] ** -5 * np.exp(-B * w[m] ** -4)
    return S


def ittc_spectrum(omega, Hs: float, Tp: float, normalize: bool = True):
    """ITTC 推荐谱：A=α g², B=1.25·ωp⁴（峰位条件 ωp=(0.8B)^{1/4}）；按 Hs=4√m0 重标定。"""
    wp = 2 * math.pi / Tp
    S = spectrum_two_param(omega, ALPHA_PM * G ** 2, B_ITTC * wp ** 4)
    if normalize:
        m0 = trapezoid(S, np.asarray(omega, dtype=float))
        hs0 = 4 * math.sqrt(m0) if m0 > 0 else 0.0
        S = S * (Hs / hs0) ** 2 if hs0 > 0 else S
    return S


def jonswap_spectrum(omega, Hs: float, Tp: float, gamma: float = 3.3):
    """JONSWAP（Hasselmann 1973），σ=0.07(ω≤ωp)/0.09(ω>ωp)，按 Hs 重标定。"""
    w = np.asarray(omega, dtype=float)
    wp = 2 * math.pi / Tp
    S = np.zeros_like(w)
    m = w > 0
    sigma = np.where(w[m] <= wp, 0.07, 0.09)
    S[m] = (ALPHA_PM * G ** 2 / w[m] ** 5 * np.exp(-1.25 * (wp / w[m]) ** 4)
            * gamma ** np.exp(-0.5 * ((w[m] - wp) / (sigma * wp)) ** 2))
    m0 = trapezoid(S, w)
    hs0 = 4 * math.sqrt(m0) if m0 > 0 else 0.0
    return S * (Hs / hs0) ** 2 if hs0 > 0 else S


def pm_spectrum(omega, Hs: float, Tp: float):
    """PM（α=0.0081, β=1.25），按 Hs 重标定。峰位条件 ωp=(0.8β)^{1/4}。"""
    wp = 2 * math.pi / Tp
    S = spectrum_two_param(omega, ALPHA_PM * G ** 2, B_PM * wp ** 4)
    m0 = trapezoid(S, np.asarray(omega, dtype=float))
    hs0 = 4 * math.sqrt(m0) if m0 > 0 else 0.0
    return S * (Hs / hs0) ** 2 if hs0 > 0 else S


# ---------------------------------------------------------------- L1 解析矩
def analytic_m0(A: float, B: float) -> float:
    return A / (4.0 * B)


def analytic_m2(A: float, B: float) -> float:
    return (A / 4.0) * math.sqrt(math.pi / B)


def analytic_Tz(A: float, B: float) -> float:
    return 2 * math.pi * math.sqrt(analytic_m0(A, B) / analytic_m2(A, B))


def analytic_Tz_over_Tp(Tp: float, beta: float = B_ITTC) -> float:
    """Tz/Tp 闭式：B = beta·ωp⁴ ⇒ Tz/Tp = 2π/( (beta·π)^{1/4} · 2π ) = 1/(beta·π)^{1/4}。
    beta=1.25 ⇒ 0.7106（教科书 0.710，Journée & Massie §3.2.2）。"""
    return 1.0 / (beta * math.pi) ** 0.25


def analytic_m_minus1(A: float, B: float) -> float:
    """m_{-1} = ∫ S/ω dω = (A/4) B^{-5/4} Γ(5/4)（p=-6 的通式）。"""
    return (A / 4.0) * B ** -1.25 * math.gamma(1.25)


def pm_peak_ratio(beta: float = 0.74) -> float:
    """给定 exp(-β(ωp/ω)^4) 参数化，真实峰频/名义 ωp = (0.8β)^{1/4}（β=1.25 ⇒ 1.0）。"""
    return (0.8 * beta) ** 0.25


def rayleigh_Hmax_over_Hs(N0: float) -> float:
    """E[Hmax]/Hs = sqrt(ln N0 / 2)（Rayleigh 峰值期望）。"""
    return math.sqrt(math.log(N0) / 2.0) if N0 > 1 else float("nan")


def rayleigh_exceedance(h_over_Hs: float) -> float:
    """P(H > h) = exp(-2h²/Hs²)（窄谱 Rayleigh）。"""
    return math.exp(-2.0 * h_over_Hs ** 2)


# ---------------------------------------------------------------- 数值工具
def trapezoid(y, x) -> float:
    """梯形积分；numpy 2.x 用 trapezoid，1.x 回退 trapz（参考核自身也需跨版本）。"""
    f = getattr(np, "trapezoid", None) or np.trapz
    return float(f(y, x))


def discrete_moments(omega, S):
    w = np.asarray(omega, dtype=float)
    S = np.asarray(S, dtype=float)
    return {
        "m0": trapezoid(S, w),
        "m2": trapezoid(S * w ** 2, w),
        "m4": trapezoid(S * w ** 4, w),
        "m_minus1": trapezoid(S / w, w),
    }


def omega_grid(Tp: float, n: int = 2000, wmax_factor: float = 5.0):
    """均匀 ω 网格 0 < ω ≤ wmax_factor·ωp（含 0 端起点）。"""
    wp = 2 * math.pi / Tp
    return np.linspace(1e-4 * wp, wmax_factor * wp, n)


def rel_err(calc: float, ref: float) -> float:
    return float(abs(calc - ref) / abs(ref) * 100.0) if ref else float("nan")

def jonswap_tz_ratio(Hs: float = 6.0, Tp: float = 10.0, gamma: float = 3.3,
                     n: int = 20000, fac: float = 20.0) -> float:
    """JONSWAP 的 Tz/Tp：无闭式，用同族独立实现的细网格数值矩给出（不调用被复核代码）。"""
    w = omega_grid(Tp, n, fac)
    S = jonswap_spectrum(w, Hs, Tp, gamma)
    mm = discrete_moments(w, S)
    return 2 * math.pi * math.sqrt(mm["m0"] / mm["m2"]) / Tp


def pm_tz_ratio(beta: float = B_PM) -> float:
    return analytic_Tz_over_Tp(1.0, beta)
