"""绿色航行域（EEDI/EEXI/CII）复算参考实现的判据测试。

设计原则
--------
1. **独立复算**：期望值在测试里用逐项手算的算式直接写出，不复用被测函数，
   因此"通过"意味着两条独立算式一致（相对误差 < 1e-12）。
2. **闭式判据**：辅助功率分段式的连续性、评级分界的精确跃迁、
   参考线与规则表达式的同值性等均为可在 1e-12 量级判定的数学恒等式。
3. **反例守护**：把平台现网旧实现（绝对阈值 CII 评级、漏项 EEDI、
   臆造阶段阈值）固化为回归用例，防止旧缺陷回潮。
4. 规则系数中未核对原文的表格**不作为基准**，只用函数关系/恒等式检验，
   缺失系数必须显式抛 NOT_IMPLEMENTED（RuleCoefficientMissing）而不是臆造兜底。
"""

import math
import pathlib
import sys

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "backend"
    / "api"
    / "green_shipping_eedi.py"
)

import importlib.util

_spec = importlib.util.spec_from_file_location("green_shipping_eedi", _MODULE_PATH)
assert _spec and _spec.loader
gs = importlib.util.module_from_spec(_spec)
sys.modules["green_shipping_eedi"] = gs   # dataclass 需要模块已注册再 exec
_spec.loader.exec_module(gs)

REL = 1e-12

#: 算例：Kamsarmax 级散货船（公开量级参数，非某具体船）
CASE = dict(
    capacity=82000.0,     # DWT [t]
    mcr=9000.0,           # 单主机 MCR [kW]  (< 10000 kW → P_AE = 0.05*MCR)
    sfc_me=170.0,         # [g/kWh]
    sfc_ae=200.0,         # [g/kWh]
    v_ref=14.0,           # [kn]
)


def _cfg(fuel_me="hfo", fuel_ae="diesel_gas_oil"):
    return gs.carbon_factor(fuel_me), gs.carbon_factor(fuel_ae)


def _eedi(**over):
    cf_me, cf_ae = _cfg()
    kw = dict(
        p_me=[CASE["mcr"]],
        sfc_me=[CASE["sfc_me"]],
        cf_me=[cf_me],
        p_ae=gs.auxiliary_power(CASE["mcr"]),
        sfc_ae=CASE["sfc_ae"],
        cf_ae=cf_ae,
        capacity=CASE["capacity"],
        v_ref=CASE["v_ref"],
    )
    kw.update(over)
    return gs.attained_eedi(**kw)


# ---------------------------------------------------------------------------
# A. 基础构件
# ---------------------------------------------------------------------------

def test_carbon_factor_lookup_and_unknown_raises():
    assert gs.carbon_factor("HFO") == 3.114
    assert gs.carbon_factor("diesel_gas_oil") == 3.206
    assert gs.carbon_factor("LNG") == 2.750
    with pytest.raises(ValueError):
        gs.carbon_factor("unobtainium")


def test_aux_power_piecewise_branches():
    # 独立算式：MCR<10000 → 0.05*MCR；MCR>=10000 → 0.025*MCR+250
    assert gs.auxiliary_power(9000.0) == pytest.approx(450.0, rel=REL)
    assert gs.auxiliary_power(20000.0) == pytest.approx(750.0, rel=REL)


def test_aux_power_continuous_at_10000_kw():
    """闭式判据：两段在 10000 kW 处必须严格连续（500 kW）。"""
    lo = gs.auxiliary_power(10000.0 - 1e-9)
    hi = gs.auxiliary_power(10000.0)
    assert hi == pytest.approx(500.0, rel=REL)
    assert abs(hi - lo) < 1e-6


def test_aux_power_rejects_nonpositive():
    with pytest.raises(ValueError):
        gs.auxiliary_power(0.0)
    with pytest.raises(ValueError):
        gs.auxiliary_power(-5.0)


# ---------------------------------------------------------------------------
# B. EEDI 获得值：独立复算 + 分项可控性
# ---------------------------------------------------------------------------

