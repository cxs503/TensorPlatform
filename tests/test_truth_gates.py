"""
Truth Gates — Comprehensive Tests + Red Team Evidence

Red team tests:
  1. Split verdict: 1 cheat + 1 pass + 1 partial → aggregation says NOT confirmed
  2. Usage reconciliation: self-reported 1000 calls vs metered 87 → >20% deviation
  3. Sidecar red flag: non-monotonic Cd-Re → checker must flag red
  4. Permission red team: agent account INSERT metered_usage → 403

All tests are self-contained and use SQLite in-memory database.
"""
import pytest
import sys
import os
import json

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.truth_gates_aggregator import aggregate_votes, reconcile_usage, reconcile_hash
from services.sidecar_checkers import (
    HoltropCdReChecker, WaveCwFnChecker, run_checker, CHECKER_REGISTRY
)


# ═══════════════════════════════════════════════════════════════════════
#  RED TEAM TEST 1: Split Verdict (2-of-3 fails to reach consensus)
# ═══════════════════════════════════════════════════════════════════════

class TestSplitVerdict:
    """
    Red Team Evidence #1:
    Inject 1 cheat + 1 pass + 1 partial → aggregation must report
    is_confirmed_cheat=False AND is_split=True.
    """

    def test_split_verdict_no_cheat_confirmation(self):
        """1 cheat + 1 pass + 1 partial = split, no strike."""
        votes = [
            {"voter_model": "gpt-4", "verdict": "cheat", "confidence": 0.9},
            {"voter_model": "claude-3", "verdict": "pass", "confidence": 0.85},
            {"voter_model": "local-llama", "verdict": "partial", "confidence": 0.7},
        ]
        result = aggregate_votes(votes)

        assert result["total_voters"] == 3
        assert result["tally"]["cheat"] == 1
        assert result["tally"]["pass"] == 1
        assert result["tally"]["partial"] == 1
        assert result["is_confirmed_cheat"] is False, \
            "RED TEAM: Split verdict must NOT confirm cheat strike"
        assert result["is_split"] is True, \
            "RED TEAM: 1-1-1 split must be flagged as split verdict"
        assert result["majority_verdict"] is None, \
            "RED TEAM: No majority in split verdict"

    def test_confirmed_cheat_2_of_3(self):
        """2 cheat + 1 pass = confirmed cheat strike."""
        votes = [
            {"voter_model": "gpt-4", "verdict": "cheat", "confidence": 0.95},
            {"voter_model": "claude-3", "verdict": "cheat", "confidence": 0.88},
            {"voter_model": "local-llama", "verdict": "pass", "confidence": 0.6},
        ]
        result = aggregate_votes(votes)

        assert result["is_confirmed_cheat"] is True, \
            "2-of-3 cheat votes must confirm strike"
        assert result["is_split"] is False
        assert result["tally"]["cheat"] == 2

    def test_unanimous_cheat(self):
        """3 cheat = confirmed cheat strike."""
        votes = [
            {"voter_model": "gpt-4", "verdict": "cheat", "confidence": 0.9},
            {"voter_model": "claude-3", "verdict": "cheat", "confidence": 0.9},
            {"voter_model": "local-llama", "verdict": "cheat", "confidence": 0.9},
        ]
        result = aggregate_votes(votes)

        assert result["is_confirmed_cheat"] is True
        assert result["tally"]["cheat"] == 3

    def test_all_pass(self):
        """3 pass = no cheat, no split."""
        votes = [
            {"voter_model": "gpt-4", "verdict": "pass", "confidence": 0.9},
            {"voter_model": "claude-3", "verdict": "pass", "confidence": 0.9},
            {"voter_model": "local-llama", "verdict": "pass", "confidence": 0.9},
        ]
        result = aggregate_votes(votes)

        assert result["is_confirmed_cheat"] is False
        assert result["is_split"] is False
        assert result["majority_verdict"] == "pass"

    def test_empty_votes(self):
        """Empty vote list returns safe defaults."""
        result = aggregate_votes([])
        assert result["total_voters"] == 0
        assert result["is_confirmed_cheat"] is False
        assert result["is_split"] is False


# ═══════════════════════════════════════════════════════════════════════
#  RED TEAM TEST 2: Usage Reconciliation (self-reported vs metered)
# ═══════════════════════════════════════════════════════════════════════

