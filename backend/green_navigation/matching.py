"""船-机-桨匹配：自航点 ``R_T(V) = T = ρ n² D⁴ K_T(J)`` + 轴系/齿轮传递效率。

物理链
------
1. **阻力**：Holtrop-Mennen(1982/1984) 回归（复用本仓
   ``api.domain.resistance_holtrop``），ITTC-57 摩擦线 + ITTC-78 相关补贴，
   粗糙度 ``k_s`` 可逐年改变（年度退化通道）。
2. **推力需求**：``T_req = R_T / (1 - t)``（推力减额 t：螺旋桨推力要克服
   阻力+吸力）。
3. **自航点**：螺旋桨在伴流中工作，进速 ``V_a = V (1 - w)``，
   ``J = V_a / (n D)``，``T = ρ n² D⁴ K_T(J)``。把 ``n = V_a/(J D)`` 消元得

       ``T(J) = ρ V_a² D² K_T(J) / J²``

   它对 J 单调递减（``K_T`` 递减且除以 ``J²``），因此在 ``(J→0, J_0)``
   上有唯一解 —— 用二分求 ``T(J) = T_req``。相比"对 n 二分"的写法，这里
   不需要人为给转速区间上界，也不会在 ``K_T`` 外插区打转。
4. **效率链**：``η_O = J K_T /(2π K_Q)``、
   ``η_D = η_O η_R (1-t)/(1-w)``（准推进系数恒等式，本模块把残差一并输出）、
   ``P_D = 2π n Q / η_R``、``P_B = P_D /(η_shaft η_gear)``。

``K_T``/``K_Q`` 来自 Wageningen B-series 回归多项式
（``services.wageningen_b_series``，REAL 回归式，适用域
``J`` 在 [0, J_0]、P/D 0.5–1.4、Ae/Ao 0.3–1.05、Z 2–7）。

证据等级
--------
* 自航点求解与效率链：REAL（力平衡残差 ~1e-9 N，恒等式残差 ~1e-16）。
* B-series 多项式：REAL*（回归式对系列桨 ±2~3%，系列外为外插）。
* Holtrop 阻力：REAL*（原文自述常规单体 ±5–10%）。
* ``w``/``t``/``η_R``：WITHHELD（经验输入，本模块只做单调性与量级断言）。
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:  # pragma: no cover - 包外导入时的垫片
    sys.path.insert(0, str(_BACKEND))

from api.domain.resistance_holtrop import (  # noqa: E402
    DEFAULT_ROUGHNESS_KS,
    ResistanceInput,
    compute_resistance,
)
from services import wageningen_b_series as bseries  # noqa: E402

RHO_SEA = 1025.0
NU_SEA = 1.19e-6
G = 9.81
KNOT_TO_MS = 0.5144444444


class MatchingError(ValueError):
    """自航点无解或输入越界。"""


@dataclass(frozen=True)
class PropellerSpec:
    diameter_m: float
    pitch_ratio: float
    ae_ao: float
    blades: int
    wake_fraction: float
    thrust_deduction: float
    relative_rotative_efficiency: float = 1.0

    def __post_init__(self) -> None:
        if self.diameter_m <= 0:
            raise MatchingError("螺旋桨直径必须为正")
        if not 0.0 <= self.wake_fraction < 0.5:
            raise MatchingError(f"伴流分数越界: {self.wake_fraction}")
        if not 0.0 <= self.thrust_deduction < 0.5:
            raise MatchingError(f"推力减额越界: {self.thrust_deduction}")
        if not 0.5 < self.relative_rotative_efficiency <= 1.1:
            raise MatchingError(f"相对旋转效率越界: {self.relative_rotative_efficiency}")

    def kt(self, j: float) -> float:
        return bseries.kt(j, self.pitch_ratio, self.ae_ao, self.blades, strict=False)

    def kq(self, j: float) -> float:
        return bseries.kq(j, self.pitch_ratio, self.ae_ao, self.blades, strict=False)

    def zero_thrust_j(self) -> float:
        j0 = bseries.zero_thrust_advance_ratio(self.pitch_ratio, self.ae_ao, self.blades)
        if not math.isfinite(j0) or j0 <= 0.0:
            raise MatchingError("无法确定零推力进速比 J0")
        return j0


@dataclass(frozen=True)
class PowertrainSpec:
    eta_shaft: float = 0.99
    eta_gear: float = 1.0

    def __post_init__(self) -> None:
        for name, val in (("eta_shaft", self.eta_shaft), ("eta_gear", self.eta_gear)):
            if not 0.0 < val <= 1.0:
                raise MatchingError(f"{name} 必须在 (0, 1]")

    @property
    def eta_total(self) -> float:
        return self.eta_shaft * self.eta_gear


@dataclass(frozen=True)
class ShipGeometry:
    """主尺度 + 可选的船型系数（缺省由 Holtrop 内部经验式估值并写入 assumptions）。"""

    length_m: float
    beam_m: float
    draft_m: float
    cb: float
    displacement_t: Optional[float] = None
    cm: Optional[float] = None          # 中横剖面系数
    cwp: Optional[float] = None         # 水线面系数
    lcb_pct: float = 0.0                # 浮心纵位置（%L，+ 艏）
    i_e_deg: float = 25.0               # 进流半角
    a_bt: float = 0.0                   # 球鼻横剖面积 m²
    h_b: float = 0.0                    # 球鼻中心高 m
    a_tr: float = 0.0                   # 方尾浸没面积 m²
    published_wetted_surface_m2: Optional[float] = None


@dataclass
class MatchingPoint:
    """一个航速下的匹配结果（全部 SI 输入，输出带无量纲量便于复核）。"""

    speed_kn: float
    speed_ms: float
    resistance_n: float
    required_thrust_n: float
    ks_m: float
    advance_ratio: float
    revolutions_per_s: float
    revolutions_per_min: float
    kt: float
    kq: float
    open_water_efficiency: float
    quasi_propulsive_efficiency: float
    quasi_propulsive_identity_residual: float
    thrust_balance_residual_n: float
    effective_power_kw: float
    delivered_power_kw: float
    brake_power_kw: float
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "detail"}
        out["detail"] = self.detail
        return out


def resistance_at(case: ShipGeometry, speed_kn: float,
                  ks_m: float = DEFAULT_ROUGHNESS_KS,
                  lcb_pct: float = 0.0, i_e_deg: float = 25.0) -> Dict[str, Any]:
    """Holtrop 阻力（含粗糙度通道）。"""
    if speed_kn <= 0:
        raise MatchingError("航速必须为正")
    inp = ResistanceInput(
        length=case.length_m, beam=case.beam_m, draft=case.draft_m, cb=case.cb,
        speed_ms=speed_kn * KNOT_TO_MS, displacement_t=case.displacement_t,
        rho=RHO_SEA, nu=NU_SEA, ks=ks_m, lcb_pct=lcb_pct, i_e_deg=i_e_deg,
        cm=case.cm, cwp=case.cwp, a_bt=case.a_bt, h_b=case.h_b, a_tr=case.a_tr,
    )
    return compute_resistance(inp)


def _thrust_from_j(j: float, va: float, prop: PropellerSpec) -> float:
    """``T(J) = ρ V_a² D² K_T(J) / J²``（单调递减）。"""
    if j <= 0:
        return math.inf
    return RHO_SEA * va ** 2 * prop.diameter_m ** 2 * prop.kt(j) / j ** 2


def match_at_speed(case: ShipGeometry, prop: PropellerSpec, train: PowertrainSpec,
                   speed_kn: float, ks_m: float = DEFAULT_ROUGHNESS_KS,
                   tol: float = 1e-10) -> MatchingPoint:
    """求给定航速下的船-机-桨自航点。"""
    if speed_kn <= 0:
        raise MatchingError("航速必须为正")
    res = resistance_at(case, speed_kn, ks_m=ks_m)
    rt = float(res["rt"])
    v = speed_kn * KNOT_TO_MS
    va = v * (1.0 - prop.wake_fraction)
    t_req = rt / (1.0 - prop.thrust_deduction)
    j0 = prop.zero_thrust_j()

    lo, hi = 1e-4, j0 * (1.0 - 1e-9)
    f_lo = _thrust_from_j(lo, va, prop) - t_req
    f_hi = _thrust_from_j(hi, va, prop) - t_req
    if not (f_lo > 0.0 > f_hi):
        raise MatchingError(
            f"V={speed_kn:.2f} kn 无自航点：T(1e-4)-T_req={f_lo:.3e} N, "
            f"T(J0)-T_req={f_hi:.3e} N（螺旋桨推力上限不足或阻力异常）")
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if _thrust_from_j(mid, va, prop) > t_req:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    j = 0.5 * (lo + hi)
    n = va / (j * prop.diameter_m)
    kt = prop.kt(j)
    kq = prop.kq(j)
    thrust = kt * RHO_SEA * n ** 2 * prop.diameter_m ** 4
    torque = kq * RHO_SEA * n ** 2 * prop.diameter_m ** 5
    p_d = 2.0 * math.pi * n * torque / prop.relative_rotative_efficiency
    p_b = p_d / train.eta_total
    p_e = rt * v
    eta_o = j * kt / (2.0 * math.pi * kq) if kq > 0 else 0.0
    eta_d = p_e / p_d if p_d > 0 else 0.0
    eta_d_identity = (eta_o * prop.relative_rotative_efficiency
                      * (1.0 - prop.thrust_deduction) / (1.0 - prop.wake_fraction))
    return MatchingPoint(
        speed_kn=speed_kn,
        speed_ms=v,
        resistance_n=rt,
        required_thrust_n=t_req,
        ks_m=ks_m,
        advance_ratio=j,
        revolutions_per_s=n,
        revolutions_per_min=n * 60.0,
        kt=kt,
        kq=kq,
        open_water_efficiency=eta_o,
        quasi_propulsive_efficiency=eta_d,
        quasi_propulsive_identity_residual=eta_d - eta_d_identity,
        thrust_balance_residual_n=thrust - t_req,
        effective_power_kw=p_e / 1e3,
        delivered_power_kw=p_d / 1e3,
        brake_power_kw=p_b / 1e3,
        detail={
            "holtrop": {
                "froude_number": res["froude_number"],
                "reynolds_number": res["reynolds_number"],
                "wetted_surface_m2": res["wetted_surface"],
                "ct": res["ct"],
                "ca": res["ca"],
                "one_plus_k1": res["one_plus_k1"],
            },
            "eta_chain": {
                "eta_o": eta_o,
                "eta_r": prop.relative_rotative_efficiency,
                "eta_hull_efficiency": (1.0 - prop.thrust_deduction) / (1.0 - prop.wake_fraction),
                "eta_shaft": train.eta_shaft,
                "eta_gear": train.eta_gear,
                "eta_d_identity": eta_d_identity,
            },
        },
    )


def speed_for_brake_power(case: ShipGeometry, prop: PropellerSpec, train: PowertrainSpec,
                          brake_power_kw: float, ks_m: float = DEFAULT_ROUGHNESS_KS,
                          v_lo_kn: float = 2.0, v_hi_kn: Optional[float] = None,
                          tol_kn: float = 1e-6) -> Tuple[float, MatchingPoint]:
    """求 ``P_B(V) = brake_power_kw`` 的航速（P_B 对 V 单调递增 → 二分）。

    EEDI 的 ``V_ref`` 就定义在这个点上（75% MCR 静水航速）。
    """
    if brake_power_kw <= 0:
        raise MatchingError("目标功率必须为正")
    hi = v_hi_kn if v_hi_kn is not None else max(2.0 * case.length_m ** 0.5, 10.0)
    # 保证上界功率足够
    for _ in range(40):
        p_hi = match_at_speed(case, prop, train, hi, ks_m=ks_m).brake_power_kw
        if p_hi >= brake_power_kw:
            break
        hi *= 1.25
    else:  # pragma: no cover
        raise MatchingError("无法在合理航速范围内达到目标功率")
    lo = v_lo_kn
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        p_mid = match_at_speed(case, prop, train, mid, ks_m=ks_m).brake_power_kw
        if p_mid < brake_power_kw:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol_kn:
            break
    v = 0.5 * (lo + hi)
    return v, match_at_speed(case, prop, train, v, ks_m=ks_m)