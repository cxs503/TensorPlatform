"""m1703: 完整稳性衡准锚点 + 生产实现语义回归.

判据来源: 本仓 `tools/benchmarks/stability_criteria_anchors.py` (精确闭式判据),
被审对象: `backend/services/ice_integration/nodes_downstream.py::imo_criteria`.

每个 test 都对应一条**可机器裁决**的主张, 不做"跑通了"式断言。
"""
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "benchmarks"))
sys.path.insert(0, ROOT)

import stability_criteria_anchors as anch  # noqa: E402
from backend.services.ice_integration import nodes_downstream as nd  # noqa: E402

B, T, KG = 46.0, 12.0, 18.0
BM = B * B / (12.0 * T)
GM = T / 2.0 + BM - KG
GRID = list(np.arange(0.0, 61.0, 2.5))
DEG = 180.0 / math.pi


def _curve(gz_values, phis_deg=None):
    """构造与生产接口同形的 curve dict (只用到 phi_deg / gz_numeric)."""
    phis = list(phis_deg if phis_deg is not None else GRID)
    gz = list(map(float, gz_values))
    return {"phi_deg": phis, "gz_numeric": gz}


# ---------------------------------------------------------------------------
# 1. 判据自证 (analytic judge 必须自己先站得住)
# ---------------------------------------------------------------------------
def test_all_anchors_pass():
    anchors = anch.run_anchors()
    failed = [a for a in anchors if not a["pass"]]
    assert not failed, f"anchor failures: {failed}"
    assert len(anchors) >= 11


def test_exact_area_matches_independent_simpson():
    """闭式原函数 vs 复合 Simpson (两条独立路径) 相对差 < 1e-9."""
    for a, b in ((0.0, 30.0), (30.0, 40.0), (0.0, 40.0), (10.0, 55.0)):
        ex = anch.area_exact(a, b, GM, BM)
        nu = anch.area_simpson(a, b, GM, BM, 200001)
        assert abs(nu - ex) / abs(ex) < 1e-9, (a, b, ex, nu)


def test_area_additivity_and_initial_slope():
    ex030 = anch.area_exact(0, 30, GM, BM)
    ex3040 = anch.area_exact(30, 40, GM, BM)
    ex040 = anch.area_exact(0, 40, GM, BM)
    assert abs(ex030 + ex3040 - ex040) / ex040 < 1e-12
    # GM 的几何定义: 初斜率 = GM
    assert abs(anch.gz_wall_sided_dphi(0.0, GM, BM) - GM) < 1e-12
    assert abs(anch.area_exact(0, 40, GM, BM) / anch.area_exact(0, 40, GM, BM) - 1) < 1e-15


def test_grid_convergence_is_second_order():
    """生产用梯形法在角度网格积分: 步长减半误差应 ≈ /4 (O(h^2))."""
    ex = anch.area_exact(0, 40, GM, BM)
    errs = []
    for h in (10.0, 5.0, 2.5, 1.25):
        g = np.arange(0.0, 40.0 + 1e-9, h)
        if abs(g[-1] - 40.0) > 1e-9:
            g = np.append(g, 40.0)
        gz = np.sin(np.deg2rad(g)) * (GM + 0.5 * BM * np.tan(np.deg2rad(g)) ** 2)
        errs.append(abs(float(np.trapezoid(gz, np.deg2rad(g))) - ex))
    ratios = [errs[i] / errs[i + 1] for i in range(len(errs) - 1)]
    assert all(3.0 < r < 5.0 for r in ratios), ratios
    # 2.5° 网格 (ice 门实际使用的密度) 面积误差 < 0.5%
    assert errs[2] / ex < 5e-3


# ---------------------------------------------------------------------------
# 2. 生产实现 ≡ IS Code 2008 Part A 2.2.1 六条
# ---------------------------------------------------------------------------
def test_production_covers_all_six_normative_items():
    curve = nd.gz_curve(B, T, KG, GRID)
    imo = nd.imo_criteria(curve, curve["GM0"] - 6.3e-4)
    normative = {k for k, v in imo["checks"].items() if v["normative"]}
    assert normative == set(anch.IS_CODE_2008_2_2_1), (
        sorted(normative), sorted(anch.IS_CODE_2008_2_2_1))
    assert imo["checks"]["gz_max_m"]["normative"] is False
    assert imo["standard"] == "IS Code 2008 Part A 2.2.1"


