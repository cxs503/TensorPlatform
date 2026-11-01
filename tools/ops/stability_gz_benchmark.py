#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""箱型驳船大倾角静稳性力臂 GZ/KN —— 独立解析基准（verification-debt 基准件）

背景
----
平台 ``/api/v1/stability-imo/gz-curve`` 与 ``/api/v1/stability/gz-curve`` 返回
0-90° 的 GZ/KN 曲线与面积量 (area_0_30 / area_0_40 / area_30_40 / GZ_max)，
底层实现见 ship-performance-platform ``backend/api/stability_imo.py``：
KB 用经验式 ``T*(0.5+0.05Cb)``，BM 用 ``(0.085Cb-0.002)B^2/T``，
KN 用 ``(BM*sin*cos + KB*sin)*(1+0.001*phi)``，另叠加 ``phi>30`` 线性衰减
与"甲板进水"×0.95 两个经验修正，并对 GZ 做 ``max(0, GZ)`` 截断。
这些近似对普通船型是第一性原理缺位，对**箱型驳**却存在严格闭式解，因此
箱型驳是天然的验收基准（判据先于动手）。

本模块提供两路**互相独立**的参考解，用来对平台输出做逐点误差判定：

1. ``wall_sided_gz``  —— 经典"直舷式"(wall-sided) 大倾角闭式解
   ``GZ = sinφ (GM + 0.5*BM*tan²φ)``，KB=T/2、BM=B²/(12T)。
   对矩形剖面，只要甲板边缘尚未入水（φ < atan(2(D-T)/B)）且底部边缘尚未
   出水（φ < atan(2T/B)），水线面宽度恒为 B，此式**精确**。
2. ``exact_kn_gz``  —— 几何裁剪 + 二重积分：把剖面多边形按倾角旋转到地坐标系，
   用 Sutherland-Hodgman 裁剪出浸没多边形，二分求水线使排水面积守恒，再由
   面积一阶矩求浮心，得 KN = η_B，GZ = KN - KG*sinφ。该解对任意倾角（含甲板
   进水、底部出水）都成立，不依赖任何经验公式。

用法
----
    python3 tools/ops/stability_gz_benchmark.py            # 基准自检 + 平台对标
    python3 tools/ops/stability_gz_benchmark.py --no-api    # 只跑解析基准

环境变量 ``SHIP_API_BASE``（默认 http://127.0.0.1:8001/api/v1）指定被测平台。
"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence, Tuple

DEFAULT_API_BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001/api/v1")

# 3% 判定阈值（平台精度标准：对标实验/参考值误差 >15% 不可接受；
# 本项目对"有严格闭式解"的量采用更严的 3% 验收线）
TOL_PCT = 3.0


# ----------------------------------------------------------------------------
# 算例定义
# ----------------------------------------------------------------------------
@dataclass
class BoxBarge:
    """矩形剖面箱型驳（prismatic box pontoon）"""

    L: float          # 垂线间长 m
    B: float          # 型宽 m
    T: float          # 吃水 m
    D: float          # 型深 m
    KG: float         # 重心高 m（自基线）
    rho: float = 1.025  # t/m^3

    # ---- 闭式静水力 ----
    @property
    def Cb(self) -> float:
        return 1.0

    @property
    def Cm(self) -> float:
        return 1.0

    @property
    def volume(self) -> float:
        """排水体积 m^3"""
        return self.L * self.B * self.T

    @property
    def displacement(self) -> float:
        """排水量 t"""
        return self.rho * self.volume

    @property
    def KB(self) -> float:
        """浮心高 —— 矩形剖面精确解 T/2"""
        return self.T / 2.0

    @property
    def BM(self) -> float:
        """稳心半径 I/∇ = (L B^3/12)/(L B T) = B^2/(12T)"""
        return self.B ** 2 / (12.0 * self.T)

    @property
    def KM(self) -> float:
        return self.KB + self.BM

    @property
    def GM(self) -> float:
        return self.KM - self.KG

    @property
    def deck_edge_angle(self) -> float:
        """甲板边缘入水角 atan(2(D-T)/B) [deg]"""
        return math.degrees(math.atan2(2.0 * (self.D - self.T), self.B))

    @property
    def bilge_emergence_angle(self) -> float:
        """底部边缘（舭部）出水角 atan(2T/B) [deg]"""
        return math.degrees(math.atan2(2.0 * self.T, self.B))

    @property
    def wall_sided_limit(self) -> float:
        """直舷式闭式解的有效上限 [deg]"""
        return min(self.deck_edge_angle, self.bilge_emergence_angle)

    def api_payload(self) -> Dict[str, float]:
        return {
            "L": self.L, "B": self.B, "T": self.T, "D": self.D,
            "displacement": round(self.displacement, 4),
            "Cb": self.Cb, "Cm": self.Cm, "KG": self.KG,
        }


