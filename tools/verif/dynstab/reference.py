#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动稳性（dynamic-stability）独立参考实现 —— 零平台依赖。

被复核对象::

    POST /api/v1/dynamic-stability/analyze        (summary="动稳性分析")
      backend/api/dynamic_stability.py :: DynamicStabilityAnalyzer.analyze
    POST /api/v1/dynamic-stability/roll-motion    (summary="横摇运动分析")
      backend/api/dynamic_stability.py :: RollMotionAnalyzer.analyze

规范/文献依据
--------------
1. **天气衡准面积定义** —— IS Code 2008（MSC.267(85)）Part A §2.3：
       lw1 = P·A·Z / (1000·g·Δ)                      [m], P = 504 Pa
       lw2 = 1.5·lw1                                 [m]
       θ0  : 定常风 lw1 下的静横倾角（GZ(θ0) = lw1）
       a   = ∫_0^θ0 (lw2 − GZ) dθ                    [m·rad]
       b   = ∫_θ0^θf GZ dθ,  θf = min(θ_进水, 50°)    [m·rad]
       判据 b ≥ a（等价 b/a ≥ 1），并要求 θ0 ≤ 16°
   注意 a 的等级：a 用 lw2（阵风）而非 lw1，且积分区间是 0…θ0 而不是 0…θf。
   本模块只做「规范表达 vs 平台表达」的定义式比对，不引入经验拟合自由度。

2. **横摇固有周期** —— 线性横摇 T_r = 2π·k / √(g·GM)，k 为横摇回转半径。
   常用工程取值 k ≈ 0.35·B（Barrass《Ship Stability》，IMO 横摇周期经验式区间
   0.32B–0.40B）；本模块以 k=0.35B 为主定义、并给 0.30B–0.45B 区间带，
   故 CONF 等级 = CONF_MED（文献取值带，非单一定值）。

3. **横摇幅值** —— 线性横摇理论：波面有效波倾角 α_a = (H/2)·k_w，
   k_w = ω²/g（深水），幅频响应 |H_θ| = 1/√((1−ν²)² + (2ζν)²)，ν = T_r/T_e。
   横摇幅值 φ_a = α_a·|H_θ|  [rad]。对 CONF_MED：ζ 取平台声明值。
   该式与 Hs 成线性，是「非线性/粘性阻尼修正前」的基线，适合做量级与量纲判据。

纪律：本文件不 import 任何平台代码；所有函数纯算术，可离线复算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

G = 9.80665          # m/s²
RHO_AIR = 1.225      # kg/m³
P_WIND = 504.0       # N/m², IS Code 2008 §2.3.1 定常风压
LW2_FACTOR = 1.5     # IS Code 2008 §2.3.3 阵风倍率
THETA_F_CAP = 50.0   # deg，IS Code 2008 §2.3.4 面积积分上限
K_ROLL_NOM = 0.35    # 横摇回转半径 / B（主定义）
K_ROLL_BAND = (0.30, 0.45)
G_PLATFORM = 9.81     # 平台代码常量（backend/api/dynamic_stability.py: self.g = 9.81）


