"""推进：叶元动量理论（BEMT）敞水特性 + 推力平衡反解转速。

修复要点（对应 m1473 审计 D5 / D6，判据 P-ETA-BOUND / P-KT / P-THRUST / P-PD）
--------------------------------------------------------------------------
在线服务 ``api/propulsion.predict`` 用两条直线冒充 B 系列图谱::

    Kt = 0.3 - 0.2*J ;  Kq = 0.04 - 0.02*J
    n  = V/(0.7*D)    ;  D  = 0.7*T*0.8

后果：J→0 时 Kq→0.04 而 Kt→0.3 ⇒ η0=Kt·J/(2πKq) 在低 J 无界（审计实见 η0=1.262
越致动盘上界 0.998），隐含 KT 可低至 0.00135；转速由 V/(0.7D) 拍定，与阻力需求
无关 ⇒ 跨端点推力 7.15 kN vs 阻力需求 6.89 MN（相差 3 个量级）。

注：服务侧 ``methods/propulsion/wageningen.py`` 的 39/43 项"B 系列系数表"亦不可用
（合成表：Z=4、P/D=1 时 KT(J=0)=0.219-0.392-0.373·4=-1.67，非物理），故本模块
**不复制**该表，改用可自洽校验的 BEMT：叶元（升力线剖面 Cl/Cd）与动量（含
Prandtl 梢/毂损失）逐半径配平，再由 T(1-t)=Rt 反解转速。

BEMT 的守恒性使其天然满足致动盘上界：η_i = 2/(1+sqrt(1+C_T))，η0 ≤ η_i。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .constants import RHO_SEA

# ---------------------------------------------------------------- 剖面极曲线


#: 剖面极曲线标定（二维升力线斜率 + 三维展弦比折减 + 翼型阻力极）
CL_SLOPE = 4.30      # 1/rad（二维 2π≈6.28，桨叶展弦比 ~1.5，三维有效斜率折减）
CD0 = 0.0180         # 零升阻力（Re≈2e6 桨叶剖面 + 三维/粗糙度折算）
CD_K = 0.0220        # 诱导阻力系数 Cd = CD0 + CD_K·Cl²
STALL_RAD = 0.21     # 12° 失速


def section_coeffs(alpha_rad: float) -> Tuple[float, float]:
    """叶剖面升阻系数（NACA-like，含失速平台）。α0=-2°。"""
    a0 = math.radians(-2.0)
    da = alpha_rad - a0
    if abs(da) <= STALL_RAD:
        cl = CL_SLOPE * da
    else:
        over = abs(da) - STALL_RAD
        cl = math.copysign(max(0.10, 0.96 - 2.2 * over), da)
    cd = CD0 + CD_K * cl * cl
    if abs(da) > STALL_RAD:
        cd += 0.9 * (abs(da) - STALL_RAD) ** 2
    return cl, cd


def chord_shape(x: float) -> float:
    """B 系列近似弦长分布形状（峰值在 0.7R）。"""
    return max(0.0, 0.55 + 1.5 * x - 1.05 * x * x)


# ---------------------------------------------------------------- BEMT


@dataclass
class OpenWaterPoint:
    j: float
    kt: float
    kq: float
    eta0: float
    thrust_N: float
    torque_Nm: float
    eta_ideal: float = float("nan")


def _bemt_point(
    j: float,
    d: float,
    z: int = 4,
    pd: float = 1.0,
    ae_ao: float = 0.55,
    hub_ratio: float = 0.2,
    rho: float = RHO_SEA,
    nr: int = 24,
    n_rps: float = 1.0,
) -> Tuple[float, float, float, float]:
    """返回 (KT, KQ, T[N], Q[Nm])。

    以诱导速度 (u_a, u_t) 为未知量（而非诱导因子），使 J=0 系泊工况仍有定义：
    动量式 dT = 4πρr·u_a(V_a+u_a)·F·dr，dQ = 2πrρ(V_a+u_a)·2u_t·F·r·dr。
    """
    R = d / 2.0
    va = j * n_rps * d
    omega = 2.0 * math.pi * n_rps
    a_exp = ae_ao * math.pi * d * d / 4.0
    xs = [hub_ratio + (0.995 - hub_ratio) * i / (nr - 1) for i in range(nr)]
    i_ch = sum(chord_shape(x) for x in xs) / nr * (0.995 - hub_ratio)
    c07 = a_exp / (z * R * i_ch) if i_ch > 0 else 0.0

    thrust = 0.0
    torque = 0.0
    dr = (0.995 - hub_ratio) * R / (nr - 1)
    for x in xs:
        r = x * R
        c = c07 * chord_shape(x)
        if c <= 0.0:
            continue
        beta = math.atan(pd * d / (2.0 * math.pi * r))
        ua_hi = max(1.0, 2.0 * va) + 0.5 * omega * r
        ut_hi = 0.95 * omega * r
        ua, ut = 0.05 * ua_hi, 0.02 * ut_hi

        for _ in range(25):
            # --- 轴向动量配平
            def res_ua(x_a: float, ut_fixed: float = ut) -> float:
                va_l = va + x_a
                vt_l = omega * r - ut_fixed
                phi = math.atan2(va_l, vt_l) if vt_l > 1e-9 else math.pi / 2
                cl, cd = section_coeffs(beta - phi)
                cn = cl * math.cos(phi) - cd * math.sin(phi)
                f = prandtl_f(z, R, r, phi)
                thr_b = 0.5 * rho * (va_l * va_l + vt_l * vt_l) * z * c * cn
                thr_m = 4.0 * math.pi * rho * r * x_a * (va + x_a) * f
                return thr_b - thr_m

            ua = _bisect(res_ua, 0.0, ua_hi)

            def res_ut(x_t: float, ua_fixed: float = ua) -> float:
                va_l = va + ua_fixed
                vt_l = omega * r - x_t
                phi = math.atan2(va_l, vt_l) if vt_l > 1e-9 else math.pi / 2
                cl, cd = section_coeffs(beta - phi)
                ct = cl * math.sin(phi) + cd * math.cos(phi)  # 升力+阻力均耗功
                f = prandtl_f(z, R, r, phi)
                w2 = va_l * va_l + vt_l * vt_l
                q_b = 0.5 * rho * w2 * z * c * ct * r
                q_m = 2.0 * math.pi * r * rho * (va + ua_fixed) * 2.0 * x_t * f * r
                return q_b - q_m

            ut = _bisect(res_ut, 0.0, ut_hi)

        va_l = va + ua
        vt_l = omega * r - ut
        phi = math.atan2(va_l, vt_l) if vt_l > 1e-9 else math.pi / 2
        cl, cd = section_coeffs(beta - phi)
        cn = cl * math.cos(phi) - cd * math.sin(phi)
        ct = cl * math.sin(phi) + cd * math.cos(phi)
        w2 = va_l * va_l + vt_l * vt_l
        thrust += 0.5 * rho * w2 * z * c * cn * dr
        torque += 0.5 * rho * w2 * z * c * ct * r * dr

    kt = thrust / (rho * n_rps ** 2 * d ** 4)
    kq = torque / (rho * n_rps ** 2 * d ** 5)
    return kt, kq, thrust, torque


def prandtl_f(z: int, R: float, r: float, phi: float) -> float:
    """Prandtl 梢部损失因子（毂部损失并入同一形式，取 min 保证 F∈(0,1]）。"""
    s = max(math.sin(phi), 1e-6)
    f_tip = z * (R - r) / (2.0 * r * s)
    f_hub = z * (r - 0.2 * R) / (2.0 * R * s) if r > 0.2 * R else 50.0
    f = min(f_tip, f_hub)
    return max(0.05, (2.0 / math.pi) * math.acos(math.exp(-min(f, 40.0))))


def _bisect(fn, lo: float, hi: float, iters: int = 44) -> float:
    """单调递减函数求根（fn(lo)>0>fn(hi) 时）；否则返回端点。"""
    flo, fhi = fn(lo), fn(hi)
    if flo <= 0.0:
        return lo
    if fhi >= 0.0:
        return hi
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if fn(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------- 敞水特性


def open_water(
    j: float,
    d: float,
    z: int = 4,
    pd: float = 1.0,
    ae_ao: float = 0.55,
    hub_ratio: float = 0.2,
    rho: float = RHO_SEA,
    nr: int = 24,
    n_rps: float = 1.0,
) -> OpenWaterPoint:
    kt, kq, thr, tor = _bemt_point(j, d, z, pd, ae_ao, hub_ratio, rho, nr, n_rps)
    eta = j * kt / (2.0 * math.pi * kq) if kq > 1e-9 else 0.0
    return OpenWaterPoint(j=j, kt=kt, kq=kq, eta0=eta, thrust_N=thr, torque_Nm=tor)


def open_water_curve(
    d: float,
    z: int = 4,
    pd: float = 1.0,
    ae_ao: float = 0.55,
    j_values: Optional[List[float]] = None,
    **kw,
) -> List[OpenWaterPoint]:
    if j_values is None:
        j_values = [i * 0.05 for i in range(0, 17)]
    return [open_water(j, d, z, pd, ae_ao, **kw) for j in j_values]


def zero_thrust_j(d: float, z: int = 4, pd: float = 1.0, ae_ao: float = 0.55, **kw) -> float:
    """零推力进速系数 J0（KT(J0)=0）。"""
    lo, hi = 0.02, 2.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if open_water(mid, d, z, pd, ae_ao, **kw).kt > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def ideal_efficiency(ct: float) -> float:
    """致动盘理想效率 η_I = 2/(1+sqrt(1+C_T))，C_T = T/(½ρV²A0)。"""
    if ct <= -1.0:
        return float("nan")
    return 2.0 / (1.0 + math.sqrt(1.0 + max(ct, 0.0)))


# ---------------------------------------------------------------- 船-桨配合


def wake_fraction(cb: float) -> float:
    """伴流分数（Taylor，单桨）：w = 0.5Cb - 0.05。"""
    return 0.5 * cb - 0.05


def thrust_deduction(cb: float) -> float:
    """推力减额（Taylor，单桨）：t = 0.5Cb - 0.12。"""
    return 0.5 * cb - 0.12


@dataclass
class PropulsionPoint:
    n_rps: float
    rpm: float
    j: float
    kt: float
    kq: float
    eta0: float
    eta_ideal: float
    thrust_N: float
    required_thrust_N: float
    rt_N: float
    t: float
    w: float
    va: float
    d: float
    pd: float
    delivered_power_kw: float
    equilibrium_error: float


def solve_n_for_thrust(
    rt_n: float,
    v_ms: float,
    d: float,
    cb: float,
    z: int = 4,
    pd: float = 1.0,
    ae_ao: float = 0.55,
    t: Optional[float] = None,
    w: Optional[float] = None,
    rho: float = RHO_SEA,
) -> PropulsionPoint:
    """由 T(1-t)=Rt 反解转速 n，返回配合点全部量。"""
    t_ = thrust_deduction(cb) if t is None else t
    w_ = wake_fraction(cb) if w is None else w
    va = v_ms * (1.0 - w_)
    req = rt_n / (1.0 - t_)

    def thrust_of_n(n: float) -> float:
        j = va / (n * d)
        return open_water(j, d, z, pd, ae_ao, rho=rho, n_rps=n).thrust_N

    lo, hi = 0.2, 40.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if thrust_of_n(mid) < req:
            lo = mid
        else:
            hi = mid
    n = 0.5 * (lo + hi)
    j = va / (n * d)
    ow = open_water(j, d, z, pd, ae_ao, rho=rho, n_rps=n)
    a0 = math.pi * d * d / 4.0
    ct = req / (0.5 * rho * va * va * a0)
    return PropulsionPoint(
        n_rps=n,
        rpm=n * 60.0,
        j=j,
        kt=ow.kt,
        kq=ow.kq,
        eta0=ow.eta0,
        eta_ideal=ideal_efficiency(ct),
        thrust_N=ow.thrust_N,
        required_thrust_N=req,
        rt_N=rt_n,
        t=t_,
        w=w_,
        va=va,
        d=d,
        pd=pd,
        delivered_power_kw=2.0 * math.pi * n * ow.torque_Nm / 1000.0,
        equilibrium_error=abs(ow.thrust_N * (1.0 - t_) - rt_n) / rt_n,
    )


def diameter_from_draft(t: float, factor: float = 0.65) -> float:
    """桨径经验：单桨 D ≈ 0.65·T（保证梢部间隙）。"""
    return factor * t


def summary(point: PropulsionPoint) -> Dict[str, float]:
    return {
        "n_rps": point.n_rps,
        "rpm": point.rpm,
        "J": point.j,
        "KT": point.kt,
        "KQ": point.kq,
        "eta0": point.eta0,
        "eta_ideal": point.eta_ideal,
        "thrust_kN": point.thrust_N / 1000.0,
        "required_thrust_kN": point.required_thrust_N / 1000.0,
        "P_D": point.pd,
        "PD_kW": point.delivered_power_kw,
        "equilibrium_error": point.equilibrium_error,
    }