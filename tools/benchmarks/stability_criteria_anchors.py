"""完整稳性衡准锚点套件 (m1703) —— 对 IS Code 2008 Part A 2.2.1 的可机器裁决判据.

背景 / 为什么需要本文件
------------------------
`calc.stability` 在能力清单里是 **REAL**, 但"衡准"这一层长期只能靠人眼比对规范条文。
判据缺失时, 阈值错配 (把 0-40° 的门槛写成 0-30° 的 0.055) 与被漏掉的条目
(最大复原力臂角 >= 25°) 都**不会**被任何测试抓到 —— 因为判据本身不存在。

本文件提供三件互相独立的东西:

1. **精确判据 (analytic truth)**: 舷侧直立 (wall-sided) 体的 GZ 闭式解

       GZ(phi) = sin(phi) * (GM + 0.5*BM*tan^2(phi))

   的面积有**初等原函数**:

       ∫ sin(phi) dphi                     = -cos(phi)
       ∫ sin(phi)*tan^2(phi) dphi          = sec(phi) + cos(phi)      (导数自检 inside)

   故 A(a,b) = GM*(cos a - cos b) + 0.5*BM*[(sec b + cos b) - (sec a + cos a)]
   是**零近似**的解析值 —— 不需要任何文献表值, 也不需要参考实现。

2. **离散化误差可测**: 生产实现用梯形法在给定角度网格上积分, 本文件量化
   该误差并验证它按 O(h^2) 收敛, 从而回答"网格够不够密"这个可判定问题。

3. **规范条目表 (IS_CODE_2008_2_2_1)**: 六条 normative 门槛 + 明确标注哪些
   门槛**不属于**该节 (advisory), 使 `passed` 的语义可被审计。

用法
----
    python3 tools/benchmarks/stability_criteria_anchors.py --out artifacts/m1703
"""
from __future__ import annotations

import argparse
import json
import math
import os
from typing import Dict, List, Optional, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# 规范条目表: IS Code 2008 (Intact Stability Code), Part A, 2.2.1
# 数值为该条文定义的阈值本身 (= 定义式, 非"文献表值"), 单位见 key 后缀。
# ---------------------------------------------------------------------------
IS_CODE_2008_2_2_1: Dict[str, float] = {
    "area_0_30_m_rad": 0.055,   # 2.2.1.1  GZ 曲线 0-30° 下面积
    "area_0_40_m_rad": 0.090,   # 2.2.1.2  0-40° 或进水角 θf (取小者) 下面积
    "area_30_40_m_rad": 0.030,  # 2.2.1.3  30-40° (或 θf) 之间面积
    "gz_at_30_m": 0.200,        # 2.2.1.4  30° 处复原力臂
    "angle_gz_max_deg": 25.0,   # 2.2.1.5  最大复原力臂对应角
    "gm0_m": 0.150,             # 2.2.1.6  初稳性高 (或用横摇周期衡准替代)
}

# 常被混入 IMO 语境、但**不在** Part A 2.2.1 六条之内的门槛:
# 本文件把它们保留为 advisory (可报出、不计入 passed), 以免"看起来更严"掩盖语义挪用。
ADVISORY_ONLY: Dict[str, float] = {
    "gz_max_m": 0.250,          # 部分船级社/船型附加要求, 非本条文
    "vanishing_angle_deg": 55.0,  # 常见旗国/船级社要求, 非本条文
}

DEG = 180.0 / math.pi


# ---------------------------------------------------------------------------
# 1. 解析判据
# ---------------------------------------------------------------------------
def gz_wall_sided(phi_rad: float, gm_m: float, bm_m: float) -> float:
    """舷侧直立体复原力臂闭式解 (仅在浸湿剖面为等宽梯形时严格成立)."""
    t = math.tan(phi_rad)
    return math.sin(phi_rad) * (gm_m + 0.5 * bm_m * t * t)


def gz_wall_sided_dphi(phi_rad: float, gm_m: float, bm_m: float) -> float:
    """dGZ/dphi 闭式解 —— 用于精确定位最大复原力臂角 (不需要扫格点)."""
    s, c, t = math.sin(phi_rad), math.cos(phi_rad), math.tan(phi_rad)
    return c * (gm_m + 0.5 * bm_m * t * t) + s * bm_m * t / (c * c)


def area_exact(a_deg: float, b_deg: float, gm_m: float, bm_m: float) -> float:
    """解析面积 A(a,b) = ∫_a^b GZ dphi  (m·rad). 无截断误差."""
    a, b = a_deg / DEG, b_deg / DEG
    sec = lambda x: 1.0 / math.cos(x)  # noqa: E731
    lin = gm_m * (math.cos(a) - math.cos(b))
    cub = 0.5 * bm_m * ((sec(b) + math.cos(b)) - (sec(a) + math.cos(a)))
    return lin + cub


