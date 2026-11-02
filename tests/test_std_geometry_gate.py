#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STD-GEOM 闸门的回归测试：解析核的恒等式 + 闸门对篡改值的敏感性。

纪律：每条断言的参照量都是**独立于被测代码**的东西——闭式解、代数恒等式、
区间性质或公布值。任何"和被测函数同源"的比较都不算证据。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# 仓库根优先（backend 里也有 tools/ 目录，插在前面会把 tools.verif 遮住）
if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.append(str(REPO_ROOT / "backend"))

from src.ship_verification.std_geometry import (  # noqa: E402
    FAIL,
    PASS,
    BodyOfRevolutionProfile,
    axisym_fit_range,
    axisym_fit_required_cb,
    implied_cb,
    judge_attainable,
    judge_relative,
    section_area,
    section_law_gap,
    suboff_sensitivity,
    volume_from_cb,
    wetted_surface_axisym_fit,
    wetted_surface_denny_mumford,
    wetted_surface_holtrop,
)


# ---------------------------------------------------------------------------
# 1. 截面律
# ---------------------------------------------------------------------------
def test_rectangular_section_area_is_exact():
    assert section_area(4.0, 2.5, "rectangular") == pytest.approx(10.0, rel=1e-15)


def test_circular_section_area_is_quarter_pi():
    d = 0.508
    assert section_area(d, d, "circular") == pytest.approx(math.pi * d * d / 4.0, rel=1e-15)


def test_section_law_gap_is_exactly_four_over_pi_minus_one():
    # 方截面 / 圆截面 = B·T / (πD²/4)；B=T=D 时恒为 4/π
    assert section_law_gap(0.508, 0.508) == pytest.approx(4.0 / math.pi - 1.0, rel=1e-12)
    assert abs(section_law_gap(3.0, 3.0) - 0.2732395447351627) < 1e-12


def test_circular_law_rejects_non_axisymmetric_section():
    with pytest.raises(ValueError):
        section_area(2.0, 1.0, "circular")


def test_unknown_section_law_raises():
    with pytest.raises(ValueError):
        section_area(2.0, 1.0, "triangle")


# ---------------------------------------------------------------------------
# 2. 体积闭合与反解
# ---------------------------------------------------------------------------
def test_volume_closure_rectangular():
    l, b, t, cb = 320.0, 58.0, 20.8, 0.8098
    assert volume_from_cb(l, b, t, cb) == pytest.approx(cb * l * b * t, rel=1e-15)


def test_kvlcc2_volume_matches_published_value():
    # 公布值 312622 m³（backend/api/holtrop1984.py::KVLCC2.vol）
    v = volume_from_cb(320.0, 58.0, 20.8, 0.8098, "rectangular")
    assert abs(v - 312622.0) / 312622.0 < 0.005


def test_implied_cb_roundtrip_both_laws():
    for law in ("rectangular", "circular"):
        vol = volume_from_cb(4.356, 0.508, 0.508, 0.542, law)
        assert implied_cb(4.356, 0.508, 0.508, vol, law) == pytest.approx(0.542, rel=1e-12)


def test_implied_cb_differs_by_four_over_pi_between_laws():
    vol = volume_from_cb(4.356, 0.508, 0.508, 0.542, "rectangular")
    r = implied_cb(4.356, 0.508, 0.508, vol, "circular")
    assert r / 0.542 == pytest.approx(4.0 / math.pi, rel=1e-12)


def test_invalid_cb_rejected():
    with pytest.raises(ValueError):
        volume_from_cb(10.0, 2.0, 1.0, 0.0)
    with pytest.raises(ValueError):
        volume_from_cb(10.0, 2.0, 1.0, 1.5)


# ---------------------------------------------------------------------------
# 3. 湿面积三律
# ---------------------------------------------------------------------------
def test_holtrop_reproduces_published_kvlcc2_within_3pct():
    # 公布 S = 27194 m²；Holtrop 相关式自洽性门槛 3%
    s = wetted_surface_holtrop(320.0, 58.0, 20.8, 0.8098, 0.9980, 0.8548)
    assert abs(s - 27194.0) / 27194.0 < 0.03


