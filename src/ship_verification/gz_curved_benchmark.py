"""曲面剖面的 GZ 引擎复核：解析解（圆剖面浮筒 / 圆舭箱体）+ 独立像素积分交叉。

被复核对象
----------
``src/ship_perf/hydrostatics.py`` 的大倾角 GZ 路径（等体积水线 + 浮心横移，
截面按多边形裁剪求面积与形心）。该路径原先只由直边梯形族驱动，且只有
wall-sided 解析式可对照——**曲面剖面（真实船体的舭圆、圆剖面浮筒）没有基准**。

三类基准（逐条独立于被复核实现）
--------------------------------
A. ``log``：半径 R 的圆柱浮筒（圆剖面）。半浸没时解析结果恰为
   KB = R(1 - 4/3π)、BM = 4R/3π、**KM = R**（精确），且任意倾角 θ 下
   浸没区是"弓形"，其面积/形心都有闭式 ⇒ GZ(θ) 有解析式（本文件实现）。
B. ``filleted_box``：宽 B、吃水 T、甲板 D、舭圆半径 r（与平底及舷侧相切）的箱体。
   水线面仍为矩形 ⇒ BM 精确；KB 由裁角一阶矩精确给出 ⇒ GM 精确。
C. ``brute``：任意剖面函数 inside(y,z) 的**像素积分**（独立算法，非多边形裁剪），
   体积配平后取形心 ⇒ 用作 B 类大倾角 GZ 的交叉基准。

模块位置：``src/ship_verification/gz_curved_benchmark.py``（复核侧代码，与 src/ship_perf 分离）
用法：``python3 -m ship_verification.gz_curved_benchmark --out outputs/gz_curved``（工作目录 py 路径含 src）
输出：``gz_curved.json``（全部数字，报告程序化注入）+ 两张 png。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from ship_perf import hydrostatics as H  # noqa: E402

ANGLES = [0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0, 75.0, 90.0, 120.0, 150.0]

# --------------------------------------------------------------------- A 圆剖面


def log_segment(R: float, h: float):
    """浸没弓形：水线在圆心以下深度 h（h<0 表示水线高于圆心）。

    返回 (面积, 形心在圆心以下的深度)。
    """
    if h >= R:
        return 0.0, 0.0
    if h <= -R:
        return math.pi * R * R, 0.0
    al = math.acos(max(-1.0, min(1.0, h / R)))  # 浸没弓形半角
    area = R * R * (al - math.sin(al) * math.cos(al))
    depth = (2.0 / 3.0) * R ** 3 * math.sin(al) ** 3 / area if area > 0 else 0.0
    return area, depth


def log_waterline(R: float, area_target: float) -> float:
    lo, hi = -R, R
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if log_segment(R, mid)[0] > area_target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def log_gz(R: float, kg: float, angles=ANGLES, area_target=None):
    """圆柱浮筒的解析 GZ(θ)。z 从龙骨（圆最低点）起算，故圆心在 z=R。"""
    at = 0.5 * math.pi * R * R if area_target is None else area_target
    h = log_waterline(R, at)
    _, depth = log_segment(R, h)
    out = []
    for a in angles:
        th = math.radians(a)
        # 水线系（原点在圆心）：浮心 (0, -depth)；旋回船体坐标系
        y_w, z_w = 0.0, -depth
        y_b = y_w * math.cos(th) + z_w * math.sin(th)
        z_b = -y_w * math.sin(th) + z_w * math.cos(th) + R
        out.append((-y_b) * math.cos(th) + (z_b - kg) * math.sin(th))
    return out


def log_hydrostatics(R: float):
    """半浸没圆柱的解析静水力（KM=R 精确，与 R 无关）。"""
    area = 0.5 * math.pi * R * R
    kb = R * (1.0 - 4.0 / (3.0 * math.pi))
    bm = 4.0 * R / (3.0 * math.pi)
    return {"area": area, "KB": kb, "BM": bm, "KM": kb + bm, "radius_R": R}


# --------------------------------------------------------------------- B 圆舭箱体


def filleted_box_hydrostatics(b: float, t: float, r: float):
    """圆舭箱体（舭圆与平底/舷侧相切，T>r）的解析静水力。

    剖面积  A = B·T - (2 - π/2)·r²
    裁角（1/4 方角减 1/4 圆）面积 a_c = r²(1-π/4)，形心高 z_c = [r³/2 - (πr²/4)(r-4r/3π)]/a_c
    KB = [B·T²/2 - 2·a_c·z_c]/A，水线面仍为矩形 ⇒ BM = (B³/12)/A。
    """
    r = max(0.0, min(r, b / 2.0, t))
    a = b * t - (2.0 - 0.5 * math.pi) * r * r
    if r > 0.0:
        a_c = r * r * (1.0 - 0.25 * math.pi)
        z_c = (r ** 3 / 2.0 - (0.25 * math.pi * r * r) * (r - 4.0 * r / (3.0 * math.pi))) / a_c
        m = b * t * t / 2.0 - 2.0 * a_c * z_c
    else:
        m = b * t * t / 2.0
    kb = m / a
    bm = (b ** 3 / 12.0) / a
    return {"area": a, "KB": kb, "BM": bm, "KM": kb + bm, "kb_moment": m, "r_bilge": r}


# --------------------------------------------------------------------- C 像素积分


class Grid:
    """剖面像素积分：inside(y,z) 的布尔场一次预计算，逐水线向量化求和。"""

    def __init__(self, inside, ylim, zlim, n):
        ys = np.linspace(ylim[0], ylim[1], n)
        zs = np.linspace(zlim[0], zlim[1], n)
        self.dy = (ylim[1] - ylim[0]) / (n - 1)
        self.dz = (zlim[1] - zlim[0]) / (n - 1)
        self.da = self.dy * self.dz
        self.Y, self.Z = np.meshgrid(ys, zs, indexing="xy")  # 形状 (nz, ny)
        self.ins = inside(self.Y, self.Z)

    def line_moments(self, theta: float, h: float):
        """水线 y·sinθ+z·cosθ<=h 以下的面积与一阶矩。"""
        s, c = math.sin(theta), math.cos(theta)
        mask = self.ins & ((self.Y * s + self.Z * c) <= h)
        a = mask.sum() * self.da
        if a <= 0.0:
            return 0.0, 0.0, 0.0
        return a, (self.Y[mask].sum() * self.da), (self.Z[mask].sum() * self.da)

    def solve(self, theta: float, area_target: float, iters: int = 60):
        s, c = math.sin(theta), math.cos(theta)
        vals = self.Y[self.ins] * s + self.Z[self.ins] * c
        lo, hi = float(vals.min()), float(vals.max())
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if self.line_moments(theta, mid)[0] < area_target:
                lo = mid
            else:
                hi = mid
        h = 0.5 * (lo + hi)
        a, my, mz = self.line_moments(theta, h)
        return a, my / a, mz / a

    def gz(self, area_target: float, kg: float, angles=ANGLES):
        out = []
        for a in angles:
            th = math.radians(a)
            _, y_b, z_b = self.solve(th, area_target)
            out.append((-y_b) * math.cos(th) + (z_b - kg) * math.sin(th))
        return out


# --------------------------------------------------------------------- 运行


def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if abs(b) > 0 else abs(a)


def run_log(R=5.0, kg_ratio=0.8, n_arc=48):
    kg = kg_ratio * R
    ex_h = log_hydrostatics(R)
    ez = log_gz(R, kg)
    rows = []
    for n in (12, 24, 48, 96):
        poly = [
            (
                R * math.cos(-0.5 * math.pi + 2.0 * math.pi * i / n),
                R + R * math.sin(-0.5 * math.pi + 2.0 * math.pi * i / n),
            )
            for i in range(n)
        ]
        a_t = ex_h["area"]
        _, y0, z0 = H.immersed_section(poly, 0.0, a_t)
        kb_eng = z0
        gz = H.gz_curve_polygon(poly, a_t, kg, ANGLES)
        g_hi = H.gz_curve_polygon(poly, a_t, kg, [1.0])[0]
        g_lo = H.gz_curve_polygon(poly, a_t, kg, [0.5])[0]
        gm_num = (g_hi - g_lo) / math.radians(0.5)  # dGZ/dθ at 0 = GM
        rows.append(
            {
                "n_facets": n,
                "KB_engine": kb_eng,
                "KB_exact": ex_h["KB"],
                "KB_rel_err": _rel(kb_eng, ex_h["KB"]),
                "GM_from_slope": gm_num,
                "GM_exact": ex_h["KM"] - kg,
                "GM_rel_err": _rel(gm_num, ex_h["KM"] - kg),
                "gz_engine": gz,
                "gz_exact": ez,
                "gz_max_abs_err": max(abs(a - b) for a, b in zip(gz, ez)),
                "gz_max_rel_err": max(_rel(a, b) for a, b in zip(gz, ez) if abs(b) > 1e-6),
            }
        )
    return {"R": R, "KG": kg, "hydrostatics_exact": ex_h, "angles": ANGLES, "rows": rows}


def run_filleted(b=20.0, t=6.0, d=9.0, r=1.5, kg=3.0, n_arc=48, grid_n=1200):
    ex = filleted_box_hydrostatics(b, t, r)
    poly = H.section_polygon_bilge_rounded(b, t, d, r, n_arc)
    a_eng = H.submerged_section_area(poly, t)
    _, y0, z0 = H.immersed_section(poly, 0.0, a_eng)
    kb_eng = z0
    gz_eng = H.gz_curve_polygon(poly, a_eng, kg, ANGLES)
    g_hi = H.gz_curve_polygon(poly, a_eng, kg, [1.0])[0]
    g_lo = H.gz_curve_polygon(poly, a_eng, kg, [0.5])[0]
    gm_num = (g_hi - g_lo) / math.radians(0.5)  # dGZ/dθ at 0 = GM

    yb = b / 2.0 - r

    def inside(Y, Z):
        # 与 section_polygon_bilge_rounded 同一闭合剖面（舷侧到甲板 D 才封顶）
        flat = (np.abs(Y) <= yb) & (Z >= 0.0) & (Z <= d)  # 中部直边体至甲板
        side = (np.abs(Y) >= yb) & (np.abs(Y) <= b / 2.0) & (Z >= r) & (Z <= d)
        arc = ((np.abs(Y) - yb) ** 2 + (Z - r) ** 2) <= r * r
        arc = arc & (np.abs(Y) >= yb) & (Z <= r)
        return flat | side | arc

    grid = Grid(inside, (-b / 2.0, b / 2.0), (0.0, d), grid_n)
    a_brute = grid.line_moments(0.0, t)[0]  # 吃水 T 处的浸没面积
    gz_brute = grid.gz(ex["area"], kg)
    # 像素网格收敛：粗网格 GZ 与细网格差
    grid_c = Grid(inside, (-b / 2.0, b / 2.0), (0.0, d), grid_n // 3)
    gz_brute_c = grid_c.gz(ex["area"], kg)

    # 判据"有牙齿"检查：若沿用 Cb·B·T 当面积目标（对圆舭剖面是错的，高估 0.8%）
    gz_wrong = H.gz_curve_polygon(poly, b * t, kg, ANGLES)

    # 直边梯形基线（r=0，等价矩形；本平台 Cb=1 梯形族）
    poly0 = H.section_polygon_bilge_rounded(b, t, d, 0.0)
    gz_box = H.gz_curve_polygon(poly0, b * t, kg, ANGLES)
    ex0 = filleted_box_hydrostatics(b, t, 0.0)

    return {
        "b": b,
        "t": t,
        "d": d,
        "r_bilge": r,
        "n_arc": n_arc,
        "KG": kg,
        "angles": ANGLES,
        "exact": ex,
        "exact_box": ex0,
        "area_engine": a_eng,
        "area_rel_err": _rel(a_eng, ex["area"]),
        "area_brute": a_brute,
        "area_brute_rel_err": _rel(a_brute, ex["area"]),
        "KB_engine": kb_eng,
        "KB_rel_err": _rel(kb_eng, ex["KB"]),
        "GM_from_slope": gm_num,
        "GM_exact": ex["KM"] - kg,
        "GM_rel_err": _rel(gm_num, ex["KM"] - kg),
        "gz_engine": gz_eng,
        "gz_brute": gz_brute,
        "gz_brute_coarse": gz_brute_c,
        "gz_engine_vs_brute_max_abs": max(abs(a - b_) for a, b_ in zip(gz_eng, gz_brute)),
        "gz_engine_vs_brute_max_rel": max(
            _rel(a, b_) for a, b_ in zip(gz_eng, gz_brute) if abs(b_) > 1e-6
        ),
        "brute_grid_convergence_max_abs": max(
            abs(a - b_) for a, b_ in zip(gz_brute, gz_brute_c)
        ),
        "gz_box": gz_box,
        "GM_box_exact": ex0["KM"] - kg,
        "gz_bilge_effect_max_abs": max(abs(a - b_) for a, b_ in zip(gz_eng, gz_box)),
        "gz_wrong_area_target": gz_wrong,
        "gz_area_target_sensitivity_max_abs": max(abs(a - b_) for a, b_ in zip(gz_wrong, gz_eng)),
        "gz_engine_vs_brute_max_rel_gt_0p5m": max(
            _rel(a, b_)
            for a, b_ in zip(gz_eng, gz_brute)
            if abs(b_) >= 0.5
        ),
        "grid_n": grid_n,
    }


def run_wall_sided(b=20.0, t=6.0, d=9.0, cb=1.0, kg=5.0):
    """直边族回归：Cb=1（垂直舷侧）时 GZ 必须与 wall-sided 解析式逐位一致（甲板入水前）。

    注：wall-sided 解析式只在舷侧垂直时精确；梯形族 Cb<1 的斜侧在倾斜后
    不满足该假设，故本回归只取 Cb=1 的浮箱极限。
    """
    res = H.evaluate_stability(b, t, d, cb, kg)
    hi = H.deck_immersion_angle(b, t, d)
    pairs = [(a, g, w) for a, g, w in zip(res.angles, res.gz, res.wall_sided) if a < hi - 1e-9]
    return {
        "b": b,
        "t": t,
        "d": d,
        "cb": cb,
        "KG": kg,
        "deck_immersion_deg": hi,
        "rows": [
            {"angle": a, "gz": g, "gz_wall_sided": w, "abs_err": abs(g - w)} for a, g, w in pairs
        ],
        "max_abs_err": max(abs(g - w) for _, g, w in pairs),
    }


def make_figures(out_dir: str, log_res: dict, fil_res: dict):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    row = [r for r in log_res["rows"] if r["n_facets"] == 48][0]
    ax1.plot(log_res["angles"], row["gz_exact"], "k-", lw=2, label="analytic (circular log)")
    ax1.plot(log_res["angles"], row["gz_engine"], "o", ms=4, mfc="none", label="engine, N=48")
    row12 = [r for r in log_res["rows"] if r["n_facets"] == 12][0]
    ax1.plot(log_res["angles"], row12["gz_engine"], "s", ms=3, mfc="none", label="engine, N=12")
    ax1.set_xlabel("heel angle [deg]")
    ax1.set_ylabel("GZ [m]")
    ax1.set_title(f"circular log R={log_res['R']} m, KG={log_res['KG']:.1f} m")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)

    ns = [r["n_facets"] for r in log_res["rows"]]
    ax2.loglog(ns, [max(r["gz_max_abs_err"], 1e-16) for r in log_res["rows"]], "o-", label="log: |ΔGZ|max")
    ax2.loglog(
        ns,
        [max(abs(r["KB_engine"] - r["KB_exact"]), 1e-16) for r in log_res["rows"]],
        "s--",
        label="log: |ΔKB|",
    )
    ax2.set_xlabel("polygon facets N")
    ax2.set_ylabel("absolute error [m]")
    ax2.set_title("facet-count convergence / error cancellation")
    ax2.grid(alpha=0.3, which="both")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    p1 = os.path.join(out_dir, "fig1_log_gz_and_convergence.png")
    fig.savefig(p1, dpi=130)
    plt.close(fig)

    fig2, (ax3, ax4) = plt.subplots(1, 2, figsize=(11, 4.2))
    ax3.plot(fil_res["angles"], fil_res["gz_engine"], "-", lw=2, label="engine (polygon, rounded bilge)")
    ax3.plot(
        fil_res["angles"],
        fil_res["gz_brute"],
        "o",
        ms=4,
        mfc="none",
        label=f"pixel integration N={fil_res['grid_n']}",
    )
    ax3.plot(fil_res["angles"], fil_res["gz_box"], "k:", lw=1.5, label="sharp bilge (r=0)")
    ax3.set_xlabel("heel angle [deg]")
    ax3.set_ylabel("GZ [m]")
    ax3.set_title(
        f"rounded bilge B={fil_res['b']} T={fil_res['t']} r={fil_res['r_bilge']} m"
    )
    ax3.grid(alpha=0.3)
    ax3.legend(fontsize=8)

    d = [
        [a - b_ for a, b_ in zip(fil_res["gz_engine"], fil_res["gz_brute"])],
        [a - b_ for a, b_ in zip(fil_res["gz_engine"], fil_res["gz_box"])],
    ]
    ax4.axhline(0.0, color="k", lw=0.8)
    ax4.plot(fil_res["angles"], d[0], "o-", label="engine − pixel reference")
    ax4.plot(fil_res["angles"], d[1], "s--", label="rounded − sharp bilge (r effect)")
    ax4.set_xlabel("heel angle [deg]")
    ax4.set_ylabel("ΔGZ [m]")
    ax4.set_title("residuals and bilge-radius effect")
    ax4.grid(alpha=0.3)
    ax4.legend(fontsize=8)
    fig2.tight_layout()
    p2 = os.path.join(out_dir, "fig2_rounded_bilge_residuals.png")
    fig2.savefig(p2, dpi=130)
    plt.close(fig2)
    return [p1, p2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("outputs", "gz_curved"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    log_res = run_log()
    fil_res = run_filleted()
    ws = run_wall_sided()
    figs = make_figures(args.out, log_res, fil_res)
    payload = {
        "log": log_res,
        "filleted_box": fil_res,
        "wall_sided_regression": ws,
        "figures": figs,
    }
    path = os.path.join(args.out, "gz_curved.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)

    print("== circular log (analytic KM = R) ==")
    for r in log_res["rows"]:
        print(
            "  N=%3d KB %8.5f/%.5f (%.2e)  GM %8.5f/%.5f (%.2e)  max|dGZ| %.3e"
            % (
                r["n_facets"],
                r["KB_engine"],
                r["KB_exact"],
                r["KB_rel_err"],
                r["GM_from_slope"],
                r["GM_exact"],
                r["GM_rel_err"],
                r["gz_max_abs_err"],
            )
        )
    print("== rounded bilge ==")
    for k in (
        "area_engine",
        "area_rel_err",
        "KB_engine",
        "KB_rel_err",
        "GM_from_slope",
        "GM_exact",
        "GM_rel_err",
        "gz_engine_vs_brute_max_abs",
        "gz_engine_vs_brute_max_rel",
        "gz_bilge_effect_max_abs",
    ):
        print("  %-32s %s" % (k, fil_res[k]))
    print("== wall-sided regression ==")
    print("  max_abs_err %.3e over %d angles" % (ws["max_abs_err"], len(ws["rows"])))
    print("wrote", path)
    return payload


if __name__ == "__main__":
    main()