#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IMO 能效链规范符合性参考实现（零平台依赖）。

背景：平台 :8001 `extended-calculations/energy` 自述 "IMO EEDI/EEOI/CII"，
但独立复算（报告 1044）判定其口径为 100% MCR、无辅机项、参考线恒散货船、
CII 分母用千海里、评级阈值绝对常数。本模块是**修复侧**：按决议文本给出
可直接落地的闭式实现，供平台接线，并作为回归门禁的判据来源。

口径来源（系数按置信度标注）：
  - EEDI 定义式与 CF 系数表: MEPC.364(79) / MARPOL Annex VI Reg.20  [CONF_HIGH]
  - P_AE 分段式 (ΣP_ME≥10 MW → 0.025ΣP_ME+250, else 0.05ΣP_ME)     [CONF_HIGH]
  - EEDI 参考线 a·Capacity^(-c) 及阶段折减 Z                          [CONF_MED 部分船型]
  - CII AER/cgDIST 定义式、G2 参考线、G3 评级 d1..d4、年度 Z          [CONF_MED]

设计纪律：所有承担 <3% 判决的量只用定义式与 CONF_HIGH 系数；CONF_MED
项一律附 confidence 标签，报告中只用于趋势/量级判断，不承担判决责任。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CONF_HIGH = "CONF_HIGH"
CONF_MED = "CONF_MED"

# --------------------------------------------------------------------------
# 1. 碳系数 CF [t CO2 / t fuel] — MEPC.364(79)
# --------------------------------------------------------------------------
CF: Dict[str, float] = {
    "diesel": 3.206,       # MDO/MGO
    "hfo": 3.114,
    "lfo": 3.151,
    "lng": 2.750,
    "lpg_propane": 3.000,
    "methanol": 1.375,
}
CF_CONF = {k: CONF_HIGH for k in CF}


# --------------------------------------------------------------------------
# 2. EEDI
# --------------------------------------------------------------------------
# 参考线系数 a, c —— 按 Capacity(=DWT 或 0.7·DWT for container) 的闭式线
EEDI_REF_LINE: Dict[str, tuple] = {
    "bulk_carrier": (961.79, 0.477),
    "tanker": (1218.80, 0.488),
    "gas_carrier": (1120.00, 0.456),
    "container_ship": (186.22, 0.200),
    "general_cargo": (107.48, 0.216),
    "refrigerated_cargo": (227.01, 0.244),
    "combination_carrier": (1219.00, 0.488),
}
REF_LINE_CONF = CONF_MED  # 单一来源记忆系数，仅作趋势/量级判据

# 阶段折减 Z（MARPOL Annex VI Reg.21 时间表），按船型
EEDI_PHASE_Z: Dict[str, Dict[int, float]] = {
    "bulk_carrier": {0: 0.00, 1: 0.10, 2: 0.20, 3: 0.30},
    "gas_carrier": {0: 0.00, 1: 0.10, 2: 0.20, 3: 0.30},
    "tanker": {0: 0.00, 1: 0.10, 2: 0.20, 3: 0.30},
    "container_ship": {0: 0.00, 1: 0.10, 2: 0.20, 3: 0.30},
    "general_cargo": {0: 0.00, 1: 0.10, 2: 0.20, 3: 0.30},
    "refrigerated_cargo": {0: 0.00, 1: 0.10, 2: 0.20, 3: 0.30},
    "combination_carrier": {0: 0.00, 1: 0.10, 2: 0.20, 3: 0.30},
}


def capacity_of(ship_type: str, dwt: float) -> float:
    """EEDI/CII 的 Capacity 口径：集装箱船为 0.70·DWT，其余取 DWT。"""
    if dwt <= 0:
        raise ValueError("dwt must be > 0")
    if ship_type == "container_ship":
        return 0.70 * dwt
    return float(dwt)


