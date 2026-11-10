#!/usr/bin/env python3
"""E1 精度模型与金标集（criteria_precheck.precision_model + gold_standard）。

判据先于实现，机器可裁：
1. 合成金标集 200 条（100 不可达成 + 100 可达成）；
2. recall(不可达成) ≥ 0.90；
3. FP(可达成) ≤ 0.05。
"""

from __future__ import annotations

import pytest

from backend.criteria_precheck.precision_model import (
    Achievability,
    assess_achievability,
    parse_criteria_thresholds,
)
from backend.criteria_precheck.gold_standard import (
    GoldStandard,
    generate_gold_standard,
    evaluate_gold_standard,
)


class TestPrecisionModel:
    """E1: 精度模型验证。"""

    def test_below_float64_precision_is_unreachable(self):
        """阈值 < 1e-10 → UNREACHABLE。"""
        text = "与解析解比较，相对误差 <= 1e-20"
        result = assess_achievability(text)
        assert result.verdict == "UNREACHABLE"

    def test_negative_threshold_is_unreachable(self):
        """负误差 → UNREACHABLE。"""
        text = "相对误差 <= -5%"
        result = assess_achievability(text)
        assert result.verdict == "UNREACHABLE"

    def test_zero_error_without_conservation_is_unreachable(self):
        """误差 == 0 且无守恒/自洽参考 → UNREACHABLE。"""
        text = "与实验数据比较，误差 == 0"
        result = assess_achievability(text)
        assert result.verdict == "UNREACHABLE"

    def test_zero_error_with_conservation_is_reachable(self):
        """误差 == 0 且有守恒参考 → REACHABLE。"""
        text = "守恒律残差 == 0（守恒核对）"
        result = assess_achievability(text)
        assert result.verdict == "REACHABLE"

    def test_reasonable_threshold_is_reachable(self):
        """合理阈值 → REACHABLE。"""
        text = "与解析解比较，相对误差 <= 1e-10"
        result = assess_achievability(text)
        assert result.verdict == "REACHABLE"

    def test_no_threshold_defaults_reachable(self):
        """无阈值 → REACHABLE（默认）。"""
        text = "效果显著提升"
        result = assess_achievability(text)
        assert result.verdict == "REACHABLE"
        assert result.confidence == 0.5

    def test_parse_thresholds(self):
        """阈值提取。"""
        text = "误差 <= 3% 且 F1 >= 0.95"
        thresholds = parse_criteria_thresholds(text)
        assert 3.0 in thresholds or 0.03 in thresholds
        assert 0.95 in thresholds

    def test_as_dict(self):
        """序列化测试。"""
        a = Achievability(verdict="UNREACHABLE",
                          reasons=("below_float64_precision:1e-20",),
                          threshold_value=1e-20)
        d = a.as_dict()
        assert d["verdict"] == "UNREACHABLE"
        assert d["threshold_value"] == 1e-20


class TestGoldStandard:
    """E1: 金标集验证。"""

    def test_gold_standard_size(self):
        """金标集 200 条（100+100）。"""
        gs = generate_gold_standard(n_reachable=100, n_unreachable=100)
        assert len(gs.cases) == 200
        assert gs.n_reachable == 100
        assert gs.n_unreachable == 100

    def test_recall_unreachable_ge_090(self):
        """recall(不可达成) ≥ 0.90。"""
        gs = generate_gold_standard(n_reachable=100, n_unreachable=100)
        metrics = evaluate_gold_standard(gs)
        assert metrics["recall_unreachable"] >= 0.90, (
            f"recall={metrics['recall_unreachable']}, "
            f"confusion={metrics['confusion']}"
        )

    def test_fp_rate_reachable_le_005(self):
        """FP(可达成) ≤ 0.05。"""
        gs = generate_gold_standard(n_reachable=100, n_unreachable=100)
        metrics = evaluate_gold_standard(gs)
        assert metrics["fp_rate_reachable"] <= 0.05, (
            f"FP_rate={metrics['fp_rate_reachable']}, "
            f"confusion={metrics['confusion']}"
        )

    def test_gold_standard_deterministic(self):
        """同种子同输出。"""
        gs1 = generate_gold_standard(100, 100, seed=42)
        gs2 = generate_gold_standard(100, 100, seed=42)
        assert gs1.n_reachable == gs2.n_reachable
        assert gs1.n_unreachable == gs2.n_unreachable
        assert len(gs1.cases) == len(gs2.cases)

    def test_as_dict(self):
        """序列化测试。"""
        gs = generate_gold_standard(10, 10)
        d = gs.as_dict()
        assert d["n_total"] == 20
        assert d["n_reachable"] == 10
        assert d["n_unreachable"] == 10
        assert len(d["cases"]) == 20
