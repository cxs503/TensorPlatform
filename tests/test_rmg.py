#!/usr/bin/env python3
"""RMG 科研任务生成器测试。

覆盖：
1. 锚案例：无有效证据引用的 proposal 必须被拒
2. 规则命中/未命中
3. 坏路径引用
4. 证据核验逻辑
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# 添加 tools/ops 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "ops"))

from rmg import (
    verify_evidence_ref,
    verify_evidence_refs,
    rule_r1_honest_fail_no_followup,
    rule_r4_prereg_untested,
    generate_proposal,
    scan_gaps,
)


class TestEvidenceVerification(unittest.TestCase):
    """证据核验测试。"""
    
    def test_anchor_case_invalid_ref_rejected(self):
        """锚案例：无有效证据引用的 proposal 必须被拒。"""
        proposal = {
            "proposal_id": "test-invalid-ref",
            "evidence_refs": [
                {
                    "file": "/nonexistent/path/verdict.json",
                    "claim": "test claim",
                    "machine_check": {
                        "type": "file_exists",
                        "path": "/nonexistent/path/verdict.json"
                    }
                }
            ]
        }
        
        passed, errors = verify_evidence_refs(proposal)
        self.assertFalse(passed, "无有效证据引用的 proposal 必须被拒")
        self.assertGreater(len(errors), 0)
    
    def test_valid_file_exists(self):
        """有效文件存在性核验。"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"test": "data"}, f)
            temp_path = f.name
        
        try:
            ref = {
                "file": temp_path,
                "claim": "test",
                "machine_check": {
                    "type": "file_exists",
                    "path": temp_path
                }
            }
            passed, msg = verify_evidence_ref(ref)
            self.assertTrue(passed)
            self.assertEqual(msg, "verified")
        finally:
            os.unlink(temp_path)
    
    def test_invalid_file_path(self):
        """无效文件路径核验。"""
        ref = {
            "file": "/nonexistent/file.json",
            "claim": "test",
            "machine_check": {
                "type": "file_exists",
                "path": "/nonexistent/file.json"
            }
        }
        passed, msg = verify_evidence_ref(ref)
        self.assertFalse(passed)
        self.assertIn("不存在", msg)
    
    def test_json_value_check_pass(self):
        """JSON 值核验通过。"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"status": "success", "value": 42}, f)
            temp_path = f.name
        
        try:
            ref = {
                "file": temp_path,
                "claim": "test",
                "machine_check": {
                    "type": "json_value",
                    "path": temp_path,
                    "key": "status",
                    "expected_contains": "success"
                }
            }
            passed, msg = verify_evidence_ref(ref)
            self.assertTrue(passed)
        finally:
            os.unlink(temp_path)
    
    def test_json_value_check_fail(self):
        """JSON 值核验失败。"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"status": "fail"}, f)
            temp_path = f.name
        
        try:
            ref = {
                "file": temp_path,
                "claim": "test",
                "machine_check": {
                    "type": "json_value",
                    "path": temp_path,
                    "key": "status",
                    "expected_contains": "success"
                }
            }
            passed, msg = verify_evidence_ref(ref)
            self.assertFalse(passed)
            self.assertIn("不包含", msg)
        finally:
            os.unlink(temp_path)
    
    def test_numeric_range_check(self):
        """数值范围核验。"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"value": 50}, f)
            temp_path = f.name
        
        try:
            ref = {
                "file": temp_path,
                "claim": "test",
                "machine_check": {
                    "type": "numeric_range",
                    "path": temp_path,
                    "key": "value",
                    "min": 0,
                    "max": 100
                }
            }
            passed, msg = verify_evidence_ref(ref)
            self.assertTrue(passed)
            
            # 超出范围
            ref["machine_check"]["max"] = 40
            passed, msg = verify_evidence_ref(ref)
            self.assertFalse(passed)
            self.assertIn("超出范围", msg)
        finally:
            os.unlink(temp_path)


class TestGapRules(unittest.TestCase):
    """缺口识别规则测试。"""
    
    def test_r1_honest_fail_detected(self):
        """R1: honest-FAIL 无后续单被检出。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "test_run_20261025"
            run_dir.mkdir()
            
            # 创建 verdict.json（有 FAIL）
            verdict = {
                "arms": {
                    "A": {"status": "fail", "reason": "error"},
                    "B": {"status": "pass"}
                }
            }
            with open(run_dir / "verdict.json", "w") as f:
                json.dump(verdict, f)
            
            runs_dir = Path(tmpdir)
            gap = rule_r1_honest_fail_no_followup(run_dir, verdict, runs_dir)
            
            self.assertIsNotNone(gap)
            self.assertEqual(gap["rule_id"], "R1")
            self.assertIn("A", gap["failed_arms"])
    
    def test_r1_no_fail_not_detected(self):
        """R1: 无 FAIL 不触发。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "test_run_20261025"
            run_dir.mkdir()
            
            verdict = {
                "arms": {
                    "A": {"status": "pass"},
                    "B": {"status": "pass"}
                }
            }
            
            runs_dir = Path(tmpdir)
            gap = rule_r1_honest_fail_no_followup(run_dir, verdict, runs_dir)
            self.assertIsNone(gap)
    
    def test_r1_has_followup_not_detected(self):
        """R1: 有后续 run 不触发。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir)
            
            # 创建原始 run
            run_dir = runs_dir / "test_run_20261025"
            run_dir.mkdir()
            verdict = {"arms": {"A": {"status": "fail"}}}
            with open(run_dir / "verdict.json", "w") as f:
                json.dump(verdict, f)
            
            # 创建后续 run
            followup_dir = runs_dir / "test_run_20261026"
            followup_dir.mkdir()
            
            gap = rule_r1_honest_fail_no_followup(run_dir, verdict, runs_dir)
            self.assertIsNone(gap, "有后续 run 不应触发 R1")
    
    def test_r4_prereg_untested_detected(self):
        """R4: prereg 假设未测被检出。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "test_run"
            run_dir.mkdir()
            
            prereg = """
            # 预注册
            判据 G1: 阻力符号正确
            判据 G2: 收敛性
            """
            
            verdict = {
                "gates": {
                    "G1": {"pass": True}
                    # G2 未测
                }
            }
            
            gap = rule_r4_prereg_untested(run_dir, prereg, verdict)
            self.assertIsNotNone(gap)
            self.assertEqual(gap["rule_id"], "R4")
            self.assertIn("G2", gap["untested_criteria"])


class TestProposalGeneration(unittest.TestCase):
    """Proposal 生成测试。"""
    
    def test_proposal_structure(self):
        """Proposal 结构完整。"""
        gap = {
            "rule_id": "R1",
            "rule_name": "honest-FAIL 无后续单",
            "evidence_dir": "/tmp/test_run",
            "detail": "1 臂 FAIL: A",
            "failed_arms": ["A"],
            "priority": "P0"
        }
        
        proposal = generate_proposal(gap, "20261025")
        
        # 检查必需字段
        required_fields = [
            "proposal_id", "title", "hypothesis", "evidence_refs",
            "why_now", "falsification_criteria", "controls",
            "budget_hint", "expected_artifacts", "gap_rule",
            "triggered_by", "generated_at", "generator_version"
        ]
        
        for field in required_fields:
            self.assertIn(field, proposal, f"缺少字段: {field}")
    
    def test_proposal_id_format(self):
        """Proposal ID 格式正确。"""
        gap = {
            "rule_id": "R1",
            "evidence_dir": "/tmp/test_case_20261025",
            "detail": "test"
        }
        
        proposal = generate_proposal(gap, "20261025")
        self.assertTrue(proposal["proposal_id"].startswith("20261025-"))
        self.assertIn("r1", proposal["proposal_id"])


class TestIntegration(unittest.TestCase):
    """集成测试。"""
    
    def test_scan_gaps_with_real_structure(self):
        """使用真实结构扫描缺口。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir)
            
            # 创建实验 run
            run_dir = runs_dir / "tau_test_20261025"
            run_dir.mkdir()
            
            # verdict.json（有 FAIL）
            verdict = {
                "arms": {
                    "A": {"status": "fail", "reason": "error"},
                    "B": {"status": "fail", "reason": "error"}
                }
            }
            with open(run_dir / "verdict.json", "w") as f:
                json.dump(verdict, f)
            
            # prereg.md
            prereg = """
            # 预注册
            判据 G1: 测试
            """
            with open(run_dir / "prereg.md", "w") as f:
                f.write(prereg)
            
            # 扫描
            gaps = scan_gaps(runs_dir, ["R1", "R4"])
            
            # 应该检出 R1（FAIL 无后续）
            r1_gaps = [g for g in gaps if g["rule_id"] == "R1"]
            self.assertGreater(len(r1_gaps), 0)


if __name__ == "__main__":
    unittest.main()
