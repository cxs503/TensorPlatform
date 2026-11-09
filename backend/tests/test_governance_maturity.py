#!/usr/bin/env python3
"""
Tests for Platform Governance Maturity Module
==============================================

≥15 tests including 3 negative controls:
- NC1: Empty report → auto-degrade (degradation_level=3)
- NC2: Forged sha256 → audit fails (verify_artifact returns False)
- NC3: Ledger update with invalid coverage → graceful error

Test categories:
  (a) Report Gate: 5 tests
  (b) Evidence Chain: 4 tests
  (c) Capability Ledger: 4 tests
  (d) Self-Healing: 4 tests
  (e) Integration: 3 tests
"""
import hashlib
import json
import sys
import os
import pytest

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.governance_maturity import (
    ReportElement,
    ReportGateResult,
    check_report_completeness,
    compute_sha256,
    compute_file_sha256,
    ProvenanceChain,
    ProvenanceRecord,
    CapabilityLevel,
    CapabilityLedger,
    CapabilityEntry,
    DegradationStrategy,
    SelfHealingManager,
    GovernanceManager,
)


# ============================================================================
# (a) Report Gate Tests (5 tests)
# ============================================================================

class TestReportGate:
    """Tests for 6-element report completeness check."""

    def _make_full_report(self) -> str:
        """Generate a markdown report containing all 6 elements."""
        return """
# 案例介绍

## 物理背景
本案例计算 Wigley 船模在 Fn=0.3 条件下的兴波阻力。
几何参数: L=3.0m, B=0.3m, T=0.125m, Cb=0.449。

## 方法
采用 Rankine 源方法，自由面线性化处理。
求解器: TensorBEM v2.1，网格 2000 panels。
工具链: Python 3.11 + NumPy + TensorBEM。

## 计算结果
| Fn | 系数 | 参考值 | 差异 |
|----|------|--------|------|
| 0.2 | 3.21 | 3.18 | 0.9% |
| 0.3 | 4.56 | 4.52 | 0.9% |

收敛性: 残差在 50 次迭代后降至 1e-6。

## 图表
图1: 波高云图 (contour plot)
图2: 阻力系数比较曲线 (comparison curve)

## 注意点
- 线性自由面假设在 Fn>0.35 时精度下降
- 网格密度对结果影响约 2%
- 局限: 未考虑粘性效应

## 误差判定
所有计算结果与实验基准偏差均 <3%，满足精度要求。
关键量: 总阻力偏差 0.9%，波高偏差 1.2%。
"""

    def test_full_report_passes(self):
        """All 6 elements present → PASS."""
        md = self._make_full_report()
        result = check_report_completeness(md, [{"name": "fig1.png"}])
        assert result.passed is True
        assert result.degradation_level == 0
        assert len(result.elements_missing) == 0
        assert len(result.elements_present) == 6

    def test_missing_one_element_degrades_1(self):
        """5/6 elements → degradation level 1."""
        # Create a report missing only error_judgment
        md = """
# 案例介绍

## 物理背景
本案例计算 Wigley 船模阻力。
几何参数: L=3.0m, B=0.3m。

## 方法
采用 Rankine 源方法。
求解器: TensorBEM v2.1。

## 计算结果
| Fn | 系数 |
|----|------|
| 0.2 | 3.21 |
| 0.3 | 4.56 |

## 图表
图1: 波高云图

## 注意点
局限: 未考虑粘性效应。
"""
        result = check_report_completeness(md, [{"name": "fig1.png"}])
        assert result.passed is False
        assert result.degradation_level == 1
        assert ReportElement.ERROR_JUDGMENT.value in result.elements_missing
        assert len(result.elements_present) == 5

    def test_missing_three_elements_degrades_2(self):
        """3/6 elements → degradation level 2 (3-4 present)."""
        md = """
# 案例介绍
物理背景: 计算船模阻力。
几何参数: L=3m, B=0.3m。

## 方法
采用 BEM 方法求解。

## 计算结果
总阻力系数 Cf=3.21。
"""
        result = check_report_completeness(md)
        # Should have: case_introduction, method, results = 3 elements
        assert result.passed is False
        assert result.degradation_level == 2
        assert len(result.elements_present) == 3

    def test_empty_report_critical_degradation(self):
        """NC1: Empty report → degradation level 3 (critical)."""
        result = check_report_completeness("")
        assert result.passed is False
        assert result.degradation_level == 3
        assert len(result.elements_present) == 0
        assert len(result.elements_missing) == 6

    def test_figures_from_list(self):
        """Figures detected from figures list even without markdown mention."""
        md = """
# 案例介绍
物理背景: 阻力计算。参数: L=3m。
方法: BEM。
结果: Cf=3.21。
注意点: 局限在低速。
误差判定: 偏差 <3%。
"""
        result = check_report_completeness(md, [{"name": "fig1.png", "caption": "test"}])
        assert ReportElement.FIGURES.value in result.elements_present


