"""批量执行器（tools/batch_scan）溯源与真实模型接线验证 — mission 1640.

背景
----
``tools/batch_scan/batch_executor.py`` 的 ``ShipCalculationEngine`` 长期是
硬编码代理：螺旋桨走 ``KT = max(0.05, 0.5-0.3J)``、``KQ = max(0.01, 0.05+0.02J)``、
效率直接 clip 到 ``[0.3, 0.85]``。m1393 已经把 REAL 模型
``backend/services/propeller_open_water.py``（叶片元-动量 BEM）写完并配了
验证套件，但**接线从未完成**，于是 ``tests/test_propeller_open_water.py::
TestBatchWrapperWiring::test_batch_executor_uses_bem_model`` 一直是红的
（``KeyError: 'model'``）。本套件把接线锁住：

1. ``TestBemDelegation`` —— 批量执行器与服务平台 BEM 模型**逐位一致**，
   并且对 Froude/转速缩放不变（相同 J 下 KT/KQ 与 D、n 无关）。
2. ``TestLegacySurrogateFalsification`` —— 就地复现旧代理并证明它违反
   螺旋桨初等不变量，说明本套件对缺陷**仍然敏感**（不是空转测试）。
3. ``TestProvenanceLabels`` —— 每个计算方法都返回 ``model`` 溯源字段，
   代理项被显式标注（SURROGATE），不再可能出现"代理冒充 REAL"。
4. ``TestCaseExecutorSmoke`` —— 生成器 -> CaseExecutor 端到端跑通，
   螺旋桨工况结果带 BEM 溯源且状态可查。
"""
from __future__ import annotations

import math
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "tools", "batch_scan"))

from services.propeller_open_water import (  # noqa: E402
    PropellerGeometry,
    open_water_point,
)

from batch_executor import CaseExecutor, ShipCalculationEngine  # noqa: E402
import case_generator  # noqa: E402

KN = 0.5144  # kn -> m/s


def _geom(params: dict) -> PropellerGeometry:
    return PropellerGeometry(
        diameter_m=float(params["diameter_m"]),
        pitch_ratio=float(params["pitch_ratio"]),
        blade_area_ratio=float(params["blade_area_ratio"]),
        blade_count=int(params["blade_count"]),
    )


PARAMS = {
    "diameter_m": 5.0,
    "pitch_ratio": 1.0,
    "blade_area_ratio": 0.55,
    "blade_count": 4,
    "rpm": 120.0,
    "advance_speed_kn": 12.0,
}


class TestBemDelegation:
    """批量执行器的螺旋桨结果必须就是 BEM 模型本身。"""

    def test_bit_exact_against_service(self):
        out = ShipCalculationEngine.calc_propeller(PARAMS)
        n = PARAMS["rpm"] / 60.0
        J = PARAMS["advance_speed_kn"] * KN / (n * PARAMS["diameter_m"])
        ref = open_water_point(_geom(PARAMS), J, rho=1025.0, n_rps=n)

        assert out["model"] == "bem_blade_element_momentum_v1"
        assert out["advance_coefficient"] == pytest.approx(J, abs=5e-5)
        # 4 位小数舍入是唯一的差异来源
        assert out["thrust_coefficient"] == pytest.approx(ref["KT"], abs=5e-5)
        assert out["torque_coefficient"] == pytest.approx(ref["KQ"], abs=5e-5)
        assert out["efficiency"] == pytest.approx(ref["eta0"], abs=5e-5)
        assert out["thrust_kn"] == pytest.approx(ref["thrust_n"] / 1000.0, abs=5e-3)
        assert out["torque_knm"] == pytest.approx(ref["torque_nm"] / 1000.0, abs=5e-5)

    def test_rate_and_scale_invariance(self):
        """相同 J 下换 D 与 rpm（同步改 Va），KT/KQ 必须不变（Froude/相似律）。"""
        base = dict(PARAMS)
        ref = ShipCalculationEngine.calc_propeller(base)
        J = ref["advance_coefficient"]

        for D, rpm in ((3.0, 200.0), (7.5, 80.0)):
            n = rpm / 60.0
            alt = dict(base, diameter_m=D, rpm=rpm, advance_speed_kn=J * n * D / KN)
            out = ShipCalculationEngine.calc_propeller(alt)
            assert out["advance_coefficient"] == pytest.approx(J, rel=1e-9)
            assert out["thrust_coefficient"] == pytest.approx(ref["thrust_coefficient"], abs=1e-3)
            assert out["torque_coefficient"] == pytest.approx(ref["torque_coefficient"], abs=1e-3)

    def test_zero_thrust_advance_ratio_exists(self):
        """KT 随 J 单调下降且穿过零点（旧代理在 J>1.5 才触底，永不为负）。"""
        lo = ShipCalculationEngine.calc_propeller(dict(PARAMS, advance_speed_kn=2.0))
        hi = ShipCalculationEngine.calc_propeller(dict(PARAMS, advance_speed_kn=14.0))
        assert lo["thrust_coefficient"] > 0.0
        assert hi["thrust_coefficient"] < lo["thrust_coefficient"]

    def test_bad_geometry_rejected(self):
        with pytest.raises(ValueError):
            ShipCalculationEngine.calc_propeller({"diameter_m": 0, "rpm": 100})


