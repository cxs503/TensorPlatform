"""Tests for capability_ledger_auto scanner module."""
import json
import pytest
from pathlib import Path
import sys
import tempfile
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capability_ledger_auto.registry import CapabilityRegistry, CapabilityDef
from capability_ledger_auto.scanner import (
    EvidenceScanner, ScanResult, CodeEvidence, TestEvidence, ReportEvidence,
    sha256_text, sha256_file, detect_mock_markers, detect_stub_markers,
    scan_code_for_patterns, scan_tests_for_patterns, scan_reports_for_patterns,
)

WORK_DIR = Path(__file__).resolve().parents[2]


class TestHashFunctions:
    def test_sha256_text(self):
        h = sha256_text("hello")
        assert len(h) == 64
        assert h == sha256_text("hello")
        assert h != sha256_text("world")

    def test_sha256_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            h = sha256_file(Path(f.name))
            assert len(h) == 64
            os.unlink(f.name)


class TestMockStubDetection:
    def test_detect_mock_markers(self):
        assert detect_mock_markers("x = np.random.rand()") == ["np.random.rand"]
        assert detect_mock_markers("return mock_response") == ["mock_response"]
        assert detect_mock_markers("normal code") == []

    def test_detect_stub_markers(self):
        assert detect_stub_markers("raise NotImplementedError") == ["NotImplementedError"]
        assert detect_stub_markers("# stub") == ["# stub"]
        assert detect_stub_markers("normal code") == []


class TestScanCodeForPatterns:
    def test_scan_code_with_function(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "resistance_calc.py").write_text(
            "def calculate_resistance(v):\n    return v * 2\n"
        )

        results = scan_code_for_patterns(tmp_path, ["resistance"], subdir="src")
        assert len(results) >= 1
        assert any(e.match_type == "function" for e in results)

    def test_scan_code_with_class(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "stability.py").write_text(
            "class StabilityCalculator:\n    pass\n"
        )

        results = scan_code_for_patterns(tmp_path, ["stability"], subdir="src")
        assert len(results) >= 1
        assert any(e.match_type == "class" for e in results)

    def test_scan_code_no_match(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "other.py").write_text("def other_func(): pass\n")

        results = scan_code_for_patterns(tmp_path, ["resistance"], subdir="src")
        assert len(results) == 0

    def test_scan_code_empty_dir(self, tmp_path):
        results = scan_code_for_patterns(tmp_path, ["resistance"], subdir="src")
        assert len(results) == 0


class TestScanTestsForPatterns:
    def test_scan_tests_with_test_functions(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_resistance.py").write_text(
            "def test_resistance_calc():\n    assert True\n\n"
            "def test_resistance_holtrop():\n    assert True\n"
        )

        results = scan_tests_for_patterns(tmp_path, ["resistance"], subdir="tests")
        assert len(results) >= 1
        assert results[0].total_tests == 2
        assert results[0].pass_rate == 1.0

    def test_scan_tests_with_skip(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_resistance.py").write_text(
            "import pytest\n\n"
            "@pytest.mark.skip\n"
            "def test_resistance_skip():\n    assert True\n\n"
            "def test_resistance_ok():\n    assert True\n"
        )

        results = scan_tests_for_patterns(tmp_path, ["resistance"], subdir="tests")
        assert len(results) >= 1
        assert results[0].total_tests == 2
        assert results[0].pass_rate == 0.5

    def test_scan_tests_no_match(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_other.py").write_text("def test_other(): pass\n")

        results = scan_tests_for_patterns(tmp_path, ["resistance"], subdir="tests")
        assert len(results) == 0


class TestScanReportsForPatterns:
    def test_scan_reports_with_pass(self, tmp_path):
        reports_dir = tmp_path / "tensor_reports"
        reports_dir.mkdir()
        report = {
            "scenario_key": "m123",
            "verdict": "PASS",
            "capability": "resistance",
        }
        (reports_dir / "m123_report.json").write_text(json.dumps(report))

        results = scan_reports_for_patterns(tmp_path, ["resistance"], subdir="tensor_reports")
        assert len(results) >= 1
        assert results[0].verdict == "PASS"

    def test_scan_reports_with_fail(self, tmp_path):
        reports_dir = tmp_path / "tensor_reports"
        reports_dir.mkdir()
        report = {
            "scenario_key": "m456",
            "verdict": "FAIL",
            "capability": "resistance",
        }
        (reports_dir / "m456_report.json").write_text(json.dumps(report))

        results = scan_reports_for_patterns(tmp_path, ["resistance"], subdir="tensor_reports")
        assert len(results) >= 1
        assert results[0].verdict == "FAIL"

    def test_scan_reports_no_match(self, tmp_path):
        reports_dir = tmp_path / "tensor_reports"
        reports_dir.mkdir()
        report = {"scenario_key": "m789", "verdict": "PASS"}
        (reports_dir / "m789_report.json").write_text(json.dumps(report))

        results = scan_reports_for_patterns(tmp_path, ["resistance"], subdir="tensor_reports")
        assert len(results) == 0


class TestEvidenceScanner:
    def test_scan_capability(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "resistance.py").write_text("def calc_resistance(): pass\n")

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_resistance.py").write_text("def test_resistance(): assert True\n")

        reports_dir = tmp_path / "tensor_reports"
        reports_dir.mkdir()
        # 报告内容必须包含模式 "resistance" 才能匹配
        report = {"scenario_key": "m1", "verdict": "PASS", "capability": "resistance"}
        (reports_dir / "m1_report.json").write_text(json.dumps(report))

        cap = CapabilityDef(
            capability_id="test.resistance",
            label="Test Resistance",
            description="Test",
            code_patterns=["resistance"],
            test_patterns=["test_resistance"],
            report_patterns=["resistance"],
        )
        registry = CapabilityRegistry()
        registry.capabilities["test.resistance"] = cap

        scanner = EvidenceScanner(tmp_path, registry)
        result = scanner.scan_capability(cap)

        assert result.capability_id == "test.resistance"
        assert result.has_implementation is True
        assert result.has_tests is True
        assert result.has_reports is True
        assert result.test_pass_rate == 1.0

    def test_scan_all(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "resistance.py").write_text("def calc_resistance(): pass\n")

        cap1 = CapabilityDef(
            capability_id="test.resistance",
            label="Test Resistance",
            description="Test",
            code_patterns=["resistance"],
        )
        cap2 = CapabilityDef(
            capability_id="test.stability",
            label="Test Stability",
            description="Test",
            code_patterns=["stability"],
        )
        registry = CapabilityRegistry()
        registry.capabilities["test.resistance"] = cap1
        registry.capabilities["test.stability"] = cap2

        scanner = EvidenceScanner(tmp_path, registry)
        results = scanner.scan_all()

        assert len(results) == 2
        assert results["test.resistance"].has_implementation is True
        assert results["test.stability"].has_implementation is False