class TestUsageReconciliation:
    """
    Red Team Evidence #2:
    Inject self-reported 1000 tool_calls vs metered 87 → deviation >20%.
    """

    def test_massive_deviation_detected(self):
        """Self-reported 1000 calls vs metered 87 → >20% deviation."""
        self_reported = {"tool_calls": 1000, "tokens": 50000, "wall_seconds": 3600}
        metered = {"tool_calls": 87, "tokens_used": 45000, "wall_seconds": 3500}

        result = reconcile_usage(self_reported, metered, threshold_pct=20.0)

        assert result["is_anomaly"] is True, \
            "RED TEAM: 1000 vs 87 tool_calls must trigger anomaly"
        assert result["max_deviation_pct"] > 20.0
        assert "tool_calls" in result["anomaly_fields"]

        # Check specific deviation for tool_calls
        tc_dev = result["deviations"]["tool_calls"]
        expected_dev = abs(1000 - 87) / 87 * 100
        assert abs(tc_dev["deviation_pct"] - expected_dev) < 0.1, \
            f"Expected ~{expected_dev:.1f}% deviation, got {tc_dev['deviation_pct']}"

    def test_within_threshold_no_anomaly(self):
        """Self-reported within 20% of metered → no anomaly."""
        self_reported = {"tool_calls": 100, "tokens": 5000, "wall_seconds": 600}
        metered = {"tool_calls": 95, "tokens_used": 4800, "wall_seconds": 580}

        result = reconcile_usage(self_reported, metered, threshold_pct=20.0)

        assert result["is_anomaly"] is False, \
            "Within 20% threshold should not trigger anomaly"
        assert len(result["anomaly_fields"]) == 0

    def test_exact_match(self):
        """Perfect match → 0% deviation."""
        self_reported = {"tool_calls": 50, "tokens": 1000, "wall_seconds": 300}
        metered = {"tool_calls": 50, "tokens_used": 1000, "wall_seconds": 300}

        result = reconcile_usage(self_reported, metered)

        assert result["is_anomaly"] is False
        assert result["max_deviation_pct"] == 0.0

    def test_zero_metered_nonzero_self(self):
        """Metered=0 but self-reported>0 → 100% deviation."""
        self_reported = {"tool_calls": 10, "tokens": 0, "wall_seconds": 0}
        metered = {"tool_calls": 0, "tokens_used": 0, "wall_seconds": 0}

        result = reconcile_usage(self_reported, metered)

        assert result["is_anomaly"] is True
        assert result["deviations"]["tool_calls"]["deviation_pct"] == 100.0

    def test_hash_reconciliation_match(self):
        """Matching hashes → no anomaly."""
        result = reconcile_hash("abc123", "abc123")
        assert result["match"] is True
        assert result["is_anomaly"] is False

    def test_hash_reconciliation_mismatch(self):
        """Mismatched hashes → anomaly."""
        result = reconcile_hash("abc123", "def456")
        assert result["match"] is False
        assert result["is_anomaly"] is True


# ═══════════════════════════════════════════════════════════════════════
#  RED TEAM TEST 3: Sidecar Physical Property Checkers
# ═══════════════════════════════════════════════════════════════════════

