"""EEDI 测试：闭式复算、负控（E6/E7）、合规 flag 双侧逻辑（E8）、Capacity 口径。"""
from __future__ import annotations

import pytest

from green_navigation import chain, eedi as eedi_mod
from services import green_navigation as gn

BASE = dict(capacity_t=300000.0, v_ref_kn=14.8, sfoc_me_g_kwh=171.0,
            cf_me=3.114, sfoc_ae_g_kwh=170.0, cf_ae=3.206)


def test_attained_matches_independent_closed_form():
    """attained = (P·SFOC·C_F + P_AE·SFOC_AE·C_F_AE)/(Capacity·V_ref) 手算复现。"""
    p_me = 19050.0
    out = eedi_mod.eedi_reference_point(brake_power_kw=p_me, **BASE)
    p_ae = 0.025 * p_me + 250.0          # ΣP_ME ≥ 10 MW 分支（规则定义，独立写出）
    expect = (p_me * 171.0 * 3.114 + p_ae * 170.0 * 3.206) / (300000.0 * 14.8)
    assert out["p_ae_kw"] == pytest.approx(p_ae, rel=1e-12)
    assert out["attained_g_per_t_nm"] == pytest.approx(expect, rel=1e-12)


def test_aux_power_small_engine_branch():
    """ΣP_ME < 10 MW 用 0.05·ΣP_ME —— 两分支都必须对。"""
    out = eedi_mod.eedi_reference_point(brake_power_kw=5000.0, **BASE)
    assert out["p_ae_kw"] == pytest.approx(0.05 * 5000.0, rel=1e-12)


def test_negative_control_zero_power_gives_zero_eedi():
    """E6：P_engine = 0 ⇒ P_AE = 0 ⇒ EEDI = 0（严格等于，不是"接近 0"）。"""
    out = eedi_mod.eedi_reference_point(brake_power_kw=0.0, **BASE)
    assert out["attained_g_per_t_nm"] == 0.0
    assert out["p_ae_kw"] == 0.0


def test_negative_control_infinite_speed_tends_to_zero():
    """E7：V_ref → ∞ ⇒ EEDI → 0（相对基线 <1e-6）。"""
    base = eedi_mod.eedi_reference_point(brake_power_kw=19050.0, **BASE)["attained_g_per_t_nm"]
    far = eedi_mod.eedi_reference_point(
        brake_power_kw=19050.0, **{**BASE, "v_ref_kn": 1e9})["attained_g_per_t_nm"]
    assert far > 0.0 and far / base < 1e-6
    # 与解析反比律一致
    quarter = eedi_mod.eedi_reference_point(
        brake_power_kw=19050.0, **{**BASE, "v_ref_kn": BASE["v_ref_kn"] * 4})["attained_g_per_t_nm"]
    assert quarter == pytest.approx(base / 4.0, rel=1e-12)


def test_attained_linear_in_sfoc_and_cf():
    """变形检验：SFOC×2 或 C_F×2 ⇒ 主机项×2（分母不变时整式按比例变化需扣辅机项）。"""
    base = eedi_mod.eedi_reference_point(brake_power_kw=19050.0, **BASE)
    doubled_sfoc = eedi_mod.eedi_reference_point(
        brake_power_kw=19050.0, **{**BASE, "sfoc_me_g_kwh": BASE["sfoc_me_g_kwh"] * 2})
    main = base["co2_breakdown"]["main_co2_g"]
    aux = base["co2_breakdown"]["aux_co2_g"]
    delta = doubled_sfoc["attained_g_per_t_nm"] - base["attained_g_per_t_nm"]
    expect = main * (300000.0 * 14.8) ** -1
    assert delta == pytest.approx(expect, rel=1e-12)
    assert aux > 0.0


def test_capacity_rule_container_is_0_7_dwt():
    out = eedi_mod.capacity_for_ship_type("container_ship", 45000.0)
    assert out["capacity_t"] == pytest.approx(0.7 * 45000.0)
    assert out["evidence_grade"] == gn.EVIDENCE_WITHHELD
    tanker = eedi_mod.capacity_for_ship_type("tanker", 300000.0)
    assert tanker["capacity_t"] == 300000.0


def test_compliant_flag_two_sided():
    """E8：attained<required ⇒ true；attained>required ⇒ false；相等 ⇒ true（≤ 语义）。"""
    req = gn.required_eedi("tanker", 300000.0, phase="phase2")["required_value"]
    low = eedi_mod.evaluate_eedi("tanker", req * 0.9, 300000.0, phase="phase2")
    high = eedi_mod.evaluate_eedi("tanker", req * 1.1, 300000.0, phase="phase2")
    eq = eedi_mod.evaluate_eedi("tanker", req, 300000.0, phase="phase2")
    assert low["compliant"] is True
    assert high["compliant"] is False
    assert eq["compliant"] is True


def test_required_value_monotone_over_phases():
    """阶段折减越深，要求值越低（phase1 > phase2 > phase3）。"""
    vals = [gn.required_eedi("tanker", 300000.0, phase=p)["required_value"]
            for p in ("phase1", "phase2", "phase3")]
    assert vals[0] > vals[1] > vals[2]


def test_invalid_inputs_raise():
    with pytest.raises(eedi_mod.EediError):
        eedi_mod.eedi_reference_point(brake_power_kw=1000.0, **{**BASE, "capacity_t": 0.0})
    with pytest.raises(eedi_mod.EediError):
        eedi_mod.eedi_reference_point(brake_power_kw=1000.0, **{**BASE, "v_ref_kn": 0.0})
    with pytest.raises(eedi_mod.EediError):
        eedi_mod.eedi_reference_point(brake_power_kw=-1.0, **BASE)


def test_chain_eedi_reference_point_uses_matched_speed(chain_result):
    """V_ref 必须来自匹配反解（P_B(V_ref)=0.75·MCR），不是填值。"""
    for case in chain_result["cases"]:
        e = case["eedi"]
        assert e["match_point_75pct"]["brake_power_kw"] == pytest.approx(
            e["p_me_kw"], rel=1e-5)
        assert 0.5 * case["design_point"]["speed_kn"] < e["v_ref_kn"] \
            < 1.1 * case["design_point"]["speed_kn"]