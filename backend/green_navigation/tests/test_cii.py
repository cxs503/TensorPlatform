"""CII 评级与退化轨迹测试：判级逻辑、边界反解、不支持船型、退化通道物理性。"""
from __future__ import annotations

import pytest

from green_navigation import cii as cii_mod
from services import green_navigation as gn


@pytest.mark.parametrize("ship_type", ["tanker", "bulk_carrier", "container_ship"])
def test_rating_boundaries_are_increasing_and_grade_monotone(ship_type):
    bnd = cii_mod.boundaries_for(ship_type)
    assert list(bnd.dd) == sorted(bnd.dd)
    grades = ["A", "B", "C", "D", "E"]
    # 比值增大，等级不得变好（单调不升）
    idx = [grades.index(bnd.rating(0.5 + 0.02 * i)) for i in range(150)]
    assert all(b >= a for a, b in zip(idx, idx[1:]))


@pytest.mark.parametrize("ship_type", ["tanker", "bulk_carrier", "container_ship"])
def test_rating_boundary_inversion_round_trip(ship_type):
    """反解一致性：ratio = dd_k 恰好落在 k 级上界；dd_k+ε 落到下一级。"""
    bnd = cii_mod.boundaries_for(ship_type)
    grades = ["A", "B", "C", "D", "E"]
    for k, grade in enumerate(grades[:-1]):
        bound = bnd.ratio_at_boundary(grade)
        assert bnd.rating(bound) == grade
        assert bnd.rating(bound * (1 + 1e-9)) == grades[k + 1]


def test_ratio_definition_matches_rule_ratio(chain_result):
    """评级输入的比值必须等于 attained/required（不得另一套口径）。"""
    for case in chain_result["cases"]:
        for row in case["cii"]["trajectory"]["years"]:
            assert row["ratio_to_required"] == pytest.approx(
                row["attained_g_per_t_nm"] / row["required_g_per_t_nm"], rel=1e-12)
            assert cii_mod.boundaries_for(case["ship_type"]).rating(
                row["ratio_to_required"]) == row["rating"]


def test_unsupported_ship_types_raise_not_approximate():
    """容量相关型（气体/LNG）与未登记船型必须显式报错，不给"看起来能算"。"""
    for st in ("gas_carrier", "lng_carrier", "cruise_passenger", "weird_ship"):
        with pytest.raises(cii_mod.CiiError):
            cii_mod.boundaries_for(st)


def test_rating_requires_positive_finite_ratio():
    bnd = cii_mod.boundaries_for("tanker")
    for bad in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(cii_mod.CiiError):
            bnd.rating(bad)


def test_degradation_channel_is_physics_not_constant(chain_result):
    """退化轨迹必须来自粗糙度通道：k_s ↑、P_B ↑、attained CII ↑ 逐项单调。"""
    for case in chain_result["cases"]:
        tr = case["cii"]["trajectory"]
        ks = [y["ks_m"] for y in tr["years"]]
        pb = [y["brake_power_kw"] for y in tr["years"]]
        att = [y["attained_g_per_t_nm"] for y in tr["years"]]
        assert all(b > a for a, b in zip(ks, ks[1:]))
        assert all(b > a for a, b in zip(pb, pb[1:]))
        assert all(b > a for a, b in zip(att, att[1:]))
        assert tr["ks_end_m"] == pytest.approx(
            tr["ks_start_m"] + tr["roughness_growth_m_per_year"] * 4, rel=1e-12)


def test_degradation_rate_scales_with_roughness_growth(chain_result):
    """30 µm/年 场景的退化率必须大于 15 µm/年 场景（1.6~2.2 倍量级）。"""
    for case in chain_result["cases"]:
        sc = case["cii"]["sensitivity_scenarios"]
        r15 = sc["maintained_15um_per_year"]["degradation_pct_per_year"]
        r30 = sc["uncleaned_30um_per_year"]["degradation_pct_per_year"]
        assert r30 > r15
        assert 1.4 < r30 / r15 < 2.5


def test_maintained_scenario_inside_industry_band(chain_result):
    """E5：年均清洗维护场景落在 0.5–1.5 %/年统计带内。"""
    band = cii_mod.load_anchors()["anchors"]["annual_degradation_clarksons"]["band_pct_per_year"]
    for case in chain_result["cases"]:
        tr = case["cii"]["trajectory"]
        assert band[0] <= tr["degradation_pct_per_year"] <= band[1], (
            case["case_id"], tr["degradation_pct_per_year"], band)
        assert tr["within_external_band"] is True


def test_required_cii_tightens_year_over_year(chain_result):
    """要求值逐年收紧（Z 系数）→ required(year) 递减。"""
    for case in chain_result["cases"]:
        req = [y["required_g_per_t_nm"] for y in case["cii"]["trajectory"]["years"]]
        assert all(b <= a + 1e-12 for a, b in zip(req, req[1:]))
        assert req[0] > req[-1]


def test_cii_attained_matches_independent_recomputation(chain_result):
    """attained CII = Σ(FC·C_F)/(Capacity·distance) —— 用年度输出独立复算。"""
    for case in chain_result["cases"]:
        cap = case["eedi"]["capacity"]["capacity_t"]
        for row in case["cii"]["trajectory"]["years"]:
            co2_g = (row["fuel_main_t"] * 1e6 * 3.114) + (row["fuel_aux_t"] * 1e6 * 3.206)
            assert row["attained_g_per_t_nm"] == pytest.approx(
                co2_g / (cap * row["distance_nm"]), rel=1e-9)
            assert row["co2_t"] == pytest.approx(co2_g / 1e6, rel=1e-9)


def test_invalid_profile_and_year_raise():
    with pytest.raises(cii_mod.CiiError):
        cii_mod.OperatingProfile(sea_days_per_year=0.0)
    with pytest.raises(cii_mod.CiiError):
        cii_mod.OperatingProfile(aux_sfoc_g_kwh=0.0)
    bnd = cii_mod.boundaries_for("tanker")
    assert bnd.rating(0.5) == "A"
    assert bnd.as_dict()["evidence_grade"] == gn.EVIDENCE_WITHHELD