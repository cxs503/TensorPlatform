"""Tests for the reproducibility audit tool.

These tests verify:
1. Sampling logic produces ≥30 nodes from ≥3 repos
2. KPI consistency (|repro_rate - JSON computed| ≤ 1e-9)
3. All report SHA256 anchors are present
4. Blocked items are excluded from repro_rate
5. Drift items have attribution labels
6. Core module functions work correctly
"""

from __future__ import annotations

import json
import os
import hashlib
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_OUTPUT = REPO_ROOT / "artifacts" / "repro_audit.json"


@pytest.fixture(scope="module")
def audit_data():
    """Load the audit output JSON."""
    if not AUDIT_OUTPUT.exists():
        pytest.skip(f"Audit output not found at {AUDIT_OUTPUT}")
    with open(AUDIT_OUTPUT) as f:
        return json.load(f)


class TestAuditOutputStructure:
    """Verify the audit output has the required structure."""

    def test_has_audit_id(self, audit_data):
        assert "audit_id" in audit_data
        assert audit_data["audit_id"] == "p70w3b-n10-repro-audit"

    def test_has_timestamp(self, audit_data):
        assert "timestamp" in audit_data
        assert "Z" in audit_data["timestamp"]

    def test_has_summary(self, audit_data):
        assert "summary" in audit_data
        summary = audit_data["summary"]
        required_keys = ["total_sampled", "pass", "drift", "blocked", 
                         "auditable", "repro_rate", "repro_rate_pct", "kpi_consistent"]
        for key in required_keys:
            assert key in summary, f"Missing key: {key}"

    def test_has_nodes(self, audit_data):
        assert "nodes" in audit_data
        assert isinstance(audit_data["nodes"], list)
        assert len(audit_data["nodes"]) > 0

    def test_has_methodology(self, audit_data):
        assert "methodology" in audit_data
        assert "sampling" in audit_data["methodology"]
        assert "tolerance_protocol" in audit_data["methodology"]
        assert "drift_classification" in audit_data["methodology"]


class TestSamplingRequirements:
    """Verify sampling meets the minimum requirements."""

    def test_sample_size_ge_30(self, audit_data):
        assert audit_data["sample_size"] >= 30, \
            f"Sample size {audit_data['sample_size']} < 30"

    def test_repos_covered_ge_3(self, audit_data):
        assert audit_data["n_repos"] >= 3, \
            f"Repos covered {audit_data['n_repos']} < 3"

    def test_repos_list_matches_count(self, audit_data):
        assert len(audit_data["repos_covered"]) == audit_data["n_repos"]


class TestKPIConsistency:
    """Verify KPI consistency requirements."""

    def test_kpi_consistent_flag(self, audit_data):
        assert audit_data["summary"]["kpi_consistent"] is True

    def test_repro_rate_matches_json_computed(self, audit_data):
        summary = audit_data["summary"]
        pass_count = summary["pass"]
        drift_count = summary["drift"]
        auditable = pass_count + drift_count
        
        if auditable > 0:
            expected_rate = pass_count / auditable
        else:
            expected_rate = 0.0
        
        actual_rate = summary["repro_rate"]
        assert abs(actual_rate - expected_rate) <= 1e-9, \
            f"KPI inconsistent: {actual_rate} vs {expected_rate}"

    def test_repro_rate_pct_matches(self, audit_data):
        summary = audit_data["summary"]
        expected_pct = f"{summary['repro_rate']*100:.2f}%"
        assert summary["repro_rate_pct"] == expected_pct

    def test_auditable_equals_pass_plus_drift(self, audit_data):
        summary = audit_data["summary"]
        assert summary["auditable"] == summary["pass"] + summary["drift"]

    def test_total_equals_pass_plus_drift_plus_blocked(self, audit_data):
        summary = audit_data["summary"]
        assert summary["total_sampled"] == summary["pass"] + summary["drift"] + summary["blocked"]


class TestNodeStructure:
    """Verify each node has the required structure."""

    def test_all_nodes_have_mid(self, audit_data):
        for node in audit_data["nodes"]:
            assert "mid" in node, f"Node missing mid: {node}"

    def test_all_nodes_have_status(self, audit_data):
        valid_statuses = {"pass", "drift", "blocked"}
        for node in audit_data["nodes"]:
            assert node["status"] in valid_statuses, \
                f"Invalid status: {node['status']}"

    def test_all_nodes_have_report_sha256(self, audit_data):
        for node in audit_data["nodes"]:
            assert "report_sha256" in node
            assert len(node["report_sha256"]) == 64, \
                f"Invalid SHA256 length: {len(node['report_sha256'])}"

    def test_blocked_nodes_have_reason(self, audit_data):
        for node in audit_data["nodes"]:
            if node["status"] == "blocked":
                assert "blocked_reason" in node
                assert node["blocked_reason"], "Blocked node has empty reason"

    def test_drift_nodes_have_attribution(self, audit_data):
        for node in audit_data["nodes"]:
            if node["status"] == "drift":
                assert "drift_attribution" in node
                assert node["drift_attribution"], "Drift node has no attribution"