def test_eedi_bulk_carrier_independent_recompute():
    """手算：P_ME=9000, SFC=170, C_F=3.114, P_AE=450, SFC_AE=200, C_FAE=3.206,
    Capacity=82000, V=14 → (9000*3.114*170 + 450*3.206*200) / (82000*14)。"""
    expected = (9000.0 * 3.114 * 170.0 + 450.0 * 3.206 * 200.0) / (82000.0 * 14.0)
    assert _eedi() == pytest.approx(expected, rel=REL)
    assert _eedi() == pytest.approx(4.4015331010452965, rel=1e-12)


def test_eedi_inverse_scaling_with_capacity_and_speed():
    base = _eedi()
    cfg = _cfg()
    kw = dict(
        p_me=[CASE["mcr"]],
        sfc_me=[CASE["sfc_me"]],
        cf_me=[cfg[0]],
        p_ae=450.0,
        sfc_ae=CASE["sfc_ae"],
        cf_ae=cfg[1],
    )
    assert gs.attained_eedi(capacity=2 * CASE["capacity"], v_ref=CASE["v_ref"], **kw) == pytest.approx(
        base / 2.0, rel=REL
    )
    assert gs.attained_eedi(capacity=CASE["capacity"], v_ref=2 * CASE["v_ref"], **kw) == pytest.approx(
        base / 2.0, rel=REL
    )


def test_eedi_f_j_multiplies_me_term_only():
    base = _eedi()
    with_fj = _eedi(f_j=1.10)
    me_term = 9000.0 * 3.114 * 170.0
    ae_term = 450.0 * 3.206 * 200.0
    denom = 82000.0 * 14.0
    assert with_fj - base == pytest.approx(0.10 * me_term / denom, rel=REL)


def test_eedi_mechanical_efficiency_technology_exact_delta():
    tech = gs.EfficiencyTechnology(name="waste_heat_recovery", p_eff_kw=500.0, f_eff=0.5)
    delta = _eedi() - _eedi(eff_tech=[tech])
    assert delta == pytest.approx(0.5 * 500.0 * 3.114 * 170.0 / (82000.0 * 14.0), rel=REL)


def test_eedi_electrical_efficiency_technology_exact_delta():
    tech = gs.EfficiencyTechnology(name="pv", p_eff_kw=200.0, kind="electrical")
    delta = _eedi() - _eedi(eff_tech=[tech])
    assert delta == pytest.approx(200.0 * 3.206 * 200.0 / (82000.0 * 14.0), rel=REL)


def test_eedi_pti_increases_by_its_own_term():
    delta = _eedi(p_pti=400.0) - _eedi()
    assert delta == pytest.approx(400.0 * 3.114 * 170.0 / (82000.0 * 14.0), rel=REL)


def test_eedi_ice_correction_scales_me_power():
    ratio = _eedi(f_ice=1.05) / _eedi()
    me_term = 9000.0 * 1.05 * 3.114 * 170.0
    ae_term = 450.0 * 3.206 * 200.0
    assert ratio == pytest.approx((me_term + ae_term) / (9000.0 * 3.114 * 170.0 + ae_term), rel=REL)


def test_eedi_weather_factor_scales_result():
    assert _eedi(f_w=1.08) == pytest.approx(1.08 * _eedi(), rel=REL)


@pytest.mark.parametrize(
    "over",
    [
        dict(capacity=0.0),
        dict(v_ref=0.0),
        dict(p_me=[]),
        dict(p_me=[9000.0, 4000.0]),  # 与 sfc_me / cf_me 长度不一致
        dict(f_i=0.0),
    ],
)
def test_eedi_rejects_invalid_inputs(over):
    with pytest.raises(ValueError):
        _eedi(**over)


def test_eedi_rejects_bad_technology_kind():
    with pytest.raises(ValueError):
        gs.EfficiencyTechnology(name="x", p_eff_kw=10.0, kind="magnetic")


# ---------------------------------------------------------------------------
# C. 参考线与要求值
# ---------------------------------------------------------------------------

