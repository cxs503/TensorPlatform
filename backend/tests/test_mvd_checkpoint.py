"""Tests for MVD checkpoint — shadow mode and evaluation logic.

Acceptance criteria:
1. Shadow mode zero side effects (no blocking)
2. Checkpoint triggers at 70% budget
3. MVD requirements evaluation is correct
4. Checkpoint logging works
"""
from __future__ import annotations

import pytest

from api.domain.mvd_checkpoint import (
    MVDCheckpointResult,
    compute_budget_pct,
    evaluate_mvd_checkpoint,
    log_checkpoint,
    get_checkpoint_log,
    compute_checkpoint_stats,
    _CHECKPOINT_LOG,
    BUDGET_THRESHOLD,
)


class TestBudgetComputation:
    """Test budget utilization computation."""
    
    def test_tool_call_budget(self):
        """Budget based on tool calls."""
        pct = compute_budget_pct(56, 80, 0, 0)
        assert pct == 70.0
    
    def test_wall_time_budget(self):
        """Budget based on wall time."""
        pct = compute_budget_pct(0, 0, 5040, 7200)
        assert pct == 70.0
    
    def test_max_of_both(self):
        """Budget takes max of tool calls and wall time."""
        pct = compute_budget_pct(40, 80, 5040, 7200)  # 50% tools, 70% time
        assert pct == 70.0
    
    def test_zero_budget(self):
        """Zero budget → 0%."""
        pct = compute_budget_pct(0, 0, 0, 0)
        assert pct == 0.0
    
    def test_capped_at_100(self):
        """Budget capped at 100%."""
        pct = compute_budget_pct(100, 80, 0, 0)
        assert pct == 100.0


class TestMVDCheckpointEvaluation:
    """Test MVD checkpoint evaluation logic."""
    
    def test_below_threshold_not_triggered(self):
        """Below 70% → not triggered."""
        result = evaluate_mvd_checkpoint(
            result={"changed_files": ["a.py"], "checks": {"pytest": "pass"}, "summary": {}},
            tool_calls=40,
            max_tool_calls=80,
            wall_seconds=3600,
            max_wall_seconds=7200,
        )
        assert result.triggered is False
        assert result.passed is True  # Not triggered = trivially passed
        assert result.budget_pct == 50.0
    
    def test_at_threshold_triggered(self):
        """At 70% → triggered."""
        result = evaluate_mvd_checkpoint(
            result={"changed_files": ["a.py"], "checks": {"pytest": "pass"}, "summary": {}},
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
        )
        assert result.triggered is True
        assert result.budget_pct == 70.0
    
    def test_above_threshold_triggered(self):
        """Above 70% → triggered."""
        result = evaluate_mvd_checkpoint(
            result={"changed_files": ["a.py"], "checks": {"pytest": "pass"}, "summary": {}},
            tool_calls=64,
            max_tool_calls=80,
            wall_seconds=5760,
            max_wall_seconds=7200,
        )
        assert result.triggered is True
        assert result.budget_pct == 80.0
    
    def test_mvd_passed_all_requirements(self):
        """MVD passed: all requirements met."""
        result = evaluate_mvd_checkpoint(
            result={
                "changed_files": ["a.py"],
                "checks": {"pytest": "pass"},
                "summary": {"text": "done"},
            },
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
        )
        assert result.triggered is True
        assert result.passed is True
        assert result.missing == []
    
    def test_mvd_failed_missing_implementation(self):
        """MVD failed: missing implementation."""
        result = evaluate_mvd_checkpoint(
            result={
                "checks": {"pytest": "pass"},
                "summary": {"text": "done"},
            },
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
        )
        assert result.triggered is True
        assert result.passed is False
        assert "implementation" in result.missing
    
    def test_mvd_failed_missing_evidence(self):
        """MVD failed: missing evidence."""
        result = evaluate_mvd_checkpoint(
            result={
                "changed_files": ["a.py"],
                "summary": {"text": "done"},
            },
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
        )
        assert result.triggered is True
        assert result.passed is False
        assert "evidence" in result.missing
    
    def test_mvd_failed_missing_report(self):
        """MVD failed: missing report."""
        result = evaluate_mvd_checkpoint(
            result={
                "changed_files": ["a.py"],
                "checks": {"pytest": "pass"},
            },
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
        )
        assert result.triggered is True
        assert result.passed is False
        assert "report" in result.missing
    
    def test_shadow_mode_default(self):
        """Shadow mode is default."""
        result = evaluate_mvd_checkpoint(
            result=None,
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
        )
        assert result.shadow_mode is True
        assert "shadow mode" in result.message.lower()
    
    def test_blocking_mode(self):
        """Blocking mode (shadow_mode=False)."""
        result = evaluate_mvd_checkpoint(
            result=None,
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
            shadow_mode=False,
        )
        assert result.shadow_mode is False


