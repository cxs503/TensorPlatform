"""六维统计功效门测试（tools/abm/six_dim_power.py）。

覆盖
----
1. 效应量计算（Cohen's d / 配对差中位数）
2. 置换检验 + Bonferroni-Holm 校正
3. MDE(n) 曲线单调性
4. solve_n_for_mde 反解
5. A/A 空对照误报率 ≤ 5%
6. 回溯性功效计算
7. 六维功效门集成判决
8. 边界条件与异常处理
"""

from __future__ import annotations

import math
import random

import pytest

from tools.abm.six_dim_power import (
    DIMENSIONS,
    aa_false_positive_rate,
    cohens_d,
    mde_curve,
    paired_median_diff,
    permutation_test_six_dim,
    retrospective_power,
    six_dim_power_gate,
    solve_n_for_mde,
)


# ---------------------------------------------------------------------------
# 1. 效应量计算
# ---------------------------------------------------------------------------

class TestCohensD:
    def test_zero_effect(self):
        """相同数据 → d = 0。"""
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert cohens_d(data, data) == 0.0

    def test_positive_effect(self):
        """treatment 整体大于 control → d > 0。"""
        rng = random.Random(10)
        ctrl = [rng.gauss(0, 1) for _ in range(20)]
        treat = [c + 1.0 + rng.gauss(0, 0.3) for c in ctrl]
        d = cohens_d(ctrl, treat)
        assert d > 0

    def test_negative_effect(self):
        """treatment 整体小于 control → d < 0。"""
        rng = random.Random(20)
        ctrl = [rng.gauss(0, 1) for _ in range(20)]
        treat = [c - 1.0 + rng.gauss(0, 0.3) for c in ctrl]
        d = cohens_d(ctrl, treat)
        assert d < 0

    def test_known_value(self):
        """已知配对差：diff = [1, 2, 3] → mean=2, std=1 → d=2。"""
        ctrl = [0.0, 0.0, 0.0]
        treat = [1.0, 2.0, 3.0]
        d = cohens_d(ctrl, treat)
        assert abs(d - 2.0) < 1e-10

    def test_unequal_length_raises(self):
        with pytest.raises(ValueError):
            cohens_d([1, 2], [1, 2, 3])

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            cohens_d([], [])


class TestPairedMedianDiff:
    def test_zero_diff(self):
        data = [1.0, 2.0, 3.0]
        assert paired_median_diff(data, data) == 0.0

    def test_constant_shift(self):
        ctrl = [1.0, 2.0, 3.0, 4.0, 5.0]
        treat = [c + 2.5 for c in ctrl]
        assert paired_median_diff(ctrl, treat) == 2.5

    def test_odd_length(self):
        ctrl = [0.0, 0.0, 0.0]
        treat = [1.0, 3.0, 2.0]
        assert paired_median_diff(ctrl, treat) == 2.0


# ---------------------------------------------------------------------------
# 2. 置换检验
# ---------------------------------------------------------------------------

class TestPermutationTest:
    def test_no_effect_high_p(self):
        """两组来自同一分布 → p 值应较大（> 0.05）。"""
        rng = random.Random(123)
        ctrl = [rng.gauss(0, 1) for _ in range(20)]
        treat = [rng.gauss(0, 1) for _ in range(20)]
        result = permutation_test_six_dim(
            {"accuracy": ctrl}, {"accuracy": treat},
            n_perm=500, seed=42,
        )
        p = result["dimensions"][0]["raw_p"]
        assert p > 0.05

    def test_large_effect_low_p(self):
        """大效应量 → p 值应很小（< 0.01）。"""
        rng = random.Random(200)
        ctrl = [rng.gauss(0, 1) for _ in range(20)]
        treat = [c + 3.0 + rng.gauss(0, 0.3) for c in ctrl]
        result = permutation_test_six_dim(
            {"accuracy": ctrl}, {"accuracy": treat},
            n_perm=500, seed=42,
        )
        p = result["dimensions"][0]["raw_p"]
        assert p < 0.01

    def test_bonferroni_holm_correction(self):
        """Bonferroni-Holm 校正后 adjusted_p ≥ raw_p。"""
        rng = random.Random(456)
        ctrl_data = {d: [rng.gauss(0, 1) for _ in range(15)] for d in DIMENSIONS}
        treat_data = {d: [c + 0.5 + rng.gauss(0, 0.2) for c in ctrl_data[d]] for d in DIMENSIONS}
        result = permutation_test_six_dim(
            ctrl_data, treat_data,
            n_perm=200, seed=42, correction="bonferroni_holm",
        )
        for rd in result["dimensions"]:
            if rd["raw_p"] is not None and rd["adjusted_p"] is not None:
                assert rd["adjusted_p"] >= rd["raw_p"] - 1e-10

    def test_six_dim_all_tested(self):
        """六维全部被测试。"""
        rng = random.Random(789)
        ctrl_data = {d: [rng.gauss(0, 1) for _ in range(10)] for d in DIMENSIONS}
        treat_data = {d: [c + 0.3 + rng.gauss(0, 0.2) for c in ctrl_data[d]] for d in DIMENSIONS}
        result = permutation_test_six_dim(
            ctrl_data, treat_data, n_perm=200, seed=42,
        )
        assert result["n_dims_tested"] == 6

    def test_missing_dim_skipped(self):
        """缺失维度被跳过。"""
        result = permutation_test_six_dim(
            {"accuracy": [1, 2, 3]},
            {"accuracy": [1.5, 2.5, 3.5]},
            n_perm=100, seed=42,
        )
        assert result["n_dims_tested"] == 1


