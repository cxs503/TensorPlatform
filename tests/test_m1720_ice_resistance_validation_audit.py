"""m1720 — level-ice (Lindqvist) 阻力"验证欠账"回归测试。

背景：``services/ice_resistance_uq.py`` 的 ``calibration_factor`` 是
``R_ref / R_raw`` 在该船自己那个试验点上拟合的，因此
``tests/test_pce_ice_resistance_uq.py::test_calibration_matches_trial_mean``
恒真（in-sample 残差 ~1e-16），对模型正确性零判别力。

本文件把审计结论钉成机器判据，防止后续"重新标定"被当成验证：

  J1 标定真空性          —— in-sample 残差为机器零，且对任意篡改参考值仍为零；
  J2 留一交叉验证        —— 单一全局常数预测被留出船，最大误差 ~23% > 15% 门槛；
  J3 同名两实现分歧      —— digital_twin 版为 UQ 版的 0.46~0.67 倍；
  J4 物理标度            —— 结构项 ∝ h²(≈1.7-1.9)、σ_c 线性、速度因子存在（m1720 修复点）；
  J5 证据等级            —— WITHHELD，且 level_ice_validation_report() 与审计 JSON 一致。

审计脚本：artifacts/m1720/audit_ice_resistance.py
审计工件：artifacts/m1720/audit_ice_resistance.json
报告：    docs/m1720_ice_resistance_validation_debt.md
"""
from __future__ import annotations

import json
import math
import os

import pytest

from services.ice_resistance_uq import (
    SHIP_TRIAL_DATA,
    VALIDATION_STATUS,
    _lindqvist_raw,
    level_ice_validation_report,
    lindqvist_ice_resistance,
)
from services.digital_twin.models.resistance import (
    lindqvist_ice_resistance as dt_ice_resistance,
)

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_JSON = os.path.join(HERE, "..", "artifacts", "m1720", "audit_ice_resistance.json")


def _nominal(name):
    t = SHIP_TRIAL_DATA[name]
    p = t["params"]
    return (
        p,
        t["nominal_ice_thickness_m"],
        t["nominal_ice_strength_MPa"],
        t["nominal_friction_coeff"],
        t["nominal_speed_ms"],
        t["mean_resistance_kN"],
    )


# ---------------------------------------------------------------------------
# J1 标定真空性
# ---------------------------------------------------------------------------

class TestCalibrationIsVacuous:
    def test_in_sample_error_is_machine_zero(self):
        """每船 1 个标定点 ⇒ in-sample 误差必然为浮点零，不是验证。"""
        for name in SHIP_TRIAL_DATA:
            p, h, sc, mu, v, ref = _nominal(name)
            pred = lindqvist_ice_resistance(p, h, sc, mu, v)
            assert abs(pred - ref) / ref < 1e-12, name

    def test_tampered_reference_still_matches_perfectly(self):
        """把参考值任意缩放后重标定，模型"依旧通过"——证明该标定无判别力。"""
        for scale in (0.1, 0.37, 1.0, 4.0, 12.0):
            for name in SHIP_TRIAL_DATA:
                p, h, sc, mu, v, ref = _nominal(name)
                raw = _lindqvist_raw(p, h, sc, mu, v)
                ref_fake = ref * scale
                pred = raw * (ref_fake / raw)
                assert abs(pred - ref_fake) / ref_fake < 1e-12

    def test_calibration_factor_identity(self):
        """calibration_factor 精确等于 R_ref / R_raw（拟合式，非独立参数）。"""
        for name in SHIP_TRIAL_DATA:
            p, h, sc, mu, v, ref = _nominal(name)
            raw = _lindqvist_raw(p, h, sc, mu, v)
            assert p.calibration_factor == pytest.approx(ref / raw, rel=1e-14)


# ---------------------------------------------------------------------------
# J2 留一交叉验证
# ---------------------------------------------------------------------------

class TestLeaveOneOut:
    def test_out_of_sample_errors_are_pinned(self):
        report = level_ice_validation_report()
        errs = {r["held_out"]: r["rel_err"] for r in report["leave_one_out"]}
        assert errs["MV_Arctic"] == pytest.approx(0.0320, abs=2e-3)
        assert errs["Polarstern"] == pytest.approx(0.2294, abs=2e-3)
        assert errs["Terry_Fox"] == pytest.approx(-0.2118, abs=2e-3)

    def test_15pct_band_is_missed_out_of_sample(self):
        """诚实结论：修好速度因子后 out-of-sample 最大误差仍 22.9%，超 15% 验收带。"""
        report = level_ice_validation_report()
        assert report["max_out_of_sample_abs_rel_err"] > 0.15
        assert report["meets_15pct_band_out_of_sample"] is False

    def test_loo_errors_are_not_zero_unlike_in_sample(self):
        """LOO 必须显著大于 in-sample 残差，否则说明检验又退化成恒真。"""
        report = level_ice_validation_report()
        assert report["max_out_of_sample_abs_rel_err"] > 1e-3
        assert report["max_in_sample_rel_err"] < 1e-12


# ---------------------------------------------------------------------------
# J3 同名两实现分歧
# ---------------------------------------------------------------------------

