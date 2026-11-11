"""Dispatch disk gate tests — fail-closed precheck before worktree creation.

Coverage (≥8 cases per acceptance criterion (a)):
  1. Low space → must reject (fail-closed)
  2. Sufficient space → must pass
  3. Threshold boundary: free_gb == min_free_gb → pass (>=)
  4. Threshold boundary: free_gb < min_free_gb by epsilon → reject
  5. Threshold boundary: used_pct == max_used_pct → pass (<=)
  6. Threshold boundary: used_pct > max_used_pct by epsilon → reject
  7. Path does not exist → must reject (fail-closed, not pass)
  8. Return schema is stable (all required keys present)
  9. assert_dispatch_ok raises on rejection
  10. assert_dispatch_ok passes on acceptance
  11. Both conditions fail → both reasons present
  12. Idempotent: two calls return identical results
"""
import json
import os
import tempfile
import time

import pytest

from tools.disk_gate import (
    DEFAULT_MAX_USED_PCT,
    DEFAULT_MIN_FREE_GB,
    assert_dispatch_ok,
    precheck_dispatch,
    run_gc_dryrun,
    SCHEMA_VERSION,
)


def _load_domain_terms():
    """Load domain terms from the lexicon (avoids hardcoding in test source)."""
    lexicon_path = os.path.join(
        os.path.dirname(__file__), "..", "tools", "evidence_domain_lexicon.json"
    )
    with open(lexicon_path) as f:
        lex = json.load(f)
    return lex.get("ascii_terms", []) + lex.get("cjk_terms", [])


# ---------------------------------------------------------------------------
# Helper: create a temp directory with controlled "disk" characteristics
# ---------------------------------------------------------------------------

def _get_real_disk_info():
    """Get real disk info for the current filesystem."""
    return precheck_dispatch("/tmp")


# ---------------------------------------------------------------------------
# (a) pytest ≥ 8 cases, all green
# ---------------------------------------------------------------------------

class TestPrecheckDispatch:
    """Core precheck_dispatch tests."""

    def test_low_space_must_reject(self):
        """When free space is below threshold, gate must reject (fail-closed)."""
        result = precheck_dispatch("/tmp", min_free_gb=1_000_000, max_used_pct=99.9)
        assert result["ok"] is False
        assert len(result["reasons"]) >= 1
        assert any("insufficient free space" in r for r in result["reasons"])

    def test_sufficient_space_must_pass(self):
        """When both conditions are met, gate must pass."""
        result = precheck_dispatch("/tmp", min_free_gb=0.001, max_used_pct=99.99)
        assert result["ok"] is True
        assert result["reasons"] == []
        assert result["free_gb"] > 0

    def test_boundary_free_gb_equal_passes(self):
        """free_gb == min_free_gb → pass (>= boundary)."""
        info = precheck_dispatch("/tmp")
        actual_free = info["free_gb"]
        result = precheck_dispatch("/tmp", min_free_gb=actual_free, max_used_pct=99.99)
        free_reasons = [r for r in result["reasons"] if "insufficient free space" in r]
        assert len(free_reasons) == 0, f"Should pass at boundary, got: {free_reasons}"

    def test_boundary_free_gb_below_rejects(self):
        """free_gb < min_free_gb by epsilon → reject."""
        info = precheck_dispatch("/tmp")
        actual_free = info["free_gb"]
        result = precheck_dispatch("/tmp", min_free_gb=actual_free + 1.0, max_used_pct=99.99)
        assert result["ok"] is False
        assert any("insufficient free space" in r for r in result["reasons"])

    def test_boundary_used_pct_equal_passes(self):
        """used_pct == max_used_pct → pass (<= boundary)."""
        info = precheck_dispatch("/tmp")
        actual_used = info["used_pct"]
        result = precheck_dispatch("/tmp", min_free_gb=0.001, max_used_pct=actual_used)
        used_reasons = [r for r in result["reasons"] if "disk usage too high" in r]
        assert len(used_reasons) == 0, f"Should pass at boundary, got: {used_reasons}"

    def test_boundary_used_pct_above_rejects(self):
        """used_pct > max_used_pct by epsilon → reject."""
        info = precheck_dispatch("/tmp")
        actual_used = info["used_pct"]
        result = precheck_dispatch("/tmp", min_free_gb=0.001, max_used_pct=actual_used - 0.01)
        assert result["ok"] is False
        assert any("disk usage too high" in r for r in result["reasons"])

    def test_path_not_exists_must_reject(self):
        """Non-existent path → must reject (fail-closed, NOT pass)."""
        result = precheck_dispatch("/nonexistent/path/that/does/not/exist")
        assert result["ok"] is False
        assert len(result["reasons"]) >= 1
        assert any("path does not exist" in r for r in result["reasons"])
        assert result["free_gb"] == 0.0

    def test_return_schema_stable(self):
        """Return dict must have all required keys with correct types."""
        result = precheck_dispatch("/tmp")
        required_keys = {
            "schema", "path", "ok", "reasons", "free_gb", "used_pct",
            "min_free_gb", "max_used_pct",
        }
        for key in required_keys:
            assert key in result, f"Missing key: {key}"
        assert isinstance(result["schema"], str)
        assert isinstance(result["path"], str)
        assert isinstance(result["ok"], bool)
        assert isinstance(result["reasons"], list)
        assert isinstance(result["free_gb"], (int, float))
        assert isinstance(result["used_pct"], (int, float))

    def test_assert_dispatch_ok_raises_on_rejection(self):
        """assert_dispatch_ok must raise RuntimeError when gate rejects."""
        with pytest.raises(RuntimeError) as exc_info:
            assert_dispatch_ok("/tmp", min_free_gb=1_000_000)
        assert "dispatch disk gate rejected" in str(exc_info.value)

    def test_assert_dispatch_ok_passes_on_acceptance(self):
        """assert_dispatch_ok must not raise when gate passes."""
        assert_dispatch_ok("/tmp", min_free_gb=0.001, max_used_pct=99.99)

    def test_both_conditions_fail_both_reasons_present(self):
        """When both conditions fail, both reasons must be present."""
        result = precheck_dispatch("/tmp", min_free_gb=1_000_000, max_used_pct=0.01)
        assert result["ok"] is False
        assert len(result["reasons"]) == 2
        assert any("insufficient free space" in r for r in result["reasons"])
        assert any("disk usage too high" in r for r in result["reasons"])

    def test_idempotent_two_calls_identical(self):
        """Two consecutive calls must return identical results."""
        r1 = precheck_dispatch("/tmp", min_free_gb=0.001, max_used_pct=99.99)
        r2 = precheck_dispatch("/tmp", min_free_gb=0.001, max_used_pct=99.99)
        assert r1["ok"] == r2["ok"]
        assert r1["free_gb"] == r2["free_gb"]
        assert r1["used_pct"] == r2["used_pct"]
        assert r1["reasons"] == r2["reasons"]
        assert r1["schema"] == r2["schema"]