# --------------------------------------------------------------------------
# 算例与 GZ 曲线（解析构造，故面积有闭式真值可对）
# --------------------------------------------------------------------------
@dataclass
class Ship:
    """算例船：GZ 曲线由解析壁式段 + 甲板浸没后线性衰减段构成。

    GZ(θ) = GM·sinθ                                  , θ ≤ θ_deck
    GZ(θ) = GM·sinθ · (θ_v − θ)/(θ_v − θ_deck)        , θ_deck < θ < θ_v
    GZ(θ) = 0                                        , θ ≥ θ_v

    θ ≤ θ_deck 段有闭式积分 GM(cosθ_lo − cosθ_hi)，用于给数值积分做真值闸。
    """

    name: str
    L: float
    B: float
    T: float
    D: float
    Cb: float
    displacement: float      # t
    GM: float                # m
    theta_deck: float        # deg
    theta_vanish: float      # deg
    wind_area: float = 0.0   # m²（缺省按平台启发式 L(D−T)·0.7）
    wind_lever: float = 0.0  # m （缺省按平台启发式 (D−T)/2 + T/2）

    def __post_init__(self) -> None:
        if self.wind_area <= 0:
            self.wind_area = self.L * (self.D - self.T) * 0.7
        if self.wind_lever <= 0:
            self.wind_lever = (self.D - self.T) / 2.0 + self.T / 2.0

    # -- GZ ---------------------------------------------------------------
    def gz(self, theta_deg: float) -> float:
        if theta_deg <= 0.0 or theta_deg >= self.theta_vanish:
            return 0.0
        base = self.GM * math.sin(math.radians(theta_deg))
        if theta_deg <= self.theta_deck:
            return base
        return base * (self.theta_vanish - theta_deg) / (self.theta_vanish - self.theta_deck)

    def gz_table(self, step: float = 5.0, start: float = 0.0) -> List[Dict[str, float]]:
        n = int(round(self.theta_vanish / step))
        out = []
        for i in range(n + 1):
            a = start + i * step
            if a > self.theta_vanish + 1e-9:
                break
            out.append({"heel": float(a), "GZ": round(self.gz(a), 6)})
        return out


# --------------------------------------------------------------------------
# 1) 风倾力臂与天气衡准面积（IS Code 2008 §2.3）
# --------------------------------------------------------------------------
def wind_heeling_lever(P: float, A: float, Z: float, displacement_t: float) -> float:
    """lw1 = P·A·Z/(1000·g·Δ)  [m]；Δ 单位 t。"""
    return P * A * Z / (1000.0 * G * displacement_t)


def _gz_integral(ship: Ship, lo: float, hi: float, n: int = 20000) -> float:
    """∫_lo^hi GZ dθ，梯形法细网格（[m·rad]，θ 单位 deg 输入）。"""
    if hi <= lo:
        return 0.0
    h = (hi - lo) / n
    s = 0.0
    for i in range(n + 1):
        w = 0.5 if i in (0, n) else 1.0
        s += w * ship.gz(lo + i * h)
    return s * h * math.pi / 180.0


def gz_area_analytic_wallside(ship: Ship, lo: float, hi: float) -> float:
    """壁式段 ∫ GZ dθ 闭式 = GM(cos lo − cos hi) [m·rad]（要求 hi ≤ θ_deck）。"""
    assert hi <= ship.theta_deck + 1e-12
    return ship.GM * (math.cos(math.radians(lo)) - math.cos(math.radians(hi)))


def steady_heel(ship: Ship, lw1: float, tol: float = 1e-10) -> Tuple[float, bool]:
    """解 GZ(θ0) = lw1（θ0 ∈ [0, θ_deck]）。返回 (θ0[deg], 是否存在平衡角)。"""
    if lw1 <= 0:
        return 0.0, True
    lo, hi = 0.0, ship.theta_deck
    if ship.gz(hi) < lw1:
        # 壁式段内无交点：风倾力臂超过该段最大复原力臂 → 只能取 θ_deck 并标记
        return hi, False
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if ship.gz(mid) < lw1:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi), True


def weather_areas(ship: Ship, lw1: float, theta_f: float = THETA_F_CAP,
                  lw2_factor: float = LW2_FACTOR,
                  integrate_a_from_zero: bool = True) -> Dict[str, float]:
    """IS Code 2008 §2.3 的 a / b / ratio。

    返回 dict: lw1, lw2, theta0, theta_f, a, b, ratio, has_steady_heel
    （theta 单位 deg；a、b 单位 m·rad；ratio = b/a，>1 判据满足）
    """
    lw2 = lw2_factor * lw1
    t0, ok = steady_heel(ship, lw1)
    theta_f_eff = min(theta_f, float(ship.theta_vanish))
    # a = ∫_0^θ0 (lw2 − GZ) dθ
    a = lw2 * math.radians(t0) - _gz_integral(ship, 0.0, t0)
    # b = ∫_θ0^θf GZ dθ
    b = _gz_integral(ship, t0, theta_f_eff)
    ratio = (b / a) if a > 1e-12 else float("inf")
    return {
        "lw1": lw1,
        "lw2": lw2,
        "theta0": t0,
        "theta_f": theta_f_eff,
        "a": a,
        "b": b,
        "ratio": ratio,
        "has_steady_heel": 1.0 if ok else 0.0,
    }


