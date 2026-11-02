"""耐波：2D 剖面附加质量（面元法 + 自由液面双体高频极限）→ 升沉 1 自由度 RAO。

为什么要这个模块（m1809，承接 m1780「耐波伪表」缺陷线索）
----------------------------------------------------------
在线 ``api/calculations/seakeeping`` 自述 ``method = "切片理论 + 经验公式"``，但它的
``heave_rao_max`` / ``roll_rao_max`` / ``pitch_rao_max`` 与波浪周期**完全无关**
（m1809 实测：T=4/8/12 s 三档逐位相同），航向角也不影响输出。线性切片解必然含
ζ̈、ζ̇ ⇒ RAO(ω) 不可能在 ω 上恒定；此外两条**不需要任何经验公式**的精确判据可机器裁决：

    H-LONGWAVE   ω→0（λ≫L）时升沉 RAO → 1 恒等（船体随波面整体平移）。精确。
    H-ROLL-SYM   ψ=180°（顶浪）时横摇激励恒为 0（左右对称 ⇒ FK 横摇矩逐项抵消）。精确。

本模块提供**独立的 REAL 参考核**，让平台能对上面两条做数值对账，并把
``耐波`` 从"无判据"提升到"可机器裁决"：

1. 2D 常强度源面元法（Laplace，Neumann）求剖面附加质量 —— 与解析解逐项对账：
   * 潜没椭圆（无自由液面）：A33 = ρπa²、A22 = ρπb²（**精确**）；
   * 半浸半圆（自由液面高频极限 φ=0）：A33 = ρπa²/2（**精确**，推导见下）。
2. 高频自由液面条件 φ=0 的严格处理：对**升沉（垂向）**运动，把剖面关于水线镜像成
   "双体"，在无限流场中解整体升沉，取一半即原问题解。
   推导（可逐项验算）：圆截面升沉的精确势 φ = -V₃a²z/r² 在 z=0 上 φ≡0 且关于 z 奇
   ⇒ 全圆解限制到下半平面即「半圆 + φ=0」的解，故 A33(半圆)=½A33(全圆)=ρπa²/2。
   横荡无此性质（φ|z=0 ≠ 0），故双体只用于垂向；横荡面元仅用于潜没校验。
3. 船体升沉模型（1 自由度切片）：A33=∫m₃₃(x)dx、C=ρgA_wp、FK 激励 ρgζ_aA_wp·e^{-kT}；
   阻尼取经验阻尼比带（**明确标注 WITHHELD**：2D 辐射阻尼需自由液面 Green 函数，
   本模块未实现；故峰值幅值不作判据，只用 T_n 与 ω→0/ω→∞ 极限）。

证据分级集中在 ``EVIDENCE``（机器可读），"精确"字样只给解析锚与上述两条恒等式。

单位：SI（m, kg, s, N, rad）；剖面量按每米船长（kg/m）。
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .constants import G, RHO_SEA

Poly = List[Tuple[float, float]]  # (y, z)，z 由龙骨向上，水线 z = draft

#: 本模块证据分级的机器可读自述（供审计与报告引用）
EVIDENCE: Dict[str, str] = {
    "panel_laplace_neumann": "REAL",
    "added_mass_submerged_ellipse": "REAL",        # 与 ρπa² / ρπb² 解析解对账
    "added_mass_fs_heave_semicircle": "REAL",      # 与 ρπa²/2 解析解对账（双体推导）
    "added_mass_fs_heave_general_section": "REAL",  # 同一面元核 + 双体镜像
    "radiation_damping": "WITHHELD",               # 未实现 2D 自由液面 Green 函数
    "heave_rao_peak_amplitude": "WITHHELD",        # 依赖阻尼比参数 ⇒ 不作判据
    "heave_long_wave_limit": "REAL",               # RAO→1 精确恒等
    "heave_natural_period": "REAL",                # 2π√((m+A33)/C)，A33 有解析锚
    "roll_fk_excitation_head_seas": "REAL",        # 对称性 ⇒ 恒 0，解析可证
}


# ------------------------------------------------------------------ 剖面几何


def immersed_polygon(beam: float, draft: float, cb: float = 1.0) -> Poly:
    """水下剖面多边形（逆时针，z 由龙骨向上，水线 z=draft）。

    梯形剖面：龙骨半宽 y_k = B(Cb-1/2)（Cb<0.5 取 0，退化为三角形），水线半宽 B/2；
    面积 = Cb·B·T（每米船长）。
    """
    b, t = float(beam), float(draft)
    yk = max(0.0, b * (cb - 0.5))
    return [(yk, 0.0), (b / 2.0, t), (-b / 2.0, t), (-yk, 0.0)]


def refine_polygon(poly: Poly, n_edge: int = 24) -> Poly:
    """把多边形的每条边等分为 n_edge 段（面元长度一致，精度可控）。"""
    out: Poly = []
    n = len(poly)
    for i in range(n):
        y1, z1 = poly[i]
        y2, z2 = poly[(i + 1) % n]
        for k in range(n_edge):
            f = k / n_edge
            out.append((y1 + f * (y2 - y1), z1 + f * (z2 - z1)))
    return out


def doubled_body(poly: Poly) -> Poly:
    """把水下剖面关于水线（z_max）镜像成闭合双体（用于 φ=0 的高频升沉）。

    顶点序：右水线点 →（沿剖面）→ 左水线点 → 左水线内侧点的镜像 → … → 右水线内侧点的镜像。
    两个水线端点与其镜像重合，故镜像链只取内点，避免零长面元。
    """
    z_w = max(p[1] for p in poly)
    n = len(poly)
    wl = [i for i, p in enumerate(poly) if abs(p[1] - z_w) <= 1e-12]
    if len(wl) < 2:
        raise ValueError("doubled_body 需要至少两个水线点（z=z_max）")
    wl_set = set(wl)
    i_r = max(wl, key=lambda i: poly[i][0])   # 右水线端点（y 最大）
    i_l = min(wl, key=lambda i: poly[i][0])   # 左水线端点
    # 步向取"立即离开水线边"的一侧，避免把水线中间的加密点带进链
    if (i_r + 1) % n not in wl_set:
        step = 1
    elif (i_r - 1) % n not in wl_set:
        step = -1
    else:
        raise ValueError("水线边退化：左右端点相邻")
    chain: Poly = [poly[i_r]]
    i = i_r
    guard = 0
    while i != i_l and guard <= 4 * n:
        i = (i + step) % n
        guard += 1
        while i in wl_set and i != i_l:      # 跳过水线边上的加密点
            i = (i + step) % n
        chain.append(poly[i])
    if chain[-1] != poly[i_l]:
        raise ValueError("doubled_body 未能闭合到左水线端点")
    mirrored = [(p[0], 2.0 * z_w - p[1]) for p in reversed(chain[1:-1])]
    return chain + mirrored


def ellipse_polygon(a: float, b: float, n: int = 240, z_center: float = 0.0) -> Poly:
    """椭圆离散多边形（半轴 a 沿 y、b 沿 z），逆时针。"""
    return [
        (a * math.cos(2 * math.pi * k / n), z_center + b * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]


def semicircle_polygon(radius: float, n: int = 240) -> Poly:
    """半浸半圆剖面（水线 z=0，圆心上移），逆时针；面积 = πρ²/2。"""
    pts = [(-radius * math.cos(math.pi * k / n), -radius * math.sin(math.pi * k / n))
           for k in range(n + 1)]
    # 左水线点 → 底部 → 右水线点，闭合边即水线
    return pts


# ------------------------------------------------------------------ 面元法

_GAUSS = np.array([
    -0.9602898564975363, -0.7966664774136267, -0.5255324099163290, -0.1834346424956498,
    0.1834346424956498, 0.5255324099163290, 0.7966664774136267, 0.9602898564975363,
])
_GAUSS_W = np.array([
    0.1012285362903763, 0.2223810344533745, 0.3137066458778873, 0.3626837833783620,
    0.3626837833783620, 0.3137066458778873, 0.2223810344533745, 0.1012285362903763,
])


def _panel_arrays(poly: Poly):
    p = np.asarray(poly, dtype=float)
    p1 = p
    p2 = np.roll(p, -1, axis=0)
    d = p2 - p1
    L = np.hypot(d[:, 0], d[:, 1])
    keep = L > 1e-14
    p1, p2, d, L = p1[keep], p2[keep], d[keep], L[keep]
    c = 0.5 * (p1 + p2)
    # 外法向（逆时针 ⇒ (dz, -dy)/L）
    nrm = np.stack([d[:, 1] / L, -d[:, 0] / L], axis=1)
    # 统一朝外：用有向面积校正
    area = float(np.sum(p1[:, 0] * p2[:, 1] - p2[:, 0] * p1[:, 1]))
    if area < 0:
        nrm = -nrm
    return p1, p2, d, L, c, nrm


def _influence_matrices(p1, p2, L, c, nrm):
    """返回 I[i,j] = ∫_j ln|P_i-Q| dl 与 Dn[i,j] = ∫_j ∂ln|P_i-Q|/∂n_i dl。

    奇异自项（i=j）在 I 中用解析值 L(ln(L/2)-1)，Dn 中 PV 部分为 0。
    """
    n = p1.shape[0]
    # Gauss 点坐标 (n, ng, 2)
    f = 0.5 * (1.0 - _GAUSS)
    g = 0.5 * (1.0 + _GAUSS)
    Q = (p1[:, None, :] * f[None, :, None] + p2[:, None, :] * g[None, :, None])
    wL = (0.5 * _GAUSS_W)[None, :] * L[:, None]              # (n_j, ng)
    diff = c[:, None, None, :] - Q[None, :, :, :]        # (n_i, n_j, ng, 2)
    r2 = np.einsum("ijkg,ijkg->ijk", diff, diff)
    r2 = np.maximum(r2, 1e-24)
    I = np.sum(0.5 * np.log(r2) * wL[None, :, :], axis=2)   # (n_i, n_j)
    # 自项奇异：用解析值替换
    idx = np.arange(n)
    I[idx, idx] = L * (np.log(np.maximum(L / 2.0, 1e-12)) - 1.0)
    # Dn：diff·n_i / r2
    dot = diff[:, :, :, 0] * nrm[:, None, None, 0] + diff[:, :, :, 1] * nrm[:, None, None, 1]
    Dn = np.sum((dot / r2) * wL[None, :, :], axis=2)
    Dn[idx, idx] = 0.0
    return I, Dn


def panel_added_mass(poly: Poly, rho: float = RHO_SEA) -> Dict[str, float]:
    """2D 常强度源面元：无限流场中剖面平移的附加质量（每米船长，kg/m）。

    求解 ∂φ/∂n = n·V（V 为单位平移）；A_kl = -ρ∮φ_k n_l ds（n 为外法向，
    等价于 Faltinsen「指向体内法向」的 +ρ∮φ n ds）。
    """
    p1, p2, d, L, c, nrm = _panel_arrays(poly)
    I, Dn = _influence_matrices(p1, p2, L, c, nrm)
    K = Dn / (2.0 * math.pi)
    np.fill_diagonal(K, 0.5)
    out: Dict[str, float] = {}
    phi = {}
    for key, k in (("sway", 0), ("heave", 1)):
        sigma = np.linalg.solve(K, nrm[:, k])
        # φ_i = (1/2π)Σ_j σ_j I_ij
        phi[key] = sigma @ I.T / (2.0 * math.pi)
    add = lambda key, k: float(-rho * np.sum(phi[key] * nrm[:, k] * L))  # noqa: E731
    out["sway"] = add("sway", 0)
    out["heave"] = add("heave", 1)
    out["sway_heave"] = float(-rho * np.sum(phi["sway"] * nrm[:, 1] * L))
    return out


def analytic_ellipse_added_mass(a: float, b: float, rho: float = RHO_SEA) -> Dict[str, float]:
    """无限流场椭圆（半轴 a 沿 y、b 沿 z）附加质量解析解。

    沿某方向平移的附加质量 = ρπ·(垂直于运动方向的半轴)² ⇒ 升沉 = ρπa²、横荡 = ρπb²；
    圆 a=b 时两者皆 ρπa²。
    """
    return {"sway": rho * math.pi * b * b, "heave": rho * math.pi * a * a}


def section_heave_added_mass(
    poly: Poly, rho: float = RHO_SEA, free_surface: bool = True,
) -> float:
    """剖面升沉附加质量（kg/m）。free_surface=True 用双体取半（φ=0 高频极限）。"""
    if free_surface:
        return 0.5 * panel_added_mass(doubled_body(poly), rho)["heave"]
    return panel_added_mass(poly, rho)["heave"]


# ------------------------------------------------------------------ 船体


@dataclass
class Hull:
    """wall-sided 船体：水线宽沿 x = 平行中体 + 两端直线收缩（Cw = Cb 自洽）。"""

    length: float
    beam: float
    draft: float
    cb: float
    station_cb: float = 1.0   # 典型剖面方形系数（wall-sided = 1）

    @property
    def volume(self) -> float:
        return self.cb * self.length * self.beam * self.draft

    @property
    def waterplane_area(self) -> float:
        return self.cb * self.length * self.beam

    def beam_at(self, x: float) -> float:
        """x∈[-L/2, L/2] 处水线宽；平行中体长 p·L，p = max(0, 2Cb-1)。"""
        p = min(1.0, max(0.0, 2.0 * self.cb - 1.0))
        half_par = 0.5 * p * self.length
        ax = abs(x)
        if ax <= half_par:
            return self.beam
        end = 0.5 * self.length - half_par
        if end <= 1e-12:
            return self.beam
        return self.beam * max(0.0, 1.0 - (ax - half_par) / end)

    def section_at(self, x: float, n_edge: int = 12) -> Poly:
        return refine_polygon(immersed_polygon(self.beam_at(x), self.draft, self.station_cb), n_edge)


def strip_heave_added_mass(
    hull: Hull, rho: float = RHO_SEA, n_stations: int = 15, n_edge: int = 12,
) -> Dict[str, float]:
    """沿船长切片积分升沉附加质量（kg）。"""
    dx = hull.length / n_stations
    total = 0.0
    b_max = 0.0
    for i in range(n_stations):
        x = -0.5 * hull.length + dx * (i + 0.5)
        b = hull.beam_at(x)
        b_max = max(b_max, b)
        if b < 1e-3:
            continue
        total += section_heave_added_mass(hull.section_at(x, n_edge), rho, True) * dx
    return {
        "added_mass_heave": total,
        "waterplane_area": hull.waterplane_area,
        "displacement_kg": hull.volume * rho,
        "n_stations": float(n_stations),
        "max_station_beam": b_max,
    }


def heave_natural_period(
    hull: Hull, mass: float, rho: float = RHO_SEA, a33: Optional[float] = None,
    n_stations: int = 15, n_edge: int = 12,
) -> Dict[str, float]:
    """升沉固有周期 T_n = 2π√((m+A33)/(ρgA_wp))（s）。"""
    if a33 is None:
        a33 = strip_heave_added_mass(hull, rho, n_stations, n_edge)["added_mass_heave"]
    c = rho * G * hull.waterplane_area
    wn = math.sqrt(c / (mass + a33))
    return {"a33": a33, "stiffness": c, "omega_n": wn, "period_n": 2.0 * math.pi / wn}


def heave_rao(
    hull: Hull, periods: Sequence[float], mass: float, wave_amp: float = 1.0,
    damping_ratio: float = 0.15, rho: float = RHO_SEA, a33: Optional[float] = None,
) -> List[Dict[str, float]]:
    """1 自由度升沉 RAO（切片附加质量 + 经验阻尼比）。

    RAO = |z_a|/ζ_a。阻尼为参数化（EVIDENCE['radiation_damping']=WITHHELD），
    故只有 T_n、ω→0（→1）、ω→∞（→0）三条可作精确判据。
    """
    if a33 is None:
        a33 = strip_heave_added_mass(hull, rho)["added_mass_heave"]
    c = rho * G * hull.waterplane_area
    mtot = mass + a33
    wn = math.sqrt(c / mtot)
    b33 = 2.0 * damping_ratio * wn * mtot
    out: List[Dict[str, float]] = []
    for t in periods:
        if t <= 0:
            continue
        w = 2.0 * math.pi / t
        k = w * w / G
        f3 = rho * G * wave_amp * hull.waterplane_area * math.exp(-k * hull.draft)
        den = complex(c - w * w * mtot, w * b33)
        out.append({
            "period": t,
            "omega": w,
            "rao_heave": abs(f3 / den) if den else 0.0,
            "fk_force_N": f3,
            "kdraft": k * hull.draft,
        })
    return out


def _cos_phase(ky: float, y: float) -> float:
    """入射波相位因子 cos(k·y·sinψ)（单位波幅）。"""
    return math.cos(ky * y)


def fk_roll_moment_free_surface(
    hull: Hull, wave_amp: float, period: float, heading_deg: float,
    rho: float = RHO_SEA, n_y: int = 61,
) -> float:
    """Froude-Krylov 横摇激励 |M4| 幅值（N·m，每米船长，力矩参考点取水线）。

    入射波压复幅值 p = ρgζ_a e^{kz}·e^{-i k y sinψ}（z 由水线向上为正）；
    M4 = ∮ p (y n_z - z n_y) ds。取复幅值（不取 t=0 实部，否则相位信息丢失）。
    ψ = 0°/180°（随浪/顶浪）时 e^{-i k y sinψ} ≡ 1 且剖面左右对称 ⇒ M4 ≡ 0（**精确**）；
    ψ = 90°（横浪）最大。该恒等式是审计判据 H-ROLL-SYM 的依据。
    """
    if wave_amp <= 0 or period <= 0:
        return 0.0
    w = 2.0 * math.pi / period
    k = w * w / G
    ky = k * math.sin(math.radians(heading_deg))
    b, t = hull.beam, hull.draft
    tot = 0.0 + 0.0j
    for side in (+1.0, -1.0):           # 两舷：n_y = ±1，ds = dz（z 由水线向上，负值在水下）
        for i in range(n_y):
            z = -t + t * (i + 0.5) / n_y
            p = rho * G * wave_amp * math.exp(k * z) * cmath.exp(-1j * ky * side * b / 2.0)
            tot += p * z * side * (t / n_y)
    for i in range(n_y):                # 船底：n_z = -1，ds = dy
        y = -b / 2.0 + b * (i + 0.5) / n_y
        p = rho * G * wave_amp * math.exp(-k * t) * cmath.exp(-1j * ky * y)
        tot += p * y * (-1.0) * (b / n_y)
    return abs(tot)


def long_wave_heave_limit() -> float:
    """ω→0 精确极限：RAO_heave → 1.0（船体随波面整体平移）。"""
    return 1.0