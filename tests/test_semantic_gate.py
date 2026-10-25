#!/usr/bin/env python3
"""语义门单元测试。

覆盖三类判据 + SI-4 翻案锚测试。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

# 添加项目路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "ops"))

from semantic_gate import SemanticGate, si4_anchor_criteria


class TestSemanticGateProvenance(unittest.TestCase):
    """溯源判据测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_detects_synthetic_marker(self):
        """检测到合成标记应判不符。"""
        # 创建含合成标记的源码
        src_file = self.worktree / "run_demo.py"
        src_file.write_text(
            "from scenarios import generate_synthetic_lbm_snapshot\n"
            "snapshot = generate_synthetic_lbm_snapshot(seed=42)\n",
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_provenance",
            "category": "provenance",
            "assertion": {
                "scan_targets": ["**/*.py"],
                "markers": ["generate_synthetic", "seed="],
            },
        }
        result = gate.check_provenance(criterion)

        self.assertEqual(result["verdict"], "non_compliant")
        self.assertIn("generate_synthetic", result["gap"])

    def test_no_synthetic_marker_compliant(self):
        """无合成标记应判符合。"""
        # 创建真实数据文件
        src_file = self.worktree / "run_simulation.py"
        src_file.write_text(
            "from tensorlbm import LBM\n"
            "sim = LBM.from_file('flow_field.npz')\n",
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_provenance",
            "category": "provenance",
            "assertion": {
                "scan_targets": ["**/*.py"],
                "markers": ["generate_synthetic", "seed="],
            },
        }
        result = gate.check_provenance(criterion)

        self.assertEqual(result["verdict"], "compliant")


class TestSemanticGatePhysicsSwitch(unittest.TestCase):
    """物理开关判据测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_gravity_disabled_in_config(self):
        """配置中重力禁用应判不符。"""
        # 创建配置文件
        config_dir = self.worktree / "outputs"
        config_dir.mkdir()
        config_file = config_dir / "summary.json"
        config_file.write_text(
            json.dumps({"gravity_enabled": False}),
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_gravity",
            "category": "physics_switch",
            "assertion": {
                "config_files": ["outputs/**/*.json"],
                "key_path": "gravity_enabled",
                "expected": True,
            },
        }
        result = gate.check_physics_switch(criterion)

        self.assertEqual(result["verdict"], "non_compliant")
        self.assertIn("gravity_enabled", result["gap"])

    def test_gravity_disabled_in_source(self):
        """源码中 gravity=None 应判不符（grep fallback）。"""
        # 创建源码
        src_dir = self.worktree / "scenarios"
        src_dir.mkdir()
        src_file = src_dir / "demo.py"
        src_file.write_text(
            "def run():\n"
            "    field = ParticleField(gravity=None)\n",
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_gravity",
            "category": "physics_switch",
            "assertion": {
                "config_files": ["outputs/**/*.json"],
                "key_path": "gravity_enabled",
                "expected": True,
                "grep_fallback": {
                    "files": ["scenarios/**/*.py"],
                    "patterns": [r"gravity\s*=\s*None"],
                    "match_means": "fail",
                },
            },
        }
        result = gate.check_physics_switch(criterion)

        self.assertEqual(result["verdict"], "non_compliant")
        self.assertIn("禁用", result["gap"])

    def test_gravity_enabled_compliant(self):
        """配置中重力开启应判符合。"""
        config_dir = self.worktree / "outputs"
        config_dir.mkdir()
        config_file = config_dir / "summary.json"
        config_file.write_text(
            json.dumps({"gravity_enabled": True}),
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_gravity",
            "category": "physics_switch",
            "assertion": {
                "config_files": ["outputs/**/*.json"],
                "key_path": "gravity_enabled",
                "expected": True,
            },
        }
        result = gate.check_physics_switch(criterion)

        self.assertEqual(result["verdict"], "compliant")


class TestSemanticGateScaleRealism(unittest.TestCase):
    """规模现实性判据测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_below_lower_bound(self):
        """低于下限应判不符。"""
        # 创建工件
        out_dir = self.worktree / "outputs"
        out_dir.mkdir()
        summary_file = out_dir / "summary.json"
        summary_file.write_text(
            json.dumps({"total_physical_time_s": 0.005}),
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_duration",
            "category": "scale_realism",
            "assertion": {
                "source": {
                    "file": "outputs/**/*.json",
                    "key_path": "total_physical_time_s",
                },
                "lower_bound": 1.0,
                "unit": "s",
            },
        }
        result = gate.check_scale_realism(criterion)

        self.assertEqual(result["verdict"], "non_compliant")
        self.assertIn("0.005", result["gap"])

    def test_above_lower_bound(self):
        """高于下限应判符合。"""
        out_dir = self.worktree / "outputs"
        out_dir.mkdir()
        summary_file = out_dir / "summary.json"
        summary_file.write_text(
            json.dumps({"total_physical_time_s": 10.5}),
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_duration",
            "category": "scale_realism",
            "assertion": {
                "source": {
                    "file": "outputs/**/*.json",
                    "key_path": "total_physical_time_s",
                },
                "lower_bound": 1.0,
                "unit": "s",
            },
        }
        result = gate.check_scale_realism(criterion)

        self.assertEqual(result["verdict"], "compliant")

    def test_key_not_found(self):
        """键不存在应判无法判定。"""
        out_dir = self.worktree / "outputs"
        out_dir.mkdir()
        summary_file = out_dir / "summary.json"
        summary_file.write_text(
            json.dumps({"other_key": 42}),
            encoding="utf-8",
        )

        gate = SemanticGate(self.worktree)
        criterion = {
            "id": "test_duration",
            "category": "scale_realism",
            "assertion": {
                "source": {
                    "file": "outputs/**/*.json",
                    "key_path": "total_physical_time_s",
                },
                "lower_bound": 1.0,
                "unit": "s",
            },
        }
        result = gate.check_scale_realism(criterion)

        self.assertEqual(result["verdict"], "undetermined")


