#!/usr/bin/env python3
"""m2039 机器判据：升沉 FK 激励的有限频率精确核。

每个用例都是一条**独立**判据（闭合解 / 周长求积 / 极限恒等式 / 单调性），
不是对同一次运行的回放。
"""

from __future__ import annotations

import math

import pytest

from src.ship_perf.constants import G as G_PLAT
from src.ship_perf.constants import RHO_SEA as RHO_PLAT
from src.ship_perf.seakeeping import (
    EVIDENCE,
    Hull,
    fk_planform_factor,
    heave_rao,
)
from src.ship_verification.fk_excitation_exact import (
    circle_fk_closed,
    fk_heave_force_hull,
    fk_heave_force_long_wave,
    fk_heave_force_rect_closed,
    fk_heave_force_section,
    long_wave_overestimate,
    rect_section,
    semicircle_section,
    sinc,
    wavenumber,
)

# 验证模块自带常数（独立复算用）；平台常数单独引入，两者差 0.09%/0.06%，比值不受影响
RHO, G = 1025.0, 9.81



# ---------------------------------------------------------------- A1 闭合解


def test_sinc_removable_singularity():
    assert sinc(0.0) == pytest.approx(1.0, rel=0, abs=1e-12)
    for u in (1e-9, 1e-7, 1e-5):
        assert sinc(u) == pytest.approx(1.0 - u * u / 6.0, rel=1e-12)


def test_rect_closed_reduces_to_long_wave_as_k_to_zero():
    """式 (4) 在 k -> 0 时必须退化为 ρgζ A_wp e^{-kT}（同一长波极限）。"""
    L, B, T = 320.0, 58.0, 20.8
    period = 400.0                          # λ ≈ 2.5 km >> L, B
    closed = fk_heave_force_rect_closed(L, B, T, 1.0, period, 90.0)["force_N"]
    long_wave = fk_heave_force_long_wave(L * B, T, 1.0, period)
    assert closed == pytest.approx(long_wave, rel=1e-4)


def test_rect_section_sinc_is_exact_against_perimeter_quadrature():
    """二维：式 (5) 的 b·sinc(kb/2) 与式 (6) 周长求积互校（< 3e-6）。"""
    B, T = 58.0, 20.8
    for period in (6.0, 8.0, 10.0, 14.0):
        k = wavenumber(period)
        closed = RHO * G * 1.0 * B * math.exp(-k * T) * abs(sinc(k * B / 2.0))
        quad = fk_heave_force_section(
            rect_section(B, T, 4000), 1.0, period, 90.0, waterline=T
        )
        assert quad == pytest.approx(closed, rel=1e-6), period


def test_rect_section_long_wave_limit_is_projected_width():
    """k -> 0：升沉 FK 力 → ρgζ × 投影宽度（阿基米德式极限，与形状无关）。"""
    B, T = 58.0, 20.8
    val = fk_heave_force_section(rect_section(B, T, 400), 1.0, 1e5, 90.0, waterline=T)
    assert val == pytest.approx(RHO * G * B, rel=1e-7)


# ---------------------------------------------------------------- A2 半圆截面


def test_semicircle_series_matches_perimeter_quadrature():
    """半圆截面：解析级数（含唯一非零奇次项 -μπ/2）vs 周长求积 < 1e-7。"""
    a = 10.0
    for period in (4.0, 6.0, 8.0, 10.0, 14.0, 20.0):
        closed = abs(circle_fk_closed(a, 1.0, period))
        quad = fk_heave_force_section(
            semicircle_section(a, 20000), 1.0, period, 90.0, waterline=0.0
        )
        assert quad == pytest.approx(closed, rel=1e-7), period


def test_semicircle_series_requires_beam_seas():
    with pytest.raises(ValueError):
        circle_fk_closed(10.0, 1.0, 8.0, heading_deg=60.0)


def test_semicircle_long_wave_limit_is_diameter():
    a = 10.0
    assert circle_fk_closed(a, 1.0, 1e6) == pytest.approx(RHO * G * 2.0 * a, rel=1e-9)


# ---------------------------------------------------------------- A3 三维船体


def test_hull_quadrature_matches_rect_closed_form():
    """式 (3) 数值积分 vs 式 (4) 闭合解（箱型船体，全部 Cb = 1）。"""
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=1.0)
    for period in (6.0, 8.0, 12.0, 20.0):
        quad = fk_heave_force_hull(hull, 1.0, period, 90.0)["force_N"]
        closed = fk_heave_force_rect_closed(320.0, 58.0, 20.8, 1.0, period, 90.0)["force_N"]
        assert quad == pytest.approx(closed, rel=1e-12), period


def test_hull_quadrature_converged():
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82)
    for period in (6.0, 12.0):
        a = fk_heave_force_hull(hull, 1.0, period, 90.0, n_x=2001)["force_N"]
        b = fk_heave_force_hull(hull, 1.0, period, 90.0, n_x=16001)["force_N"]
        assert a == pytest.approx(b, rel=1e-3), period