class TestImplementationDivergence:
    def test_digital_twin_variant_under_predicts(self):
        report = level_ice_validation_report()
        for row in report["implementation_divergence"]:
            assert 0.40 < row["ratio_dt_over_this"] < 0.75, row

    def test_divergence_bounds_pinned(self):
        """同一船同一冰况，两套"Lindqvist"给出最多 2.2 倍差距。"""
        ratios = []
        for name in SHIP_TRIAL_DATA:
            p, h, sc, mu, v, ref = _nominal(name)
            a = lindqvist_ice_resistance(p, h, sc, mu, v)
            b = dt_ice_resistance(v, p.length_m, p.beam_m, p.draft_m, p.cb, 1.0, h)["R_ice_N"] / 1e3
            ratios.append(b / a)
        assert min(ratios) == pytest.approx(0.457, abs=0.01)
        assert max(ratios) == pytest.approx(0.668, abs=0.01)


# ---------------------------------------------------------------------------
# J4 物理标度
# ---------------------------------------------------------------------------

class TestPhysicalScaling:
    def _p(self):
        return SHIP_TRIAL_DATA["MV_Arctic"]

    def test_structural_terms_scale_with_h_squared(self):
        """σ_c=0、V=0 提取浸没项后，纯结构项 (Rc+Rb) 的 h 指数 ≈2（弯曲项 l_c∝h^0.75 略压低）。"""
        t = self._p()
        p = t["params"]
        sc, mu = t["nominal_ice_strength_MPa"], t["nominal_friction_coeff"]
        sub = _lindqvist_raw(p, 1.0, 0.0, mu, 0.0)
        h1, h2 = 0.8, 1.2
        r1 = _lindqvist_raw(p, h1, sc, mu, 0.0) - _lindqvist_raw(p, h1, 0.0, mu, 0.0)
        r2 = _lindqvist_raw(p, h2, sc, mu, 0.0) - _lindqvist_raw(p, h2, 0.0, mu, 0.0)
        expo = math.log(r2 / r1) / math.log(h2 / h1)
        assert 1.6 <= expo <= 2.0
        assert sub > 0

    def test_total_resistance_h_exponent_is_submersion_dominated(self):
        """总阻力 h 指数只有 ~1.2-1.6：浸没项占比 40-66% 且 ∝h，非纯 h²。"""
        t = self._p()
        p = t["params"]
        sc, mu = t["nominal_ice_strength_MPa"], t["nominal_friction_coeff"]
        r1 = _lindqvist_raw(p, 0.8, sc, mu, 0.0)
        r2 = _lindqvist_raw(p, 1.2, sc, mu, 0.0)
        expo = math.log(r2 / r1) / math.log(1.2 / 0.8)
        assert 1.1 <= expo <= 1.7

    def test_sigma_c_is_linear(self):
        """σ_c 加倍 ⇒ 结构项加倍（浸没/阻力项与 σ_c 无关）。"""
        t = self._p()
        p = t["params"]
        mu = t["nominal_friction_coeff"]
        sub = _lindqvist_raw(p, 1.0, 0.0, mu, 0.0)
        r1 = _lindqvist_raw(p, 1.0, 1.5, mu, 0.0) - sub
        r2 = _lindqvist_raw(p, 1.0, 3.0, mu, 0.0) - sub
        assert r2 / r1 == pytest.approx(2.0, rel=1e-9)

    def test_velocity_multiplier_is_present(self):
        """m1720 修复点：Lindqvist 速度因子必须存在，0→3 m/s 阻力显著上升（≥10%）。"""
        t = self._p()
        p = t["params"]
        sc, mu = t["nominal_ice_strength_MPa"], t["nominal_friction_coeff"]
        r0 = _lindqvist_raw(p, 1.0, sc, mu, 0.0)
        r3 = _lindqvist_raw(p, 1.0, sc, mu, 3.0)
        assert (r3 - r0) / r0 > 0.10
        # 速度因子解析值 (1 + 1.4*3/sqrt(9.81*1)) = 2.34 应精确作用于 (Rc+Rb)
        sub = _lindqvist_raw(p, 1.0, 0.0, mu, 0.0)
        struct0 = r0 - sub
        struct3 = r3 - sub - 0.5 * 1025 * p.beam_m * 1.0 * 9.0 * 0.1 / 1e3
        assert struct3 / struct0 == pytest.approx(1.0 + 1.4 * 3.0 / math.sqrt(9.81), rel=1e-9)


# ---------------------------------------------------------------------------
# J5 证据等级与工件一致性
# ---------------------------------------------------------------------------

class TestEvidenceGrade:
    def test_grade_is_withheld(self):
        assert VALIDATION_STATUS["evidence_grade"] == "WITHHELD"
        assert VALIDATION_STATUS["meets_15pct_band_out_of_sample"] is False

    @pytest.mark.skipif(not os.path.exists(AUDIT_JSON), reason="audit json not generated")
    def test_report_matches_audit_json(self):
        with open(AUDIT_JSON, encoding="utf-8") as fh:
            audit = json.load(fh)
        loo = {r["held_out"]: r["rel_err"] for r in audit["J2_leave_one_out"]["loo"]}
        report = level_ice_validation_report()
        live = {r["held_out"]: r["rel_err"] for r in report["leave_one_out"]}
        for name in loo:
            assert live[name] == pytest.approx(loo[name], rel=1e-9)
        assert audit["J2_leave_one_out"]["verdict_le_15pct"] is False