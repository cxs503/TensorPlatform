#!/usr/bin/env python3
"""质量门杀伤性自测（R1 约束质量门自身）。

QUALITY_BAR.md R1：新增或修改任何验收门/基准判据，必须附 mutation 探针证明该门
在故意破坏时会失败。本文件即 quality_gate.py 的 mutation 探针集。

测试策略：
  - 喂故意坏工件 → 必须各判 FAIL
  - 喂好工件 → 必须判 PASS
  - 边界情况 → UNDETERMINED 登记（不假绿）

覆盖：
  - R1 探针存在性（有/无 @mutation 探针）
  - R2 参考数据声明（缺声明/无效声明/有效声明）
  - R3 物理项路径（无声明/有声明无路径/有声明有路径）
  - R4 基线红记账（新增红/无新增红/解析逻辑）
  - R5 判决史（删除判决/修改移除 FAIL/正常修改/无变更）
  - 总判定逻辑（任一 FAIL → overall FAIL）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "ops"))

from quality_gate import (
    QualityGateResult,
    RuleVerdict,
    check_r1_probe_existence,
    check_r2_reference_provenance,
    check_r3_physics_path,
    check_r5_verdict_history,
    parse_pytest_failures,
)


def init_git_repo(worktree: Path) -> None:
    """初始化 git 仓库。"""
    subprocess.run(["git", "init", "-b", "main"], cwd=worktree, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=worktree, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=worktree, capture_output=True, check=True)


def git_commit(worktree: Path, msg: str) -> None:
    """提交所有变更。"""
    subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"],
                   cwd=worktree, capture_output=True, check=True)


def create_branch(worktree: Path, branch: str, base: str = "main") -> None:
    """创建并切换到新分支。"""
    subprocess.run(["git", "checkout", "-b", branch, base],
                   cwd=worktree, capture_output=True, check=True)


def checkout_branch(worktree: Path, branch: str) -> None:
    """切换到已有分支。"""
    subprocess.run(["git", "checkout", branch],
                   cwd=worktree, capture_output=True, check=True)


class TestR1ProbeExistence(unittest.TestCase):
    """R1 探针存在性测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)
        init_git_repo(self.worktree)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_no_gate_changes_pass(self):
        """无门/判据变更 → PASS（R1 不适用）。"""
        # 创建 main 分支
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        # 创建 feature 分支，只改 README
        create_branch(self.worktree, "feature")
        (self.worktree / "README.md").write_text("# Test Updated", encoding="utf-8")
        git_commit(self.worktree, "update readme")

        result = check_r1_probe_existence(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.PASS)
        self.assertIn("无门/判据变更", result.reason)

    def test_gate_change_no_probe_fail(self):
        """门/判据变更但无 @mutation 探针 → FAIL。"""
        # 创建 main 分支
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        # 创建 feature 分支，改 gates.json
        create_branch(self.worktree, "feature")
        benchmarks_dir = self.worktree / "benchmarks" / "case1"
        benchmarks_dir.mkdir(parents=True)
        (benchmarks_dir / "gates.json").write_text(
            json.dumps({"case": "test", "gates": []}), encoding="utf-8")
        git_commit(self.worktree, "add gates")

        result = check_r1_probe_existence(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)
        self.assertIn("无 @mutation 探针", result.reason)

    def test_gate_change_with_probe_pass(self):
        """门/判据变更且有 @mutation 探针 → PASS。"""
        # 创建 main 分支
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        # 创建 feature 分支，改 gates.json + 加探针
        create_branch(self.worktree, "feature")
        benchmarks_dir = self.worktree / "benchmarks" / "case1"
        benchmarks_dir.mkdir(parents=True)
        (benchmarks_dir / "gates.json").write_text(
            json.dumps({"case": "test", "gates": []}), encoding="utf-8")

        tests_dir = self.worktree / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_probe.py").write_text(
            "# @mutation\n"
            "def test_bad_reference():\n"
            "    pass\n",
            encoding="utf-8")
        git_commit(self.worktree, "add gates + probe")

        result = check_r1_probe_existence(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.PASS)
        self.assertIn("@mutation 探针", result.reason)

    def test_validation_report_change_no_probe_fail(self):
        """validation_report 变更但无探针 → FAIL。"""
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        create_branch(self.worktree, "feature")
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps({"task": "m999"}), encoding="utf-8")
        git_commit(self.worktree, "add validation report")

        result = check_r1_probe_existence(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)


