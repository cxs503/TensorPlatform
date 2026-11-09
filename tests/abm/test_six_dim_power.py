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
    DIMENSIONS,
    aa_false_positive_rate,
    analytic_mde,
    analytic_power_paired,
    cohens_d,
    mde_curve,
    min_n_for_effect,
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