# --------------------------------------------------------------------------
# 2) 横摇固有周期 / 线性横摇响应
# --------------------------------------------------------------------------
def roll_natural_period(B: float, GM: float, k_ratio: float = K_ROLL_NOM,
                        g: float = G) -> float:
    """T_r = 2π·k/√(g·GM)，k = k_ratio·B [s]。"""
    if GM <= 0:
        return float("inf")
    return 2.0 * math.pi * k_ratio * B / math.sqrt(g * GM)


def roll_rao(Te: float, Tr: float, zeta: float) -> float:
    """线性横摇幅频响应 |H_θ| = 1/√((1−ν²)² + (2ζν)²)，ν = T_r/T_e。"""
    nu = Tr / Te
    return 1.0 / math.sqrt((1.0 - nu ** 2) ** 2 + (2.0 * zeta * nu) ** 2)


def roll_amplitude_deg(Hs: float, Te: float, Tr: float, zeta: float,
                       g: float = G) -> Dict[str, float]:
    """线性横摇理论幅值（度）。α_a = k_w·H/2，k_w = ω²/g（深水）。"""
    omega = 2.0 * math.pi / Te
    k_w = omega ** 2 / g
    alpha = k_w * (Hs / 2.0)
    rao = roll_rao(Te, Tr, zeta)
    return {
        "wave_slope_rad": alpha,
        "rao": rao,
        "amp_deg": math.degrees(alpha * rao),
    }


# --------------------------------------------------------------------------
# 3) 平台表达式复刻（仅用于反事实闸门与离线复现，非参考实现）
# --------------------------------------------------------------------------
def platform_analyze_replica(ship: Ship, gz_table: Sequence[Dict[str, float]],
                             wind_speed: float, Hs: float, Te: float,
                             roll_damping: float = 0.1,
                             P_override: float | None = None) -> Dict[str, float]:
    """逐行复刻 DynamicStabilityAnalyzer.analyze 的算式（2026-11-02 基线）。

    复刻目的：① 与在线端点做 1e-6 级一致性自校（在线可用时）；
    ② 在离线/端点 500 时仍能给出「平台表达」的数值，供反事实闸门使用。
    """
    gz = list(gz_table)
    # GM = GZ[0]/sin(heel[0]) —— 曲线首点 heel=0 时此处除零（在线即 500）
    heel0 = math.radians(gz[0]["heel"])
    gm = (gz[0]["GZ"] / math.sin(heel0)) if abs(math.sin(heel0)) > 1e-12 else float("nan")

    # 受风面积 / 力臂（平台启发式；算例可显式覆盖以做参数扫描）
    wind_area = ship.wind_area
    wind_lever = ship.wind_lever
    # 平台用动压 P=0.5·ρ·V²，非 IS Code 504 Pa；P_override 用于隔离「定义式误差」
    # 与「风压取值误差」两件事
    P = P_override if P_override is not None else 0.5 * RHO_AIR * wind_speed ** 2
    M = P * wind_area * wind_lever

    # 风致横倾角
    sin_th = M / (ship.displacement * 1000.0 * G_PLATFORM * gm)
    wind_heel = math.degrees(math.asin(min(sin_th, 1.0)))

    # 横摇自然周期（平台 analyze 用 0.8·B）
    Tr = 2.0 * math.pi * ship.B / math.sqrt(G_PLATFORM * gm) * 0.8
    omega, omega_n = 2.0 * math.pi / Te, 2.0 * math.pi / Tr
    r = omega / omega_n
    Dfac = math.sqrt((1 - r ** 2) ** 2 + (2 * roll_damping * r) ** 2)
    rao = 1.0 / Dfac
    wave_roll = (Hs / 2.0) * rao * 0.1             # 无量纲「简化系数」

    max_heel = wind_heel + wave_roll
    downflood = 50.0                                # 平台硬编码

    # 面积：GZ 全曲线 vs 风倾力矩×max_heel
    area_gz = 0.0
    for i in range(len(gz) - 1):
        d = math.radians(gz[i + 1]["heel"] - gz[i]["heel"])
        area_gz += (gz[i]["GZ"] + gz[i + 1]["GZ"]) / 2.0 * d
    area_wind = M * math.radians(max_heel) / (ship.displacement * 1000.0 * G_PLATFORM)
    ratio = area_gz / area_wind if area_wind > 0 else 10.0

    if ratio > 2.0:
        assess = "good"
    elif ratio > 1.5:
        assess = "satisfies"
    elif ratio > 1.0:
        assess = "marginal"
    else:
        assess = "insufficient"

    return {
        "gm_est": gm,
        "wind_pressure": P,
        "wind_moment_MNm": M / 1e6,
        "wind_heel_deg": wind_heel,
        "Tr_analyze": Tr,
        "rao": rao,
        "wave_roll_deg": wave_roll,
        "max_heel_deg": max_heel,
        "downflooding_deg": downflood,
        "area_gz": area_gz,
        "area_wind": area_wind,
        "ratio": ratio,
        "assessment": assess,
    }


