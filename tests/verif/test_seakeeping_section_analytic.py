"""m1797 —— 耐波内核 2D 剖面水动力对解析基准的复核闸门（离线、只读）。

复核对象：``backend/api/seakeeping_physics.py``（替代 MOCK 单自由度振子的 STF 型切片内核，
mission 1515 审计 D5 之后合入）。该内核的 2D 水动力系数为

    A33_i = ρ·A_i·f_a(ν)，B33_i = ρ·ω_e·b_i²·f_b(ν)，  ν = ω_e²T/g
    f_a(ν) = 1 - 0.25ν²/(1+ν²)     f_b(ν) = 1.6ν/(1+ν²)

f_a/f_b 只依赖 ν、与剖面几何无关。本测试用经典势流解析基准（自由面 ν→0 刚性壁 + 镜像法 +
半椭圆闭式解，见 ``artifacts/verif/seakeeping_section/lewis_reference.py``）逐项复核：

  L1 参考实现自校验 —— 半圆极限比值为 1、平板极限发散、深水色散精确；
  L2 解析可验证项必须通过 —— kL→0 时垂荡 RAO→1、纵摇 RAO→1（rad/(ka)）；色散 k=ω²/g；
  L3 缺陷锁 —— 零频附加质量对半宽半椭圆应为 ρπa²/2，内核恒为 ρ·A（形状盲），
      偏差应落在 evidence.json 记录的带内；剖面族比值恒为 1（形状盲指纹）。

设计意图：L3 的比值钉死是「修复即需复核」闸门 —— 若有人把 f_a 换成形状相关（Lewis/Ursel）
真解，test_kernel_section_law_is_shape_blind 与 L3 的带宽断言会失败，强制重跑 probe.py
并复核 evidence.json，而不是让旧结论悄悄失效。

运行：python3 -m pytest tests/verif/test_seakeeping_section_analytic.py -q
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
#: 参考核与证据工件属**版本化验证资产**（artifacts/ 会被合并门禁滤除）：版本化路径优先。
VDIR_CANDIDATES = [
    os.path.join(ROOT, "tests", "data", "verifier_assets", "seakeeping_section"),
    os.path.join(ROOT, "artifacts", "verif", "seakeeping_section"),
]
VDIR = next((d for d in VDIR_CANDIDATES
             if os.path.exists(os.path.join(d, "lewis_reference.py"))), None)
assert VDIR is not None, "lewis_reference.py 缺失（候选: %s）——版本化验证资产不得删除" % VDIR_CANDIDATES
sys.path.insert(0, VDIR)

import lewis_reference as ref  # noqa: E402

RHO = ref.RHO_DEFAULT

SHIPS = {
    "KVLCC2": dict(L=320.0, B=58.0, T=20.8, Cb=0.8098, Cwp=0.90,
                   displacement=320_000.0, GM=0.50, KG=18.6, speed=15.0),
    "KCS": dict(L=230.0, B=32.2, T=10.8, Cb=0.6505, Cwp=0.85,
                displacement=52_030.0, GM=0.60, KG=7.3, speed=15.0),
    "BARGE60": dict(L=60.0, B=15.0, T=3.0, Cb=0.85, Cwp=0.90,
                    displacement=2_295.0, GM=1.0, KG=2.5, speed=0.0),
}

#: evidence.json 记录的当前偏差带（相对解析参考）。修复后必须同步收紧。
ERR_BAND = {
    "KVLCC2": (-0.20, 0.00),
    "KCS": (-0.35, -0.15),
    "BARGE60": (-0.55, -0.35),
}


@pytest.fixture(scope="module")
def kernel():
    path = os.path.join(ROOT, "backend", "api", "seakeeping_physics.py")
    spec = importlib.util.spec_from_file_location("seakeeping_physics_m1797_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def evidence():
    with open(os.path.join(VDIR, "evidence.json")) as f:
        return json.load(f)


# ---------------------------------------------------------------- L1 参考自校验
def test_reference_semicircle_limit_is_unity():
    """半圆剖面（a=T）零频附加质量比 = 1（式 3 的经典极限）。"""
    a = T = 4.3
    a33 = ref.hemi_ellipse_zero_freq_added_mass(a, RHO)
    area = math.pi * a * T / 2.0
    assert a33 / (RHO * area) == pytest.approx(1.0, rel=1e-12)
    assert ref.zero_freq_ratio(1.0) == 1.0


def test_reference_flat_plate_limit_diverges():
    """T→0（固定半宽）平板极限：A33 有限而面积→0 ⇒ 比值发散（式 3）。"""
    a = 5.0
    a33 = ref.hemi_ellipse_zero_freq_added_mass(a, RHO)
    assert a33 == pytest.approx(RHO * math.pi * a * a / 2.0, rel=1e-12)
    for T in (0.5, 0.1, 0.01):
        ratio = ref.zero_freq_ratio(a / T)
        assert ratio > 9.0  # 远大于 1：形状盲构式必然失效
    assert ref.zero_freq_ratio(a / 0.01) > ref.zero_freq_ratio(a / 0.5)


def test_reference_equivalent_ellipse_is_consistent():
    """等价半椭圆吃水反解自洽：πaT_eq/2 = 剖面面积。"""
    area, a = 1099.0, 29.0
    T_eq = ref.equivalent_ellipse_draft(area, a)
    assert math.pi * a * T_eq / 2.0 == pytest.approx(area, rel=1e-12)


def test_evidence_artifact_present_and_consistent(evidence):
    """证据工件与测试同源：三船、剖面族、RAO 极限、色散齐全。"""
    assert set(evidence["ships"]) == set(SHIPS)
    for name, s in evidence["ships"].items():
        assert s["A33_kernel_kg"] > 0 and s["A33_ref_kg"] > 0
        assert s["err_total"] == pytest.approx(
            (s["A33_kernel_kg"] - s["A33_ref_kg"]) / s["A33_ref_kg"], rel=1e-9)
        assert len(s["sections"]) >= 20
    assert len(evidence["section_family"]) >= 6
    assert evidence["dispersion"]["err"] < 1e-12


# --------------------------------------------------- L2 解析可验证项（必须通过）
@pytest.mark.parametrize("name", ["KVLCC2", "KCS"])
def test_rao_long_wave_analytic_limits(kernel, name):
    """kL→0 零速：垂荡 RAO→1（跟随波面）、纵摇 RAO→1（跟随波面倾角）。"""
    ship = SHIPS[name]
    lim = ref.rao_long_wave_limits()
    for w in (3e-4, 1e-3, 3e-3):
        assert kernel.deep_water_k(w) * ship["L"] < 1e-2
        r = kernel.solve_rao(ship, w, 180.0, 0.0)
        assert r["heave_rao"] == pytest.approx(lim["heave_rao"], abs=0.01), \
            f"{name} ω={w:g}: 垂荡长波极限 {r['heave_rao']:.4f} ≠ 1"
        assert r["pitch_rao"] == pytest.approx(lim["pitch_rao"], abs=0.01), \
            f"{name} ω={w:g}: 纵摇长波极限 {r['pitch_rao']:.4f} ≠ 1"


def test_deep_water_dispersion_is_exact(kernel):
    """深水色散 k = ω²/g 必须精确（内核不得用浅水/工程近似）。"""
    for w in (0.1, 0.5, 1.5, 3.0):
        assert kernel.deep_water_k(w) == pytest.approx(
            ref.deep_water_dispersion(w), rel=1e-12)


def test_encounter_frequency_zero_speed_degenerates(kernel):
    """零航速时遭遇频率 = 波浪频率（任意航向）。"""
    for beta in (0.0, 90.0, 180.0):
        assert kernel.encounter_frequency(0.7, 0.0, beta) == pytest.approx(0.7, rel=1e-12)


def test_damping_vanishes_at_low_frequency(kernel):
    """辐射阻尼 ν→0 趋于零（无辐射波），高频趋于零；中间存在峰值。"""
    assert kernel._fb(1e-4) < 1e-3
    assert kernel._fb(1e3) < 1e-2
    assert kernel._fb(1.0) > 0.5           # ν≈1 附近峰值
    assert kernel._fb(1.0) > kernel._fb(0.1)


def test_geometry_matches_prismatic_hydrostatics(kernel):
    """站线几何自洽（支持性，非独立参考）：Σ A dx = ∇ = Cb·L·B·T、Σ 2b dx = Aw。"""
    for name, ship in SHIPS.items():
        geo = kernel.sectional_geometry(ship["L"], ship["B"], ship["T"],
                                        ship["Cb"], ship.get("Cwp"))
        assert geo["nabla"] == pytest.approx(
            ship["Cb"] * ship["L"] * ship["B"] * ship["T"], rel=2e-3), name
        assert geo["Aw"] == pytest.approx(
            geo["Cwp"] * ship["L"] * ship["B"], rel=2e-3), name


# ------------------------------------------------------------- L3 缺陷锁
def test_kernel_section_law_is_shape_blind(kernel):
    """形状盲指纹：f_a(ν) 与剖面 a/T 无关 ⇒ A33/(ρ·面积) 恒为 1。

    若此测试失败，说明内核已改为形状相关（Lewis/Ursel）真解 ——
    必须重跑 probe.py、更新 evidence.json 与 ERR_BAND，并复核 docs 结论。
    """
    T = 10.0
    ratios = []
    for ratio in (0.5, 1.0, 2.0, 4.0):
        a = ratio * T
        b = np.array([a])
        area = np.array([math.pi * a * T / 2.0])
        co = kernel.section_coefficients(1e-6, b, area, T, RHO)
        ratios.append(co["a33"][0] / (RHO * area[0]))
    assert max(abs(r - 1.0) for r in ratios) < 1e-6, \
        f"内核 f_a 已形状相关（比值 {ratios}）—— 请复核 evidence.json"
    # 同时钉死高频支路：f_a(ν→∞) = 0.75，也与形状无关
    assert kernel._fa(1e6) == pytest.approx(0.75, abs=1e-6)


@pytest.mark.parametrize("name", sorted(SHIPS))
def test_zero_freq_added_mass_error_in_documented_band(kernel, name):
    """零频附加质量对解析参考的相对偏差必须落在 evidence 记录的带内。

    解析参考：A33(0) = ρπa²/2（半宽为 a 的半椭圆，自由面镜像法 + 椭圆闭式解），
    与剖面面积无关；内核给出 ρ·A（形状盲），故偏差 = A/(πa²/2) - 1。
    """
    ship = SHIPS[name]
    geo = kernel.sectional_geometry(ship["L"], ship["B"], ship["T"],
                                    ship["Cb"], ship.get("Cwp"))
    b, area, dx = geo["b"], geo["area"], geo["dx"]
    co = kernel.section_coefficients(1e-6, b, area, ship["T"], RHO)
    a_kernel = float(np.sum(co["a33"] * dx))
    a_ref = float(np.sum(RHO * math.pi * b ** 2 / 2.0 * dx))
    err = (a_kernel - a_ref) / a_ref
    lo, hi = ERR_BAND[name]
    assert lo <= err <= hi, f"{name} 偏差 {err*100:+.2f}% 越出记录带 [{lo*100:.0f}%, {hi*100:.0f}%]"


def test_known_gap_zero_freq_added_mass_within_3pct():
    """（已知缺口，xfail）零频附加质量对解析参考误差 ≤3%。

    当前实测：KVLCC2 -11.0%、KCS -25.1%、BARGE60 -47.9% —— 不满足平台 15% 精度闸门。
    修复（Lewis close-fit / Ursel 级数或自由面 Green 函数面元）合入后本测试应转 XPASS，
    随后按 xfail(strict=False) → 提升为硬断言。
    """
    pytest.xfail("m1797 已定位：内核 f_a 形状盲（A33=ρ·面积），实测偏差 -11%~-48%")


def test_natural_period_shift_is_bounded(kernel, evidence):
    """A33 偏差经 T33=2π√((m+A33)/(ρgAw)) 传播：排水量越小偏移越大（工程影响量）。"""
    shifts = {n: evidence["ships"][n]["period_shift"]["dT33_rel"] for n in SHIPS}
    assert shifts["KVLCC2"] < 0 and shifts["BARGE60"] < shifts["KVLCC2"]
    assert abs(shifts["KVLCC2"]) < 0.05
    assert abs(shifts["BARGE60"]) > 0.10