# ----------------------------------------------------------------------------
# 参考解 1：直舷式闭式解
# ----------------------------------------------------------------------------
def wall_sided_gz(barge: BoxBarge, phi_deg: float) -> float:
    """wall-sided 大倾角闭式解 GZ = sinφ (GM + BM/2 tan²φ)"""
    phi = math.radians(phi_deg)
    return math.sin(phi) * (barge.GM + 0.5 * barge.BM * math.tan(phi) ** 2)


# ----------------------------------------------------------------------------
# 参考解 2：几何裁剪 + 面积一阶矩（任意倾角精确解）
# ----------------------------------------------------------------------------
def section_polygon(barge: BoxBarge) -> List[Tuple[float, float]]:
    """剖面多边形（船体坐标：y 向右舷，z 自基线向上，原点在龙骨中线）"""
    hb, T, D = barge.B / 2.0, barge.T, barge.D
    return [(-hb, 0.0), (hb, 0.0), (hb, D), (-hb, D)]


def _to_earth(poly: Sequence[Tuple[float, float]], phi: float, c: float
              ) -> List[Tuple[float, float]]:
    """船体坐标 → 地坐标（绕龙骨中线横倾 phi，再抬升 c）"""
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    return [(y * cos_p + z * sin_p, -y * sin_p + z * cos_p + c) for y, z in poly]


