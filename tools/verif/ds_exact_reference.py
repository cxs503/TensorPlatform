"""独立精确参考：盒式驳船破舱静稳性 GZ（mission 2043, verifier 席）。

与平台实现（``backend/api/damage_stability.py``）的数学路线完全不同：

* 平台：横向截面在 241 个 y 节点上**梯形采样**，破损区按「沿船长平均带宽」移除。
* 本参考：把积分区间按水面线与舱室的**全部断点**切开，每段用 3 点
  Gauss–Legendre（被积函数在每段上是不超过 2 次的多项式 ⇒ 精确至 5 次）**精确积分**；
  破损以「按船长份额加权的渗透率 α = μ·l/L」作用在**几何带宽** b 上。

模型（与平台同构，便于逐量对账）：
  截面 z_w(y) = d/cosθ + y·tanθ（无纵倾；对称/中置舱室时精确）
  h(y)      = clip(z_w, 0, D)                     浸没高度
  g(y)      = clip(min(h, z_t) − z_b, 0, ∞) 且 y 在破口带内   舱内水柱
  A(θ)      = ∫(h − α·g)dy 等三个矩 → 求 d 使 A·L = ∇0 = Cb·L·B·T
  GZ(θ)     = y_B·cosθ + (z_B − KG)·sinθ       （损失浮力法，不再另扣自由液面修正）

解析锚（与本模块相互独立，用于验证本模块自身）：
  t'  = L·B·T / (L·B − μ·l·b)
  KB' = t'/2, BM' = (I_T0 − μ·l·b³/12 − A_loss·y_c²)/∇0, GM = KB' + BM' − KG
  完整船（μ→0）壁式闭式：GZ = sinθ·(GM + ½·BM·tan²θ)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

_GAUSS_X = np.array([-0.7745966692414834, 0.0, 0.7745966692414834])
_GAUSS_W = np.array([0.5555555555555556, 0.8888888888888888, 0.5555555555555556])


@dataclass
class Barge:
    """盒式驳船（Cb = 1，等宽壁式）。"""
    L: float
    B: float
    D: float
    T: float
    KG: float

    @property
    def nabla0(self) -> float:
        return self.L * self.B * self.T

    @property
    def I_T0(self) -> float:
        return self.L * self.B ** 3 / 12.0


@dataclass
class Void:
    """矩形舱室（几何，未做渗透率折减）。"""
    l: float
    b: float
    z_b: float
    z_t: float
    y_c: float = 0.0
    mu: float = 0.95


def _y_of_z(z: float, theta: float, d: float) -> Optional[float]:
    tan = math.tan(theta)
    if abs(tan) < 1e-12:
        return None
    return (z - d / math.cos(theta)) / tan


def _section(barge: Barge, d: float, theta: float,
             void: Optional[Void]) -> Tuple[float, float, float, float, float, float]:
    """返回 (A_i, My_i, Mz_i, A_v, My_v, Mz_v)。

    下标 i = 完整截面矩；下标 v = 舱室侵占区（几何，未乘渗透率）矩。
    Mz 的舱室项为 ∫(z_b·g + g²/2)dy（对 z 轴的一阶矩损失）。
    """
    B, D = barge.B, barge.D
    cos_t, tan_t = math.cos(theta), math.tan(theta)
    ylo, yhi = -B / 2.0, B / 2.0

    def h_of(y: np.ndarray) -> np.ndarray:
        return np.clip(d / cos_t + y * tan_t, 0.0, D)

    # ---- 断点：水面线与 0/D/z_b/z_t 的交点 + 破口带边界 ----
    cuts: List[float] = [ylo, yhi]
    if void is not None:
        yl, yr = void.y_c - void.b / 2.0, void.y_c + void.b / 2.0
        cuts += [max(ylo, min(yhi, yl)), max(ylo, min(yhi, yr))]
        zs = (void.z_b, void.z_t)
    else:
        zs = ()
    for z in (0.0, D) + tuple(zs):
        y = _y_of_z(z, theta, d)
        if y is not None and ylo < y < yhi:
            cuts.append(y)
    cuts = sorted(set(round(v, 10) for v in cuts))

    A_i = My_i = Mz_i = 0.0
    A_v = My_v = Mz_v = 0.0
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b <= a:
            continue
        half = 0.5 * (b - a)
        mid = 0.5 * (a + b)
        yg = mid + half * _GAUSS_X
        wg = half * _GAUSS_W
        h = h_of(yg)
        A_i += float(np.sum(wg * h))
        My_i += float(np.sum(wg * yg * h))
        Mz_i += float(np.sum(wg * 0.5 * h * h))
        if void is not None:
            yl, yr = void.y_c - void.b / 2.0, void.y_c + void.b / 2.0
            g = np.clip(np.minimum(h, void.z_t) - void.z_b, 0.0, None)
            g = np.where((yg >= yl) & (yg <= yr), g, 0.0)
            A_v += float(np.sum(wg * g))
            My_v += float(np.sum(wg * yg * g))
            Mz_v += float(np.sum(wg * (void.z_b * g + 0.5 * g * g)))
    return A_i, My_i, Mz_i, A_v, My_v, Mz_v


def _alpha(barge: Barge, void: Optional[Void]) -> float:
    """按船长份额加权的渗透率 α = μ·l/L（l 截断到船长）。"""
    if void is None or void.b <= 0:
        return 0.0
    return void.mu * min(void.l, barge.L) / barge.L


def solve_waterline(barge: Barge, theta: float, void: Optional[Void],
                    tol: float = 1e-12) -> float:
    """求平衡水线参数 d，使 ``A_bar·L = ∇0``（二分，精确断点积分 ⇒ d 可达机器精度）。"""
    alpha = _alpha(barge, void)
    target = barge.nabla0 / barge.L

    def A_bar(d: float) -> float:
        A_i, _, _, A_v, _, _ = _section(barge, d, theta, void)
        return A_i - alpha * A_v

    lo, hi = -2.0 * barge.D, 4.0 * barge.D
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if A_bar(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def gz(barge: Barge, theta_deg: float, void: Optional[Void],
       d: Optional[float] = None) -> float:
    """精确 GZ（m）。损失浮力法：重心不动，浮心由精确截面矩求得。"""
    theta = math.radians(theta_deg)
    alpha = _alpha(barge, void)
    if d is None:
        d = solve_waterline(barge, theta, void)
    A_i, My_i, Mz_i, A_v, My_v, Mz_v = _section(barge, d, theta, void)
    A = A_i - alpha * A_v
    My = My_i - alpha * My_v
    Mz = Mz_i - alpha * Mz_v
    if A <= 0:
        return 0.0
    y_B, z_B = My / A, Mz / A
    return y_B * math.cos(theta) + (z_B - barge.KG) * math.sin(theta)


def equilibrium_heel(barge: Barge, void: Optional[Void],
                     scan: float = 60.0, step: float = 1.0) -> float:
    """GZ(θ)=0 的 |θ| 最小根（精确参考版）。"""
    ang = np.arange(-scan, scan + 1e-9, step)
    vals = [gz(barge, float(a), void) for a in ang]
    roots = []
    for i in range(len(ang) - 1):
        v0, v1 = vals[i], vals[i + 1]
        if v0 == 0.0:
            roots.append(float(ang[i]))
        elif v0 * v1 < 0.0:
            lo, hi, flo = float(ang[i]), float(ang[i + 1]), v0
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                fm = gz(barge, mid, void)
                if flo * fm <= 0.0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            roots.append(0.5 * (lo + hi))
    if not roots:
        return float(ang[int(np.argmin(np.abs(vals)))])
    return min(roots, key=abs)


def gz_curve(barge: Barge, void: Optional[Void], heel0: float, cap: float,
             step: float = 1.0) -> Dict[str, float]:
    """残存 GZ 曲线（heel0 → cap）+ GZmax / Range / 面积。"""
    if cap <= heel0:
        return {"gz_max": 0.0, "gz_max_angle": heel0, "range": 0.0,
                "area": 0.0, "angles": [heel0], "gz": [0.0]}
    ang = list(np.arange(heel0, cap + 1e-9, step))
    if ang[-1] < cap - 1e-9:
        ang.append(cap)
    vals = [gz(barge, float(a), void) for a in ang]
    arr = np.asarray(vals)
    i = int(np.argmax(arr))
    gz_max, a_max = float(arr[i]), float(ang[i])
    rng, a_van = float(cap) - heel0, float(cap)
    for k in range(i, len(arr) - 1):
        if arr[k] > 0.0 >= arr[k + 1]:
            lo, hi, flo = float(ang[k]), float(ang[k + 1]), float(arr[k])
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                fm = gz(barge, mid, void)
                if flo * fm <= 0.0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            a_van = 0.5 * (lo + hi)
            rng = a_van - heel0
            break
    area = 0.0
    for k in range(len(ang) - 1):
        if float(ang[k]) < heel0:
            continue
        if float(ang[k]) > a_van:
            break
        area += 0.5 * (vals[k] + vals[k + 1]) * (ang[k + 1] - ang[k]) * math.pi / 180.0
    return {"gz_max": max(0.0, gz_max), "gz_max_angle": a_max, "range": rng,
            "area": area, "angles": [float(a) for a in ang], "gz": vals,
            "vanishing_angle": a_van}


# ---------------- 解析锚（与上面的数值路线互相独立的第二路） ----------------
def analytic_draft(barge: Barge, void: Void) -> float:
    """损失浮力平行下沉终吃水 t' = L·B·T/(L·B − μ·l·b)（水线仍在舱内时成立）。"""
    return barge.L * barge.B * barge.T / (barge.L * barge.B - void.mu * void.l * void.b)