def platform_roll_motion_replica(ship: Ship, GM: float, Hs: float, Te: float,
                                 roll_damping: float = 0.1,
                                 k_ratio: float = 0.4) -> Dict[str, float]:
    """逐行复刻 RollMotionAnalyzer.analyze（该端点在线恒 500，见审计报告）。"""
    k = k_ratio * ship.B
    Tr = 2.0 * math.pi * k / math.sqrt(G_PLATFORM * GM)
    omega_n = 2.0 * math.pi / Tr
    omega = 2.0 * math.pi / Te
    r = omega / omega_n
    Dfac = math.sqrt((1 - r ** 2) ** 2 + (2 * roll_damping * r) ** 2)
    rao = 1.0 / Dfac if Dfac > 0 else 1.0
    amp = Hs * rao * 0.1
    return {"Tr": Tr, "rao": rao, "amp_deg": amp, "k_ratio": k_ratio}


# --------------------------------------------------------------------------
# 4) 算例库
# --------------------------------------------------------------------------
def cases() -> List[Ship]:
    """4 型典型船（公开典型主尺度；GZ 由解析式构造，非取自平台）。"""
    return [
        Ship("S1_bulk_50k", L=190.0, B=32.0, T=12.0, D=17.5, Cb=0.82,
             displacement=50000.0, GM=1.60, theta_deck=25.0, theta_vanish=70.0),
        Ship("S2_tanker_300k", L=330.0, B=60.0, T=21.0, D=28.0, Cb=0.82,
             displacement=300000.0, GM=2.00, theta_deck=30.0, theta_vanish=65.0),
        Ship("S3_container_150k", L=366.0, B=51.0, T=14.0, D=24.0, Cb=0.65,
             displacement=150000.0, GM=1.20, theta_deck=18.0, theta_vanish=55.0),
        Ship("S4_coaster_3k", L=90.0, B=15.0, T=5.5, D=8.5, Cb=0.75,
             displacement=3000.0, GM=0.80, theta_deck=22.0, theta_vanish=60.0),
    ]


def borderline_sweep(gm_grid: Sequence[float], area_grid: Sequence[float],
                     base: Ship | None = None) -> List[Dict[str, float]]:
    """扫描 (GM, 受风面积) 找规范 b/a ≈ 1 的边界船（不手挑，程序化选取）。"""
    base = base or cases()[0]
    out = []
    for gm in gm_grid:
        for a_scale in area_grid:
            ship = Ship(base.name + f"_sweep", L=base.L, B=base.B, T=base.T, D=base.D,
                        Cb=base.Cb, displacement=base.displacement, GM=gm,
                        theta_deck=base.theta_deck, theta_vanish=base.theta_vanish,
                        wind_area=base.wind_area * a_scale, wind_lever=base.wind_lever)
            lw1 = wind_heeling_lever(P_WIND, ship.wind_area, ship.wind_lever,
                                     ship.displacement)
            w = weather_areas(ship, lw1)
            out.append({"GM": gm, "area_scale": a_scale, "lw1": lw1,
                        "ratio_ref": w["ratio"], "theta0": w["theta0"],
                        "a": w["a"], "b": w["b"]})
    return out