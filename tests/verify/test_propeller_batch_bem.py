"""m1664 — 生产批量通道必须跑 BEM 物理模型（不是 clamp 替代式）。

历史缺陷：backend/services/propeller_open_water.py 已把敞水桨换成叶元-动量模型，
但 tools/batch_scan/batch_executor.py::calc_propeller 仍保留
    KT = max(0.05, 0.5-0.3J), KQ = max(0.01, 0.05+0.02J), eta0 = clip(...,0.3,0.85)
即"已合并的物理模型在千工况通道里不可见"，且该替代式的 KQ 随 J *上升*（非物理）。
本测试把修复钉死：批量通道与平台服务同源、无 clamp、风车状态不再被抹平。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (str(REPO_ROOT), str(REPO_ROOT / "backend"),
          str(REPO_ROOT / "tools" / "batch_scan")):
    if p not in sys.path:
        sys.path.insert(0, p)

from batch_executor import ShipCalculationEngine  # noqa: E402
from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry, open_water_point, solve_zero_thrust_advance_ratio,
)
from case_generator import generate_propeller_cases  # noqa: E402


def _params(**kw):
    base = dict(diameter_m=5.0, pitch_ratio=1.0, blade_area_ratio=0.55,
                blade_count=4, rpm=120, advance_speed_kn=0.0)
    base.update(kw)
    return base


def _j_to_params(J, **kw):
    p = _params(**kw)
    n = p["rpm"] / 60.0
    p["advance_speed_kn"] = J * n * p["diameter_m"] / 0.5144
    return p


class TestBatchPathIsBEM:
    def test_reports_bem_model(self):
        out = ShipCalculationEngine.calc_propeller(_j_to_params(0.6))
        assert out["model"].startswith("bem")

    def test_matches_service_bitwise(self):
        """批量通道与平台服务共用同一实现 ⇒ 数值必须一致（差异只来自 round）。"""
        for J in (0.2, 0.5, 0.8):
            out = ShipCalculationEngine.calc_propeller(_j_to_params(J))
            geom = PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                     blade_area_ratio=0.55, blade_count=4)
            pt = open_water_point(geom, J, rho=1025.0, n_rps=2.0)
            assert out["thrust_coefficient"] == pytest.approx(pt["KT"], abs=5e-5)
            assert out["torque_coefficient"] == pytest.approx(pt["KQ"], abs=5e-5)

    def test_torque_falls_with_advance_ratio(self):
        """真实桨的扭矩必须随 J 下降（旧替代式 KQ 随 J 上升 = 非物理）。"""
        kqs = [ShipCalculationEngine.calc_propeller(_j_to_params(J))["torque_coefficient"]
               for J in (0.1, 0.3, 0.5, 0.7)]
        assert all(b < a for a, b in zip(kqs, kqs[1:])), kqs

    def test_no_efficiency_plateau(self):
        """η0 不得被钳位成平台值；必须随 J 连续变化且 < 动盘上界。"""
        etas = [ShipCalculationEngine.calc_propeller(_j_to_params(J))["efficiency"]
                for J in (0.2, 0.4, 0.6, 0.8)]
        assert len({round(e, 4) for e in etas}) == len(etas)
        for J, e in zip((0.2, 0.4, 0.6, 0.8), etas):
            out = ShipCalculationEngine.calc_propeller(_j_to_params(J))
            assert e <= out["eta_ideal"] + 1e-6

    def test_geometry_sensitivity(self):
        """KT 必须依赖 P/D、Ae/A0、Z —— 旧替代式三者全无关。"""
        base = ShipCalculationEngine.calc_propeller(_j_to_params(0.0))["thrust_coefficient"]
        pitch = ShipCalculationEngine.calc_propeller(
            _j_to_params(0.0, pitch_ratio=1.2))["thrust_coefficient"]
        area = ShipCalculationEngine.calc_propeller(
            _j_to_params(0.0, blade_area_ratio=0.7))["thrust_coefficient"]
        blade = ShipCalculationEngine.calc_propeller(
            _j_to_params(0.0, blade_count=5))["thrust_coefficient"]
        assert pitch > base and area > base
        assert abs(blade - base) > 1e-4

    def test_windmilling_not_clamped(self):
        """J > J0 时 KT/KQ 必须为负（风车状态），不得被 clamp 成 0.05/0.01。"""
        geom = PropellerGeometry(diameter_m=5.0, pitch_ratio=1.0,
                                 blade_area_ratio=0.55, blade_count=4)
        J0 = solve_zero_thrust_advance_ratio(geom)
        out = ShipCalculationEngine.calc_propeller(_j_to_params(J0 + 0.4, rpm=60))
        assert out["advance_coefficient"] > J0
        assert out["thrust_coefficient"] < 0.0
        assert out["torque_coefficient"] < 0.0

    def test_froude_scaling_invariance(self):
        """D→2D, n→n/2 保持 J 与 KT/KQ（全部无量纲）。"""
        a = ShipCalculationEngine.calc_propeller(_j_to_params(0.6, diameter_m=4.0, rpm=120))
        b = ShipCalculationEngine.calc_propeller(_j_to_params(0.6, diameter_m=8.0, rpm=120))
        assert a["thrust_coefficient"] == pytest.approx(b["thrust_coefficient"], rel=2e-3)
        assert a["torque_coefficient"] == pytest.approx(b["torque_coefficient"], rel=2e-3)

    def test_invalid_geometry_still_rejected(self):
        with pytest.raises(ValueError):
            ShipCalculationEngine.calc_propeller({"diameter_m": 0, "rpm": 100})


class TestCaseGeneratorPhysicalBox:
    def test_advance_ratio_inside_validity_box(self):
        Js = [c["parameters"]["target_advance_coefficient"]
              for c in generate_propeller_cases(30)]
        assert all(0.15 <= j <= 1.10 for j in Js)

    def test_criteria_allow_windmilling(self):
        c = generate_propeller_cases(1)[0]["criteria"]
        assert c["thrust_coefficient"]["min"] < 0.0
        assert c["torque_coefficient"]["min"] < 0.0


class TestBseriesAbsoluteBenchmark:
    """对已入库的绝对量级台账做回归护栏（数值来自 artifacts，不手抄）。"""

    ART = REPO_ROOT / "artifacts" / "m1664" / "bem_vs_bseries.json"

    def test_ledger_present(self):
        assert self.ART.exists(), "run tools/verify/propeller_open_water/benchmark_bem_vs_bseries.py"
        d = json.loads(self.ART.read_text())
        assert d["summary"]["n_points"] >= 200

    def test_measured_band_not_silently_improved(self):
        """BEM vs B 系列：中位 KT 误差已测 ~10.6%。若声称更紧必须补新证据。"""
        d = json.loads(self.ART.read_text())["summary"]
        lb = d["load_band_e_KT"]
        assert 0.03 <= lb["p50"] <= 0.20, lb
        assert lb["frac_over_band"] > 0.20, "若已全点 <15%，需更新台账与报告结论"

    def test_efficiency_respects_actuator_disc_bound(self):
        d = json.loads(self.ART.read_text())
        assert d["summary"]["eta_below_bound_all"] is True
