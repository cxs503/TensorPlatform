"""Tests for backend/tools/goal_lint.py — 四要素检测器回归测试。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.tools.goal_lint import (
    lint_goal,
    _detect_numerical_gate,
    _detect_reference_anchor,
    _detect_negative_control,
    _detect_artifact_path,
    _build_synthetic_corpus,
    compute_auc,
    compute_recall_at_threshold,
    point_biserial_correlation,
    run_calibration,
    run_holdout_calibration,
    run_j2_control,
    run_j4_determinism,
    run_full_validation,
    GoalLintResult,
)

# ──────────────────────────────────────────────
# 单要素检测测试
# ──────────────────────────────────────────────

class TestNumericalGate:
    def test_detects_threshold_with_number(self):
        r = _detect_numerical_gate("误差 ≤3%")
        assert r.present is True
        assert r.score == 1.0

    def test_detects_tolerance(self):
        r = _detect_numerical_gate("收敛容差 <1e-6")
        assert r.present is True

    def test_detects_percentage(self):
        r = _detect_numerical_gate("相对误差 <5%")
        assert r.present is True

    def test_no_number_no_gate(self):
        r = _detect_numerical_gate("误差分析")
        assert r.present is False

    def test_no_keyword_no_gate(self):
        r = _detect_numerical_gate("计算结果很好")
        assert r.present is False

    def test_english_tolerance(self):
        r = _detect_numerical_gate("tolerance ≤0.01")
        assert r.present is True

    def test_within_pattern(self):
        r = _detect_numerical_gate("error within 3%")
        assert r.present is True


class TestReferenceAnchor:
    def test_detects_analytical(self):
        r = _detect_reference_anchor("对比解析解")
        assert r.present is True

    def test_detects_experiment(self):
        r = _detect_reference_anchor("与实验数据对标")
        assert r.present is True

    def test_detects_itcc(self):
        r = _detect_reference_anchor("对标 ITTC 推荐公式")
        assert r.present is True

    def test_detects_baseline(self):
        r = _detect_reference_anchor("与 baseline 对比")
        assert r.present is True

    def test_no_anchor(self):
        r = _detect_reference_anchor("改进算法")
        assert r.present is False

    def test_detects_vs(self):
        r = _detect_reference_anchor("result vs reference")
        assert r.present is True


class TestNegativeControl:
    def test_detects_negative_control(self):
        r = _detect_negative_control("负控：网格粗化必红")
        assert r.present is True

    def test_detects_ablation(self):
        r = _detect_negative_control("消融测试验证鲁棒性")
        assert r.present is True

    def test_detects_sensitivity(self):
        r = _detect_negative_control("参数扫描敏感性分析")
        assert r.present is True

    def test_detects_must_fail(self):
        r = _detect_negative_control("错误参数必失败")
        assert r.present is True

    def test_no_neg_control(self):
        r = _detect_negative_control("优化性能")
        assert r.present is False

    def test_detects_robustness(self):
        r = _detect_negative_control("鲁棒性测试")
        assert r.present is True


class TestArtifactPath:
    def test_detects_artifacts_path(self):
        r = _detect_artifact_path("输出到 artifacts/resistance.csv")
        assert r.present is True

    def test_detects_docs_path(self):
        r = _detect_artifact_path("生成 docs/report.md")
        assert r.present is True

    def test_detects_runs_path(self):
        r = _detect_artifact_path("保存 runs/study.json")
        assert r.present is True

    def test_detects_pytest(self):
        r = _detect_artifact_path("pytest test_convergence.py 通过")
        assert r.present is True

    def test_detects_python_file(self):
        r = _detect_artifact_path("运行 script.py")
        assert r.present is True

    def test_no_artifact(self):
        r = _detect_artifact_path("改进算法")
        assert r.present is False


# ──────────────────────────────────────────────
# 完整 lint 测试
# ──────────────────────────────────────────────

class TestLintGoal:
    def test_full_four_elements(self):
        goal = "阻力误差 ≤2%，对标 ITTC 78 方法，负控：网格粗化必红，输出 artifacts/resistance.md"
        r = lint_goal(goal)
        assert r.total == 4
        assert r.score_a.present is True
        assert r.score_b.present is True
        assert r.score_c.present is True
        assert r.score_d.present is True

    def test_three_elements(self):
        goal = "阻力计算误差 ≤3%，对标 ITTC 推荐公式，结果输出到 artifacts/resistance.csv"
        r = lint_goal(goal)
        assert r.total == 3
        assert r.score_a.present is True
        assert r.score_b.present is True
        assert r.score_c.present is False
        assert r.score_d.present is True

    def test_zero_elements(self):
        goal = "改进阻力计算模块"
        r = lint_goal(goal)
        assert r.total == 0

    def test_one_element_numerical(self):
        goal = "误差 ≤3%"
        r = lint_goal(goal)
        assert r.total == 1
        assert r.score_a.present is True

    def test_one_element_reference(self):
        goal = "对标 ITTC 推荐公式"
        r = lint_goal(goal)
        assert r.total == 1
        assert r.score_b.present is True

    def test_sha256_deterministic(self):
        goal = "阻力计算误差 ≤3%，对标 ITTC 推荐公式"
        r1 = lint_goal(goal)
        r2 = lint_goal(goal)
        assert r1.sha256 == r2.sha256
        assert r1.total == r2.total

    def test_to_dict(self):
        goal = "误差 ≤3%"
        r = lint_goal(goal)
        d = r.to_dict()
        assert "goal" in d
        assert "total" in d
        assert "sha256" in d
        assert "score_a" in d
        assert "score_b" in d
        assert "score_c" in d
        assert "score_d" in d


# ──────────────────────────────────────────────
# 评估指标测试
# ──────────────────────────────────────────────

class TestMetrics:
    def test_auc_perfect(self):
        scores = [1.0, 1.0, 0.0, 0.0]
        labels = [1, 1, 0, 0]
        auc = compute_auc(scores, labels)
        assert auc == 1.0

    def test_auc_random(self):
        scores = [0.5, 0.5, 0.5, 0.5]
        labels = [1, 1, 0, 0]
        auc = compute_auc(scores, labels)
        assert 0.4 <= auc <= 0.6

    def test_auc_reversed(self):
        scores = [0.0, 0.0, 1.0, 1.0]
        labels = [1, 1, 0, 0]
        auc = compute_auc(scores, labels)
        assert auc == 0.0

    def test_recall_perfect(self):
        results = [
            GoalLintResult("g1", *_make_elements(1,1,1,1), 4, "h1"),
            GoalLintResult("g2", *_make_elements(1,1,1,0), 3, "h2"),
        ]
        labels = [1, 1]
        recall, fpr = compute_recall_at_threshold(results, labels, threshold=2)
        assert recall == 1.0

    def test_recall_zero(self):
        results = [
            GoalLintResult("g1", *_make_elements(0,0,0,0), 0, "h1"),
            GoalLintResult("g2", *_make_elements(0,0,0,0), 0, "h2"),
        ]
        labels = [1, 1]
        recall, fpr = compute_recall_at_threshold(results, labels, threshold=2)
        assert recall == 0.0

    def test_point_biserial_perfect(self):
        scores = [4.0, 4.0, 0.0, 0.0]
        labels = [1, 1, 0, 0]
        r = point_biserial_correlation(scores, labels)
        assert abs(r) > 0.9

    def test_point_biserial_zero(self):
        scores = [2.0, 2.0, 2.0, 2.0]
        labels = [1, 1, 0, 0]
        r = point_biserial_correlation(scores, labels)
        assert r == 0.0


def _make_elements(a, b, c, d):
    """Helper to create ElementResult instances."""
    from backend.tools.goal_lint import ElementResult
    return (
        ElementResult(present=bool(a), score=float(a)),
        ElementResult(present=bool(b), score=float(b)),
        ElementResult(present=bool(c), score=float(c)),
        ElementResult(present=bool(d), score=float(d)),
    )


# ──────────────────────────────────────────────
# 校准测试
# ──────────────────────────────────────────────

class TestCalibration:
    def test_synthetic_corpus_size(self):
        corpus = _build_synthetic_corpus()
        assert len(corpus) == 120
        pos = sum(1 for c in corpus if c["label"] == 1)
        neg = sum(1 for c in corpus if c["label"] == 0)
        assert pos == 60
        assert neg == 60

    def test_calibration_runs(self):
        corpus = _build_synthetic_corpus()
        result = run_calibration(corpus)
        assert "auc" in result
        assert "recall_at_threshold_2" in result
        assert "false_positive_rate_at_threshold_2" in result
        assert "score_distribution" in result
        assert result["n_samples"] == 120

    def test_holdout_calibration(self):
        corpus = _build_synthetic_corpus()
        result = run_holdout_calibration(corpus)
        assert "train" in result
        assert "test" in result
        assert result["train_size"] == 60
        assert result["test_size"] == 60


# ──────────────────────────────────────────────
# 预注册判据测试
# ──────────────────────────────────────────────

class TestPreRegisteredCriteria:
    def test_j1_synthetic_corpus(self):
        """J1: 合成语料召回 ≥0.90 且误报率 ≤0.10。"""
        corpus = _build_synthetic_corpus()
        result = run_calibration(corpus)
        assert result["j1_pass"] is True, (
            f"J1 failed: recall={result['recall_at_threshold_2']}, "
            f"fpr={result['false_positive_rate_at_threshold_2']}"
        )

    def test_j2_m611_control(self):
        """J2: 已知无判据历史单必须 0 分。"""
        result = run_j2_control()
        assert result["j2_pass"] is True, (
            f"J2 failed: {result['n_samples']} samples, "
            f"all_zero={result['all_zero']}"
        )

    def test_j3_holdout_auc(self):
        """J3: hold-out AUC ≥0.65。"""
        corpus = _build_synthetic_corpus()
        result = run_holdout_calibration(corpus)
        test_auc = result["test"]["auc"]
        assert result["test"]["j3_pass"] is True, (
            f"J3 failed: test AUC={test_auc} < 0.65"
        )

    def test_j4_determinism(self):
        """J4: 同输入同 sha256。"""
        result = run_j4_determinism()
        assert result["j4_pass"] is True, (
            f"J4 failed: all_match={result['all_match']}"
        )

    def test_full_validation(self):
        """完整预注册验证。"""
        result = run_full_validation()
        assert result["all_pass"] is True


# ──────────────────────────────────────────────
# 边界/回归测试
# ──────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_goal(self):
        r = lint_goal("")
        assert r.total == 0

    def test_unicode_goal(self):
        r = lint_goal("误差 ≤3% 🚀")
        assert r.score_a.present is True

    def test_very_long_goal(self):
        goal = "a" * 10000 + "误差 ≤3%"
        r = lint_goal(goal)
        assert r.score_a.present is True

    def test_mixed_language(self):
        goal = "误差 ≤3%, compare with ITTC baseline, negative control required, output to artifacts/result.json"
        r = lint_goal(goal)
        assert r.total == 4

    def test_goal_with_newlines(self):
        goal = "误差 ≤3%\n对标 ITTC\n负控必红\n输出 artifacts/r.json"
        r = lint_goal(goal)
        assert r.total == 4

    def test_score_range(self):
        """总分必须在 0-4 范围内。"""
        goals = [
            "改进模块",
            "误差 ≤3%",
            "误差 ≤3%，对标 ITTC",
            "误差 ≤3%，对标 ITTC，负控必红",
            "误差 ≤3%，对标 ITTC，负控必红，输出 artifacts/r.json",
        ]
        for i, g in enumerate(goals):
            r = lint_goal(g)
            assert 0 <= r.total <= 4
            assert r.total == i