# ============================================================================
# (b) Evidence Chain Tests (4 tests)
# ============================================================================

class TestEvidenceChain:
    """Tests for SHA256 provenance tracking."""

    def test_register_and_verify(self):
        """Register artifact and verify sha256 matches."""
        chain = ProvenanceChain()
        data = "test artifact content"
        record = chain.register_artifact(
            artifact_id="art-001",
            artifact_path="/tmp/art-001.json",
            data=data,
            node_id="node-A",
            mission_id=100,
        )
        expected_hash = hashlib.sha256(data.encode()).hexdigest()
        assert record.sha256 == expected_hash
        assert chain.verify_artifact("art-001", data) is True

    def test_forged_sha256_fails_verification(self):
        """NC2: Forged data → verification fails."""
        chain = ProvenanceChain()
        chain.register_artifact(
            artifact_id="art-002",
            artifact_path="/tmp/art-002.json",
            data="original content",
            node_id="node-B",
            mission_id=101,
        )
        # Tampered data should fail
        assert chain.verify_artifact("art-002", "tampered content") is False

    def test_dependency_chain_integrity(self):
        """Full dependency chain has no broken links."""
        chain = ProvenanceChain()
        chain.register_artifact("root", "/root.json", "root data", "n1", 1)
        chain.register_artifact("mid", "/mid.json", "mid data", "n2", 1, upstream_deps=["root"])
        chain.register_artifact("leaf", "/leaf.json", "leaf data", "n3", 1, upstream_deps=["mid"])

        integrity = chain.check_chain_integrity("leaf")
        assert integrity["valid"] is True
        assert integrity["chain_length"] == 3
        assert len(integrity["broken_links"]) == 0

    def test_broken_dependency_detected(self):
        """Missing upstream dependency is detected."""
        chain = ProvenanceChain()
        chain.register_artifact(
            "orphan", "/orphan.json", "data", "n1", 1,
            upstream_deps=["nonexistent-parent"]
        )
        integrity = chain.check_chain_integrity("orphan")
        assert integrity["valid"] is False
        assert len(integrity["broken_links"]) == 1
        assert integrity["broken_links"][0]["missing_dep"] == "nonexistent-parent"


# ============================================================================
# (c) Capability Ledger Tests (4 tests)
# ============================================================================

class TestCapabilityLedger:
    """Tests for automatic capability ledger updates."""

    def test_register_and_retrieve(self):
        """Register a capability and retrieve it."""
        ledger = CapabilityLedger()
        entry = ledger.register_capability(
            capability_id="resistance",
            name="Wave Resistance",
            level=CapabilityLevel.REAL,
            domain="hydrodynamics",
            coverage=0.85,
            evidence_path="/evidence/resistance.json",
            mission_id=200,
        )
        assert entry.level == CapabilityLevel.REAL
        assert entry.coverage == 0.85

        retrieved = ledger.get_capability("resistance")
        assert retrieved is not None
        assert retrieved.name == "Wave Resistance"

    def test_coverage_summary(self):
        """Coverage summary aggregates by level and domain."""
        ledger = CapabilityLedger()
        ledger.register_capability("cap1", "Cap1", CapabilityLevel.REAL, "hydro", 0.9, "/e1", 1)
        ledger.register_capability("cap2", "Cap2", CapabilityLevel.MOCK, "hydro", 0.5, "/e2", 2)
        ledger.register_capability("cap3", "Cap3", CapabilityLevel.REAL, "structure", 0.7, "/e3", 3)

        summary = ledger.get_coverage_summary()
        assert summary["total_capabilities"] == 3
        assert summary["by_level"]["REAL"] == 2
        assert summary["by_level"]["MOCK"] == 1
        assert abs(summary["real_coverage_ratio"] - 2/3) < 0.01

    def test_invalid_coverage_raises(self):
        """NC3: Invalid coverage value → graceful error (ValueError)."""
        ledger = CapabilityLedger()
        with pytest.raises(ValueError, match="Coverage must be in"):
            ledger.register_capability(
                "bad", "Bad", CapabilityLevel.REAL, "test", 1.5, "/e", 1
            )

    def test_update_log_tracks_changes(self):
        """Update log records all create/update actions."""
        ledger = CapabilityLedger()
        ledger.register_capability("cap1", "Cap1", CapabilityLevel.STUB, "test", 0.1, "/e1", 1)
        ledger.register_capability("cap1", "Cap1", CapabilityLevel.REAL, "test", 0.9, "/e2", 2)

        log = ledger.get_update_log()
        assert len(log) == 2
        assert log[0]["action"] == "create"
        assert log[1]["action"] == "update"
        assert log[1]["level"] == "REAL"


# ============================================================================
# (d) Self-Healing Tests (4 tests)
# ============================================================================