def closed_form_valid(barge: Barge, void: Void) -> bool:
    """闭式 t' 的适用条件：舱底贴龙骨(z_b=0)且水线未追及舱顶(d ≤ z_t)。"""
    if void is None or void.mu * void.l * void.b <= 0:
        return True
    return abs(void.z_b) < 1e-9 and analytic_draft(barge, void) <= void.z_t + 1e-12


def analytic_gm(barge: Barge, void: Optional[Void]) -> float:
    """损失浮力法初稳性高 GM = t'/2 + I_eff/∇0 − KG。

    t' 取**精确水线**（θ=0 时由断点积分求得）。闭式 t' 只在舱底贴龙骨且未追及舱顶时
    成立，故此处不再用闭式，避免把「舱底有高度 / 舱顶被追上」的几何误当直达龙骨。
    """
    if void is None or void.mu * void.l * void.b <= 0:
        kb = barge.T / 2.0
        i_eff = barge.I_T0
    else:
        alpha = _alpha(barge, void)
        d = solve_waterline(barge, 0.0, void)
        A_i, _, Mz_i, A_v, _, Mz_v = _section(barge, d, 0.0, void)
        kb = (Mz_i - alpha * Mz_v) / (A_i - alpha * A_v)   # 舱底 z_b>0 时 z_B ≠ t'/2
        i_eff = (barge.I_T0 - void.mu * void.l * void.b ** 3 / 12.0
                 - void.mu * void.l * void.b * void.y_c ** 2)
    return kb + i_eff / barge.nabla0 - barge.KG


