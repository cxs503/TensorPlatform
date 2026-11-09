"""真实型线截面模型（P66-W2-N02）。

本模块把「壁直（wall-sided）矩形截面」升级为**真实型线**数值截面：

* 每个站位 x 的截面由参数族描述——局部水线半宽 ``b(x)``、舭部半径 ``R(x) = r·b(x)``、
  吃水 ``T``、型深 ``D``，以及水线以上外飘（flare）；
* 水下截面面积有**闭式解**（舭圆弧段用 ``πR²/4`` 精确表示）：

      A(x) = 2·b·T − 2·r²·(1 − π/4)·b²        （当 T ≥ R 时精确）

  因此 Cb 的拟合（对进流/去流丰满度指数 q 求根）可在机器精度内闭合；
* 同一截面同时给出**离散多边形**（舭圆弧离散为 n_arc 段），供横倾后的多边形裁剪
  数值积分使用——这是把闭式 GZ 换成真实型线数值积分的入口。

几何门（平台铁律）：``geometry_gate()`` 回传 Cb/L/B 三个相对残差，要求全部 < 1e-12。

单位：SI（m, m³, m²）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

RHO_W = 1025.0                        # kg/m³ 海水
QUARTER_BILGE = 1.0 - math.pi / 4.0   # 1 − π/4

Point = Tuple[float, float]           # (y, z)：横剖面坐标，y 向右舷为正，z 向上
Polygon = List[Point]


# ---------------------------------------------------------------------------
# 多边形工具（凸多边形：截面与包围盒均为凸域，逐半平面裁剪 = 求交）
# ---------------------------------------------------------------------------


def clip_halfplane(poly: Polygon, a: float, b: float, c: float) -> Polygon:
    """保留满足 ``a·y + b·z ≤ c`` 的部分（Sutherland–Hodgman 单半平面裁剪）。"""
    if not poly:
        return []
    out: Polygon = []
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        f0 = a * y0 + b * z0 - c
        f1 = a * y1 + b * z1 - c
        if f0 <= 0.0:
            out.append((y0, z0))
        if (f0 < 0.0 < f1) or (f1 < 0.0 < f0):
            t = f0 / (f0 - f1)
            out.append((y0 + t * (y1 - y0), z0 + t * (z1 - z0)))
    return out


def clip_box(poly: Polygon, y_lo: float, y_hi: float,
             z_lo: float, z_hi: float) -> Polygon:
    """裁剪到轴对齐盒子 [y_lo,y_hi]×[z_lo,z_hi]。"""
    p = clip_halfplane(poly, 1.0, 0.0, y_hi)
    p = clip_halfplane(p, -1.0, 0.0, -y_lo)
    p = clip_halfplane(p, 0.0, 1.0, z_hi)
    p = clip_halfplane(p, 0.0, -1.0, -z_lo)
    return p


def clip_waterline(poly: Polygon, level: float) -> Polygon:
    """保留 ``z ≤ level``（用空间水平面裁剪，用于淹没域）。"""
    return clip_halfplane(poly, 0.0, 1.0, level)


def area_centroid(poly: Polygon) -> Tuple[float, float, float]:
    """返回 (面积, 形心 y, 形心 z)；面积取绝对值。"""
    n = len(poly)
    if n < 3:
        return 0.0, 0.0, 0.0
    a2 = cy = cz = 0.0
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a2 += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    if abs(a2) < 1e-18:
        return 0.0, 0.0, 0.0
    sign = 1.0 if a2 > 0 else -1.0
    return abs(0.5 * a2), sign * cy / (3.0 * a2), sign * cz / (3.0 * a2)


def rotate(poly: Polygon, phi_rad: float) -> Polygon:
    """绕纵轴（中龙骨线）旋转，使船体向 starboard 横倾 φ。"""
    c = math.cos(phi_rad)
    s = math.sin(phi_rad)
    return [(y * c + z * s, -y * s + z * c) for (y, z) in poly]


# ---------------------------------------------------------------------------
# 截面参数族
# ---------------------------------------------------------------------------


def _F(u: float) -> float:
    u = max(-1.0, min(1.0, u))
    return 0.5 * (u * math.sqrt(max(0.0, 1.0 - u * u)) + math.asin(u))


@dataclass
class SectionParams:
    """单站位截面：局部半宽 b、舭部半径比 r_bilge（R = r·b）、吃水 T、型深 D、外飘角。"""

    b: float
    r_bilge: float
    T: float
    D: float
    flare_deg: float = 0.0

    @property
    def R(self) -> float:
        return self.r_bilge * self.b

    def area_sub_exact(self) -> float:
        """水下截面面积闭式解。"""
        b, R, T = self.b, self.R, self.T
        if b <= 0.0:
            return 0.0
        if T >= R:
            return 2.0 * b * T - 2.0 * self.r_bilge ** 2 * QUARTER_BILGE * b * b
        # 极瘦削端部：弦高 T 的舭圆圆缺
        return 2.0 * ((b - R) * T + R * R * (_F((T - R) / R) - _F(-1.0)))

    def half_breadth(self, z: float) -> float:
        """z 处半宽（0 ≤ z ≤ D）。"""
        b, R = self.b, self.R
        if z <= 0.0:
            return 0.0
        if z <= R and R > 0.0:
            return b - R + math.sqrt(max(0.0, R * R - (z - R) ** 2))
        if z <= self.T:
            return b
        return b + (z - self.T) * math.tan(math.radians(self.flare_deg))

    def chain(self, n_arc: int = 48) -> Polygon:
        """右舷半边开链：龙骨中轴 (0,0) → 平底 → 舭圆弧 → 直舷 → 甲板 → (0,D)。"""
        b, R, T, D = self.b, self.R, self.T, self.D
        if b <= 1e-12:
            return []
        pts: Polygon = [(0.0, 0.0)]
        if R > 1e-12:
            pts.append((b - R, 0.0))
            for i in range(1, n_arc + 1):
                th = -0.5 * math.pi + 0.5 * math.pi * i / n_arc
                pts.append((b - R + R * math.cos(th), R + R * math.sin(th)))
        else:
            pts.append((b, 0.0))
        if self.flare_deg != 0.0 and D > T:
            pts.append((b, T))
            pts.append((self.half_breadth(D), D))
        else:
            pts.append((b, D))
        pts.append((0.0, D))
        return pts

    def polygon(self, n_arc: int = 48) -> Polygon:
        """完整截面凸多边形（逆时针，含左右舷）。"""
        star = self.chain(n_arc)
        if not star:
            return []
        mid = star[1:-1]
        return [(0.0, 0.0)] + mid + [(0.0, star[-1][1])] + [(-y, z) for (y, z) in reversed(mid)]


# ---------------------------------------------------------------------------
# 船体线型
# ---------------------------------------------------------------------------


@dataclass
class HullLines:
    """对称型线船体：b(x) = (B/2)·f(x)，平行中体 f=1，两端 cos^q 收窄，q 由 Cb 求根。"""

    L: float
    B: float
    T: float
    D: float
    Cb: float
    r_bilge: float = 0.18
    p_body: float = 0.50
    flare_deg: float = 0.0
    n_station: int = 41
    n_arc: int = 48
    q: float = 1.0

    # ---------- 半宽分布 ----------
    def fullness(self, x: float) -> float:
        half = 0.5 * self.L
        xi = abs(x - half)
        lp = 0.5 * self.p_body * self.L
        le = max(1e-9, half - lp)
        if xi <= lp:
            return 1.0
        t = min(1.0, (xi - lp) / le)
        return math.cos(0.5 * math.pi * t) ** self.q

    def half_beam(self, x: float) -> float:
        return 0.5 * self.B * self.fullness(x)

    def section(self, x: float) -> SectionParams:
        return SectionParams(b=self.half_beam(x), r_bilge=self.r_bilge,
                             T=self.T, D=self.D, flare_deg=self.flare_deg)

    def station_x(self) -> List[float]:
        return [self.L * i / (self.n_station - 1) for i in range(self.n_station)]

    # ---------- 排水体积（闭式截面 + 复合 Simpson） ----------
    def sectional_area(self, x: float) -> float:
        return self.section(x).area_sub_exact()

    def volume(self) -> float:
        xs = self.station_x()
        return simpson(xs, [self.sectional_area(x) for x in xs])

    def volume_target(self) -> float:
        return self.Cb * self.L * self.B * self.T

    def solve_fullness(self, tol: float = 1e-15) -> float:
        """对 q 求根使 ∫A dx = Cb·L·B·T（体积对 q 单调递减）。"""
        lo, hi = 1e-3, 60.0
        target = self.volume_target()
        for _ in range(300):
            mid = 0.5 * (lo + hi)
            self.q = mid
            v = self.volume()
            if abs(v - target) <= tol * target:
                break
            if v > target:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-15:
                break
        return self.q

    # ---------- 几何门 ----------
    def geometry_gate(self) -> Dict[str, float]:
        vol = self.volume()
        cb_geom = vol / (self.L * self.B * self.T)
        xs = self.station_x()
        l_geom = xs[-1] - xs[0]
        b_geom = 2.0 * max(self.half_beam(x) for x in xs)
        return {
            "cb_declared": self.Cb,
            "cb_geometry": cb_geom,
            "cb_rel_err": abs(cb_geom - self.Cb) / self.Cb,
            "L_declared": self.L,
            "L_geometry": l_geom,
            "L_rel_err": abs(l_geom - self.L) / self.L,
            "B_declared": self.B,
            "B_geometry": b_geom,
            "B_rel_err": abs(b_geom - self.B) / self.B,
            "volume_m3": vol,
            "displacement_t": vol * RHO_W / 1000.0,
            "q_fullness": self.q,
        }

    def polygons(self) -> List[Polygon]:
        return [self.section(x).polygon(self.n_arc) for x in self.station_x()]

    def to_dict(self) -> Dict[str, float]:
        return {"L": self.L, "B": self.B, "T": self.T, "D": self.D, "Cb": self.Cb,
                "q_fullness": self.q, "r_bilge": self.r_bilge,
                "p_body": self.p_body, "flare_deg": self.flare_deg}


def box_hull(L: float, B: float, T: float, D: float) -> HullLines:
    """壁直矩形浮箱（舭部半径 0、平行中体 100%、Cb=1）。用作闭式 GZ 的精确对照。"""
    return HullLines(L=L, B=B, T=T, D=D, Cb=1.0, r_bilge=0.0, p_body=1.0,
                     flare_deg=0.0, q=1.0)


def simpson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """复合 Simpson（等距）；点数为偶数时尾部退化为梯形。"""
    n = len(xs)
    if n < 2:
        return 0.0
    if n % 2 == 0:
        h = xs[1] - xs[0]
        return simpson(xs[:-1], ys[:-1]) + 0.5 * h * (ys[-2] + ys[-1])
    h = (xs[-1] - xs[0]) / (n - 1)
    s = ys[0] + ys[-1]
    for i in range(1, n - 1):
        s += (4.0 if i % 2 else 2.0) * ys[i]
    return s * h / 3.0