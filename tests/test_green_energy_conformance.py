#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IMO 能效链参考实现的回归门禁（变异集 + 恒等式 + 量纲/极限）。

判据分三层：
  L1 恒等式：定义式内部自洽（分子/分母复算、量纲一致、容量口径）；
  L2 极限/单调：物理可解释的方向性（SFC↑→EEDI↑，V↑→EEDI↓，年份↑→CII 要求↓）；
  L3 反事实（关键）：把报告 1044 记录的平台缺陷注入 → 判据必须报警。
"""
import math

import pytest

from tools.verif.green.imo_energy import (
    CF,
    EEDIInput,
    Engine,
    auxiliary_power,
    capacity_of,
    cii_attained_aer,
    cii_attained_cgdistance,
    cii_rating,
    cii_reference,
    cii_required,
    co2_emission_g,
    counterfactual_platform_defects,
    eedi_attained,
    eedi_compliance,
    eedi_reference,
    eedi_required,
    eeoI,
    limited_power_kw,
    _eedi_with_pme,
)


def bulk(dwt=50000, v=14.5, mcr=9000, sfc=175.0, fuel="hfo"):
    return EEDIInput(
        ship_type="bulk_carrier",
        dwt=dwt,
        speed_kn=v,
        main_engines=[Engine(mcr_kw=mcr, sfc=sfc, fuel=fuel)],
    )


# ---------------- L1 恒等式 ----------------
def test_aux_power_continuous_at_10MW():
    """MARPOL Reg.20 辅机分段式在 10 MW 处必须连续（0.025P+250 == 0.05P）。"""
    assert auxiliary_power(10_000.0) == pytest.approx(500.0, rel=1e-12)
    assert auxiliary_power(10_000.0) == pytest.approx(0.05 * 10_000.0, rel=1e-12)
    assert auxiliary_power(9_999.0) < auxiliary_power(10_000.0) < auxiliary_power(10_001.0)


def test_eedi_numerator_denominator_identity():
    r = eedi_attained(bulk())
    assert r["eedi"] == pytest.approx(r["numerator_gph"] / r["denominator"], rel=1e-12)
    assert r["denominator"] == pytest.approx(50000 * 14.5, rel=1e-12)


def test_p_me_is_75pct_mcr():
    r = eedi_attained(bulk(mcr=10000))
    assert r["p_me_kw"] == pytest.approx(7500.0, rel=1e-12)
    # 7500 MW-equivalent < 10 MW → 辅机走 5% 分支
    assert r["p_ae_kw"] == pytest.approx(0.05 * 7500.0, rel=1e-12)


def test_container_capacity_is_70pct_dwt():
    assert capacity_of("container_ship", 150000) == pytest.approx(105000.0)
    assert capacity_of("bulk_carrier", 150000) == pytest.approx(150000.0)


def test_co2_emission_linear_in_fuel():
    assert co2_emission_g(2.0, "hfo") == pytest.approx(2 * co2_emission_g(1.0, "hfo"), rel=1e-12)


# ---------------- L2 单调/极限 ----------------
def test_eedi_linear_in_sfc():
    a = eedi_attained(bulk(sfc=175))["eedi"]
    b = eedi_attained(EEDIInput(ship_type="bulk_carrier", dwt=50000, speed_kn=14.5,
                                main_engines=[Engine(mcr_kw=9000, sfc=350.0, fuel="hfo")],
                                aux_sfc=350.0))["eedi"]
    # 主机项与辅机项同时对 SFC 线性 → 整体严格 2 倍
    assert b == pytest.approx(2 * a, rel=1e-12)


def test_eedi_inverse_in_speed():
    a = eedi_attained(bulk(v=14.5))["eedi"]
    b = eedi_attained(bulk(v=29.0))["eedi"]
    assert b == pytest.approx(a / 2, rel=1e-12)


def test_eedi_inverse_in_dwt():
    a = eedi_attained(bulk(dwt=50000))["eedi"]
    b = eedi_attained(bulk(dwt=100000))["eedi"]
    assert b == pytest.approx(a / 2, rel=1e-12)


def test_reference_line_decreasing_with_size():
    for st in ("bulk_carrier", "tanker", "container_ship"):
        assert eedi_reference(st, 200000) < eedi_reference(st, 50000)


def test_phase_reduction_exact():
    ref = eedi_reference("bulk_carrier", 50000)
    assert eedi_required("bulk_carrier", 50000, 0) == pytest.approx(ref, rel=1e-12)
    assert eedi_required("bulk_carrier", 50000, 3) == pytest.approx(0.70 * ref, rel=1e-12)


def test_eedi_magnitude_plausible_for_bulk_50k():
    """公开典型量级锚（±C 类）：50k DWT 散货船新造船 attained 约 3–8 g/(t·nm)。"""
    assert 3.0 < eedi_attained(bulk())["eedi"] < 8.0


def test_compliance_boundary():
    req = eedi_required("bulk_carrier", 50000, 3)
    assert eedi_compliance(req, req)["compliant"] is True
    assert eedi_compliance(req * 1.01, req)["compliant"] is False
    assert eedi_compliance(req * 0.99, req)["margin_pct"] == pytest.approx(1.0, abs=1e-9)


# ---------------- CII / EEOI ----------------
def test_aer_equals_cgdist_single_voyage():
    a = cii_attained_aer(8000.0, 50000, 60000.0)
    b = cii_attained_cgdistance(8000.0, 50000, 60000.0)
    assert a == pytest.approx(b, rel=1e-12)


def test_aer_double_fuel_double_intensity():
    a = cii_attained_aer(8000.0, 50000, 60000.0)
    assert cii_attained_aer(16000.0, 50000, 60000.0) == pytest.approx(2 * a, rel=1e-12)


def test_aer_magnitude_order():
    """AER 定义式量级：8000 t HFO / (50k dwt · 60k nm) ≈ 8.3 g/(dwt·nm)。"""
    aer = cii_attained_aer(8000.0, 50000, 60000.0)
    assert 8.0 < aer < 8.6


def test_cii_required_decreases_by_year():
    vals = [cii_required("bulk_carrier", 50000, y) for y in (2023, 2024, 2025, 2026)]
    assert all(x > y for x, y in zip(vals, vals[1:]))


def test_cii_reference_monotone_in_dwt():
    assert cii_reference("bulk_carrier", 200000) < cii_reference("bulk_carrier", 20000)


def test_rating_grades_cover_all_five_and_boundaries():
    dwt, year = 50000, 2026
    req = cii_required("bulk_carrier", dwt, year)
    d1, d2, d3, d4 = 0.86, 0.94, 1.06, 1.18
    seen = set()
    for mult, exp in ((0.5, "A"), (d1 - 1e-6, "A"), (d1 + 1e-6, "B"), (d2 + 1e-6, "C"),
                      (d3 + 1e-6, "D"), (d4 + 1e-6, "E")):
        r = cii_rating("bulk_carrier", dwt, year, req * mult)
        assert r["grade"] == exp, (mult, r)
        seen.add(r["grade"])
    assert seen == {"A", "B", "C", "D", "E"}


def test_eeoi_same_dimension_as_aer():
    assert eeoI(8000.0, 50000, 60000.0) == pytest.approx(cii_attained_aer(8000.0, 50000, 60000.0))


def test_eexi_limited_power_reduces_intensity():
    inp = bulk(mcr=9000)
    p75 = 0.75 * 9000
    assert _eedi_with_pme(inp, limited_power_kw(9000, 0.5)) < _eedi_with_pme(inp, p75)
    with pytest.raises(ValueError):
        limited_power_kw(9000, 1.5)


# ---------------- L3 反事实：判据必须抓住 1044 记录的平台缺陷 ----------------
def test_counterfactual_100pct_mcr_is_flagged():
    cf = counterfactual_platform_defects(bulk())["D1_100pct_MCR_no_aux"]
    # 100%MCR 且无辅机项 → 平台偏高，量级 ~+20..+45%
    assert cf["rel_err_pct"] > 15.0
    assert cf["expect_sign"] == "+"


def test_counterfactual_bulk_line_for_container_is_flagged():
    inp = EEDIInput(ship_type="container_ship", dwt=150000, speed_kn=22.0,
                    main_engines=[Engine(mcr_kw=45000, sfc=180.0, fuel="hfo")])
    cf = counterfactual_platform_defects(inp)["D2_bulk_line_only"]
    # 用散货线当集装箱线 → 要求线偏松/偏严的偏差必须显著（>10%）
    assert abs(cf["rel_err_pct"]) > 10.0


def test_counterfactual_phase0_threshold_creates_false_compliance():
    cf = counterfactual_platform_defects(bulk())["D3_phase0_threshold"]
    # Phase-0 阈值比 Phase-3 宽松 42.9%（1/0.7-1），伪合规窗口宽度可量化
    assert cf["extra_margin_pct"] == pytest.approx(100.0 * (1 / 0.7 - 1), rel=1e-9)


def test_counterfactual_thousand_nm_denominator_is_1000x():
    cf = counterfactual_platform_defects(bulk())["D4_ciinm_thousand_nm"]
    assert cf["factor"] == pytest.approx(1000.0, rel=1e-9)


# ---------------- 输入守卫 ----------------
def test_input_guards():
    with pytest.raises(ValueError):
        capacity_of("bulk_carrier", 0)
    with pytest.raises(ValueError):
        eedi_attained(bulk(v=0))
    with pytest.raises(ValueError):
        cii_attained_aer(8000.0, 50000, 0.0)
    with pytest.raises(KeyError):
        eedi_reference("submarine", 50000)
    with pytest.raises(KeyError):
        cii_required("bulk_carrier", 50000, 1999)