def test_planform_factor_unity_in_long_wave():
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82)
    for heading in (0.0, 45.0, 90.0):
        assert fk_planform_factor(hull, 1e-10, heading) == pytest.approx(1.0, rel=1e-9)


def test_planform_factor_head_seas_is_length_sinc():
    """首/尾浪（ψ = 0）时相位因子 = |sinc(kL/2)|（水线面积分只剩 x 相位）。"""
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=1.0)
    for period in (8.0, 12.0):
        k = wavenumber(period)
        got = fk_planform_factor(hull, k, 0.0, n_x=200001)
        assert got == pytest.approx(abs(sinc(k * 320.0 / 2.0)), rel=2e-4)


def test_hull_factor_consistent_with_verification_module():
    """seakeeping 内联核与 ship_verification 独立实现一致（< 1e-3）。"""
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82)
    for period in (6.0, 8.0, 14.0):
        k = wavenumber(period)
        a = fk_planform_factor(hull, k, 90.0, n_x=20001)
        b = fk_heave_force_hull(hull, 1.0, period, 90.0)["force_N"] / (
            RHO * G * 1.0 * _awp(hull) * math.exp(-k * hull.draft)
        )
        assert a == pytest.approx(b, rel=1e-3), period


def _awp(hull):
    import numpy as np

    xs = np.linspace(-hull.length / 2, hull.length / 2, 20001)
    return float(np.trapezoid([hull.beam_at(x) for x in xs], xs))


# ---------------------------------------------------------------- A4 债务量化


def test_long_wave_formula_overestimates_at_short_waves():
    """平台长波式对精确 FK 的高估随波长缩短单调放大（债务量化判据）。"""
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82)
    rows = long_wave_overestimate(hull, [6.0, 8.0, 10.0, 12.0, 14.0, 18.0], 90.0)
    ratios = [r["ratio_long_wave_over_exact"] for r in rows]
    assert all(r >= 1.0 - 1e-9 for r in ratios)          # 只会高估
    assert ratios == sorted(ratios, reverse=True)        # 短波更严重
    assert rows[0]["ratio_long_wave_over_exact"] > 5.0   # λ/L ≈ 0.18 时 > 5 倍
    # < 3% 只在 λ/L ≳ 1.4（长涌）成立
    assert rows[-1]["rel_err_pct"] < 3.0


def test_seakeeping_default_is_bit_identical_to_long_wave_formula():
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82)
    mass = 4.5e8
    for rec in heave_rao(hull, [8.0, 12.0], mass):
        k = rec["omega"] ** 2 / G_PLAT
        expect = RHO_PLAT * G_PLAT * 1.0 * hull.waterplane_area * math.exp(-k * hull.draft)
        assert rec["fk_force_N"] == pytest.approx(expect, rel=1e-9)


def test_seakeeping_exact_fk_lowers_short_wave_response():
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82)
    mass = 4.5e8
    long_wave = {r["period"]: r for r in heave_rao(hull, [8.0, 16.0], mass)}
    exact = {
        r["period"]: r
        for r in heave_rao(hull, [8.0, 16.0], mass, fk_model="exact", heading_deg=90.0)
    }
    # 短波（λ/L ≈ 0.31）显著下降，长波（λ/L ≈ 1.25）差别收窄
    ratio_short = exact[8.0]["rao_heave"] / long_wave[8.0]["rao_heave"]
    ratio_long = exact[16.0]["rao_heave"] / long_wave[16.0]["rao_heave"]
    assert ratio_short < 0.70
    assert 0.9 < ratio_long <= 1.0
    assert ratio_short < ratio_long
    # 激励比等于水线面相位因子（同一核，两处实现互证）
    k8 = (2.0 * math.pi / 8.0) ** 2 / G_PLAT
    assert exact[8.0]["fk_force_N"] / long_wave[8.0]["fk_force_N"] == pytest.approx(
        fk_planform_factor(hull, k8, 90.0), rel=1e-3
    )


def test_seakeeping_exact_fk_converges_to_long_wave():
    hull = Hull(length=320.0, beam=58.0, draft=20.8, cb=0.82)
    a = heave_rao(hull, [400.0], 4.5e8)[0]["rao_heave"]
    b = heave_rao(hull, [400.0], 4.5e8, fk_model="exact")[0]["rao_heave"]
    assert b == pytest.approx(a, rel=1e-6)


def test_seakeeping_rejects_unknown_fk_model():
    hull = Hull(length=100.0, beam=15.0, draft=5.0, cb=0.7)
    with pytest.raises(ValueError):
        heave_rao(hull, [6.0], 5.0e6, fk_model="magic")


def test_evidence_declares_new_real_key():
    assert EVIDENCE["heave_fk_excitation_finite_frequency"] == "REAL"
    assert EVIDENCE["radiation_damping"] == "WITHHELD"   # 未变：债务仍在