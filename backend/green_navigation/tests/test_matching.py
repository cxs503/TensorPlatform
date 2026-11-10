"""船-机-桨匹配测试：力平衡/恒等式残差、单调性、与仓库既有求解器交叉复核。"""
from __future__ import annotations

import math

import pytest

from green_navigation import matching as m


def test_thrust_balance_residual_is_machine_precision(vlcc):
    _, ship, prop, train = vlcc
    for v in (10.0, 13.0, 15.5):
        mp = m.match_at_speed(ship, prop, train, v)
        scale = mp.required_thrust_n
        assert abs(mp.thrust_balance_residual_n) / scale < 1e-8
        assert mp.kt > 0.0 and mp.kq > 0.0


def test_quasi_propulsive_identity_holds(vlcc):
    """η_D = η_O η_R (1-t)/(1-w) 必须成立到机器精度。"""
    _, ship, prop, train = vlcc
    mp = m.match_at_speed(ship, prop, train, 15.5)
    assert abs(mp.quasi_propulsive_identity_residual) < 1e-12
    expect = mp.open_water_efficiency * prop.relative_rotative_efficiency * \
        (1 - prop.thrust_deduction) / (1 - prop.wake_fraction)
    assert mp.quasi_propulsive_efficiency == pytest.approx(expect, rel=1e-12)


def test_power_chain_scaling(vlcc):
    """P_B = P_D/(η_shaft η_gear)，P_E = R_T·V，P_D = 2π n Q/η_R —— 逐项复算。"""
    _, ship, prop, train = vlcc
    mp = m.match_at_speed(ship, prop, train, 15.5)
    assert mp.effective_power_kw == pytest.approx(
        mp.resistance_n * mp.speed_ms / 1e3, rel=1e-12)
    assert mp.delivered_power_kw / train.eta_total == pytest.approx(mp.brake_power_kw, rel=1e-12)
    q = mp.kq * m.RHO_SEA * mp.revolutions_per_s ** 2 * prop.diameter_m ** 5
    p_d = 2 * math.pi * mp.revolutions_per_s * q / prop.relative_rotative_efficiency
    assert mp.delivered_power_kw == pytest.approx(p_d / 1e3, rel=1e-12)


def test_resistance_monotone_in_speed(vlcc):
    _, ship, prop, train = vlcc
    speeds = [8.0, 10.0, 12.0, 14.0, 15.5, 17.0]
    rts = [m.match_at_speed(ship, prop, train, v).resistance_n for v in speeds]
    assert all(b > a for a, b in zip(rts, rts[1:]))
    pbs = [m.match_at_speed(ship, prop, train, v).brake_power_kw for v in speeds]
    assert all(b > a for a, b in zip(pbs, pbs[1:]))


def test_roughness_increases_resistance_and_power(vlcc):
    """粗糙度通道：k_s ↑ ⇒ C_A ↑ ⇒ R_T ↑ ⇒ P_B ↑（E5 轨迹的物理基础）。"""
    _, ship, prop, train = vlcc
    clean = m.match_at_speed(ship, prop, train, 15.5, ks_m=150e-6)
    rough = m.match_at_speed(ship, prop, train, 15.5, ks_m=300e-6)
    assert rough.resistance_n > clean.resistance_n
    assert rough.brake_power_kw > clean.brake_power_kw
    assert rough.detail["holtrop"]["ca"] > clean.detail["holtrop"]["ca"]


def test_speed_for_brake_power_inverts_match(vlcc):
    _, ship, prop, train = vlcc
    target = 0.75 * 25400.0
    v, mp = m.speed_for_brake_power(ship, prop, train, target)
    assert mp.brake_power_kw == pytest.approx(target, rel=1e-6)
    again = m.match_at_speed(ship, prop, train, v)
    assert again.brake_power_kw == pytest.approx(target, rel=1e-6)


def test_cross_check_against_repo_self_propulsion_solver(vlcc):
    """与 ``services.propulsion.self_propulsion_point`` 交叉复核（独立实现）。"""
    from services import propulsion as repo_prop

    _, ship, prop, train = vlcc
    mp = m.match_at_speed(ship, prop, train, 15.5)
    ref = repo_prop.self_propulsion_point(
        mp.resistance_n, mp.speed_ms, prop.diameter_m,
        prop.kt, prop.kq,
        wake_fraction=prop.wake_fraction, thrust_deduction=prop.thrust_deduction,
        n_lo=0.2, n_hi=6.0)
    assert ref.revolutions_per_s == pytest.approx(mp.revolutions_per_s, rel=1e-6)
    assert ref.delivered_power_w / 1e3 == pytest.approx(mp.delivered_power_kw, rel=1e-6)
    assert ref.thrust_balance_residual_n / mp.required_thrust_n < 1e-6


def test_bad_inputs_raise(vlcc):
    _, ship, prop, train = vlcc
    with pytest.raises(m.MatchingError):
        m.match_at_speed(ship, prop, train, 0.0)
    with pytest.raises(m.MatchingError):
        m.PropellerSpec(diameter_m=0.0, pitch_ratio=0.7, ae_ao=0.4, blades=4,
                        wake_fraction=0.3, thrust_deduction=0.2)
    with pytest.raises(m.MatchingError):
        m.PowertrainSpec(eta_shaft=1.2)
    with pytest.raises(m.MatchingError):
        m.speed_for_brake_power(ship, prop, train, 0.0)


def test_froude_invariance_of_open_water_point(vlcc):
    """几何相似缩放（D→D/λ, V→V/√λ, n→n√λ）必须给同一 J/K_T/η_O（Froude 不变性）。"""
    _, ship, prop, train = vlcc
    mp = m.match_at_speed(ship, prop, train, 15.5)
    lam = 4.0
    d_model = prop.diameter_m / lam
    v_model = mp.speed_ms / math.sqrt(lam)
    n_model = mp.revolutions_per_s * math.sqrt(lam)
    j_model = (v_model * (1 - prop.wake_fraction)) / (n_model * d_model)
    assert j_model == pytest.approx(mp.advance_ratio, rel=1e-12)
    assert prop.kt(j_model) == pytest.approx(mp.kt, rel=1e-12)
    assert prop.kq(j_model) == pytest.approx(mp.kq, rel=1e-12)