def area_simpson(a_deg: float, b_deg: float, gm_m: float, bm_m: float, n: int = 20001) -> float:
    """高分辨率复合 Simpson —— 与 area_exact 相互独立的第二条计算路径."""
    if n % 2 == 0:
        n += 1
    phis = np.linspace(a_deg / DEG, b_deg / DEG, n)
    gz = np.sin(phis) * (gm_m + 0.5 * bm_m * np.tan(phis) ** 2)
    return float(_composite_simpson(gz, phis))


def _composite_simpson(y: np.ndarray, x: np.ndarray) -> float:
    h = (x[-1] - x[0]) / (len(x) - 1)
    return float(h / 3.0 * (y[0] + y[-1] + 4.0 * y[1:-1:2].sum() + 2.0 * y[2:-1:2].sum()))


def angle_gz_max_exact(gm_m: float, bm_m: float, hi_deg: float = 89.0) -> float:
    """最大复原力臂角: 在 (0, hi) 上对 GZ' 二分求根 (解析导数, 无网格分辨率误差)."""
    lo, hi = 0.0, hi_deg / DEG
    if gz_wall_sided_dphi(lo, gm_m, bm_m) <= 0.0:
        return 0.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if gz_wall_sided_dphi(mid, gm_m, bm_m) > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi) * DEG


def gz_at_30_exact(gm_m: float, bm_m: float) -> float:
    return gz_wall_sided(30.0 / DEG, gm_m, bm_m)


# ---------------------------------------------------------------------------
# 2. 参考衡准评估器 (独立于生产实现)
# ---------------------------------------------------------------------------
def evaluate_reference(
    phis_deg: Sequence[float],
    gz_m: Sequence[float],
    gm0_m: float,
    flooding_angle_deg: Optional[float] = None,
    max_heel_deg: float = 90.0,
) -> Dict[str, object]:
    """按 IS Code 2008 Part A 2.2.1 评估一条 GZ 曲线 (参考实现).

    与生产实现的差别 (即审计关注的语义点):
      * 面积上限取 ``min(40°, θf)`` —— θf 小于 40° 时按 θf 截断;
      * 30° 处力臂与最大力臂**角**都单独判定;
      * 最大力臂**幅值**与消失角只作 advisory, 不计入 ``passed``。
    """
    phis = np.deg2rad(np.asarray(phis_deg, dtype=float))
    gz = np.asarray(gz_m, dtype=float)
    lim = min(40.0, flooding_angle_deg) if flooding_angle_deg else 40.0
    lim = min(lim, max_heel_deg)

    def area(a_deg: float, b_deg: float) -> float:
        a_deg, b_deg = min(a_deg, lim), min(b_deg, lim)
        if b_deg <= a_deg:
            return 0.0
        m = (np.rad2deg(phis) >= a_deg - 1e-9) & (np.rad2deg(phis) <= b_deg + 1e-9)
        return float(np.trapezoid(gz[m], phis[m])) if m.sum() > 1 else 0.0

    phi_max_deg = float(np.rad2deg(phis[int(np.argmax(gz))]))
    values = {
        "area_0_30_m_rad": area(0.0, 30.0),
        "area_0_40_m_rad": area(0.0, lim),
        "area_30_40_m_rad": area(30.0, lim),
        "gz_at_30_m": float(np.interp(30.0, np.rad2deg(phis), gz)),
        "angle_gz_max_deg": phi_max_deg,
        "gm0_m": float(gm0_m),
    }
    checks = {
        k: {"value": values[k], "required": IS_CODE_2008_2_2_1[k]}
        for k in IS_CODE_2008_2_2_1
    }
    margins = {k: checks[k]["value"] - checks[k]["required"] for k in checks}
    advisory = {
        "gz_max_m": {"value": float(gz.max()), "required": ADVISORY_ONLY["gz_max_m"]},
        "vanishing_angle_deg": {
            "value": float(np.interp(0.0, gz[::-1], np.rad2deg(phis)[::-1]))
            if gz.min() <= 0 < gz.max() else float("nan"),
            "required": ADVISORY_ONLY["vanishing_angle_deg"],
        },
    }
    return {
        "limits_deg": {"upper": lim, "flooding_angle_deg": flooding_angle_deg},
        "checks": checks,
        "margins": margins,
        "min_margin": float(min(margins.values())),
        "advisory": advisory,
        "passed": bool(all(v >= -1e-12 for v in margins.values())),
    }