class TestSelfHealing:
    """Tests for auto-degradation after consecutive failures."""

    def test_threshold_triggers_degradation(self):
        """3 consecutive failures → should_degrade returns True."""
        mgr = SelfHealingManager(threshold=3)
        for i in range(3):
            mgr.record_failure("node-X", 300, "timeout", f"fail {i}")

        assert mgr.should_degrade("node-X") is True

    def test_below_threshold_no_degradation(self):
        """2 consecutive failures → should_degrade returns False."""
        mgr = SelfHealingManager(threshold=3)
        mgr.record_failure("node-Y", 301, "error", "fail 1")
        mgr.record_failure("node-Y", 301, "error", "fail 2")

        assert mgr.should_degrade("node-Y") is False

    def test_success_resets_counter(self):
        """Success after failures resets consecutive count."""
        mgr = SelfHealingManager(threshold=3)
        mgr.record_failure("node-Z", 302, "error", "fail 1")
        mgr.record_failure("node-Z", 302, "error", "fail 2")
        mgr.record_success("node-Z")
        mgr.record_failure("node-Z", 302, "error", "fail 3")

        assert mgr.should_degrade("node-Z") is False
        assert mgr.get_node_status("node-Z")["consecutive_failures"] == 1

    def test_degradation_escalation(self):
        """Degradation strategy escalates through levels."""
        mgr = SelfHealingManager(threshold=3)

        # First degradation
        for i in range(3):
            mgr.record_failure("node-W", 303, "error", f"fail {i}")
        s1 = mgr.get_degradation_strategy("node-W")
        assert s1 == DegradationStrategy.REDUCE_PRECISION
        mgr.apply_degradation("node-W", 303, s1)

        # More failures → escalate
        for i in range(3):
            mgr.record_failure("node-W", 303, "error", f"fail {i+3}")
        s2 = mgr.get_degradation_strategy("node-W")
        assert s2 == DegradationStrategy.SWITCH_SOLVER

    def test_recovery_after_degradation(self):
        """Success after degradation marks recovery."""
        mgr = SelfHealingManager(threshold=3)
        for i in range(3):
            mgr.record_failure("node-R", 304, "error", f"fail {i}")
        mgr.apply_degradation("node-R", 304, DegradationStrategy.REDUCE_PRECISION)
        mgr.record_success("node-R")

        status = mgr.get_node_status("node-R")
        assert status["recovered"] is True
        assert status["consecutive_failures"] == 0


# ============================================================================
# (e) Integration Tests (3 tests)
# ============================================================================

class TestIntegration:
    """Integration tests for the full governance pipeline."""

    def test_full_node_output_pipeline(self):
        """Process node output: provenance + capabilities + healing reset."""
        gov = GovernanceManager()

        # Simulate upstream node
        gov.process_node_output(
            node_id="upstream",
            mission_id=400,
            artifact_id="up-art-1",
            artifact_path="/data/upstream.json",
            data="upstream result",
        )

        # Simulate downstream node depending on upstream
        result = gov.process_node_output(
            node_id="downstream",
            mission_id=400,
            artifact_id="down-art-1",
            artifact_path="/data/downstream.json",
            data="downstream result",
            upstream_deps=["up-art-1"],
            capabilities=[
                {
                    "capability_id": "test_cap",
                    "name": "Test Capability",
                    "level": "REAL",
                    "domain": "testing",
                    "coverage": 0.95,
                }
            ],
        )

        assert result["provenance"]["sha256"] is not None
        assert result["chain_integrity"]["valid"] is True
        assert len(result["capability_updates"]) == 1
        assert result["capability_updates"][0]["level"] == "REAL"

    def test_failure_then_recovery_pipeline(self):
        """Node fails 3 times → degrades → succeeds → recovery recorded."""
        gov = GovernanceManager()

        # 3 failures
        for i in range(3):
            result = gov.handle_node_failure(
                node_id="flaky-node",
                mission_id=401,
                error_type="convergence_error",
                error_message=f"Failed to converge (attempt {i+1})",
            )

        assert result["should_degrade"] is True
        assert result["recommended_strategy"] == "reduce_precision"
        assert result["degradation_applied"] is True

        # Recovery
        gov.healing.record_success("flaky-node")
        status = gov.healing.get_node_status("flaky-node")
        assert status["recovered"] is True

    def test_full_audit_returns_complete_state(self):
        """Full audit returns provenance, capabilities, and healing state."""
        gov = GovernanceManager()
        gov.process_node_output("n1", 500, "a1", "/a1.json", "data1")
        gov.ledger.register_capability("c1", "Cap1", CapabilityLevel.REAL, "test", 0.8, "/e", 500)
        gov.handle_node_failure("n2", 501, "err", "msg")

        audit = gov.full_audit()
        assert audit["provenance"]["total_artifacts"] == 1
        assert audit["capabilities"]["total_capabilities"] == 1
        assert "self_healing" in audit


# ============================================================================
# Run with pytest
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
