"""
Tests for mission_failure_analysis module.

Covers:
- Classification rules (pattern matching)
- Recoverability logic
- Recovery suggestions
- Edge cases (empty result, unknown categories)
- Data model correctness
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from mission_failure_analysis import (
    CATEGORY_RULES,
    RECOVERABILITY,
    RECOVERY_SUGGESTIONS,
    classify_failure,
    is_recoverable,
    get_recovery_suggestion,
    MissionRecord,
    AnalysisResult,
    generate_report_md,
    generate_recommendations,
)


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestClassifyFailure:
    """Test failure classification based on result text."""

    def test_budget_exhausted(self):
        result = json.dumps({"evidence": {"checks": {"fail_stage": "budget_exhausted"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "budget_exhausted"
        assert stage == "budget_exhausted"

    def test_wall_exhausted(self):
        result = json.dumps({"evidence": {"checks": {"fail_stage": "wall_exhausted"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "wall_exhausted"
        assert stage == "wall_exhausted"

    def test_no_progress(self):
        result = json.dumps({"evidence": {"checks": {"fail_stage": "no_progress"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "no_progress"
        assert stage == "no_progress"

    def test_agent_lost(self):
        result = json.dumps({"stop_condition": "agent_lost"}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "agent_lost"
        assert stage == ""

    def test_worktree_empty(self):
        result = json.dumps({"evidence": {"checks": {"reason": "工作树无任何改动"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "worktree_empty"
        assert stage == ""

    def test_llm_error(self):
        result = json.dumps({"evidence": {"checks": {"reason": "llm_error"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "llm_error"
        assert stage == ""

    def test_runtime_error(self):
        result = json.dumps({"error": "RuntimeError: division by zero"}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "runtime_error"
        assert stage == ""

    def test_timeout(self):
        result = json.dumps({"evidence": {"checks": {"reason": "timeout"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "timeout"
        assert stage == ""

    def test_revoked(self):
        result = json.dumps({"status": "revoked"}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "revoked"
        assert stage == ""

    def test_stopped(self):
        result = json.dumps({"status": "stopped"}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "stopped"
        assert stage == ""

    def test_empty_result(self):
        cat, stage = classify_failure("")
        assert cat == "unknown"
        assert stage == ""

    def test_none_result(self):
        cat, stage = classify_failure(None)
        assert cat == "unknown"
        assert stage == ""

    def test_unknown_category(self):
        result = json.dumps({"some": "random text"}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "other"

    def test_combined_fail_stage(self):
        result = json.dumps({"evidence": {"checks": {"fail_stage": "budget_exhausted+forced_finish"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "budget_exhausted"
        assert stage == "budget_exhausted+forced_finish"

    def test_case_insensitive(self):
        result = json.dumps({"evidence": {"checks": {"reason": "BUDGET_EXHAUSTED"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "budget_exhausted"

    def test_chinese_patterns(self):
        result = json.dumps({"evidence": {"checks": {"reason": "撤销"}}}, ensure_ascii=False)
        cat, stage = classify_failure(result)
        assert cat == "revoked"


# ---------------------------------------------------------------------------
# Recoverability tests
# ---------------------------------------------------------------------------

class TestRecoverability:
    """Test recoverability logic."""

    def test_recoverable_categories(self):
        recoverable_cats = [c for c, v in RECOVERABILITY.items() if v]
        assert "budget_exhausted" in recoverable_cats
        assert "wall_exhausted" in recoverable_cats
        assert "no_progress" in recoverable_cats
        assert "agent_lost" in recoverable_cats
        assert "worktree_empty" in recoverable_cats
        assert "llm_error" in recoverable_cats
        assert "timeout" in recoverable_cats

    def test_non_recoverable_categories(self):
        non_recoverable = [c for c, v in RECOVERABILITY.items() if not v]
        assert "runtime_error" in non_recoverable
        assert "revoked" in non_recoverable
        assert "stopped" in non_recoverable

    def test_is_recoverable_true(self):
        assert is_recoverable("budget_exhausted") is True
        assert is_recoverable("wall_exhausted") is True
        assert is_recoverable("no_progress") is True

    def test_is_recoverable_false(self):
        assert is_recoverable("runtime_error") is False
        assert is_recoverable("revoked") is False
        assert is_recoverable("stopped") is False

    def test_is_recoverable_unknown(self):
        assert is_recoverable("unknown") is False


# ---------------------------------------------------------------------------
# Recovery suggestion tests
# ---------------------------------------------------------------------------

class TestRecoverySuggestions:
    """Test recovery suggestion generation."""

    def test_suggestion_exists_for_all(self):
        """All categories should have documented patterns."""
        for cat in RECOVERABILITY:
            suggestion = get_recovery_suggestion(cat)
            assert suggestion, f"No suggestion for {cat}"
            assert len(suggestion) > 10

    def test_specific_suggestions(self):
        assert "budget" in get_recovery_suggestion("budget_exhausted").lower()
        assert "wall" in get_recovery_suggestion("wall_exhausted").lower()
        assert "goal" in get_recovery_suggestion("no_progress").lower()
        assert "heartbeat" in get_recovery_suggestion("agent_lost").lower()


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------

class TestDataModel:
    """Test data model correctness."""

    def test_mission_record_creation(self):
        record = MissionRecord(
            id=1,
            status="failed",
            repo="TensorLBM",
            model="DeepSeek-V4-Flash-0731",
            goal="Test mission",
            result={"test": True},
            created_at="2026-11-07T00:00:00Z",
            finished_at="2026-11-07T01:00:00Z",
            duration_s=3600,
            fail_category="budget_exhausted",
            fail_stage="budget_exhausted",
            recoverable=True,
            recovery_suggestion="Increase budget",
        )
        assert record.id == 1
        assert record.status == "failed"
        assert record.recoverable is True

    def test_analysis_result_creation(self):
        result = AnalysisResult(
            total_missions=100,
            terminal_missions=100,
            terminal_distribution={"succeeded": 80, "failed": 20},
            failed_count=20,
            failure_categories={"budget_exhausted": 10, "no_progress": 10},
            failure_by_repo={"TensorLBM": 15, "TensorFEM": 5},
            failure_by_model={"ModelA": 12, "ModelB": 8},
            failure_by_stage={"budget_exhausted": 10, "no_progress": 10},
            recoverable_count=15,
            recoverable_rate=0.75,
            category_recovery={
                "budget_exhausted": {"count": 10, "recoverable": 10, "recoverable_rate": 1.0, "suggestion": "Increase budget"},
                "no_progress": {"count": 10, "recoverable": 5, "recoverable_rate": 0.5, "suggestion": "Improve goals"},
            },
            recommendations=["Rec 1", "Rec 2"],
            analysis_timestamp="2026-11-07T00:00:00Z",
            sample_missions=[{"id": 1, "repo": "TensorLBM", "category": "budget_exhausted", "recoverable": True}],
        )
        assert result.total_missions == 100
        assert result.failed_count == 20
        assert result.recoverable_rate == 0.75


# ---------------------------------------------------------------------------
# Report generation tests
# ---------------------------------------------------------------------------

class TestReportGeneration:
    """Test report generation."""

    def test_generate_report_md(self):
        result = AnalysisResult(
            total_missions=100,
            terminal_missions=100,
            terminal_distribution={"succeeded": 80, "failed": 20},
            failed_count=20,
            failure_categories={"budget_exhausted": 10, "no_progress": 10},
            failure_by_repo={"TensorLBM": 15, "TensorFEM": 5},
            failure_by_model={"ModelA": 12, "ModelB": 8},
            failure_by_stage={"budget_exhausted": 10, "no_progress": 10},
            recoverable_count=15,
            recoverable_rate=0.75,
            category_recovery={
                "budget_exhausted": {"count": 10, "recoverable": 10, "recoverable_rate": 1.0, "suggestion": "Increase budget"},
                "no_progress": {"count": 10, "recoverable": 5, "recoverable_rate": 0.5, "suggestion": "Improve goals"},
            },
            recommendations=["Rec 1", "Rec 2"],
            analysis_timestamp="2026-11-07T00:00:00Z",
            sample_missions=[{"id": 1, "repo": "TensorLBM", "model": "ModelA", "category": "budget_exhausted", "stage": "budget_exhausted", "recoverable": True}],
        )
        md = generate_report_md(result)
        
        # Check required sections
        assert "# Mission Failure Root Cause Classification & Recovery Analysis" in md
        assert "## 1. Overview" in md
        assert "## 2. Failure Root Cause Classification" in md
        assert "## 3. Failure by Repository" in md
        assert "## 4. Failure by Model" in md
        assert "## 5. Recovery Analysis" in md
        assert "## 6. Re-dispatch Strategy Recommendations" in md
        assert "## 7. Sample Failed Missions" in md
        assert "## 8. Limitations" in md
        
        # Check data
        assert "100" in md
        assert "20" in md
        assert "75.0%" in md
        assert "budget_exhausted" in md
        assert "no_progress" in md

    def test_recommendations_generation(self):
        cat_dist = {"budget_exhausted": 50, "no_progress": 30, "agent_lost": 20}
        recs = generate_recommendations(cat_dist, 0.7, {"TensorLBM": 30}, {"ModelA": 25})
        assert len(recs) >= 3
        assert any("recoverable" in r.lower() for r in recs)
        assert any("budget" in r.lower() for r in recs)


# ---------------------------------------------------------------------------
# Classification rules documentation tests
# ---------------------------------------------------------------------------

class TestClassificationRules:
    """Test that classification rules are well-documented."""

    def test_rules_are_documented(self):
        """All categories should have documented patterns."""
        for cat, patterns in CATEGORY_RULES:
            assert cat, f"Category name is empty"
            assert patterns, f"No patterns for {cat}"
            assert len(patterns) > 0, f"No patterns for {cat}"

    def test_all_categories_have_recovery_info(self):
        """All categories should have recovery info."""
        for cat in RECOVERABILITY:
            assert cat in RECOVERY_SUGGESTIONS, f"No suggestion for {cat}"

    def test_rules_count(self):
        """Should have at least 8 categories."""
        assert len(CATEGORY_RULES) >= 8

    def test_rules_no_duplicates(self):
        """No duplicate category names."""
        cats = [c for c, _ in CATEGORY_RULES]
        assert len(cats) == len(set(cats)), f"Duplicate categories: {cats}"


# ---------------------------------------------------------------------------
# Integration test (mocked DB)
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration tests with mocked database."""

    @patch("mission_failure_analysis.psycopg.connect")
    def test_get_terminal_missions_mocked(self, mock_connect):
        """Test mission fetching with mocked DB."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "failed", "TensorLBM", "ModelA", "Test goal", '{"evidence": {"checks": {"fail_stage": "budget_exhausted"}}}', "2026-11-07", "2026-11-07", 3600),
            (2, "succeeded", "TensorFEM", "ModelB", "Success goal", None, "2026-11-07", "2026-11-07", 1800),
        ]
        mock_connect.return_value.cursor.return_value = mock_cursor
        
        from mission_failure_analysis import get_terminal_missions
        missions = get_terminal_missions(limit=10)
        
        assert len(missions) == 2
        assert missions[0]["status"] == "failed"
        assert missions[1]["status"] == "succeeded"

    def test_analyze_missions(self):
        """Test full analysis pipeline."""
        missions = [
            {"id": 1, "status": "failed", "repo": "TensorLBM", "model": "ModelA", "goal": "Test", "result": {"evidence": {"checks": {"fail_stage": "budget_exhausted"}}}, "created_at": "2026-11-07", "finished_at": "2026-11-07", "duration_s": 3600},
            {"id": 2, "status": "failed", "repo": "TensorFEM", "model": "ModelB", "goal": "Test", "result": {"evidence": {"checks": {"fail_stage": "no_progress"}}}, "created_at": "2026-11-07", "finished_at": "2026-11-07", "duration_s": 1800},
            {"id": 3, "status": "succeeded", "repo": "TensorLBM", "model": "ModelA", "goal": "Success", "result": None, "created_at": "2026-11-07", "finished_at": "2026-11-07", "duration_s": 900},
        ]
        
        from mission_failure_analysis import analyze_missions
        result = analyze_missions(missions)
        
        assert result.total_missions == 3
        assert result.failed_count == 2
        assert result.recoverable_count == 2  # Both failed are recoverable
        assert result.recoverable_rate == 1.0
        assert "budget_exhausted" in result.failure_categories
        assert "no_progress" in result.failure_categories
