"""m1809：耐波参考核 + 端点判据的失效/解析测试（pytest）。

解析锚（与文献/闭式解逐项对账，全部可在本机复算）：
  * 无限流场椭圆剖面附加质量：沿某方向 = ρπ·(垂直该方向的半轴)²；
    圆 a=b ⇒ 任意方向 ρπa²（Landau & Lifshitz《流体力学》§11 经典结果）。
  * 自由液面高频极限 φ=0：半浸半圆剖面升沉 A33 = ρπa²/2
    （圆截面升沉精确势 φ=-V₃a²z/r² 在 z=0 上 φ≡0 且关于 z 奇 ⇒ 双体取半）。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from ship_perf import seakeeping as sk  # noqa: E402
from ship_perf.constants import RHO_SEA  # noqa: E402
from ship_verification import audit_seakeeping as audit  # noqa: E402

TOL = 0.01  # 面元离散 1% 内


# ------------------------------------------------------------------ 面元核


def test_submerged_circle_added_mass_matches_analytic():
    r = sk.panel_added_mass(sk.ellipse_polygon(1.0, 1.0, 384), RHO_SEA)
    exact = RHO_SEA * math.pi
    assert abs(r["heave"] / exact - 1.0) < TOL
    assert abs(r["sway"] / exact - 1.0) < TOL


def test_submerged_ellipse_two_axes_follow_perpendicular_semi_axis():
    r = sk.panel_added_mass(sk.ellipse_polygon(2.0, 1.0, 384), RHO_SEA)
    assert abs(r["heave"] / (RHO_SEA * math.pi * 4.0) - 1.0) < TOL   # 升沉 ⇒ ρπa²
    assert abs(r["sway"] / (RHO_SEA * math.pi * 1.0) - 1.0) < TOL    # 横荡 ⇒ ρπb²


def test_free_surface_semicircle_is_half_circle_result():
    a33 = sk.section_heave_added_mass(sk.semicircle_polygon(1.0, 320), RHO_SEA, True)
    exact = RHO_SEA * math.pi / 2.0
    assert abs(a33 / exact - 1.0) < TOL


def test_symmetric_section_has_no_sway_heave_coupling():
    r = sk.panel_added_mass(sk.ellipse_polygon(1.5, 1.0, 192), RHO_SEA)
    assert abs(r["sway_heave"]) < 1e-6 * abs(r["heave"])


def test_doubled_body_is_closed_and_symmetric_about_waterline():
    poly = sk.refine_polygon(sk.immersed_polygon(15.0, 5.0, 1.0), 8)
    dbl = sk.doubled_body(poly)
    z_w = max(p[1] for p in poly)
    ys = sorted(p[0] for p in dbl)
    assert abs(ys[0] + ys[-1]) < 1e-9                     # 左右对称
    assert abs(max(p[1] for p in dbl) - (2 * z_w - min(p[1] for p in dbl))) < 1e-9


def test_barge_section_added_mass_positive_and_grows_with_draft():
    thin = sk.section_heave_added_mass(sk.immersed_polygon(10.0, 2.0, 1.0), RHO_SEA, True)
    thick = sk.section_heave_added_mass(sk.immersed_polygon(10.0, 6.0, 1.0), RHO_SEA, True)
    assert thin > 0 and thick > thin


# ------------------------------------------------------------------ 动力学恒等式


@pytest.mark.parametrize("period", [150.0, 200.0, 300.0])
def test_heave_rao_tends_to_unity_for_long_waves(period):
    hull = sk.Hull(100.0, 15.0, 5.0, 0.60)
    rows = sk.heave_rao(hull, [period], 4616.0 * 1000.0, damping_ratio=0.15)
    assert abs(rows[0]["rao_heave"] - 1.0) < 0.01


@pytest.mark.parametrize("period", [2.0, 3.0])
def test_heave_rao_vanishes_for_short_waves(period):
    hull = sk.Hull(100.0, 15.0, 5.0, 0.60)
    rows = sk.heave_rao(hull, [period], 4616.0 * 1000.0, damping_ratio=0.15)
    assert rows[0]["rao_heave"] < 0.35


def test_heave_natural_period_positive_and_monotone_in_mass():
    hull = sk.Hull(100.0, 15.0, 5.0, 0.60)
    tn = sk.heave_natural_period(hull, 4616.0 * 1000.0)
    assert 3.0 < tn["period_n"] < 12.0
    heavier = sk.heave_natural_period(hull, 2.0 * 4616.0 * 1000.0)
    assert heavier["period_n"] > tn["period_n"]          # 质量↑ ⇒ 周期↑
    wide = sk.Hull(100.0, 30.0, 5.0, 0.60)
    tw = sk.heave_natural_period(wide, 4616.0 * 1000.0)
    assert abs(tw["stiffness"] / tn["stiffness"] - 2.0) < 1e-9   # C ∝ B
    assert tw["a33"] > tn["a33"]                          # A33 随 B 更快增长


def test_fk_roll_moment_vanishes_in_following_and_head_seas():
    hull = sk.Hull(100.0, 15.0, 5.0, 0.60)
    scale = RHO_SEA * 9.80665 * 1.0 * hull.beam * hull.draft   # N·m/m 量级
    periods = (6.0, 8.0, 10.0, 12.0)
    m90 = max(sk.fk_roll_moment_free_surface(hull, 1.0, tp, 90.0) for tp in periods)
    assert m90 > 1e-3 * scale                     # 横浪确有激励
    for heading in (0.0, 180.0):                  # 随浪/顶浪：解析恒 0
        m = max(sk.fk_roll_moment_free_surface(hull, 1.0, tp, heading) for tp in periods)
        assert m < 1e-9 * scale


def test_wave_length_deep_water():
    assert abs(audit.wave_length(10.0) - 9.80665 * 100.0 / (2 * math.pi)) < 1e-9


# ------------------------------------------------------------------ 审计判据（离线）


def _constant_fetch(value: float):
    """伪表：与输入无关的常数返回（复现被测端点的行为形状）。"""
    return lambda payload: {
        "heave_rao_max": value, "roll_rao_max": 2.0, "pitch_rao_max": 3.5,
        "heave_amplitude": value * payload["wave_height"] / 2.0,
        "roll_amplitude": payload["wave_height"], "pitch_amplitude": 1.75 * payload["wave_height"],
        "added_mass_heave": 3.92e6, "added_mass_roll": 3.0e7, "added_mass_pitch": 3.2e9,
        "vertical_acc": 0.7, "lateral_acc": 0.16, "roll_natural_period": 11.65,
        "method": "切片理论 + 经验公式",
    }


def test_audit_marks_constant_table_as_mock():
    case = audit.CASES[0]
    res = audit.audit_case(case, _constant_fetch(1.3))
    assert res["grade"] == "MOCK"
    verdicts = {c["id"]: c["verdict"] for c in res["criteria"]}
    assert verdicts["H-FREQ"] == "FAIL"        # 周期无关
    assert verdicts["H-HEADING"] == "FAIL"     # 航向无关
    assert verdicts["H-ROLL-SYM"] == "FAIL"    # 顶浪横摇 RAO≠0
    assert verdicts["H-LINEAR"] == "PASS"      # 判据非"全否"
    assert verdicts["G-CB"] == "PASS"          # 几何先行通过


def test_audit_passes_long_wave_when_endpoint_returns_exactly_one():
    case = audit.CASES[0]
    res = audit.audit_case(case, _constant_fetch(1.0))
    verdicts = {c["id"]: c["verdict"] for c in res["criteria"]}
    assert verdicts["H-LONGWAVE"] == "PASS"


def test_audit_reports_geometry_inconsistency():
    case = audit.Case("BAD", 100.0, 15.0, 5.0, 0.60, 8000.0)  # Δ 与 Cb·LBT 不自洽
    res = audit.audit_case(case, _constant_fetch(1.0))
    verdicts = {c["id"]: c["verdict"] for c in res["criteria"]}
    assert verdicts["G-CB"] == "FAIL"


def test_markdown_render_contains_grades():
    rep = audit.run(cases=(audit.CASES[0],), fetch=_constant_fetch(1.3))
    md = audit.to_markdown(rep)
    assert "MOCK" in md and "H-FREQ" in md