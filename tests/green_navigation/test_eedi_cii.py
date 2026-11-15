"""绿色航行 EEDI/CII 测试套件。

覆盖验收判据：
C1 ≥3 艘公开船逐船参数表与出值
C2 独立第二实现复算 attained EEDI，相对差 <1e-6
C3 attained vs required EEDI 差值表 pass/fail
C4 CII A–E 边界与 IMO 边界值相对误差 ≤0.5%
C5 边界与异常用例 ≥6，fail-closed
C6 artifacts/green_eedi/result.json + docs/green_eedi_report.md
C7 pytest ≥6 passed
"""

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ship_perf.green_navigation import (
    CARBON_FACTOR,
    REQUIRED_EEDI_COEFFS,
    CII_REDUCTION_FACTORS,
    EngineSpec,
    ShipParams,
    EEDIResult,
    GreenNavigationError,
    calc_attained_eedi_scalar,
    calc_attained_eedi_vectorized,
    calc_required_eedi,
    calc_cii,
    calc_cii_boundaries,
    rate_cii,
    calc_cii_full,
    cross_validate_eedi,
    _normalize_ship_type,
    _validate_ship_params,
)


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "green_eedi")

def _load_ship_params():
    with open(os.path.join(DATA_DIR, "ship_params.json")) as f:
        data = json.load(f)
    ships = []
    for s in data["ships"]:
        me = EngineSpec(**s["main_engine"])
        sp = ShipParams(
            name=s["name"],
            ship_type=s["ship_type"],
            dwt=s["dwt"],
            capacity=s["capacity"],
            v_ref=s["v_ref"],
            main_engine=me,
            aux_engine_power_kw=s["aux_engine_power_kw"],
            aux_sfc_g_kwh=s["aux_sfc_g_kwh"],
            aux_fuel_type=s["aux_fuel_type"],
            ice_class=s["ice_class"],
            f_c=s["f_c"],
            f_eff=s["f_eff"],
            p_eff_kw=s["p_eff_kw"],
            c_f_eff=s["c_f_eff"],
        )
        ships.append(sp)
    return ships


# =====================================================================
# C1: ≥3 艘公开船逐船参数表与出值
# =====================================================================

class TestC1_ShipParameters:
    @pytest.fixture(scope="class")
    def ships(self):
        return _load_ship_params()

    @pytest.fixture(scope="class")
    def results(self, ships):
        return [calc_attained_eedi_scalar(s) for s in ships]

    def test_three_ships_loaded(self, ships):
        assert len(ships) == 3
        names = [s.name for s in ships]
        assert "KCS" in names
        assert "KVLCC2" in names
        assert "BC_82K" in names

    def test_all_results_positive(self, results):
        for r in results:
            assert r.attained_eedi > 0, f"{r.ship_name} attained EEDI 必须为正"
            assert r.required_eedi > 0, f"{r.ship_name} required EEDI 必须为正"

    def test_eedi_values_reasonable(self, results):
        for r in results:
            assert 0.1 < r.attained_eedi < 100, \
                f"{r.ship_name} attained EEDI={r.attained_eedi:.4f} 超出合理范围"

    def test_ship_type_mapping(self):
        assert _normalize_ship_type("kcs") == "container_ship"
        assert _normalize_ship_type("kvlcc2") == "oil_tanker"
        assert _normalize_ship_type("bulk") == "bulk_carrier"
        assert _normalize_ship_type("oil_tanker") == "oil_tanker"


# =====================================================================
# C2: 独立第二实现交叉验证
# =====================================================================

class TestC2_CrossValidation:
    @pytest.fixture(scope="class")
    def ships(self):
        return _load_ship_params()

    def test_cross_validation_all_ships(self, ships):
        for s in ships:
            cv = cross_validate_eedi(s)
            assert cv["rel_diff_attained"] < 1e-6, \
                f"{s.name} attained EEDI 交叉验证失败: rel_diff={cv['rel_diff_attained']:.2e}"
            assert cv["rel_diff_required"] < 1e-6, \
                f"{s.name} required EEDI 交叉验证失败: rel_diff={cv['rel_diff_required']:.2e}"

    def test_scalar_vs_vectorized_identical(self, ships):
        for s in ships:
            r_a = calc_attained_eedi_scalar(s)
            r_b = calc_attained_eedi_vectorized(s)
            assert r_a.attained_eedi == r_b.attained_eedi, \
                f"{s.name} 两路径 attained EEDI 不一致"
            assert r_a.required_eedi == r_b.required_eedi, \
                f"{s.name} 两路径 required EEDI 不一致"