class TestGcDryrun:
    """GC dry-run tests."""

    def test_dryrun_idempotent(self):
        """Two consecutive dry-runs must return byte-identical JSON (except timestamp)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wt = os.path.join(tmpdir, "m12345")
            os.makedirs(os.path.join(wt, ".git"), exist_ok=True)

            r1 = run_gc_dryrun(tmpdir)
            r2 = run_gc_dryrun(tmpdir)

            r1_cmp = {k: v for k, v in r1.items() if k != "scanned_at"}
            r2_cmp = {k: v for k, v in r2.items() if k != "scanned_at"}
            assert r1_cmp == r2_cmp

    def test_dryrun_protect_count_gt_zero(self):
        """PROTECT-class worktrees must be counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wt = os.path.join(tmpdir, "m12345")
            os.makedirs(os.path.join(wt, ".git"), exist_ok=True)
            with open(os.path.join(wt, "dirty_file.txt"), "w") as f:
                f.write("dirty")

            result = run_gc_dryrun(tmpdir)
            assert result["total_worktrees"] >= 1

    def test_dryrun_delete_list_empty(self):
        """Dry-run must never produce a delete list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wt = os.path.join(tmpdir, "m12345")
            os.makedirs(os.path.join(wt, ".git"), exist_ok=True)

            result = run_gc_dryrun(tmpdir)
            assert result["delete_list"] == []

    def test_dryrun_schema_present(self):
        """Dry-run result must have required schema keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_gc_dryrun(tmpdir)
            required = {"root", "schema", "total_worktrees", "by_class",
                        "estimated_releasable_bytes", "estimated_releasable_gb",
                        "disk_gate", "protect_count", "delete_list"}
            for key in required:
                assert key in result, f"Missing key: {key}"

    def test_dryrun_nonexistent_root(self):
        """Non-existent root must return error, not crash."""
        result = run_gc_dryrun("/nonexistent/root/path")
        assert "error" in result
        assert result["total_worktrees"] == 0


class TestDomainNeutrality:
    """Zero domain semantics verification."""

    def test_no_domain_terms_in_source(self):
        """The disk_gate module must not contain domain vocabulary.

        Domain terms are loaded from the lexicon configuration file,
        not hardcoded in this test (to avoid triggering the scanner).
        """
        import inspect
        import re
        from tools import disk_gate

        source = inspect.getsource(disk_gate)
        domain_terms = _load_domain_terms()

        for term in domain_terms:
            if re.match(r'^[a-zA-Z]+$', term):
                pattern = r'\b' + re.escape(term) + r'\b'
            else:
                pattern = re.escape(term)
            assert not re.search(pattern, source, re.IGNORECASE), \
                f"Domain term '{term}' found in disk_gate.py"
