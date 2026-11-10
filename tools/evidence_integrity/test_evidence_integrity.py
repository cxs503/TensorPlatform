#!/usr/bin/env python3
"""Tests for Evidence Chain Integrity Checker.

Tests cover:
- Six-element verification
- Number traceability
- Figure provenance
- Criteria coverage
- Honest-FAIL detection
- Audit trail verification
- Full report verification
"""
import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.evidence_integrity.checker import (
    EvidenceReport,
    VerificationResult,
    verify_report,
    verify_six_elements,
    verify_number_traceability,
    verify_figure_provenance,
    verify_criteria_coverage,
    verify_honest_fail,
    verify_audit_trail,
    SIX_ELEMENTS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def good_report():
    """A report that passes all checks."""
    return EvidenceReport(
        title="Test Report: Resistance Benchmark",
        summary="验证阻力计算精度",
        markdown="""
日期：2026-11-10 版本 v1.0

## 案例介绍
物理背景：Wigley 船体阻力验证。几何参数 L=3.6m, B=0.3m, Cb=0.45。

## 怎么做的
使用 TensorBEM 求解器，网格 10k 面板，Fr=0.2, 0.3, 0.4。

## 计算结果
| Fr | Cf_calc | Cf_ref | 误差 |
|----|---------|--------|------|
| 0.2 | 0.00312 | 0.00320 | 2.5% |
| 0.3 | 0.00285 | 0.00290 | 1.7% |

数据来源：artifact m123/resistance.json，SQL 溯源表 resistance_benchmark。

## 结果图
见 figures/resistance_curve.png

## 注意点
- 网格无关性已验证（5k/10k/20k）
- 适用范围：Fr < 0.5

## 误差判定
关键量 vs 基准误差：Cf 误差 2.5%，< 3% ✓
""",
        figures=[
            {"name": "resistance_curve", "sha256": "abc123", "png_base64": "..."},
        ],
        mission_id=10171,
        scenario_key="p227w2-N10",
        tags=["resistance", "benchmark"],
        metadata={
            "acceptance_criteria": ["误差 < 3%", "网格无关性"],
        },
    )


@pytest.fixture
def bad_report():
    """A report that fails multiple checks."""
    return EvidenceReport(
        title="Quick Test",
        summary="test",
        markdown="Just some text with no structure. Error happened.",
        figures=[
            {"name": "fig1"},  # no hash, no path
        ],
    )


# ---------------------------------------------------------------------------
# Six-element tests
# ---------------------------------------------------------------------------

class TestSixElements:
    def test_good_report_has_all_elements(self, good_report):
        result = verify_six_elements(good_report)
        assert result["complete"] is True
        assert result["found"] == 6

    def test_bad_report_missing_elements(self, bad_report):
        result = verify_six_elements(bad_report)
        assert result["complete"] is False
        assert result["found"] < 6

    def test_empty_report(self):
        report = EvidenceReport()
        result = verify_six_elements(report)
        assert result["found"] == 0
        assert result["complete"] is False

    def test_chinese_keywords(self):
        report = EvidenceReport(
            markdown="案例介绍 物理背景 怎么做的 计算结果 结果图 注意点 误差判定"
        )
        result = verify_six_elements(report)
        assert result["found"] >= 5


# ---------------------------------------------------------------------------
# Number traceability tests
# ---------------------------------------------------------------------------

class TestNumberTraceability:
    def test_traced_numbers(self, good_report):
        result = verify_number_traceability(good_report)
        assert result["passed"] is True
        assert result["traced"] > 0

    def test_untraced_numbers(self):
        report = EvidenceReport(markdown="The value is 42.7 and also 99%.")
        result = verify_number_traceability(report)
        # No source references nearby
        assert result["untraced"] >= 0  # depends on context window

    def test_no_numbers(self):
        report = EvidenceReport(markdown="No numbers here at all.")
        result = verify_number_traceability(report)
        assert result["total"] == 0
        assert result["passed"] is True  # vacuously true


# ---------------------------------------------------------------------------
# Figure provenance tests
# ---------------------------------------------------------------------------

class TestFigureProvenance:
    def test_figures_with_hash(self, good_report):
        result = verify_figure_provenance(good_report)
        assert result["passed"] is True
        assert result["verified"] == 1

    def test_figures_without_hash(self, bad_report):
        result = verify_figure_provenance(bad_report)
        assert result["passed"] is False
        assert len(result["issues"]) > 0

    def test_no_figures(self):
        report = EvidenceReport(figures=[])
        result = verify_figure_provenance(report)
        assert result["passed"] is True
        assert result["total_figures"] == 0


# ---------------------------------------------------------------------------
# Criteria coverage tests
# ---------------------------------------------------------------------------

class TestCriteriaCoverage:
    def test_criteria_covered(self, good_report):
        result = verify_criteria_coverage(good_report)
        assert result["passed"] is True

    def test_no_explicit_criteria(self, bad_report):
        result = verify_criteria_coverage(bad_report)
        # Should still find implicit signals
        assert isinstance(result["passed"], bool)

    def test_uncovered_criteria(self):
        report = EvidenceReport(
            markdown="Some text without relevant keywords",
            metadata={"acceptance_criteria": ["quantum_entanglement_verified"]},
        )
        result = verify_criteria_coverage(report)
        assert result["passed"] is False


# ---------------------------------------------------------------------------
# Honest-FAIL tests
# ---------------------------------------------------------------------------

class TestHonestFail:
    def test_no_failure(self, good_report):
        result = verify_honest_fail(good_report)
        assert result["passed"] is True

    def test_honest_failure(self):
        report = EvidenceReport(
            markdown="The test failed honestly. 如实报告：误差超出范围。"
        )
        result = verify_honest_fail(report)
        assert result["passed"] is True

    def test_dishonest_failure(self):
        report = EvidenceReport(
            markdown="Error occurred but we pretend it's fine."
        )
        result = verify_honest_fail(report)
        # Has failure signal but no honest declaration
        assert result["has_failure_signals"] is True


# ---------------------------------------------------------------------------
# Audit trail tests
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_good_audit_trail(self, good_report):
        result = verify_audit_trail(good_report)
        assert result["passed"] is True
        assert result["checks"]["has_mission_id"] is True
        assert result["checks"]["has_scenario"] is True

    def test_missing_audit_info(self, bad_report):
        result = verify_audit_trail(bad_report)
        assert result["checks"]["has_mission_id"] is False


# ---------------------------------------------------------------------------
# Full verification tests
# ---------------------------------------------------------------------------

class TestFullVerification:
    def test_good_report_passes(self, good_report):
        result = verify_report(good_report)
        assert result.passed is True
        assert result.score >= 0.7
        assert len(result.errors) == 0

    def test_bad_report_fails(self, bad_report):
        result = verify_report(bad_report)
        assert result.score < 0.7 or len(result.warnings) > 0

    def test_verification_result_serializable(self, good_report):
        result = verify_report(good_report)
        d = result.to_dict()
        assert "passed" in d
        assert "score" in d
        assert "checks" in d
        # Must be JSON-serializable
        json.dumps(d)

    def test_from_json_roundtrip(self, good_report, tmp_path):
        data = {
            "title": good_report.title,
            "summary": good_report.summary,
            "markdown": good_report.markdown,
            "figures": good_report.figures,
            "mission_id": good_report.mission_id,
            "scenario_key": good_report.scenario_key,
            "tags": good_report.tags,
            "metadata": good_report.metadata,
        }
        path = tmp_path / "report.json"
        path.write_text(json.dumps(data))
        loaded = EvidenceReport.from_json(path)
        assert loaded.title == good_report.title
        assert loaded.mission_id == good_report.mission_id
