"""EEDI_attained 计算链（75% MCR 参考点 + P_AE 规则）与合规判决。

规则口径（MARPOL Annex VI Reg.20-21 / MEPC.308(73)、MEPC.364(79)）
------------------------------------------------------------------
``EEDI = (f_j · P_ME · SFOC_ME · C_F_ME + P_AE · SFOC_AE · C_F_AE + ...) /
(f_i · f_c · Capacity · V_ref)``

* ``P_ME = 0.75 × MCR``（无轴带发电机/限功率修正时）；
* ``V_ref`` = 该功率下的**静水、深水、无风浪**航速 —— 本包用船-机-桨匹配
  反解（``matching.speed_for_brake_power``），不是手填值；
* ``P_AE``：``ΣP_ME ≥ 10 MW → 0.025 ΣP_ME + 250``，否则 ``0.05 ΣP_ME``
  （复用 ``services.green_navigation.auxiliary_power_default``）；
* ``Capacity``：油/散/气体 = DWT；集装箱 = 0.7 × DWT（``container_0_7_dwt``）。
  该口径本身是规则条款，故标 WITHHELD，见 ``capacity_for_ship_type``。

证据分层
--------
达到值 ``attained``：REAL（能量→燃油→CO₂ 链 + 匹配出的 V_ref，可逐项复算）。
要求值 ``required``：WITHHELD（参考线 a/c 与阶段削减率是转录系数，复用
``services.green_navigation`` 的同一张表，不在此处另造一套数字）。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_BACKEND))

from services import green_navigation as gn  # noqa: E402

CONTAINER_CAPACITY_FACTOR = 0.7
EEDI_REFERENCE_LOAD = 0.75


class EediError(ValueError):
    """EEDI 输入非法（零容量/零航速等）。"""


def capacity_for_ship_type(ship_type: str, dwt_t: float,
                           rule: Optional[str] = None) -> Dict[str, Any]:
    """EEDI/CII 分母 Capacity 的口径。集装箱按 0.7·DWT（规则条款，WITHHELD）。"""
    if dwt_t <= 0:
        raise EediError("DWT 必须为正")
    key = (ship_type or "").strip().lower()
    if rule == "container_0_7_dwt" or (rule is None and key == "container_ship"):
        return {
            "capacity_t": CONTAINER_CAPACITY_FACTOR * dwt_t,
            "basis": "0.7 × DWT (container ship)",
            "evidence_grade": gn.EVIDENCE_WITHHELD,
        }
    return {
        "capacity_t": float(dwt_t),
        "basis": "DWT",
        "evidence_grade": gn.EVIDENCE_WITHHELD,
    }


def eedi_reference_point(brake_power_kw: float, capacity_t: float,
                         v_ref_kn: float, sfoc_me_g_kwh: float, cf_me: float,
                         sfoc_ae_g_kwh: float, cf_ae: float,
                         aux_power_kw: Optional[float] = None,
                         f_i: float = 1.0, f_c: float = 1.0,
                         f_j: float = 1.0, p_eff_kw: float = 0.0,
                         feff: float = 1.0) -> Dict[str, Any]:
    """闭式 EEDI_attained（不含匹配；匹配结果由 :func:`evaluate_eedi` 注入）。

    负控性质（本函数显式保证，`tests` 里逐条断言）：

    * ``brake_power_kw = 0`` → ``P_AE = 0`` → ``attained = 0``；
    * ``v_ref_kn → ∞`` → ``attained → 0``；
    * ``attained`` 对 ``SFOC``、``C_F``、``P_ME`` 线性（变形检验用）。
    """
    if capacity_t <= 0:
        raise EediError("Capacity 必须为正")
    if v_ref_kn <= 0:
        raise EediError("参考航速必须为正")
    if brake_power_kw < 0:
        raise EediError("功率必须非负")
    p_ae = gn.auxiliary_power_default(brake_power_kw) if brake_power_kw > 0 else 0.0
    if aux_power_kw is not None:
        p_ae = float(aux_power_kw)
        if p_ae < 0:
            raise EediError("辅助功率必须非负")
    chain = gn.PowerChain(
        p_me_kw=brake_power_kw,
        sfoc_me=sfoc_me_g_kwh,
        cf_me=cf_me,
        p_ae_kw=p_ae,
        sfoc_ae=sfoc_ae_g_kwh,
        cf_ae=cf_ae,
        p_eff_kw=p_eff_kw,
        f_i=f_i,
        f_c=f_c,
        f_j=(f_j,),
        feff=feff,
    )
    attained = gn.attained_index(chain, capacity_t=capacity_t, v_ref_kn=v_ref_kn)
    terms = chain.co2_terms()
    return {
        "attained_g_per_t_nm": attained,
        "unit": "gCO2/(t·nm)",
        "p_me_kw": brake_power_kw,
        "p_ae_kw": p_ae,
        "sfoc_me_g_kwh": sfoc_me_g_kwh,
        "sfoc_ae_g_kwh": sfoc_ae_g_kwh,
        "cf_me": cf_me,
        "cf_ae": cf_ae,
        "v_ref_kn": v_ref_kn,
        "capacity_t": capacity_t,
        "co2_breakdown": terms,
        "attained_evidence_grade": gn.EVIDENCE_REAL,
    }


def evaluate_eedi(ship_type: str, attained_g_per_t_nm: float, capacity_t: float,
                  phase: str = "phase2") -> Dict[str, Any]:
    """把达到值与参考线要求值组合，给出 compliant 判决。"""
    if attained_g_per_t_nm < 0:
        raise EediError("attained 必须非负")
    req = gn.required_eedi(ship_type, capacity_t, phase=phase)
    required = float(req["required_value"])
    return {
        "attained_g_per_t_nm": attained_g_per_t_nm,
        "required_g_per_t_nm": required,
        "reference_line_g_per_t_nm": req["reference_value"],
        "phase": phase,
        "phase_reduction": req["phase_reduction"],
        "ratio_to_required": attained_g_per_t_nm / required,
        "compliant": attained_g_per_t_nm <= required,
        "attained_evidence_grade": gn.EVIDENCE_REAL,
        "required_evidence_grade": req["evidence_grade"],
        "coefficient_source": req["source"],
        "compliant_note": (
            "compliant 只在'转录参考线'意义下成立：attained 是 REAL，required 是 "
            "WITHHELD（离线未回读决议原文），故合规结论继承 WITHHELD，不得单独"
            "作为规范符合性声明。"),
    }