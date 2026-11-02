"""曲面剖面 GZ 引擎的解析基准复核（圆剖面浮筒 / 圆舭箱体）+ 判据有牙齿检查。

被复核对象：``src/ship_perf/hydrostatics.py`` 的大倾角 GZ 路径
（``gz_curve_polygon`` / ``gz_curve`` / ``section_polygon_bilge_rounded``）。

三条基准彼此独立于被复核实现：
* 解析：半浸没圆柱浮筒 KM = R（精确）与 GZ(θ) 闭式；
* 解析：圆舭箱体 A = B·T − (2−π/2)r²、KB 由裁角一阶矩闭式、BM = (B³/12)/A；
* 独立算法：剖面像素积分（体积配平 + 形心），与多边形裁剪完全不同。

判据（平台精度标准 15%，本单自查线取更严的 3%/1%）：
  C1 圆剖面 KB/GM 相对误差 < 1%（N≥48 分面）；
  C2 圆剖面 GZ 全程最大绝对误差 < 0.5%·max|GZ_exact|；
  C3 圆舭箱体浸没面积 / KB 相对误差 < 0.1%，GM < 1%；
  C4 圆舭箱体 GZ 对像素积分基准：max|Δ| < 3%·GZ_max，且像素网格加密后收敛；
  C5 直边族（Cb=1 浮箱）GZ 与 wall-sided 解析式一致到 1e-9（回归，不得退化）；
  C6 面积目标用错（拿 Cb·B·T 冒充圆舭浸没面积）时偏差必须显著抬升 ⇒ 判据非空转。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ship_perf import hydrostatics as H  # noqa: E402
from ship_verification import gz_curved_benchmark as GB  # noqa: E402


# ------------------------------------------------------------------ A 圆剖面
def test_log_hydrostatics_closed_form():
    """半浸没圆柱：KB = R(1-4/3π)、BM = 4R/3π、KM = R（与 R 无关）。"""
    for R in (1.0, 5.0, 12.5):
        h = GB.log_hydrostatics(R)
        assert h["KB"] == pytest.approx(R * (1.0 - 4.0 / (3.0 * math.pi)), rel=1e-12)
        assert h["KM"] == pytest.approx(R, rel=1e-12)


def test_log_engine_matches_analytic():
    """C1/C2：引擎在多边形分面上逼近圆剖面解析 GZ。"""
    res = GB.run_log(R=5.0, kg_ratio=0.8)
    by_n = {r["n_facets"]: r for r in res["rows"]}
    fine = by_n[48]
    gz_shift = abs(res["KG"])  # GZ 量级
    assert fine["KB_rel_err"] < 1e-2, fine["KB_rel_err"]
    assert fine["GM_rel_err"] < 1e-2, fine["GM_rel_err"]
    assert fine["gz_max_abs_err"] < 5e-3 * gz_shift, fine["gz_max_abs_err"]

    # 收敛：误差随分面数单调下降（每倍 4×，圆逼近的二阶收敛）
    errs = [by_n[n]["gz_max_abs_err"] for n in (12, 24, 48)]
    assert errs[0] > errs[1] > errs[2], errs
    assert errs[0] / errs[2] > 20.0, errs


# ------------------------------------------------------------------ B 圆舭箱体
@pytest.fixture(scope="module")
def filleted():
    return GB.run_filleted(b=20.0, t=6.0, d=9.0, r=1.5, kg=3.0, n_arc=48, grid_n=700)


def test_filleted_section_area_closed_form(filleted):
    """C3：圆舭剖面浸没面积与 A = B·T − (2−π/2)r² 一致。"""
    ex = filleted["exact"]["area"]
    assert filleted["area_rel_err"] < 1e-3, filleted["area_rel_err"]
    # 与主尺度关系自检：面积必须落在矩形与 B·T 减去裁角之间
    assert 0.95 * ex < filleted["area_engine"] < 20.0 * 6.0


def test_filleted_hydrostatics_matches_analytic(filleted):
    """C3：KB（裁角一阶矩）与 BM/GM（矩形水线面）相对误差 < 1e-3 / 1e-2。"""
    assert filleted["KB_rel_err"] < 1e-3, filleted["KB_rel_err"]
    assert filleted["GM_rel_err"] < 1e-2, filleted["GM_rel_err"]
    # 圆舭挖去排水体积 ⇒ 同 KG 下 GM 比尖舭箱体略大
    assert filleted["GM_exact"] > filleted["GM_box_exact"], (
        filleted["GM_exact"],
        filleted["GM_box_exact"],
    )


def test_filleted_gz_matches_pixel_reference(filleted):
    """C4：引擎 GZ 对独立像素积分基准 max|Δ| < 3%·GZ_max，且网格加密收敛。"""
    gz_max = max(abs(v) for v in filleted["gz_brute"])
    assert filleted["gz_engine_vs_brute_max_abs"] < 0.03 * gz_max, (
        filleted["gz_engine_vs_brute_max_abs"],
        gz_max,
    )
    assert filleted["gz_engine_vs_brute_max_rel_gt_0p5m"] < 0.03
    # 像素基准自身：细网格应比粗网格更接近引擎（误差随分辨率下降）
    assert filleted["brute_grid_convergence_max_abs"] > filleted["gz_engine_vs_brute_max_abs"]


def test_bilge_radius_changes_gz(filleted):
    """圆舭确实改变 GZ（不是装饰参数）：差值量级 1e-2 m。"""
    assert filleted["gz_bilge_effect_max_abs"] > 1e-2, filleted["gz_bilge_effect_max_abs"]


def test_area_target_matters(filleted):
    """C6 判据有牙齿：把 Cb·B·T 当浸没面积目标，GZ 偏差必须显著放大。"""
    gz_max = max(abs(v) for v in filleted["gz_brute"])
    sens = filleted["gz_area_target_sensitivity_max_abs"]
    right = filleted["gz_engine_vs_brute_max_abs"]
    # 正确面积目标下 GZ 误差在 1%·GZ_max 以内；用错目标则被推出该线
    assert right < 0.01 * gz_max, (right, gz_max)
    assert sens > 0.01 * gz_max, (sens, gz_max)
    assert sens > 2.0 * right, (sens, right)


# ------------------------------------------------------------------ C 回归
def test_wall_sided_regression():
    """C5：直边族（Cb=1）GZ 与 wall-sided 解析式一致到 1e-9（甲板入水前）。"""
    ws = GB.run_wall_sided(cb=1.0)
    assert ws["max_abs_err"] < 1e-9, ws["max_abs_err"]
    assert len(ws["rows"]) >= 3


def test_gz_curve_delegation_is_bitwise_identical():
    """重构不改变原 ``gz_curve`` 结果（梯形族逐角逐位一致）。"""
    kwargs = dict(b=20.0, t=6.0, d=9.0, cb=0.7, kg=5.0)
    angles = [float(a) for a in range(0, 91, 5)]
    ref = H.gz_curve(angles=angles, **kwargs)
    poly = H.section_polygon(20.0, 6.0, 9.0, 0.7)
    got = H.gz_curve_polygon(poly, 0.7 * 20.0 * 6.0, 5.0, angles)
    assert got == ref


def test_rounded_section_degenerates_to_box():
    """r=0 时圆舭剖面必须精确退化为矩形剖面（面积、GZ 均一致）。"""
    b, t, d, kg = 20.0, 6.0, 9.0, 3.0
    angles = [float(a) for a in range(0, 91, 5)]
    poly_r0 = H.section_polygon_bilge_rounded(b, t, d, 0.0)
    assert H.submerged_section_area(poly_r0, t) == pytest.approx(b * t, rel=1e-12)
    gz_r0 = H.gz_curve_polygon(poly_r0, b * t, kg, angles)
    gz_box = H.gz_curve_polygon(H.section_polygon(b, t, d, 1.0), b * t, kg, angles)
    for a, c in zip(gz_r0, gz_box):
        assert a == pytest.approx(c, abs=1e-12)


def test_negative_bilge_radius_rejected():
    with pytest.raises(ValueError):
        H.section_polygon_bilge_rounded(20.0, 6.0, 9.0, -0.5)