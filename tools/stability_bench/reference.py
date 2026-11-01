"""
稳性解析基准（Stability analytic benchmarks）
=============================================

本模块提供**独立于被审计平台**的稳性参考解，用于复核船体总体性能平台
`POST /api/v1/calculations/stability` 与 `POST /api/v1/methods/hydrostatics/calculate`
两个端点的物理保真度（验证欠账）。

参考解分三类，可信度递减，调用方必须按类声明误差门限：

R1（精确解，误差 0，仅受数值积分误差影响）
    矩形横剖面驳船（box barge）。当 Cb=Cw=Cm=1 时，水线面为矩形，浮心/稳心
    半径有闭式解，大倾角复原力臂可由剖面几何直接积分得到，**不依赖任何经验系数**：
        ∇ = L·B·T ,  KB = T/2 ,  BM = I/∇ = B²/(12T)
        GZ(φ) = KN(φ) − KG·sinφ
    其中 KN 由恒定排水体积条件下浸水梯形剖面的形心求得（见 `heeled_box_kn`）。
    矩形剖面属 "wall-sided"（舷侧垂直），故 KN 与经典 wall-sided 闭式解
        GZ = ( GM + ½·BM·tan²φ )·sinφ
    在甲板边缘入水 / 舭部出水之前**严格等价**——两法互为交叉校验（见自检）。

R2（半解析，误差量级 ~1e-3 m）
    同上几何 + 数值求积；仅在 R1 有效角域外（甲板浸水、舭部露出）才需要，
    本项目不使用。

R3（经验近似，误差 O(5~15%)，仅作量级一致性检查）
    Morrish/Normand 浮心高度：KB = T·(5/6 − Cb/(3·Cw))
    中剖面型浮心高度：        KB = T·(0.53 + 0.085·(1 − Cm))
    水线面惯性矩系数：        I_wp = Cw·(Cw − 0.1)/12 · L·B³
    **R3 只能用于"是否为离群值"的判断，不能作为 <3% 判据的基准。**

口径（与平台一致）
    角度 φ 单位为度；力臂单位 m；面积判据单位 m·rad。
    倾斜方向：水线在 +y（右舷）侧更深，即 z_w(y) = T + y·tanφ。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

__all__ = [
    "BoxBarge",
    "platform_kb",
    "platform_bm",
    "platform_gz",
    "platform_imo_areas",
    "heeled_box_kn",
    "heeled_box_centroid",
    "wall_sided_gz",
    "morrish_kb",
    "section_type_kb",
    "corrected_inertia_coeff",
    "area_under_curve",
    "box_valid_heel_limit_deg",
]


# ----------------------------------------------------------------------------
# 几何：矩形横剖面驳船
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class BoxBarge:
    """矩形横剖面驳船（精确基准几何）。

    L   垂线间长 (m)
    B   型宽 (m)
    T   吃水 (m)
    D   型深 (m)，用于甲板边缘入水判据
    KG  重心高 (m)
    rho 水密度 (kg/m³)
    """

    L: float
    B: float
    T: float
    D: float
    KG: float
    rho: float = 1025.0

    # --- 精确静水力 ---
    @property
    def volume(self) -> float:
        """排水体积 ∇ = L·B·T（矩形剖面 ⇒ Cb = 1）。"""
        return self.L * self.B * self.T

    @property
    def displacement_t(self) -> float:
        return self.volume * self.rho / 1000.0

    @property
    def kb(self) -> float:
        """精确浮心高 KB = T/2（矩形浸水剖面形心）。"""
        return self.T / 2.0

    @property
    def bm(self) -> float:
        """精确稳心半径 BM = I/∇ = (L·B³/12)/(L·B·T) = B²/(12·T)。"""
        return self.B ** 2 / (12.0 * self.T)

    @property
    def gm(self) -> float:
        return self.kb + self.bm - self.KG

    @property
    def cb(self) -> float:
        return 1.0

    @property
    def cw(self) -> float:
        return 1.0

    @property
    def cm(self) -> float:
        return 1.0


def box_valid_heel_limit_deg(barge: BoxBarge) -> float:
    """wall-sided / 矩形剖面精确解的适用角域上限 (°)。

    两个独立限制取小：
      1) 舭部出水：水线在低舷侧仍与船底相交 ⇒ (B/2)·tanφ ≤ T
      2) 甲板边缘入水：水线在高舷侧不超出舷侧 ⇒ (B/2)·tanφ ≤ D − T
    超过该角域，浸水剖面不再是矩形梯形，本模块的 R1 解不再精确（需 R2/横剖面曲线）。
    """
    lim = min(barge.T, barge.D - barge.T)
    if lim <= 0:
        return 0.0
    return math.degrees(math.atan(2.0 * lim / barge.B))


def _section_moments(barge: "BoxBarge", phi_deg: float, d: float, n: int = 4001):
    """给定水线参数 d（z_w(y) = d + y·tanφ，并裁剪到 [0, D]）的剖面矩。

    返回 (A, ∫y·z_w dy, ∫½·z_w² dy)，单位为 m² / m³。
    """
    tanp = math.tan(math.radians(phi_deg))
    half = barge.B / 2.0
    n = n if n % 2 == 1 else n + 1
    h = barge.B / (n - 1)
    a = m1 = m2 = 0.0
    for i in range(n):
        y = -half + i * h
        z = d + y * tanp
        if z < 0.0:
            z = 0.0
        elif z > barge.D:
            z = barge.D
        w = 1.0 if i in (0, n - 1) else (4.0 if i % 2 == 1 else 2.0)
        a += w * z
        m1 += w * y * z
        m2 += w * 0.5 * z * z
    return a * h / 3.0, m1 * h / 3.0, m2 * h / 3.0


def heeled_box_centroid(barge: "BoxBarge", phi_deg: float, n: int = 4001, tol: float = 1e-12):
    """恒定排水体积（∇ = L·B·T）下横倾 φ 时的浸水剖面形心（数值解）。

    与 `heeled_box_centroid_wallsided` 的区别：这里对浸水剖面做 [0, D] 裁剪，
    并用二分法求解水线参数 d 使湿面积严格等于 B·T，因此**舭部出水**与**甲板浸水**
    之后的角域也可用（通用解）。

    返回 (area, y_centroid, z_centroid)。
    """
    target = barge.B * barge.T
    lo, hi = -2.0 * barge.B, barge.D + 2.0 * barge.B
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        a, _, _ = _section_moments(barge, phi_deg, mid, n=n)
        if a < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    d = 0.5 * (lo + hi)
    a, m1, m2 = _section_moments(barge, phi_deg, d, n=n)
    return a, m1 / a, m2 / a


def heeled_box_centroid_wallsided(barge: "BoxBarge", phi_deg: float, n: int = 20001):
    """wall-sided 角域内的闭式水线（d = T·cosφ）形心解，供通用解交叉校验。

    水线 z_w(y) = T + y·tanφ，有效域见 `box_valid_heel_limit_deg`。
    """
    tanp = math.tan(math.radians(phi_deg))
    half = barge.B / 2.0
    n = n if n % 2 == 1 else n + 1
    h = barge.B / (n - 1)
    a = m1 = m2 = 0.0
    for i in range(n):
        y = -half + i * h
        z = barge.T + y * tanp
        w = 1.0 if i in (0, n - 1) else (4.0 if i % 2 == 1 else 2.0)
        a += w * z
        m1 += w * y * z
        m2 += w * 0.5 * z * z
    a *= h / 3.0
    m1 *= h / 3.0
    m2 *= h / 3.0
    return a, m1 / a, m2 / a


def _kn_from_centroid(ybar: float, zbar: float, phi_deg: float) -> float:
    phi = math.radians(phi_deg)
    return ybar * math.cos(phi) + zbar * math.sin(phi)


def heeled_box_kn(barge: "BoxBarge", phi_deg: float, n: int = 4001) -> float:
    """KN(φ)：龙骨中心线 K 到浮心作用线的横向距离 (m)，通用（含甲板浸水）。"""
    _, ybar, zbar = heeled_box_centroid(barge, phi_deg, n=n)
    return _kn_from_centroid(ybar, zbar, phi_deg)


def heeled_box_gz(barge: "BoxBarge", phi_deg: float, n: int = 4001) -> float:
    """R1 精确复原力臂 GZ(φ) = KN(φ) − KG·sinφ (m)（通用解）。"""
    return heeled_box_kn(barge, phi_deg, n=n) - barge.KG * math.sin(math.radians(phi_deg))


def full_submersion_angle_deg(barge: "BoxBarge") -> float:
    """甲板边缘入水角 (°)：φ_deck = atan((D − T)/(B/2))；超过后精确解仍可算但已非
    常规完整稳性评估域。"""
    if barge.D <= barge.T:
        return 0.0
    return math.degrees(math.atan((barge.D - barge.T) / (barge.B / 2.0)))


def wall_sided_gz(phi_deg: float, gm: float, bm: float) -> float:
    """经典 wall-sided 复原力臂：GZ = (GM + ½·BM·tan²φ)·sinφ (m)。

    对矩形剖面在 `box_valid_heel_limit_deg` 内与 `heeled_box_gz` 严格等价
    （本项目自检断言两者偏差 < 1e-3 m）。
    """
    phi = math.radians(phi_deg)
    return (gm + 0.5 * bm * math.tan(phi) ** 2) * math.sin(phi)


# ----------------------------------------------------------------------------
# R3：经验近似（仅作量级一致性检查）
# ----------------------------------------------------------------------------
def morrish_kb(T: float, Cb: float, Cw: float) -> float:
    """Morrish/Normand 浮心高：KB = T·(5/6 − Cb/(3·Cw))。

    对矩形剖面（Cb=Cw=1）严格给出 T/2，与精确解一致。
    """
    return T * (5.0 / 6.0 - Cb / (3.0 * Cw))


def section_type_kb(T: float, Cm: float) -> float:
    """中剖面型浮心高（平台 stability_hydrostatics 模块所用）：T·(0.53+0.085·(1−Cm))。"""
    return T * (0.53 + 0.085 * (1.0 - Cm))


def corrected_inertia_coeff(Cw: float) -> float:
    """修正水线面惯性矩系数 I_wp = C_I·L·B³ 中的 C_I = Cw·(Cw−0.1)/12。

    矩形水线面（Cw=1）时 C_I = 0.075，而精确值 1/12 ≈ 0.0833 ⇒ 该经验系数在
    Cw→1 端固有 −10% 偏差；其标定域是常规船型 Cw ≈ 0.75~0.85。
    """
    return Cw * (Cw - 0.1) / 12.0


# ----------------------------------------------------------------------------
# 被审计平台的**现役实现**（逐字复刻，用于离线可复现的差值断言）
# ----------------------------------------------------------------------------
def platform_kb(T: float, Cb: float) -> float:
    """平台 `backend/api/calculations.py::calculate_kb`：KB = T·(1 − Cb/3)。"""
    return T * (1.0 - Cb / 3.0)


def platform_bm(B: float, T: float, Cb: float) -> float:
    """平台 `calculate_bm`：CWP = 0.75+0.25·Cb；BM = CWP²·B²/(12·T·Cb)。

    注：I_T = CWP²·B³/12（单位船长），∇ = B·T·Cb（单位船长）。
    """
    cwp = 0.75 + 0.25 * Cb
    return cwp ** 2 * B ** 3 / 12.0 / (B * T * Cb)


def platform_gz(phi_deg: float, gm: float, kb: float, bm: float, kg: float, B: float) -> float:
    """平台 `calculate_gz_curve` 逐字复刻。

    φ < 15°  : GZ = GM·sinφ
    φ ≥ 15°  : GZ = (GM + ½·BM·sin²φ)·sinφ   ← 用 sin²φ 而非 tan²φ
    φ > 40°  : 再乘人为线性收敛因子 (60−φ)/20
    最后 max(0, GZ) 截断。
    """
    phi = math.radians(phi_deg)
    if phi_deg < 15.0:
        gz = gm * math.sin(phi)
    else:
        gz = (gm + 0.5 * bm * math.sin(phi) ** 2) * math.sin(phi)
        if phi_deg > 40.0:
            gz *= (60.0 - phi_deg) / 20.0
    return max(0.0, gz)


def platform_imo_areas(gz_curve: dict[float, float]):
    """平台 IMO 面积判据逐字复刻（5° 梯形积分，含其区间判据）。

    返回 (area_0_30, area_30_40, area_0_40)，单位 m·rad。
    """
    angles = sorted(gz_curve)
    gz = [gz_curve[a] for a in angles]

    def trap(start: float, end: float) -> float:
        acc = 0.0
        for i in range(len(angles) - 1):
            if angles[i] >= start and angles[i + 1] <= end:
                acc += 0.5 * (gz[i] + gz[i + 1]) * math.radians(5.0)
        return acc

    a0_30, a30_40 = trap(0.0, 30.0), trap(30.0, 40.0)
    return a0_30, a30_40, a0_30 + a30_40


# ----------------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------------
def area_under_curve(xs: Sequence[float], ys: Sequence[float], a: float, b: float,
                     samples: int = 401) -> float:
    """∫_a^b y dx，单位 rad（x 为度）。线性插值 + 细网格梯形积分。

    与平台 5° 粗梯形积分对比，可量化"积分分辨率"贡献的面积偏差。
    """
    pts = [(x, y) for x, y in zip(xs, ys)]

    def interp(x: float) -> float:
        if x <= pts[0][0]:
            return pts[0][1]
        if x >= pts[-1][0]:
            return pts[-1][1]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if x0 <= x <= x1:
                if x1 == x0:
                    return y0
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        return pts[-1][1]

    h = (b - a) / (samples - 1)
    acc = 0.0
    for i in range(samples):
        x = a + i * h
        w = 0.5 if i in (0, samples - 1) else 1.0
        acc += w * interp(x)
    return acc * h * math.pi / 180.0


def rel_err(model: float, ref: float) -> float:
    """相对误差 (model − ref)/|ref|，无量纲（×100 得 %）。"""
    if ref == 0:
        return float("inf") if model != 0 else 0.0
    return (model - ref) / abs(ref)

# ----------------------------------------------------------------------------
# 第三路参考解：多边形裁剪（计算几何，与前两路的代数路径完全独立）
# ----------------------------------------------------------------------------
def clip_rect_section(barge: "BoxBarge", phi_deg: float):
    """矩形剖面与半平面 z ≤ T + y·tanφ 的交集多边形（Sutherland–Hodgman 裁剪）。

    返回顶点列表 [(y, z), ...]（逆时针）。此路径只用计算几何（凸多边形裁剪 +
    鞋带公式），不涉及任何剖面矩积分代数，因此可与 `heeled_box_centroid`、
    `wall_sided_gz` 构成三路互校验。
    """
    tanp = math.tan(math.radians(phi_deg))
    half = barge.B / 2.0

    def inside(p) -> bool:
        y, z = p
        return z <= barge.T + y * tanp + 1e-12

    def intersect(p, q):
        y1, z1 = p
        y2, z2 = q
        f1 = z1 - (barge.T + y1 * tanp)
        f2 = z2 - (barge.T + y2 * tanp)
        if f2 == f1:
            return p
        t = f1 / (f1 - f2)
        return (y1 + t * (y2 - y1), z1 + t * (z2 - z1))

    poly = [(-half, 0.0), (half, 0.0), (half, barge.D), (-half, barge.D)]
    out = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        cur_in, prev_in = inside(cur), inside(prev)
        if cur_in:
            if not prev_in:
                out.append(intersect(prev, cur))
            out.append(cur)
        elif prev_in:
            out.append(intersect(prev, cur))
    return out


def polygon_area_centroid(poly):
    """鞋带公式：多边形面积与形心 (y, z)。"""
    a = cy = cz = 0.0
    n = len(poly)
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    a *= 0.5
    if a == 0:
        return 0.0, 0.0, 0.0
    return a, cy / (6.0 * a), cz / (6.0 * a)


def polygon_kn(barge: "BoxBarge", phi_deg: float) -> float:
    """KN(φ) 的几何路径实现：裁剪多边形形心投影到横向水平轴。"""
    poly = clip_rect_section(barge, phi_deg)
    area, ybar, zbar = polygon_area_centroid(poly)
    phi = math.radians(phi_deg)
    return ybar * math.cos(phi) + zbar * math.sin(phi)


def polygon_gz(barge: "BoxBarge", phi_deg: float) -> float:
    """GZ(φ) 的几何路径实现 GZ = KN_poly − KG·sinφ (m)。"""
    return polygon_kn(barge, phi_deg) - barge.KG * math.sin(math.radians(phi_deg))