class TestBlockedExclusion:
    """Verify blocked items are excluded from repro_rate."""

    def test_blocked_not_in_auditable(self, audit_data):
        summary = audit_data["summary"]
        assert summary["auditable"] == summary["pass"] + summary["drift"]
        assert summary["auditable"] + summary["blocked"] == summary["total_sampled"]

    def test_repro_rate_excludes_blocked(self, audit_data):
        summary = audit_data["summary"]
        if summary["auditable"] > 0:
            expected = summary["pass"] / summary["auditable"]
            assert abs(summary["repro_rate"] - expected) <= 1e-9


class TestSha256Anchor:
    """Verify SHA256 anchor is present and valid."""

    def test_has_report_sha256_anchor(self, audit_data):
        assert "report_sha256_anchor" in audit_data
        assert len(audit_data["report_sha256_anchor"]) == 64

    def test_anchor_is_valid_sha256(self, audit_data):
        anchor = audit_data["report_sha256_anchor"]
        int(anchor, 16)


class TestToleranceSettings:
    """Verify tolerance settings are documented."""

    def test_has_tolerance_settings(self, audit_data):
        assert "tolerance_settings" in audit_data
        assert "observed_rel_tol" in audit_data["tolerance_settings"]
        assert "diagnostic_abs_tol" in audit_data["tolerance_settings"]

    def test_tolerance_values(self, audit_data):
        ts = audit_data["tolerance_settings"]
        assert ts["observed_rel_tol"] == 1e-6
        assert ts["diagnostic_abs_tol"] == 1e-10


class TestCoreModule:
    """Test the existing repro_audit.core module functions."""

    def test_import_core(self):
        from tools.repro_audit import core
        assert hasattr(core, "classify_value")
        assert hasattr(core, "flatten_numbers")
        assert hasattr(core, "parse_tolerance")
        assert hasattr(core, "sample_stratified")
        assert hasattr(core, "verdict_of")

    def test_classify_value_exact(self):
        from tools.repro_audit.core import classify_value
        result = classify_value(1.0, 1.0)
        assert result["ok"] is True
        assert result["delta"] == 0.0

    def test_classify_value_within_tol(self):
        from tools.repro_audit.core import classify_value
        result = classify_value(1.0, 1.0 + 1e-7)
        assert result["ok"] is True

    def test_classify_value_drift(self):
        from tools.repro_audit.core import classify_value
        result = classify_value(1.0, 1.1)
        assert result["ok"] is False
        assert result["delta"] == pytest.approx(0.1, abs=1e-6)

    def test_classify_value_diagnostic(self):
        from tools.repro_audit.core import classify_value, DIAG_NEAR_ZERO
        result = classify_value(1e-9, 2e-9)
        assert result["mode"] == "diagnostic"

    def test_flatten_numbers(self):
        from tools.repro_audit.core import flatten_numbers
        obj = {"a": 1.0, "b": {"c": 2.0, "d": [3.0, 4.0]}}
        result = flatten_numbers(obj)
        assert result["a"] == 1.0
        assert result["b.c"] == 2.0
        assert "b.d[0]" in result or "b.d.0" in result

    def test_parse_tolerance_pct(self):
        from tools.repro_audit.core import parse_tolerance
        assert parse_tolerance("误差<5%") == pytest.approx(0.05)

    def test_parse_tolerance_sci(self):
        from tools.repro_audit.core import parse_tolerance
        assert parse_tolerance("<1e-9") == pytest.approx(1e-9)

    def test_parse_tolerance_default(self):
        from tools.repro_audit.core import parse_tolerance, OBS_REL_TOL
        assert parse_tolerance(None) == OBS_REL_TOL

    def test_sample_stratified(self):
        from tools.repro_audit.core import sample_stratified
        nodes = [
            {"id": 1, "mission_id": "m1", "metadata": {"repo": "A"}, "created_at": "2024-01-01"},
            {"id": 2, "mission_id": "m2", "metadata": {"repo": "A"}, "created_at": "2024-01-02"},
            {"id": 3, "mission_id": "m3", "metadata": {"repo": "B"}, "created_at": "2024-01-01"},
            {"id": 4, "mission_id": "m4", "metadata": {"repo": "B"}, "created_at": "2024-01-02"},
            {"id": 5, "mission_id": "m5", "metadata": {"repo": "C"}, "created_at": "2024-01-01"},
        ]
        result = sample_stratified(nodes, n=3, seed="test")
        assert len(result) == 3
        # Should cover multiple repos
        repos = {n.get("metadata", {}).get("repo") for n in result}
        assert len(repos) >= 2