# ---------------------------------------------------------------------------
# 3. 锚点
# ---------------------------------------------------------------------------
def run_anchors() -> List[Dict[str, object]]:
    """锚点清单: 每条都给出 (判据 / 实测 / 容差 / 结论), 全部可离线自证."""
    A: List[Dict[str, object]] = []

    def add(aid: str, what: str, value: float, tol: float, note: str = "") -> None:
        A.append({
            "id": aid,
            "criterion": what,
            "value": value,
            "tolerance": tol,
            "pass": bool(abs(value) <= tol),
            "note": note,
        })

    # 判例船: 与 ice 集成门同一组参数 (B=46, T=12, KG=18), 便于交叉对照
    B, T, KG = 46.0, 12.0, 18.0
    BM = B * B / (12.0 * T)
    KB = T / 2.0
    GM = KB + BM - KG

    # S-A1 解析原函数 vs 数值积分 (两条独立路径互证, 抓符号/系数错)
    for aid, (a, b) in {"S-A1a": (0.0, 30.0), "S-A1b": (30.0, 40.0), "S-A1c": (0.0, 40.0)}.items():
        ex = area_exact(a, b, GM, BM)
        nu = area_simpson(a, b, GM, BM, 200001)
        add(aid, f"area_exact vs Simpson on [{a:g},{b:g}]deg (rel)", (nu - ex) / ex, 1e-9,
            f"exact={ex:.9f} m*rad")

    # S-A2 面积可加性: A(0,30)+A(30,40) == A(0,40) —— 快照错位/端点重复会被抓
    add(
        "S-A2",
        "area additivity A(0,30)+A(30,40)-A(0,40) (rel)",
        (area_exact(0, 30, GM, BM) + area_exact(30, 40, GM, BM) - area_exact(0, 40, GM, BM))
        / area_exact(0, 40, GM, BM),
        1e-12,
    )

    # S-B1 GZ(0) == 0 (任何 GM)
    add("S-B1", "GZ(phi=0) (abs, m)", gz_wall_sided(0.0, GM, BM), 1e-15)

    # S-B2 初斜率 == GM: d GZ/dphi |_0 = GM —— GM 的几何定义
    add("S-B2", "dGZ/dphi|_0 - GM (abs)", gz_wall_sided_dphi(0.0, GM, BM) - GM, 1e-14)

    # S-B3 解析最大值角 vs 细网格 argmax
    phis_fine = np.arange(0.0, 60.0 + 1e-9, 0.01)
    gz_fine = np.sin(np.deg2rad(phis_fine)) * (
        GM + 0.5 * BM * np.tan(np.deg2rad(phis_fine)) ** 2
    )
    add("S-B3", "argmax角 exact - 细网格argmax (deg)",
        angle_gz_max_exact(GM, BM, 60.0) - float(phis_fine[int(np.argmax(gz_fine))]), 5e-3)

    # S-C1 生产离散化 (梯形, 2.5° 网格) 的面积误差量级
    grid = np.arange(0.0, 61.0, 2.5)
    gz_grid = np.sin(np.deg2rad(grid)) * (GM + 0.5 * BM * np.tan(np.deg2rad(grid)) ** 2)
    trap_0_40 = float(np.trapezoid(gz_grid[grid <= 40.0 + 1e-9],
                                   np.deg2rad(grid[grid <= 40.0 + 1e-9])))
    ex_0_40 = area_exact(0, 40, GM, BM)
    add("S-C1", "trapezoid(2.5deg grid) rel error vs exact on A(0,40)",
        (trap_0_40 - ex_0_40) / ex_0_40, 5e-3,
        f"trap={trap_0_40:.6f} exact={ex_0_40:.6f}")

    # S-C2 网格收敛: 步长减半, 误差应 ≈ /4 (O(h^2))
    errs = []
    for h in (10.0, 5.0, 2.5, 1.25):
        g = np.arange(0.0, 40.0 + 1e-9, h)
        if abs(g[-1] - 40.0) > 1e-9:
            g = np.append(g, 40.0)
        gz = np.sin(np.deg2rad(g)) * (GM + 0.5 * BM * np.tan(np.deg2rad(g)) ** 2)
        errs.append(abs(float(np.trapezoid(gz, np.deg2rad(g))) - ex_0_40))
    ratios = [errs[i] / errs[i + 1] for i in range(len(errs) - 1)]
    add("S-C2", "grid-convergence ratio (expect ~4 for O(h^2)); |ratio-4| max",
        max(abs(r - 4.0) for r in ratios), 1.0, f"ratios={['%.3f' % r for r in ratios]}")

    # S-D1 单调性: GM 减小 -> A(0,30) 与最小裕度都必须严格减小 (灵敏度方向)
    gms = [GM, GM * 0.75, GM * 0.5]
    areas_030 = [area_exact(0, 30, g, BM) for g in gms]
    mono = all(areas_030[i] > areas_030[i + 1] for i in range(len(areas_030) - 1))
    A.append({
        "id": "S-D1",
        "criterion": "A(0,30) strictly decreasing in GM",
        "value": 0.0 if mono else 1.0,
        "tolerance": 0.0,
        "pass": mono,
        "note": "areas=" + ", ".join(f"{a:.6f}" for a in areas_030),
    })

    # S-E1 θf 截断语义: 上限取 min(40, θf) 时面积必须单调不增
    full = evaluate_reference(list(grid), gz_grid.tolist(), GM)["checks"]["area_0_40_m_rad"]["value"]
    capped = evaluate_reference(list(grid), gz_grid.tolist(), GM,
                                flooding_angle_deg=35.0)["checks"]["area_0_40_m_rad"]["value"]
    A.append({
        "id": "S-E1",
        "criterion": "flooding-angle capping: A(0,min(40,theta_f)) <= A(0,40) and equals analytic A(0,35)",
        "value": abs(capped - area_exact(0, 35, GM, BM)),
        "tolerance": 2e-3,
        "pass": bool(capped <= full + 1e-12 and abs(capped - area_exact(0, 35, GM, BM)) < 2e-3),
        "note": f"full={full:.6f} capped={capped:.6f} exact35={area_exact(0, 35, GM, BM):.6f}",
    })
    return A