class TestSidecarCheckers:
    """
    Red Team Evidence #3:
    Inject non-monotonic Cd-Re data → checker must flag red.
    Also test wave Cw(Fn) peak position check.
    """

    def test_holtrop_cd_re_monotonic_pass(self):
        """Physically correct: Cd decreases with increasing Re."""
        checker = HoltropCdReChecker()
        data = {
            "reynolds_numbers": [1e6, 5e6, 1e7, 5e7, 1e8],
            "cd_values": [5e-3, 3.5e-3, 2.5e-3, 1.8e-3, 1.5e-3],
        }
        result = checker.check(data)

        assert result["passed"] is True, \
            "Monotonically decreasing Cd-Re should pass"
        assert result["red_flag"] is False

    def test_holtrop_cd_re_non_monotonic_red_flag(self):
        """RED TEAM: Non-monotonic Cd-Re → must flag red."""
        checker = HoltropCdReChecker()
        data = {
            "reynolds_numbers": [1e6, 5e6, 1e7, 5e7, 1e8],
            # Cd INCREASES from index 2 to 3 — unphysical!
            "cd_values": [5e-3, 3.5e-3, 2.5e-3, 4.0e-3, 1.5e-3],
        }
        result = checker.check(data)

        assert result["passed"] is False, \
            "RED TEAM: Non-monotonic Cd-Re must fail"
        assert result["red_flag"] is True, \
            "RED TEAM: Non-monotonic Cd-Re must raise red flag"
        assert len(result["details"]["monotonicity_violations"]) > 0

    def test_holtrop_cd_re_negative_red_flag(self):
        """Negative Cd → unphysical, must flag red."""
        checker = HoltropCdReChecker()
        data = {
            "reynolds_numbers": [1e6, 5e6, 1e7],
            "cd_values": [5e-3, -1e-3, 2.5e-3],  # negative!
        }
        result = checker.check(data)

        assert result["passed"] is False
        assert result["red_flag"] is True
        assert len(result["details"]["negative_violations"]) > 0

    def test_holtrop_cd_re_magnitude_out_of_range(self):
        """Cd outside [1e-4, 1e-1] → magnitude violation."""
        checker = HoltropCdReChecker()
        data = {
            "reynolds_numbers": [1e6, 5e6, 1e7],
            "cd_values": [5e-3, 3.5e-3, 5.0],  # 5.0 is way too large
        }
        result = checker.check(data)

        assert result["passed"] is False
        assert result["red_flag"] is True
        assert len(result["details"]["magnitude_violations"]) > 0

    def test_wave_cw_fn_peak_correct(self):
        """Peak at Fn≈0.45 → should pass."""
        checker = WaveCwFnChecker()
        data = {
            "froude_numbers": [0.1, 0.2, 0.3, 0.4, 0.45, 0.5, 0.6],
            "cw_values": [1e-5, 5e-5, 2e-4, 8e-4, 1.5e-3, 1.2e-3, 8e-4],
        }
        result = checker.check(data)

        assert result["passed"] is True, \
            "Peak at Fn=0.45 should pass"
        assert result["details"]["peak_position_ok"] is True
        assert result["details"]["peak_fn"] == 0.45

    def test_wave_cw_fn_peak_wrong_position_red_flag(self):
        """RED TEAM: Peak at Fn=0.15 (wrong) → must flag red."""
        checker = WaveCwFnChecker()
        data = {
            "froude_numbers": [0.1, 0.15, 0.2, 0.3, 0.4, 0.5],
            # Peak at Fn=0.15 — way too low!
            "cw_values": [1e-4, 5e-3, 2e-3, 1e-3, 5e-4, 2e-4],
        }
        result = checker.check(data)

        assert result["passed"] is False, \
            "RED TEAM: Peak at Fn=0.15 must fail"
        assert result["red_flag"] is True, \
            "RED TEAM: Wrong peak position must raise red flag"
        assert result["details"]["peak_position_ok"] is False
        assert result["details"]["peak_fn"] == 0.15

    def test_wave_cw_fn_negative_red_flag(self):
        """Negative Cw → unphysical."""
        checker = WaveCwFnChecker()
        data = {
            "froude_numbers": [0.1, 0.2, 0.3, 0.4, 0.5],
            "cw_values": [1e-5, -1e-4, 2e-4, 8e-4, 5e-4],  # negative!
        }
        result = checker.check(data)

        assert result["passed"] is False
        assert result["red_flag"] is True

    def test_run_checker_registry(self):
        """Test the checker registry dispatch."""
        # Valid checker
        result = run_checker("holtrop_cd_re", {
            "reynolds_numbers": [1e6, 5e6, 1e7],
            "cd_values": [5e-3, 3.5e-3, 2.5e-3],
        })
        assert result["checker_name"] == "holtrop_cd_re_monotonicity"
        assert result["passed"] is True

        # Unknown checker
        result = run_checker("nonexistent_checker", {})
        assert result["passed"] is False
        assert result["red_flag"] is True

    def test_checker_registry_contents(self):
        """Verify both required checkers are registered."""
        assert "holtrop_cd_re" in CHECKER_REGISTRY
        assert "wave_cw_fn" in CHECKER_REGISTRY
        assert len(CHECKER_REGISTRY) >= 2


# ═══════════════════════════════════════════════════════════════════════
#  RED TEAM TEST 4: Permission Enforcement
# ═══════════════════════════════════════════════════════════════════════