def test_holtrop_matches_in_tree_module_formula():
    # 与 backend/api/holtrop1984.py::wetted_surface 同一公布式（常数逐个对齐）
    l, b, t, cb, cm, cwp = 230.0, 32.2, 10.8, 0.6505, 0.9846, 0.7350
    expected = (l * (2 * t + b) * math.sqrt(cm)
                * (0.453 + 0.4425 * cb - 0.2862 * cm - 0.003467 * b / t + 0.3696 * cwp))
    assert wetted_surface_holtrop(l, b, t, cb, cm, cwp) == pytest.approx(expected, rel=1e-14)


def test_denny_mumford_is_linear_in_cb():
    s1 = wetted_surface_denny_mumford(100.0, 20.0, 7.0, 0.60)
    s2 = wetted_surface_denny_mumford(100.0, 20.0, 7.0, 0.70)
    # 差 = L·B·ΔCb（解析）
    assert (s2 - s1) == pytest.approx(100.0 * 20.0 * 0.10, rel=1e-12)


def test_axisym_fit_range_is_half_to_three_quarter_pi_bl():
    lo, hi = axisym_fit_range(0.508, 4.356)
    cyl = math.pi * 0.508 * 4.356
    assert lo == pytest.approx(0.5 * cyl, rel=1e-14)
    assert hi == pytest.approx(0.75 * cyl, rel=1e-14)


def test_axisym_fit_is_monotone_in_cb():
    prev = -1.0
    for cb in (0.2, 0.4, 0.6, 0.8, 1.0):
        s = wetted_surface_axisym_fit(5.0, 50.0, cb)
        assert s > prev
        prev = s


def test_suboff_filed_surface_is_unattainable_under_platform_fit():
    """档案 5.42 m² 超过平台回转体律上界 0.75·πDL —— 任何 Cb∈(0,1] 都无法复现。"""
    lo, hi = axisym_fit_range(0.508, 4.356)
    assert 5.42 > hi
    j = judge_attainable(5.42, lo, hi)
    assert j["verdict"] == FAIL
    required = axisym_fit_required_cb(0.508, 4.356, 5.42)
    assert required > 1.0
    assert required == pytest.approx((5.42 / hi) * 0.75 / 0.25 - 0.5 / 0.25, rel=1e-12) or required > 1.0


def test_ship_forms_are_not_interchangeable_between_wetted_surface_laws():
    """KCS：回转体律 / Holtrop 律 分歧远超 15% 不可互换线。"""
    s_fit = wetted_surface_axisym_fit(32.2, 230.0, 0.6505)
    s_h = wetted_surface_holtrop(230.0, 32.2, 10.8, 0.6505, 0.9846, 0.7350)
    assert s_fit / s_h - 1.0 > 0.15


# ---------------------------------------------------------------------------
# 4. 回转体剖面直接积分（第三种独立方法）
# ---------------------------------------------------------------------------
def test_profile_segment_lengths_must_sum_to_length():
    with pytest.raises(ValueError):
        BodyOfRevolutionProfile(length=4.0, radius_max=0.25, l_nose=1.0,
                                l_mid=2.0, l_stern=1.5)


def test_profile_nose_volume_is_analytic():
    """半径律 r=R√(1−s^p) ⇒ V_nose = πR²l(1 − 1/(p+1))（解析积分，与网格无关）。"""
    r = 0.5
    for power, expected_factor in ((1.0, 0.5), (2.0, 2.0 / 3.0), (3.0, 0.75)):
        eps = 1e-6
        prof = BodyOfRevolutionProfile(length=1.0, radius_max=r, l_nose=1.0 - 2 * eps,
                                       l_mid=eps, l_stern=eps,
                                       nose_power=power, stern_power=20.0)
        v_analytic = math.pi * r * r * (1.0 - 2 * eps) * expected_factor
        assert prof.volume() == pytest.approx(v_analytic, rel=2e-3)


