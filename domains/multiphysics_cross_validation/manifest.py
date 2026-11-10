"""把 10 个物理面的解析锚装配成**平台引擎可裁**的 manifest（领域内容 → 注入文档）。

设计原则（可审计）：

* 所有数值由 :mod:`domains.multiphysics_cross_validation.anchors` 的闭式公式**计算**得到，
  本文件不出现手抄结果常数；
* 量级带（``band_log10``）、守恒容差、耦合端口、不确定度、**预声明期望排序**
  全部在"跑之前"写死在本文件 —— 引擎只做机械比对，不参与调参；
* 期望排序的推导写在注释里（弹性结构 → 平局规则），属先验物理判断，非事后拟合。

跑法：``python3 domains/multiphysics_cross_validation/run_validation.py``
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from domains.multiphysics_cross_validation import anchors as A

SUITE_ID = "p135w2-multiphysics-integration"

# ---------------------------------------------------------------------------
# 单位表：7 个 SI 基本量指数向量 (M, L, T, I, TH, N, J) + 到 SI 的标度
# ---------------------------------------------------------------------------
UNITS: Dict[str, Dict[str, Any]] = {
    "1": {"dim": [0, 0, 0, 0, 0, 0, 0], "scale": 1.0},
    "kg": {"dim": [1, 0, 0, 0, 0, 0, 0], "scale": 1.0},
    "t": {"dim": [1, 0, 0, 0, 0, 0, 0], "scale": 1e3},
    "m": {"dim": [0, 1, 0, 0, 0, 0, 0], "scale": 1.0},
    "m2": {"dim": [0, 2, 0, 0, 0, 0, 0], "scale": 1.0},
    "m3": {"dim": [0, 3, 0, 0, 0, 0, 0], "scale": 1.0},
    "1/m2": {"dim": [0, -2, 0, 0, 0, 0, 0], "scale": 1.0},
    "s": {"dim": [0, 0, 1, 0, 0, 0, 0], "scale": 1.0},
    "K": {"dim": [0, 0, 0, 0, 1, 0, 0], "scale": 1.0},
    "J": {"dim": [1, 2, -2, 0, 0, 0, 0], "scale": 1.0},
    "MJ": {"dim": [1, 2, -2, 0, 0, 0, 0], "scale": 1e6},
    "J/m2": {"dim": [1, 0, -2, 0, 0, 0, 0], "scale": 1.0},
    "J/kg": {"dim": [0, 2, -2, 0, 0, 0, 0], "scale": 1.0},
    "J/m3": {"dim": [1, -1, -2, 0, 0, 0, 0], "scale": 1.0},
    "N": {"dim": [1, 1, -2, 0, 0, 0, 0], "scale": 1.0},
    "MN": {"dim": [1, 1, -2, 0, 0, 0, 0], "scale": 1e6},
    "MN/m": {"dim": [1, 0, -2, 0, 0, 0, 0], "scale": 1e6},
    "N/m3": {"dim": [1, -2, -2, 0, 0, 0, 0], "scale": 1.0},
    "Pa": {"dim": [1, -1, -2, 0, 0, 0, 0], "scale": 1.0},
    "MPa": {"dim": [1, -1, -2, 0, 0, 0, 0], "scale": 1e6},
    "W": {"dim": [1, 2, -3, 0, 0, 0, 0], "scale": 1.0},
    "kW": {"dim": [1, 2, -3, 0, 0, 0, 0], "scale": 1e3},
    "MW": {"dim": [1, 2, -3, 0, 0, 0, 0], "scale": 1e6},
    "W/m2": {"dim": [1, 0, -3, 0, 0, 0, 0], "scale": 1.0},
    "W/m3": {"dim": [1, -1, -3, 0, 0, 0, 0], "scale": 1.0},
    "W/m2K": {"dim": [1, 0, -3, 0, -1, 0, 0], "scale": 1.0},
    "kg/m3": {"dim": [1, -3, 0, 0, 0, 0, 0], "scale": 1.0},
    "m/s": {"dim": [0, 1, -1, 0, 0, 0, 0], "scale": 1.0},
    "1/s": {"dim": [0, 0, -1, 0, 0, 0, 0], "scale": 1.0},
    "1/day": {"dim": [0, 0, -1, 0, 0, 0, 0], "scale": 1.0 / 86400.0},
    "rad": {"dim": [0, 0, 0, 0, 0, 0, 0], "scale": 1.0},
    "deg": {"dim": [0, 0, 0, 0, 0, 0, 0], "scale": math.pi / 180.0},
    "deg/s": {"dim": [0, 0, -1, 0, 0, 0, 0], "scale": math.pi / 180.0},
    "kg/h": {"dim": [1, 0, -1, 0, 0, 0, 0], "scale": 1.0 / 3600.0},
    "kg/day": {"dim": [1, 0, -1, 0, 0, 0, 0], "scale": 1.0 / 86400.0},
    "g/kWh": {"dim": [0, -2, 2, 0, 0, 0, 0], "scale": 1e-3 / 3.6e6},
    "S/m": {"dim": [-1, -3, 3, 2, 0, 0, 0], "scale": 1.0},
    "T": {"dim": [1, 0, -2, -1, 0, 0, 0], "scale": 1.0},
}

# ---------------------------------------------------------------------------
# 设计工况（SI）—— 代表性量级的常规工况；改工况即改算例
# ---------------------------------------------------------------------------
CASE: Dict[str, float] = {
    "undex_mass": 100.0, "undex_standoff": 15.0,
    "undex_bubble_frac": 0.42, "undex_thermal_frac": 0.35,
    "coll_m1": 1.0e8, "coll_v1": 4.5, "coll_m2": 2.0e8,
    "coll_r0": 150.0e6, "coll_k": 40.0e6, "coll_s": 2.9, "coll_fric_frac": 0.05,
    "fat_dsigma": 120.0e6, "fat_deps_p": 1.67e-3, "fat_volume": 1.0e-2, "fat_shape": 1.0,
    "fat_log_a": 12.164, "fat_m": 3.0, "fat_heat_frac": 0.93, "fat_stored_frac": 0.05,
    "mhd_sigma": 4.0, "mhd_u": 10.0, "mhd_b": 4.0, "mhd_len": 1.0,
    "cav_r0": 1.0e-3, "cav_p_amb": 1.0e5, "cav_p_vap": 2.3e3,
    "lng_u": 0.13, "lng_area": 12000.0, "lng_dT": 203.0, "lng_rho": 450.0,
    "lng_volume": 138000.0, "lng_hfg": 4.8e5, "lng_dT_struct": 160.0,
    "steel_e": 210.0e9, "steel_alpha": 1.2e-5,
    "ep_p_elec": 20.0e6, "ep_eta_sb": 0.99, "ep_eta_tr": 0.985,
    "ep_eta_dr": 0.98, "ep_eta_mo": 0.97, "ep_eta_o": 0.65, "v_ship": 8.23,
    "ps_n_gen": 4, "ps_rating": 7.5e6, "ps_hotel": 1.5e6, "ps_sfoc": 175.0,
    "iso_rho_air": 1.225, "iso_cd": 0.8, "iso_area_t": 800.0, "iso_v_rw": 12.0,
    "iso_exponent": 3.0,
    "man_k": 0.045, "man_delta_deg": 35.0, "man_length": 200.0,
    "design_resistance": 1.40e6,
}


def derived() -> Dict[str, float]:
    """核心量：全部由闭式公式计算（SI）。"""
    c = CASE
    e_shock = A.undex_shock_energy(c["undex_mass"], c["undex_standoff"], A.RHO_W, A.C_W)
    e_chem = c["undex_mass"] * A.TNT_SPECIFIC
    ke = A.collision_ke(c["coll_m1"], c["coll_v1"])
    vc = A.collision_vc(c["coll_m1"], c["coll_v1"], c["coll_m2"])
    ke_res = 0.5 * (c["coll_m1"] + c["coll_m2"]) * vc ** 2
    e_crush = A.collision_crush_energy(c["coll_r0"], c["coll_k"], c["coll_s"])
    e_cyc = A.fatigue_cycle_energy(c["fat_dsigma"], c["fat_deps_p"], c["fat_volume"], c["fat_shape"])
    n_cyc = A.fatigue_life_cycles(c["fat_dsigma"], c["fat_log_a"], c["fat_m"])
    dp = c["cav_p_amb"] - c["cav_p_vap"]
    p_shaft = A.ep_shaft_power(c["ep_p_elec"], c["ep_eta_sb"], c["ep_eta_tr"],
                               c["ep_eta_dr"], c["ep_eta_mo"])
    demand = A.ps_total_demand(p_shaft, c["ep_eta_mo"], c["ps_hotel"])
    sfoc_si = c["ps_sfoc"] * 1e-3 / 3.6e6
    return {
        "e_shock": e_shock,
        "e_chem": e_chem,
        "ke_init": ke,
        "v_c": vc,
        "ke_res": ke_res,
        "e_crush": e_crush,
        "e_fric": c["coll_fric_frac"] * (ke - ke_res),
        "e_cycle": e_cyc,
        "n_cycle": n_cyc,
        "e_life": e_cyc * n_cyc,
        "f_mhd": A.mhd_force_density(c["mhd_sigma"], c["mhd_u"], c["mhd_b"]),
        "ha": A.mhd_hartmann(c["mhd_b"], c["mhd_len"], c["mhd_sigma"], A.MU0),
        "dp_cav": dp,
        "t_cav": A.cav_collapse_time(c["cav_r0"], dp, A.RHO_W),
        "e_cav": A.cav_collapse_energy(c["cav_r0"], dp),
        "p_imp": A.cav_impact_pressure(dp, A.RHO_W, A.C_W),
        "q_lng": A.lng_heat_leak(c["lng_u"], c["lng_area"], c["lng_dT"]),
        "bor": A.lng_bor_per_day(c["lng_u"], c["lng_area"], c["lng_dT"], c["lng_rho"],
                                 c["lng_volume"], c["lng_hfg"]),
        "sig_th": A.lng_thermal_stress(c["steel_e"], c["steel_alpha"], c["lng_dT_struct"]),
        "p_shaft": p_shaft,
        "thrust": A.ep_thrust(c["ep_p_elec"], c["ep_eta_sb"], c["ep_eta_tr"], c["ep_eta_dr"],
                              c["ep_eta_mo"], c["ep_eta_o"], c["v_ship"]),
        "demand": demand,
        "avail": A.ps_available(int(c["ps_n_gen"]), c["ps_rating"]),
        "fuel_day": A.ps_fuel_per_day(demand, sfoc_si),
        "raa": A.iso_raa(c["iso_rho_air"], c["iso_cd"], c["iso_area_t"], c["iso_v_rw"]),
        "dpp": A.iso_delta_power_over_pd(c["iso_rho_air"], c["iso_cd"], c["iso_area_t"],
                                         c["iso_v_rw"], c["v_ship"], p_shaft),
        "dv": A.iso_speed_correction(c["iso_rho_air"], c["iso_cd"], c["iso_area_t"],
                                     c["iso_v_rw"], c["v_ship"], p_shaft, c["iso_exponent"]),
        "dtl": A.man_tactical_diameter_over_l(c["man_k"], math.radians(c["man_delta_deg"]),
                                              c["v_ship"], c["man_length"]),
    }


def build_anchors() -> List[Dict[str, Any]]:
    """26 条解析锚（值全部由闭式公式算出；band 为量级带）。"""
    d, c = derived(), CASE

    def _a(entry_id, node, quantity, value, unit, band, formula, reference=None, inputs=None):
        return {"entry_id": entry_id, "node_id": node, "quantity": quantity,
                "value": float(value), "unit": unit, "band_log10": list(band),
                "anchor_kind": "analytic", "formula": formula,
                "reference": reference, "inputs": inputs or {}}

    return [
        _a("undex.shock_energy", "undex", "冲击波总能量", d["e_shock"], "J", (7.5, 8.5),
           "E=4πR²·P_max²θ/(2ρc); P_max=52.4MPa·(W^(1/3)/R)^1.13; θ=1e-4·W^(1/3)",
           inputs={"mass_kg": c["undex_mass"], "standoff_m": c["undex_standoff"],
                   "rho": A.RHO_W, "c": A.C_W}),
        _a("undex.chemical_energy", "undex", "装药化学能", d["e_chem"], "J", (8.5, 8.8),
           "E_chem=W·4.52MJ/kg", inputs={"mass_kg": c["undex_mass"]}),
        _a("undex.shock_coupling", "undex", "冲击波能量耦合比", d["e_shock"] / d["e_chem"], "1",
           (-0.90, -0.50), "η=E_shock/E_chem",
           reference={"value": 0.20, "unit": "1", "tol_rel": 0.35,
                      "source": "深水 TNT 冲击波能量耦合比文献区间 12%–30%"},
           inputs={"E_shock": d["e_shock"], "E_chem": d["e_chem"]}),
        _a("collision.ke_initial", "collision", "撞击船初始动能", d["ke_init"], "J", (8.8, 9.3),
           "KE=0.5·m1·v1²", inputs={"m1": c["coll_m1"], "v1": c["coll_v1"]}),
        _a("collision.ke_residual", "collision", "耦合后剩余动能", d["ke_res"], "J", (8.3, 8.8),
           "KE_res=0.5·(m1+m2)·v_c²; v_c=m1v1/(m1+m2)",
           inputs={"m1": c["coll_m1"], "m2": c["coll_m2"], "v_c": d["v_c"]}),
        _a("collision.crush_energy", "collision", "结构压溃吸能", d["e_crush"], "J", (8.5, 9.1),
           "E=∫(R0+k·s)ds=R0·s+0.5·k·s²",
           inputs={"R0": c["coll_r0"], "k": c["coll_k"], "s": c["coll_s"]}),
        _a("fatigue.cycle_energy", "fatigue", "单次循环滞回耗能", d["e_cycle"], "J", (2.8, 3.5),
           "ΔW=shape·Δσ·Δε_p·V",
           inputs={"dsigma": c["fat_dsigma"], "deps_p": c["fat_deps_p"], "volume": c["fat_volume"]}),
        _a("fatigue.life_cycles", "fatigue", "Basquin 寿命循环数", d["n_cycle"], "1", (5.5, 6.5),
           "log10 N=log10 a−m·log10 Δσ[MPa] (DNV D: log a=12.164, m=3)",
           inputs={"dsigma_MPa": c["fat_dsigma"] / 1e6}),
        _a("fatigue.life_dissipation", "fatigue", "全寿命累积耗能", d["e_life"], "J", (8.8, 9.6),
           "E_life=ΔW·N", inputs={"dW": d["e_cycle"], "N": d["n_cycle"]}),
        _a("mhd.force_density", "mhd", "洛伦兹力体积密度", d["f_mhd"], "N/m3", (2.3, 3.3),
           "f=σ·u·B²", inputs={"sigma": c["mhd_sigma"], "u": c["mhd_u"], "B": c["mhd_b"]}),
        _a("mhd.hartmann", "mhd", "Hartmann 数", d["ha"], "1", (3.4, 4.3),
           "Ha=B·L·√(σ/μ0)", inputs={"B": c["mhd_b"], "L": c["mhd_len"]}),
        _a("cav.collapse_time", "cavitation", "溃灭时间", d["t_cav"], "s", (-4.4, -3.6),
           "t_c=0.915·R0·√(ρ/Δp)", inputs={"R0": c["cav_r0"], "dp": d["dp_cav"]}),
        _a("cav.collapse_energy", "cavitation", "单泡溃灭功", d["e_cav"], "J", (-3.9, -2.9),
           "E=(4/3)πR0³·Δp", inputs={"R0": c["cav_r0"], "dp": d["dp_cav"]}),
        _a("cav.impact_pressure", "cavitation", "微射流水锤压", d["p_imp"], "Pa", (6.9, 8.0),
           "p=ρc·√(2Δp/ρ)", inputs={"dp": d["dp_cav"]}),
        _a("lng.heat_leak", "lng", "舱壁热漏", d["q_lng"], "W", (5.2, 5.8),
           "Q=U·A·ΔT", inputs={"U": c["lng_u"], "A": c["lng_area"], "dT": c["lng_dT"]}),
        _a("lng.bor_per_day", "lng", "日蒸发率(无量纲/天)", d["bor"], "1", (-3.3, -2.7),
           "BOR=Q/(h_fg·ρ·V)·86400",
           reference={"value": 1.0e-3, "unit": "1", "tol_rel": 0.25,
                      "source": "Mark III 薄膜舱典型 BOR 0.10–0.15 %/day"},
           inputs={"U": c["lng_u"], "A": c["lng_area"], "dT": c["lng_dT"],
                   "hfg": c["lng_hfg"], "rho": c["lng_rho"], "V": c["lng_volume"]}),
        _a("lng.thermal_stress", "lng", "全约束热应力(上界)", d["sig_th"], "Pa", (8.3, 8.9),
           "σ=E·α·ΔT_struct", inputs={"E": c["steel_e"], "alpha": c["steel_alpha"],
                                      "dT_struct": c["lng_dT_struct"]}),
        _a("ep.shaft_power", "electric_propulsion", "轴功率", d["p_shaft"], "W", (7.0, 7.5),
           "P_D=P_elec·η_sb·η_tr·η_dr·η_mo",
           inputs={"P_elec": c["ep_p_elec"], "eta": 0.9269}),
        _a("ep.thrust", "electric_propulsion", "有效推力", d["thrust"], "N", (5.8, 6.5),
           "T_E=η_O·P_D/V", inputs={"P_D": d["p_shaft"], "eta_o": c["ep_eta_o"],
                                    "V": c["v_ship"]}),
        _a("ps.total_demand", "power_system", "总电负荷", d["demand"], "W", (7.0, 7.6),
           "P_dem=P_D/η_mo+P_hotel",
           inputs={"P_D": d["p_shaft"], "eta_mo": c["ep_eta_mo"], "hotel": c["ps_hotel"]}),
        _a("ps.available_n1", "power_system", "N+1 可用容量", d["avail"], "W", (7.1, 7.6),
           "P_avail=(n_gen−1)·rating",
           inputs={"n_gen": c["ps_n_gen"], "rating": c["ps_rating"]}),
        _a("ps.fuel_per_day", "power_system", "日油耗", d["fuel_day"], "kg", (4.6, 5.2),
           "m_fuel=P_dem·SFOC·86400",
           inputs={"demand": d["demand"], "SFOC_gkWh": c["ps_sfoc"]}),
        _a("iso.raa", "iso15016", "风阻", d["raa"], "N", (4.4, 5.1),
           "R_AA=0.5·ρ_air·C_D·A_T·V_RW²",
           inputs={"rho_air": c["iso_rho_air"], "C_D": c["iso_cd"],
                   "A_T": c["iso_area_t"], "V_RW": c["iso_v_rw"]}),
        _a("iso.delta_power_over_pd", "iso15016", "风修正功率比", d["dpp"], "1", (-2.0, -1.2),
           "ΔP/P_D=R_AA·V_s/P_D",
           reference={"value": 0.03, "unit": "1", "tol_rel": 0.50,
                      "source": "ISO 15016 风修正功率量级 2%–4%"},
           inputs={"R_AA": d["raa"], "V_s": c["v_ship"], "P_D": d["p_shaft"]}),
        _a("iso.speed_correction", "iso15016", "航速修正比", d["dv"], "1", (-2.5, -1.7),
           "ΔV/V=−(1/n)·ΔP/P_D",
           inputs={"dPP": d["dpp"], "n": c["iso_exponent"]}),
        _a("man.tactical_diameter_over_l", "manoeuvring_cfd", "战术直径/船长", d["dtl"], "1",
           (0.2, 0.7), "D_T/L=(2V/(K·δ))/L",
           reference={"value": 5.0, "unit": "1", "tol_rel": 1.00,
                      "source": "IMO A.749 战术直径 ≤5L（判据上限）"},
           inputs={"K": c["man_k"], "delta_rad": math.radians(c["man_delta_deg"]),
                   "V": c["v_ship"], "L": c["man_length"]}),
    ]


def build_balances() -> List[Dict[str, Any]]:
    """三条能量守恒式（跨物理面能量框架自洽）。"""
    d, c = derived(), CASE
    return [
        {"balance_id": "bal_undex_partition", "node_id": "undex",
         "total": {"value": d["e_chem"], "unit": "J"},
         "parts": [
             {"label": "shock_wave", "value": d["e_shock"], "unit": "J", "sign": "+"},
             {"label": "bubble_pulse", "value": c["undex_bubble_frac"] * d["e_chem"],
              "unit": "J", "sign": "+"},
             {"label": "thermal_other", "value": c["undex_thermal_frac"] * d["e_chem"],
              "unit": "J", "sign": "+"},
         ], "tol_rel": 0.10,
         "note": "化学能分配：冲击波（解析）、气泡脉动 42%、热/其他 35%（文献份额）"},
        {"balance_id": "bal_collision_partition", "node_id": "collision",
         "total": {"value": d["ke_init"], "unit": "J"},
         "parts": [
             {"label": "residual_ke", "value": d["ke_res"], "unit": "J", "sign": "+"},
             {"label": "crush_absorption", "value": d["e_crush"], "unit": "J", "sign": "+"},
             {"label": "friction_heat", "value": d["e_fric"], "unit": "J", "sign": "+"},
         ], "tol_rel": 0.08,
         "note": "动量守恒定剩余动能；压溃曲线独立定吸能；差值归摩擦/热"},
        {"balance_id": "bal_fatigue_partition", "node_id": "fatigue",
         "total": {"value": d["e_life"], "unit": "J"},
         "parts": [
             {"label": "heat", "value": c["fat_heat_frac"] * d["e_life"], "unit": "J", "sign": "+"},
             {"label": "stored", "value": c["fat_stored_frac"] * d["e_life"], "unit": "J",
              "sign": "+"},
         ], "tol_rel": 0.05,
         "note": "疲劳耗能分配：热 93% + 储能 5%，缺额记未分配（不闭合即报警）"},
    ]


def build_ordering() -> List[Dict[str, Any]]:
    """跨面量级/序关系（能量框架自洽的序判据）。"""
    return [
        {"edge_id": "ord_undex_lt_collision", "a": "undex.shock_energy",
         "b": "collision.ke_initial", "relation": "a<b", "min_gap_log10": 0.5},
        {"edge_id": "ord_fatigue_cycle_lt_undex", "a": "fatigue.cycle_energy",
         "b": "undex.shock_energy", "relation": "a<b", "min_gap_log10": 3.0},
        {"edge_id": "ord_undex_lt_fatigue_life", "a": "undex.shock_energy",
         "b": "fatigue.life_dissipation", "relation": "a<b", "min_gap_log10": 1.0},
        {"edge_id": "ord_crush_lt_ke", "a": "collision.crush_energy",
         "b": "collision.ke_initial", "relation": "a<b", "min_gap_log10": 0.0},
        {"edge_id": "ord_chem_vs_ke_same_order", "a": "undex.chemical_energy",
         "b": "collision.ke_initial", "relation": "same_magnitude", "min_gap_log10": 0.5},
    ]


EXTERNAL_NODES = ("TensorLBM.solver", "TensorFEM.shell", "TensorFEM.plate",
                  "TensorFEM.hotspot", "TensorFEM.tank", "TensorCFD.solver",
                  "ShipBalance.resistance")


def build_interfaces() -> List[Dict[str, Any]]:
    """12 条跨面耦合边（含 1 条量纲桥接 + 1 条帧等价类桥接）。"""
    def port(node, name, unit, frame, shape=(), dtype="float64", sign=None):
        p = {"node_id": node, "port": name, "unit": unit, "frame": frame,
             "shape": list(shape), "dtype": dtype}
        if sign:
            p["sign_convention"] = sign
        return p

    return [
        {"edge_id": "e01_mhd_to_lbm", "producer": port("mhd", "body_force_density", "N/m3",
                                                      "cartesian_lab", (3,), sign="fluid_positive"),
         "consumer": port("TensorLBM.solver", "body_force_source", "N/m3", "cartesian_lab", (3,),
                          sign="fluid_positive")},
        {"edge_id": "e02_undex_to_shell",
         "producer": port("undex", "shock_energy_flux", "J/m2", "shock_front_normal",
                          sign="outward_positive"),
         "consumer": port("TensorFEM.shell", "surface_energy_flux", "J/m2", "shock_front_normal",
                          sign="outward_positive")},
        {"edge_id": "e03_collision_to_plate",
         "producer": port("collision", "crush_force", "MN", "hull_global"),
         "consumer": port("TensorFEM.plate", "contact_force", "MN", "hull_global")},
        {"edge_id": "e04_fatigue_to_hotspot",
         "producer": port("fatigue", "dissipation_per_cycle", "J", "detail_local"),
         "consumer": port("TensorFEM.hotspot", "dissipated_energy", "J", "detail_local")},
        {"edge_id": "e05_cav_to_cfd",
         "producer": port("cavitation", "impact_pressure", "MPa", "wall_normal"),
         "consumer": port("TensorCFD.solver", "wall_impingement_pressure", "MPa", "wall_normal")},
        {"edge_id": "e06_lng_heat_to_tank",
         "producer": port("lng", "heat_leak", "W", "tank_wall_local"),
         "consumer": port("TensorFEM.tank", "wall_heat_flux", "W/m2", "tank_wall_local"),
         "bridge": {"gain": 1.0 / CASE["lng_area"], "gain_unit": "1/m2",
                    "rationale": "总热漏按换热面积归一为壁面热流（section 型降维）"}},
        {"edge_id": "e07_lng_temp_to_tank",
         "producer": port("lng", "tank_wall_temperature", "K", "tank_wall_local", (2,)),
         "consumer": port("TensorFEM.tank", "thermal_boundary_temperature", "K",
                          "tank_wall_local", (2,))},
        {"edge_id": "e08_prop_to_power",
         "producer": port("electric_propulsion", "shaft_power", "MW", "shaft_frame"),
         "consumer": port("power_system", "propulsion_load", "MW", "electrical_bus"),
         "bridge": {"gain": 1.0 / CASE["ep_eta_mo"], "gain_unit": "1",
                    "rationale": "轴功率→电功率反算（电机效率）；帧等价类见 frame_classes"}},
        {"edge_id": "e09_power_to_iso",
         "producer": port("power_system", "delivered_shaft_power", "MW", "shaft_frame"),
         "consumer": port("iso15016", "shaft_power_input", "MW", "shaft_frame")},
        {"edge_id": "e10_iso_to_manoeuvring",
         "producer": port("iso15016", "corrected_speed", "m/s", "water_referenced"),
         "consumer": port("manoeuvring_cfd", "inflow_speed", "m/s", "water_referenced")},
        {"edge_id": "e11_manoeuvring_to_cfd",
         "producer": port("manoeuvring_cfd", "rudder_angle", "deg", "hull_global",
                          sign="starboard_positive"),
         "consumer": port("TensorCFD.solver", "rudder_deflection", "deg", "hull_global",
                          sign="starboard_positive")},
        {"edge_id": "e12_thrust_to_balance",
         "producer": port("electric_propulsion", "thrust", "MN", "hull_global"),
         "consumer": port("ShipBalance.resistance", "thrust_available", "N", "hull_global")},
    ]


FRAME_CLASSES = [["shaft_frame", "electrical_bus"]]


def _prop_target(target_id, node, output, unit, fn, params: Dict[str, float],
                 inputs: List[str]) -> Dict[str, Any]:
    """构造一个传播目标：偏导用中心差分（相对步长 1e-6）自动求。"""
    y = float(fn(**params))
    terms = []
    for name in inputs:
        d = A.derivative_of(fn, params, name)
        sigma_rel = A.INPUTS[name]["sigma_rel"]
        x = float(params[name])
        terms.append({"input_id": name, "symbol": name.split(".")[-1], "value": x,
                      "sigma": abs(x) * sigma_rel, "derivative": d, "unit": None,
                      "rationale": A.INPUTS[name]["rationale"]})
    return {"target_id": target_id, "node_id": node, "output": output, "unit": unit,
            "value": y, "terms": terms}


def build_propagation() -> Dict[str, Any]:
    c = CASE
    dp = c["cav_p_amb"] - c["cav_p_vap"]
    targets = [
        _prop_target("undex.shock_energy", "undex", "冲击波总能量", "J",
                     lambda **k: A.undex_shock_energy(k["undex.mass_kg"], k["undex.standoff_m"],
                                                     k["undex.rho"], k["undex.c"]),
                     {"undex.mass_kg": c["undex_mass"], "undex.standoff_m": c["undex_standoff"],
                      "undex.rho": A.RHO_W, "undex.c": A.C_W},
                     ["undex.mass_kg", "undex.standoff_m", "undex.rho", "undex.c"]),
        _prop_target("collision.ke_initial", "collision", "撞击船初始动能", "J",
                     lambda **k: A.collision_ke(k["collision.m1"], k["collision.v1"]),
                     {"collision.m1": c["coll_m1"], "collision.v1": c["coll_v1"]},
                     ["collision.m1", "collision.v1"]),
        _prop_target("fatigue.cycle_energy", "fatigue", "单次循环滞回耗能", "J",
                     lambda **k: A.fatigue_cycle_energy(k["fatigue.dsigma"], k["fatigue.deps_p"],
                                                        k["fatigue.volume"], c["fat_shape"]),
                     {"fatigue.dsigma": c["fat_dsigma"], "fatigue.deps_p": c["fat_deps_p"],
                      "fatigue.volume": c["fat_volume"]},
                     ["fatigue.dsigma", "fatigue.deps_p", "fatigue.volume"]),
        _prop_target("lng.bor_per_day", "lng", "日蒸发率", "1",
                     lambda **k: A.lng_bor_per_day(k["lng.u_ins"], k["lng.area"], k["lng.dT"],
                                                   c["lng_rho"], c["lng_volume"], k["lng.hfg"]),
                     {"lng.u_ins": c["lng_u"], "lng.area": c["lng_area"], "lng.dT": c["lng_dT"],
                      "lng.hfg": c["lng_hfg"]},
                     ["lng.u_ins", "lng.area", "lng.dT", "lng.hfg"]),
        _prop_target("mhd.force_density", "mhd", "洛伦兹力体积密度", "N/m3",
                     lambda **k: A.mhd_force_density(k["mhd.sigma_e"], k["mhd.u"], k["mhd.b"]),
                     {"mhd.sigma_e": c["mhd_sigma"], "mhd.u": c["mhd_u"], "mhd.b": c["mhd_b"]},
                     ["mhd.sigma_e", "mhd.u", "mhd.b"]),
        _prop_target("cav.collapse_time", "cavitation", "溃灭时间", "s",
                     lambda **k: A.cav_collapse_time(k["cav.r0"], k["cav.dp"], A.RHO_W),
                     {"cav.r0": c["cav_r0"], "cav.dp": dp}, ["cav.r0", "cav.dp"]),
        _prop_target("iso.raa", "iso15016", "风阻", "N",
                     lambda **k: A.iso_raa(k["iso.rho_air"], k["iso.cd"], k["iso.area_t"],
                                           k["iso.v_rw"]),
                     {"iso.rho_air": c["iso_rho_air"], "iso.cd": c["iso_cd"],
                      "iso.area_t": c["iso_area_t"], "iso.v_rw": c["iso_v_rw"]},
                     ["iso.rho_air", "iso.cd", "iso.area_t", "iso.v_rw"]),
        _prop_target("man.tactical_diameter_over_l", "manoeuvring_cfd", "战术直径/船长", "1",
                     lambda **k: A.man_tactical_diameter_over_l(
                         k["man.k_gain"], k["man.delta"], k["man.v"], c["man_length"]),
                     {"man.k_gain": c["man_k"], "man.delta": math.radians(c["man_delta_deg"]),
                      "man.v": c["v_ship"]},
                     ["man.k_gain", "man.delta", "man.v"]),
        _prop_target("ep.shaft_power", "electric_propulsion", "轴功率", "W",
                     lambda **k: A.ep_shaft_power(k["ep.p_elec"], k["ep.eta_sb"], k["ep.eta_tr"],
                                                  k["ep.eta_dr"], k["ep.eta_mo"]),
                     {"ep.p_elec": c["ep_p_elec"], "ep.eta_sb": c["ep_eta_sb"],
                      "ep.eta_tr": c["ep_eta_tr"], "ep.eta_dr": c["ep_eta_dr"],
                      "ep.eta_mo": c["ep_eta_mo"]},
                     ["ep.p_elec", "ep.eta_sb", "ep.eta_tr", "ep.eta_dr", "ep.eta_mo"]),
        _prop_target("ps.fuel_per_day", "power_system", "日油耗", "kg",
                     lambda **k: A.ps_fuel_per_day(k["ps.demand"], k["ps.sfoc"]),
                     {"ps.demand": c["ep_p_elec"] * 0.9269 / 0.97 + c["ps_hotel"],
                      "ps.sfoc": c["ps_sfoc"] * 1e-3 / 3.6e6},
                     ["ps.demand", "ps.sfoc"]),
    ]

    # 预声明期望（先验推导，见模块 docstring）：
    #  平局规则：|S| ↓ → |S|·σ_rel ↓ → input_id ↑
    #  弹性：E∝m^1.0867·R^-0.26·ρ^-1·c^-1；KE∝m·v²；ΔW∝Δσ·Δε_p·V；BOR∝U·A·ΔT/(h_fg)；
    #        f_MHD∝σ·u·B²；t_c∝R0·Δp^-0.5；R_AA∝ρ_air·C_D·A_T·V_RW²；D_T/L∝V/(K·δ·L)；
    #        P_D∝P_elec·Πη；m_fuel∝P_dem·SFOC
    expectations = {
        "top_k": 5,
        "per_target_order": {
            "undex.shock_energy": ["undex.mass_kg", "undex.c", "undex.rho", "undex.standoff_m"],
            "collision.ke_initial": ["collision.v1", "collision.m1"],
            "fatigue.cycle_energy": ["fatigue.deps_p", "fatigue.volume", "fatigue.dsigma"],
            "lng.bor_per_day": ["lng.u_ins", "lng.dT", "lng.area", "lng.hfg"],
            "mhd.force_density": ["mhd.b", "mhd.sigma_e", "mhd.u"],
            "cav.collapse_time": ["cav.r0", "cav.dp"],
            "iso.raa": ["iso.v_rw", "iso.cd", "iso.area_t", "iso.rho_air"],
            "man.tactical_diameter_over_l": ["man.k_gain", "man.v", "man.delta"],
            "ep.shaft_power": ["ep.p_elec", "ep.eta_dr", "ep.eta_mo", "ep.eta_sb", "ep.eta_tr"],
            "ps.fuel_per_day": ["ps.sfoc", "ps.demand"],
        },
        "global_elasticity_rank_expected": [
            "iso.raa:iso.v_rw", "collision.ke_initial:collision.v1",
            "mhd.force_density:mhd.b", "undex.shock_energy:undex.mass_kg",
            "fatigue.cycle_energy:fatigue.deps_p",
        ],
        "global_variance_rank_expected": [
            "iso.raa:iso.v_rw", "fatigue.cycle_energy:fatigue.deps_p",
            "fatigue.cycle_energy:fatigue.volume",
            "man.tactical_diameter_over_l:man.k_gain", "lng.bor_per_day:lng.u_ins",
        ],
        "sign_expectations": [
            {"target_id": "undex.shock_energy", "input_id": "undex.mass_kg", "sign": "+",
             "abs_elasticity": 1.0867, "tol_rel": 0.02},
            {"target_id": "undex.shock_energy", "input_id": "undex.standoff_m", "sign": "-",
             "abs_elasticity": 0.26, "tol_rel": 0.05},
            {"target_id": "collision.ke_initial", "input_id": "collision.v1", "sign": "+",
             "abs_elasticity": 2.0, "tol_rel": 0.01},
            {"target_id": "mhd.force_density", "input_id": "mhd.b", "sign": "+",
             "abs_elasticity": 2.0, "tol_rel": 0.01},
            {"target_id": "cav.collapse_time", "input_id": "cav.dp", "sign": "-",
             "abs_elasticity": 0.5, "tol_rel": 0.02},
            {"target_id": "lng.bor_per_day", "input_id": "lng.hfg", "sign": "-",
             "abs_elasticity": 1.0, "tol_rel": 0.01},
            {"target_id": "man.tactical_diameter_over_l", "input_id": "man.k_gain", "sign": "-",
             "abs_elasticity": 1.0, "tol_rel": 0.01},
            {"target_id": "iso.raa", "input_id": "iso.v_rw", "sign": "+",
             "abs_elasticity": 2.0, "tol_rel": 0.01},
            {"target_id": "iso.raa", "input_id": "iso.area_t", "sign": "+",
             "abs_elasticity": 1.0, "tol_rel": 0.01},
        ],
    }
    return {"targets": targets, "expectations": expectations}


def build_cross_checks() -> List[Dict[str, Any]]:
    """跨面数值交叉检查（表达式引用锚 id，白名单 AST 求值）。"""
    c = CASE
    return [
        {"check_id": "cc_power_margin_n1", "expr": "ps.available_n1 - ps.total_demand",
         "relation": "gte", "rhs": 0.0,
         "note": "N+1 可用容量必须覆盖总电负荷"},
        {"check_id": "cc_thrust_ge_resistance", "expr": "ep.thrust",
         "relation": "gte", "rhs": c["design_resistance"],
         "note": "电力推进有效推力须 ≥ 设计阻力预算 1.40 MN"},
        {"check_id": "cc_imo_turning_limit", "expr": "man.tactical_diameter_over_l",
         "relation": "lte", "rhs": 5.0,
         "note": "IMO A.749 战术直径 ≤5L"},
        {"check_id": "cc_bor_upper", "expr": "lng.bor_per_day", "relation": "lte",
         "rhs": 1.5e-3, "note": "BOR ≤0.15 %/day（薄膜舱设计上界）"},
        {"check_id": "cc_bor_lower", "expr": "lng.bor_per_day", "relation": "gte",
         "rhs": 7.0e-4, "note": "BOR ≥0.07 %/day（低于此值说明热漏模型漏项）"},
        {"check_id": "cc_shock_coupling_band",
         "expr": "undex.shock_energy / undex.chemical_energy", "relation": "lte",
         "rhs": 0.30, "note": "冲击波能量占比 ≤30%"},
        {"check_id": "cc_fatigue_sn_validity", "expr": "fatigue.life_cycles", "relation": "lte",
         "rhs": 1.0e7, "note": "Basquin 曲线适用上限 1e7 循环"},
        {"check_id": "cc_iso_correction_sign", "expr": "iso.speed_correction", "relation": "lte",
         "rhs": 0.0, "note": "风/浪修正使实测航速修正为负（阻力增加）"},
        {"check_id": "cc_prop_consistency", "expr": "ps.total_demand",
         "relation": "gte", "rhs": 1.0,
         "note": "总电负荷为正（量级 sanity）"},
    ]


def build_manifest(report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "suite_id": SUITE_ID,
        "description": "P135-W2 十物理面输出集成验证：能量框架自洽 + 耦合接口契约 + "
                       "解析锚基准库 + 一阶误差传播灵敏度",
        "units": UNITS,
        "frame_classes": FRAME_CLASSES,
        "anchors": build_anchors(),
        "balances": build_balances(),
        "ordering": build_ordering(),
        "interfaces": build_interfaces(),
        "propagation": build_propagation(),
        "cross_checks": build_cross_checks(),
        "case": CASE,
        "external_nodes": list(EXTERNAL_NODES),
    }
    if report:
        manifest["report"] = report
    return manifest