class TestR2ReferenceProvenance(unittest.TestCase):
    """R2 参考数据声明测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_no_validation_report_undetermined(self):
        """无 validation_report → UNDETERMINED。"""
        result = check_r2_reference_provenance(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.UNDETERMINED)

    def test_missing_provenance_fail(self):
        """validation_report 缺 reference_provenance → FAIL。"""
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r2_reference_provenance(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)
        self.assertIn("缺少 reference_provenance", result.reason)

    def test_invalid_provenance_type_fail(self):
        """reference_provenance.type 无效 → FAIL。"""
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
            "reference_provenance": {"type": "self_generated"},
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r2_reference_provenance(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)
        # reason 可能是"无效"或"不在允许值"
        self.assertTrue(
            "无效" in result.reason or "不在允许值" in result.reason,
            f"unexpected reason: {result.reason}")

    def test_experiment_without_source_fail(self):
        """experiment 类型缺 source → FAIL。"""
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
            "reference_provenance": {"type": "experiment"},
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r2_reference_provenance(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)
        # reason 可能是"缺少 source"或"无效"
        self.assertTrue(
            "缺少 source" in result.reason or "无效" in result.reason,
            f"unexpected reason: {result.reason}")

    def test_valid_analytical_pass(self):
        """有效 analytical 声明 → PASS。"""
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
            "reference_provenance": {
                "type": "analytical",
                "source": "M&M 1953",
            },
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r2_reference_provenance(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.PASS)

    def test_valid_experiment_with_source_pass(self):
        """有效 experiment+source 声明 → PASS。"""
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
            "reference_provenance": {
                "type": "experiment",
                "source": "Lethe 2024",
            },
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r2_reference_provenance(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.PASS)

    def test_valid_independent_impl_pass(self):
        """有效 independent_impl 声明 → PASS。"""
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
            "reference_provenance": {"type": "independent_impl"},
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r2_reference_provenance(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.PASS)


class TestR3PhysicsPath(unittest.TestCase):
    """R3 物理项路径抽查测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_no_physics_switches_undetermined(self):
        """无物理开关声明 → UNDETERMINED。"""
        result = check_r3_physics_path(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.UNDETERMINED)

    def test_physics_switch_no_equation_undetermined(self):
        """有物理开关但无方程路径 → UNDETERMINED。"""
        report = {
            "task": "m999",
            "physics_switches": {"gravity": True},
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r3_physics_path(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.UNDETERMINED)
        self.assertIn("无法静态判定", result.reason)

    def test_physics_switch_with_equation_pass(self):
        """有物理开关且有方程路径 → PASS。"""
        report = {
            "task": "m999",
            "physics_switches": {"gravity": True},
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        # 创建含重力方程的源码
        src_dir = self.worktree / "src"
        src_dir.mkdir()
        (src_dir / "solver.py").write_text(
            "def apply_gravity(rho, g_vec):\n"
            "    body_force = rho * g_vec\n"
            "    return body_force\n",
            encoding="utf-8")

        result = check_r3_physics_path(self.worktree, "main")
        self.assertEqual(result.verdict, RuleVerdict.PASS)

    def test_disabled_switch_not_checked(self):
        """禁用的物理开关不检查。"""
        report = {
            "task": "m999",
            "physics_switches": {"gravity": False},
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        result = check_r3_physics_path(self.worktree, "main")
        # 无启用的开关 → UNDETERMINED
        self.assertEqual(result.verdict, RuleVerdict.UNDETERMINED)


class TestR4BaselineReds(unittest.TestCase):
    """R4 基线红记账测试（单元测试 parse_pytest_failures）。"""

    def test_parse_pytest_failures_empty(self):
        """空输出 → 空列表。"""
        self.assertEqual(parse_pytest_failures(""), [])

    def test_parse_pytest_failures_single(self):
        """单个失败测试。"""
        output = "FAILED tests/test_foo.py::test_bar - AssertionError"
        result = parse_pytest_failures(output)
        self.assertEqual(result, ["tests/test_foo.py::test_bar"])

    def test_parse_pytest_failures_multiple(self):
        """多个失败测试。"""
        output = """
FAILED tests/test_a.py::test_1 - AssertionError
FAILED tests/test_b.py::test_2 - ValueError
PASSED tests/test_c.py::test_3
"""
        result = parse_pytest_failures(output)
        self.assertEqual(result, [
            "tests/test_a.py::test_1",
            "tests/test_b.py::test_2",
        ])

    def test_parse_pytest_failures_no_failures(self):
        """无失败测试。"""
        output = """
PASSED tests/test_a.py::test_1
PASSED tests/test_b.py::test_2
"""
        result = parse_pytest_failures(output)
        self.assertEqual(result, [])


class TestR5VerdictHistory(unittest.TestCase):
    """R5 判决史 append-only 测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)
        init_git_repo(self.worktree)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_no_verdict_changes_pass(self):
        """无判决文件变更 → PASS。"""
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        create_branch(self.worktree, "feature")
        (self.worktree / "README.md").write_text("# Updated", encoding="utf-8")
        git_commit(self.worktree, "update readme")

        result = check_r5_verdict_history(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.PASS)

    def test_verdict_deletion_fail(self):
        """判决文件被删除 → FAIL。"""
        # main 分支有判决文件
        verdict_dir = self.worktree / "benchmarks" / "case1"
        verdict_dir.mkdir(parents=True)
        (verdict_dir / "verdict.json").write_text(
            json.dumps({"case": "case1", "verdict": "promote"}), encoding="utf-8")
        git_commit(self.worktree, "initial")

        # feature 分支删除判决文件
        create_branch(self.worktree, "feature")
        (verdict_dir / "verdict.json").unlink()
        git_commit(self.worktree, "delete verdict")

        result = check_r5_verdict_history(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)
        self.assertIn("被删除", result.reason)

    def test_verdict_fail_removed_fail(self):
        """判决文件的 FAIL 条目被移除 → FAIL。"""
        # main 分支有 FAIL 判决
        verdict_dir = self.worktree / "benchmarks" / "case1"
        verdict_dir.mkdir(parents=True)
        (verdict_dir / "gates.json").write_text(
            json.dumps({"case": "case1", "verdict": "FAIL"}), encoding="utf-8")
        git_commit(self.worktree, "initial")

        # feature 分支修改 verdict 为 PASS
        create_branch(self.worktree, "feature")
        (verdict_dir / "gates.json").write_text(
            json.dumps({"case": "case1", "verdict": "PASS"}), encoding="utf-8")
        git_commit(self.worktree, "modify verdict")

        result = check_r5_verdict_history(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)
        self.assertIn("FAIL 条目被移除", result.reason)

    def test_verdict_gates_fail_removed_fail(self):
        """判决文件的 gates FAIL 数减少 → FAIL。"""
        # main 分支有 2 个 gate FAIL
        verdict_dir = self.worktree / "benchmarks" / "case1"
        verdict_dir.mkdir(parents=True)
        (verdict_dir / "gates.json").write_text(
            json.dumps({
                "case": "case1",
                "verdict": "terminal",
                "gates": [
                    {"gate": "g1", "pass": False},
                    {"gate": "g2", "pass": False},
                ],
            }), encoding="utf-8")
        git_commit(self.worktree, "initial")

        # feature 分支修复 1 个 gate
        create_branch(self.worktree, "feature")
        (verdict_dir / "gates.json").write_text(
            json.dumps({
                "case": "case1",
                "verdict": "terminal",
                "gates": [
                    {"gate": "g1", "pass": True},
                    {"gate": "g2", "pass": False},
                ],
            }), encoding="utf-8")
        git_commit(self.worktree, "fix one gate")

        result = check_r5_verdict_history(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)
        # Check evidence field for gates FAIL count change
        self.assertTrue(
            any("gates FAIL 数" in ev for ev in result.evidence),
            f"expected 'gates FAIL 数' in evidence, got: {result.evidence}")

    def test_verdict_normal_modification_pass(self):
        """判决文件正常修改（未删除 FAIL）→ PASS。"""
        # main 分支有判决
        verdict_dir = self.worktree / "benchmarks" / "case1"
        verdict_dir.mkdir(parents=True)
        (verdict_dir / "gates.json").write_text(
            json.dumps({"case": "case1", "verdict": "promote"}), encoding="utf-8")
        git_commit(self.worktree, "initial")

        # feature 分支添加字段（不删 FAIL）
        create_branch(self.worktree, "feature")
        (verdict_dir / "gates.json").write_text(
            json.dumps({
                "case": "case1",
                "verdict": "promote",
                "timestamp": "2026-10-25",
            }), encoding="utf-8")
        git_commit(self.worktree, "add timestamp")

        result = check_r5_verdict_history(self.worktree, "feature")
        self.assertEqual(result.verdict, RuleVerdict.PASS)


class TestOverallJudgment(unittest.TestCase):
    """总判定逻辑测试。"""

    def test_all_pass_overall_pass(self):
        """全部 PASS → overall PASS。"""
        result = QualityGateResult()
        result.add(RuleVerdict("R1", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R2", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R3", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R4", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R5", RuleVerdict.PASS, "ok"))

        self.assertEqual(result.overall, "PASS")

    def test_one_fail_overall_fail(self):
        """任一 FAIL → overall FAIL。"""
        result = QualityGateResult()
        result.add(RuleVerdict("R1", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R2", RuleVerdict.FAIL, "bad"))
        result.add(RuleVerdict("R3", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R4", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R5", RuleVerdict.PASS, "ok"))

        self.assertEqual(result.overall, "FAIL")

    def test_undetermined_not_fail(self):
        """UNDETERMINED 不阻断 → overall PASS_WITH_UNDETERMINED。"""
        result = QualityGateResult()
        result.add(RuleVerdict("R1", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R2", RuleVerdict.UNDETERMINED, "unknown"))
        result.add(RuleVerdict("R3", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R4", RuleVerdict.PASS, "ok"))
        result.add(RuleVerdict("R5", RuleVerdict.PASS, "ok"))

        self.assertEqual(result.overall, "PASS_WITH_UNDETERMINED")

    def test_fail_and_undetermined_overall_fail(self):
        """FAIL + UNDETERMINED → overall FAIL（FAIL 优先）。"""
        result = QualityGateResult()
        result.add(RuleVerdict("R1", RuleVerdict.FAIL, "bad"))
        result.add(RuleVerdict("R2", RuleVerdict.UNDETERMINED, "unknown"))

        self.assertEqual(result.overall, "FAIL")

    def test_tool_error_overall_undetermined(self):
        """工具异常 → overall UNDETERMINED（fail-open）。"""
        result = QualityGateResult()
        result.tool_error = "RuntimeError: test error"

        self.assertEqual(result.overall, "UNDETERMINED")

    def test_to_dict_structure(self):
        """to_dict 结构正确。"""
        result = QualityGateResult()
        result.add(RuleVerdict("R1", RuleVerdict.PASS, "ok", ["ev1"]))

        d = result.to_dict()
        self.assertIn("overall", d)
        self.assertIn("rules", d)
        self.assertIn("R1", d["rules"])
        self.assertEqual(d["rules"]["R1"]["verdict"], "PASS")
        self.assertEqual(d["rules"]["R1"]["evidence"], ["ev1"])


class TestAdversarialScenarios(unittest.TestCase):
    """杀伤性场景测试：喂故意坏工件必须 FAIL。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)
        init_git_repo(self.worktree)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_adversarial_missing_reference_declaration(self):
        """故意坏工件：缺参考声明 → R2 FAIL。"""
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        create_branch(self.worktree, "bad")
        # 故意不加 reference_provenance
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
            # 缺 reference_provenance
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")
        git_commit(self.worktree, "bad report")

        result = check_r2_reference_provenance(self.worktree, "bad")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)

    def test_adversarial_delete_fail_verdict(self):
        """故意坏工件：删 FAIL 判决 → R5 FAIL。"""
        verdict_dir = self.worktree / "benchmarks" / "case1"
        verdict_dir.mkdir(parents=True)
        (verdict_dir / "gates.json").write_text(
            json.dumps({"case": "case1", "verdict": "FAIL"}), encoding="utf-8")
        git_commit(self.worktree, "initial")

        create_branch(self.worktree, "bad")
        (verdict_dir / "gates.json").unlink()
        git_commit(self.worktree, "delete fail verdict")

        result = check_r5_verdict_history(self.worktree, "bad")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)

    def test_adversarial_gate_change_no_probe(self):
        """故意坏工件：改门但无探针 → R1 FAIL。"""
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        create_branch(self.worktree, "bad")
        benchmarks_dir = self.worktree / "benchmarks" / "case1"
        benchmarks_dir.mkdir(parents=True)
        (benchmarks_dir / "gates.json").write_text(
            json.dumps({"case": "test", "gates": []}), encoding="utf-8")
        git_commit(self.worktree, "add gate without probe")

        result = check_r1_probe_existence(self.worktree, "bad")
        self.assertEqual(result.verdict, RuleVerdict.FAIL)

    def test_good_artifact_all_pass(self):
        """好工件：全部合规 → 各规则 PASS 或 UNDETERMINED。"""
        (self.worktree / "README.md").write_text("# Test", encoding="utf-8")
        git_commit(self.worktree, "initial")

        create_branch(self.worktree, "good")
        # 好工件：有参考声明 + 有探针
        report = {
            "task": "m999",
            "new_tests": 5,
            "new_tests_passed": True,
            "reference_provenance": {
                "type": "analytical",
                "source": "M&M 1953",
            },
        }
        (self.worktree / "validation_report_m999.json").write_text(
            json.dumps(report), encoding="utf-8")

        tests_dir = self.worktree / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_probe.py").write_text(
            "# @mutation\n"
            "def test_bad_reference():\n"
            "    pass\n",
            encoding="utf-8")
        git_commit(self.worktree, "good artifact")

        # R1: 有 validation_report 变更 + 有探针 → PASS
        r1 = check_r1_probe_existence(self.worktree, "good")
        self.assertEqual(r1.verdict, RuleVerdict.PASS)

        # R2: 有有效 reference_provenance → PASS
        r2 = check_r2_reference_provenance(self.worktree, "good")
        self.assertEqual(r2.verdict, RuleVerdict.PASS)

        # R5: 无判决文件变更 → PASS
        r5 = check_r5_verdict_history(self.worktree, "good")
        self.assertEqual(r5.verdict, RuleVerdict.PASS)


if __name__ == "__main__":
    unittest.main()