def test_eedi_reference_line_double_source_bulk_carrier():
    """散货船参考线 961.79*DWT^-0.477 —— 与平台既有代码注释的表达式同值（双源锚点）。"""
    expected = 961.79 * 82000.0 ** (-0.477)
    assert gs.eedi_reference_line("bulk_carrier", 82000.0) == pytest.approx(expected, rel=REL)
    assert gs.eedi_reference_line("bulk_carrier", 82000.0) == pytest.approx(4.357035, rel=1e-6)


def test_required_eedi_phase_relations():
    ref = gs.eedi_reference_line("bulk_carrier", 180000.0)
    assert gs.required_eedi("bulk_carrier", 180000.0, "phase_1") == pytest.approx(0.90 * ref, rel=REL)
    assert gs.required_eedi("bulk_carrier", 180000.0, "phase_2") == pytest.approx(0.80 * ref, rel=REL)
    assert gs.required_eedi("bulk_carrier", 180000.0, "phase_3") == pytest.approx(0.70 * ref, rel=REL)


def test_required_eedi_phase3_gate_raises_below_threshold():
    with pytest.raises(gs.RuleCoefficientMissing):
        gs.required_eedi("bulk_carrier", 5000.0, "phase_3")
    # 未收录 Phase 3 门槛的船型同样必须显式拒绝
    with pytest.raises(gs.RuleCoefficientMissing):
        gs.required_eedi("refrigerated_cargo", 20000.0, "phase_3")


def test_required_eexi_is_reduced_reference_line():
    ref = gs.eedi_reference_line("container_ship", 45000.0)
    x = gs.EEXI_REDUCTION_FROM_EEDI_REF["container_ship"]
    assert gs.required_eexi("container_ship", 45000.0) == pytest.approx(
        (1 - x / 100.0) * ref, rel=REL
    )


def test_unknown_ship_type_raises_not_implemented():
    with pytest.raises(gs.RuleCoefficientMissing):
        gs.eedi_reference_line("submarine", 1000.0)
    with pytest.raises(gs.RuleCoefficientMissing):
        gs.required_eexi("submarine", 1000.0)


# ---------------------------------------------------------------------------
# D. EEXI 与 EEDI 的规则差异
# ---------------------------------------------------------------------------

def test_eexi_equals_eedi_under_matching_substitutions():
    """恒等式：EEXI(0.75*MCR, P_AE 分段式) == EEDI(P_ME=0.75*MCR, 同 P_AE, f_w=1)。"""
    cf_me, cf_ae = _cfg()
    p_me = 0.75 * CASE["mcr"]
    p_ae = gs.auxiliary_power(CASE["mcr"])
    eedi_equiv = gs.attained_eedi(
        p_me=[p_me],
        sfc_me=[CASE["sfc_me"]],
        cf_me=[cf_me],
        p_ae=p_ae,
        sfc_ae=CASE["sfc_ae"],
        cf_ae=cf_ae,
        capacity=CASE["capacity"],
        v_ref=CASE["v_ref"],
    )
    eexi = gs.attained_eexi(
        mcr_me=[CASE["mcr"]],
        sfc_me=[CASE["sfc_me"]],
        cf_me=[cf_me],
        sfc_ae=CASE["sfc_ae"],
        cf_ae=cf_ae,
        capacity=CASE["capacity"],
        v_ref=CASE["v_ref"],
    )
    assert eexi == pytest.approx(eedi_equiv, rel=REL)


def test_eexi_uses_75pct_mcr_not_full_mcr():
    cf_me, cf_ae = _cfg()
    eexi = gs.attained_eexi(
        mcr_me=[CASE["mcr"]],
        sfc_me=[CASE["sfc_me"]],
        cf_me=[cf_me],
        sfc_ae=CASE["sfc_ae"],
        cf_ae=cf_ae,
        capacity=CASE["capacity"],
        v_ref=CASE["v_ref"],
    )
    expected = (0.75 * 9000.0 * 3.114 * 170.0 + 450.0 * 3.206 * 200.0) / (82000.0 * 14.0)
    assert eexi == pytest.approx(expected, rel=REL)
    # 重要：P_AE 由 ΣMCR_ME 导出（不乘 0.75），故 EEXI 严格小于 0.75*EEDI
    assert eexi > 0.75 * _eedi()