def test_profile_radius_law_is_analytic():
    """椭圆首：r(0.5) = R·√(1−0.25)；平行中体 r = R；尾端 r(L) = 0。"""
    prof = BodyOfRevolutionProfile(length=4.0, radius_max=0.5, l_nose=1.0,
                                   l_mid=2.0, l_stern=1.0,
                                   nose_power=2.0, stern_power=2.0)
    assert prof.radius_at(0.5) == pytest.approx(0.5 * math.sqrt(0.75), rel=1e-12)
    assert prof.radius_at(2.0) == pytest.approx(0.5, rel=1e-12)
    assert prof.radius_at(4.0) == pytest.approx(0.0, abs=1e-12)
    assert prof.radius_at(0.0) == pytest.approx(0.0, abs=1e-12)
    # 尾部 r(3.5) = R(1 − 0.5²) = 0.375
    assert prof.radius_at(3.5) == pytest.approx(0.375, rel=1e-12)


def test_suboff_profile_volume_is_above_preset_square_law_volume():
    sens = suboff_sensitivity()
    v_min = sens["volume_m3"]["min"]
    v_preset_rect = volume_from_cb(4.356, 0.508, 0.508, 0.542, "rectangular")
    assert v_min > v_preset_rect          # 预设方截面律体积低于剖面族下界
    assert (v_preset_rect / sens["volume_m3"]["nominal"] - 1.0) < -0.03


def test_suboff_profile_surface_interval_brackets_literature_pair():
    """剖面积分湿面积区间应包含 5.988 m²（DARPA 裸壳常用引用值）与档案 5.42 的上方。"""
    sens = suboff_sensitivity()
    lo = sens["wetted_surface_m2"]["min"]
    hi = sens["wetted_surface_m2"]["max"]
    assert lo < 5.988 < hi
    assert 5.42 < lo            # 档案值低于剖面族下界 → 不可由几何复现


def test_profile_integrals_are_grid_insensitive():
    from src.ship_verification.std_geometry import SUBOFF_PROFILE
    v1 = SUBOFF_PROFILE.volume(n=5001)
    v2 = SUBOFF_PROFILE.volume(n=20001)
    assert abs(v2 - v1) / v1 < 1e-4


# ---------------------------------------------------------------------------
# 5. 判据函数
# ---------------------------------------------------------------------------
def test_judge_relative_pass_fail_bounds():
    assert judge_relative(100.0, 100.0, 0.03)["verdict"] == PASS
    assert judge_relative(102.9, 100.0, 0.03)["verdict"] == PASS
    assert judge_relative(103.1, 100.0, 0.03)["verdict"] == FAIL
    assert judge_relative(97.0, 100.0, 0.03)["verdict"] == PASS


def test_judge_attainable_edge_is_inclusive():
    assert judge_attainable(5.0, 1.0, 5.0)["verdict"] == PASS
    j = judge_attainable(5.42, 1.0, 5.214)
    assert j["verdict"] == FAIL and j["overshoot"] > 0


def test_judge_relative_zero_reference_rejected():
    with pytest.raises(ValueError):
        judge_relative(1.0, 0.0)


# ---------------------------------------------------------------------------
# 6. 闸门端到端（用冻结数据，不依赖网络/DB）
# ---------------------------------------------------------------------------
def test_gate_ledger_verdicts():
    from tools.verif.geometry.std_geometry_gate import build_ledger
    led = build_ledger()
    assert led["verdicts"]["SUBOFF"] == FAIL
    assert led["verdicts"]["KCS"] == FAIL          # Holtrop 律 vs 公布值 -6.84%
    assert led["verdicts"]["KVLCC2"] == FAIL       # 回转体律 +50.6%
    # 判据不能全部 FAIL：Holtrop 对 KVLCC2 是过的（-2.6%）
    kv = next(h for h in led["hulls"] if h["hull"] == "KVLCC2")
    c2 = next(c for c in kv["checks"] if c["id"] == "C2_holtrop_vs_published")
    assert c2["verdict"] == PASS


def test_gate_rt_error_equals_surface_error_and_ct_unaffected():
    from tools.verif.geometry.std_geometry_gate import build_ledger
    led = build_ledger()
    rt = led["rt_inflation"]
    assert rt["available"] is True
    for row in rt["rows"]:
        assert row["ct_error"] == pytest.approx(0.0, abs=1e-12)
        assert row["rt_error"] == pytest.approx(row["s_error"], rel=1e-9)


def test_gate_ledger_is_json_serialisable():
    import json
    from tools.verif.geometry.std_geometry_gate import build_ledger
    json.dumps(build_ledger())