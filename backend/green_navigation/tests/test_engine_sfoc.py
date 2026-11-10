"""SFOC 油耗图测试：标定残差（E1）、形状物理性、可辨识性诚实性。"""
from __future__ import annotations

import math

import pytest

from green_navigation import chain
from green_navigation.engine import (
    SfocMap, SfocMapError, co2_g_per_h, fit_sfoc_map,
    propeller_law_speed_ratio, validate_sfoc_map,
)


def test_reference_points_load_and_residual_below_5pct(sfoc_model):
    ref = chain.load_json("sfoc_reference.json")["points"]
    val = validate_sfoc_map(sfoc_model, ref, tol_pct=5.0)
    assert val["within_tol"]
    assert val["max_rel_err_pct_075_090"] < 5.0
    assert val["max_rel_err_pct"] < 5.0


def test_calibration_is_independent_of_reference_value(monkeypatch):
    """把 MCR 点整体 +10 g/kWh，拟合出的 sfoc_min 必须跟着动 → 标定真的来自数据。"""
    ref = chain.load_json("sfoc_reference.json")["points"]
    base = fit_sfoc_map(ref)
    bumped = [{**p, "sfoc_g_kwh": p["sfoc_g_kwh"] + 10.0} for p in ref]
    shifted = fit_sfoc_map(bumped)
    assert shifted.sfoc_min_g_kwh == pytest.approx(base.sfoc_min_g_kwh + 10.0, rel=1e-9)
    assert shifted.a == pytest.approx(base.a, rel=1e-9)


def test_sfoc_curve_shape_is_physical(sfoc_model):
    """最优点在 0.75-0.85；两端单调上升；a>0（凸）。"""
    loads = [0.3 + 0.05 * i for i in range(15)]
    vals = [sfoc_model.sfoc(p) for p in loads]
    argmin = loads[min(range(len(vals)), key=vals.__getitem__)]
    assert 0.75 <= argmin <= 0.85
    assert sfoc_model.a > 0
    assert sfoc_model.sfoc(1.0) > sfoc_model.sfoc(0.8)
    assert sfoc_model.sfoc(0.3) > sfoc_model.sfoc(0.8)


def test_sfoc_scaling_identities(sfoc_model):
    """SFOC(1.0) 与参考表 MCR 值数值一致；ΔSFOC 严格等于 a·Δ(P-0.8)²。"""
    ref = {p["load"]: p["sfoc_g_kwh"] for p in chain.load_json("sfoc_reference.json")["points"]}
    assert sfoc_model.sfoc(1.0) == pytest.approx(ref[1.0], rel=0.01)
    p1, p2 = 0.6, 0.9
    delta = sfoc_model.sfoc(p2) - sfoc_model.sfoc(p1)
    expect = sfoc_model.a * ((p2 - sfoc_model.p_opt) ** 2 - (p1 - sfoc_model.p_opt) ** 2)
    assert delta == pytest.approx(expect, rel=1e-12)


def test_n_term_reported_unidentifiable_by_default(sfoc_model):
    """螺旋桨定律下 b 不可辨识 → 默认置 0 且如实标注（不谎称 4 参数都标定）。"""
    assert sfoc_model.b == 0.0
    assert sfoc_model.n_term_identified is False
    fit = sfoc_model.fit
    assert fit["identify_n_term"] is False
    assert "共线" in fit["collinearity_note"]
    assert sfoc_model.as_dict()["evidence"]["b_term"].startswith("WITHHELD")
    cond_if_b = fit["condition_number_if_b_fitted"]
    assert cond_if_b is not None and cond_if_b > 10.0 * fit["normal_equation_condition_number"]


def test_propeller_law_speed_ratio():
    assert propeller_law_speed_ratio(1.0) == pytest.approx(1.0)
    assert propeller_law_speed_ratio(0.125) == pytest.approx(0.5)
    with pytest.raises(SfocMapError):
        propeller_law_speed_ratio(0.0)


def test_invalid_inputs_raise(sfoc_model):
    with pytest.raises(SfocMapError):
        sfoc_model.sfoc(0.0)
    with pytest.raises(SfocMapError):
        fit_sfoc_map([{"load": 0.5, "sfoc_g_kwh": 175.0}])
    with pytest.raises(SfocMapError):
        SfocMap(sfoc_min_g_kwh=170.0, a=50.0).sfoc(0.75, n_over_mcr=0.0)


def test_co2_rate_is_linear_in_power_and_sfoc():
    """CO₂ [g/h] = P·SFOC·C_F —— 三重线性（变形检验）。"""
    cf = 3.114
    base = co2_g_per_h(10000.0, 170.0, cf)
    assert base == pytest.approx(10000.0 * 170.0 * cf, rel=1e-12)
    assert co2_g_per_h(20000.0, 170.0, cf) == pytest.approx(2.0 * base, rel=1e-12)
    assert co2_g_per_h(10000.0, 340.0, cf) == pytest.approx(2.0 * base, rel=1e-12)


def test_2d_fit_identifies_n_term():
    """给出二维标定点时 b 才可辨识（此时才声明 REAL）。"""
    pts = [
        {"load": 0.50, "n_over_mcr": 0.70, "sfoc_g_kwh": 180.0},
        {"load": 0.50, "n_over_mcr": 0.85, "sfoc_g_kwh": 176.5},
        {"load": 0.75, "n_over_mcr": 0.80, "sfoc_g_kwh": 172.0},
        {"load": 0.75, "n_over_mcr": 0.95, "sfoc_g_kwh": 170.5},
        {"load": 1.00, "n_over_mcr": 0.95, "sfoc_g_kwh": 174.0},
        {"load": 1.00, "n_over_mcr": 1.05, "sfoc_g_kwh": 177.0},
    ]
    model = fit_sfoc_map(pts, identify_n_term=True)
    assert model.n_term_identified is True
    assert model.b > 0.0
    assert validate_sfoc_map(model, pts, tol_pct=1.0)["within_tol"]