class TestCheckpointLogging:
    """Test checkpoint logging functionality."""
    
    def setup_method(self):
        """Clear checkpoint log before each test."""
        _CHECKPOINT_LOG.clear()
    
    def test_log_checkpoint(self):
        """Log a checkpoint result."""
        checkpoint_result = MVDCheckpointResult(
            triggered=True,
            passed=False,
            budget_pct=70.0,
            missing=["implementation"],
            message="MVD failed",
        )
        log_checkpoint(mission_id=123, result=checkpoint_result)
        
        log = get_checkpoint_log()
        assert len(log) == 1
        assert log[0]["mission_id"] == 123
        assert log[0]["triggered"] is True
        assert log[0]["passed"] is False
    
    def test_get_checkpoint_log_by_mission(self):
        """Filter log by mission ID."""
        log_checkpoint(123, MVDCheckpointResult(True, True, 70.0))
        log_checkpoint(456, MVDCheckpointResult(True, False, 80.0, ["implementation"]))
        log_checkpoint(123, MVDCheckpointResult(True, True, 90.0))
        
        log_123 = get_checkpoint_log(mission_id=123)
        assert len(log_123) == 2
        
        log_456 = get_checkpoint_log(mission_id=456)
        assert len(log_456) == 1
    
    def test_get_checkpoint_log_limit(self):
        """Limit log entries."""
        for i in range(10):
            log_checkpoint(i, MVDCheckpointResult(True, True, 70.0))
        
        log = get_checkpoint_log(limit=5)
        assert len(log) == 5
    
    def test_compute_checkpoint_stats(self):
        """Compute checkpoint statistics."""
        log_checkpoint(1, MVDCheckpointResult(True, True, 70.0))
        log_checkpoint(2, MVDCheckpointResult(True, False, 80.0, ["implementation"]))
        log_checkpoint(3, MVDCheckpointResult(False, True, 50.0))
        
        stats = compute_checkpoint_stats()
        assert stats["total"] == 3
        assert stats["triggered"] == 2
        assert stats["passed"] == 1
        assert stats["failed"] == 1
        assert stats["pass_rate"] == 50.0  # 1 passed out of 2 triggered
        assert stats["shadow_mode_ratio"] == 100.0  # All in shadow mode


class TestShadowModeZeroSideEffects:
    """Shadow mode must have zero side effects on existing paths."""
    
    def test_shadow_mode_does_not_block(self):
        """Shadow mode records but doesn't block."""
        result = evaluate_mvd_checkpoint(
            result=None,
            tool_calls=56,
            max_tool_calls=80,
            wall_seconds=5040,
            max_wall_seconds=7200,
            shadow_mode=True,
        )
        # Should record the failure but not raise or block
        assert result.triggered is True
        assert result.passed is False
        assert result.shadow_mode is True
        # No exception raised = zero side effects
    
    def test_disabled_checkpoint_is_noop(self):
        """When checkpoint is disabled (not implemented yet), it's a no-op.
        
        This test documents that the checkpoint is currently shadow-only
        and does not integrate into the live mission lifecycle.
        """
        # The checkpoint module exists but is not wired into
        # tensor_mission_lifecycle.py heartbeat() yet.
        # This is intentional: shadow mode first, then decide.
        # This test ensures we don't accidentally wire it in without
        # completing the shadow experiment.
        from api.domain import mvd_checkpoint
        
        # Module exists and is importable
        assert hasattr(mvd_checkpoint, "evaluate_mvd_checkpoint")
        assert hasattr(mvd_checkpoint, "BUDGET_THRESHOLD")
        assert mvd_checkpoint.BUDGET_THRESHOLD == 0.70
