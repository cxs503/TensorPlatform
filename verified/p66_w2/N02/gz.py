"""横倾多边形数值积分 GZ（替代壁直闭式）与闭式对照（P66-W2-N02）。

方法（KN 型横稳性，无纵倾假设）
--------------------------------
对给定横倾角 φ：把每个站位的**真实型线截面多边形**绕纵轴旋转 φ（船体横倾），
用空间水平面 ``z_s = d`` 裁剪得到淹没域，逐站位积分：

    V(d,φ)  = ∫ a_i dx            （Simpson）
    y_B(d,φ)= ∫ a_i·y_i dx / V
    z_B(d,φ)= ∫ a_i·z_i dx / V

``d`` 由浮力平衡 V(d,φ) = V_target 求根。重心 G 在空间中的位置为（G 在船中线上）：
``y_G = KG·sinφ``，故

    GZ(φ) = y_B(d,φ) − KG·sinφ

壁直闭式（对照量）：``GZ_ws(φ) = sinφ·(GM + ½·BM·tan²φ)``，对**矩形截面浮箱**在
「舭部入水 / 甲板边缘未入水」区间内**精确**（``api.stability_core.wall_sided_gz``
为平台单源实现，本模块直接引用，不复制公式）。
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .hull_lines import (
    HullLines, Polygon, RHO_W, area_centroid, clip_waterline, rotate, simpson,
)

# ---- 平台单源：壁直闭式 GZ 与 BM 公式（backend/api/stability_core.py） ----
try:  # pragma: no cover - 取决于导入路径
    from api.stability_core import wall_sided_gz, wall_sided_limit_deg
except ModuleNotFoundError:  # 以 repo/backend 为根运行时的回退导入
    _BACKEND = Path(__file__).resolve().parents[2]
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    from api.stability_core import wall_sided_gz, wall_sided_limit_deg


@dataclass
class HeeledState:
    """给定横倾角下的浮态切片（空间坐标）。"""

    heel_deg: float
    level: float          # 空间水平面高度 z_s = level
    volume: float         # 排水体积（含进水域，损失浮力法外）
    y_B: float
    z_B: float
    waterplane_area: float


def _station_integrals(hull: HullLines, polys: Sequence[Polygon], phi: float,
                       level: float) -> Tuple[float, float, float, float]:
    """逐站位裁剪积分 → (V, ∫a·y, ∫a·z, 水线面面积)。"""
    xs = hull.station_x()
    ax: List[float] = []
    my: List[float] = []
    mz: List[float] = []
    aw: List[float] = []
    for poly in polys:
        if len(poly) < 3:
            ax.append(0.0); my.append(0.0); mz.append(0.0); aw.append(0.0)
            continue
        rot = rotate(poly, phi)
        clipped = clip_waterline(rot, level)
        area, cy, cz = area_centroid(clipped)
        ax.append(area)
        my.append(area * cy)
        mz.append(area * cz)
        # 水线面局部宽度：由裁剪多边形与 z=level 的交线长度估计（单调近似）
        aw.append(_waterline_width(rot, level))
    return simpson(xs, ax), simpson(xs, my), simpson(xs, mz), simpson(xs, aw)


def _waterline_width(rot: Polygon, level: float) -> float:
    """旋转多边形在 z = level 处的截面宽度（交点间距之和）。"""
    n = len(rot)
    hits: List[float] = []
    for i in range(n):
        y0, z0 = rot[i]
        y1, z1 = rot[(i + 1) % n]
        if (z0 - level) == 0.0:
            hits.append(y0)
        if (z0 < level) != (z1 < level):
            t = (level - z0) / (z1 - z0)
            hits.append(y0 + t * (y1 - y0))
    if len(hits) < 2:
        return 0.0
    hits.sort()
    return hits[-1] - hits[0]


def _volume_at_level(hull: HullLines, polys: Sequence[Polygon], phi: float,
                     level: float) -> float:
    xs = hull.station_x()
    ax = []
    for poly in polys:
        if len(poly) < 3:
            ax.append(0.0)
            continue
        area, _, _ = area_centroid(clip_waterline(rotate(poly, phi), level))
        ax.append(area)
    return simpson(xs, ax)


def solve_level(hull: HullLines, polys: Sequence[Polygon], phi: float,
                v_target: float, xtol: float = 1e-13) -> float:
    """求空间水平面高度 level 使排水体积等于 v_target（二分，单调）。"""
    lo, hi = 0.0, hull.D + 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _volume_at_level(hull, polys, phi, mid) < v_target:
            lo = mid
        else:
            hi = mid
        if hi - lo < xtol:
            break
    return 0.5 * (lo + hi)


def hydrostatics(hull: HullLines, polys: Sequence[Polygon], phi_deg: float,
                 v_target: float, level: Optional[float] = None) -> HeeledState:
    """给定横倾角的浮态（level 未给则求根）。"""
    phi = math.radians(phi_deg)
    d = solve_level(hull, polys, phi, v_target) if level is None else level
    v, my, mz, aw = _station_integrals(hull, polys, phi, d)
    return HeeledState(heel_deg=phi_deg, level=d, volume=v,
                       y_B=(my / v if v > 0 else 0.0),
                       z_B=(mz / v if v > 0 else 0.0),
                       waterplane_area=aw)


def gz_curve(hull: HullLines, polys: Sequence[Polygon], KG: float,
             angles_deg: Iterable[float]) -> List[Dict[str, float]]:
    """完好船 GZ 曲线（真实型线数值积分）：GZ = y_B − KG·sinφ。"""
    rows: List[Dict[str, float]] = []
    for a in angles_deg:
        st = hydrostatics(hull, polys, a, hull.volume())
        gz = st.y_B - KG * math.sin(math.radians(a))
        rows.append({"heel_deg": float(a), "gz_m": gz, "level_m": st.level,
                     "z_B": st.z_B, "waterplane_area": st.waterplane_area})
    return rows


def hydrostatic_moments(hull: HullLines, polys: Sequence[Polygon]) -> Dict[str, float]:
    """零倾角浮态量：∇、KB、BM、水线面惯性矩 I_T。"""
    st = hydrostatics(hull, polys, 0.0, hull.volume())
    v = st.volume
    kb = st.z_B
    # 水线面惯性矩：由各站位水线半宽 b_w(x) 的 2/3·b³ 积分（矩形条带）
    xs = hull.station_x()
    level = st.level
    i_terms = []
    for poly in polys:
        w = _waterline_width(poly, level) if len(poly) >= 3 else 0.0
        i_terms.append((2.0 / 3.0) * (0.5 * w) ** 3 if w > 0 else 0.0)
    i_t = simpson(xs, i_terms)
    bm = i_t / v if v > 0 else 0.0
    return {"volume_m3": v, "level_m": level, "KB_m": kb, "BM_m": bm,
            "KM_m": kb + bm, "I_T_m4": i_t}


def gz_max_and_range(curve: Sequence[Dict[str, float]],
                     gz_floor: float = 0.0) -> Tuple[float, float]:
    """由 GZ 曲线取 GZ_max 与稳性范围（正值域宽度，deg）。"""
    pts = [(r["heel_deg"], r["gz_m"]) for r in curve]
    if not pts:
        return 0.0, 0.0
    gz_max = max(g for _, g in pts)
    # 正值域：从 0° 起到首个 ≤ floor 的交点（线性插值）
    start = 0.0
    end = pts[-1][0]
    for i in range(1, len(pts)):
        a0, g0 = pts[i - 1]
        a1, g1 = pts[i]
        if g0 > gz_floor >= g1:
            t = (gz_floor - g0) / (g1 - g0)
            end = a0 + t * (a1 - a0)
            break
    return gz_max, max(0.0, end - start)


def wall_sided_reference(gm: float, bm: float,
                         angles_deg: Iterable[float]) -> List[Dict[str, float]]:
    """壁直闭式 GZ 参考曲线（平台单源 stability_core.wall_sided_gz）。"""
    return [{"heel_deg": float(a), "gz_m": wall_sided_gz(gm, bm, float(a))}
            for a in angles_deg]


def compare_with_wall_sided(hull: HullLines, polys: Sequence[Polygon], KG: float,
                            angles_deg: Sequence[float] = (5.0, 10.0, 15.0, 30.0)
                            ) -> Dict[str, object]:
    """多边形 GZ vs 壁直闭式逐角对照（返回逐角相对误差）。"""
    hyd = hydrostatic_moments(hull, polys)
    gm = hyd["KM_m"] - KG
    num = gz_curve(hull, polys, KG, angles_deg)
    ref = wall_sided_reference(gm, hyd["BM_m"], angles_deg)
    rows = []
    for n, r in zip(num, ref):
        denom = abs(r["gz_m"]) if abs(r["gz_m"]) > 1e-12 else 1.0
        rows.append({"heel_deg": n["heel_deg"],
                     "gz_polygon_m": n["gz_m"],
                     "gz_wall_sided_m": r["gz_m"],
                     "abs_err_m": abs(n["gz_m"] - r["gz_m"]),
                     "rel_err": abs(n["gz_m"] - r["gz_m"]) / denom})
    return {"GM_m": gm, "BM_m": hyd["BM_m"], "KB_m": hyd["KB_m"],
            "hydrostatics": hyd, "rows": rows,
            "max_rel_err": max(r["rel_err"] for r in rows),
            "max_abs_err_m": max(r["abs_err_m"] for r in rows)}