def wall_sided_gz(barge: Barge, theta_deg: float, gm: float, bm: float) -> float:
    """完整船壁式闭式（甲板边浸没前精确）：GZ = sinθ·(GM + ½·BM·tan²θ)。"""
    t = math.radians(theta_deg)
    return math.sin(t) * (gm + 0.5 * bm * math.tan(t) ** 2)


def deck_edge_angle(barge: Barge, draft: float) -> float:
    """甲板边浸没角 atan(2(D − t)/B)。"""
    fb = barge.D - draft
    if fb <= 0:
        return 0.0
    return math.degrees(math.atan(2.0 * fb / barge.B))


def solas_s_index(gz_max: float, rng_deg: float) -> float:
    """SOLAS 2009 Reg.7.2 生存概率 s（单区、K=1）。"""
    if gz_max < 0.1 or rng_deg < 16.0:
        return 0.0
    if gz_max >= 0.2 and rng_deg >= 20.0:
        return 1.0
    return min(1.0, ((gz_max / 0.12) * (rng_deg / 16.0)) ** 0.25)


def solas_required_index(ls: float) -> float:
    """SOLAS 2009 Reg.6 货船要求分舱指数 R（Ls≥80 m）。"""
    return 1.0 - 128.0 / (ls + 152.0) if ls >= 80.0 else 1.0 - 152.0 / (ls + 152.0)