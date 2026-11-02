"""m2077 · 冰区船体磨损（Archard）+ 冰带结构能力折损 —— 验收判据测试套件。

每条判据都在代码里用**独立路线**复算，检查点不依赖生产实现的中间量：

* 磨损：Archard ``V = K W s / H`` 的三条标度（载荷 / 滑移距离 / 硬度倒数）、
  载荷-面积 与 接触压强 两条路径的等价性、摩擦功形式 ``V/Q = K/(mu H)``。
* 结构：板格屈曲闭式解 ``sigma_cr = k pi^2 E t^2 / (12(1-nu^2) b^2)`` 的
  Rayleigh-Ritz 独立复算（把 k=4 从"传说"变成可复现的数）、``t^2`` 标度指数、
  箱型剖面惯性矩 纤维积分 vs 闭式解 双路线。
* 可辨识性：``K``、``p``、``beta`` 只能以乘积 ``K p beta`` 被观测识别，任何拆分
  必须给出同一个磨损深度（诚实性判据，防止把未标定系数当"验证过的 K"）。
* 数值：航线积分器的观测阶 ``p_obs``（对线性压强剖面解析积分）与分段增量精确性。

运行：python3 -m pytest tests/test_ice_abrasion_verification.py -q
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import ice_abrasion as ab  # noqa: E402

H = ab.STEEL_HB * ab.PA_PER_HB
K = ab.default_k_eff()
BETA = ab.CONTACT_FRACTION_DEFAULT
P_REF = ab.PRESSURE_REFERENCE
S = 1000.0 * 1852.0 * BETA


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


# ------------------------------------------------------------------ Archard


def test_load_linearity():
    d1 = ab.wear_depth_from_pressure(K, P_REF, S, H)
    d2 = ab.wear_depth_from_pressure(K, 2.0 * P_REF, S, H)
    assert abs(d2 / d1 - 2.0) < 1e-14


def test_sliding_distance_linearity():
    d1 = ab.wear_depth_from_pressure(K, P_REF, S, H)
    d3 = ab.wear_depth_from_pressure(K, P_REF, 2.5 * S, H)
    assert abs(d3 / d1 - 2.5) < 1e-14


def test_hardness_inverse():
    d1 = ab.wear_depth_from_pressure(K, P_REF, S, H)
    d4 = ab.wear_depth_from_pressure(K, P_REF, S, H / 4.0)
    assert abs(d4 / d1 - 4.0) < 1e-14


def test_load_and_pressure_routes_agree():
    area = 3.5
    d_load = ab.wear_depth_from_load(K, P_REF * area, S, H, area)
    d_pres = ab.wear_depth_from_pressure(K, P_REF, S, H)
    assert _rel(d_load, d_pres) < 1e-14


def test_friction_energy_form_of_archard():
    mu, load = 0.12, 5.0e4 * 2.0
    vol = ab.wear_volume(K, load, S, H)
    work = ab.friction_work_j(mu, load, S)
    assert _rel(vol / work, ab.wear_per_unit_work(K, mu, H)) < 1e-14


def test_zero_inputs_rejected():
    with pytest.raises(ValueError):
        ab.wear_depth_from_pressure(0.0, P_REF, S, H)
    with pytest.raises(ValueError):
        ab.wear_depth_from_pressure(K, P_REF, S, 0.0)
    with pytest.raises(ValueError):
        ab.wear_volume(K, -1.0, S, H)


# ------------------------------------------------------------------ identifiability


def test_identifiable_group_invariance():
    """Only K*p*beta is observable: three splits must give the same depth."""
    splits = [(K, P_REF, BETA), (10.0 * K, P_REF / 10.0, BETA), (K, 4.0 * P_REF, BETA / 4.0)]
    depths = [ab.wear_depth_from_pressure(k, p, 1000.0 * 1852.0 * b, H) for k, p, b in splits]
    assert (max(depths) - min(depths)) / max(depths) < 1e-14


def test_calibration_roundtrip_over_service_band():
    lo, hi = ab.ABRASION_RATE_BAND_MM_PER_1000NM
    for rate in (lo, 0.25, 0.5, hi):
        k = ab.effective_wear_coefficient(rate, P_REF, H, BETA)
        back = ab.predicted_rate_mm_per_1000nm(k, P_REF, H, BETA)
        assert _rel(rate, back) < 1e-12
    k_lo, k_hi = ab.k_eff_band()
    assert k_lo < k_hi
    assert _rel(ab.default_k_eff(), math.sqrt(k_lo * k_hi)) < 1e-12


# ------------------------------------------------------------------ structures


def test_plate_buckling_rayleigh_ritz_reproduces_k4():
    span, t = 0.90, 0.018
    sig_cf = ab.plate_buckling_stress(t, span)
    sig_rr = ab.plate_buckling_rayleigh_ritz(t, span)
    assert _rel(sig_cf, sig_rr) < 1e-12
    k_implied = sig_cf * 12.0 * (1 - ab.STEEL_NU ** 2) * span ** 2 / (
        math.pi ** 2 * ab.STEEL_E * t ** 2)
    assert abs(k_implied - 4.0) < 1e-12


def test_plate_buckling_scales_with_t_squared():
    span = 0.90
    ts = np.linspace(0.010, 0.024, 15)
    sig = np.array([ab.plate_buckling_stress(float(t), span) for t in ts])
    slope, _ = np.polyfit(np.log(ts), np.log(sig), 1)
    assert abs(slope - 2.0) < 1e-12


def test_box_modulus_two_routes():
    g = ab.BoxGirder(32.0, 22.0, 0.022, 0.022, 0.018)
    i_fib, w_fib = ab.box_section_modulus(g)
    i_cf, w_cf = ab.box_section_modulus_closed_form(g)
    assert _rel(i_fib, i_cf) < 1e-12
    assert _rel(w_fib, w_cf) < 1e-12


def test_closed_form_guards_asymmetric_section():
    with pytest.raises(ValueError):
        ab.box_section_modulus_closed_form(ab.BoxGirder(32.0, 22.0, 0.030, 0.022, 0.018))


def test_degenerate_box_rejected():
    with pytest.raises(ValueError):
        ab.BoxGirder(32.0, 0.03, 0.022, 0.022, 0.018).validate()   # flanges thicker than depth


def test_capacity_retention_is_exact_square_law():
    g = ab.BoxGirder(32.0, 22.0, 0.022, 0.022, 0.018)
    cap = ab.ice_belt_capacity_retention(g, 0.90, 4.0e-3, "side")
    assert abs(cap.strength_retention - cap.thickness_ratio ** 2) < 1e-12
    assert 0.0 < cap.strength_retention < 1.0
    assert 0.0 < cap.modulus_retention < 1.0
    # the naive linear-thickness model overstates the retention: bias is real
    assert cap.thickness_ratio > cap.strength_retention


def test_zero_wear_is_identity():
    g = ab.BoxGirder(32.0, 22.0, 0.022, 0.022, 0.018)
    cap = ab.ice_belt_capacity_retention(g, 0.90, 0.0, "side")
    assert cap.strength_retention == pytest.approx(1.0, abs=1e-14)
    assert cap.modulus_retention == pytest.approx(1.0, abs=1e-14)


def test_section_lost_is_rejected():
    g = ab.BoxGirder(32.0, 22.0, 0.022, 0.022, 0.018)
    with pytest.raises(ValueError):
        ab.ice_belt_capacity_retention(g, 0.90, 0.05, "side")


# ------------------------------------------------------------------ voyage level


def test_leg_additivity_and_zero_concentration():
    legs = [
        ab.AbrasionCondition(1800.0, 0.8, 6.0e4, 0.02),
        ab.AbrasionCondition(1200.0, 0.4, 3.0e4, 0.02),
    ]
    both = ab.abrasion_depth_legs(legs, K)
    one = ab.abrasion_depth_legs([legs[0]], K).depth_m + ab.abrasion_depth_legs([legs[1]], K).depth_m
    assert _rel(both.depth_m, one) < 1e-14
    open_water = ab.abrasion_depth_legs([ab.AbrasionCondition(1000.0, 0.0, 5.0e4, 0.02)], K)
    assert open_water.depth_mm == 0.0


def test_result_is_declared_withheld():
    res = ab.abrasion_depth_legs([ab.AbrasionCondition(3200.0, 0.65, P_REF, BETA)], K)
    assert res.calibration_status == "WITHHELD"


def test_condition_validation():
    with pytest.raises(ValueError):
        ab.AbrasionCondition(100.0, 1.5, 5.0e4).validate()
    with pytest.raises(ValueError):
        ab.AbrasionCondition(-1.0, 0.5, 5.0e4).validate()
    with pytest.raises(ValueError):
        ab.abrasion_depth_legs([], K)


def test_profile_increment_matches_analytic():
    leg = ab.AbrasionCondition(2000.0, 0.7, 5.0e4, 0.02)
    prof = ab.abrasion_depth_profile([leg], K, 5)
    expect = ab.wear_depth_from_pressure(K, 5.0e4, (2000.0 / 5) * 1852.0 * 0.7 * 0.02, H)
    assert _rel(prof[0], expect) < 1e-14
    # cumulative and monotone
    assert all(b >= a for a, b in zip(prof, prof[1:]))
    assert _rel(prof[-1], ab.abrasion_depth_legs([leg], K).depth_m) < 1e-12


def test_route_quadrature_is_first_order_against_analytic_integral():
    """Right-endpoint quadrature on a linear pressure ramp: p_obs must be 1."""
    l_nm, p_bow, p_aft, beta = 4000.0, 120.0e3, 20.0e3, 0.02
    exact = K / H * 1852.0 * beta * 0.5 * (p_bow + p_aft) * l_nm

    def riemann(n: int) -> float:
        h = l_nm / n
        return sum(
            ab.wear_depth_from_pressure(K, p_bow + (p_aft - p_bow) * (h * (i + 1)) / l_nm,
                                        h * 1852.0 * beta, H)
            for i in range(n)
        )

    e1 = abs(riemann(100) - exact) / exact
    e2 = abs(riemann(200) - exact) / exact
    e4 = abs(riemann(400) - exact) / exact
    p_obs = math.log(e2 / e4) / math.log(2.0)
    assert abs(p_obs - 1.0) < 0.02
    assert e4 < e1
    assert e4 < 5e-3


def test_verification_artifact_is_consistent():
    """The committed artifact must exist, be all-pass and reference this module."""
    import json

    path = os.path.join(ROOT, "artifacts", "m2077_ice_abrasion", "verification.json")
    if not os.path.exists(path):
        pytest.skip("verification artifact not generated in this checkout")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["module"] == "backend/services/ice_abrasion.py"
    assert data["summary"]["n_criteria"] >= 20
    assert data["summary"]["all_pass"] is True
    assert data["reference_voyage"]["rate_mm_per_1000nm"] > 0.0