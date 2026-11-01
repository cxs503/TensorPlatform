"""绿色航行域（EEDI / EEXI / CII）规则复算参考实现。

背景
----
平台现网（:8001）绿色航行域只有两条路径，且均为 MOCK：
  * ``api/green_shipping.py``        —— 7 行硬编码 ``{optimal_speed: 18.5, fuel_saving: 12.0, cii_rating: "B"}``；
  * ``api/extended_calculations.py`` —— ``eedi = P*C_F*SFC/(f_i*DWT*V)``，``f_i ≡ 1.0`` 恒等，
    缺 P_AE / f_j / f_c / f_l / P_PTI / 节能技术项；``eedi_baseline`` 把散货船系数
    (961.79, -0.477) 套到所有船型；"阶段"用基线 0.7/0.8/0.9 阈值臆造；CII 用绝对阈值
    (cii<4→A, <5→B, <6→C) 评级，与 IMO dd-vector 法无关。

本模块按规则闭式复算，作为该域的参考实现（可被上层 API 直接调用），并把"规则系数
的可核验性"显式分级，避免把转录系数冒充为已验证基准。

规则来源（文本层面）
--------------------
* EEDI 计算公式        : MARPOL Annex VI Reg.22 + MEPC.308(73) §2
* EEDI 参考线/阶段     : MEPC.203(62) Annex / MEPC.328(76)
* 冰级容量修正 f_i     : MEPC.1/Circ.874 §4.2
* 天气修正 f_w         : MEPC.1/Circ.855/Rev.2
* EEXI 计算与 P_ME/P_AE: MEPC.333(76) / MEPC.350(78) §2.2
* CII G1（获得值/参考线/折减因子 Z）: MEPC.336(76) / MEPC.338(76)
* CII G4（dd-vectors 评级）: MEPC.354(78)

系数可核验性分级（``RULE_COEFFICIENT_PROVENANCE``）
---------------------------------------------------
* ``IN_REPO_CORROBORATED``        : 平台既有代码/工件给出同值，双源可交叉核对；
* ``TRANSCRIBED_OFFLINE_UNVERIFIED``: 本会话离线，仅凭规则文本转录，**未经规则原文机检**，
                                    禁止直接用于法定合规判定，须与规则原文逐条复核。

本模块不做重计算（全部为闭式算术），可在高负载机器上安全运行。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 1. 规则系数表
# ---------------------------------------------------------------------------

#: MEPC.308(73) Table 1 —— 燃油碳转换因子 C_F [g CO2 / g fuel]
CF_TABLE: Dict[str, float] = {
    "diesel_gas_oil": 3.206,
    "lfo": 3.151,
    "hfo": 3.114,
    "lpg_propane": 3.000,
    "lpg_butane": 3.030,
    "lng": 2.750,
    "methanol": 1.375,
}

#: EEDI 参考线 EEDI_ref = a * b^(-c)，b 为载重量 DWT（集装箱船等按规则取容量参数）
EEDI_REFERENCE_LINES: Dict[str, Tuple[float, float]] = {
    "bulk_carrier": (961.79, 0.477),
    "tanker": (1218.80, 0.488),
    "container_ship": (174.22, 0.201),
    "gas_carrier": (1120.00, 0.456),
    "general_cargo": (107.48, 0.216),
    "refrigerated_cargo": (227.01, 0.244),
    "combination_carrier": (1219.00, 0.488),
}

#: EEDI 阶段折减率（相对参考线的 X%），按规则适用于 2025 起的 Phase 3
EEDI_PHASE_REDUCTION: Dict[str, float] = {
    "phase_0": 0.0,
    "phase_1": 10.0,
    "phase_2": 20.0,
    "phase_3": 30.0,
}

#: Phase 3 适用性（DWT 门槛，单位 t）；不在表中者 Phase 3 不适用
EEDI_PHASE3_MIN_DWT: Dict[str, float] = {
    "bulk_carrier": 15000.0,
    "tanker": 15000.0,
    "gas_carrier": 15000.0,
    "container_ship": 15000.0,
    "combination_carrier": 15000.0,
}

#: EEXI 相对 EEDI 参考线的折减率 X_EEXI [%]
EEXI_REDUCTION_FROM_EEDI_REF: Dict[str, float] = {
    "bulk_carrier": 20.0,
    "tanker": 20.0,
    "gas_carrier": 20.0,
    "container_ship": 30.0,
    "general_cargo": 30.0,
    "refrigerated_cargo": 15.0,
    "combination_carrier": 20.0,
}

#: CII 参考线 CII_ref = a * Capacity^(-c) [g CO2/(dwt*nm)]（G1 guidelines）
CII_REFERENCE_LINES: Dict[str, Tuple[float, float]] = {
    "bulk_carrier": (4745.0, 0.622),
    "tanker": (5247.0, 0.610),
    "container_ship": (1984.0, 0.489),
}

#: CII 年度折减因子 Z [%]（2023 起，MEPC.338(76)）
CII_REDUCTION_Z: Dict[int, float] = {2023: 5.0, 2024: 7.0, 2025: 9.0, 2026: 11.0}

#: CII 评级 dd-vectors (d1, d2, d3, d4)，G4 guidelines
CII_DD_VECTORS: Dict[str, Tuple[float, float, float, float]] = {
    "bulk_carrier": (0.86, 0.94, 1.06, 1.18),
    "tanker": (0.82, 0.93, 1.08, 1.28),
    "container_ship": (0.83, 0.94, 1.07, 1.19),
}

#: 系数可核验性分级 —— 决定某张表能否作为"基准"用于误差判定
RULE_COEFFICIENT_PROVENANCE: Dict[str, Dict[str, object]] = {
    "CF_TABLE": {
        "source": "MEPC.308(73) Table 1",
        "grade": "TRANSCRIBED_OFFLINE_UNVERIFIED",
        "note": "碳转换因子为规则常数表，需与规则原文逐条复核",
    },
    "EEDI_REFERENCE_LINES.bulk_carrier": {
        "source": "MEPC.203(62); 与平台 extended_calculations.py:254 注释同值（双源）",
        "grade": "IN_REPO_CORROBORATED",
        "note": "961.79 / 0.477 在平台既有代码中独立出现，构成本仓内交叉校核锚点",
    },
    "EEDI_REFERENCE_LINES.others": {
        "source": "MEPC.203(62) Annex",
        "grade": "TRANSCRIBED_OFFLINE_UNVERIFIED",
        "note": "离线转录，未与规则原表逐项比对",
    },
    "EEDI_PHASE_REDUCTION": {
        "source": "MEPC.203(62) / MEPC.328(76)",
        "grade": "TRANSCRIBED_OFFLINE_UNVERIFIED",
        "note": "10/20/30% 三阶段为公开常识级数值，但适用性与 DWT 门槛需复核",
    },
    "EEXI_REDUCTION_FROM_EEDI_REF": {
        "source": "MEPC.328(76) Annex",
        "grade": "TRANSCRIBED_OFFLINE_UNVERIFIED",
        "note": "各船型 15~30% 折减率各版汇总口径不一，须以规则附录取值",
    },
    "CII_REFERENCE_LINES": {
        "source": "MEPC.336(76) G1",
        "grade": "TRANSCRIBED_OFFLINE_UNVERIFIED",
        "note": "仅收录散货/油轮/集装箱三类；其余船型主动 NOT_IMPLEMENTED，不臆造",
    },
    "CII_REDUCTION_Z": {
        "source": "MEPC.338(76)",
        "grade": "TRANSCRIBED_OFFLINE_UNVERIFIED",
        "note": "2023-2026: 5/7/9/11%",
    },
    "CII_DD_VECTORS": {
        "source": "MEPC.354(78) G4 Table 1",
        "grade": "TRANSCRIBED_OFFLINE_UNVERIFIED",
        "note": "dd-vectors 随修订变化，须以最新 G4 表为准",
    },
}


class RuleCoefficientMissing(NotImplementedError):
    """该船型/年份的规则系数未收录 —— 显式 NOT_IMPLEMENTED，不用臆造值兜底。"""


def provenance_report() -> Dict[str, Dict[str, object]]:
    """返回系数可核验性分级表（供证据工件与报告引用）。"""
    return {k: dict(v) for k, v in RULE_COEFFICIENT_PROVENANCE.items()}


# ---------------------------------------------------------------------------
# 2. 基础构件
# ---------------------------------------------------------------------------

def carbon_factor(fuel: str) -> float:
    """燃油碳转换因子 C_F [g CO2/g fuel]。未知燃油抛 ValueError（不静默兜底）。"""
    key = fuel.strip().lower().replace(" ", "_").replace("/", "_")
    if key not in CF_TABLE:
        raise ValueError(
            f"未知燃油类型 {fuel!r}；已收录 {sorted(CF_TABLE)}（MEPC.308(73) Table 1）"
        )
    return CF_TABLE[key]


def auxiliary_power(mcr_total_kw: float) -> float:
    """辅助功率 P_AE [kW]（MEPC.308(73) §2.2.3 / MEPC.350(78) §2.2.3）。

    P_AE = 0.05 * ΣMCR_ME                    (ΣMCR_ME < 10000 kW)
    P_AE = 0.025 * ΣMCR_ME + 250             (ΣMCR_ME >= 10000 kW)

    两段在 10000 kW 处连续（0.05*10000 = 500 = 0.025*10000 + 250），测试以此做闭式判据。
    """
    if not math.isfinite(mcr_total_kw) or mcr_total_kw <= 0.0:
        raise ValueError(f"ΣMCR_ME 必须为正有限值，收到 {mcr_total_kw!r}")
    if mcr_total_kw >= 10000.0:
        return 0.025 * mcr_total_kw + 250.0
    return 0.05 * mcr_total_kw


def estimated_speed_from_power(v_ref: float, p_ref: float, p_new: float) -> float:
    """由参考工况按立方律换算参考航速（ITTC 常用 V ∝ P^(1/3) 近似，用于 EEXI 限功率后 V_ref）。

    仅作"缺少试航/模型试验数据时"的兜底近似，报告中必须标注为近似而非规则值。
    """
    if v_ref <= 0.0 or p_ref <= 0.0 or p_new <= 0.0:
        raise ValueError("v_ref / p_ref / p_new 必须为正")
    return v_ref * (p_new / p_ref) ** (1.0 / 3.0)


def eedi_reference_line(ship_type: str, capacity: float) -> float:
    """EEDI 参考线值 EEDI_ref = a * b^(-c)。"""
    if ship_type not in EEDI_REFERENCE_LINES:
        raise RuleCoefficientMissing(
            f"EEDI 参考线未收录船型 {ship_type!r}；已收录 {sorted(EEDI_REFERENCE_LINES)}"
        )
    if capacity <= 0.0:
        raise ValueError(f"capacity 必须为正，收到 {capacity!r}")
    a, c = EEDI_REFERENCE_LINES[ship_type]
    return a * capacity ** (-c)


def required_eedi(ship_type: str, capacity: float, phase: str = "phase_3") -> float:
    """要求 EEDI = (1 - X/100) * EEDI_ref；Phase 3 受船型/DWT 门槛约束。"""
    if phase not in EEDI_PHASE_REDUCTION:
        raise ValueError(f"未知阶段 {phase!r}；可选 {sorted(EEDI_PHASE_REDUCTION)}")
    ref = eedi_reference_line(ship_type, capacity)
    x = EEDI_PHASE_REDUCTION[phase]
    if phase == "phase_3":
        gate = EEDI_PHASE3_MIN_DWT.get(ship_type)
        if gate is None:
            raise RuleCoefficientMissing(f"Phase 3 对 {ship_type!r} 不适用（未收录门槛）")
        if capacity < gate:
            raise RuleCoefficientMissing(
                f"Phase 3 仅适用于容量 >= {gate} 的 {ship_type}（当前 {capacity}）"
            )
    return (1.0 - x / 100.0) * ref


def required_eexi(ship_type: str, capacity: float) -> float:
    """要求 EEXI = (1 - X_EEXI/100) * EEDI_ref（相对 EEDI 参考线折减）。"""
    if ship_type not in EEXI_REDUCTION_FROM_EEDI_REF:
        raise RuleCoefficientMissing(
            f"EEXI 折减率未收录船型 {ship_type!r}；已收录 {sorted(EEXI_REDUCTION_FROM_EEDI_REF)}"
        )
    x = EEXI_REDUCTION_FROM_EEDI_REF[ship_type]
    return (1.0 - x / 100.0) * eedi_reference_line(ship_type, capacity)


# ---------------------------------------------------------------------------
# 3. EEDI / EEXI 获得值
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EfficiencyTechnology:
    """节能技术项（电/机/废热回收等）。

    ``kind="mechanical"``  —— 减少主机燃油消耗，按 C_FME*SFC_ME 计价；
    ``kind="electrical"``  —— 减少辅机燃油消耗，按 C_FAE*SFC_AE 计价。
    """

    name: str
    p_eff_kw: float
    f_eff: float = 1.0
    kind: str = "mechanical"

    def __post_init__(self) -> None:
        if self.kind not in ("mechanical", "electrical"):
            raise ValueError(f"kind 必须为 mechanical/electrical，收到 {self.kind!r}")
        if self.p_eff_kw < 0.0 or not (0.0 <= self.f_eff <= 1.0):
            raise ValueError("p_eff_kw 须非负、f_eff 须在 [0,1]")


def attained_eedi(
    *,
    p_me: Sequence[float],
    sfc_me: Sequence[float],
    cf_me: Sequence[float],
    p_ae: float,
    sfc_ae: float,
    cf_ae: float,
    capacity: float,
    v_ref: float,
    f_j: float = 1.0,
    f_i: float = 1.0,
    f_c: float = 1.0,
    f_l: float = 1.0,
    f_w: float = 1.0,
    f_ice: float = 1.0,
    p_pti: float = 0.0,
    sfc_pti: Optional[float] = None,
    cf_pti: Optional[float] = None,
    eff_tech: Iterable[EfficiencyTechnology] = (),
) -> float:
    """获得 EEDI [g CO2/(t*nm)]，MARPOL Annex VI Reg.22 / MEPC.308(73) §2。

    分子 = (∏f_j) * Σ_i [ P_ME,i * f_ice,i * C_FME,i * SFC_ME,i ]
           + P_AE * C_FAE * SFC_AE
           + P_PTI * C_FPTI * SFC_PTI                     （轴带电机/PTI 附加油耗）
           - Σ_eff f_eff * P_eff,mech * C_FME * SFC_ME    （机械类节能技术）
           - Σ_eff f_eff * P_eff,elec * C_FAE * SFC_AE    （电气类节能技术）
    分母 = f_i * f_c * f_l * Capacity * V_ref

    f_w（天气修正，MEPC.1/Circ.855/Rev.2）与 f_ice（冰级修正，MEPC.1/Circ.874）
    分别作用于分子与主机功率项；两者缺省 1.0 时不改变规则基本式。
    """
    if len(p_me) != len(sfc_me) or len(p_me) != len(cf_me):
        raise ValueError("p_me / sfc_me / cf_me 长度须一致")
    if not p_me:
        raise ValueError("至少需要一台主机")
    if capacity <= 0.0 or v_ref <= 0.0:
        raise ValueError(f"capacity、v_ref 必须为正，收到 {capacity!r} / {v_ref!r}")
    if f_i <= 0.0 or f_c <= 0.0 or f_l <= 0.0 or f_j <= 0.0:
        raise ValueError("f_i / f_c / f_l / f_j 必须为正")

    me_term = sum(
        p * f_ice * cf * sfc for p, cf, sfc in zip(p_me, sfc_me, cf_me)
    )
    numerator = f_j * me_term + p_ae * cf_ae * sfc_ae
    if p_pti > 0.0:
        numerator += p_pti * (cf_pti if cf_pti is not None else cf_me[0]) * (
            sfc_pti if sfc_pti is not None else sfc_me[0]
        )
    mech_ref = (cf_me[0], sfc_me[0])
    ae_ref = (cf_ae, sfc_ae)
    for tech in eff_tech:
        cf_ref, sfc_ref = mech_ref if tech.kind == "mechanical" else ae_ref
        numerator -= tech.f_eff * tech.p_eff_kw * cf_ref * sfc_ref

    denominator = f_i * f_c * f_l * capacity * v_ref
    return f_w * numerator / denominator


def attained_eexi(
    *,
    mcr_me: Sequence[float],
    sfc_me: Sequence[float],
    cf_me: Sequence[float],
    sfc_ae: float,
    cf_ae: float,
    capacity: float,
    v_ref: float,
    f_j: float = 1.0,
    f_i: float = 1.0,
    f_c: float = 1.0,
    f_l: float = 1.0,
    p_pti: float = 0.0,
    eff_tech: Iterable[EfficiencyTechnology] = (),
    me_power_ratio: float = 0.75,
    p_ae_override: Optional[float] = None,
) -> float:
    """获得 EEXI [g CO2/(t*nm)]，MEPC.333(76) / MEPC.350(78) §2.2。

    与 EEDI 的差别（本函数显式实现）：
      * P_ME,i = me_power_ratio * MCR_ME,i（无轴带电机时 0.75，MEPC.350(78) §2.2.1）；
      * P_AE 由 ΣMCR_ME 按 §2.2.3 分段式导出（见 :func:`auxiliary_power`）；
      * 无 f_w 天气修正项。
    """
    if len(mcr_me) != len(sfc_me) or len(mcr_me) != len(cf_me):
        raise ValueError("mcr_me / sfc_me / cf_me 长度须一致")
    if not mcr_me:
        raise ValueError("至少需要一台主机")
    p_me = [me_power_ratio * m for m in mcr_me]
    p_ae = (
        p_ae_override
        if p_ae_override is not None
        else auxiliary_power(sum(mcr_me))
    )
    return attained_eedi(
        p_me=p_me,
        sfc_me=sfc_me,
        cf_me=cf_me,
        p_ae=p_ae,
        sfc_ae=sfc_ae,
        cf_ae=cf_ae,
        capacity=capacity,
        v_ref=v_ref,
        f_j=f_j,
        f_i=f_i,
        f_c=f_c,
        f_l=f_l,
        f_w=1.0,
        p_pti=p_pti,
        eff_tech=eff_tech,
    )


# ---------------------------------------------------------------------------
# 4. CII（G1 获得值/参考线/Z 折减；G4 评级）
# ---------------------------------------------------------------------------

def cii_reference_line(ship_type: str, capacity: float) -> float:
    """CII 参考线 CII_ref = a * Capacity^(-c) [g CO2/(dwt*nm)]。"""
    if ship_type not in CII_REFERENCE_LINES:
        raise RuleCoefficientMissing(
            f"CII 参考线未收录船型 {ship_type!r}；已收录 {sorted(CII_REFERENCE_LINES)}"
            "（未核对原文的系数一律不录入，避免臆造基准）"
        )
    if capacity <= 0.0:
        raise ValueError(f"capacity 必须为正，收到 {capacity!r}")
    a, c = CII_REFERENCE_LINES[ship_type]
    return a * capacity ** (-c)


def required_cii(ship_type: str, capacity: float, year: int) -> float:
    """要求 CII_year = (1 - Z_year/100) * CII_ref。"""
    if year not in CII_REDUCTION_Z:
        raise RuleCoefficientMissing(
            f"CII 折减因子未收录年份 {year}；已收录 {sorted(CII_REDUCTION_Z)}"
        )
    ref = cii_reference_line(ship_type, capacity)
    return (1.0 - CII_REDUCTION_Z[year] / 100.0) * ref


def attained_cii(
    *,
    fuel_mass_t: Sequence[float],
    fuel_types: Sequence[str],
    capacity: float,
    distance_nm: float,
) -> float:
    """获得 CII [g CO2/(dwt*nm)] = Σ(F_C * C_F) / (Capacity * D)。"""
    if len(fuel_mass_t) != len(fuel_types):
        raise ValueError("fuel_mass_t / fuel_types 长度须一致")
    if not fuel_mass_t:
        raise ValueError("至少需要一种燃油")
    if capacity <= 0.0 or distance_nm <= 0.0:
        raise ValueError("capacity、distance_nm 必须为正")
    co2_g = sum(m * 1.0e6 * carbon_factor(t) for m, t in zip(fuel_mass_t, fuel_types))
    return co2_g / (capacity * distance_nm)


def cii_rating(attained: float, required: float, ship_type: str) -> Tuple[str, Dict[str, float]]:
    """G4 评级（A-E）。返回 (评级, 四个分界值)。

    CII <= d1*required -> A; <= d2*required -> B; <= d3*required -> C;
    <= d4*required -> D; 否则 E。评级仅取决于获得值与要求值的比值，与绝对阈值无关
    （平台既有实现的 ``cii<4→A`` 绝对阈值法即为此处的反例）。
    """
    if ship_type not in CII_DD_VECTORS:
        raise RuleCoefficientMissing(
            f"CII dd-vectors 未收录船型 {ship_type!r}；已收录 {sorted(CII_DD_VECTORS)}"
        )
    if attained <= 0.0 or required <= 0.0:
        raise ValueError("attained / required 必须为正")
    d1, d2, d3, d4 = CII_DD_VECTORS[ship_type]
    bounds = {
        "d1": d1 * required,
        "d2": d2 * required,
        "d3": d3 * required,
        "d4": d4 * required,
    }
    ratio = attained / required
    if ratio <= d1:
        grade = "A"
    elif ratio <= d2:
        grade = "B"
    elif ratio <= d3:
        grade = "C"
    elif ratio <= d4:
        grade = "D"
    else:
        grade = "E"
    return grade, bounds


# ---------------------------------------------------------------------------
# 5. 旧实现（平台现网 :8001）的复刻 —— 仅用于缺陷量化，不作为计算路径
# ---------------------------------------------------------------------------

def legacy_eedi_extended_calculations(
    *, power_kw: float, sfc: float, dwt: float, v: float, cf: float = 3.206, f_i: float = 1.0
) -> float:
    """复刻平台 ``api/extended_calculations.py`` 的 EEDI 表达（缺陷取证用）。

    原式 ``(P * Cf * SFC) / (f_i * DWT * V)``，f_i 在源码中恒为 1.0。
    """
    if dwt <= 0.0 or v <= 0.0:
        return 0.0
    return (power_kw * cf * sfc) / (f_i * dwt * v)


def legacy_eedi_phase_label(attained: float, baseline: float) -> str:
    """复刻平台"阶段"判定（基线 0.7/0.8/0.9 阈值）—— 与规则的阶段定义无关。"""
    if attained <= baseline * 0.7:
        return "Phase 3 (30%减排)"
    if attained <= baseline * 0.8:
        return "Phase 2 (20%减排)"
    if attained <= baseline * 0.9:
        return "Phase 1 (10%减排)"
    return "Phase 0 (基准)"


def legacy_cii_rating(cii: float) -> str:
    """复刻平台 CII 绝对阈值评级（cii<4→A, <5→B, <6→C, ...）。"""
    if cii < 4:
        return "A"
    if cii < 5:
        return "B"
    if cii < 6:
        return "C"
    if cii < 7:
        return "D"
    return "E"


# ---------------------------------------------------------------------------
# 6. 综合评估（API / 证据脚本共用）
# ---------------------------------------------------------------------------

def assess_green_shipping(
    *,
    ship_type: str,
    capacity: float,
    mcr_me: Sequence[float],
    sfc_me: float,
    fuel_me: str,
    sfc_ae: float,
    fuel_ae: str,
    v_ref: float,
    f_j: float = 1.0,
    f_i: float = 1.0,
    f_c: float = 1.0,
    f_l: float = 1.0,
    phase: str = "phase_3",
    cii_year: int = 2026,
    annual_fuel_t: Optional[float] = None,
    annual_distance_nm: Optional[float] = None,
) -> Dict[str, object]:
    """一站式评估：获得/要求 EEDI、获得/要求 EEXI、CII 与评级。

    缺失的规则系数抛 ``RuleCoefficientMissing``（NOT_IMPLEMENTED），不返回臆造值。
    """
    cf_me = carbon_factor(fuel_me)
    cf_ae = carbon_factor(fuel_ae)
    n = len(mcr_me)
    sfc_seq = [sfc_me] * n
    cf_seq = [cf_me] * n

    eedi = attained_eedi(
        p_me=list(mcr_me),
        sfc_me=sfc_seq,
        cf_me=cf_seq,
        p_ae=auxiliary_power(sum(mcr_me)),
        sfc_ae=sfc_ae,
        cf_ae=cf_ae,
        capacity=capacity,
        v_ref=v_ref,
        f_j=f_j,
        f_i=f_i,
        f_c=f_c,
        f_l=f_l,
    )
    eexi = attained_eexi(
        mcr_me=list(mcr_me),
        sfc_me=sfc_seq,
        cf_me=cf_seq,
        sfc_ae=sfc_ae,
        cf_ae=cf_ae,
        capacity=capacity,
        v_ref=v_ref,
        f_j=f_j,
        f_i=f_i,
        f_c=f_c,
        f_l=f_l,
    )
    out: Dict[str, object] = {
        "ship_type": ship_type,
        "capacity": capacity,
        "attained_eedi": eedi,
        "eedi_reference": eedi_reference_line(ship_type, capacity),
        "eexi_attained": eexi,
        "eexi_required": required_eexi(ship_type, capacity),
        "cf_me": cf_me,
        "cf_ae": cf_ae,
    }
    try:
        req = required_eedi(ship_type, capacity, phase)
        out["eedi_required"] = req
        out["eedi_compliant"] = eedi <= req
        out["eedi_margin_pct"] = 100.0 * (req - eedi) / req
    except RuleCoefficientMissing as exc:
        out["eedi_required"] = None
        out["eedi_compliant"] = None
        out["eedi_note"] = str(exc)
    out["eexi_compliant"] = eexi <= out["eexi_required"]

    if annual_fuel_t is not None and annual_distance_nm is not None:
        cii = attained_cii(
            fuel_mass_t=[annual_fuel_t],
            fuel_types=[fuel_me],
            capacity=capacity,
            distance_nm=annual_distance_nm,
        )
        req_cii = required_cii(ship_type, capacity, cii_year)
        grade, bounds = cii_rating(cii, req_cii, ship_type)
        out.update(
            {
                "cii_attained": cii,
                "cii_required": req_cii,
                "cii_year": cii_year,
                "cii_rating": grade,
                "cii_bounds": bounds,
                "cii_ratio": cii / req_cii,
            }
        )
    return out


def annual_co2_tonnes(fuel_mass_t: float, fuel: str) -> float:
    """年 CO2 排放 [t] = 燃油质量 [t] * C_F。"""
    if fuel_mass_t < 0.0:
        raise ValueError("fuel_mass_t 须非负")
    return fuel_mass_t * carbon_factor(fuel)


# ---------------------------------------------------------------------------
# 7. 可选 FastAPI 路由（未安装 fastapi 时模块仍可纯函数使用）
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 环境相关
    from fastapi import APIRouter  # type: ignore
    from pydantic import BaseModel, Field  # type: ignore

    router = APIRouter()

    class _EngineIn(BaseModel):
        p_me_kw: List[float] = Field(..., description="各主机 MCR [kW]")
        sfc_me: float = Field(..., description="主机 SFC [g/kWh]")
        fuel_me: str = Field(..., description="主机燃油类型")
        sfc_ae: float = Field(..., description="辅机 SFC [g/kWh]")
        fuel_ae: str = Field("diesel_gas_oil", description="辅机燃油类型")
        p_ae_kw: Optional[float] = Field(None, description="辅机功率，缺省按 MEPC.308(73) §2.2.3 导出")

    class _EediIn(_EngineIn):
        ship_type: str
        capacity: float = Field(..., description="载重量/容量参数")
        v_ref: float = Field(..., description="参考航速 [kn]")
        f_j: float = 1.0
        f_i: float = 1.0
        f_c: float = 1.0
        f_l: float = 1.0
        phase: str = "phase_3"

    class _CiiIn(BaseModel):
        ship_type: str
        capacity: float
        annual_fuel_t: float
        annual_distance_nm: float
        fuel: str = "hfo"
        year: int = 2026

    @router.post("/eedi")
    def api_eedi(body: _EediIn) -> Dict[str, object]:
        cf_me = carbon_factor(body.fuel_me)
        cf_ae = carbon_factor(body.fuel_ae)
        p_ae = body.p_ae_kw if body.p_ae_kw is not None else auxiliary_power(sum(body.p_me_kw))
        attained = attained_eedi(
            p_me=body.p_me_kw,
            sfc_me=[body.sfc_me] * len(body.p_me_kw),
            cf_me=[cf_me] * len(body.p_me_kw),
            p_ae=p_ae,
            sfc_ae=body.sfc_ae,
            cf_ae=cf_ae,
            capacity=body.capacity,
            v_ref=body.v_ref,
            f_j=body.f_j,
            f_i=body.f_i,
            f_c=body.f_c,
            f_l=body.f_l,
        )
        result: Dict[str, object] = {
            "attained_eedi": attained,
            "eedi_reference": eedi_reference_line(body.ship_type, body.capacity),
            "p_ae_used": p_ae,
            "provenance": provenance_report(),
        }
        try:
            req = required_eedi(body.ship_type, body.capacity, body.phase)
            result.update(
                {
                    "eedi_required": req,
                    "compliant": attained <= req,
                    "margin_pct": 100.0 * (req - attained) / req,
                }
            )
        except RuleCoefficientMissing as exc:
            result.update({"eedi_required": None, "compliant": None, "note": str(exc)})
        return result

    @router.post("/eexi")
    def api_eexi(body: _EediIn) -> Dict[str, object]:
        cf_me = carbon_factor(body.fuel_me)
        cf_ae = carbon_factor(body.fuel_ae)
        p_ae = body.p_ae_kw if body.p_ae_kw is not None else auxiliary_power(sum(body.p_me_kw))
        attained = attained_eexi(
            mcr_me=body.p_me_kw,
            sfc_me=[body.sfc_me] * len(body.p_me_kw),
            cf_me=[cf_me] * len(body.p_me_kw),
            sfc_ae=body.sfc_ae,
            cf_ae=cf_ae,
            capacity=body.capacity,
            v_ref=body.v_ref,
            f_j=body.f_j,
            f_i=body.f_i,
            f_c=body.f_c,
            f_l=body.f_l,
            p_ae_override=p_ae if body.p_ae_kw is not None else None,
        )
        required = required_eexi(body.ship_type, body.capacity)
        return {
            "attained_eexi": attained,
            "eexi_required": required,
            "compliant": attained <= required,
            "margin_pct": 100.0 * (required - attained) / required,
            "provenance": provenance_report(),
        }

    @router.post("/cii")
    def api_cii(body: _CiiIn) -> Dict[str, object]:
        cii = attained_cii(
            fuel_mass_t=[body.annual_fuel_t],
            fuel_types=[body.fuel],
            capacity=body.capacity,
            distance_nm=body.annual_distance_nm,
        )
        required = required_cii(body.ship_type, body.capacity, body.year)
        grade, bounds = cii_rating(cii, required, body.ship_type)
        return {
            "attained_cii": cii,
            "required_cii": required,
            "rating": grade,
            "bounds": bounds,
            "ratio": cii / required,
            "provenance": provenance_report(),
        }
except Exception:  # pragma: no cover
    router = None  # type: ignore