# ---------------------------------------------------------------------------
# 4. 生产实现对照 (in-repo: ice 集成节点)
# ---------------------------------------------------------------------------
def compare_production(B: float = 46.0, T: float = 12.0, KG: float = 18.0) -> Dict[str, object]:
    """把 in-repo `imo_criteria` 的条目集合/数值与规范条目表逐项对照."""
    import sys
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from backend.services.ice_integration import nodes_downstream as nd

    BM = B * B / (12.0 * T)
    KB = T / 2.0
    GM = KB + BM - KG
    grid = list(np.arange(0.0, 61.0, 2.5))
    curve = nd.gz_curve(B, T, KG, grid)
    prod = nd.imo_criteria(curve, curve["GM0"] - 6.3e-4)
    ref = evaluate_reference(grid, curve["gz_analytic"], curve["GM0"] - 6.3e-4)

    prod_keys = set(prod["checks"])
    norm_keys = set(IS_CODE_2008_2_2_1)
    adv_keys = set(ADVISORY_ONLY)
    deltas = {}
    for k in norm_keys & prod_keys:
        if k in ref["checks"]:
            r = ref["checks"][k]["value"]
            p = prod["checks"][k]["value"]
            deltas[k] = {
                "production": p,
                "reference": r,
                "abs_diff": abs(p - r),
                "rel_diff": abs(p - r) / abs(r) if abs(r) > 1e-12 else float("nan"),
            }
    return {
        "case": {"B": B, "T": T, "KG": KG, "BM": BM, "KB": KB, "GM0": GM},
        "production_missing_normative": sorted(norm_keys - prod_keys),
        "production_extra_non_normative": sorted((prod_keys - norm_keys) & adv_keys),
        "production_unknown_keys": sorted(prod_keys - norm_keys - adv_keys),
        "threshold_mismatch": {
            k: {"production": prod["checks"][k]["required"], "is_code": IS_CODE_2008_2_2_1[k]}
            for k in norm_keys & prod_keys
            if abs(prod["checks"][k]["required"] - IS_CODE_2008_2_2_1[k]) > 1e-12
        },
        "value_deltas": deltas,
        "production_passed": prod["passed"],
        "reference_passed": ref["passed"],
        "production_gz_curve_rel_err": curve["max_relative_error"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/m1703")
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    anchors = run_anchors()
    prod = compare_production()

    n_pass = sum(1 for a in anchors if a["pass"])
    worst = max((abs(a["value"]) for a in anchors if a["tolerance"] > 0), default=0.0)
    summary = {
        "anchors_total": len(anchors),
        "anchors_passed": n_pass,
        "anchors_failed": len(anchors) - n_pass,
        "worst_abs_deviation": worst,
        "is_code_2008_2_2_1": IS_CODE_2008_2_2_1,
        "advisory_only": ADVISORY_ONLY,
        "production_compare": prod,
    }
    with open(os.path.join(args.out, "stability_criteria_anchors.json"), "w") as f:
        json.dump({"summary": summary, "anchors": anchors}, f, indent=2, ensure_ascii=False)

    print(f"anchors: {n_pass}/{len(anchors)} pass; worst |deviation| = {worst:.3e}")
    for a in anchors:
        print(f"  [{'PASS' if a['pass'] else 'FAIL'}] {a['id']:<6} {a['criterion'][:70]:<70} "
              f"val={a['value']:+.3e} tol={a['tolerance']:.1e}")
    print("\nproduction vs IS Code 2008 Part A 2.2.1:")
    print("  missing normative items :", prod["production_missing_normative"])
    print("  extra non-normative     :", prod["production_extra_non_normative"])
    print("  threshold mismatches    :", prod["threshold_mismatch"])

    if not args.no_figure:
        make_figure(os.path.join(args.out, "fig_stability_criteria.png"), prod)
        print("figure ->", os.path.join(args.out, "fig_stability_criteria.png"))
    return 0 if n_pass == len(anchors) else 1


def make_figure(path: str, prod: Dict[str, object]) -> None:
    """左: GZ 曲线 (解析 vs 离散) + 衡准关注角; 右: 规范裕度条形图."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    case = prod["case"]
    B, T, KG = case["B"], case["T"], case["KG"]
    BM, GM = case["BM"], case["GM0"]
    grid = np.arange(0.0, 61.0, 2.5)
    gz = np.sin(np.deg2rad(grid)) * (GM + 0.5 * BM * np.tan(np.deg2rad(grid)) ** 2)
    fine = np.linspace(0.0, 60.0, 600)
    gz_fine = np.sin(np.deg2rad(fine)) * (GM + 0.5 * BM * np.tan(np.deg2rad(fine)) ** 2)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.6))

    # -- 左: GZ 曲线 --
    ax1.fill_between(fine, 0, gz_fine, where=fine <= 30, color="#4c72b0", alpha=0.18,
                     label="A(0,30) = %.4f m·rad (exact)" % area_exact(0, 30, GM, BM))
    ax1.fill_between(fine, 0, gz_fine, where=(fine >= 30) & (fine <= 40), color="#dd8452",
                     alpha=0.28, label="A(30,40) = %.4f (exact)" % area_exact(30, 40, GM, BM))
    ax1.plot(fine, gz_fine, "k-", lw=1.6, label="GZ closed form (wall-sided)")
    ax1.plot(grid, gz, "o", ms=3.5, color="#c44e52", label="production grid (2.5°)")
    for ang, lbl in ((30.0, "30°"), (40.0, "40°")):
        ax1.axvline(ang, color="grey", ls=":", lw=1)
        ax1.text(ang, 0.02, lbl, fontsize=8, color="grey", ha="center")
    ax1.set_xlabel("heel angle φ (deg)")
    ax1.set_ylabel("GZ (m)")
    ax1.set_title(f"GZ curve — box B={B:g} T={T:g} KG={KG:g} (GM={GM:.3f} m)")
    ax1.legend(fontsize=7.5, loc="upper left")
    ax1.grid(alpha=0.3)

    # -- 右: 规范裕度 --
    ref = evaluate_reference(list(grid), gz.tolist(), GM)
    keys = ["area_0_30_m_rad", "area_0_40_m_rad", "area_30_40_m_rad",
            "gz_at_30_m", "angle_gz_max_deg", "gm0_m"]
    labels = ["A(0,30)≥0.055", "A(0,40)≥0.090", "A(30,40)≥0.030",
              "GZ(30°)≥0.20 m", "φ(GZmax)≥25°", "GM₀≥0.15 m"]
    cur = np.array([ref["checks"][k]["value"] for k in keys])
    req = np.array([IS_CODE_2008_2_2_1[k] for k in keys])
    marg = cur - req
    colors = ["#55a868" if m >= 0 else "#c44e52" for m in marg]
    y = np.arange(len(keys))
    ax2.barh(y, marg, color=colors)
    ax2.set_yticks(y, labels, fontsize=8.5)
    ax2.axvline(0, color="k", lw=1)
    for i, (m, c) in enumerate(zip(marg, cur)):
        ax2.text(m + (0.02 * max(marg.max(), 1e-9)) * (1 if m >= 0 else -1), i,
                 f"val={c:.3f}", va="center", fontsize=7.5)
    ax2.set_xlabel("margin = value − IS Code 2008 Part A 2.2.1 threshold")
    ax2.set_title("Normative margins (reference evaluator)")
    ax2.grid(alpha=0.3, axis="x")

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())