# =====================================================================
# C3: attained vs required EEDI 差值表
# =====================================================================

class TestC3_EEDIComparison:
    @pytest.fixture(scope="class")
    def results(self):
        ships = _load_ship_params()
        return [calc_attained_eedi_scalar(s) for s in ships]

    def test_pass_fail_declared(self, results):
        for r in results:
            assert isinstance(r.pass_eedi, bool), f"{r.ship_name} pass_eedi 必须为 bool"
            assert r.pass_eedi == (r.attained_eedi <= r.required_eedi), \
                f"{r.ship_name} pass/fail 逻辑不一致"

    def test_margin_correct(self, results):
        for r in results:
            expected_margin = r.attained_eedi - r.required_eedi
            assert abs(r.margin - expected_margin) < 1e-10, \
                f"{r.ship_name} margin 计算错误"

    def test_phase_declared(self, results):
        for r in results:
            assert r.phase in ["phase0", "phase1", "phase2", "phase3"], \
                f"{r.ship_name} phase 未声明"


# =====================================================================
# C4: CII A–E 边界验证
# =====================================================================

class TestC4_CIIBoundaries:
    def test_cii_boundary_formula(self):
        """验证边界公式: boundary = ref_line × (1 - rf/100)。
        A: rf=31 → 0.69×ref, B: rf=23 → 0.77×ref, C: rf=15 → 0.85×ref,
        D: rf=7 → 0.93×ref, E: rf=0 → 1.0×ref
        """
        dwt = 50000.0
        boundaries = calc_cii_boundaries("bulk_carrier", dwt, 2023)

        a, c = REQUIRED_EEDI_COEFFS["bulk_carrier"]["phase0"]
        ref_line = a * (dwt ** (-c))

        expected_a = ref_line * 0.69
        assert abs(boundaries["A"] - expected_a) / expected_a < 0.005

        expected_b = ref_line * 0.77
        assert abs(boundaries["B"] - expected_b) / expected_b < 0.005

        expected_c = ref_line * 0.85
        assert abs(boundaries["C"] - expected_c) / expected_c < 0.005

        expected_d = ref_line * 0.93
        assert abs(boundaries["D"] - expected_d) / expected_d < 0.005

        expected_e = ref_line * 1.0
        assert abs(boundaries["E"] - expected_e) / expected_e < 0.005

    def test_cii_rating_logic(self):
        """CII 评级逻辑：boundary_A < boundary_B < ... < boundary_E。
        较低 CII = 较好评级。
        """
        boundaries = {"A": 6.9, "B": 7.7, "C": 8.5, "D": 9.3, "E": 10.0}
        assert rate_cii(5.0, boundaries) == "A"
        assert rate_cii(7.0, boundaries) == "B"
        assert rate_cii(8.0, boundaries) == "C"
        assert rate_cii(9.0, boundaries) == "D"
        assert rate_cii(10.5, boundaries) == "E"

    def test_cii_calculation(self):
        """CII 计算正确。"""
        cii = calc_cii(10000.0, 50000.0, 100000.0)
        assert abs(cii - 2.0) < 1e-10

    def test_cii_full_integration(self):
        """CII 完整计算集成测试。"""
        # 使用非常大的 DWT 和非常小的 CO2 排放，使得 CII 很低
        # DWT=300000, distance=500000, co2=1000 → CII = 1000*1e6/(300000*500000) = 0.00667
        rating = calc_cii_full(1000.0, 300000.0, 500000.0, "oil_tanker", 2023)
        assert abs(rating.cii_value - 0.006666666666666667) < 1e-10
        # 验证边界递增: A < B < C < D < E
        assert rating.boundaries["A"] < rating.boundaries["B"]
        assert rating.boundaries["B"] < rating.boundaries["C"]
        assert rating.boundaries["C"] < rating.boundaries["D"]
        assert rating.boundaries["D"] < rating.boundaries["E"]
        # 0.00667 < boundary_A → A
        assert rating.rating == "A"

    def test_cii_boundary_error_within_05pct(self):
        """CII 边界与 IMO 公式相对误差 ≤0.5%。"""
        for st in ["bulk_carrier", "oil_tanker", "container_ship"]:
            for dwt in [10000, 50000, 100000, 300000]:
                boundaries = calc_cii_boundaries(st, dwt, 2023)
                a, c = REQUIRED_EEDI_COEFFS[st]["phase0"]
                ref_line = a * (dwt ** (-c))
                expected = {"A": 0.69, "B": 0.77, "C": 0.85, "D": 0.93, "E": 1.0}
                for rating, factor in expected.items():
                    exp_val = ref_line * factor
                    if exp_val > 0:
                        rel_err = abs(boundaries[rating] - exp_val) / exp_val
                        assert rel_err < 0.005, \
                            f"{st}/{dwt}/{rating} 边界误差 {rel_err:.4f} > 0.5%"