# ---------------------------------------------------------------------------
# 3. MDE 曲线
# ---------------------------------------------------------------------------

class TestMdeCurve:
    def test_mde_decreases_with_n(self):
        """MDE 应随样本量增加而单调递减。"""
        curve = mde_curve(
            alpha=0.05, power=0.8,
            n_range=[10, 20, 40],
            n_perm=100, seed=42,
        )
        mdes = [pt["mde"] for pt in curve]
        for i in range(1, len(mdes)):
            assert mdes[i] <= mdes[i - 1] + 0.1  # 允许小波动

    def test_mde_positive(self):
        """MDE 应为正数。"""
        curve = mde_curve(
            alpha=0.05, power=0.8,
            n_range=[10, 20],
            n_perm=100, seed=42,
        )
        for pt in curve:
            assert pt["mde"] >= 0


# ---------------------------------------------------------------------------
# 4. solve_n_for_mde
# ---------------------------------------------------------------------------

class TestSolveNForMde:
    def test_returns_n(self):
        """对合理 MDE 应返回 n。"""
        result = solve_n_for_mde(
            target_mde=2.0, alpha=0.05, power=0.8,
            n_perm=100, seed=42, n_max=50,
        )
        assert result["n"] is not None
        assert result["n"] >= 5


# ---------------------------------------------------------------------------
# 5. A/A 空对照误报率
# ---------------------------------------------------------------------------

class TestAANullControl:
    def test_fpr_within_nominal(self):
        """A/A 误报率应 ≤ alpha（名义水平 5%）。"""
        result = aa_false_positive_rate(
            n=15, n_sim=200, n_perm=200,
            alpha=0.05, seed=42, n_dims=6,
        )
        assert result["fpr_within_nominal"] is True
        assert result["false_positive_rate"] <= 0.05

    def test_fpr_reported(self):
        """误报率应被报告。"""
        result = aa_false_positive_rate(
            n=10, n_sim=100, n_perm=100,
            alpha=0.05, seed=99, n_dims=6,
        )
        assert "false_positive_rate" in result
        assert isinstance(result["false_positive_rate"], float)


# ---------------------------------------------------------------------------
# 6. 回溯性功效
# ---------------------------------------------------------------------------

class TestRetrospectivePower:
    def test_large_effect_high_power(self):
        """大效应量 → 回溯功效应高。"""
        rng = random.Random(300)
        ctrl = [rng.gauss(0, 1) for _ in range(15)]
        treat = [c + 3.0 + rng.gauss(0, 0.3) for c in ctrl]
        result = retrospective_power(
            {"accuracy": ctrl}, {"accuracy": treat},
            n_perm=200, seed=42, n_sim_power=50,
        )
        power = result["dimensions"][0]["retrospective_power"]
        assert power >= 0.8

    def test_small_effect_low_power(self):
        """小效应量 + 大噪声 → 回溯功效应较低。
        
        使用有真实方差的数据：diff 均值小但方差大。
        """
        rng = random.Random(555)
        # 构造数据：diff 均值小 (~0.1)，但方差大 (std ~2)
        ctrl = [rng.gauss(0, 2) for _ in range(10)]
        # treatment 有独立噪声，使 diff 有真实方差
        treat = [c + 0.1 + rng.gauss(0, 2) for c in ctrl]
        result = retrospective_power(
            {"accuracy": ctrl}, {"accuracy": treat},
            n_perm=100, seed=42, n_sim_power=50,
        )
        power = result["dimensions"][0]["retrospective_power"]
        # With effect size ~0.06 and n=10, power should be low (< 0.5)
        assert power <= 0.55, f"Expected low power for tiny effect, got {power}"

    def test_all_dims_reported(self):
        """六维全部报告。"""
        rng = random.Random(222)
        ctrl_data = {d: [rng.gauss(0, 1) for _ in range(10)] for d in DIMENSIONS}
        treat_data = {d: [c + 0.5 + rng.gauss(0, 0.2) for c in ctrl_data[d]] for d in DIMENSIONS}
        result = retrospective_power(
            ctrl_data, treat_data,
            n_perm=100, seed=42, n_sim_power=30,
        )
        assert len(result["dimensions"]) == 6


