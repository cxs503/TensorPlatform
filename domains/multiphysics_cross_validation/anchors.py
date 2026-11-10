"""多物理面集成验证 —— 解析锚模型（领域内容，闭式公式，非手抄常数）。

本文件属**领域侧**：10 个物理面的闭式解析/半解析锚模型 + 输入不确定度来源。
数值一律由公式**计算**得出（零手抄），平台侧引擎 ``ops/taskplane/physics_integration_verify.py``
只做域无关的机械判定（量纲/守恒/契约/传播），不含任何域词条。

10 个面（P135-W2）：UNDEX 冲击、船-船碰撞、疲劳裂纹扩展耗能、MHD 洛伦兹力、
空化溃灭、LNG 蒸发率/热应力、电力推进效率链、电力系统负载与油耗、
ISO15016 航速修正、操纵性 CFD 回转圈。

每个锚模型都有：闭式公式（``formula``）、文献依据（``reference``）、输入及其
不确定度来源（``INPUTS`` 的 ``sigma_rel``/``rationale``）。不确定度 σ 的来源写死为
**文献/仪器**依据，不按结果反调。
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

# 物理常数（SI）
RHO_W = 1000.0      # 海水密度 kg/m3
C_W = 1500.0        # 水中声速 m/s
RHO_AIR = 1.225     # 空气密度 kg/m3
TNT_SPECIFIC = 4.52e6   # TNT 比能 J/kg
MU0 = 4.0e-7 * math.pi


# ===========================================================================
# 1. UNDEX：水下爆炸冲击波能量（Arons 峰压 + 指数衰减能量通量）
# ===========================================================================
def undex_shock_energy(mass_kg: float, standoff_m: float, rho: float, c: float) -> float:
    """冲击波总能量 E = 4πR²·∫p²/(ρc)dt，p(t)=P_max·e^(-t/θ)。"""
    w13 = mass_kg ** (1.0 / 3.0)
    p_max = 52.4e6 * (w13 / standoff_m) ** 1.13     # Arons (1948)，Pa
    theta = 1.0e-4 * w13                            # 衰减时间常数 s
    flux = p_max ** 2 * theta / (2.0 * rho * c)     # J/m²
    return 4.0 * math.pi * standoff_m ** 2 * flux


# ===========================================================================
# 2. 碰撞：完全耦合水平碰撞的初始动能与结构压溃吸能
# ===========================================================================
def collision_ke(m1: float, v1: float) -> float:
    """撞击船初始动能 0.5·m1·v1²。"""
    return 0.5 * m1 * v1 * v1


def collision_vc(m1: float, v1: float, m2: float) -> float:
    """完全耦合后的共速 m1·v1/(m1+m2)。"""
    return m1 * v1 / (m1 + m2)


def collision_crush_energy(r0: float, k: float, s: float) -> float:
    """压溃吸能 ∫0^s (R0 + k·s')ds' = R0·s + 0.5·k·s²。"""
    return r0 * s + 0.5 * k * s * s


# ===========================================================================
# 3. 疲劳：单次循环滞回耗能 + Basquin 寿命
# ===========================================================================
def fatigue_cycle_energy(dsigma: float, deps_p: float, volume: float, shape: float) -> float:
    """ΔW = shape·Δσ·Δε_p·V（Masing 型滞回环面积，线弹性-理想塑性极限）。"""
    return shape * dsigma * deps_p * volume


def fatigue_life_cycles(dsigma: float, log_a: float, m: float) -> float:
    """Basquin / S-N 双对数：log10 N = log10 a − m·log10 Δσ[MPa]。"""
    return 10.0 ** (log_a - m * math.log10(dsigma / 1.0e6))


def fatigue_life_dissipation(dsigma: float, deps_p: float, volume: float, shape: float,
                             log_a: float, m: float) -> float:
    return fatigue_cycle_energy(dsigma, deps_p, volume, shape) * fatigue_life_cycles(dsigma, log_a, m)


# ===========================================================================
# 4. MHD：洛伦兹力密度 + Hartmann 数
# ===========================================================================
def mhd_force_density(sigma_e: float, u: float, b: float) -> float:
    """f = σ·u·B²（E=0，u⊥B 的 MHD 通道）。"""
    return sigma_e * u * b * b


def mhd_hartmann(b: float, length: float, sigma_e: float, mu: float) -> float:
    return b * length * math.sqrt(sigma_e / mu)


# ===========================================================================
# 5. 空化：Rayleigh–Plesset 溃灭时间 + 溃灭功 + 水锤冲击压
# ===========================================================================
def cav_collapse_time(r0: float, dp: float, rho: float) -> float:
    """t_c = 0.915·R0·√(ρ/Δp)（Rayleigh 球对称溃灭）。"""
    return 0.915 * r0 * math.sqrt(rho / dp)


def cav_collapse_energy(r0: float, dp: float) -> float:
    """泡内压差做功 E = (4/3)πR0³·Δp。"""
    return 4.0 / 3.0 * math.pi * r0 ** 3 * dp


def cav_impact_pressure(dp: float, rho: float, c: float) -> float:
    """微射流速度 v=√(2Δp/ρ)，水锤压 p=ρ·c·v。"""
    return rho * c * math.sqrt(2.0 * dp / rho)


# ===========================================================================
# 6. LNG：薄膜舱热漏 → 蒸发率；全约束热应力
# ===========================================================================
def lng_heat_leak(u_ins: float, area: float, dT: float) -> float:
    return u_ins * area * dT


def lng_bor_per_day(u_ins: float, area: float, dT: float, rho_lng: float,
                    volume: float, hfg: float) -> float:
    """日蒸发率（无量纲/天）= Q/(h_fg·m_LNG)·86400。"""
    q = lng_heat_leak(u_ins, area, dT)
    mass = rho_lng * volume
    return (q / hfg) / mass * 86400.0


def lng_thermal_stress(e_mod: float, alpha: float, dT: float) -> float:
    """全约束热应力 σ = E·α·ΔT（上界估计）。"""
    return e_mod * alpha * dT


# ===========================================================================
# 7. 电力推进：效率链 → 轴功率 → 有效推力
# ===========================================================================
def ep_shaft_power(p_elec: float, eta_sb: float, eta_tr: float, eta_dr: float, eta_mo: float) -> float:
    return p_elec * eta_sb * eta_tr * eta_dr * eta_mo


def ep_thrust(p_elec: float, eta_sb: float, eta_tr: float, eta_dr: float, eta_mo: float,
              eta_o: float, v: float) -> float:
    """有效推力 T_E = η_O·P_D/V（P_D 为收到功率，此处取轴功率）。"""
    return eta_o * ep_shaft_power(p_elec, eta_sb, eta_tr, eta_dr, eta_mo) / v


# ===========================================================================
# 8. 电力系统：总电负荷、可用容量、日油耗
# ===========================================================================
def ps_total_demand(shaft_power: float, eta_mo: float, hotel: float) -> float:
    return shaft_power / eta_mo + hotel


def ps_available(n_gen: int, rating: float, n_minus_1: int = 1) -> float:
    return (n_gen - n_minus_1) * rating


def ps_fuel_per_day(demand: float, sfoc: float) -> float:
    """日油耗 kg/day = demand[kW]·SFOC[g/kWh]·24/1000（SI：W 与 kg/J）。"""
    return demand * sfoc * 86400.0


# ===========================================================================
# 9. ISO 15016：风阻与功率修正
# ===========================================================================
def iso_raa(rho_air: float, cd: float, area_t: float, v_rw: float) -> float:
    """R_AA = 0.5·ρ_air·C_D·A_T·V_RW²。"""
    return 0.5 * rho_air * cd * area_t * v_rw ** 2


def iso_delta_power_over_pd(rho_air: float, cd: float, area_t: float, v_rw: float,
                            v_s: float, pd: float) -> float:
    """ΔP/P_D = R_AA·V_s/P_D。"""
    return iso_raa(rho_air, cd, area_t, v_rw) * v_s / pd


def iso_speed_correction(rho_air: float, cd: float, area_t: float, v_rw: float,
                         v_s: float, pd: float, exponent: float = 3.0) -> float:
    """速度修正 ΔV/V = −(1/n)·ΔP/P_D（P∝Vⁿ 的幂律近似）。"""
    return -iso_delta_power_over_pd(rho_air, cd, area_t, v_rw, v_s, pd) / exponent


# ===========================================================================
# 10. 操纵性：Nomoto 一阶 → 稳态回转直径 / 船长
# ===========================================================================
def man_tactical_diameter_over_l(k_gain: float, delta: float, v: float, length: float) -> float:
    """r_ss = K·δ；R = V/r_ss；战术直径 D_T = 2R；返回 D_T/L。"""
    r_ss = k_gain * delta
    return 2.0 * v / r_ss / length


# ===========================================================================
# 输入不确定度（σ_rel 与来源）—— 全部来自仪器/文献，非按结果反调
# ===========================================================================
INPUTS: Dict[str, Dict[str, object]] = {
    "undex.mass_kg":    {"sigma_rel": 0.05, "rationale": "装药质量称量与炸药类型差异（TNT 当量折算）"},
    "undex.standoff_m": {"sigma_rel": 0.02, "rationale": "爆源-目标距离声学定位"},
    "undex.rho":        {"sigma_rel": 0.005, "rationale": "海水密度随盐度/温度变化"},
    "undex.c":          {"sigma_rel": 0.005, "rationale": "声速随温度/深度变化"},
    "collision.m1":     {"sigma_rel": 0.02, "rationale": "排水量由水尺检验，2%"},
    "collision.v1":     {"sigma_rel": 0.03, "rationale": "对水速度由计程仪/GPS 融合"},
    "fatigue.dsigma":   {"sigma_rel": 0.05, "rationale": "焊趾应力集中系数 SCF 的离散"},
    "fatigue.deps_p":   {"sigma_rel": 0.20, "rationale": "局部塑性应变范围最缺数据（缺口应变测量）"},
    "fatigue.volume":   {"sigma_rel": 0.10, "rationale": "塑性区体积由热点外推估计"},
    "lng.u_ins":        {"sigma_rel": 0.08, "rationale": "绝缘层时效/吸湿使 U 值劣化"},
    "lng.area":         {"sigma_rel": 0.03, "rationale": "舱容几何建模误差"},
    "lng.dT":           {"sigma_rel": 0.05, "rationale": "海水/环境温度季节波动"},
    "lng.hfg":          {"sigma_rel": 0.03, "rationale": "LNG 组分导致汽化潜热变化"},
    "mhd.sigma_e":      {"sigma_rel": 0.05, "rationale": "海水电导率随盐度/温度变化"},
    "mhd.u":            {"sigma_rel": 0.03, "rationale": "通道内速度分布未解析"},
    "mhd.b":            {"sigma_rel": 0.02, "rationale": "超导磁体场强标定"},
    "cav.r0":           {"sigma_rel": 0.05, "rationale": "核化气泡初始半径由光学测量"},
    "cav.dp":           {"sigma_rel": 0.10, "rationale": "环境压与饱和蒸汽压差"},
    "ep.p_elec":        {"sigma_rel": 0.02, "rationale": "变频器输入功率计量"},
    "ep.eta_dr":        {"sigma_rel": 0.01, "rationale": "变频器效率曲线"},
    "ep.eta_mo":        {"sigma_rel": 0.01, "rationale": "永磁电机效率曲线"},
    "ep.eta_sb":        {"sigma_rel": 0.01, "rationale": "主配电板损耗"},
    "ep.eta_tr":        {"sigma_rel": 0.01, "rationale": "变压器效率"},
    "ps.sfoc":          {"sigma_rel": 0.05, "rationale": "柴油发电机 SFOC 台架-实船偏差"},
    "ps.demand":        {"sigma_rel": 0.02, "rationale": "负荷模型与实测偏差"},
    "iso.v_rw":         {"sigma_rel": 0.10, "rationale": "相对风速由风速仪+航速推算"},
    "iso.cd":           {"sigma_rel": 0.05, "rationale": "上层建筑风阻系数经验值离散"},
    "iso.area_t":       {"sigma_rel": 0.03, "rationale": "受风面积由总布置几何统计"},
    "iso.rho_air":      {"sigma_rel": 0.01, "rationale": "空气密度随温压变化"},
    "man.k_gain":       {"sigma_rel": 0.10, "rationale": "Nomoto 增益 K 由 CFD/自航试验辨识"},
    "man.v":            {"sigma_rel": 0.03, "rationale": "回转中速降由试航记录"},
    "man.delta":        {"sigma_rel": 0.02, "rationale": "舵角机械零位与标定"},
}


def central_derivative(fn: Callable[[float], float], x: float, h_rel: float = 1e-6) -> float:
    """中心差分 ∂fn/∂x（相对步长 1e-6，截断误差 O(h²)≈1e-12 相对）。"""
    step = abs(x) * h_rel
    if step == 0.0:
        step = h_rel
    return (fn(x + step) - fn(x - step)) / (2.0 * step)


def derivative_of(fn: Callable[..., float], base: Dict[str, float], name: str) -> float:
    """对多参函数按单参数求偏导（其余参数固定）。"""
    def _f(v: float) -> float:
        args = dict(base)
        args[name] = v
        return fn(**args)

    return central_derivative(_f, float(base[name]))