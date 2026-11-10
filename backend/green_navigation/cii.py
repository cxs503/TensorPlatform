"""CII（碳强度指标）：达到值 / 要求值 / A–E 评级 / 年度粗糙度退化轨迹。

补齐的缺口
----------
``services/green_navigation.cii_rating_status()`` 把 A–E 评级显式标为
NOT_SUPPORTED（"未取得 G4 原文"）。本模块提供**带证据标签**的实现：

* 判级规则（比值 vs dd1..dd4 逐级比较、边界闭/开区间、单调性）—— REAL；
* dd 向量常数 —— WITHHELD_PROVENANCE（``data/cii_rating_boundaries.json`` 转录，
  CONF_MED）；
* 容量相关型边界（气体运输船 / LNG / 客船）—— NOT_SUPPORTED（显式抛错，
  不给假能力）。

年度退化轨迹
------------
``dCII/dt`` 不是外生常数，而是由**船体粗糙度增长**通过物理链推出来的：

    k_s(t) = k_s0 + r·t
      → Holtrop 的 ITTC-78 相关补贴 C_A(k_s/L) 上升
      → R_T 上升（同航速）
      → P_B 上升（自航点重解）
      → 燃油/CO₂ 上升
      → attained CII 上升

``C_A(k_s, L) = (105·(k_s/L)^(1/3) − 0.64)·10⁻³`` 是可复算解析式，因此这条
轨迹的**通道**是 REAL；``r``（µm/年）与外部统计带（Clarksons/行业 0.5–1.5 %/年）
用于量级对照，标 WITHHELD。

``r`` 的默认值取 **15 µm/年**（= 年均船体清洗维护下的净增长率），``30 µm/年``
对应"5 年不清洁"的最坏情形 —— 两者都作为曝光场景列出（``sensitivity_scenarios``），
不允许只报一个数字就宣称"与统计一致"。本链只含船体粗糙度通道，不含螺旋桨污损、主机退化、
航速/配载策略变化，故落在统计带的下半区是**预期**行为（不是拟合结果）。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import eedi as eedi_mod
from . import engine as engine_mod
from . import matching as matching_mod

from services import green_navigation as gn  # noqa: E402  (matching 已垫 sys.path)

DATA_DIR = Path(__file__).resolve().parent / "data"
BOUNDARY_FILE = DATA_DIR / "cii_rating_boundaries.json"
ANCHOR_FILE = DATA_DIR / "external_anchors.json"

RATING_ORDER = ("A", "B", "C", "D", "E")


class CiiError(ValueError):
    """CII 输入非法或该船型评级不支持。"""


@dataclass(frozen=True)
class RatingBoundaries:
    ship_type: str
    dd: tuple
    provenance: Dict[str, Any] = field(default_factory=dict)

    def rating(self, ratio: float) -> str:
        """比值 → 等级（A 最优）。边界为闭区间上界：``ratio <= dd_k`` 判 k 级。"""
        if not math.isfinite(ratio) or ratio <= 0:
            raise CiiError(f"比值必须为有限正数，得到 {ratio}")
        for grade, bound in zip(RATING_ORDER, self.dd):
            if ratio <= bound:
                return grade
        return "E"

    def ratio_at_boundary(self, grade: str) -> float:
        """各等级的上界比值（供反解一致性测试）。"""
        idx = RATING_ORDER.index(grade)
        return float(self.dd[idx])

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ship_type": self.ship_type,
            "dd": list(self.dd),
            "rule": "A: r<=dd1, B: <=dd2, C: <=dd3, D: <=dd4, else E",
            "provenance": self.provenance,
            "evidence_grade": gn.EVIDENCE_WITHHELD,
        }


def load_boundaries(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else BOUNDARY_FILE
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def boundaries_for(ship_type: str, spec: Optional[Dict[str, Any]] = None) -> RatingBoundaries:
    """取某船型的 dd 向量；容量相关型或未登记船型一律显式报错。"""
    spec = spec or load_boundaries()
    key = (ship_type or "").strip().lower()
    if key in spec.get("capacity_dependent_types_not_supported", []):
        raise CiiError(
            f"{key}: G4 的 dd 向量随 Capacity 变化（容量相关型边界），本版不实现 → "
            "NOT_SUPPORTED，不做近似替代")
    dd = spec["dd_vectors"].get(key)
    if dd is None:
        raise CiiError(f"未登记的船型 {ship_type!r}；可用: {sorted(spec['dd_vectors'])}")
    if len(dd) != 4 or any(b <= 0 for b in dd) or list(dd) != sorted(dd):
        raise CiiError(f"{key} 的 dd 向量非严格递增: {dd}")
    return RatingBoundaries(ship_type=key, dd=tuple(float(b) for b in dd),
                            provenance=spec.get("provenance", {}))


def load_anchors(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else ANCHOR_FILE
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 年度 CII（AER 口径）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OperatingProfile:
    """营运剖面（只需年度航行天数：AER 里小时数在分子分母同时出现）。"""

    sea_days_per_year: float = 220.0
    aux_sfoc_g_kwh: float = 175.0

    def __post_init__(self) -> None:
        if not 0 < self.sea_days_per_year <= 366:
            raise CiiError("年度航行天数越界")
        if self.aux_sfoc_g_kwh <= 0:
            raise CiiError("辅机油耗必须为正")

    @property
    def sea_hours_per_year(self) -> float:
        return self.sea_days_per_year * 24.0


@dataclass
class AnnualCiiResult:
    year: int
    ks_m: float
    speed_kn: float
    brake_power_kw: float
    main_load_ratio: float
    sfoc_main_g_kwh: float
    fuel_main_t: float
    fuel_aux_t: float
    co2_t: float
    distance_nm: float
    attained_g_per_t_nm: float
    required_g_per_t_nm: float
    reference_g_per_t_nm: float
    z_factor: float
    ratio_to_required: float
    rating: str
    power_delta_pct_vs_year1: float
    attained_delta_pct_vs_year1: float

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def annual_cii(
    geometry: matching_mod.ShipGeometry,
    prop: matching_mod.PropellerSpec,
    train: matching_mod.PowertrainSpec,
    sfoc_map: engine_mod.SfocMap,
    ship_type: str,
    dwt_t: float,
    capacity_t: float,
    service_speed_kn: float,
    mcr_kw: float,
    year: int,
    ks_m: float,
    profile: OperatingProfile = OperatingProfile(),
    cf_main: float = 3.114,
    cf_aux: float = 3.206,
    boundaries: Optional[RatingBoundaries] = None,
) -> AnnualCiiResult:
    """单年度 attained / required / rating（服务航速固定，含粗糙度 k_s）。"""
    if year < 2023:
        raise CiiError("CII 只对 2023 及以后生效")
    if mcr_kw <= 0:
        raise CiiError("MCR 必须为正")
    point = matching_mod.match_at_speed(geometry, prop, train, service_speed_kn, ks_m=ks_m)
    load = point.brake_power_kw / mcr_kw
    if not 0.05 <= load <= 1.05:
        raise CiiError(f"服务点负荷 {load:.3f} 明显越界（主机与船型不匹配）")
    sfoc_main = sfoc_map.sfoc(load)
    hours = profile.sea_hours_per_year
    fuel_main_g = point.brake_power_kw * sfoc_main * hours
    p_ae = gn.auxiliary_power_default(mcr_kw)
    fuel_aux_g = p_ae * profile.aux_sfoc_g_kwh * hours
    co2_g = fuel_main_g * cf_main + fuel_aux_g * cf_aux
    distance_nm = service_speed_kn * hours
    attained = gn.attained_cii(co2_g, capacity_t, distance_nm)
    req = gn.required_cii(ship_type, capacity_t, year)
    required = float(req["required_value"])
    bnd = boundaries or boundaries_for(ship_type)
    ratio = attained / required
    return AnnualCiiResult(
        year=year,
        ks_m=ks_m,
        speed_kn=service_speed_kn,
        brake_power_kw=point.brake_power_kw,
        main_load_ratio=load,
        sfoc_main_g_kwh=sfoc_main,
        fuel_main_t=fuel_main_g / 1e6,
        fuel_aux_t=fuel_aux_g / 1e6,
        co2_t=co2_g / 1e6,
        distance_nm=distance_nm,
        attained_g_per_t_nm=attained,
        required_g_per_t_nm=required,
        reference_g_per_t_nm=float(req["reference_value_2019"]),
        z_factor=float(req["z_factor"]),
        ratio_to_required=ratio,
        rating=bnd.rating(ratio),
        power_delta_pct_vs_year1=0.0,
        attained_delta_pct_vs_year1=0.0,
    )


def degradation_trajectory(
    geometry: matching_mod.ShipGeometry,
    prop: matching_mod.PropellerSpec,
    train: matching_mod.PowertrainSpec,
    sfoc_map: engine_mod.SfocMap,
    ship_type: str,
    dwt_t: float,
    capacity_t: float,
    service_speed_kn: float,
    mcr_kw: float,
    years: int = 5,
    first_year: int = 2023,
    ks_initial_m: float = 150e-6,
    roughness_growth_m_per_year: float = 15e-6,
    profile: OperatingProfile = OperatingProfile(),
    boundaries: Optional[RatingBoundaries] = None,
) -> Dict[str, Any]:
    """N 年退化轨迹：粗糙度增长 → 功率/CO₂ 增长 → attained CII 与评级漂移。"""
    if years < 2:
        raise CiiError("轨迹至少需要 2 年")
    if roughness_growth_m_per_year < 0:
        raise CiiError("粗糙度增长率必须非负")
    if ks_initial_m <= 0:
        raise CiiError("初始粗糙度必须为正")
    rows: List[AnnualCiiResult] = []
    for i in range(years):
        row = annual_cii(
            geometry, prop, train, sfoc_map, ship_type, dwt_t, capacity_t,
            service_speed_kn, mcr_kw, first_year + i,
            ks_m=ks_initial_m + roughness_growth_m_per_year * i,
            profile=profile, boundaries=boundaries,
        )
        rows.append(row)
    base = rows[0]
    for row in rows:
        row.power_delta_pct_vs_year1 = (row.brake_power_kw / base.brake_power_kw - 1.0) * 100.0
        row.attained_delta_pct_vs_year1 = (
            row.attained_g_per_t_nm / base.attained_g_per_t_nm - 1.0) * 100.0

    # 退化率的两种口径：只含物理通道（固定年份要求值）与含要求值年度收紧
    physics_rates = []
    for prev, cur in zip(rows, rows[1:]):
        physics_rates.append(
            (cur.attained_g_per_t_nm / prev.attained_g_per_t_nm - 1.0) * 100.0)
    mean_physics = sum(physics_rates) / len(physics_rates)
    required_shrink = [
        (rows[i].required_g_per_t_nm / rows[i - 1].required_g_per_t_nm - 1.0) * 100.0
        for i in range(1, len(rows))
    ]
    anchors = load_anchors()["anchors"]["annual_degradation_clarksons"]
    band = anchors["band_pct_per_year"]
    return {
        "years": [r.as_dict() for r in rows],
        "roughness_growth_m_per_year": roughness_growth_m_per_year,
        "ks_start_m": ks_initial_m,
        "ks_end_m": ks_initial_m + roughness_growth_m_per_year * (years - 1),
        "degradation_pct_per_year": mean_physics,
        "degradation_yearly_pct": physics_rates,
        "required_tightening_pct_per_year": required_shrink,
        "rating_sequence": [r.rating for r in rows],
        "rating_drift": rows[0].rating != rows[-1].rating,
        "external_band_pct_per_year": band,
        "within_external_band": band[0] <= mean_physics <= band[1],
        "external_anchor": anchors,
        "channel_note": (
            "本轨迹只含船体粗糙度 k_s 通道（ITTC-78 C_A），不含螺旋桨污损/主机退化/"
            "航速与配载策略，故落在统计带下半区属预期。"),
        "evidence": {
            "degradation_channel": gn.EVIDENCE_REAL,
            "roughness_growth_rate": gn.EVIDENCE_WITHHELD,
            "external_band": gn.EVIDENCE_WITHHELD,
        },
    }


def rating_from_ratio(ratio: float, ship_type: str) -> Dict[str, Any]:
    """便捷函数：比值 → 等级 + 边界的完整剖面（供 API/报告用）。"""
    bnd = boundaries_for(ship_type)
    grade = bnd.rating(ratio)
    return {
        "ship_type": ship_type,
        "ratio_to_required": ratio,
        "rating": grade,
        "grade_index": RATING_ORDER.index(grade),
        "boundaries": bnd.as_dict(),
        "evidence_grade": gn.EVIDENCE_WITHHELD,
    }