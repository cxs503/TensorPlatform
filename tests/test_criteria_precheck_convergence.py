#!/usr/bin/env python3
"""E3 收敛阶判定（criteria_precheck.convergence）。

判据先于实现，机器可裁：
1. p=0 数据必须判 SUSPECT；
2. p=2 数据必须判 PRE-FEASIBLE；
3. 误判率 ≤ 10%。
"""

from __future__ import annotations

import math
import random

import pytest

from backend.criteria_precheck.convergence import (
    ConvergenceResult,
    estimate_order,
    judge_convergence,
)


class TestConvergenceOrder:
    """E3: 收敛阶判定验证。"""

    def test_p0_is_suspect(self):
        """p≈0 的数据必须判 SUSPECT。"""
        # 常数误差（不收敛）
        h_values = [1.0, 0.5, 0.25, 0.125, 0.0625]
        e_values = [0.1, 0.1, 0.1, 0.1, 0.1]

        result = judge_convergence(h_values, e_values)
        assert result.verdict == "SUSPECT", f"Expected SUSPECT, got {result.verdict} (p={result.order})"

    def test_p2_is_pre_feasible(self):
        """p≈2 的数据必须判 PRE-FEASIBLE。"""
        # 二阶收敛：e ∝ h²
        h_values = [1.0, 0.5, 0.25, 0.125, 0.0625]
        e_values = [h ** 2 for h in h_values]

        result = judge_convergence(h_values, e_values)
        assert result.verdict == "PRE-FEASIBLE", f"Expected PRE-FEASIBLE, got {result.verdict} (p={result.order})"
        assert abs(result.order - 2.0) < 0.1, f"Expected p≈2, got p={result.order}"

    def test_p1_is_feasible(self):
        """p≈1 的数据判 FEASIBLE。"""
        h_values = [1.0, 0.5, 0.25, 0.125, 0.0625]
        e_values = [h for h in h_values]

        result = judge_convergence(h_values, e_values)
        assert result.verdict == "FEASIBLE", f"Expected FEASIBLE, got {result.verdict} (p={result.order})"
        assert abs(result.order - 1.0) < 0.1, f"Expected p≈1, got p={result.order}"

    def test_misjudgment_rate_under_10_percent(self):
        """误判率 ≤ 10%：对已知收敛阶的数据集做批量判定。"""
        rng = random.Random(42)
        n_cases = 50
        misjudged = 0

        for i in range(n_cases):
            # 生成带噪声的收敛数据
            target_p = [0.0, 1.0, 2.0][i % 3]
            h_values = [1.0 / (2 ** j) for j in range(6)]
            noise = [rng.uniform(0.9, 1.1) for _ in h_values]
            e_values = [(h ** target_p) * n for h, n in zip(h_values, noise)]

            result = judge_convergence(h_values, e_values)

            expected = "SUSPECT" if target_p < 0.3 else ("PRE-FEASIBLE" if target_p >= 1.5 else "FEASIBLE")
            if result.verdict != expected:
                misjudged += 1

        rate = misjudged / n_cases
        assert rate <= 0.10, f"Misjudgment rate {rate:.2%} > 10% ({misjudged}/{n_cases})"

    def test_divergent_is_suspect(self):
        """发散（p<0）判 SUSPECT。"""
        h_values = [1.0, 0.5, 0.25, 0.125]
        e_values = [0.1, 0.2, 0.4, 0.8]  # 误差随网格加密而增大

        result = judge_convergence(h_values, e_values)
        assert result.verdict == "SUSPECT", f"Expected SUSPECT for divergent, got {result.verdict}"

    def test_too_few_points(self):
        """点数不足时返回默认值。"""
        result = judge_convergence([1.0], [0.1])
        assert result.order == 0.0
        assert result.verdict == "SUSPECT"

    def test_r_squared_perfect(self):
        """完美收敛数据 R²=1。"""
        h_values = [1.0, 0.5, 0.25, 0.125]
        e_values = [h ** 2 for h in h_values]

        p, r2 = estimate_order(h_values, e_values)
        assert abs(r2 - 1.0) < 1e-10, f"Expected R²=1, got {r2}"

    def test_as_dict(self):
        """序列化测试。"""
        cr = ConvergenceResult(order=1.95, verdict="PRE-FEASIBLE",
                               n_points=5, r_squared=0.999)
        d = cr.as_dict()
        assert d["order"] == 1.95
        assert d["verdict"] == "PRE-FEASIBLE"
        assert d["n_points"] == 5