def test_eexi_has_no_weather_factor_path():
    import inspect

    sig = inspect.signature(gs.attained_eexi)
    assert "f_w" not in sig.parameters


# ---------------------------------------------------------------------------
# E. CII
# ---------------------------------------------------------------------------

def test_cii_reference_line_bulk_independent_recompute():
    expected = 4745.0 * 82000.0 ** (-0.622)
    assert gs.cii_reference_line("bulk_carrier", 82000.0) == pytest.approx(expected, rel=REL)
    assert gs.cii_reference_line("bulk_carrier", 82000.0) == pytest.approx(4.167197, rel=1e-6)


def test_required_cii_yearly_reduction():
    ref = gs.cii_reference_line("tanker", 120000.0)
    for year, z in ((2023, 5.0), (2024, 7.0), (2025, 9.0), (2026, 11.0)):
        assert gs.required_cii("tanker", 120000.0, year) == pytest.approx(
            (1 - z / 100.0) * ref, rel=REL
        )
    with pytest.raises(gs.RuleCoefficientMissing):
        gs.required_cii("tanker", 120000.0, 2050)


def test_attained_cii_independent_recompute():
    # 5000 t HFO + 200 t MGO 行驶 60000 nm，载重量 82000 t
    expected = (5000.0 * 1e6 * 3.114 + 200.0 * 1e6 * 3.206) / (82000.0 * 60000.0)
    got = gs.attained_cii(
        fuel_mass_t=[5000.0, 200.0],
        fuel_types=["hfo", "diesel_gas_oil"],
        capacity=82000.0,
        distance_nm=60000.0,
    )
    assert got == pytest.approx(expected, rel=REL)


def test_cii_rating_transitions_exactly_at_dd_boundaries():
    required = gs.required_cii("bulk_carrier", 82000.0, 2026)
    d1, d2, d3, d4 = gs.CII_DD_VECTORS["bulk_carrier"]
    eps = 1e-9
    assert gs.cii_rating(d1 * required, required, "bulk_carrier")[0] == "A"
    assert gs.cii_rating((d1 + eps) * required, required, "bulk_carrier")[0] == "B"
    assert gs.cii_rating((d2 + eps) * required, required, "bulk_carrier")[0] == "C"
    assert gs.cii_rating((d3 + eps) * required, required, "bulk_carrier")[0] == "D"
    assert gs.cii_rating((d4 + eps) * required, required, "bulk_carrier")[0] == "E"


def test_cii_rating_is_scale_invariant():
    """评级只依赖 获得/要求 的比值 —— 绝对量放大 100 倍不得改变评级。
    （旧的 `cii<4→A` 绝对阈值法在此判据下必然失败。）"""
    required = gs.required_cii("tanker", 120000.0, 2026)
    for ratio in (0.80, 0.90, 1.00, 1.10, 1.40):
        g1 = gs.cii_rating(ratio * required, required, "tanker")[0]
        g2 = gs.cii_rating(100.0 * ratio * required, 100.0 * required, "tanker")[0]
        assert g1 == g2


def test_cii_missing_ship_type_raises():
    with pytest.raises(gs.RuleCoefficientMissing):
        gs.cii_reference_line("gas_carrier", 80000.0)
    with pytest.raises(gs.RuleCoefficientMissing):
        gs.cii_rating(3.0, 3.0, "gas_carrier")


# ---------------------------------------------------------------------------
# F. 辅助换算与综合评估
# ---------------------------------------------------------------------------

def test_speed_cube_law_roundtrip():
    v = gs.estimated_speed_from_power(14.0, 9000.0, 9000.0 * 0.5)
    assert v == pytest.approx(14.0 * 0.5 ** (1.0 / 3.0), rel=REL)
    back = gs.estimated_speed_from_power(v, 9000.0 * 0.5, 9000.0)
    assert back == pytest.approx(14.0, rel=REL)