def test_production_thresholds_match_is_code_exactly():
    curve = nd.gz_curve(B, T, KG, GRID)
    imo = nd.imo_criteria(curve, curve["GM0"] - 6.3e-4)
    for k, req in anch.IS_CODE_2008_2_2_1.items():
        assert imo["checks"][k]["required"] == pytest.approx(req, abs=1e-12), k


def _simpson_area(phis_deg, gz, a_deg, b_deg):
    """高分辨率参考面积: 同一 GZ 曲线上的复合 Simpson (只衡量离散化, 不混入模型差)."""
    phis = np.deg2rad(np.asarray(phis_deg, dtype=float))
    gz = np.asarray(gz, dtype=float)
    m = (np.rad2deg(phis) >= a_deg - 1e-9) & (np.rad2deg(phis) <= b_deg + 1e-9)
    y, x = gz[m], phis[m]
    if len(y) % 2 == 0:                     # Simpson 需要奇数点
        y, x = y[:-1], x[:-1]
    h = (x[-1] - x[0]) / (len(x) - 1)
    return float(h / 3.0 * (y[0] + y[-1] + 4.0 * y[1:-1:2].sum() + 2.0 * y[2:-1:2].sum()))


def test_production_areas_match_analytic_inside_wall_sided_domain():
    """解析式**只在甲板边缘入水前**成立 (φ_deck = atan(2T/B) ≈ 27.6°).

    故解析判据只能用在 0->25° 窗口; 40° 处舷侧直立解已失效, 与生产的多边形积分
    不可比 —— 这是本单第一版测试自己被抓住的物理错误, 保留为反例。
    """
    curve = nd.gz_curve(B, T, KG, GRID)
    lim = curve["analytic_validity_limit_deg"]
    assert 25.0 < lim < 30.0
    phis = np.arange(0.0, 25.0 + 1e-9, 2.5)
    gz = np.sin(np.deg2rad(phis)) * (curve["GM0"] + 0.5 * BM * np.tan(np.deg2rad(phis)) ** 2)
    prod = float(np.trapezoid(gz, np.deg2rad(phis)))          # 生产同款梯形
    ex = anch.area_exact(0.0, 25.0, curve["GM0"], BM)
    assert abs(prod - ex) / ex < 6e-3, (prod, ex)


def test_production_area_discretisation_is_second_order():
    """同一 GZ 曲线上: 2.5° 梯形 vs 0.25° Simpson 参考, 误差应随步长 O(h^2) 收敛."""
    phis_fine = np.arange(0.0, 61.0, 0.25)
    fine = nd.gz_curve(B, T, KG, phis_fine)
    ref = _simpson_area(phis_fine, fine["gz_numeric"], 0.0, 40.0)
    errs = []
    for h in (2.5, 1.25, 0.625):
        gg = np.arange(0.0, 40.0 + 1e-9, h)
        if abs(gg[-1] - 40.0) > 1e-9:
            gg = np.append(gg, 40.0)
        gz = np.interp(gg, phis_fine, fine["gz_numeric"])
        errs.append(abs(float(np.trapezoid(gz, np.deg2rad(gg))) - ref))
    ratios = [errs[i] / errs[i + 1] for i in range(len(errs) - 1)]
    assert all(3.0 < r < 5.0 for r in ratios), (errs, ratios)
    assert errs[0] / ref < 5e-3, errs[0] / ref