class TestSI4AnchorCase(unittest.TestCase):
    """SI-4 翻案锚测试：语义门对 SI-4 工件必须判不符合。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)

        # 模拟 SI-4 工件
        self._create_si4_artifacts()

    def tearDown(self):
        self.tempdir.cleanup()

    def _create_si4_artifacts(self):
        """创建 SI-4 锚案例工件（合成载荷/无重力/小规模）。"""
        # 源码：含合成标记 + gravity=None
        scenarios_dir = self.worktree / "scenarios" / "suboff_icebreaking" / "si4_full_chain"
        scenarios_dir.mkdir(parents=True)
        run_demo = scenarios_dir / "run_demo.py"
        run_demo.write_text(
            "from scenarios.suboff_icebreaking.si1_one_way.lbm_load_extract import (\n"
            "    generate_synthetic_lbm_snapshot,\n"
            ")\n"
            "\n"
            "def main():\n"
            "    generate_synthetic_lbm_snapshot(seed=42)\n"
            "    field = BondedParticleField(gravity=None)\n",
            encoding="utf-8",
        )

        # 工件：小规模
        outputs_dir = self.worktree / "outputs" / "si4_full_chain"
        outputs_dir.mkdir(parents=True)

        # full_chain_summary.json
        summary = {
            "scenario": "SI-4: 全链演示",
            "n_chain_steps": 5,
            "n_dem_substeps": 3,
            "total_physical_time_s": 0.005,  # 5ms
            "n_elements": 16,  # 4×4 网格
            "final_state": {
                "n_particles": 12,
                "n_bonds": 30,
                "n_bonds_broken": 0,
            },
        }
        (outputs_dir / "full_chain_summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )

        # dem_snapshot_step0.json
        dem_snap = {
            "chain_step": 0,
            "n_particles": 12,
            "n_bonds": 30,
            "n_bonds_broken_total": 0,
            "potential_energy_J": -0.0,  # 无重力
        }
        (outputs_dir / "dem_snapshot_step0.json").write_text(
            json.dumps(dem_snap), encoding="utf-8"
        )

    def test_si4_overall_non_compliant(self):
        """SI-4 总判定必须为不符合或部分符合。"""
        gate = SemanticGate(self.worktree)
        criteria = si4_anchor_criteria()
        report = gate.evaluate("SUBOFF 顶破冰板完整演化", criteria)

        # 总判定不能是 compliant
        self.assertIn(
            report["overall_verdict"],
            ["non_compliant", "partial_compliant"],
            f"SI-4 应判不符合或部分符合，实际：{report['overall_verdict']}",
        )

    def test_si4_provenance_gap(self):
        """SI-4 必须检出合成载荷差距。"""
        gate = SemanticGate(self.worktree)
        criteria = si4_anchor_criteria()
        report = gate.evaluate("SUBOFF 顶破冰板完整演化", criteria)

        # 找溯源判据
        provenance_results = [
            r for r in report["criteria_results"]
            if r["category"] == "provenance"
        ]
        self.assertTrue(len(provenance_results) > 0, "应有溯源判据")

        # 至少一个溯源判据不符
        provenance_gaps = [
            r for r in provenance_results
            if r["verdict"] == "non_compliant"
        ]
        self.assertTrue(
            len(provenance_gaps) > 0,
            "SI-4 应检出合成载荷差距",
        )

    def test_si4_physics_switch_gap(self):
        """SI-4 必须检出重力禁用差距。"""
        gate = SemanticGate(self.worktree)
        criteria = si4_anchor_criteria()
        report = gate.evaluate("SUBOFF 顶破冰板完整演化", criteria)

        # 找物理开关判据
        physics_results = [
            r for r in report["criteria_results"]
            if r["category"] == "physics_switch"
        ]
        self.assertTrue(len(physics_results) > 0, "应有物理开关判据")

        # 至少一个物理开关判据不符
        physics_gaps = [
            r for r in physics_results
            if r["verdict"] == "non_compliant"
        ]
        self.assertTrue(
            len(physics_gaps) > 0,
            "SI-4 应检出重力禁用差距",
        )

    def test_si4_scale_gaps(self):
        """SI-4 必须检出规模差距（时长/网格/颗粒）。"""
        gate = SemanticGate(self.worktree)
        criteria = si4_anchor_criteria()
        report = gate.evaluate("SUBOFF 顶破冰板完整演化", criteria)

        # 找规模判据
        scale_results = [
            r for r in report["criteria_results"]
            if r["category"] == "scale_realism"
        ]
        self.assertTrue(len(scale_results) > 0, "应有规模判据")

        # 至少两个规模判据不符
        scale_gaps = [
            r for r in scale_results
            if r["verdict"] == "non_compliant"
        ]
        self.assertTrue(
            len(scale_gaps) >= 2,
            f"SI-4 应检出至少 2 个规模差距，实际：{len(scale_gaps)}",
        )

    def test_si4_gap_summary(self):
        """SI-4 差距摘要应反映三类差距。"""
        gate = SemanticGate(self.worktree)
        criteria = si4_anchor_criteria()
        report = gate.evaluate("SUBOFF 顶破冰板完整演化", criteria)

        summary = report["gap_summary"]
        self.assertGreater(summary["provenance_gaps"], 0, "应有溯源差距")
        self.assertGreater(summary["physics_switch_gaps"], 0, "应有物理开关差距")
        self.assertGreater(summary["scale_realism_gaps"], 0, "应有规模差距")


class TestOverallVerdictLogic(unittest.TestCase):
    """总判定逻辑测试。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_all_compliant(self):
        """全部符合应判内容符合。"""
        gate = SemanticGate(self.worktree)
        criteria = [
            {
                "id": "c1",
                "category": "provenance",
                "assertion": {"scan_targets": ["**/*.txt"], "markers": ["synthetic"]},
            },
        ]
        # 无文件，无合成标记
        report = gate.evaluate("test goal", criteria)
        self.assertEqual(report["overall_verdict"], "compliant")

    def test_provenance_fail_means_non_compliant(self):
        """溯源不符应判不符合。"""
        # 创建含合成标记的文件
        (self.worktree / "test.py").write_text("synthetic_data = True\n")

        gate = SemanticGate(self.worktree)
        criteria = [
            {
                "id": "c1",
                "category": "provenance",
                "assertion": {"scan_targets": ["**/*.py"], "markers": ["synthetic"]},
            },
        ]
        report = gate.evaluate("test goal", criteria)
        self.assertEqual(report["overall_verdict"], "non_compliant")

    def test_two_categories_fail_means_non_compliant(self):
        """两类判据不符应判不符合。"""
        # 创建含合成标记的文件
        (self.worktree / "test.py").write_text("synthetic_data = True\n")
        # 创建配置文件（规模不符）
        out_dir = self.worktree / "outputs"
        out_dir.mkdir()
        (out_dir / "summary.json").write_text(json.dumps({"value": 1}))

        gate = SemanticGate(self.worktree)
        criteria = [
            {
                "id": "c1",
                "category": "provenance",
                "assertion": {"scan_targets": ["**/*.py"], "markers": ["synthetic"]},
            },
            {
                "id": "c2",
                "category": "scale_realism",
                "assertion": {
                    "source": {"file": "outputs/**/*.json", "key_path": "value"},
                    "lower_bound": 100,
                },
            },
        ]
        report = gate.evaluate("test goal", criteria)
        self.assertEqual(report["overall_verdict"], "non_compliant")

    def test_one_category_fail_means_partial(self):
        """一类判据不符应判部分符合。"""
        # 创建配置文件（规模不符）
        out_dir = self.worktree / "outputs"
        out_dir.mkdir()
        (out_dir / "summary.json").write_text(json.dumps({"value": 1}))

        gate = SemanticGate(self.worktree)
        criteria = [
            {
                "id": "c1",
                "category": "scale_realism",
                "assertion": {
                    "source": {"file": "outputs/**/*.json", "key_path": "value"},
                    "lower_bound": 100,
                },
            },
        ]
        report = gate.evaluate("test goal", criteria)
        self.assertEqual(report["overall_verdict"], "partial_compliant")


if __name__ == "__main__":
    unittest.main()