def _clip_below_waterline(poly: Sequence[Tuple[float, float]]
                          ) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman：保留 z <= 0（水线以下）部分"""
    out: List[Tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        p_cur = poly[i]
        p_nxt = poly[(i + 1) % n]
        in_cur = p_cur[1] <= 0.0
        in_nxt = p_nxt[1] <= 0.0
        if in_cur:
            out.append(p_cur)
        if in_cur != in_nxt:
            # 交于 z=0
            dz = p_nxt[1] - p_cur[1]
            if abs(dz) < 1e-15:
                continue
            t = -p_cur[1] / dz
            out.append((p_cur[0] + t * (p_nxt[0] - p_cur[0]), 0.0))
    return out


def _area_centroid(poly: Sequence[Tuple[float, float]]) -> Tuple[float, Tuple[float, float]]:
    """多边形面积与形心（shoelace），返回 (A, (ybar, zbar))"""
    n = len(poly)
    if n < 3:
        return 0.0, (0.0, 0.0)
    a2 = 0.0
    cy = 0.0
    cz = 0.0
    for i in range(n):
        y0, z0 = poly[i]
        y1, z1 = poly[(i + 1) % n]
        cross = y0 * z1 - y1 * z0
        a2 += cross
        cy += (y0 + y1) * cross
        cz += (z0 + z1) * cross
    if abs(a2) < 1e-15:
        return 0.0, (0.0, 0.0)
    area = 0.5 * a2
    return area, (cy / (3.0 * a2), cz / (3.0 * a2))


def exact_kn_gz(barge: BoxBarge, phi_deg: float, tol: float = 1e-12
                ) -> Dict[str, float]:
    """任意倾角精确 KN/GZ（等排水量约束下水线位置二分求解）

    返回 {kn, gz, yb, zb, area, waterline}
    """
    phi = math.radians(phi_deg)
    poly = section_polygon(barge)
    a_target = barge.Cb * barge.B * barge.T  # 单位长度浸没面积
    diag = math.hypot(barge.B, barge.D)

    def submerged(c: float) -> Tuple[float, float, float]:
        clipped = _clip_below_waterline(_to_earth(poly, phi, c))
        area, (yb, zb) = _area_centroid(clipped)
        return area, yb, zb

    # 二分：area(c) 关于 c 单调递减（c 为船体相对水线的抬升量）
    lo, hi = -diag, diag
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        area, _, _ = submerged(mid)
        if area > a_target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    c = 0.5 * (lo + hi)
    area, yb, zb = submerged(c)
    kn = yb                      # KN 即浮心横向坐标（地坐标，重心过龙骨中线）
    gz = kn - barge.KG * math.sin(phi)
    return {
        "kn": kn,
        "gz": gz,
        "yb": yb,
        "zb": zb,
        "area": area,
        "waterline_lift": c,
    }


def exact_curve(barge: BoxBarge, step: float = 0.5) -> Dict[str, List[float]]:
    """精确参考曲线（细网格）"""
    n = int(round(90.0 / step))
    angles = [i * step for i in range(n + 1)]
    kn, gz = [], []
    for a in angles:
        r = exact_kn_gz(barge, a)
        kn.append(r["kn"])
        gz.append(r["gz"])
    return {"angles": angles, "kn": kn, "gz": gz}


# ----------------------------------------------------------------------------
# 面积量 / 特征量（在平台同一 5° 网格上做梯形积分，隔离"积分误差"与"公式误差"）
# ----------------------------------------------------------------------------
def trapezoid_area(angles: Sequence[float], gz: Sequence[float],
                   a_lo: float, a_hi: float) -> float:
    """在 [a_lo, a_hi] 上对 GZ 曲线梯形积分（rad）"""
    total = 0.0
    for i in range(len(angles) - 1):
        p1, p2 = angles[i], angles[i + 1]
        if p2 <= a_lo or p1 >= a_hi:
            continue
        g1, g2 = gz[i], gz[i + 1]
        # 端点截断（本基准网格为 5°，30/40 均为网格点，截断仅作保护）
        if p1 < a_lo:
            f = (a_lo - p1) / (p2 - p1)
            g1 = g1 + f * (g2 - g1)
            p1 = a_lo
        if p2 > a_hi:
            f = (a_hi - p2) / (p2 - p1)
            g2 = g2 + f * (g1 - g2)
            p2 = a_hi
        total += 0.5 * (g1 + g2) * math.radians(p2 - p1)
    return total


def reference_metrics(barge: BoxBarge, angles: Sequence[float],
                      gz: Sequence[float]) -> Dict[str, float]:
    """在给定网格上求参考特征量（与平台输出结构对齐）"""
    area_0_30 = trapezoid_area(angles, gz, 0.0, 30.0)
    area_30_40 = trapezoid_area(angles, gz, 30.0, 40.0)
    area_0_40 = trapezoid_area(angles, gz, 0.0, 40.0)
    pairs = list(zip(gz, angles))
    gz_max, gz_max_angle = max(pairs, key=lambda p: p[0])
    # 消失角（GZ 由正转负的角度，线性插值）
    vanishing = None
    for i in range(len(angles) - 1):
        if gz[i] > 0.0 >= gz[i + 1]:
            f = gz[i] / (gz[i] - gz[i + 1])
            vanishing = angles[i] + f * (angles[i + 1] - angles[i])
            break
    return {
        "area_0_30": area_0_30,
        "area_0_40": area_0_40,
        "area_30_40": area_30_40,
        "GZ_max": gz_max,
        "GZ_max_angle": gz_max_angle,
        "vanishing_angle": vanishing,
    }


# ----------------------------------------------------------------------------
# 自检：参考解自身必须与闭式解一致（判据先于动手）
# ----------------------------------------------------------------------------
def selfcheck(barge: BoxBarge) -> Dict[str, object]:
    """参考解自检：小倾角斜率、wall-sided 一致性、排水体积守恒"""
    checks = []

    # 1) 排水面积守恒（任意倾角下裁剪后面积应等于 Cb*B*T）
    worst_area = 0.0
    for a in (0.0, 10.0, 25.0, 45.0, 70.0, 89.0):
        r = exact_kn_gz(barge, a)
        worst_area = max(worst_area, abs(r["area"] - barge.Cb * barge.B * barge.T))
    checks.append({
        "name": "排水面积守恒 (max|dA|, m^2)", "value": worst_area,
        "limit": 1e-9, "pass": worst_area < 1e-9,
    })

    # 2) 初始稳性斜率 dGZ/dφ(φ→0) = GM；dKN/dφ = KM
    h = 1e-5
    gz_p = exact_kn_gz(barge, math.degrees(h))["gz"]
    gz_m = exact_kn_gz(barge, math.degrees(-h))["gz"]
    slope_gz = (gz_p - gz_m) / (2.0 * h)
    kn_p = exact_kn_gz(barge, math.degrees(h))["kn"]
    kn_m = exact_kn_gz(barge, math.degrees(-h))["kn"]
    slope_kn = (kn_p - kn_m) / (2.0 * h)
    checks.append({
        "name": "dGZ/dφ vs GM", "value": slope_gz,
        "limit": barge.GM, "pass": abs(slope_gz - barge.GM) / barge.GM < 1e-4,
    })
    checks.append({
        "name": "dKN/dφ vs KM", "value": slope_kn,
        "limit": barge.KM, "pass": abs(slope_kn - barge.KM) / barge.KM < 1e-4,
    })

    # 3) 直舷式有效区间内两路参考解互相独立一致
    worst = 0.0
    worst_at = 0.0
    a = 0.0
    while a <= barge.wall_sided_limit:
        gz_exact = exact_kn_gz(barge, a)["gz"]
        gz_ws = wall_sided_gz(barge, a)
        d = abs(gz_exact - gz_ws) / abs(gz_ws) * 100.0 if abs(gz_ws) > 1e-9 else 0.0
        if d > worst:
            worst, worst_at = d, a
        a += 1.0
    checks.append({
        "name": "两路参考解一致性 max 相对差 (%)", "value": worst,
        "limit": 1.0, "pass": worst < 1.0, "at_angle": worst_at,
    })

    # 4) 物理合理量：KB(0°) = T/2（浮心高于水线的深度取负号）
    z_at_zero = exact_kn_gz(barge, 0.0)
    kb_num = -z_at_zero["zb"]
    checks.append({
        "name": "KB 数值解 vs T/2", "value": kb_num,
        "limit": barge.KB, "pass": abs(kb_num - barge.KB) < 1e-9,
    })
    draft_num = -z_at_zero["waterline_lift"]
    checks.append({
        "name": "水线求解回代吃水 vs T", "value": draft_num,
        "limit": barge.T, "pass": abs(draft_num - barge.T) < 1e-6,
    })

    return {"barge": asdict(barge), "closed_form": {
        "KB": barge.KB, "BM": barge.BM, "KM": barge.KM, "GM": barge.GM,
        "deck_edge_angle": barge.deck_edge_angle,
        "bilge_emergence_angle": barge.bilge_emergence_angle,
        "wall_sided_limit": barge.wall_sided_limit,
        "volume": barge.volume, "displacement": barge.displacement,
    }, "checks": checks,
        "all_pass": all(c["pass"] for c in checks)}


# ----------------------------------------------------------------------------
# 平台对标
# ----------------------------------------------------------------------------
def query_api(barge: BoxBarge, base: str = DEFAULT_API_BASE,
              timeout: float = 15.0) -> Dict[str, object]:
    """调用平台 GZ 端点（两个端点都取，检查内部一致性）"""
    import requests

    payload = barge.api_payload()
    out: Dict[str, object] = {"base": base, "payload": payload}
    for name, path in (
        ("stability_imo_gz", "/stability-imo/gz-curve"),
        ("stability_gz", "/stability/gz-curve"),
    ):
        url = base.rstrip("/") + path
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            out[name] = {"status": resp.status_code, "data": resp.json()
                         if resp.status_code == 200 else resp.text[:400]}
        except Exception as exc:  # noqa: BLE001  平台不可用要如实记录
            out[name] = {"status": None, "error": f"{type(exc).__name__}: {exc}"}
    return out


def compare(api: Dict[str, object], barge: BoxBarge) -> Dict[str, object]:
    """逐量误差判定（参考解 vs 平台）"""
    data = None
    for key in ("stability_imo_gz", "stability_gz"):
        blk = api.get(key) or {}
        if blk.get("status") == 200 and isinstance(blk.get("data"), dict):
            data = blk["data"]
            used = key
            break
    if data is None:
        return {"available": False, "reason": "平台 GZ 端点不可用"}

    angles = [float(a) for a in data["angles"]]
    gz_api = [float(v) for v in data["gz_values"]]
    kn_api = [float(v) for v in data.get("kn_values", [])] or None

    # 参考曲线：与平台同网格（5°），用精确裁剪解
    gz_ref = [exact_kn_gz(barge, a)["gz"] for a in angles]
    kn_ref = [exact_kn_gz(barge, a)["kn"] for a in angles]
    gz_ws = [wall_sided_gz(barge, a) if a <= barge.wall_sided_limit else None
             for a in angles]
    ref_metrics = reference_metrics(barge, angles, gz_ref)
    # 细网格（0.25°）积分作为积分误差上界
    fine = exact_curve(barge, 0.25)
    fine_metrics = reference_metrics(barge, fine["angles"], fine["gz"])

    def pct(a_api, a_ref):
        if abs(a_ref) < 1e-12:
            return None
        return (a_api - a_ref) / abs(a_ref) * 100.0

    points = []
    for a, ga, gr, gw, ka, kr in zip(angles, gz_api, gz_ref, gz_ws,
                                     kn_api or [None] * len(angles),
                                     kn_ref):
        points.append({
            "angle": a,
            "gz_api": ga, "gz_exact": gr,
            "gz_err_pct": pct(ga, gr),
            "gz_wall_sided": gw,
            "gz_err_vs_wall_sided_pct": (pct(ga, gw) if gw is not None else None),
            "kn_api": ka, "kn_exact": kr,
            "kn_err_pct": pct(ka, kr) if ka is not None else None,
        })

    scalars = {}
    for key, api_val in (("area_0_30", data.get("area_0_30")),
                         ("area_0_40", data.get("area_0_40")),
                         ("area_30_40", data.get("area_30_40")),
                         ("GZ_max", data.get("GZ_max"))):
        if api_val is None:
            continue
        err = pct(float(api_val), ref_metrics[key])
        scalars[key] = {
            "api": float(api_val), "exact": ref_metrics[key],
            "exact_fine_grid": fine_metrics[key],
            "err_pct": err, "within_3pct": (err is not None and abs(err) < TOL_PCT),
        }
    if "GZ_max_angle" in data:
        scalars["GZ_max_angle"] = {
            "api": float(data["GZ_max_angle"]),
            "exact": ref_metrics["GZ_max_angle"],
            "exact_fine_grid": fine_metrics["GZ_max_angle"],
            "err_deg": float(data["GZ_max_angle"]) - ref_metrics["GZ_max_angle"],
            "within_tol": abs(float(data["GZ_max_angle"])
                              - ref_metrics["GZ_max_angle"]) <= 5.0,
            "tol_deg": 5.0,
        }

    hydro = {
        "KB": {"exact": barge.KB}, "BM": {"exact": barge.BM},
        "KM": {"exact": barge.KM}, "GM": {"exact": barge.GM},
    }
    # 由平台曲线反推 GM0 / KM0（小倾角：GZ ≈ GM*sinφ，KN ≈ KM*sinφ）
    if len(angles) >= 2:
        s = math.sin(math.radians(angles[1]))
        hydro["GM"]["from_curve"] = gz_api[1] / s
        hydro["GM"]["err_pct"] = pct(gz_api[1] / s, barge.GM)
        hydro["GM"]["within_3pct"] = abs(hydro["GM"]["err_pct"]) < TOL_PCT
        if kn_api:
            hydro["KM"]["from_curve"] = kn_api[1] / s
            hydro["KM"]["err_pct"] = pct(kn_api[1] / s, barge.KM)
            hydro["KM"]["within_3pct"] = abs(hydro["KM"]["err_pct"]) < TOL_PCT
        # KB / BM 由 KM-GM 关系无法分离，取平台曲线两端点反推
        # KN = (BM sinφ cosφ + KB sinφ)(1+0.001φ) 的反演跳过，只报告合成量。

    # 平台是否截断负 GZ（物理上消失角之后 GZ 应为负）
    neg_exact = [p for p in points if p["gz_exact"] < -0.02]
    clipped = [p for p in points if p["gz_exact"] < -0.02 and abs(p["gz_api"]) < 1e-9]
    diagnostics = {
        "api_reports_negative_gz": any(v < 0 for v in gz_api),
        "n_angles_exact_negative": len(neg_exact),
        "n_angles_where_api_clipped_to_zero": len(clipped),
        "gz_zero_tail_from_deg": next(
            (a for a, v in zip(angles, gz_api) if v == 0.0 and a > 0), None),
        "endpoints_agree": None,
    }
    other = api.get("stability_gz") or api.get("stability_imo_gz") or {}
    prim = api.get("stability_imo_gz") or api.get("stability_gz") or {}
    if other.get("status") == 200 and prim.get("status") == 200:
        diagnostics["endpoints_agree"] = (
            other["data"].get("gz_values") == prim["data"].get("gz_values"))

    cand = [p for p in points
            if p["gz_exact"] > 1e-9 and p["gz_err_pct"] is not None]
    worst_gz = (max(cand, key=lambda p: abs(p["gz_err_pct"]))
                if cand else None)
    return {
        "available": True,
        "endpoint_used": used,
        "angles": angles,
        "points": points,
        "scalars": scalars,
        "hydrostatics": hydro,
        "diagnostics": diagnostics,
        "reference_metrics_on_api_grid": ref_metrics,
        "reference_metrics_fine_grid": fine_metrics,
        "worst_point": worst_gz,
        "summary": {
            "n_points_within_3pct": sum(
                1 for p in points
                if p["gz_err_pct"] is not None and abs(p["gz_err_pct"]) < TOL_PCT),
            "n_points": len(points),
            "all_scalars_within_3pct": all(
                v.get("within_3pct", v.get("within_tol", True))
                for v in scalars.values()),
        },
    }


# ----------------------------------------------------------------------------
# IMO 完整稳性判据：平台判决 vs 精确参考判决（判据翻转扫描）
# ----------------------------------------------------------------------------
# IS Code 2008 Part A 2.2 / 平台 stability_imo.check_imo_criteria 所用阈值
IMO_LIMITS = {
    "GM": 0.15,            # m
    "GZ_max": 0.20,        # m
    "GZ_max_angle": 25.0,  # deg
    "vanishing_angle": 60.0,  # deg
    "area_0_30": 0.055,    # m.rad
    "area_0_40": 0.090,    # m.rad
    "area_30_40": 0.030,   # m.rad
}


def reference_imo(barge: BoxBarge, step: float = 1.0) -> Dict[str, object]:
    """用精确参考曲线按 IMO 阈值给出判决（与平台同一套阈值，曲线不同）"""
    n = int(round(90.0 / step))
    angles = [i * step for i in range(n + 1)]
    gz = [exact_kn_gz(barge, a)["gz"] for a in angles]
    met = reference_metrics(barge, angles, gz)
    values = {
        "GM": barge.GM,
        "GZ_max": met["GZ_max"],
        "GZ_max_angle": met["GZ_max_angle"],
        "vanishing_angle": met["vanishing_angle"] if met["vanishing_angle"] is not None
        else 90.0,
        "area_0_30": met["area_0_30"],
        "area_0_40": met["area_0_40"],
        "area_30_40": met["area_30_40"],
    }
    verdicts = {k: bool(values[k] >= IMO_LIMITS[k]) for k in IMO_LIMITS}
    return {"values": values, "verdicts": verdicts,
            "all_met": all(verdicts.values())}


def verdict_flip_scan(base: str, kws: Sequence[float], timeout: float = 15.0
                      ) -> Dict[str, object]:
    """扫一组 KG，比较平台 IMO 端点判决与精确参考判决是否翻转"""
    import requests

    rows = []
    for kw in kws:
        barge = BoxBarge(L=100.0, B=20.0, T=5.0, D=9.0, KG=kw)
        ref = reference_imo(barge)
        payload = dict(barge.api_payload())
        payload.update({"GM": None, "KB": None, "GZ_max": None, "GZ_angle": None,
                        "vanishing_angle": None, "gz_curve": None,
                        "ship_type": "cargo"})
        row: Dict[str, object] = {"KG": kw, "GM_exact": barge.GM}
        try:
            resp = requests.post(base.rstrip("/") + "/stability-imo/check",
                                 json=payload, timeout=timeout)
            data = resp.json() if resp.status_code == 200 else None
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            continue
        if not data:
            row["error"] = f"HTTP {resp.status_code}"
            rows.append(row)
            continue
        crit = data.get("criteria_results") or {}
        plat = {k: bool((crit.get(k) or {}).get("met")) for k in IMO_LIMITS
                if k in crit}
        flips = {k: (plat[k] != ref["verdicts"][k]) for k in plat}
        row.update({
            "platform_verdicts": plat,
            "reference_verdicts": {k: ref["verdicts"][k] for k in plat},
            "flipped_criteria": [k for k, v in flips.items() if v],
            "n_flipped": sum(flips.values()),
            "platform_all_met": data.get("all_criteria_met", data.get("passed")),
            "reference_all_met": ref["all_met"],
            "reference_values": ref["values"],
            "platform_values": {k: (crit.get(k) or {}).get("actual_value")
                                for k in plat},
        })
        rows.append(row)
    total = sum(r.get("n_flipped", 0) for r in rows)
    return {
        "cases": rows,
        "n_cases": len(rows),
        "total_flipped_criteria": total,
        "flipped_cases": [r["KG"] for r in rows if r.get("n_flipped")],
    }


# ----------------------------------------------------------------------------
# 出图
# ----------------------------------------------------------------------------
def make_figures(barge: BoxBarge, cmp_result: Dict[str, object], outdir: str
                 ) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    files = []
    fine = exact_curve(barge, 0.25)
    plt.rcParams.update({"font.size": 9, "figure.dpi": 140})

    if cmp_result.get("available"):
        pts = cmp_result["points"]
        ang = [p["angle"] for p in pts]
        gz_api = [p["gz_api"] for p in pts]
        gz_ref = [p["gz_exact"] for p in pts]
        kn_api = [p["kn_api"] for p in pts]
    else:
        ang, gz_api, gz_ref, kn_api = [], [], [], []

    # 图1：GZ 曲线对比
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(fine["angles"], fine["gz"], "-", lw=2.0,
            label="exact (section clipping, 0.25° grid)", color="#1f77b4")
    ws_a = [a for a in fine["angles"] if a <= barge.wall_sided_limit]
    ax.plot(ws_a, [wall_sided_gz(barge, a) for a in ws_a], "--", lw=1.4,
            label="wall-sided closed form", color="#2ca02c")
    if ang:
        ax.plot(ang, gz_api, "o", ms=4.5, label="platform API (5° grid)",
                color="#d62728")
    ax.axvline(barge.deck_edge_angle, color="0.5", ls=":", lw=1.0)
    ax.text(barge.deck_edge_angle + 0.8, 0.05, "deck edge\nimmersion",
            fontsize=7, color="0.35")
    ax.axhline(0.0, color="k", lw=0.6)
    ax.set_xlabel("heel angle φ (deg)")
    ax.set_ylabel("GZ (m)")
    ax.set_title(f"Box barge GZ: platform vs. exact reference "
                 f"(L={barge.L:g}, B={barge.B:g}, T={barge.T:g}, KG={barge.KG:g})")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7.5)
    f1 = os.path.join(outdir, "fig1_gz_vs_reference.png")
    fig.tight_layout()
    fig.savefig(f1)
    plt.close(fig)
    files.append(f1)

    # 图2：逐点误差 + 3% 带
    if ang:
        fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8))
        err_gz = [p["gz_err_pct"] for p in pts]
        ax = axes[0]
        ax.plot(ang, err_gz, "o-", ms=4, color="#d62728", label="GZ error")
        ax.axhspan(-TOL_PCT, TOL_PCT, color="#2ca02c", alpha=0.12,
                   label="±3% acceptance band")
        ax.axhline(0, color="k", lw=0.6)
        ax.set_xlabel("heel angle φ (deg)")
        ax.set_ylabel("relative error of GZ (%)")
        ax.set_title("GZ point-wise error vs. exact solution")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7.5)
        ax = axes[1]
        if any(k is not None for k in [p["kn_err_pct"] for p in pts]):
            ax.plot(ang, [p["kn_err_pct"] for p in pts], "s-", ms=4,
                    color="#9467bd", label="KN error")
            ax.axhspan(-TOL_PCT, TOL_PCT, color="#2ca02c", alpha=0.12)
            ax.axhline(0, color="k", lw=0.6)
            ax.set_xlabel("heel angle φ (deg)")
            ax.set_ylabel("relative error of KN (%)")
            ax.set_title("KN point-wise error vs. exact solution")
            ax.grid(alpha=0.3)
            ax.legend(fontsize=7.5)
        f2 = os.path.join(outdir, "fig2_pointwise_error.png")
        fig.tight_layout()
        fig.savefig(f2)
        plt.close(fig)
        files.append(f2)

    # 图3：剖面几何 + 浸没多边形（φ=0/30°）
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.4))
    for ax, phi in zip(axes, (0.0, barge.deck_edge_angle + 1.0, 45.0)):
        rad = math.radians(phi)
        poly = section_polygon(barge)
        a_target = barge.Cb * barge.B * barge.T
        diag = math.hypot(barge.B, barge.D)
        lo, hi = -diag, diag
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            ar = _area_centroid(_clip_below_waterline(
                _to_earth(poly, rad, mid)))[0]
            if ar < a_target:
                lo = mid
            else:
                hi = mid
        c = 0.5 * (lo + hi)
        earth = _to_earth(poly, rad, c)
        sub = _clip_below_waterline(earth)
        ax.fill([p[0] for p in earth], [p[1] for p in earth],
                color="#cfe2f3", ec="#1f4e79", lw=1.2)
        if sub:
            ax.fill([p[0] for p in sub], [p[1] for p in sub],
                    color="#3b7dd8", alpha=0.55, ec="#0b3d91", lw=1.0)
        r = exact_kn_gz(barge, phi)
        ax.plot([r["yb"]], [r["zb"]], "r*", ms=11, label="buoyancy centre B")
        ax.axhline(0.0, color="k", lw=1.0)
        ax.set_aspect("equal")
        ax.set_title(f"φ = {phi:.1f}°, KN = {r['kn']:.3f} m", fontsize=9)
        ax.set_xlabel("y (m)")
        ax.set_ylabel("z (m)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    f3 = os.path.join(outdir, "fig3_section_submerged.png")
    fig.tight_layout()
    fig.savefig(f3)
    plt.close(fig)
    files.append(f3)

    # 图4：特征量误差柱状 + 3% 线
    if cmp_result.get("available"):
        scal = cmp_result["scalars"]
        labels, errs = [], []
        for k in ("area_0_30", "area_0_40", "area_30_40", "GZ_max"):
            if k in scal and scal[k]["err_pct"] is not None:
                labels.append(k)
                errs.append(scal[k]["err_pct"])
        gm = cmp_result["hydrostatics"]["GM"]
        km = cmp_result["hydrostatics"]["KM"]
        labels += ["GM (from curve)", "KM (from curve)"]
        errs += [gm["err_pct"], km.get("err_pct")]
        fig, ax = plt.subplots(figsize=(6.6, 3.8))
        colors = ["#2ca02c" if abs(e) < TOL_PCT else "#d62728" for e in errs]
        bars = ax.bar(labels, errs, color=colors, ec="k", lw=0.5)
        ax.axhline(0, color="k", lw=0.6)
        ax.axhline(TOL_PCT, color="#2ca02c", ls="--", lw=1.0)
        ax.axhline(-TOL_PCT, color="#2ca02c", ls="--", lw=1.0)
        for b, e in zip(bars, errs):
            ax.text(b.get_x() + b.get_width() / 2, e,
                    f"{e:+.1f}%", ha="center",
                    va="bottom" if e >= 0 else "top", fontsize=8)
        ax.set_ylabel("relative error vs. exact (%)")
        ax.set_title("Platform stability metrics vs. exact box-barge reference")
        plt.setp(ax.get_xticklabels(), rotation=18, ha="right", fontsize=8)
        ax.grid(axis="y", alpha=0.3)
        f4 = os.path.join(outdir, "fig4_metric_errors.png")
        fig.tight_layout()
        fig.savefig(f4)
        plt.close(fig)
        files.append(f4)

    return files


# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-api", action="store_true", help="跳过平台对标")
    ap.add_argument("--base", default=DEFAULT_API_BASE)
    ap.add_argument("--outdir", default="artifacts/stability_gz_verify")
    ap.add_argument("--L", type=float, default=100.0)
    ap.add_argument("--B", type=float, default=20.0)
    ap.add_argument("--T", type=float, default=5.0)
    ap.add_argument("--D", type=float, default=9.0)
    ap.add_argument("--KG", type=float, default=6.0)
    ap.add_argument("--scan-kg", default="",
                    help="逗号分隔的 KG 列表，做 IMO 判决翻转扫描")
    args = ap.parse_args()

    barge = BoxBarge(L=args.L, B=args.B, T=args.T, D=args.D, KG=args.KG)
    sc = selfcheck(barge)
    print("== 参考解自检 ==")
    for c in sc["checks"]:
        print(f"  [{'OK ' if c['pass'] else 'FAIL'}] {c['name']}: {c['value']}"
              f" (limit {c['limit']})")
    print(f"  闭式量: KB={barge.KB:.4f} BM={barge.BM:.4f} KM={barge.KM:.4f} "
          f"GM={barge.GM:.4f}; deck-edge={barge.deck_edge_angle:.2f}°, "
          f"wall-sided 有效至 {barge.wall_sided_limit:.2f}°")

    report: Dict[str, object] = {"barge": asdict(barge), "selfcheck": sc}

    if not args.no_api:
        api = query_api(barge, args.base)
        cmp_result = compare(api, barge)
        report["api_raw_status"] = {k: v.get("status") if isinstance(v, dict) else None
                                    for k, v in api.items() if k not in ("base", "payload")}
        report["comparison"] = cmp_result
        if cmp_result.get("available"):
            print("\n== 平台对标（误差 = (平台-精确)/精确）==")
            for p in cmp_result["points"]:
                e = p["gz_err_pct"]
                es = f"{e:+7.2f}%" if e is not None else "   n/a "
                if e is None:
                    flag = "OK0" if abs(p["gz_api"]) < 1e-9 else "FAIL"
                else:
                    flag = "OK " if abs(e) < TOL_PCT else "FAIL"
                print(f"  φ={p['angle']:5.1f}°  GZ_api={p['gz_api']:.4f}  "
                      f"GZ_exact={p['gz_exact']:.4f}  err={es} [{flag}]")
            print("\n== 特征量 ==")
            for k, v in cmp_result["scalars"].items():
                if "err_pct" in v:
                    print(f"  {k}: api={v['api']} exact={v['exact']:.4f} "
                          f"err={v['err_pct']:+.3f}% within3%={v['within_3pct']}")
                else:  # 角度类量（单位 deg）
                    print(f"  {k}: api={v['api']} exact={v['exact']:.4f} "
                          f"err={v['err_deg']:+.2f}deg within5deg={v['within_tol']}")
            print("\n== 静水力 ==")
            for k, v in cmp_result["hydrostatics"].items():
                print(f"  {k}: {v}")
            print("\n== 诊断 ==")
            for k, v in cmp_result["diagnostics"].items():
                print(f"  {k}: {v}")
        else:
            print(f"\n平台不可用: {cmp_result.get('reason')}")

    if args.scan_kg and not args.no_api:
        kws = [float(x) for x in args.scan_kg.split(",") if x.strip()]
        scan = verdict_flip_scan(args.base, kws)
        report["imo_verdict_scan"] = scan
        print("\n== IMO 判决翻转扫描 ==")
        for r in scan["cases"]:
            print(f"  KG={r['KG']:.2f} (GM_exact={r['GM_exact']:+.3f} m): "
                  f"翻转判据={r.get('flipped_criteria')} "
                  f"平台全通过={r.get('platform_all_met')} "
                  f"参考全通过={r.get('reference_all_met')}")
        print(f"  合计翻转判据数={scan['total_flipped_criteria']}")

    os.makedirs(args.outdir, exist_ok=True)
    figs = make_figures(barge, report.get("comparison", {"available": False}),
                        args.outdir)
    report["figures"] = figs
    json_path = os.path.join(args.outdir, "stability_gz_verification.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\nJSON: {json_path}")
    for f in figs:
        print(f"FIG : {f}")
    # 退出码：参考解自检失败 → 1（平台偏差不决定退出码，由报告判定）
    return 0 if sc["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())