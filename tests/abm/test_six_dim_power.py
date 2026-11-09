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
9. m8646 回归：MDE 塌陷 / 回溯功效膨胀 / 跨进程可复现 / 门校准与欠功效 / 零方差退化
"""

from __future__ import annotations

import math
import os
import random
import subprocess
import sys

import pytest

from tools.abm.six_dim_power import (
    DIMENSION_DIRECTION,
    DIMENSION_NOTE,
    DIMENSIONS,
    MIN_TESTABLE_PAIRS,
    aa_false_positive_rate,
    aa_null_control,
    analytic_mde,
    analytic_power_paired,
    binom_ci,
    build_pairs,
    cohens_d,
    family_decision_power_curve,
    holm_adjust,
    mde_coefficient,
    mde_curve,
    min_n_for_effect,
    min_perm_for_holm,
    null_family_sim,
    oriented_deltas,
    paired_effect,
    paired_median_diff,
    permutation_test_six_dim,
    power_positive_control,
    power_vs_rho,
    required_n_for_family_decision,
    retrospective_power,
    sign_flip_permutation_test,
    six_dim_advantage,
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
        """无效应且样本量充足（n=60 ≥ 检出 d=0.5 所需 n）→ fail（证否）。

        m8646 语义修正：修复前 n=15 的无效应数据一律判 fail，等于把
        "检验没本事看出 0.5 的效应" 说成 "没有效应"。
        """
        ctrl, treat = self._make_data(shift=0.0, n=60)
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=200, seed=42,
            run_aa_check=False, run_mde=False, run_retrospective=False,
        )
        assert result["sample_underpowered"] is False
        assert result["gate_verdict"] == "fail"

    def test_underpowered_no_effect_is_inconclusive(self):
        """无效应 + 样本量不足 → inconclusive（证据不足 ≠ 证否）。"""
        ctrl, treat = self._make_data(shift=0.0, n=8)
        result = six_dim_power_gate(
            ctrl, treat,
            alpha=0.05, n_perm=200, seed=42,
            run_aa_check=False, run_mde=False, run_retrospective=False,
        )
        assert result["sample_underpowered"] is True
        assert result["gate_verdict"] == "inconclusive"
        assert "证据不足" in result["gate_reason"]

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


# ---------------------------------------------------------------------------
# 9. m8646 回归：五处实质缺陷（每条都能在修复前跑红）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mde_small_grid():
    """MDE(n) 小网格（模块级缓存：一次 ~8 s，三条回归共用）。"""
    return mde_curve(alpha=0.05, power=0.8, n_range=[6, 20, 100], n_perm=300, seed=42)


class TestMdeNotCollapsed:
    """缺陷 1：`_estimate_power` 造出的配对差没有方差（d=inf），
    MDE(n) 曲线整体塌到搜索下界 —— n=6 与 n=200 都报 ~0.01，与解析 MDE 差两个数量级。"""

    def test_mde_tracks_sample_size(self, mde_small_grid):
        curve = mde_small_grid
        m = {pt["n"]: pt["mde"] for pt in curve}
        assert all(v is not None for v in m.values()), m
        assert m[6] > 1.0, f"n=6 的 MDE 应 >1.0，实得 {m[6]}（修复前 ~0.01）"
        assert m[20] > 0.5, m
        assert m[100] < 0.45, m

    def test_mde_monotone_decreasing(self, mde_small_grid):
        m = [pt["mde"] for pt in mde_small_grid]
        assert all(m[i] <= m[i - 1] for i in range(1, len(m))), m

    def test_mde_within_15pct_of_analytic(self, mde_small_grid):
        errs = {
            pt["n"]: abs(pt["mde"] - pt["mde_analytic"]) / pt["mde_analytic"]
            for pt in mde_small_grid
            if pt["n"] >= 10
        }
        for n, e in errs.items():
            assert e <= 0.15, f"n={n} 相对解析 MDE 误差 {e:.1%} > 15%"


class TestRetrospectivePowerCalibrated:
    """缺陷 2：回溯功效模拟把噪声写成 std_diff*0.1，模拟 diff 的 std 只有观测的 1/10，
    功效被系统性高估（实测把 0.53 的真值报成 1.0）。"""

    @staticmethod
    def _paired(seed=1234, n=12, mean=0.45, sd=1.0):
        rng = random.Random(seed)
        ctrl = [rng.gauss(0.0, 1.0) for _ in range(n)]
        treat = [c + rng.gauss(mean, sd) for c in ctrl]
        return ctrl, treat

    def test_no_longer_saturates(self):
        ctrl, treat = self._paired()
        power = retrospective_power(
            {"accuracy": ctrl}, {"accuracy": treat},
            n_perm=200, seed=42, n_sim_power=200,
        )["dimensions"][0]["retrospective_power"]
        assert power < 0.9, f"n=12、小效应下的回溯功效不应饱和，实得 {power}（修复前 1.0）"

    def test_matches_direct_resampling(self):
        """与"按观测 diff 分布直接重抽"的独立实现对比，差距 ≤ 0.12。"""
        ctrl, treat = self._paired()
        n = len(ctrl)
        result = retrospective_power(
            {"accuracy": ctrl}, {"accuracy": treat},
            n_perm=200, seed=42, n_sim_power=200,
        )["dimensions"][0]
        diffs = [t - c for c, t in zip(ctrl, treat)]
        m = sum(diffs) / n
        sd = math.sqrt(sum((d - m) ** 2 for d in diffs) / (n - 1))
        rng = random.Random(424242)
        n_rej = 0
        n_sim = 200
        for sim in range(n_sim):
            base = [rng.gauss(0.0, 1.0) for _ in range(n)]
            tr = [c + rng.gauss(m, sd) for c in base]
            from tools.abm.six_dim_power import _permutation_p_value
            n_rej += int(_permutation_p_value(base, tr, n_perm=200, seed=42 + sim) < 0.05)
        truth = n_rej / n_sim
        assert abs(result["retrospective_power"] - truth) <= 0.12, (result, truth)
        # 同时给解析功效对照
        assert abs(result["analytic_power_same_effect"] - truth) <= 0.2, (result, truth)

    def test_cross_process_reproducible(self):
        """跨进程可复现：子种子不能用内建 hash()（受 PYTHONHASHSEED 随机化）。"""
        code = (
            "import random\n"
            "from tools.abm.six_dim_power import retrospective_power\n"
            "rng = random.Random(10)\n"
            "n = 12\n"
            "c = [rng.gauss(0, 1) for _ in range(n)]\n"
            "t = [x + rng.gauss(0.05, 1.0) for x in c]\n"
            "r = retrospective_power({'accuracy': c, 'stability': c},"
            " {'accuracy': t, 'stability': t}, n_perm=200, seed=42, n_sim_power=120)\n"
            "print([d['retrospective_power'] for d in r['dimensions']])\n"
        )
        outs = []
        for hs in ("0", "1"):
            env = dict(os.environ, PYTHONHASHSEED=hs)
            out = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, env=env,
                cwd=os.getcwd(), check=True,
            ).stdout.strip()
            outs.append(out)
        assert outs[0] == outs[1], f"跨进程结果不一致：{outs}"


class TestGateCalibration:
    """缺陷 3：aa_check 只落库不参与判决 —— A/A 校准失控时仍给出 pass/fail。"""

    def test_broken_aa_blocks_verdict(self, monkeypatch):
        rng = random.Random(400)
        ctrl, treat = {}, {}
        for d in DIMENSIONS:
            base = [rng.gauss(0, 1) for _ in range(15)]
            ctrl[d] = base
            treat[d] = [c + rng.gauss(0, 0.3) for c in base]

        import tools.abm.six_dim_power as sdp

        def _broken(**_kw):
            return {
                "false_positive_rate": 0.5, "nominal_alpha": 0.05,
                "fpr_within_nominal": False, "n_sim": 10, "n_perm": 100,
                "n": 20, "n_dims": 6, "total_tests": 60, "n_reject": 30, "seed": 42,
            }

        monkeypatch.setattr(sdp, "aa_false_positive_rate", _broken)
        res = sdp.six_dim_power_gate(
            ctrl, treat, n_perm=200, seed=42,
            run_aa_check=True, run_mde=False, run_retrospective=False,
        )
        assert res["calibration_ok"] is False
        assert res["gate_verdict"] == "fail"
        assert "校准失控" in res["gate_reason"]


class TestZeroVarianceDegeneracy:
    """退化行为护栏：配对差为 0 方差（常值偏移）时 Cohen's d=inf。

    这里的显式分支是防御性改动（实测基线与新分支 p 值一致，都触到
    1/(n_perm+1) 下限），目标是让 p 值由符号排列结构决定，
    而不是依赖 inf >= inf 的浮点比较。测试用于锁定该行为不被后续重构破坏。
    """

    def test_constant_shift_is_significant(self):
        res = permutation_test_six_dim(
            {"accuracy": [0.0] * 25}, {"accuracy": [1.0] * 25},
            n_perm=2000, seed=42,
        )
        d = res["dimensions"][0]
        assert d["significant"] is True, d
        assert d["raw_p"] < 0.01, d

    def test_normal_case_unaffected(self):
        rng = random.Random(11)
        c = [rng.gauss(0, 1) for _ in range(20)]
        t = [x + rng.gauss(0.1, 1.0) for x in c]
        res = permutation_test_six_dim({"accuracy": c}, {"accuracy": t}, n_perm=1000, seed=42)
        assert res["dimensions"][0]["raw_p"] > 0.05


class TestAnalyticHelpers:
    """解析对照（独立于置换模拟）自身的性质。"""

    def test_power_monotone_in_n(self):
        ps = [analytic_power_paired(0.5, n) for n in (6, 10, 20, 40, 80)]
        assert all(ps[i] > ps[i - 1] for i in range(1, len(ps))), ps

    def test_mde_power_round_trip(self):
        for n in (10, 20, 50):
            d = analytic_mde(n, alpha=0.05, power=0.8)
            assert abs(analytic_power_paired(d, n, alpha=0.05) - 0.8) < 0.02

    def test_min_n_for_effect_consistent(self):
        n_req = min_n_for_effect(0.5, alpha=0.05, power=0.8)
        assert n_req is not None
        assert analytic_power_paired(0.5, n_req) >= 0.8
        assert analytic_power_paired(0.5, n_req - 1) < 0.8

    def test_analytic_close_to_simulated(self):
        """解析功效 vs 置换模拟功效（同一 n、同一 d）误差 ≤ 0.15。"""
        from tools.abm.six_dim_power import _estimate_power
        sim = _estimate_power(0.5, 20, 0.05, 400, 42, cohens_d, 1.0, n_rep=200)
        ana = analytic_power_paired(0.5, 20, alpha=0.05)
        assert abs(sim - ana) <= 0.15, (sim, ana)


# ---------------------------------------------------------------------------
# 10. 方向语义 / 溯源配对 / 族判决（G25）/ 设计期功效
# ---------------------------------------------------------------------------


def _mk_records(deltas_by_dim, *, n=None, lineage="L1", source="工件#L1"):
    """构造记录：``deltas_by_dim`` = {dim: delta}（正 = 实验组更好，已按方向口径给）。"""
    recs = []
    n = n if n is not None else len(next(iter(deltas_by_dim.values())))
    for dim, deltas in deltas_by_dim.items():
        sgn = DIMENSION_DIRECTION[dim]
        for i, delta in enumerate(deltas):
            base = 1.0
            recs.append({"lineage": lineage, "seat": f"S{i}", "arm": "control",
                         "dimension": dim, "value": base, "unit": "u", "source": source})
            # 存储值按方向反推：value_t - value_c = sgn * delta
            recs.append({"lineage": lineage, "seat": f"S{i}", "arm": "treatment",
                         "dimension": dim, "value": base + sgn * delta,
                         "unit": "u", "source": source + "#t"})
    return recs


class TestDimensionDirection:
    def test_direction_and_note_cover_all_dims(self):
        assert set(DIMENSION_DIRECTION) == set(DIMENSIONS) == set(DIMENSION_NOTE)
        assert set(DIMENSION_DIRECTION.values()) <= {+1, -1}
        # 三个「越小越好」的度量必须编码为 -1，否则效应方向整体反号
        for dim in ("accuracy", "efficiency", "reproducibility"):
            assert DIMENSION_DIRECTION[dim] == -1
        for dim in ("correctness", "stability", "applicability"):
            assert DIMENSION_DIRECTION[dim] == +1

    def test_oriented_deltas_flip_lower_is_better(self):
        pair = [{"v_control": 0.30, "v_treatment": 0.10}]
        # accuracy 越小越好：treatment 0.10 优于 0.30 → 方向化后 delta 为正
        assert oriented_deltas(pair, "accuracy") == [pytest.approx(0.20)]
        # correctness 越大越好：同样的数值差 → delta 为负
        assert oriented_deltas(pair, "correctness") == [pytest.approx(-0.20)]

    def test_oriented_deltas_unknown_dim_raises(self):
        with pytest.raises(ValueError):
            oriented_deltas([], "no_such_dim")


class TestBuildPairs:
    def test_pairs_require_both_arms_same_unit_and_source(self):
        recs = [
            {"lineage": "L", "seat": "a", "arm": "control", "dimension": "accuracy",
             "value": 1.0, "unit": "rel", "source": "s"},
            {"lineage": "L", "seat": "a", "arm": "treatment", "dimension": "accuracy",
             "value": 0.5, "unit": "rel", "source": "t"},
            # 缺 source → 不成对
            {"lineage": "L", "seat": "b", "arm": "control", "dimension": "accuracy",
             "value": 1.0, "unit": "rel"},
            {"lineage": "L", "seat": "b", "arm": "treatment", "dimension": "accuracy",
             "value": 0.5, "unit": "rel", "source": "t"},
            # 单位不一致 → 不成对
            {"lineage": "L", "seat": "c", "arm": "control", "dimension": "accuracy",
             "value": 1.0, "unit": "rel", "source": "s"},
            {"lineage": "L", "seat": "c", "arm": "treatment", "dimension": "accuracy",
             "value": 0.5, "unit": "pct", "source": "t"},
            # 重复记录 → 报错且不入账
            {"lineage": "L", "seat": "a", "arm": "control", "dimension": "accuracy",
             "value": 2.0, "unit": "rel", "source": "s2"},
        ]
        folded = build_pairs(recs)
        assert folded["n_pairs_by_dim"]["accuracy"] == 1
        assert folded["pairs"]["accuracy"][0]["seat"] == "a"
        assert folded["pairs"]["accuracy"][0]["sources"] == {
            "control": "s", "treatment": "t"}
        # 三类错误都要被记录（缺 source / 单位不一致 / 重复）
        assert any("缺 source" in e for e in folded["errors"])
        assert any("单位不一致" in e for e in folded["errors"])
        assert any("重复记录" in e for e in folded["errors"])

    def test_unknown_dim_and_non_numeric_rejected(self):
        folded = build_pairs([
            {"lineage": "L", "seat": "a", "arm": "control", "dimension": "bogus",
             "value": 1.0, "unit": "u", "source": "s"},
            {"lineage": "L", "seat": "a", "arm": "control", "dimension": "accuracy",
             "value": "1.0", "unit": "u", "source": "s"},
        ])
        assert len(folded["errors"]) == 2
        assert folded["n_pairs_by_dim"]["accuracy"] == 0

    def test_pairing_key_is_lineage_and_seat(self):
        recs = []
        for lineage in ("L1", "L2"):
            for i in range(2):
                recs.append({"lineage": lineage, "seat": f"S{i}", "arm": "control",
                             "dimension": "stability", "value": 1.0, "unit": "u", "source": "s"})
                recs.append({"lineage": lineage, "seat": f"S{i}", "arm": "treatment",
                             "dimension": "stability", "value": 1.2, "unit": "u", "source": "t"})
        folded = build_pairs(recs)
        assert folded["n_pairs_by_dim"]["stability"] == 4
        assert {p["lineage"] for p in folded["pairs"]["stability"]} == {"L1", "L2"}


class TestPairedEffect:
    def test_known_values(self):
        eff = paired_effect([1.0, 2.0, 3.0])
        assert eff["n"] == 3
        assert eff["mean_diff"] == pytest.approx(2.0)
        assert eff["median_diff"] == pytest.approx(2.0)
        assert eff["sd"] == pytest.approx(1.0)
        assert eff["cohen_dz"] == pytest.approx(2.0)
        lo, hi = eff["ci95_mean"]
        assert lo < eff["mean_diff"] < hi

    def test_median_ci_and_constant_deltas(self):
        eff = paired_effect([1.0, 2.0, 3.0, 4.0, 5.0])
        lo, hi = eff["ci95_median"]
        assert lo <= eff["median_diff"] <= hi
        const = paired_effect([0.5] * 6)
        assert const["sd"] == 0.0
        assert const["cohen_dz"] == float("inf")  # 零方差 → 如实标 inf，不伪造有限值
        empty = paired_effect([])
        assert empty["n"] == 0 and empty["cohen_dz"] is None


class TestSignFlipPermutation:
    def test_large_effect_is_significant_and_deterministic(self):
        deltas = [0.8 + 0.05 * ((-1) ** i) * (i % 3) for i in range(12)]
        a = sign_flip_permutation_test(deltas, n_perm=999, seed=11, alternative="greater")
        b = sign_flip_permutation_test(deltas, n_perm=999, seed=11, alternative="greater")
        assert a == b
        assert a["p_value"] < 0.01
        assert a["p_value"] >= 1.0 / (1 + 999)

    def test_zero_effect_not_significant(self):
        deltas = [0.3, -0.3, 0.2, -0.2, 0.1, -0.1, 0.05, -0.05, 0.01, -0.01]
        r = sign_flip_permutation_test(deltas, n_perm=999, seed=5, alternative="two-sided")
        assert r["p_value"] > 0.05

    def test_alternative_direction_matters(self):
        deltas = [0.4, 0.5, 0.45, 0.55, 0.42, 0.48, 0.51, 0.47, 0.44, 0.53]
        greater = sign_flip_permutation_test(deltas, n_perm=499, seed=2, alternative="greater")
        less = sign_flip_permutation_test(deltas, n_perm=499, seed=2, alternative="less")
        assert greater["p_value"] < 0.01
        assert less["p_value"] > 0.99

    def test_degenerate_inputs_raise(self):
        with pytest.raises(ValueError):
            sign_flip_permutation_test([1.0], n_perm=10, seed=0)
        with pytest.raises(ValueError):
            sign_flip_permutation_test([1.0, 2.0], n_perm=0, seed=0)
        with pytest.raises(ValueError):
            sign_flip_permutation_test([1.0, 2.0], n_perm=10, seed=0, alternative="bogus")


class TestHolmAdjust:
    def test_step_down_rejects_then_stops(self):
        h = holm_adjust({"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04}, alpha=0.05)
        # 4*0.01 = 0.04 ≤ 0.05 → 拒绝；3*0.02 = 0.06 > 0.05 → 其后一律不拒绝
        assert h["reject"]["a"] is True
        assert h["reject"]["b"] is False and h["reject"]["c"] is False and h["reject"]["d"] is False
        assert h["n_reject"] == 1

    def test_adjusted_monotone_and_not_below_raw(self):
        pv = {"a": 0.005, "b": 0.013, "c": 0.04, "d": 0.2, "e": 0.5}
        h = holm_adjust(pv, alpha=0.05)
        ordered = [s["p_adj"] for s in h["steps"]]
        assert ordered == sorted(ordered)
        for dim, p in pv.items():
            assert h["adjusted"][dim] >= p - 1e-12
            assert h["adjusted"][dim] <= 1.0

    def test_monte_carlo_resolution_floor(self):
        # Holm 下最小可达 p = 1/(1+B) 必须 ≤ alpha/m；B=50 时族判决在数学上不可达
        assert min_perm_for_holm(0.05, 6) == 119
        assert min_perm_for_holm(0.05, 1) == 19
        assert min_perm_for_holm(0.01, 2) == 199


class TestSixDimAdvantage:
    def test_two_real_improvements_pass_gate(self):
        deltas = {d: [0.0] * 12 for d in DIMENSIONS}
        deltas["accuracy"] = [0.05 + 0.002 * (i % 3) for i in range(12)]
        deltas["efficiency"] = [0.10 + 0.004 * (i % 4) for i in range(12)]
        res = six_dim_advantage(_mk_records(deltas), n_perm=999, seed=3, min_dims=2)
        assert res["decision_pass"] is True
        assert res["n_significant_better"] >= 2
        assert res["dimensions"]["accuracy"]["direction"] == "treatment_better"
        assert res["dimensions"]["accuracy"]["significant_better"] is True
        # 无差异的维不应被判为显著更好
        assert res["dimensions"]["correctness"]["significant_better"] is False
        assert res["dimensions"]["correctness"]["direction"] == "indistinguishable"

    def test_no_data_does_not_pass(self):
        res = six_dim_advantage([], n_perm=199, seed=0)
        assert res["decision_pass"] is False
        assert res["n_significant_better"] == 0
        assert all(v["p_perm"] is None for v in res["dimensions"].values())

    def test_worse_treatment_is_not_better(self):
        deltas = {d: [0.0] * 12 for d in DIMENSIONS}
        deltas["correctness"] = [-0.2 - 0.01 * (i % 3) for i in range(12)]  # 实验组更差
        res = six_dim_advantage(_mk_records(deltas), n_perm=999, seed=4, min_dims=1)
        assert res["dimensions"]["correctness"]["significant_better"] is False
        assert res["dimensions"]["correctness"]["direction"] == "treatment_worse"
        assert res["decision_pass"] is False

    def test_resolution_flag_when_n_perm_too_small(self):
        recs = _mk_records({d: [0.5] * 10 for d in DIMENSIONS})
        res = six_dim_advantage(recs, n_perm=50, seed=0)
        assert res["n_perm_resolution_ok"] is False
        assert res["min_perm_for_holm"] == 119

    def test_sources_are_auditable(self):
        recs = _mk_records({d: [0.3] * 10 for d in DIMENSIONS})
        res = six_dim_advantage(recs, n_perm=199, seed=1)
        assert res["errors"] == []
        assert res["n_pairs_by_dim"]["accuracy"] == 10


class TestNullFamilyFWER:
    def test_six_dim_fwer_controlled(self):
        r = null_family_sim(n_families=40, n_pairs=12, n_perm=199, min_dims=1, seed=7)
        assert r["family_any"]["p_hat"] <= 0.10, r["family_any"]
        assert r["per_dim_max_rate"] <= 0.10
        assert r["n_perm_resolution_ok"] is True
        assert set(r["per_dim"]) == set(DIMENSIONS)
        assert r["family_all"]["p_hat"] == 0.0

    def test_aa_null_control_structure(self):
        r = aa_null_control(n_sims=15, n_pairs=12, n_perm=149, min_dims=2,
                            seed=1, stability_seeds=(0, 1))
        assert r["pass"] is True
        assert r["pooled"]["n_families"] == 30
        assert r["ci_upper_family"] >= r["primary"]["family_ge_min_dims"]["p_hat"]
        assert r["ci_upper_per_dim_pooled"] >= 0.0


class TestDesignPower:
    def test_power_positive_control_detects_known_effect(self):
        r = power_positive_control(d=0.5, n_pairs=20, rho=0.7, n_sims=150,
                                   n_perm=299, seed=5)
        assert r["detection_rate"] >= 0.75, r
        assert 0.5 < r["rho_realized"] < 0.9
        assert r["detection_ci"]["lo"] <= r["detection_rate"] <= r["detection_ci"]["hi"]
        # 与解析功效同量级（±0.15）
        assert abs(r["detection_rate"] - r["analytic_power_normal_t"]) <= 0.15

    def test_power_increases_with_pairing_correlation(self):
        r = power_vs_rho(n_pairs=20, n_sims=100, n_perm=199,
                         rho_grid=(0.0, 0.5, 0.8), seed=3)
        powers = [row["power_greater"] for row in r["rows"]]
        assert powers == sorted(powers)
        assert powers[-1] >= 0.85
        assert powers[-1] > powers[0]

    def test_family_decision_power_curve_and_n(self):
        curve = family_decision_power_curve(n_grid=(9, 20, 40), n_rep=25, n_perm=199, seed=2)
        vals = [row["power_ge_min_dims"] for row in curve["rows"]]
        assert vals == sorted(vals)
        assert vals[0] == 0.0 and vals[-1] >= 0.8
        assert curve["n_perm_resolution_ok"] is True
        req = required_n_for_family_decision(n_grid=(9, 20, 40), n_rep=25, n_perm=199, seed=2)
        assert req["required_n_grid_hit"] in (20, 40)

    def test_family_curve_flags_insufficient_perm(self):
        curve = family_decision_power_curve(n_grid=(9,), n_rep=10, n_perm=50, seed=0)
        assert curve["n_perm_resolution_ok"] is False


class TestBinomialCI:
    def test_known_interval(self):
        ci = binom_ci(0, 20)
        assert ci["p_hat"] == 0.0
        assert ci["lo"] == 0.0
        assert 0.0 < ci["hi"] < 0.25
        mid = binom_ci(10, 20)
        assert mid["lo"] < 0.5 < mid["hi"]
        assert mid["method"] in ("clopper-pearson", "wilson")

    def test_degenerate(self):
        ci = binom_ci(0, 0)
        assert ci["method"] == "n/a"


class TestMdeCoefficient:
    def test_matches_published_constants(self):
        assert mde_coefficient(0.05, 0.8, two_sided=True) == pytest.approx(2.8016, abs=2e-4)
        assert mde_coefficient(0.05, 0.8, two_sided=False) == pytest.approx(2.4865, abs=2e-4)
