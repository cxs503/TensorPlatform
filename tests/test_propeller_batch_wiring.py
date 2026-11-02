"""m1661 · 批量执行器敞水路径接线的守门测试（占位式 → 叶元-动量 BEM）。

守的是 ``tools/batch_scan/batch_executor.py::ShipCalculationEngine.calc_propeller``：
该函数曾经返回线性占位式（KQ 随 J 上升、效率 clip 在 0.85、对几何零响应）。
``tests/test_propeller_open_water.py::TestBatchWrapperWiring`` 只断言了返回键与一处
KQ 单调性；本文件把"接线后是否真的产出物理量"补成一组可机器裁的判据，
并把与文献 B 系列回归（Oosterveld & van Oossanen 1975）的偏差量化成回归护栏。
"""
from __future__ import annotations

import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "batch_scan"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools", "verify", "propeller_open_water"))

from batch_executor import ShipCalculationEngine  # noqa: E402
from backend.services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    ideal_efficiency_bound,
    open_water_point,
    solve_zero_thrust_advance_ratio,
)
from reference_bseries import open_water_coefficients as bs_coeffs  # noqa: E402

BASE = dict(diameter_m=5.0, pitch_ratio=1.0, blade_area_ratio=0.55, blade_count=4, rpm=100)


def params_for_J(J: float, **over) -> dict:
    p = dict(BASE, **over)
    p["advance_speed_kn"] = J * (p["rpm"] / 60.0) * p["diameter_m"] / 0.5144
    return p


def calc(J: float, **over) -> dict:
    return ShipCalculationEngine.calc_propeller(params_for_J(J, **over))


class TestWrapperProducesPhysics:
    def test_returns_bem_marker_and_schema(self):
        out = calc(0.4)
        assert out["model"] == "bem-open-water/v1"
        # 旧字段必须保留（批量扫描报告/判据依赖它们）
        for key in ("advance_coefficient", "thrust_coefficient", "torque_coefficient",
                    "efficiency", "thrust_kn"):
            assert key in out
        assert out["induction_converged"] in (True, False)

    def test_torque_coefficient_falls_with_advance_ratio(self):
        sweep = [calc(J)["torque_coefficient"] for J in (0.0, 0.2, 0.4, 0.6, 0.8)]
        assert all(b < a for a, b in zip(sweep, sweep[1:])), sweep

    def test_thrust_coefficient_falls_and_J0_exists(self):
        geom = PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                 blade_area_ratio=0.55, blade_count=4)
        J0 = solve_zero_thrust_advance_ratio(geom)
        assert 0.0 < J0 < 3.0
        assert calc(0.98 * J0)["thrust_coefficient"] > 0.0
        assert calc(1.02 * J0)["thrust_coefficient"] < 0.0

    def test_efficiency_below_actuator_disc_bound(self):
        geom = PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                 blade_area_ratio=0.55, blade_count=4)
        for J in (0.1, 0.3, 0.5, 0.7, 0.9):
            out = calc(J)
            bound = ideal_efficiency_bound(out["thrust_coefficient"], J, geom)
            assert 0.0 <= out["efficiency"] < bound + 1e-3, (J, out, bound)

    def test_no_efficiency_plateau(self):
        geom = PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                 blade_area_ratio=0.55, blade_count=4)
        J0 = solve_zero_thrust_advance_ratio(geom)
        etas = [calc(f * J0)["efficiency"] for f in (0.6, 0.8, 1.0)]
        assert max(etas) - min(etas) > 1e-3, etas

    def test_geometry_sensitivity(self):
        kt_low = calc(0.6, pitch_ratio=0.8)["thrust_coefficient"]
        kt_high = calc(0.6, pitch_ratio=1.2)["thrust_coefficient"]
        assert abs(kt_high - kt_low) / kt_low > 0.05
        kt_area = calc(0.6, blade_area_ratio=0.70)["thrust_coefficient"]
        kt_ref = calc(0.6, blade_area_ratio=0.40)["thrust_coefficient"]
        assert abs(kt_area - kt_ref) / kt_ref > 0.01
        kt_z6 = calc(0.6, blade_count=6)["thrust_coefficient"]
        kt_z4 = calc(0.6, blade_count=4)["thrust_coefficient"]
        assert abs(kt_z6 - kt_z4) / kt_z4 > 0.01

    def test_froude_invariance(self):
        a = calc(0.6)
        b = ShipCalculationEngine.calc_propeller(
            params_for_J(0.6, diameter_m=10.0, rpm=50.0))
        for key in ("thrust_coefficient", "torque_coefficient", "efficiency"):
            assert a[key] == pytest.approx(b[key], abs=1e-4), (key, a[key], b[key])

    def test_matches_bem_module_pointwise(self):
        for J in (0.0, 0.3, 0.6, 0.9):
            got = calc(J)
            ref = open_water_point(
                PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                  blade_area_ratio=0.55, blade_count=4),
                J, n_rps=100 / 60.0)
            assert got["thrust_coefficient"] == pytest.approx(ref["KT"], abs=1e-4)
            assert got["torque_coefficient"] == pytest.approx(ref["KQ"], abs=1e-4)


class TestLiteratureEnvelope:
    """与 B 系列回归（Oosterveld & van Oossanen 1975）的偏差护栏。

    BEM 是优先原理模型，不应与经验回归逐点重合；护栏只防"再退回占位式"或
    "沉默漂移"。相对误差只在 ``KT_ref >= 0.15`` 的**有界域**上统计：靠近
    零推力点时 KT/η0 的分母趋零，相对误差失去鉴别力（会被小分母放大成 O(100%)），
    那类点只作绝对偏差记录。有界域实测偏差见 `artifacts/m1661/propeller_batch_wiring.json` 的 W9 项。
    """

    @pytest.mark.parametrize("P_D,Ae_Ao,Z", [(1.0, 0.55, 4), (1.0, 0.70, 4), (0.8, 0.55, 4)])
    def test_efficiency_within_envelope(self, P_D, Ae_Ao, Z):
        worst = 0.0
        n_used = 0
        for J in (0.2, 0.4, 0.6, 0.8):
            ref = bs_coeffs(J, P_D, Ae_Ao, Z)
            if ref["KT"] < 0.15:
                continue  # 近零推力点，相对误差不可判
            got = calc(J, pitch_ratio=P_D, blade_area_ratio=Ae_Ao, blade_count=Z)
            worst = max(worst, abs(got["efficiency"] - ref["eta_0"]) / ref["eta_0"])
            n_used += 1
        assert n_used >= 2, (P_D, Ae_Ao, Z, "sample too small")
        assert worst < 0.25, (P_D, Ae_Ao, Z, worst)

    def test_legacy_surrogate_would_violate_envelope(self):
        """占位式必须显著越界——否则本文件的护栏没有鉴别力。"""
        def legacy(J, P_D, Ae_Ao, Z):
            KT = max(0.05, 0.5 - 0.3 * J)
            KQ = max(0.01, 0.05 + 0.02 * J)
            return min(0.85, max(0.3, J * KT / (2 * math.pi * KQ)))

        ref = bs_coeffs(0.8, 1.0, 0.55, 4)
        KT_legacy = max(0.05, 0.5 - 0.3 * 0.8)
        assert abs(KT_legacy - ref["KT"]) / ref["KT"] > 0.25
        # η0 侧：占位式在 J=0.8 触 0.85 天花板，与回归值差异同样越护栏
        assert abs(legacy(0.8, 1.0, 0.55, 4) - ref["eta_0"]) / ref["eta_0"] > 0.25