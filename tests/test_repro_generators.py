"""tests/test_repro_generators.py —— 生成器框架测试。

测试覆盖：
  1. 生成器加载与运行
  2. 幂等性验证
  3. 入库门检查
  4. 盘点器功能
"""
import json
import os
import sys
import tempfile

import pytest

# 添加项目根目录到路径
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.repro_audit.generators import (
    run_generator, compare_outputs, sha256_file, WHITELIST_FIELDS
)
from tools.repro_audit.generators.inventory import (
    scan_inventory, report_gaps, scan_file_for_writers
)
from tools.repro_audit.generators.gate import IngestionGate


class TestGeneratorFramework:
    """生成器框架基础测试。"""
    
    def test_sha256_file(self, tmp_path):
        """测试文件 sha256 计算。"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        
        sha = sha256_file(str(test_file))
        assert len(sha) == 64
        assert sha == sha256_file(str(test_file))  # 幂等
    
    def test_compare_outputs_identical(self):
        """测试相同输出比对。"""
        output1 = {"result": {"value": 1.0, "count": 10}}
        output2 = {"result": {"value": 1.0, "count": 10}}
        
        result = compare_outputs(output1, output2)
        assert result["match"] is True
        assert len(result["differences"]) == 0
    
    def test_compare_outputs_different(self):
        """测试不同输出比对。"""
        output1 = {"result": {"value": 1.0}}
        output2 = {"result": {"value": 2.0}}
        
        result = compare_outputs(output1, output2)
        assert result["match"] is False
        assert len(result["differences"]) > 0
    
    def test_compare_outputs_whitelist(self):
        """测试白名单字段跳过。"""
        output1 = {"result": {"value": 1.0}, "timestamp": "2026-01-01T00:00:00Z"}
        output2 = {"result": {"value": 1.0}, "timestamp": "2026-01-02T00:00:00Z"}
        
        result = compare_outputs(output1, output2)
        assert result["match"] is True  # timestamp 在白名单里


class TestGeneratorExecution:
    """生成器执行测试。"""
    
    def test_run_benchmark_validation_generator(self):
        """测试 benchmark_validation 生成器。"""
        gen_path = os.path.join(ROOT, "tools/repro_audit/generators/gen_benchmark_validation.py")
        if not os.path.exists(gen_path):
            pytest.skip("Generator not found")
        
        result = run_generator(gen_path)
        assert "result" in result
        assert "module_sha256" in result
        assert "timestamp" in result
        assert result["result"]["total_cases"] > 0
    
    def test_run_pilot_verification_generator(self):
        """测试 pilot_verification 生成器。"""
        gen_path = os.path.join(ROOT, "tools/repro_audit/generators/gen_pilot_verification.py")
        if not os.path.exists(gen_path):
            pytest.skip("Generator not found")
        
        result = run_generator(gen_path)
        assert "result" in result
        assert result["result"]["pass_count"] > 0
    
    def test_generator_idempotency(self):
        """测试生成器幂等性（除白名单字段）。"""
        gen_path = os.path.join(ROOT, "tools/repro_audit/generators/gen_benchmark_validation.py")
        if not os.path.exists(gen_path):
            pytest.skip("Generator not found")
        
        result1 = run_generator(gen_path)
        result2 = run_generator(gen_path)
        
        # 比对结果（忽略白名单字段）
        comparison = compare_outputs(result1["result"], result2["result"])
        assert comparison["match"] is True, f"Generator not idempotent: {comparison['differences']}"


class TestInventory:
    """盘点器测试。"""
    
    def test_scan_file_for_writers(self, tmp_path):
        """测试文件扫描。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("""
import json
with open('output.json', 'w') as f:
    json.dump(data, f)
""")
        
        writers = scan_file_for_writers(str(test_file))
        assert len(writers) > 0
    
    def test_scan_inventory(self, tmp_path):
        """测试盘点器。"""
        # 创建模拟 mission 目录
        for i in range(5):
            mdir = tmp_path / f"m{i}"
            mdir.mkdir()
            art_dir = mdir / "artifacts"
            art_dir.mkdir()
            (art_dir / "result.json").write_text('{"value": 1}')
        
        inv = scan_inventory(str(tmp_path), n_sample=5)
        assert inv["total"] == 5
        assert "nodes" in inv
        assert "generator_registry" in inv
    
    def test_report_gaps(self):
        """测试缺口报告。"""
        inv = {
            "total": 10,
            "covered": 3,
            "uncovered": 7,
            "coverage_rate": 0.3,
            "nodes": [
                {"mission_id": 1, "artifacts": ["a.json"], "generators": [], "covered": False},
                {"mission_id": 2, "artifacts": ["b.json"], "generators": ["gen.py"], "covered": True},
            ]
        }
        
        gaps = report_gaps(inv)
        assert gaps["total_gaps"] == 1
        assert gaps["gaps"][0]["mission_id"] == 1


class TestIngestionGate:
    """入库门测试。"""
    
    def test_gate_no_generator(self, tmp_path):
        """测试无生成器时门失败。"""
        # 创建报告
        report = {"artifacts/test/result.json": {"value": 1.0}}
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(report))
        
        gate = IngestionGate(str(tmp_path))
        result = gate.check_report(str(report_path))
        
        # 应该失败（无生成器）
        assert result["passed"] is False or result["traceable"] == 0
    
    def test_gate_with_generator(self, tmp_path):
        """测试有生成器时门通过。"""
        # 创建生成器目录
        gen_dir = tmp_path / "tools" / "repro_audit" / "generators"
        gen_dir.mkdir(parents=True)
        
        # 创建生成器
        gen_file = gen_dir / "gen_test.py"
        gen_file.write_text("""
OUTPUT_PATH = "artifacts/test/result.json"

def generate():
    return {"value": 1.0}
""")
        
        # 创建工件
        art_dir = tmp_path / "artifacts" / "test"
        art_dir.mkdir(parents=True)
        (art_dir / "result.json").write_text('{"value": 1.0}')
        
        # 创建报告
        report = {"artifacts/test/result.json": {"value": 1.0}}
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(report))
        
        gate = IngestionGate(str(tmp_path))
        result = gate.check_report(str(report_path))
        
        # 应该通过
        assert result["traceable"] > 0


class TestGeneratorCount:
    """生成器数量测试。"""
    
    def test_at_least_24_generators(self):
        """测试至少有 24 个生成器。"""
        gen_dir = os.path.join(ROOT, "tools/repro_audit/generators")
        generators = [f for f in os.listdir(gen_dir) 
                     if f.startswith("gen_") and f.endswith(".py")]
        
        assert len(generators) >= 24, f"Only {len(generators)} generators found, need >= 24"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