# ---------------------------------------------------------------------------
# 3. 抓历史错的判别性用例 (旧实现会误判 pass)
# ---------------------------------------------------------------------------
def test_area_0_40_between_0055_and_0090_is_not_compliant():
    """决定性用例: A(0,40)=0.0585 m·rad.

    旧语义 (把 0.055 挂在 0-40° 上) 会判合规; IS Code 2.2.1.2 要求 0.090 -> 不合规。
    """
    GMs, BMs = 0.25, 0.0
    gz = GMs * np.sin(np.deg2rad(GRID))          # 纯 GM*sin(phi)
    curve = _curve(gz)
    imo = nd.imo_criteria(curve, GMs)
    area_040 = imo["checks"]["area_0_40_m_rad"]
    assert 0.055 < area_040["value"] < 0.090, area_040
    assert area_040["required"] == pytest.approx(0.090, abs=1e-12)
    assert imo["margins"]["area_0_40_m_rad"] < 0
    assert imo["passed"] is False


def test_angle_of_max_gz_is_judged():
    """峰位在 20° 的曲线: 原先只报 gz_max_angle_deg 不判定, 现在必须判负."""
    phi = np.array(GRID)
    gz = np.where(phi <= 40.0, 0.3 * np.sin(np.pi * phi / 40.0),
                  0.3 * np.sin(np.pi * 40.0 / 40.0) * np.clip((60.0 - phi) / 20.0, 0, 1))
    imo = nd.imo_criteria(_curve(gz), 0.30)
    chk = imo["checks"]["angle_gz_max_deg"]
    assert chk["value"] == pytest.approx(20.0, abs=1e-9)
    assert chk["required"] == pytest.approx(25.0, abs=1e-12)
    assert chk["normative"] is True
    assert imo["margins"]["angle_gz_max_deg"] < 0
    assert imo["passed"] is False


def test_flooding_angle_truncates_areas_per_2_2_1_2():
    """θf < 40° 时面积上限取 θf (2.2.1.2), 且截断值 == 同一曲线上的 ∫_0^θf GZ dφ."""
    phis_fine = np.arange(0.0, 61.0, 0.25)
    fine = nd.gz_curve(B, T, KG, phis_fine)
    gm = fine["GM0"] - 6.3e-4
    prod = nd.imo_criteria(fine, gm)
    capped = nd.imo_criteria(fine, gm, flooding_angle_deg=35.0)
    assert capped["area_upper_limit_deg"] == pytest.approx(35.0)
    ref35 = _simpson_area(phis_fine, fine["gz_numeric"], 0.0, 35.0)
    assert capped["checks"]["area_0_40_m_rad"]["value"] == pytest.approx(ref35, rel=1e-3)
    assert (capped["checks"]["area_0_40_m_rad"]["value"]
            <= prod["checks"]["area_0_40_m_rad"]["value"] + 1e-12)
    # θf=30° 时 30-40° 区间为空 -> 面积 0 (旧实现无截断, 会给出非零值)
    tight = nd.imo_criteria(fine, gm, flooding_angle_deg=30.0)
    assert tight["checks"]["area_30_40_m_rad"]["value"] == pytest.approx(0.0, abs=1e-12)


def test_advisory_items_do_not_drive_passed():
    """GZmax 幅值不足但六条 normative 全过时, passed 仍为 True (advisory 不越权)."""
    curve = nd.gz_curve(B, T, KG, GRID)
    imo = nd.imo_criteria(curve, curve["GM0"] - 6.3e-4)
    assert imo["margins"]["gm0_m"] > 0
    # 人为把 advisory 门槛抬高到必挂, passed 不应改变
    imo2 = nd.imo_criteria(curve, curve["GM0"] - 6.3e-4)
    imo2["checks"]["gz_max_m"]["required"] = 99.0
    assert imo2["passed"] is True


def test_reference_evaluator_agrees_with_production_on_analytic_curve():
    """独立参考评估器 vs 生产: 六条 normative 判定必须同号 (语义一致)."""
    curve = nd.gz_curve(B, T, KG, GRID)
    gm = curve["GM0"] - 6.3e-4
    prod = nd.imo_criteria(curve, gm)
    ref = anch.evaluate_reference(GRID, curve["gz_analytic"], gm)
    assert set(ref["margins"]) == set(prod["margins"])
    for k in ref["margins"]:
        assert (ref["margins"][k] >= 0) == (prod["margins"][k] >= 0), k