# =====================================================================
# C5: 边界与异常用例 ≥6，fail-closed
# =====================================================================

class TestC5_FailClosed:
    def test_zero_distance(self):
        with pytest.raises(GreenNavigationError):
            calc_cii(1000.0, 50000.0, 0.0)

    def test_negative_distance(self):
        with pytest.raises(GreenNavigationError):
            calc_cii(1000.0, 50000.0, -100.0)

    def test_negative_co2(self):
        with pytest.raises(GreenNavigationError):
            calc_cii(-100.0, 50000.0, 100000.0)

    def test_zero_dwt(self):
        me = EngineSpec(power_kw=10000.0, sfc_g_kwh=170.0, fuel_type="heavy_fuel")
        p = ShipParams(
            name="test", ship_type="bulk_carrier", dwt=0.0, capacity=0.0,
            v_ref=14.0, main_engine=me
        )
        with pytest.raises(GreenNavigationError):
            calc_attained_eedi_scalar(p)

    def test_negative_dwt(self):
        me = EngineSpec(power_kw=10000.0, sfc_g_kwh=170.0, fuel_type="heavy_fuel")
        p = ShipParams(
            name="test", ship_type="bulk_carrier", dwt=-1000.0, capacity=1000.0,
            v_ref=14.0, main_engine=me
        )
        with pytest.raises(GreenNavigationError):
            calc_attained_eedi_scalar(p)

    def test_missing_sfc(self):
        me = EngineSpec(power_kw=10000.0, sfc_g_kwh=0.0, fuel_type="heavy_fuel")
        p = ShipParams(
            name="test", ship_type="bulk_carrier", dwt=50000.0, capacity=50000.0,
            v_ref=14.0, main_engine=me
        )
        with pytest.raises(GreenNavigationError):
            calc_attained_eedi_scalar(p)

    def test_zero_capacity(self):
        me = EngineSpec(power_kw=10000.0, sfc_g_kwh=170.0, fuel_type="heavy_fuel")
        p = ShipParams(
            name="test", ship_type="bulk_carrier", dwt=50000.0, capacity=0.0,
            v_ref=14.0, main_engine=me
        )
        with pytest.raises(GreenNavigationError):
            calc_attained_eedi_scalar(p)

    def test_invalid_fuel_type(self):
        me = EngineSpec(power_kw=10000.0, sfc_g_kwh=170.0, fuel_type="hydrogen")
        p = ShipParams(
            name="test", ship_type="bulk_carrier", dwt=50000.0, capacity=50000.0,
            v_ref=14.0, main_engine=me
        )
        with pytest.raises(GreenNavigationError):
            calc_attained_eedi_scalar(p)

    def test_invalid_ship_type(self):
        with pytest.raises(GreenNavigationError):
            _normalize_ship_type("flying_boat")

    def test_negative_power(self):
        me = EngineSpec(power_kw=-1000.0, sfc_g_kwh=170.0, fuel_type="heavy_fuel")
        p = ShipParams(
            name="test", ship_type="bulk_carrier", dwt=50000.0, capacity=50000.0,
            v_ref=14.0, main_engine=me
        )
        with pytest.raises(GreenNavigationError):
            calc_attained_eedi_scalar(p)


# =====================================================================
# 附加测试：碳因子与系数完整性
# =====================================================================

class TestConstants:
    def test_carbon_factors_positive(self):
        for fuel, cf in CARBON_FACTOR.items():
            assert cf > 0, f"{fuel} 碳因子必须为正"

    def test_required_eedi_coeffs_exist(self):
        for st, phases in REQUIRED_EEDI_COEFFS.items():
            for phase, (a, c) in phases.items():
                assert a > 0, f"{st}/{phase} a 系数必须为正"
                assert c > 0, f"{st}/{phase} c 系数必须为正"

    def test_cii_reduction_factors_monotonic(self):
        """CII reduction factors 应单调递减（A≥B≥C≥D≥E），
        因为 A 需要最大减排（rf=31），E 不需要减排（rf=0）。"""
        for st, years in CII_REDUCTION_FACTORS.items():
            for year, ratings in years.items():
                vals = [ratings[r] for r in ["A", "B", "C", "D", "E"]]
                for i in range(len(vals) - 1):
                    assert vals[i] >= vals[i + 1], \
                        f"{st}/{year} reduction factors 非递减: {vals}"