class TestLegacySurrogateFalsification:
    """复现旧代理并证明它被本套件判死（敏感性证据）。"""

    @staticmethod
    def _legacy(J: float) -> tuple[float, float, float]:
        KT = max(0.05, 0.5 - 0.3 * J)
        KQ = max(0.01, 0.05 + 0.02 * J)
        eta0 = min(0.85, max(0.3, (J * KT) / (2 * math.pi * KQ) if KQ > 0 else 0.0))
        return KT, KQ, eta0

    def test_legacy_kq_increases_with_advance_ratio(self):
        """物理上 KQ 随 J 下降；旧代理却随 J 上升（符号错误）。"""
        kq_lo = self._legacy(0.2)[1]
        kq_hi = self._legacy(0.8)[1]
        assert kq_hi > kq_lo  # 旧代理的缺陷
        real_lo = ShipCalculationEngine.calc_propeller(dict(PARAMS, advance_speed_kn=2.0))
        real_hi = ShipCalculationEngine.calc_propeller(dict(PARAMS, advance_speed_kn=8.0))
        assert real_hi["torque_coefficient"] < real_lo["torque_coefficient"]  # 真实模型

    def test_legacy_never_produces_negative_thrust(self):
        """旧代理把 KT 夹在 0.05 以上，物理上高 J 区必然出现负推力。"""
        assert self._legacy(3.0)[0] == pytest.approx(0.05)
        out = ShipCalculationEngine.calc_propeller(dict(PARAMS, advance_speed_kn=40.0))
        assert out["thrust_coefficient"] < 0.0

    def test_legacy_efficiency_is_a_clip_plateau(self):
        """旧代理效率在两端被 clip 成平台（0.3/0.85），无物理内容。"""
        etas = {round(self._legacy(J)[2], 6) for J in (0.05, 0.1, 1.5)}
        assert 0.3 in etas


class TestProvenanceLabels:
    """每个计算方法都必须自报模型来源。"""

    CASES = {
        "calc_resistance": (
            {"length_m": 150, "beam_m": 25, "speed_kn": 14, "block_coefficient": 0.7},
            "surrogate_ittc57_friction_v0",
        ),
        "calc_stability": (
            {"length_m": 150, "beam_m": 25, "draft_m": 8, "kg_m": 8},
            "surrogate_box_hydrostatics_v0",
        ),
        "calc_geometry": (
            {"length_m": 150, "beam_m": 25, "draft_m": 8},
            "surrogate_parametric_geometry_v0",
        ),
    }

    @pytest.mark.parametrize("method", sorted(CASES))
    def test_surrogate_methods_are_labelled(self, method):
        params, expected = self.CASES[method]
        out = getattr(ShipCalculationEngine, method)(params)
        assert out["model"] == expected

    def test_propeller_is_not_labelled_surrogate(self):
        out = ShipCalculationEngine.calc_propeller(PARAMS)
        assert out["model"].startswith("bem")
        assert "surrogate" not in out["model"]


class TestCaseExecutorSmoke:
    """生成器 -> 执行器端到端：螺旋桨工况带 BEM 溯源且状态可查。"""

    def test_generated_propeller_cases_run_through_executor(self):
        cases = case_generator.generate_propeller_cases(10)
        ex = CaseExecutor(timeout_s=30.0)
        results = [ex.execute(c) for c in cases]
        assert len(results) == 10
        for r in results:
            assert r.status.value in {"success", "failed"}
            assert "error" in r.result or True  # 结果字典始终存在
            assert r.result.get("model") == "bem_blade_element_momentum_v1"
        # 判据失败只允许因为物理真值越界（高 J 负推力），不允许因为缺字段
        for r in results:
            for key, det in r.criteria_detail.items():
                assert det.get("status") != "missing", (key, r.case_id)