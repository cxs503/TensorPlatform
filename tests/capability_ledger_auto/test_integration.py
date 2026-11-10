"""Integration tests for capability_ledger_auto pipeline."""
import json
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capability_ledger_auto.registry import CapabilityRegistry, CapabilityDef
from capability_ledger_auto.scanner import EvidenceScanner, CodeEvidence
from capability_ledger_auto.evidence_chain import build_evidence_chain
from capability_ledger_auto.grader import GradeDeterminer

WORK_DIR = Path(__file__).resolve().parents[2]


class TestFullPipeline:
    def test_pipeline_with_synthetic_data(self, tmp_path):
        """端到端测试：创建合成数据，跑完整流水线。"""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        reports_dir = tmp_path / "tensor_reports"
        reports_dir.mkdir()
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # 创建代码
        (src_dir / "resistance.py").write_text(
            "def calculate_resistance(v, L, B):\n"
            "    '''Calculate resistance using Holtrop method.'''\n"
            "    return v * L * B * 0.001\n"
        )

        # 创建测试
        (tests_dir / "test_resistance.py").write_text(
            "def test_resistance_basic():\n"
            "    assert True\n\n"
            "def test_resistance_zero():\n"
            "    assert True\n"
        )

        # 创建报告（verdict=PASS，内容包含 resistance）
        report = {
            "scenario_key": "m9999",
            "verdict": "PASS",
            "capability": "resistance",
            "error_pct": 1.5,
        }
        (reports_dir / "m9999_report.json").write_text(json.dumps(report))

        # 创建理论文档
        (docs_dir / "resistance_theory.md").write_text(
            "# 阻力计算理论\n\n"
            "## 控制方程\n\n"
            "船舶阻力计算基于 Holtrop-1984 方法。\n"
            "总阻力 R_T = R_F + R_R + R_A\n"
        )

        # 创建配置文件（包含 resistance 关键词）
        (config_dir / "resistance_config.json").write_text(
            '{"method": "holtrop", "resistance_calc": true}'
        )

        # 创建数据文件（包含 resistance 关键词）
        (data_dir / "resistance_data.json").write_text(
            '{"ship_type": "container", "resistance_test": true}'
        )

        # 创建注册表
        cap = CapabilityDef(
            capability_id="test.resistance",
            label="Test Resistance",
            description="Test resistance calculation",
            expected_evidence_types={"theory", "code", "config", "data", "report"},
            code_patterns=["resistance", "holtrop"],
            test_patterns=["test_resistance"],
            report_patterns=["resistance"],
        )
        registry = CapabilityRegistry()
        registry.capabilities["test.resistance"] = cap

        # 运行流水线（真实执行测试文件：REAL 判级要求执行证据，不接受静态推断）
        scanner = EvidenceScanner(tmp_path, registry, exec_tests=True)
        scan_result = scanner.scan_capability(cap)

        chain = build_evidence_chain(cap, scan_result, tmp_path)

        determiner = GradeDeterminer()
        grade_result = determiner.determine(chain)

        # 验证扫描结果
        assert scan_result.has_implementation is True
        assert scan_result.has_tests is True
        assert scan_result.has_reports is True
        assert scan_result.test_pass_rate == 1.0
        assert scan_result.report_verdicts == ["PASS"]

        # 验证证据链
        assert chain.five_tuple.present_count >= 4

        assert scan_result.test_evidence_source == "executed"

        # 验证等级：五元齐全 + 测试真实执行通过 + 报告 PASS → REAL
        assert grade_result.grade == "REAL"
        assert grade_result.confidence >= 0.85

    def test_pipeline_with_mock_code(self, tmp_path):
        """测试 MOCK 代码降级。"""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        # 创建 MOCK 代码（使用 random，避免使用 placeholder 等 STUB 标记词）
        (src_dir / "resistance.py").write_text(
            "import numpy as np\n\n"
            "def calculate_resistance(v):\n"
            "    # [MOCK] hardcoded random output\n"
            "    return np.random.rand() * 100\n"
        )

        # 创建测试（会失败因为随机）
        (tests_dir / "test_resistance.py").write_text(
            "from src.resistance import calculate_resistance\n\n"
            "def test_resistance():\n"
            "    r = calculate_resistance(10)\n"
            "    assert r == 20.0  # Will fail due to random\n"
        )

        cap = CapabilityDef(
            capability_id="test.resistance",
            label="Test Resistance",
            description="Test",
            code_patterns=["resistance"],
            test_patterns=["test_resistance"],
        )
        registry = CapabilityRegistry()
        registry.capabilities["test.resistance"] = cap

        scanner = EvidenceScanner(tmp_path, registry)
        scan_result = scanner.scan_capability(cap)
        chain = build_evidence_chain(cap, scan_result, tmp_path)

        determiner = GradeDeterminer()
        grade_result = determiner.determine(chain)

        # 有代码但包含 MOCK 标记 → MOCK
        assert grade_result.grade == "MOCK"

    def test_pipeline_with_real_registry(self):
        """使用真实注册表文件运行扫描。"""
        registry = CapabilityRegistry.from_json(WORK_DIR / "config" / "capabilities.json")
        assert registry.count() == 21

        scanner = EvidenceScanner(WORK_DIR, registry)
        results = scanner.scan_all()

        assert len(results) == 21

        has_impl = sum(1 for r in results.values() if r.has_implementation)
        assert has_impl > 0, "Expected at least some capabilities with implementation"

        chains = {}
        for cap in registry.list_all():
            scan = results[cap.capability_id]
            chain = build_evidence_chain(cap, scan, WORK_DIR)
            chains[cap.capability_id] = chain

        determiner = GradeDeterminer()
        grade_results = determiner.determine_all(chains)
        summary = determiner.summary(grade_results)

        assert summary["total_capabilities"] == 21
        assert sum(summary["grade_tally"].values()) == 21

        print("\n=== Real Registry Scan Results ===")
        for grade, count in summary["grade_tally"].items():
            if count > 0:
                print(f"  {grade}: {count}")