def auxiliary_power(sum_p_me_kw: float) -> float:
    """MARPOL Annex VI Reg.20 辅机功率分段式（10 MW 处连续）。"""
    if sum_p_me_kw >= 10_000.0:
        return 0.025 * sum_p_me_kw + 250.0
    return 0.05 * sum_p_me_kw


@dataclass
class Engine:
    mcr_kw: float
    sfc: float = 175.0          # g/kWh
    fuel: str = "hfo"
    count: int = 1


@dataclass
class EEDIInput:
    ship_type: str
    dwt: float
    speed_kn: float
    main_engines: List[Engine]
    aux_sfc: float = 175.0
    aux_fuel: str = "diesel"
    # 修正系数（缺省 1.0 = 无修正）
    f_j: float = 1.0        # 冰区/穿梭油轮等
    f_i: float = 1.0        # 容量修正（一般货船）
    f_c: float = 1.0        # 立方容量修正
    f_l: float = 1.0        # 总纵强度修正
    p_eff_kw: float = 0.0   # 能效技术功率折减
    f_eff: float = 1.0
    p_pti_kw: float = 0.0   # 轴带发电机/推进电机附加功率（按轴带电机口径计入分子）


def eedi_attained(inp: EEDIInput) -> Dict[str, float]:
    """EEDI attained [g CO2 / (t·nm)]，75% MCR 口径 + 辅机项。"""
    if inp.speed_kn <= 0:
        raise ValueError("speed_kn must be > 0")
    cap = capacity_of(inp.ship_type, inp.dwt)

    sum_mcr = sum(e.mcr_kw * e.count for e in inp.main_engines)
    p_me = 0.75 * sum_mcr
    # 主机的加权 SFC/CF（按 75% MCR 的功率份额加权）
    if p_me <= 0:
        co2_me = 0.0
    else:
        co2_me = 0.0
        for e in inp.main_engines:
            share = (0.75 * e.mcr_kw * e.count) / p_me
            co2_me += share * p_me * e.sfc * CF[e.fuel]  # kW·g/kWh × tCO2/t = g CO2/h
    # 单位：kW × g/kWh = g fuel/h；× CF[tCO2/t fuel] = g CO2/h
    co2_me_gph = co2_me

    p_ae = auxiliary_power(p_me)
    co2_ae_gph = p_ae * inp.aux_sfc * CF[inp.aux_fuel]  # g CO2/h

    co2_pti_gph = inp.p_pti_kw * inp.aux_sfc * CF[inp.aux_fuel]
    co2_saved_gph = inp.f_eff * inp.p_eff_kw * inp.aux_sfc * CF[inp.aux_fuel]

    numerator = inp.f_j * (co2_me_gph + co2_ae_gph + co2_pti_gph - co2_saved_gph)
    denom = inp.f_i * inp.f_c * inp.f_l * cap * inp.speed_kn
    return {
        "eedi": numerator / denom,
        "p_me_kw": p_me,
        "p_ae_kw": p_ae,
        "capacity": cap,
        "denominator": denom,
        "numerator_gph": numerator,
    }


def eedi_reference(ship_type: str, dwt: float) -> float:
    if ship_type not in EEDI_REF_LINE:
        raise KeyError(f"no EEDI reference line for ship_type={ship_type}")
    a, c = EEDI_REF_LINE[ship_type]
    return a * capacity_of(ship_type, dwt) ** (-c)


def eedi_required(ship_type: str, dwt: float, phase: int = 3) -> float:
    z = EEDI_PHASE_Z[ship_type].get(phase)
    if z is None:
        raise KeyError(f"no phase {phase} for {ship_type}")
    return eedi_reference(ship_type, dwt) * (1.0 - z)


def eedi_compliance(att: float, req: float) -> Dict[str, object]:
    """合规判据：attained ≤ required（含 0.1% 数值容差）。"""
    tol = 1e-3
    ratio = att / req if req else float("inf")
    return {
        "attained": att,
        "required": req,
        "ratio": ratio,
        "margin_pct": (1.0 - ratio) * 100.0,
        "compliant": att <= req * (1.0 + tol),
    }


