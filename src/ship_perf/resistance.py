"""阻力：ITTC-57 摩擦线 + Holtrop(1984) 形状因子/兴波 + 水下回转体分支与域校验。

修复要点（对应 m1473 审计 D1 / A-CT）
------------------------------------
在线服务 ``api/resistance_advanced.calculate_holtrop_full`` 的剩余阻力
``Cr_base`` 是自造分段线性式（Fn<0.2/<0.3/<0.4/else），在**水下回转体**
（SUBOFF 类，Fn_L=0.765）上被照常代入得 Cr≈0.0256、Ct=0.02847；而水下
航行体无自由面兴波，自引基准 0.0044（=(1+k)Cf+Ca+附体），超差 +547%。
同时总阻力组合式中**缺 (1+k)** 形状因子。

本模块：
1. ``holtrop_domain`` 显式域校验：水下回转体/越域几何不得进入兴波回归；
2. 水下回转体走 ``submerged_body_resistance``：Ct=(1+k)Cf+Ca+ΣCd·A·Q/S；
3. 水面船走 ``holtrop_surface_resistance``（Holtrop-Mennen 1984 公开式）。

约定：``form_factor_1pk`` 一律返回 **(1+k)**；阻力单位 N；系数以湿面积 S 为参考。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .constants import G, NU_FRESH, NU_SEA, RHO_FRESH, RHO_SEA

# ---------------------------------------------------------------- 基础阻力线


def ittc57_cf(re: float) -> float:
    """ITTC-57 平板摩擦阻力系数 Cf = 0.075 / (log10 Re - 2)^2。"""
    if re <= 0:
        raise ValueError("Re 必须为正")
    lg = math.log10(re) - 2.0
    return 0.075 / (lg * lg)


def roughness_allowance(lwl: float) -> float:
    """Holtrop 粗糙度补贴 Ca = 0.006 (L+100)^-0.16 - 0.00205（L 为水线长 m）。"""
    return 0.006 * (lwl + 100.0) ** (-0.16) - 0.00205


# ---------------------------------------------------------------- Holtrop 域


class OutOfDomainError(ValueError):
    """输入超出方法适用域：必须显式拒绝，而不是静默返回数字。"""

    def __init__(self, method: str, reasons: List[str]):
        self.method = method
        self.reasons = reasons
        super().__init__(f"{method} 适用域校验失败: " + "; ".join(reasons))


@dataclass
class HullForm:
    """船型/回转体主尺度。回转体取 T=D/2、B=D、Cm=1。"""

    lwl: float
    lpp: float
    b: float
    t: float
    displacement: float  # 排水体积 m^3
    cb: float
    cm: float = 0.98
    cwp: Optional[float] = None
    lcb_pct: float = 0.0  # LCB（%Lpp，+为前）
    depth: Optional[float] = None
    bulb_area: float = 0.0
    bulb_height: float = 0.0
    transom_area: float = 0.0
    cstern: float = 10.0

    @property
    def cp(self) -> float:
        return self.cb / self.cm

    @property
    def cwp_eff(self) -> float:
        if self.cwp is not None:
            return self.cwp
        return min(1.0, (1.0 + 2.0 * self.cb) / 3.0)

    @property
    def is_axisymmetric(self) -> bool:
        """水下回转体判据：圆剖面(Cm≥0.995) + B/T≈2 + L/B≥6（潜艇/鱼雷外形）。"""
        bt = self.b / self.t
        return self.cm >= 0.995 and abs(bt - 2.0) <= 0.10 * bt and self.lwl / self.b >= 6.0


def holtrop_domain(h: HullForm, target: str = "surface") -> List[str]:
    """Holtrop-Mennen 兴波回归适用域校验，返回违规原因（空列表=可算）。"""
    reasons: List[str] = []
    if target == "submerged":
        # 水下分支不用 Holtrop 兴波回归，故不套用其几何域；只校验回转体假设
        if not h.is_axisymmetric:
            reasons.append("非轴对称回转体，不适用水下航行体阻力公式")
        return reasons
    lb = h.lwl / h.b
    bt = h.b / h.t
    if not (0.50 <= h.cb <= 0.90):
        reasons.append(f"Cb={h.cb:.3f} 超出 Holtrop 域 [0.50, 0.90]")
    if not (3.9 <= lb <= 14.0):
        reasons.append(f"L/B={lb:.2f} 超出 Holtrop 域 [3.9, 14.0]")
    if not (2.10 <= bt <= 4.00):
        reasons.append(f"B/T={bt:.2f} 超出 Holtrop 域 [2.10, 4.00]")
    if not (0.55 <= h.cp <= 0.85):
        reasons.append(f"Cp={h.cp:.3f} 超出 Holtrop 域 [0.55, 0.85]")
    if h.is_axisymmetric:
        reasons.append("圆剖面水下回转体（Cm≈1, B/T≈2）：无自由面兴波，禁止进入兴波回归")
    return reasons


# ---------------------------------------------------------------- 湿表面积


def wetted_surface(h: HullForm) -> float:
    """湿表面积 (m^2)。回转体 S = 0.86·π·D·L（SUBOFF 光体折算）；水面船 Holtrop 回归。"""
    if h.is_axisymmetric:
        return 0.86 * math.pi * h.b * h.lwl
    s = h.lwl * (2.0 * h.t + h.b) * math.sqrt(h.cm) * (
        0.453
        + 0.4425 * h.cb
        - 0.2862 * h.cm
        - 0.003467 * (h.b / h.t)
        + 0.3696 * h.cwp_eff
    )
    if h.bulb_area > 0:
        s += 2.38 * h.bulb_area / max(h.cb, 0.1)
    return s


def form_factor_1pk(h: HullForm) -> float:
    """(1+k) 形状因子。

    水面船：Holtrop 回归
        (1+k)=0.93+0.487118·c14·(B/L)^1.06806·(T/L)^0.46106·(L/LR)^0.121563
                     ·(L³/∇)^0.36486·(1-Cp)^(-0.604247)
    回转体：L/D≈8.6 流线型体 CD(迎流面积)≈0.10 ⇒ (1+k)≈1.20
            （Hoerner《Fluid-Dynamic Drag》流线型回转体，量级校核见 docs/m1490）
    """
    if h.is_axisymmetric:
        return 1.20
    c14 = 1.0 + 0.011 * h.cstern
    lr = h.lpp * (1.0 - h.cp + 0.06 * h.cp * h.lcb_pct / (4.0 * h.cp - 1.0))
    if lr <= 0:
        lr = h.lpp
    return (
        0.93
        + 0.487118
        * c14
        * (h.b / h.lwl) ** 1.06806
        * (h.t / h.lwl) ** 0.46106
        * (h.lwl / lr) ** 0.121563
        * (h.lwl ** 3 / h.displacement) ** 0.36486
        * (1.0 - h.cp) ** (-0.604247)
    )


# ---------------------------------------------------------------- 附体


# SUBOFF AFF-8 附体（围壳 + 4 鳍 + 4 尾舵）折算面积 ΣCd·A·Q ≈ 4.5e-3 m^2
SUBOFF_APPENDAGES: List[Dict[str, float]] = [
    {"name": "sail", "area": 0.00400, "cd": 0.55, "interference": 1.25},
    {"name": "fins(4)", "area": 0.00090, "cd": 0.50, "interference": 1.20},
    {"name": "stern_planes(4)", "area": 0.00060, "cd": 0.50, "interference": 1.20},
]


def appendage_drag_area(appendages: Optional[List[Dict[str, float]]]) -> float:
    """Σ Cd·A·Q（m^2），用于折算附体阻力系数。"""
    if not appendages:
        return 0.0
    return sum(a["area"] * a["cd"] * a.get("interference", 1.0) for a in appendages)


# ---------------------------------------------------------------- 结果容器


@dataclass
class ResistancePoint:
    speed_ms: float
    re: float
    fn: float
    cf: float
    ct: float
    rt: float  # N
    rf: float  # N
    rr: float  # N（非摩擦部分）
    surface: float
    method: str
    form_factor_1pk: float = 0.0
    warnings: List[str] = field(default_factory=list)

    @property
    def ct_over_cf(self) -> float:
        return self.ct / self.cf


# ---------------------------------------------------------------- 水下回转体


def submerged_body_resistance(
    h: HullForm,
    speed_ms: float,
    rho: float = RHO_FRESH,
    nu: float = NU_FRESH,
    appendages: Optional[List[Dict[str, float]]] = None,
    roughness: bool = True,
) -> ResistancePoint:
    """水下回转体：Ct=(1+k)Cf+Ca+ΣCd·A·Q/S，无兴波项（``appendages=None``=光体）。"""
    why = holtrop_domain(h, target="submerged")
    if why:
        raise OutOfDomainError("submerged_body_resistance", why)
    s = wetted_surface(h)
    re = speed_ms * h.lwl / nu
    cf = ittc57_cf(re)
    ff = form_factor_1pk(h)
    ca = roughness_allowance(h.lwl) if roughness else 0.0
    c_app = appendage_drag_area(appendages) / s
    ct = ff * cf + ca + c_app
    q = 0.5 * rho * speed_ms ** 2
    return ResistancePoint(
        speed_ms=speed_ms,
        re=re,
        fn=speed_ms / math.sqrt(G * h.lwl),
        cf=cf,
        ct=ct,
        rt=q * s * ct,
        rf=q * s * cf,
        rr=q * s * (ff * cf + ca + c_app - cf),
        surface=s,
        method="submerged-body: (1+k)Cf + Ca + CdA/S",
        form_factor_1pk=ff,
    )


# ---------------------------------------------------------------- 水面船 Holtrop


def _c7(h: HullForm) -> float:
    bl = h.b / h.lwl
    if bl < 0.11:
        return 0.229577 * bl ** (1.0 / 3.0)
    if bl <= 0.25:
        return bl
    return 0.5 - 0.0625 * h.lwl / h.b


def _c16(cp: float) -> float:
    if cp < 0.8:
        return 8.07981 * cp - 13.8673 * cp ** 2 + 6.984388 * cp ** 3
    return 1.73014 - 0.7067 * cp


def _c15(h: HullForm) -> float:
    ratio = h.lpp / h.displacement ** (1.0 / 3.0)
    return -1.69385 + (ratio - 8.0) / 2.36 if ratio < 12.0 else -1.69385


def _lr(h: HullForm) -> float:
    lr = h.lpp * (1.0 - h.cp + 0.06 * h.cp * h.lcb_pct / (4.0 * h.cp - 1.0))
    return lr if lr > 0 else h.lpp


def _half_entrance_angle(h: HullForm) -> float:
    """iE 回归（deg）。"""
    lv = h.displacement ** (1.0 / 3.0)
    expo = (
        (h.lwl / h.b) ** 0.80856
        * (1.0 - h.cwp_eff) ** 0.30484
        * (1.0 - h.cp - 0.0225 * h.lcb_pct) ** 0.6367
        * (_lr(h) / h.b) ** 0.34574
        * (100.0 * lv ** 3 / h.lwl ** 3) ** 0.16302
    )
    return 1.0 + 89.0 * math.exp(-expo)


def holtrop_wave_resistance(h: HullForm, fn: float, rho: float = RHO_SEA) -> float:
    """Rw = c1·c2·c5·∇·ρ·g·exp(m1·Fn^d + m2·cos(λ·Fn^-2))（不含球鼻/艉封板）。"""
    cp = h.cp
    lb = h.lwl / h.b
    c7 = _c7(h)
    ie = _half_entrance_angle(h)
    c1 = 2223105.0 * c7 ** 3.78613 * (h.t / h.b) ** 1.07961 * (90.0 - ie) ** (-1.37565)
    if h.bulb_area > 0.0 and h.bulb_height > 0.0:
        c3 = 0.56 * h.bulb_area ** 1.5 / (
            h.b * h.t * (0.31 * math.sqrt(h.bulb_area) + h.t - h.bulb_height)
        )
        c2 = math.exp(-1.89 * math.sqrt(c3)) if c3 > 0 else 1.0
    else:
        c2 = 1.0
    c5 = 1.0 - 0.8 * h.transom_area / (h.b * h.t * h.cm)
    lam = 1.446 * cp - 0.03 * lb if lb < 12.0 else 1.446 * cp - 0.36
    m1 = 0.0140407 * h.lwl / h.t - 1.75254 * h.displacement ** (1 / 3) / h.lwl - 4.79323 * h.b / h.lwl - _c16(cp)
    m2 = _c15(h) * cp ** 2 * math.exp(-0.1 * fn ** (-2))
    d = -0.9
    rw = c1 * c2 * c5 * h.displacement * rho * G * math.exp(m1 * fn ** d + m2 * math.cos(lam * fn ** (-2)))
    return max(0.0, rw)


def holtrop_surface_resistance(
    h: HullForm,
    speed_ms: float,
    rho: float = RHO_SEA,
    nu: float = NU_SEA,
    appendage_area: float = 0.0,
    appendage_factor: float = 1.5,
) -> ResistancePoint:
    """水面船 Holtrop-Mennen 1984 总阻力 (N)：含 (1+k)、Ca、Rw、Rb、Rtr、Rapp。"""
    why = holtrop_domain(h, target="surface")
    if why:
        raise OutOfDomainError("holtrop_surface_resistance", why)
    s = wetted_surface(h)
    re = speed_ms * h.lwl / nu
    cf = ittc57_cf(re)
    ff = form_factor_1pk(h)
    ca = roughness_allowance(h.lwl)
    fn = speed_ms / math.sqrt(G * h.lwl)
    q = 0.5 * rho * speed_ms ** 2
    rf = q * s * cf
    r_form = q * s * (ff - 1.0) * cf
    r_rough = q * s * ca
    rw = holtrop_wave_resistance(h, fn, rho)

    tf = h.t
    rb = 0.0
    if h.bulb_area > 0.0 and h.bulb_height > 0.0:
        pb = 0.56 * math.sqrt(h.bulb_area) / (tf - 1.5 * h.bulb_height)
        fni = speed_ms / math.sqrt(
            G * (tf - h.bulb_height - 0.25 * math.sqrt(h.bulb_area)) + 0.15 * speed_ms ** 2
        )
        rb = 0.11 * math.exp(-3.0 * pb ** (-2)) * fni ** 3 * h.bulb_area ** 1.5 * rho * G / (1.0 + fni ** 2)
    rtr = 0.0
    if h.transom_area > 0.0:
        fnt = speed_ms / math.sqrt(G * h.transom_area / h.b)
        c6 = 0.2 * (1.0 - 0.2 * fnt) if fnt < 5.0 else 0.0
        rtr = 0.5 * rho * speed_ms ** 2 * h.transom_area * c6
    rapp = q * appendage_area * appendage_factor * cf
    rt = rf + r_form + r_rough + rw + rb + rtr + rapp
    warns: List[str] = []
    if fn > 0.45:
        warns.append(f"Fn={fn:.3f}>0.45：兴波回归外推")
    if h.lwl >= 150.0:
        warns.append("L>=150 m：未计空气阻力 Raa")
    return ResistancePoint(
        speed_ms=speed_ms,
        re=re,
        fn=fn,
        cf=cf,
        ct=rt / (q * s),
        rt=rt,
        rf=rf,
        rr=rw + rb + rtr + r_form + r_rough + rapp,
        surface=s,
        method="Holtrop-Mennen 1984",
        form_factor_1pk=ff,
        warnings=warns,
    )


def predict_resistance(
    h: HullForm,
    speed_ms: float,
    submerged: Optional[bool] = None,
    appendages: Optional[List[Dict[str, float]]] = None,
    **kw,
) -> ResistancePoint:
    """统一入口：自动判别水下回转体/水面船，越域时抛 OutOfDomainError。"""
    is_sub = h.is_axisymmetric if submerged is None else submerged
    if is_sub:
        return submerged_body_resistance(h, speed_ms, appendages=appendages, **kw)
    return holtrop_surface_resistance(h, speed_ms, **kw)