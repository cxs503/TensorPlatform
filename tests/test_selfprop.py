"""自航点反解与推进效率链测试（m21817）。

覆盖三类核心断言：
  1. 自航点反解收敛（C1：残差 ≤ 2%）
  2. ηR 非平凡（C2：方差 > 0，物理窗 [0.90,1.10]）
  3. 乘性恒等非平凡性断言（C3：任一因子方差为 0 → fail）
"""

import math
import statistics
import json
import os

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ship_perf.selfprop import (
    holtrop_etaR,
    solve_selfprop_point,
    solve_selfprop_batch,
    check_triviality,
    SelfPropPoint,
    SelfPropResult,
    result_to_dict,
)
from ship_perf.propeller import wake_fraction, thrust_deduction


# ================================================================
# 1. 自航点反解收敛测试（C1）
# ================================================================

class TestSelfpropConvergence:
    """自航点反解：|T(1−t)−R_T|/R_T ≤ 2%，逐状态。"""

    def test_single_point_residual(self):
        """单点残差 ≤ 2%。"""
        cb, lpp, b, t_draft = 0.72, 200.0, 30.0, 9.5
        d = 7.0
        v_ms = 8.0
        rt_N = 5e5
        p = solve_selfprop_point(
            state_id="S01", rt_N=rt_N, v_ms=v_ms,
            d=d, cb=cb, b=b, t_draft=t_draft, lpp=lpp,
        )
        assert p.residual <= 0.02, f"残差 {p.residual:.6f} > 2%"

    def test_thrust_equilibrium(self):
        """推力平衡：T(1−t) ≈ R_T。"""
        cb, lpp, b, t_draft = 0.72, 200.0, 30.0, 9.5
        d = 7.0
        v_ms = 8.0
        rt_N = 5e5
        p = solve_selfprop_point(
            state_id="S01", rt_N=rt_N, v_ms=v_ms,
            d=d, cb=cb, b=b, t_draft=t_draft, lpp=lpp,
        )
        t_ = thrust_deduction(cb)
        thrust_balance = p.thrust_N * (1.0 - t_)
        rel_err = abs(thrust_balance - p.rt_N) / p.rt_N
        assert rel_err <= 0.02, f"推力平衡误差 {rel_err:.6f} > 2%"

    def test_result_json_residuals(self):
        """从 result.json 验证所有状态残差 ≤ 2%。"""
        result_path = os.path.join(os.path.dirname(__file__), "..", "artifacts", "selfprop", "result.json")
        if os.path.exists(result_path):
            with open(result_path) as f:
                data = json.load(f)
            for pt in data["points"]:
                assert pt["residual"] <= 0.02, (
                    f"状态 {pt['state_id']} 残差 {pt['residual']:.6f} > 2%"
                )


# ================================================================
# 2. ηR 非平凡测试（C2）
# ================================================================

class TestEtaRNonTrivial:
    """ηR 非常数（样本方差 > 0）且落在 [0.90,1.10] 的状态数 ≥ 9/11。"""

    def test_etaR_variance_positive(self):
        """ηR 方差 > 0（非常数）。"""
        results = []
        for i in range(11):
            cb = 0.700 + i * 0.004
            lpp, b, t_draft = 200.0, 30.0, 9.5
            er = holtrop_etaR(cb, b, t_draft, lpp)
            if er is not None:
                results.append(er)
        assert len(results) >= 2
        var_r = statistics.variance(results)
        assert var_r > 1e-12, f"ηR 方差 {var_r:.2e} ≈ 0（恒等）"

    def test_etaR_in_physical_window(self):
        """ηR 落在 [0.90,1.10] 的状态数 ≥ 9/11。"""
        in_window = 0
        for i in range(11):
            cb = 0.700 + i * 0.004
            lpp, b, t_draft = 200.0, 30.0, 9.5
            er = holtrop_etaR(cb, b, t_draft, lpp)
            if er is not None and 0.90 <= er <= 1.10:
                in_window += 1
        assert in_window >= 9, f"仅 {in_window}/11 状态 ηR 在物理窗内"

    def test_holtrop_etaR_not_constant(self):
        """Holtrop ηR 随船型参数变化。"""
        etaR1 = holtrop_etaR(cb=0.70, b=30.0, t_draft=9.5, lpp=200.0)
        etaR2 = holtrop_etaR(cb=0.74, b=30.0, t_draft=9.5, lpp=200.0)
        assert etaR1 is not None
        assert etaR2 is not None
        assert abs(etaR1 - etaR2) > 1e-6, "ηR 应随船型参数变化"

    def test_holtrop_etaR_out_of_domain(self):
        """越域时 ηR 返回 None（→ WITHHELD）。"""
        assert holtrop_etaR(cb=0.30, b=30.0, t_draft=9.5, lpp=200.0) is None
        assert holtrop_etaR(cb=0.70, b=30.0, t_draft=9.5, lpp=100.0) is None
        assert holtrop_etaR(cb=0.70, b=30.0, t_draft=20.0, lpp=200.0) is None

    def test_result_json_etaR_window(self):
        """从 result.json 验证 ≥9/11 状态 ηR 在物理窗内。"""
        result_path = os.path.join(os.path.dirname(__file__), "..", "artifacts", "selfprop", "result.json")
        if os.path.exists(result_path):
            with open(result_path) as f:
                data = json.load(f)
            in_window = sum(
                1 for pt in data["points"]
                if pt["etaR"] is not None and 0.90 <= pt["etaR"] <= 1.10
            )
            assert in_window >= 9, f"仅 {in_window}/{len(data['points'])} 状态 ηR 在物理窗内"
            assert data["etaR_variance"] > 1e-12, "ηR 方差应为正"