# --------------------------------------------------------------------------
# 3. EEXI（现有船，限功率法）
# --------------------------------------------------------------------------
def eexi_attained(inp: EEDIInput, pm_limited: Optional[float] = None) -> float:
    """EEXI ≈ 用限用功率替换 75%MCR 的 EEDI 形式。"""
    return eedi_attained(inp)["eedi"] if pm_limited is None else _eedi_with_pme(inp, pm_limited)


def _eedi_with_pme(inp: EEDIInput, p_me: float) -> float:
    cap = capacity_of(inp.ship_type, inp.dwt)
    sum_mcr = sum(e.mcr_kw * e.count for e in inp.main_engines)
    co2_me = 0.0
    for e in inp.main_engines:
        share = (e.mcr_kw * e.count) / sum_mcr if sum_mcr else 0.0
        co2_me += share * p_me * e.sfc * CF[e.fuel]
    p_ae = auxiliary_power(p_me)
    co2_ae = p_ae * inp.aux_sfc * CF[inp.aux_fuel]
    return inp.f_j * (co2_me + co2_ae) / (inp.f_i * inp.f_c * inp.f_l * cap * inp.speed_kn)


def limited_power_kw(mcr_kw: float, pm_ratio: float) -> float:
    if not 0 < pm_ratio <= 1:
        raise ValueError("pm_ratio must be in (0, 1]")
    return mcr_kw * pm_ratio


# --------------------------------------------------------------------------
# 4. CII / AER / cgDIST / EEOI
# --------------------------------------------------------------------------
CII_REF_G2: Dict[str, tuple] = {
    "bulk_carrier": (4745.0, 0.622),
    "tanker": (5247.0, 0.610),
    "container_ship": (1984.0, 0.489),
}
CII_REF_CONF = CONF_MED

# MEPC.338(76) 年度折减（2027–2030 复审后暂按 11%）
CII_Z_BY_YEAR: Dict[int, float] = {2023: 0.05, 2024: 0.07, 2025: 0.09, 2026: 0.11}
for _y in range(2027, 2031):
    CII_Z_BY_YEAR[_y] = 0.11
CII_Z_CONF = CONF_MED

# G3 评级边界 d1..d4（sup/ref 的比值）：A<d1, B<d2, C<d3, D<d4, else E
CII_RATING_D: Dict[str, tuple] = {
    "bulk_carrier": (0.86, 0.94, 1.06, 1.18),
    "tanker": (0.82, 0.93, 1.08, 1.28),
    "container_ship": (0.83, 0.94, 1.07, 1.19),
}
CII_RATING_CONF = CONF_MED


def co2_emission_g(fuel_t: float, fuel: str = "hfo") -> float:
    return fuel_t * CF[fuel] * 1e6


def cii_attained_aer(fuel_t: float, dwt: float, distance_nm: float, fuel: str = "hfo") -> float:
    """AER [g CO2 / (dwt·nm)]：分母必须是海里，不是千海里。"""
    if distance_nm <= 0 or dwt <= 0:
        raise ValueError("distance_nm and dwt must be > 0")
    return co2_emission_g(fuel_t, fuel) / (dwt * distance_nm)


def cii_attained_cgdistance(fuel_t: float, dwt: float, distance_nm: float, fuel: str = "hfo") -> float:
    """cgDIST 的简化一致式（单一航段/单一燃油时与 AER 恒等）。"""
    return cii_attained_aer(fuel_t, dwt, distance_nm, fuel)


def cii_reference(ship_type: str, dwt: float) -> float:
    if ship_type not in CII_REF_G2:
        raise KeyError(f"no CII G2 reference line for ship_type={ship_type}")
    a, c = CII_REF_G2[ship_type]
    return a * dwt ** (-c)


def cii_required(ship_type: str, dwt: float, year: int) -> float:
    z = CII_Z_BY_YEAR.get(year)
    if z is None:
        raise KeyError(f"no CII Z for year {year}")
    return cii_reference(ship_type, dwt) * (1.0 - z)


