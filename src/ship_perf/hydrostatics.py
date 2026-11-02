"""稳性：KB/BM/GM 与剖面几何一致；大倾角 GZ 由剖面多边形裁剪积分求得。

修复要点（对应 m1473 审计 D2 / D3，判据 S-KB-BOX / S-GM / S-GZMAX / S-AREA-3040 / S-VANISH）
--------------------------------------------------------------------------------
在线服务两处 KB 模型都与几何不符：
  * ``api/stability.evaluate``: KB = T(1-0.5(1-Cb))  ⇒ Cb=1 时 KB=T（解析值 T/2，超差 +100%）
  * ``api/stability_imo.calculate_kb``: KB = 2T/3（楔形截面值，套在浮箱上）
GZ 侧：``calculate_gz_curve`` 用 KN≈(BM sinφcosφ + KB sinφ)(1+0.001φ) 的启发式，
再乘 (1-0.002(φ-30))、乘 0.95、最后 ``max(0, GZ)`` 截断 ⇒ GZ 恒非负、
不存在 GZ=0 根，"消失角"只能由 ``60 + 10·GM`` 线性式给出（60° 常数封顶）。

本模块的正确做法
----------------
1. KB 由**梯形剖面**（底半宽 B(Cb-0.5)→水线半宽 B/2）一阶矩解析求得：
       KB = T·(1 + 1/(2Cb))/3
   两个解析极限自洽：Cb=1（矩形浮箱）→ T/2；Cb=0.5（V 形楔）→ 2T/3。
2. 大倾角 GZ 用同一梯形剖面 + 甲板封顶的多边形，逐角度按等体积裁剪求解水线，
   取浮心 (y_B,z_B)：GZ = y_B·cosθ - (z_B - KG)·sinθ。甲板入水、舭部出水、
   浮心横移全部自然包含，GZ 可为负 ⇒ 消失角 = GZ=0 的真实根。
3. ``wall_sided_gz`` 给出 wall-sided 解析式（对垂直舷侧船体在甲板入水前**精确**），
   作为审计引用的对照基准。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

Poly = List[Tuple[float, float]]


# ---------------------------------------------------------------- 静水力


def kb_from_cb(t: float, cb: float) -> float:
    """浮心高度 KB（梯形剖面一阶矩解析解）。

    KB/T = (1 + 1/(2Cb))/3；Cb=1→0.5（矩形），Cb=0.5→2/3（楔形）。
    Cb<0.5 时按 2T/3 截断（更瘦的剖面已超出梯形族）。
    """
    cb_eff = max(cb, 0.5)
    return t * (1.0 + 1.0 / (2.0 * cb_eff)) / 3.0


def bm_transverse(b: float, t: float, cb: float) -> float:
    """横稳心半径 BM = I_wl/∇ = B²/(12·T·Cb)（棱柱体模型，满水线面）。"""
    return b * b / (12.0 * t * cb)


def km_transverse(b: float, t: float, cb: float) -> Tuple[float, float, float]:
    kb = kb_from_cb(t, cb)
    bm = bm_transverse(b, t, cb)
    return kb, bm, kb + bm


def gm_transverse(b: float, t: float, cb: float, kg: float) -> float:
    kb, bm, km = km_transverse(b, t, cb)
    return km - kg


def deck_immersion_angle(b: float, t: float, d: float) -> float:
    """甲板边缘入水角（deg）：tanθ = (D-T)/(B/2)；D<=T 时返回 90。"""
    if d <= t:
        return 90.0
    return math.degrees(math.atan(2.0 * (d - t) / b))


# ---------------------------------------------------------------- wall-sided


def wall_sided_gz(angles: Sequence[float], gm: float, bm: float) -> List[float]:
    """GZ_ws(θ) = sinθ·(GM + ½·BM·tan²θ)。垂直舷侧船体甲板入水前精确。"""
    out = []
    for a in angles:
        th = math.radians(a)
        out.append(math.sin(th) * (gm + 0.5 * bm * math.tan(th) ** 2))
    return out


# ---------------------------------------------------------------- 剖面几何


def section_polygon(b: float, t: float, d: float, cb: float) -> Poly:
    """梯形剖面 + 垂直舷侧至甲板 D。水下面积（吃水 T）= Cb·B·T（每米船长）。

    顶点逆时针：(+y_k,0) → (+B/2,T) → (+B/2,D) → (-B/2,D) → (-B/2,T) → (-y_k,0)
    其中 y_k = B(Cb-0.5)（Cb<0.5 时取 0，成为三角形）。
    """
    yk = max(0.0, b * (cb - 0.5))
    d = max(d, t)
    return [
        (yk, 0.0),
        (b / 2.0, t),
        (b / 2.0, d),
        (-b / 2.0, d),
        (-b / 2.0, t),
        (-yk, 0.0),
    ]


def section_polygon_bilge_rounded(
    b: float, t: float, d: float, r_bilge: float, n_arc: int = 24
) -> Poly:
    """圆舭剖面（垂直舷侧 + 平底 + 两个等半径舭圆弧），逆时针多边形。

    底部平段半宽 ``B/2 - r``，舭圆与平底、舷侧相切（圆弧半径 ``r=r_bilge``，
    圆心 ``(±(B/2-r), r)``），舷侧垂直至甲板 ``D``。``r=0`` 退化为矩形剖面
    ``section_polygon(b, t, d, 1.0)``（矩形浮箱极限）。剖面积由裁角精确给出::

        A = B·T - (2 - π/2)·r²        （T > r，两个 1/4 角各裁 r²(1-π/4)）

    有效方形系数 ``Cb_eff = A/(B·T)`` 小于 1：圆舭是从矩形箱体上裁角，
    因此与 ``section_polygon`` 的同主尺度梯形族**不可混用**，等体积水线
    须以 ``section_area`` 求得的面积为目标（见 ``gz_curve_polygon``）。
    """
    if r_bilge < 0.0:
        raise ValueError("r_bilge 必须非负")
    r = min(r_bilge, b / 2.0, t)
    dd = max(d, t)
    yb = b / 2.0 - r  # 底部平段半宽
    poly: Poly = [(yb, 0.0)]
    for i in range(1, n_arc + 1):  # 右舷舭圆弧：圆心 (yb, r)，从 φ=0 到 φ=90°
        ph = 0.5 * math.pi * i / n_arc
        poly.append((yb + r * math.sin(ph), r - r * math.cos(ph)))
    poly.append((b / 2.0, dd))
    poly.append((-b / 2.0, dd))
    poly.append((-b / 2.0, r))
    for i in range(n_arc, 0, -1):  # 左舷舭圆弧：镜像，从舷侧 (φ=90°) 绕回平底
        ph = 0.5 * math.pi * i / n_arc
        poly.append((-(yb + r * math.sin(ph)), r - r * math.cos(ph)))
    poly.append((-yb, 0.0))
    return poly


def _clip(poly: Poly, theta: float, h: float) -> Poly:
    """Sutherland-Hodgman：保留 y·sinθ + z·cosθ <= h 的部分。"""
    s, c = math.sin(theta), math.cos(theta)

    def val(p):
        return p[0] * s + p[1] * c

    out: Poly = []
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        v1, v2 = val(p1), val(p2)
        in1, in2 = v1 <= h, v2 <= h
        if in1:
            out.append(p1)
        if in1 != in2:
            denom = v1 - v2
            if abs(denom) > 1e-15:
                tt = (v1 - h) / denom
                out.append((p1[0] + tt * (p2[0] - p1[0]), p1[1] + tt * (p2[1] - p1[1])))
    return out


def _area_centroid(poly: Poly) -> Tuple[float, float, float]:
    """多边形面积与形心（shoelace）。"""
    if len(poly) < 3:
        return 0.0, 0.0, 0.0
    a2 = 0.0
    cx = 0.0
    cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a2 += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(a2) < 1e-15:
        return 0.0, 0.0, 0.0
    area = abs(a2) / 2.0
    return area, cx / (3.0 * a2), cy / (3.0 * a2)


def immersed_section(
    poly: Poly,
    theta_rad: float,
    area_target: float,
    tol: float = 1e-10,
    iters: int = 120,
) -> Tuple[float, float, float]:
    """按等体积求水线 (y·sinθ+z·cosθ=h) 并返回浸没面积与浮心 (y_B,z_B)。"""
    s, c = math.sin(theta_rad), math.cos(theta_rad)
    vals = [p[0] * s + p[1] * c for p in poly]
    lo, hi = min(vals), max(vals)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        area, _, _ = _area_centroid(_clip(poly, theta_rad, mid))
        if area < area_target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    h = 0.5 * (lo + hi)
    clipped = _clip(poly, theta_rad, h)
    area, y_b, z_b = _area_centroid(clipped)
    return area, y_b, z_b


def gz_curve_polygon(
    poly: Poly,
    area_target: float,
    kg: float,
    angles: Optional[Sequence[float]] = None,
) -> List[float]:
    """通用剖面的大倾角 GZ 曲线（m）：GZ = -y_B·cosθ + (z_B - KG)·sinθ。

    剖面以 z=0 为基线（龙骨）的任意简单多边形给出，浸没面积由等体积条件
    ``immersed_section`` 求解，因而圆舭/曲面剖面（真实船体型线）与
    ``section_polygon`` 的直边梯形族共用同一物理模型。浸没侧为 -y
    （水线半平面 y·sinθ+z·cosθ<=h），浮心 y_B<0，故 GZ 取 -y_B。
    """
    if angles is None:
        angles = [float(a) for a in range(0, 91, 5)]
    out: List[float] = []
    for a in angles:
        th = math.radians(a)
        _, y_b, z_b = immersed_section(poly, th, area_target)
        out.append(-y_b * math.cos(th) + (z_b - kg) * math.sin(th))
    return out


def section_area(poly: Poly) -> float:
    """多边形剖面全高面积（m²，每米船长），含甲板以上封顶部分。"""
    return _area_centroid(poly)[0]


def submerged_section_area(poly: Poly, draft: float) -> float:
    """吃水 ``draft`` 以下的剖面积（m²），并给出等体积 GZ 求解的面积目标。

    对 ``section_polygon_bilge_rounded`` 等非梯形剖面，``Cb·B·T`` 不再是浸没
    面积的正确替身（圆舭是从矩形箱体裁角），必须由几何求得。
    """
    return _area_centroid(_clip(poly, 0.0, draft))[0]


def gz_curve(
    b: float,
    t: float,
    d: float,
    cb: float,
    kg: float,
    angles: Optional[Sequence[float]] = None,
) -> List[float]:
    """大倾角 GZ 曲线（m）：直边梯形族，等价于 ``gz_curve_polygon`` 的特例。"""
    poly = section_polygon(b, t, d, cb)
    return gz_curve_polygon(poly, cb * b * t, kg, angles)


# ---------------------------------------------------------------- 曲线特征


def integrate_area(angles: Sequence[float], gz: Sequence[float], a1: float, a2: float) -> float:
    """梯形积分 ∫_{a1}^{a2} GZ dθ（m·rad），网格外按端点线性插值。"""
    if len(angles) != len(gz) or len(angles) < 2:
        return 0.0

    def interp(a: float) -> float:
        if a <= angles[0]:
            return float(gz[0])
        if a >= angles[-1]:
            return float(gz[-1])
        for i in range(len(angles) - 1):
            if angles[i] <= a <= angles[i + 1]:
                f = (a - angles[i]) / (angles[i + 1] - angles[i])
                return gz[i] + f * (gz[i + 1] - gz[i])
        return float(gz[-1])

    pts = [a for a in angles if a1 < a < a2]
    xs = [a1] + pts + [a2]
    total = 0.0
    for i in range(len(xs) - 1):
        r1, r2 = math.radians(xs[i]), math.radians(xs[i + 1])
        total += 0.5 * (interp(xs[i]) + interp(xs[i + 1])) * (r2 - r1)
    return total


def max_in_band(angles: Sequence[float], gz: Sequence[float], hi: float) -> Tuple[float, float]:
    """[0, hi] 内的 GZ 最大值及其角度。"""
    best_v, best_a = -1e9, 0.0
    for a, v in zip(angles, gz):
        if a <= hi + 1e-9 and v > best_v:
            best_v, best_a = v, a
    return best_v, best_a


def vanishing_angle(
    angles: Sequence[float], gz: Sequence[float], min_cross_deg: float = 5.0
) -> Optional[float]:
    """稳性消失角：GZ 由正变负的第一个根（deg）。无根返回 None。"""
    vmax, amax = max_in_band(angles, gz, max(angles))
    for i in range(len(angles) - 1):
        if angles[i] < amax - 1e-9:
            continue
        v1, v2 = gz[i], gz[i + 1]
        if v1 > 0.0 >= v2:
            f = v1 / (v1 - v2) if (v1 - v2) != 0 else 0.0
            root = angles[i] + f * (angles[i + 1] - angles[i])
            if root >= min_cross_deg:
                return root
    return None


def gz_at(angles: Sequence[float], gz: Sequence[float], a: float) -> float:
    """线性插值取 GZ(a)。"""
    return _interp(angles, gz, a)


def _interp(angles: Sequence[float], gz: Sequence[float], a: float) -> float:
    if a <= angles[0]:
        return float(gz[0])
    if a >= angles[-1]:
        return float(gz[-1])
    for i in range(len(angles) - 1):
        if angles[i] <= a <= angles[i + 1]:
            f = (a - angles[i]) / (angles[i + 1] - angles[i])
            return gz[i] + f * (gz[i + 1] - gz[i])
    return float(gz[-1])


# ---------------------------------------------------------------- 汇总


@dataclass
class StabilityResult:
    kb: float
    bm: float
    km: float
    gm: float
    angles: List[float]
    gz: List[float]
    gz_max: float
    gz_max_angle: float
    vanishing_angle: Optional[float]
    area_0_30: float
    area_30_40: float
    area_0_40: float
    wall_sided: List[float] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def evaluate_stability(
    b: float,
    t: float,
    d: float,
    cb: float,
    kg: float,
    cm: float = 1.0,
    angles: Optional[Sequence[float]] = None,
) -> StabilityResult:
    """完整稳性评估：静水力 + 大倾角 GZ 曲线 + IMO 面积量。"""
    if angles is None:
        angles = [float(a) for a in range(0, 91, 5)]
    angles = list(angles)
    kb, bm, km = km_transverse(b, t, cb)
    gm = km - kg
    gz = gz_curve(b, t, d, cb, kg, angles)
    ws = wall_sided_gz(angles, gm, bm)
    hi = min(40.0, deck_immersion_angle(b, t, d))  # wall-sided 有效域上限
    gz_max, gz_max_angle = max_in_band(angles, gz, hi)
    notes = [f"GZ_max 在 wall-sided 有效域 θ≤{hi:.1f}° 内取极大（甲板入水角与 40° 取小）"]
    return StabilityResult(
        kb=kb,
        bm=bm,
        km=km,
        gm=gm,
        angles=angles,
        gz=gz,
        gz_max=gz_max,
        gz_max_angle=gz_max_angle,
        vanishing_angle=vanishing_angle(angles, gz),
        area_0_30=integrate_area(angles, gz, 0.0, 30.0),
        area_30_40=integrate_area(angles, gz, 30.0, 40.0),
        area_0_40=integrate_area(angles, gz, 0.0, 40.0),
        wall_sided=ws,
        notes=notes,
    )