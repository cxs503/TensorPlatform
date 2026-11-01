"""
Holtrop–Mennen (1982/1984) 静水阻力参考实现 —— 可复核、带假设声明的替代引擎。

背景（为什么有这个模块）
------------------------------------------------------------------
平台 `POST /calculations/resistance` 原实现把总阻力拆成
    R_T = R_F + R_R ,  R_R = 0.5 ρ S Cr V²,  Cr = 0.002 (1 + 2 Cb) Fn^2.5
（早期工作树版本）或部署版本 `Cr = 0.005 C4 · C1 · C2 · C3`（`C4 = 0.5 + 2.5 Fn` 等）。
两条 Cr 都不是 Holtrop 公式，而是手调量级的 fudge：部署版本在 KVLCC2 设计点给出
Cr = 4.744e-3（真值量级 ~0.3–1.5e-3），导致 R_T 约 2–3 倍偏高、PE = 42.5 MW
超过该船型装机功率上限（见 docs/reports/reference_validation_m1545.md）。

本模块按 Holtrop & Mennen (1982)、Holtrop (1984) 的公开公式集实现：
    R_T = R_F (1 + k1) + R_W + R_B + R_TR + R_A (+ R_APP)
其中 R_F 用 ITTC-1957 摩擦线，(1+k1) 用 Holtrop 形状因子回归，R_W 用 Holtrop 兴波
阻力回归（c1 c2 c5 ∇ρg exp(m1 Fn^d + m2 cos(λ Fn^-2))），R_A 用 ITTC-1978 相关补贴。
球鼻艏 R_B、方尾 R_TR、附体 R_APP 在缺参数时置 0（并显式记录 assumption）。

误差分级与适用范围
------------------------------------------------------------------
* ITTC-1957 摩擦线、R_A、Froude/Reynolds 定义：解析式，本模块与解析值一致（REAL）。
* (1+k1)、R_W：Holtrop 回归式本身对常规单体排水型船（Fn<0.4，0.55<Cb<0.85）
  的精度约 ±5–10%（原文自述），但需要 i_E / lcb / C_stern / A_BT 等船型参数；
  缺省时本模块用经验估值并把这些估值写进 `assumptions`，同时提供敏感性带
  （见 `sensitivity_band`），因此单点绝对值应视为「独立方法交叉参考」，不是实验基准。
* 对非常规船型（潜艇/双体/高速滑行艇）不适用——此时应判 NOT_SUPPORTED，而非硬算。

引用
------------------------------------------------------------------
* Holtrop, J. & Mennen, G.G.J. (1982). "An approximate power prediction method".
  International Shipbuilding Progress 29(335), 166–170.
* Holtrop, J. (1984). "A statistical re-analysis of resistance and propulsion data".
  International Shipbuilding Progress 31(363), 272–276.
* ITTC (1957) Recommended Procedures 7.5-02-02-01 (model-ship correlation line).
* ITTC (1978) Performance Prediction Method (correlation allowance C_A).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

G = 9.81
#: ITTC-1978 相关补贴用标准粗糙度（新船，m）
DEFAULT_ROUGHNESS_KS = 150e-6
RHO_FRESH = 998.0
RHO_SEA = 1025.0
NU_FRESH = 1.004e-6
NU_SEA = 1.19e-6


# ---------------------------------------------------------------------------
# 基础解析式（可对解析值逐位复核）
# ---------------------------------------------------------------------------

def ittc57_cf(re: float) -> float:
    """ITTC-1957 模型-实船相关线：Cf = 0.075 / (log10 Re - 2)^2。"""
    if re <= 0:
        raise ValueError("Re must be positive")
    denom = math.log10(re) - 2.0
    if denom <= 0:
        raise ValueError("Re too small for ITTC-1957 line")
    return 0.075 / denom ** 2


def correlation_allowance_ca(length_m: float, ks_m: float = DEFAULT_ROUGHNESS_KS) -> float:
    """ITTC-1978 相关补贴 C_A = (105 (k_s/L)^(1/3) - 0.64) x 1e-3。"""
    if length_m <= 0 or ks_m <= 0:
        raise ValueError("L and ks must be positive")
    return (105.0 * (ks_m / length_m) ** (1.0 / 3.0) - 0.64) * 1e-3


def froude_number(v_ms: float, length_m: float) -> float:
    return v_ms / math.sqrt(G * length_m)


def reynolds_number(v_ms: float, length_m: float, nu: float) -> float:
    return v_ms * length_m / nu


# ---------------------------------------------------------------------------
# Holtrop 几何量
# ---------------------------------------------------------------------------

def wetted_surface_holtrop(L: float, B: float, T: float, Cb: float,
                           Cm: float, Cwp: float, A_bt: float = 0.0) -> float:
    """Holtrop (1984) 湿表面积回归式（含球鼻艏附加项）。

    S = L(2T+B)sqrt(Cm)(0.453 + 0.4425Cb - 0.2862Cm - 0.003467 B/T + 0.3696Cwp)
        + 2.38 A_BT / Cb
    """
    base = L * (2.0 * T + B) * math.sqrt(Cm) * (
        0.453 + 0.4425 * Cb - 0.2862 * Cm - 0.003467 * (B / T) + 0.3696 * Cwp
    )
    return base + 2.38 * A_bt / Cb


def form_factor_1k1(L: float, B: float, T: float, Cp: float, lcb_pct: float,
                    vol: float, c_stern: float = 0.0) -> float:
    """Holtrop 形状因子 (1 + k1)。

    (1+k1) = 0.93 + 0.487118 c14 (B/L)^1.06806 (T/L)^0.46106
             (L/L_R)^0.121563 (L^3/vol)^0.36486 (1 - Cp)^-0.604247
    c14 = 1 + 0.011 C_stern ;  L_R = L (1 - Cp + 0.06 Cp lcb / (4 Cp - 1))
    """
    c14 = 1.0 + 0.011 * c_stern
    lr = L * (1.0 - Cp + 0.06 * Cp * lcb_pct / (4.0 * Cp - 1.0))
    if lr <= 0 or vol <= 0:
        raise ValueError("invalid L_R / displacement volume")
    return (0.93 + 0.487118 * c14
            * (B / L) ** 1.06806
            * (T / L) ** 0.46106
            * (L / lr) ** 0.121563
            * (L ** 3 / vol) ** 0.36486
            * (1.0 - Cp) ** -0.604247)


def wave_resistance_hull(L: float, B: float, T: float, Tf: float, Cp: float,
                         vol: float, Fn: float, i_e_deg: float,
                         A_bt: float = 0.0, h_b: float = 0.0,
                         A_tr: float = 0.0, Cm: float = 1.0) -> Dict[str, float]:
    """Holtrop 兴波阻力 R_W = c1 c2 c5 ∇ρg exp(m1 Fn^d + m2 cos(λ Fn^-2))。"""
    b_over_l = B / L
    if b_over_l < 0.11:
        c7 = 0.229577 * b_over_l ** (1.0 / 3.0)
    elif b_over_l <= 0.25:
        c7 = b_over_l
    else:
        c7 = 0.5 - 0.0625 * (L / B)

    c1 = (2223105.0 * c7 ** 3.78613 * (T / B) ** 1.07961
          * (90.0 - i_e_deg) ** -1.37565)

    if A_bt > 0.0:
        c3 = 0.56 * A_bt ** 1.5 / (B * T * (0.31 * math.sqrt(A_bt) + Tf - h_b))
        c2 = math.exp(-1.89 * math.sqrt(c3))
    else:
        c3 = 0.0
        c2 = 1.0

    c5 = 1.0 - 0.8 * A_tr / (B * T * Cm) if A_tr > 0.0 else 1.0

    if Cp < 0.8:
        c16 = 8.07981 * Cp - 13.8673 * Cp ** 2 + 6.984388 * Cp ** 3
    else:
        c16 = 1.73014 - 0.7067 * Cp

    m1 = 0.0140407 * L / T - 1.75254 * vol ** (1.0 / 3.0) / L - 4.79323 * B / L - c16
    d = -0.9

    vol_ratio = L ** 3 / vol
    if vol_ratio < 512.0:
        c15 = -1.69385
    elif vol_ratio <= 1727.0:
        c15 = -1.69385 + (L / vol ** (1.0 / 3.0) - 8.0) / 2.36
    else:
        c15 = 0.0

    m2 = c15 * Cp ** 2 * math.exp(-0.1 * Fn ** -2)

    lam = 1.446 * Cp - 0.03 * L / B if L / B < 12.0 else 1.446 * Cp - 0.36

    exponent = m1 * Fn ** d + m2 * math.cos(lam * Fn ** -2)
    rw_over_rhogv = c1 * c2 * c5 * math.exp(exponent)

    return {
        "c1": c1, "c2": c2, "c5": c5, "c3": c3, "c7": c7,
        "m1": m1, "m2": m2, "lambda": lam, "c15": c15, "c16": c16, "d": d,
        "rw_over_rhogvol": rw_over_rhogv,
    }


# ---------------------------------------------------------------------------
# 适用域（Holtrop-Mennen 1982/1984 回归样本范围）
# ---------------------------------------------------------------------------

#: Holtrop-Mennen 系列回归的公布适用区间。超出即属外推，必须显式拒绝/降级，
#: 而不是静默返数（平台"几何/方法先验证"规则）。
HOLTROP_DOMAIN = {
    "cb": (0.55, 0.87),        # 方形系数
    "l_over_b": (3.9, 14.9),   # 长宽比
    "b_over_t": (2.1, 4.0),    # 宽吃水比
    "fn_max": 0.40,            # 兴波回归外推上限
    "l_min_m": 20.0,           # 回归样本最小船长（m）
}


def applicability_violations(length: float, beam: float, draft: float,
                             cb: float, speed_ms: float) -> List[str]:
    """返回超出 Holtrop 适用域的逐项理由；空列表 = 域内可算。

    判据全部取自公布区间的**保守端**：一次误放行比一次误拒绝代价大得多，
    因为越域外推不会报错，只会静默给出无依据的数字。
    """
    why: List[str] = []
    l_b = length / beam
    b_t = beam / draft
    cb_lo, cb_hi = HOLTROP_DOMAIN["cb"]
    lb_lo, lb_hi = HOLTROP_DOMAIN["l_over_b"]
    bt_lo, bt_hi = HOLTROP_DOMAIN["b_over_t"]
    if not (cb_lo <= cb <= cb_hi):
        why.append(f"Cb={cb:.3f} 超出 [{cb_lo}, {cb_hi}]")
    if not (lb_lo <= l_b <= lb_hi):
        why.append(f"L/B={l_b:.2f} 超出 [{lb_lo}, {lb_hi}]")
    if not (bt_lo <= b_t <= bt_hi):
        why.append(f"B/T={b_t:.2f} 超出 [{bt_lo}, {bt_hi}]（回转体/特殊艇型）")
    if length < HOLTROP_DOMAIN["l_min_m"]:
        why.append(f"L={length:.1f} m < {HOLTROP_DOMAIN['l_min_m']} m 回归样本下限")
    fn = froude_number(speed_ms, length) if length > 0 else 0.0
    if fn > HOLTROP_DOMAIN["fn_max"]:
        why.append(f"Fn={fn:.3f} > {HOLTROP_DOMAIN['fn_max']} 兴波回归外推")
    return why


# ---------------------------------------------------------------------------
# 主求解器
# ---------------------------------------------------------------------------

@dataclass
class ResistanceInput:
    """Holtrop 阻力计算输入（缺省船型参数会写进 assumptions）。"""
    length: float
    beam: float
    draft: float
    cb: float
    speed_ms: float
    displacement_t: Optional[float] = None   # 排水量（t）；缺省由 Cb·L·B·T 估
    rho: float = RHO_SEA
    nu: float = NU_SEA
    cm: Optional[float] = None               # 中横剖面系数
    cwp: Optional[float] = None              # 水线面系数
    lcb_pct: float = 0.0                     # 浮心纵位置（%L，+ 为艏）
    i_e_deg: float = 25.0                    # 进流半角（deg）
    c_stern: float = 0.0                     # 艉型系数（-10..+10）
    a_bt: float = 0.0                        # 球鼻艏横剖面积（m²）
    h_b: float = 0.0                         # 球鼻中心高（m）
    a_tr: float = 0.0                        # 方尾浸没面积（m²）
    ks: float = DEFAULT_ROUGHNESS_KS          # 粗糙度（m）
    tf: Optional[float] = None               # 艏吃水（m），缺省 = T
    s_wet_override: Optional[float] = None    # 几何验证过的湿面积（m²），优于此式回归


def compute_resistance(inp: ResistanceInput) -> Dict[str, object]:
    """返回 R_T 及其分解、无量纲量与全部假设；字段与平台 API 一一对应。

    返回键：reynolds_number, froude_number, wetted_surface, cf, ca, cr, ct,
    rf, rr, ra, rt, pe, method, assumptions。
    """
    L, B, T = inp.length, inp.beam, inp.draft
    Cb, V = inp.cb, inp.speed_ms
    if not (0.3 < Cb < 1.0):
        raise ValueError(f"Cb={Cb} out of physical range [0.3, 1.0]")
    if min(L, B, T, V) <= 0:
        raise ValueError("L, B, T, V must be positive")

    assumptions: List[str] = []

    if inp.cm is not None:
        Cm = inp.cm
    else:
        Cm = 0.97 + 0.03 * Cb
        assumptions.append(f"Cm estimated 0.97+0.03Cb = {Cm:.4f}")
    if inp.cwp is not None:
        Cwp = inp.cwp
    else:
        Cwp = (1.0 + 2.0 * Cb) / 3.0
        assumptions.append(f"Cwp estimated (1+2Cb)/3 = {Cwp:.4f}")

    Cp = Cb / Cm
    vol = inp.displacement_t / (inp.rho / 1000.0) if inp.displacement_t else Cb * L * B * T
    if inp.displacement_t:
        # 几何一致性：Cb·L·B·T 与输入排水量偏差 >5% 时告警（几何先验验证）
        vol_geom = Cb * L * B * T
        if abs(vol - vol_geom) / vol_geom > 0.05:
            assumptions.append(
                f"WARNING: displacement {inp.displacement_t} t vs Cb·L·B·T "
                f"{vol_geom:.0f} m³ mismatch >5%; Cb/L/B/T should be re-verified")
    Tf = inp.tf if inp.tf else T

    if inp.s_wet_override is not None:
        S = float(inp.s_wet_override)
        if S <= 0.0:
            raise ValueError(f"s_wet_override must be > 0, got {S}")
        assumptions.append("S_wet = geometry-validated input (overrides Holtrop regression)")
    else:
        S = wetted_surface_holtrop(L, B, T, Cb, Cm, Cwp, inp.a_bt)
    Re = reynolds_number(V, L, inp.nu)
    Fn = froude_number(V, L)
    Cf = ittc57_cf(Re)
    one_k1 = form_factor_1k1(L, B, T, Cp, inp.lcb_pct, vol, inp.c_stern)
    rw = wave_resistance_hull(L, B, T, Tf, Cp, vol, Fn, inp.i_e_deg,
                              inp.a_bt, inp.h_b, inp.a_tr, Cm)
    Rw = rw["rw_over_rhogvol"] * inp.rho * G * vol
    Rf = 0.5 * inp.rho * S * Cf * V ** 2
    Ca = correlation_allowance_ca(L, inp.ks)
    Ra = 0.5 * inp.rho * S * Ca * V ** 2
    Rt = Rf * one_k1 + Rw + Ra
    # 等效：Cr 定义为剩余阻力系数（含形状因子与相关补贴），供旧字段兼容
    q = 0.5 * inp.rho * S * V ** 2
    Cr_equiv = (Rf * (one_k1 - 1.0) + Rw + Ra) / q

    assumptions.append(f"Cp = Cb/Cm = {Cp:.4f}")
    assumptions.append(f"i_E = {inp.i_e_deg} deg (default; sensitivity band applies)")
    assumptions.append(f"k_s = {inp.ks * 1e6:.0f} um (ITTC-1978 new ship)")
    assumptions.append("R_B/R_TR/R_APP = 0 (no bulb transom or appendage data)")

    return {
        "reynolds_number": Re,
        "froude_number": Fn,
        "wetted_surface": S,
        "cf": Cf,
        "ca": Ca,
        "cr": Cr_equiv,
        "ct": Rt / q,
        "rf": Rf * one_k1,
        "rr": Rw,
        "ra": Ra,
        "rt": Rt,
        "pe": Rt * V,               # W
        "one_plus_k1": one_k1,
        "wave_coeffs": rw,
        "method": "Holtrop-Mennen(1982/1984): Rf(1+k1)+Rw+Ra, ITTC-57/ITTC-78",
        "assumptions": assumptions,
    }


def sensitivity_band(inp: ResistanceInput,
                     i_e_range=(10.0, 40.0),
                     lcb_range=(-2.0, 2.0),
                     ks_range=(75e-6, 300e-6)) -> Dict[str, float]:
    """对无法确知的船型参数做敏感性带，返回 R_T 的 min/max（N）。

    业界对 (1+k1)、R_W 的绝对精度没有「逐位」基准，只有带内比较才有意义；
    因此任何单点比对必须先声明带，否则结论不可辩护。
    """
    rts = []
    for i_e in i_e_range:
        for lcb in lcb_range:
            for ks in ks_range:
                sub = ResistanceInput(**{**inp.__dict__, "i_e_deg": i_e,
                                         "lcb_pct": lcb, "ks": ks})
                rts.append(compute_resistance(sub)["rt"])
    return {"rt_min": min(rts), "rt_max": max(rts),
            "rt_median": sorted(rts)[len(rts) // 2], "n": len(rts)}