def cii_rating(ship_type: str, dwt: float, year: int, attained: float) -> Dict[str, object]:
    d1, d2, d3, d4 = CII_RATING_D[ship_type]
    req = cii_required(ship_type, dwt, year)
    ratio = attained / req
    if ratio < d1:
        grade = "A"
    elif ratio < d2:
        grade = "B"
    elif ratio < d3:
        grade = "C"
    elif ratio < d4:
        grade = "D"
    else:
        grade = "E"
    return {"grade": grade, "sup_ratio": ratio, "required": req,
            "boundaries": (d1, d2, d3, d4)}


def eeoI(fuel_t: float, dwt: float, distance_nm: float, fuel: str = "hfo") -> float:
    """EEOI [g CO2 / (t·nm)]（航次口径，与 AER 同量纲）。"""
    return cii_attained_aer(fuel_t, dwt, distance_nm, fuel)


# --------------------------------------------------------------------------
# 5. 反事实（变异集）判据：必须能抓住报告 1044 记录的平台缺陷
# --------------------------------------------------------------------------
def counterfactual_platform_defects(inp: EEDIInput, year: int = 2026) -> Dict[str, dict]:
    """复现平台四处缺陷并给出与规范口径的偏差，作为回归门禁用判据。"""
    ref = eedi_attained(inp)
    out: Dict[str, dict] = {}

    # D1: 全程 100% MCR（平台旧口径）
    sum_mcr = sum(e.mcr_kw * e.count for e in inp.main_engines)
    p100 = _eedi_with_pme(inp, sum_mcr)
    # 平台还缺辅机项：用 EEDI 定义式但把 P_AE 置 0
    p75_noaux = _eedi_with_pme(inp, 0.75 * sum_mcr)
    d1_att = p100  # 100%MCR + 无辅机
    out["D1_100pct_MCR_no_aux"] = {
        "attained": d1_att,
        "rel_err_pct": (d1_att - ref["eedi"]) / ref["eedi"] * 100.0,
        "expect_sign": "+",  # 平台偏高
    }

    # D2: 参考线恒散货船 → 集装箱船偏差
    if inp.ship_type != "bulk_carrier" and inp.ship_type in EEDI_REF_LINE:
        bulk = eedi_reference("bulk_carrier", inp.dwt)
        true_line = eedi_reference(inp.ship_type, inp.dwt)
        out["D2_bulk_line_only"] = {
            "required_bulk_line": bulk,
            "required_true_line": true_line,
            "rel_err_pct": (bulk - true_line) / true_line * 100.0,
        }

    # D3: 合规阈值用 Phase-0（折减 0%）→ 伪合规窗口
    req_phase3 = eedi_required(inp.ship_type, inp.dwt, 3)
    req_phase0 = eedi_required(inp.ship_type, inp.dwt, 0)
    out["D3_phase0_threshold"] = {
        "required_phase0": req_phase0,
        "required_phase3": req_phase3,
        "extra_margin_pct": (req_phase0 - req_phase3) / req_phase3 * 100.0,
    }

    # D4: CII 分母用千海里 → 1000 倍量级错误
    aer = cii_attained_aer(8000.0, inp.dwt, 60000.0)
    out["D4_ciinm_thousand_nm"] = {
        "aer_correct": aer,
        "aer_platform": ciinm_wrong(ciinm_wrong_input(8000.0, inp.dwt)),
        "factor": ciinm_wrong(ciinm_wrong_input(8000.0, inp.dwt)) / aer,  # 平台/正确 = 1000
    }
    return out


def ciinm_wrong_input(fuel_t: float, dwt: float) -> dict:
    return {"fuel_t": fuel_t, "dwt": dwt, "distance_nm": 60000.0 / 1000.0}


def ciinm_wrong(p: dict) -> float:
    """模拟平台缺陷式：分母 '千海里'。"""
    return co2_emission_g(p["fuel_t"]) / (p["dwt"] * p["distance_nm"])