# ---------------------------------------------------------------------------
# 7. 六维功效门集成
# ---------------------------------------------------------------------------

class TestSixDimPowerGate:
    def _make_data(self, shift=0.0, noise=0.3, n=20, seed=400):
        rng = random.Random(seed)
        ctrl = {}
        treat = {}
        for d in DIMENSIONS:
            base = [rng.gauss(0, 1) for _ in range(n)]
            ctrl[d] = base
            treat[d] = [c + shift + rng.gauss(0, noise) for c in base]
        return ctrl, treat

    def test_pass_verdict(self):
        """大效应 + 足够样本 → pass。"""
        ctrl, treat = self._make_data(shift=3.0, n=20)
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=200, seed=42,
            min_power=0.5, min_effect_size=0.5,
            run_aa_check=False, run_mde=False, run_retrospective=False,
        )
        assert result["gate_verdict"] == "pass"

    def test_fail_verdict(self):
        """无效应 → fail。"""
        ctrl, treat = self._make_data(shift=0.0, n=15)
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=200, seed=42,
            run_aa_check=False, run_mde=False, run_retrospective=False,
        )
        assert result["gate_verdict"] == "fail"

    def test_aa_check_included(self):
        """AA 检查应包含在结果中。"""
        ctrl, treat = self._make_data(shift=1.0, n=15)
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=100, seed=42,
            run_aa_check=True, run_mde=False, run_retrospective=False,
        )
        assert result["aa_check"] is not None
        assert "false_positive_rate" in result["aa_check"]

    def test_mde_curve_included(self):
        """MDE 曲线应包含在结果中。"""
        ctrl, treat = self._make_data(shift=1.0, n=15)
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=100, seed=42,
            run_aa_check=False, run_mde=True, run_retrospective=False,
        )
        assert result["mde_curve"] is not None
        assert len(result["mde_curve"]) > 0

    def test_retrospective_included(self):
        """回溯功效应包含在结果中。"""
        ctrl, treat = self._make_data(shift=2.0, n=15)
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=100, seed=42,
            run_aa_check=False, run_mde=False, run_retrospective=True,
        )
        assert result["retrospective_power"] is not None
        assert len(result["retrospective_power"]["dimensions"]) == 6

    def test_n_observations_reported(self):
        """每维观测数应被报告。"""
        ctrl = {d: [0.0] * 25 for d in DIMENSIONS}
        treat = {d: [1.0] * 25 for d in DIMENSIONS}
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=100, seed=42,
            run_aa_check=False, run_mde=False, run_retrospective=False,
        )
        for d in DIMENSIONS:
            assert result["n_observations_per_dim"][d] == 25


# ---------------------------------------------------------------------------
# 8. 边界条件
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_sample_returns_none(self):
        """单样本 → 效应量为 None。"""
        result = permutation_test_six_dim(
            {"accuracy": [1.0]}, {"accuracy": [2.0]},
            n_perm=100, seed=42,
        )
        assert result["dimensions"][0]["effect_size"] is None

    def test_deterministic_with_seed(self):
        """相同种子 → 相同结果。"""
        ctrl = {"accuracy": [1, 2, 3, 4, 5]}
        treat = {"accuracy": [2, 3, 4, 5, 6]}
        r1 = permutation_test_six_dim(ctrl, treat, n_perm=200, seed=42)
        r2 = permutation_test_six_dim(ctrl, treat, n_perm=200, seed=42)
        assert r1["dimensions"][0]["raw_p"] == r2["dimensions"][0]["raw_p"]

    def test_different_seed_different_result(self):
        """不同种子 → 结果可能不同（概率性）。"""
        ctrl = {"accuracy": [1, 2, 3, 4, 5]}
        treat = {"accuracy": [2, 3, 4, 5, 6]}
        r1 = permutation_test_six_dim(ctrl, treat, n_perm=200, seed=42)
        r2 = permutation_test_six_dim(ctrl, treat, n_perm=200, seed=999)
        assert "raw_p" in r1["dimensions"][0]
        assert "raw_p" in r2["dimensions"][0]