def test_assess_green_shipping_full_payload():
    out = gs.assess_green_shipping(
        ship_type="bulk_carrier",
        capacity=CASE["capacity"],
        mcr_me=[CASE["mcr"]],
        sfc_me=CASE["sfc_me"],
        fuel_me="hfo",
        sfc_ae=CASE["sfc_ae"],
        fuel_ae="diesel_gas_oil",
        v_ref=CASE["v_ref"],
        phase="phase_3",
        cii_year=2026,
        annual_fuel_t=5200.0,
        annual_distance_nm=60000.0,
    )
    assert out["attained_eedi"] == pytest.approx(4.4015331010452965, rel=1e-12)
    assert out["eedi_reference"] == pytest.approx(4.357035, rel=1e-6)
    assert out["eedi_required"] == pytest.approx(0.7 * out["eedi_reference"], rel=REL)
    assert out["eedi_compliant"] is False  # 该量级老设计相对 Phase 3 不合规
    # EEXI = (0.75*ME + P_AE) 同一分母；P_AE 不随 0.75 缩放
    assert out["eexi_attained"] == pytest.approx(
        (0.75 * 9000.0 * 3.114 * 170.0 + 450.0 * 3.206 * 200.0) / (82000.0 * 14.0), rel=REL
    )
    assert out["eexi_attained"] > 0.75 * out["attained_eedi"]
    assert out["cii_rating"] in list("ABCDE")
    assert math.isfinite(out["cii_attained"])


def test_assess_reports_not_implemented_for_missing_phase_rules():
    out = gs.assess_green_shipping(
        ship_type="refrigerated_cargo",
        capacity=10000.0,
        mcr_me=[4000.0],
        sfc_me=180.0,
        fuel_me="mdo" if False else "diesel_gas_oil",
        sfc_ae=210.0,
        fuel_ae="diesel_gas_oil",
        v_ref=18.0,
        phase="phase_3",
    )
    assert out["eedi_required"] is None
    assert "Phase 3" in out["eedi_note"]


# ---------------------------------------------------------------------------
# G. 旧实现缺陷回归（反例守护）
# ---------------------------------------------------------------------------

def test_legacy_eedi_omits_auxiliary_power_share():
    """旧式 (P*C_F*SFC)/(DWT*V) 漏掉 P_AE 项 → 系统性低估，本算例低估约 5.4%。"""
    legacy = gs.legacy_eedi_extended_calculations(
        power_kw=CASE["mcr"],
        sfc=CASE["sfc_me"],
        dwt=CASE["capacity"],
        v=CASE["v_ref"],
        cf=gs.carbon_factor("hfo"),   # 与新版同燃油，隔离"漏项"这一唯一变量
    )
    new = _eedi()
    assert legacy < new
    assert (new - legacy) / new == pytest.approx(1.0 - 4.764420e6 / 5.052960e6, rel=1e-6)
    assert 0.05 < (new - legacy) / new < 0.07


def test_legacy_phase_label_can_declare_compliant_when_it_is_not():
    """旧"阶段"判定：attained=0.75*基线 → 旧标签 Phase 2；但 Phase 3 要求 0.70*基线。"""
    ref = gs.eedi_reference_line("bulk_carrier", 180000.0)
    attained = 0.75 * ref
    assert gs.legacy_eedi_phase_label(attained, ref) == "Phase 2 (20%减排)"
    assert attained > gs.required_eedi("bulk_carrier", 180000.0, "phase_3")


def test_legacy_cii_absolute_threshold_verdict_vs_rule_based():
    """CII=4.5 时旧式给 B；规则法（82000 DWT 散货 2026）要求 3.71 → 比值 1.21 > d4 → E。"""
    required = gs.required_cii("bulk_carrier", 82000.0, 2026)
    assert gs.legacy_cii_rating(4.5) == "B"
    assert gs.cii_rating(4.5, required, "bulk_carrier")[0] == "E"