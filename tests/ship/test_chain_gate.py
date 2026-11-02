"""跨端点闭环闸门（阻力↔推进）回归测试。

设计原则（PB-009 v2 纪律）：门禁必须**双向**有效——
* 合法闭环必须放行（否则是恒假门，只会制造噪声）；
* 任一端漂移必须拒绝（否则是哑门）。
"""

import math

import pytest

from tools.chain_gate.chain_gate import (
    ETA_D_BAND,
    KNOT_MS,
    T_BAND,
    check_cross_pe,
    check_cw_invariance,
    check_diameter_band,
    check_j_band,
    check_kt_plausibility,
    check_pe_identity,
    check_power_closure,
    check_thrust_closure,
    cw_of,
    evaluate_case,
    power_required_band,
    thrust_required_band,
)

# 一个「合法闭环」基准工况：KCS 类船，15 kn
V_MS = 15.0 * KNOT_MS
RT = 516.0            # kN，与推力自洽的阻力
T_FRAC = 0.20
T_THRUST = RT / (1 - T_FRAC)
PE = RT * V_MS                 # kW（I1 定义）
ETA_D = 0.68
P_D = PE / ETA_D
N_RPM = 105.0
D_M = 6.0


def _consistent_row():
    return ({"speed": 15.0, "total_resistance": RT, "friction_resistance": 300.0,
             "wave_resistance": RT - 300.0, "effective_power": PE},
            {"thrust": T_THRUST, "power": P_D, "propeller_rpm": N_RPM,
             "propeller_diameter": D_M})


# ------------------------------------------------------------------ 正向放行
def test_consistent_closure_passes_every_criterion():
    row, prop = _consistent_row()
    out = evaluate_case({"name": "synthetic", "speed_kn": 15.0, "T_m": 10.8}, row, prop)
    assert out["n_fail"] == 0, [v for v in out["verdicts"] if v["state"] == "FAIL"]
    assert out["n_withheld"] == 0
    assert all(v["state"] == "PASS" for v in out["verdicts"])


def test_pe_identity_exact():
    v = check_pe_identity(RT, V_MS, RT * V_MS)
    assert v.state == "PASS" and abs(v.rel_dev) < 1e-12


def test_thrust_band_brackets_exact_solution():
    lo, hi = thrust_required_band(RT)
    for t in T_BAND:
        assert lo <= RT / (1 - t) <= hi


def test_cw_invariance_passes_for_perfect_scaling():
    assert check_cw_invariance(1.0, 1.004).state == "PASS"


def test_j_band_passes_for_normal_operating_point():
    assert check_j_band(T_THRUST, V_MS, N_RPM, D_M).state == "PASS"


# ------------------------------------------------------------------ 负向拒绝
def test_thrust_drop_by_half_is_rejected():
    """推进端推力虚低 50%（典型「两端点未接线」指纹）必须被 I2 抓出。"""
    v = check_thrust_closure(RT, T_THRUST * 0.5)
    assert v.state == "FAIL" and v.rel_dev < -0.1


def test_thrust_overdrive_is_rejected():
    v = check_thrust_closure(RT, T_THRUST * 3.0)
    assert v.state == "FAIL" and v.rel_dev > 0.1


def test_power_deficit_is_rejected():
    row, prop = _consistent_row()
    prop = dict(prop, power=P_D * 0.55)
    out = evaluate_case({"name": "s", "speed_kn": 15.0, "T_m": 10.8}, row, prop)
    keys = {v["key"]: v["state"] for v in out["verdicts"]}
    assert keys["I3_power_closure"] == "FAIL"
    assert keys["I4_cross_PE"] == "PASS"        # 推力仍自洽 -> 只单条失败，判据正交


def test_pe_identity_detects_resistance_drift():
    v = check_pe_identity(RT, V_MS, RT * V_MS * 1.02)
    assert v.state == "FAIL" and abs(v.rel_dev - 0.02) < 1e-9


def test_cross_pe_ratio_detects_uncoupled_endpoints():
    v = check_cross_pe(PE, T_THRUST * 0.5, V_MS)
    assert v.state == "FAIL"
    assert v.interval[1] < 1.0                  # 比值区间整体低于 1


def test_kt_implied_rejects_absurd_loading():
    assert check_kt_plausibility(T_THRUST * 6.0, N_RPM, D_M).state == "FAIL"
    assert check_kt_plausibility(T_THRUST * 0.02, N_RPM, D_M).state == "FAIL"


def test_diameter_band_rejects_pencil_propeller():
    assert check_diameter_band(1.2, 20.8).state == "FAIL"
    assert check_diameter_band(12.0, 6.0).state == "FAIL"


def test_cw_invariance_rejects_scale_dependent_wave_resistance():
    assert check_cw_invariance(1.0, 1.30).state == "FAIL"


def test_withheld_when_fields_missing():
    v = check_thrust_closure(RT, None)
    assert v.state == "WITHHELD"
    v = check_kt_plausibility(T_THRUST, None, D_M)
    assert v.state == "WITHHELD"


# ------------------------------------------------------------------ 工具函数
def test_required_bands_are_monotone():
    lo, hi = power_required_band(PE)
    assert PE < lo < hi                        # eta_D<1 -> 交付功率必大于有效功率
    assert math.isclose(lo, PE / ETA_D_BAND[1])
    assert math.isclose(hi, PE / ETA_D_BAND[0])


def test_cw_of_requires_wetted_surface():
    assert cw_of(RT, 300.0, V_MS, 1025.0, None) is None
    cw = cw_of(RT, 300.0, V_MS, 1025.0, 9500.0)
    assert cw == pytest.approx((RT - 300.0) * 1e3 / (0.5 * 1025.0 * V_MS ** 2 * 9500.0))