# ================================================================
# 3. 乘性恒等非平凡性断言（C3）
# ================================================================

class TestTrivialityAssertion:
    """ηD = ηH·ηR·η0 任一因子方差为 0 → 判「不可用于诊断」并 fail。"""

    def test_trivial_etaR_detected(self):
        """ηR 恒等 → 非平凡性断言触发。"""
        points = [
            SelfPropPoint(
                state_id=f"S{i}", rt_N=1e6, t=0.18, w=0.28,
                rpm=100.0, n_rps=100/60, j=0.6,
                kt=0.2, kq=0.03, eta0=0.55,
                etaH=1.1, etaR=1.0, etaD=0.605,
                thrust_N=1.2e6, residual=0.01,
                pass_=True, etaR_status="OK",
            )
            for i in range(5)
        ]
        is_trivial, reason = check_triviality(points)
        assert is_trivial, "ηR 恒等应触发非平凡性断言"
        assert "ηR" in reason

    def test_trivial_eta0_detected(self):
        """η0 恒等 → 非平凡性断言触发。"""
        points = [
            SelfPropPoint(
                state_id=f"S{i}", rt_N=1e6 + i * 1e5, t=0.18 + i * 0.01, w=0.28 + i * 0.01,
                rpm=100.0 + i, n_rps=(100+i)/60, j=0.6,
                kt=0.2, kq=0.03, eta0=0.55,
                etaH=1.1 + i * 0.01, etaR=1.0 + i * 0.005, etaD=0.605,
                thrust_N=1.2e6, residual=0.01,
                pass_=True, etaR_status="OK",
            )
            for i in range(5)
        ]
        is_trivial, reason = check_triviality(points)
        assert is_trivial, "η0 恒等应触发非平凡性断言"
        assert "η0" in reason

    def test_non_trivial_passes(self):
        """所有因子均变化 → 非平凡性断言不触发。"""
        points = [
            SelfPropPoint(
                state_id=f"S{i}", rt_N=1e6 + i * 1e5, t=0.18 + i * 0.01, w=0.28 + i * 0.01,
                rpm=100.0 + i * 5, n_rps=(100+i*5)/60, j=0.6 + i * 0.02,
                kt=0.2 + i * 0.01, kq=0.03 + i * 0.002,
                eta0=0.55 + i * 0.01,
                etaH=1.1 + i * 0.01, etaR=1.0 + i * 0.005,
                etaD=0.605 + i * 0.02,
                thrust_N=1.2e6 + i * 1e5, residual=0.01,
                pass_=True, etaR_status="OK",
            )
            for i in range(5)
        ]
        is_trivial, reason = check_triviality(points)
        assert not is_trivial, f"非平凡情况不应触发断言: {reason}"

    def test_result_json_triviality(self):
        """从 result.json 验证非平凡性断言通过。"""
        result_path = os.path.join(os.path.dirname(__file__), "..", "artifacts", "selfprop", "result.json")
        if os.path.exists(result_path):
            with open(result_path) as f:
                data = json.load(f)
            assert not data["triviality_fail"], f"非平凡性断言失败: {data['triviality_reason']}"


# ================================================================
# 4. 序列化与产物格式（C3/C4）
# ================================================================

class TestResultSerialization:
    """result.json 格式校验。"""

    def test_result_dict_has_required_fields(self):
        """逐条含 R_T、t、w、rpm、K_T、η0、ηH、ηR、残差、pass/fail。"""
        result_path = os.path.join(os.path.dirname(__file__), "..", "artifacts", "selfprop", "result.json")
        if os.path.exists(result_path):
            with open(result_path) as f:
                data = json.load(f)
            assert "points" in data
            assert len(data["points"]) == 11
            for pt in data["points"]:
                for key in ["rt_N", "t", "w", "rpm", "kt", "eta0", "etaH", "etaR", "residual", "pass"]:
                    assert key in pt, f"缺少字段 {key}"

    def test_result_dict_withheld_handling(self):
        """越域状态 ηR = None。"""
        cb, lpp, b, t_draft = 0.30, 200.0, 30.0, 9.5  # Cb 越域
        d = 7.0
        p = solve_selfprop_point(
            state_id="S01", rt_N=1e6, v_ms=8.0,
            d=d, cb=cb, b=b, t_draft=t_draft, lpp=lpp,
        )
        assert math.isnan(p.etaR) or p.etaR_status == "WITHHELD"
        assert p.pass_ is False


# ================================================================
# 5. ηD 恒等式校验
# ================================================================

class TestEtaDIdentity:
    """ηD = ηH·ηR·η0 数值恒等。"""

    def test_etaD_multiplicative_identity(self):
        """ηD = ηH × ηR × η0。"""
        cb, lpp, b, t_draft = 0.72, 200.0, 30.0, 9.5
        d = 7.0
        p = solve_selfprop_point(
            state_id="S01", rt_N=5e5, v_ms=8.0,
            d=d, cb=cb, b=b, t_draft=t_draft, lpp=lpp,
        )
        expected_etaD = p.etaH * p.etaR * p.eta0
        assert abs(p.etaD - expected_etaD) < 1e-10, (
            f"ηD 恒等失败: {p.etaD} vs {expected_etaD}"
        )
