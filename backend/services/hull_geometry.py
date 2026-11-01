"""
Hull geometry primitives with *auditable* reference models (m1478 validation debt).

背景
----
`api/calculations.py` 早期用单一条经验公式估算湿表面积::

    S = pi * B * L * (0.5 + 0.25 * Cb)

该式对任何可验证的船型都不成立：长度量纲上它永远返回 ``0.75*pi*B*L``
(Cb=1) 或 ``0.5*pi*B*L`` (Cb=0)，与真实几何（方盒、圆柱、球、胶囊体）
的相对误差可达 -37% ~ +20%，而摩擦阻力 ``Rf`` 正比于 ``S``，误差直接传递
到总阻力。本模块提供三类"可核验"的参照：

1. ``exact_wetted_surface`` —— 四个规范形体（方盒 / 圆柱 / 球 / 胶囊体）的
   **闭式**湿表面积与闭式 Cb。误差局限在浮点舍入，可作为 0% 基准。
2. ``holtrop_wetted_surface`` —— Holtrop & Mennen (1982) 湿表面积回归式
   （商船适用，0.55 <= Cb <= 0.85），作为文献基准。
3. ``box_gz_numeric`` / ``wall_sided_gz`` —— 横剖面为矩形的"直舷"船体，
   在给定横倾角下的复原力臂。前者用**多边形裁剪 + 浮心矩积分**独立数值
   求解（不依赖任何闭式公式），后者是解析 wall-sided 公式；两者互为验证，
   并给出 wall-sided 公式的**适用横倾角上限**（甲板边缘入水 / 龙骨出水）。

所有函数纯 python、无外部依赖、单次调用 < 5 ms，可用于单元测试。
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

# 规范形体的湿表面积 / Cb 都是相对"外接方盒" L x B x T 定义的。
# 对潜没回转体取 T = B（直径竖直方向），保证 Cb <= 1 与 API 约束一致。
Y_OFFSET_EPS = 1e-12

FORM_ALIASES = {
    "ship": "ship",
    "generic": "ship",
    "merchant": "ship",
    "box": "box",
    "barge": "box",
    "cylinder": "cylinder",
    "sphere": "sphere",
    "capsule": "capsule",
    "auto": "auto",
}


def normalise_form(form: str) -> str:
    key = (form or "auto").strip().lower()
    if key not in FORM_ALIASES:
        raise ValueError(
            f"unknown hull form '{form}'; expected one of {sorted(set(FORM_ALIASES))}"
        )
    return FORM_ALIASES[key]


# ---------------------------------------------------------------------------
# 1. 规范形体：闭式湿表面积 + 闭式 Cb（0% 误差基准）
# ---------------------------------------------------------------------------

def form_cb(form: str, L: float, B: float, T: float) -> float:
    """规范形体的闭式方形系数（体积 / (L*B*T)）。"""
    f = normalise_form(form)
    if f == "box":
        return 1.0
    if f == "cylinder":
        # 卧式圆柱：体积 = pi*R^2*L，外接方盒 L*B*T，R = B/2，T = B
        return math.pi * B / (4.0 * T)
    if f == "sphere":
        # 球：直径 B = T = L = 2R
        vol = 4.0 / 3.0 * math.pi * (B / 2.0) ** 3
        return vol / (L * B * T)
    if f == "capsule":
        R = B / 2.0
        vol = math.pi * R * R * L + 4.0 / 3.0 * math.pi * R ** 3
        return vol / (L * B * T)
    raise ValueError(f"hull form '{form}' has no closed-form Cb; use 'ship'")


def exact_wetted_surface(form: str, L: float, B: float, T: float) -> float:
    """规范形体的闭式湿表面积 (m^2)。

    - box     : 浮于水面的方驳：S = L*B + 2*T*(L+B)
    - cylinder: 潜没卧式圆柱（端面计入）：S = 2*pi*R*L + 2*pi*R^2, R = B/2
    - sphere  : S = 4*pi*R^2, R = B/2
    - capsule : 圆柱 + 两端半球：S = 2*pi*R*L + 4*pi*R^2
    """
    f = normalise_form(form)
    if f == "box":
        return L * B + 2.0 * T * (L + B)
    R = B / 2.0
    if f == "cylinder":
        return 2.0 * math.pi * R * L + 2.0 * math.pi * R * R
    if f == "sphere":
        return 4.0 * math.pi * R * R
    if f == "capsule":
        return 2.0 * math.pi * R * L + 4.0 * math.pi * R * R
    raise ValueError(f"hull form '{form}' has no closed-form wetted surface; use 'ship'")


# ---------------------------------------------------------------------------
# 2. 文献基准：Holtrop & Mennen (1982) 湿表面积
# ---------------------------------------------------------------------------

def holtrop_wetted_surface(
    L: float,
    B: float,
    T: float,
    Cb: float,
    Cm: float = 0.98,
    Cwp: float | None = None,
    abt: float = 0.0,
) -> float:
    """Holtrop & Mennen (1982) 湿表面积回归式 (无球鼻艏时 abt=0)。

    S = L(2T+B)*sqrt(Cm)*(0.453 + 0.4425Cb - 0.2862Cm
        - 0.003467*B/T + 0.3696Cwp) + 2.38*Abt/Cb

    Cwp 缺省取 Schneekluth 近似 Cwp = (1 + 2Cb)/3。
    """
    if Cwp is None:
        Cwp = (1.0 + 2.0 * Cb) / 3.0
    bracket = (
        0.453
        + 0.4425 * Cb
        - 0.2862 * Cm
        - 0.003467 * (B / T)
        + 0.3696 * Cwp
    )
    return L * (2.0 * T + B) * math.sqrt(Cm) * bracket + 2.38 * abt / Cb


def denny_mumford_wetted_surface(L: float, B: float, T: float, Cb: float) -> float:
    """Denny-Mumford (SNAME) 湿表面积近似：S = L(1.7T + Cb*B)。

    仅作交叉参考（文献另一条回归），不作平台主模型。
    """
    return L * (1.7 * T + Cb * B)


# ---------------------------------------------------------------------------
# 3. 统一入口 + 几何一致性校验
# ---------------------------------------------------------------------------

def resolve_form(form: str, Cb: float) -> str:
    """把 'auto' 解析成具体形体：Cb>=0.98 视为直壁方盒，否则按商船处理。"""
    f = normalise_form(form)
    if f != "auto":
        return f
    return "box" if Cb >= 0.98 else "ship"


def wetted_surface(
    form: str, L: float, B: float, T: float, Cb: float
) -> Tuple[float, str]:
    """返回 (湿表面积 m^2, 模型名)。规范形体走闭式解，其余走 Holtrop 回归。"""
    f = resolve_form(form, Cb)
    if f in ("box", "cylinder", "sphere", "capsule"):
        return exact_wetted_surface(f, L, B, T), f"exact:{f}"
    return holtrop_wetted_surface(L, B, T, Cb), "holtrop1982"


def legacy_wetted_surface(L: float, B: float, T: float, Cb: float) -> float:
    """m1478 之前 api/calculations.py 使用的经验式（保留用于回归对照）。"""
    return math.pi * B * L * (0.5 + 0.25 * Cb)


def verify_geometry(
    form: str,
    L: float,
    B: float,
    T: float,
    Cb: float,
    rel_tol: float = 0.02,
) -> Dict[str, object]:
    """几何一致性校验（平台规则：几何必须先验证 Cb/L/B）。

    对规范形体，Cb 由 L/B/T 唯一决定，输入 Cb 与闭式值偏差 > rel_tol 时报错；
    对 'ship'，检查 Holtrop 适用域 Cb in [0.55, 0.85] 并给出警告（不阻断）。
    """
    if not (0.0 < Cb <= 1.0):
        raise ValueError(f"Cb={Cb} 超出物理范围 (0, 1]")
    if L <= 0 or B <= 0 or T <= 0:
        raise ValueError("L/B/T 必须为正")
    f = resolve_form(form, Cb)
    info: Dict[str, object] = {"form": f, "warnings": []}

    if f in ("box", "cylinder", "sphere", "capsule"):
        cb_ref = form_cb(f, L, B, T)
        err = abs(Cb - cb_ref) / cb_ref
        info["cb_form"] = round(cb_ref, 6)
        info["cb_rel_error"] = round(err, 6)
        if err > rel_tol:
            raise ValueError(
                f"几何不一致: form={f} 时 Cb 由 L/B/T 唯一确定为 {cb_ref:.4f}，"
                f"输入 Cb={Cb:.4f} 偏差 {err*100:.1f}% > {rel_tol*100:.0f}%"
            )
    else:
        if not (0.55 <= Cb <= 0.85):
            info["warnings"].append(
                f"Holtrop 湿表面积式适用域 0.55<=Cb<=0.85，当前 Cb={Cb:.3f} 超出，"
                "结果不确定性放大（建议提供型值表直接积分）"
            )
        if not (3.0 <= L / B <= 12.0):
            info["warnings"].append(f"L/B={L/B:.2f} 超出商船常规 3~12")
    return info


# ---------------------------------------------------------------------------
# 4. 复原力臂：解析 wall-sided 公式 vs 独立数值解（矩形横剖面）
# ---------------------------------------------------------------------------

def wall_sided_gz(GM: float, BM: float, phi_rad: float) -> float:
    """壁舷（直舷）船体复原力臂闭式解：GZ = sin(phi)*(GM + 0.5*BM*tan^2(phi))。"""
    if abs(phi_rad) >= math.pi / 2:
        raise ValueError("wall-sided 公式在 |phi| >= 90 deg 时发散")
    return math.sin(phi_rad) * (GM + 0.5 * BM * math.tan(phi_rad) ** 2)


def wall_sided_limit_rad(B: float, T: float, depth: float) -> float:
    """wall-sided 假设的横倾角上限 (rad)。

    假设要求 (a) 甲板边缘未入水: T + (B/2)tan(phi) <= depth；(b) 低舷侧龙骨
    仍浸没: T - (B/2)tan(phi) >= 0。故 tan(phi) < min(2(depth-T)/B, 2T/B)。
    """
    tan_lim = min(2.0 * (depth - T) / B, 2.0 * T / B)
    if tan_lim <= 0:
        return 0.0
    return math.atan(tan_lim)


def _clip_halfplane(poly, z_max):
    """Sutherland-Hodgman: 保留 z <= z_max 的部分。"""
    out = []
    n = len(poly)
    for i in range(n):
        y1, z1 = poly[i]
        y2, z2 = poly[(i + 1) % n]
        in1, in2 = z1 <= z_max, z2 <= z_max
        if in1:
            out.append((y1, z1))
        if in1 != in2:
            t = (z_max - z1) / (z2 - z1)
            out.append((y1 + t * (y2 - y1), z_max))
    return out


def _poly_area_centroid(poly):
    a = cy = cz = 0.0
    n = len(poly)
    for i in range(n):
        y1, z1 = poly[i]
        y2, z2 = poly[(i + 1) % n]
        cross = y1 * z2 - y2 * z1
        a += cross
        cy += (y1 + y2) * cross
        cz += (z1 + z2) * cross
    a *= 0.5
    if abs(a) < 1e-15:
        return 0.0, 0.0, 0.0
    return abs(a), cy / (6.0 * a), cz / (6.0 * a)


def box_section_polygon(B: float, depth: float):
    return [(-B / 2.0, 0.0), (B / 2.0, 0.0), (B / 2.0, depth), (-B / 2.0, depth)]


def box_gz_numeric(
    B: float, T: float, depth: float, KG: float, phi_deg: float, iters: int = 80
) -> float:
    """矩形横剖面船体的复原力臂数值解（独立于 wall-sided 公式）。

    做法：把横剖面绕 x 轴（方向：向右舷横倾 phi）旋转到世界系，二分水线高度
    z_w 使浸没面积严格等于 B*T（等排水量），再用多边形形心得到浮心 (Y_B, Z_B)，
    最后 GZ = Y_B - Y_G（G 在中心线上，Y_G = KG*sin(phi)）。
    """
    phi = math.radians(phi_deg)
    c, s = math.cos(phi), math.sin(phi)
    # body -> world: Y = u*c + w*s ; Z = -u*s + w*c
    poly = [(u * c + w * s, -u * s + w * c) for u, w in box_section_polygon(B, depth)]
    target = B * T
    z_lo = min(p[1] for p in poly) - 1e-6
    z_hi = max(p[1] for p in poly) + 1e-6
    for _ in range(iters):
        z_mid = 0.5 * (z_lo + z_hi)
        area, _, _ = _poly_area_centroid(_clip_halfplane(poly, z_mid))
        if area < target:
            z_lo = z_mid
        else:
            z_hi = z_mid
    area, y_b, z_b = _poly_area_centroid(_clip_halfplane(poly, 0.5 * (z_lo + z_hi)))
    y_g = KG * s
    return y_b - y_g


def box_hydrostatics(B: float, T: float, KG: float) -> Dict[str, float]:
    """矩形横剖面（wall-sided）船体的静水力学量：KB、BM、KM、GM。"""
    kb = T / 2.0
    bm = B * B / (12.0 * T)
    return {"KB": kb, "BM": bm, "KM": kb + bm, "GM": kb + bm - KG}