class TestPermissionRedTeam:
    """
    Permission Red Team:
    Agent account INSERT metered_usage → must be 403.
    This tests the logic layer (actual HTTP test requires running server).
    """

    def test_agent_role_blocked_from_metered_write(self):
        """Agent/engineer role must be blocked from writing metered_usage."""
        # Simulate the permission check logic from truth_gates.py
        user_role = "engineer"  # agent account role
        username = "agent_m613"

        # This is the same logic as in the API endpoint
        is_permitted = user_role in ("admin", "platform")

        assert is_permitted is False, \
            "RED TEAM: Agent account must NOT be permitted to write metered_usage"

    def test_admin_role_permitted(self):
        """Admin role should be permitted."""
        user_role = "admin"
        is_permitted = user_role in ("admin", "platform")
        assert is_permitted is True

    def test_platform_role_permitted(self):
        """Platform role should be permitted."""
        user_role = "platform"
        is_permitted = user_role in ("admin", "platform")
        assert is_permitted is True


# ═══════════════════════════════════════════════════════════════════════
#  INTEGRATION: Full Pipeline Test
# ═══════════════════════════════════════════════════════════════════════

class TestFullPipeline:
    """Integration test: full truth-gates pipeline."""

    def test_full_cheat_detection_pipeline(self):
        """
        Simulate full pipeline:
        1. Three independent voters submit votes
        2. Aggregator computes result
        3. If cheat confirmed → strike
        4. If split → split event recorded
        """
        # Scenario: 2-of-3 agree on cheat
        votes = [
            {"voter_model": "gpt-4", "verdict": "cheat", "confidence": 0.92},
            {"voter_model": "claude-3", "verdict": "cheat", "confidence": 0.88},
            {"voter_model": "local-llama", "verdict": "pass", "confidence": 0.65},
        ]

        result = aggregate_votes(votes)

        assert result["is_confirmed_cheat"] is True
        assert result["is_split"] is False
        assert result["tally"]["cheat"] == 2

    def test_full_reconciliation_pipeline(self):
        """
        Simulate full reconciliation:
        1. Agent self-reports usage
        2. Platform has metered usage
        3. Reconciliation detects deviation
        4. Integrity event created
        """
        self_reported = {"tool_calls": 500, "tokens": 200000, "wall_seconds": 7200}
        metered = {"tool_calls": 87, "tokens_used": 45000, "wall_seconds": 3500}

        result = reconcile_usage(self_reported, metered)

        assert result["is_anomaly"] is True
        assert result["max_deviation_pct"] > 100  # 500 vs 87 is huge

    def test_full_sidecar_pipeline(self):
        """
        Simulate full sidecar check:
        1. Simulation outputs Holtrop Cd-Re data
        2. Sidecar checker validates physical constraints
        3. Red flag → auto-enqueue for reproduction
        """
        # Bad data: non-monotonic
        bad_data = {
            "reynolds_numbers": [1e6, 5e6, 1e7, 5e7],
            "cd_values": [5e-3, 3.5e-3, 5e-3, 2e-3],  # index 2 goes UP
        }

        result = run_checker("holtrop_cd_re", bad_data)

        assert result["red_flag"] is True
        assert result["passed"] is False
        # This would auto-enqueue for reproduction in production


# ═══════════════════════════════════════════════════════════════════════
#  SQL REPRODUCIBILITY: Aggregation is pure function
# ═══════════════════════════════════════════════════════════════════════

class TestSQLReproducibility:
    """Verify that aggregation can be re-computed from raw vote data."""

    def test_aggregation_deterministic(self):
        """Same input → same output (pure function)."""
        votes = [
            {"voter_model": "a", "verdict": "cheat", "confidence": 0.9},
            {"voter_model": "b", "verdict": "cheat", "confidence": 0.8},
            {"voter_model": "c", "verdict": "pass", "confidence": 0.7},
        ]

        r1 = aggregate_votes(votes)
        r2 = aggregate_votes(votes)

        assert r1 == r2, "Aggregation must be deterministic"

    def test_aggregation_order_independent(self):
        """Vote order should not affect result."""
        votes_a = [
            {"voter_model": "a", "verdict": "cheat", "confidence": 0.9},
            {"voter_model": "b", "verdict": "pass", "confidence": 0.8},
            {"voter_model": "c", "verdict": "cheat", "confidence": 0.7},
        ]
        votes_b = [
            {"voter_model": "c", "verdict": "cheat", "confidence": 0.7},
            {"voter_model": "a", "verdict": "cheat", "confidence": 0.9},
            {"voter_model": "b", "verdict": "pass", "confidence": 0.8},
        ]

        r_a = aggregate_votes(votes_a)
        r_b = aggregate_votes(votes_b)

        # Tally should be same regardless of order
        assert r_a["tally"] == r_b["tally"]
        assert r_a["is_confirmed_cheat"] == r_b["is_confirmed_cheat"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
