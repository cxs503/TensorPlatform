"""Comprehensive tests for failure signature miner.

Tests cover:
1. Classification accuracy (six types)
2. Clustering and deduplication
3. Idempotent ledger (sha256 consistency 10/10)
4. Zero domain semantics (domain blacklist scan)
5. Negative control: synthetic failed records (6 types × 5)
6. SQL evidence reproducibility
7. KNOWN_TRAPS similarity check
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Ensure the worktree is on the path
WORKTREE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKTREE))

from tools.failure_miner.signature_miner import (
    FailedMissionView,
    FailureSignature,
    compute_reason_hash,
    extract_error_code,
    extract_file_fingerprint,
    extract_failure_reason,
)
from tools.failure_miner.classifier import (
    classify_mission,
    classify_batch,
    classification_report,
    SIGNATURE_TYPES,
)
from tools.failure_miner.cluster import (
    cluster_signatures,
    deduplicate_against_existing,
    compute_similarity,
)
from tools.failure_miner.domain_guard import (
    scan_for_domain_terms,
    verify_zero_domain_semantics,
    load_domain_blacklist,
)
from tools.failure_miner.ledger import (
    compute_run_hash,
    LEDGER_DDL,
)


# ─────────────────────────────── Fixtures ───────────────────────────────

def _make_mission(
    mission_id: int,
    *,
    goal: str = "test mission",
    repo: str = "TensorPlatform",
    status: str = "failed",
    fail_stage: str | None = None,
    result: dict | None = None,
    has_report: bool = False,
    lifecycle_events: list[dict] | None = None,
    organization_id: int | None = 1,
) -> FailedMissionView:
    """Helper to create a FailedMissionView for testing."""
    return FailedMissionView(
        mission_id=mission_id,
        goal=goal,
        repo=repo,
        status=status,
        fail_stage=fail_stage,
        result=result or {},
        has_report=has_report,
        lifecycle_events=lifecycle_events or [],
        organization_id=organization_id,
    )


# ─────────────────────────────── 1. Classification Tests ───────────────────────────────

class TestClassification:
    """Test six-class classification accuracy."""

    def test_timeout_wall_exhausted(self):
        m = _make_mission(1, fail_stage="wall_exhausted", result={
            "evidence": {"checks": {"reason": "wall clock exceeded max_wall_seconds"}}
        })
        assert classify_mission(m) == "timeout"

    def test_timeout_budget_exhausted(self):
        m = _make_mission(2, fail_stage="budget_exhausted")
        assert classify_mission(m) == "timeout"

    def test_timeout_result_reason(self):
        m = _make_mission(3, result={
            "evidence": {"checks": {"reason": "timeout: max_tool_calls reached"}}
        })
        assert classify_mission(m) == "timeout"

    def test_infra_lost_agent_lost(self):
        m = _make_mission(4, fail_stage="agent_lost")
        assert classify_mission(m) == "infra_lost"

    def test_infra_lost_worker_crash(self):
        m = _make_mission(5, fail_stage="worker_crash")
        assert classify_mission(m) == "infra_lost"

    def test_infra_lost_connection(self):
        m = _make_mission(6, result={
            "evidence": {"checks": {"reason": "Failed to connect to remote"}}
        })
        assert classify_mission(m) == "infra_lost"

    def test_criteria_infeasible(self):
        m = _make_mission(7, result={
            "evidence": {"checks": {"reason": "acceptance criteria infeasible: goal unverifiable"}}
        })
        assert classify_mission(m) == "criteria_infeasible"

    def test_criteria_infeasible_chinese(self):
        m = _make_mission(8, result={
            "evidence": {"checks": {"reason": "目标不可判定，判据缺失"}}
        })
        assert classify_mission(m) == "criteria_infeasible"

    def test_physics_honest_fail(self):
        m = _make_mission(9, fail_stage="honest_fail")
        assert classify_mission(m) == "physics_honest_fail"

    def test_physics_honest_fail_reason(self):
        m = _make_mission(10, result={
            "evidence": {"checks": {"reason": "honest-FAIL: physics limit reached"}}
        })
        assert classify_mission(m) == "physics_honest_fail"

    def test_report_owed_no_report(self):
        m = _make_mission(11, has_report=False, result={})
        assert classify_mission(m) == "report_owed"

    def test_report_owed_with_report_not_owed(self):
        m = _make_mission(12, has_report=True, result={
            "evidence": {"checks": {"reason": "timeout"}}
        })
        assert classify_mission(m) == "timeout"

    def test_default_report_owed_for_unknown(self):
        """Mission with unknown error and no report → report_owed (catch-all)."""
        m = _make_mission(13, result={"evidence": {"checks": {"reason": "unknown error"}}})
        assert classify_mission(m) == "report_owed"

    def test_default_infra_lost_with_report(self):
        """Mission with unknown error but has report → infra_lost."""
        m = _make_mission(14, has_report=True, result={"evidence": {"checks": {"reason": "unknown error"}}})
        assert classify_mission(m) == "infra_lost"

    def test_batch_classification(self):
        missions = [
            _make_mission(1, fail_stage="wall_exhausted"),
            _make_mission(2, fail_stage="agent_lost"),
            _make_mission(3, has_report=False, result={}),
        ]
        classified = classify_batch(missions)
        types = [t for _, t in classified]
        assert types == ["timeout", "infra_lost", "report_owed"]

    def test_classification_report(self):
        missions = [
            _make_mission(1, fail_stage="wall_exhausted"),
            _make_mission(2, fail_stage="wall_exhausted"),
            _make_mission(3, fail_stage="agent_lost"),
        ]
        classified = classify_batch(missions)
        report = classification_report(classified)
        assert report["total"] == 3
        assert report["distribution"]["timeout"] == 2
        assert report["distribution"]["infra_lost"] == 1
        assert report["percentages"]["timeout"] == pytest.approx(66.67, abs=0.1)


# ─────────────────────────────── 2. Clustering Tests ───────────────────────────────

class TestClustering:
    """Test clustering and deduplication."""

    def test_cluster_same_signature(self):
        missions = [
            _make_mission(1, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }),
            _make_mission(2, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }),
        ]
        classified = classify_batch(missions)
        signatures = cluster_signatures(classified)
        # Same reason → same cluster
        assert len(signatures) == 1
        assert signatures[0].frequency == 2

    def test_cluster_different_signatures(self):
        missions = [
            _make_mission(1, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout A"}}
            }),
            _make_mission(2, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout B"}}
            }),
        ]
        classified = classify_batch(missions)
        signatures = cluster_signatures(classified)
        assert len(signatures) == 2

    def test_cluster_impact_score(self):
        missions = [
            _make_mission(1, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }, organization_id=1),
            _make_mission(2, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }, organization_id=2),
        ]
        classified = classify_batch(missions)
        signatures = cluster_signatures(classified)
        # Multiple orgs → higher impact
        assert signatures[0].impact_score > 2.0

    def test_deduplicate_against_existing(self):
        sig = FailureSignature(
            signature_type="timeout",
            error_code="wall_exhausted",
            file_fingerprint="abc123",
            reason_hash="def456",
            mission_ids=[1],
            frequency=1,
            impact_score=1.0,
        )
        existing = [{"text": "timeout wall_exhausted abc123"}]
        new, marked = deduplicate_against_existing([sig], existing, similarity_threshold=0.5)
        assert len(marked) == 1
        assert len(new) == 0

    def test_compute_similarity(self):
        assert compute_similarity("hello world", "hello world") == 1.0
        assert compute_similarity("hello world", "hello") >= 0.5
        assert compute_similarity("abc", "xyz") == 0.0


# ─────────────────────────────── 3. Idempotent Ledger Tests ───────────────────────────────

class TestIdempotentLedger:
    """Test idempotent ledger (sha256 consistency 10/10)."""

    def test_run_hash_deterministic(self):
        sigs = [
            FailureSignature(
                signature_type="timeout", error_code="wall",
                file_fingerprint="fp1", reason_hash="rh1",
                mission_ids=[1], frequency=1, impact_score=1.0,
            ),
            FailureSignature(
                signature_type="infra_lost", error_code="crash",
                file_fingerprint="fp2", reason_hash="rh2",
                mission_ids=[2], frequency=2, impact_score=2.0,
            ),
        ]
        h1 = compute_run_hash(sigs)
        h2 = compute_run_hash(sigs)
        assert h1 == h2

    def test_run_hash_order_independent(self):
        sigs1 = [
            FailureSignature(
                signature_type="timeout", error_code="wall",
                file_fingerprint="fp1", reason_hash="rh1",
                mission_ids=[1], frequency=1, impact_score=1.0,
            ),
            FailureSignature(
                signature_type="infra_lost", error_code="crash",
                file_fingerprint="fp2", reason_hash="rh2",
                mission_ids=[2], frequency=2, impact_score=2.0,
            ),
        ]
        sigs2 = list(reversed(sigs1))
        assert compute_run_hash(sigs1) == compute_run_hash(sigs2)

    def test_run_hash_different_data(self):
        sigs1 = [
            FailureSignature(
                signature_type="timeout", error_code="wall",
                file_fingerprint="fp1", reason_hash="rh1",
                mission_ids=[1], frequency=1, impact_score=1.0,
            ),
        ]
        sigs2 = [
            FailureSignature(
                signature_type="timeout", error_code="wall",
                file_fingerprint="fp1", reason_hash="rh1",
                mission_ids=[1, 2], frequency=2, impact_score=2.0,
            ),
        ]
        assert compute_run_hash(sigs1) != compute_run_hash(sigs2)

    def test_idempotency_10_runs(self, tmp_path):
        """Run hash must be consistent across 10 identical runs."""
        import sqlite3
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session
        from tools.failure_miner.ledger import upsert_signatures_sqlite

        db_path = tmp_path / "test_ledger.db"
        engine = create_engine(f"sqlite:///{db_path}")

        # SQLite requires executing statements one at a time
        with engine.connect() as conn:
            for stmt in LEDGER_DDL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))
            conn.commit()

        sigs = [
            FailureSignature(
                signature_type="timeout", error_code="wall",
                file_fingerprint="fp1", reason_hash="rh1",
                mission_ids=[1], frequency=1, impact_score=1.0,
            ),
        ]

        hashes = []
        for _ in range(10):
            with Session(engine) as session:
                result = upsert_signatures_sqlite(session, sigs)
                session.commit()
                hashes.append(result["run_hash"])

        # All 10 runs must produce the same hash
        assert len(set(hashes)) == 1, f"Expected 1 unique hash, got {len(set(hashes))}: {set(hashes)}"


# ─────────────────────────────── 4. Zero Domain Semantics Tests ───────────────────────────────

class TestZeroDomainSemantics:
    """Test that failure miner output contains zero domain terms."""

    def test_scan_ascii_terms(self):
        texts = ["hull resistance test", "normal platform error"]
        blacklist = ["hull", "propeller"]
        hits = scan_for_domain_terms(texts, blacklist)
        assert len(hits) == 1
        assert hits[0]["term"] == "hull"

    def test_scan_cjk_terms(self):
        texts = ["船舶阻力测试", "正常平台错误"]
        blacklist = ["船舶", "阻力"]
        hits = scan_for_domain_terms(texts, blacklist)
        assert len(hits) == 2

    def test_verify_clean(self):
        candidates = [
            {"signature_type": "timeout", "error_code": "wall", "description": "timeout error"},
        ]
        blacklist = ["hull", "船舶"]
        result = verify_zero_domain_semantics(candidates, blacklist)
        assert result["clean"] is True

    def test_verify_dirty(self):
        candidates = [
            {"signature_type": "timeout", "error_code": "hull", "description": "hull error"},
        ]
        blacklist = ["hull"]
        result = verify_zero_domain_semantics(candidates, blacklist)
        assert result["clean"] is False
        assert len(result["hits"]) == 2  # error_code + description

    def test_signature_miner_no_domain_terms(self):
        """Verify that signature_miner module itself contains no domain terms."""
        module_path = WORKTREE / "tools" / "failure_miner" / "signature_miner.py"
        if not module_path.exists():
            pytest.skip("Module file not found")
        with open(module_path, "r", encoding="utf-8") as f:
            content = f.read()

        blacklist = ["hull", "propeller", "船舶", "阻力", "螺旋桨"]
        hits = scan_for_domain_terms([content], blacklist)
        assert len(hits) == 0, f"Found domain terms in signature_miner.py: {hits}"

    def test_classifier_no_domain_terms(self):
        """Verify that classifier module itself contains no domain terms."""
        module_path = WORKTREE / "tools" / "failure_miner" / "classifier.py"
        if not module_path.exists():
            pytest.skip("Module file not found")
        with open(module_path, "r", encoding="utf-8") as f:
            content = f.read()

        blacklist = ["hull", "propeller", "船舶", "阻力", "螺旋桨"]
        hits = scan_for_domain_terms([content], blacklist)
        assert len(hits) == 0, f"Found domain terms in classifier.py: {hits}"


# ─────────────────────────────── 5. Negative Control: Synthetic Records ───────────────────────────────

class TestNegativeControl:
    """Test with 30 synthetic failed records (6 types × 5 each)."""

    @pytest.fixture
    def synthetic_missions(self):
        """Generate 30 synthetic missions (6 types × 5)."""
        missions = []
        mid = 1000

        # timeout × 5 (same reason → same cluster)
        for i in range(5):
            missions.append(_make_mission(
                mid + i, fail_stage="wall_exhausted",
                result={"evidence": {"checks": {"reason": "timeout"}}}
            ))

        # infra_lost × 5 (same reason → same cluster)
        for i in range(5):
            missions.append(_make_mission(
                mid + 5 + i, fail_stage="agent_lost",
                result={"evidence": {"checks": {"reason": "agent lost"}}}
            ))

        # criteria_infeasible × 5 (same reason → same cluster)
        for i in range(5):
            missions.append(_make_mission(
                mid + 10 + i,
                result={"evidence": {"checks": {"reason": "criteria infeasible"}}}
            ))

        # physics_honest_fail × 5 (same reason → same cluster)
        for i in range(5):
            missions.append(_make_mission(
                mid + 15 + i, fail_stage="honest_fail",
                result={"evidence": {"checks": {"reason": "honest fail"}}}
            ))

        # report_owed × 5 (no report, no strong signal)
        for i in range(5):
            missions.append(_make_mission(
                mid + 20 + i, has_report=False, result={}
            ))

        # duplicate_seed × 5 (same signature as timeout)
        for i in range(5):
            missions.append(_make_mission(
                mid + 25 + i, fail_stage="wall_exhausted",
                result={"evidence": {"checks": {"reason": "timeout"}}}
            ))

        return missions

    def test_synthetic_classification_recall(self, synthetic_missions):
        """Classification recall ≥ 0.90 on synthetic data."""
        classified = classify_batch(synthetic_missions)

        # Count correct classifications
        expected_types = {
            "timeout": 5,
            "infra_lost": 5,
            "criteria_infeasible": 5,
            "physics_honest_fail": 5,
            "report_owed": 5,
        }

        actual_counts = {}
        for _, sig_type in classified:
            actual_counts[sig_type] = actual_counts.get(sig_type, 0) + 1

        # Check recall for each expected type
        correct = 0
        total_expected = sum(expected_types.values())
        for sig_type, expected_count in expected_types.items():
            actual_count = actual_counts.get(sig_type, 0)
            correct += min(actual_count, expected_count)

        recall = correct / total_expected if total_expected > 0 else 0
        assert recall >= 0.90, f"Recall {recall:.2f} < 0.90. Distribution: {actual_counts}"

    def test_synthetic_clustering(self, synthetic_missions):
        """Clustering should group same-type missions."""
        classified = classify_batch(synthetic_missions)
        signatures = cluster_signatures(classified)

        # Should have fewer clusters than missions (some grouped)
        assert len(signatures) < len(synthetic_missions)

        # Timeout + duplicate_seed should merge into 1 cluster (same reason)
        timeout_sigs = [s for s in signatures if s.signature_type == "timeout"]
        assert len(timeout_sigs) == 1  # All 10 timeout/duplicate missions in 1 cluster
        assert timeout_sigs[0].frequency == 10  # 5 timeout + 5 duplicate


# ─────────────────────────────── 6. SQL Evidence Reproducibility ───────────────────────────────

class TestSQLEvidence:
    """Test that signature evidence SQL can reproduce source rows."""

    def test_evidence_sql_format(self):
        m = _make_mission(1, fail_stage="wall_exhausted")
        classified = [(m, "timeout")]
        signatures = cluster_signatures(classified)

        assert len(signatures) == 1
        sig = signatures[0]
        assert "SELECT" in sig.evidence_sql
        assert "tensor_dev_missions" in sig.evidence_sql
        assert "1" in sig.evidence_sql

    def test_evidence_sql_multiple_missions(self):
        missions = [
            _make_mission(1, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }),
            _make_mission(2, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }),
            _make_mission(3, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }),
        ]
        classified = classify_batch(missions)
        signatures = cluster_signatures(classified)

        assert len(signatures) == 1
        sig = signatures[0]
        # SQL should reference all mission IDs
        for mid in [1, 2, 3]:
            assert str(mid) in sig.evidence_sql


# ─────────────────────────────── 7. KNOWN_TRAPS Similarity ───────────────────────────────

class TestKnownTrapsSimilarity:
    """Test similarity check against existing KNOWN_TRAPS."""

    def test_high_similarity_marked(self):
        sig = FailureSignature(
            signature_type="timeout", error_code="wall_exhausted",
            file_fingerprint="abc", reason_hash="def",
            mission_ids=[1], frequency=1, impact_score=1.0,
        )
        existing = [{"text": "timeout wall_exhausted abc def"}]
        new, marked = deduplicate_against_existing([sig], existing, similarity_threshold=0.5)
        assert len(marked) == 1
        assert len(new) == 0

    def test_low_similarity_not_marked(self):
        sig = FailureSignature(
            signature_type="timeout", error_code="wall_exhausted",
            file_fingerprint="xyz", reason_hash="uvw",
            mission_ids=[1], frequency=1, impact_score=1.0,
        )
        existing = [{"text": "completely different trap entry"}]
        new, marked = deduplicate_against_existing([sig], existing, similarity_threshold=0.5)
        assert len(marked) == 0
        assert len(new) == 1

    def test_miss_rate_under_10_percent(self):
        """Test that miss rate is ≤ 10% for known traps."""
        # Create 10 signatures, 9 should match existing
        sigs = []
        for i in range(10):
            sigs.append(FailureSignature(
                signature_type="timeout", error_code=f"error_{i}",
                file_fingerprint=f"fp_{i}", reason_hash=f"rh_{i}",
                mission_ids=[i], frequency=1, impact_score=1.0,
            ))

        # Existing traps match 9 out of 10
        existing = [{"text": f"timeout error_{i} fp_{i} rh_{i}"} for i in range(9)]

        new, marked = deduplicate_against_existing(sigs, existing, similarity_threshold=0.5)
        miss_rate = len(new) / len(sigs) if sigs else 0
        assert miss_rate <= 0.10, f"Miss rate {miss_rate:.2f} > 0.10"


# ─────────────────────────────── 8. Integration: Full Pipeline ───────────────────────────────

class TestFullPipeline:
    """Integration test for the full pipeline."""

    def test_full_pipeline_with_synthetic_data(self, tmp_path):
        """Run full pipeline with synthetic data and verify output."""
        from tools.failure_miner.classifier import classify_batch
        from tools.failure_miner.cluster import cluster_signatures, deduplicate_against_existing
        from tools.failure_miner.domain_guard import verify_zero_domain_semantics

        # Create synthetic missions
        missions = [
            _make_mission(1, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }),
            _make_mission(2, fail_stage="agent_lost", result={
                "evidence": {"checks": {"reason": "agent lost"}}
            }),
            _make_mission(3, has_report=False, result={}),
            _make_mission(4, fail_stage="honest_fail", result={
                "evidence": {"checks": {"reason": "honest fail"}}
            }),
            _make_mission(5, result={"evidence": {"checks": {"reason": "criteria infeasible"}}}),
        ]

        # Classify
        classified = classify_batch(missions)
        assert len(classified) == 5

        # Cluster
        signatures = cluster_signatures(classified)
        assert len(signatures) >= 1

        # Verify zero domain semantics
        trap_dicts = [s.to_dict() for s in signatures]
        domain_check = verify_zero_domain_semantics(trap_dicts, ["hull", "船舶"])
        assert domain_check["clean"] is True

        # Export format
        output = {
            "candidates": [s.to_dict() for s in signatures],
            "domain_check": domain_check,
        }

        output_path = tmp_path / "traps_candidates.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)

        # Verify output is valid JSON
        with open(output_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert "candidates" in loaded
        assert "domain_check" in loaded


# ─────────────────────────────── 9. Cohen's Kappa Test ───────────────────────────────

class TestCohensKappa:
    """Test classification consistency (Cohen's κ ≥ 0.70)."""

    def _compute_cohens_kappa(self, labels1: list[str], labels2: list[str]) -> float:
        """Compute Cohen's kappa between two label sets."""
        from collections import Counter

        assert len(labels1) == len(labels2)
        n = len(labels1)

        # Observed agreement
        observed = sum(1 for a, b in zip(labels1, labels2) if a == b) / n

        # Expected agreement
        counts1 = Counter(labels1)
        counts2 = Counter(labels2)
        expected = sum(counts1[k] * counts2.get(k, 0) for k in set(counts1) | set(counts2)) / (n * n)

        if expected == 1.0:
            return 1.0
        return (observed - expected) / (1 - expected)

    def test_kappa_self_agreement(self):
        """Self-agreement should be perfect (κ = 1.0)."""
        labels = ["timeout", "infra_lost", "report_owed", "timeout", "infra_lost"]
        kappa = self._compute_cohens_kappa(labels, labels)
        assert kappa == 1.0

    def test_kappa_high_agreement(self):
        """High agreement should yield κ ≥ 0.70."""
        labels1 = ["timeout"] * 40 + ["infra_lost"] * 30 + ["report_owed"] * 30
        labels2 = ["timeout"] * 38 + ["infra_lost"] * 2 + ["infra_lost"] * 28 + ["report_owed"] * 2 + ["report_owed"] * 30
        kappa = self._compute_cohens_kappa(labels1, labels2)
        assert kappa >= 0.70, f"Kappa {kappa:.2f} < 0.70"

    def test_kappa_classification_consistency(self):
        """Classification should be deterministic (κ = 1.0 for same input)."""
        missions = [
            _make_mission(i, fail_stage="wall_exhausted", result={
                "evidence": {"checks": {"reason": "timeout"}}
            }) for i in range(20)
        ] + [
            _make_mission(i + 20, fail_stage="agent_lost", result={
                "evidence": {"checks": {"reason": "agent lost"}}
            }) for i in range(20)
        ] + [
            _make_mission(i + 40, has_report=False, result={}) for i in range(20)
        ]

        # Run classification twice
        classified1 = classify_batch(missions)
        classified2 = classify_batch(missions)

        labels1 = [t for _, t in classified1]
        labels2 = [t for _, t in classified2]

        kappa = self._compute_cohens_kappa(labels1, labels2)
        assert kappa == 1.0, f"Expected κ=1.0 for deterministic classification, got {kappa}"


# ─────────────────────────────── 10. Edge Cases ───────────────────────────────

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_missions(self):
        classified = classify_batch([])
        assert classified == []

    def test_mission_with_no_result(self):
        m = _make_mission(1, result=None)
        sig_type = classify_mission(m)
        assert sig_type == "report_owed"  # default for no evidence

    def test_mission_with_string_result(self):
        m = _make_mission(1, result="raw string result")
        # Should not crash - string result is handled gracefully
        sig_type = classify_mission(m)
        assert sig_type in SIGNATURE_TYPES

    def test_signature_hash_deterministic(self):
        sig = FailureSignature(
            signature_type="timeout", error_code="wall",
            file_fingerprint="fp", reason_hash="rh",
            mission_ids=[1], frequency=1, impact_score=1.0,
        )
        h1 = sig.signature_hash
        h2 = sig.signature_hash
        assert h1 == h2

    def test_signature_to_dict_roundtrip(self):
        sig = FailureSignature(
            signature_type="timeout", error_code="wall",
            file_fingerprint="fp", reason_hash="rh",
            mission_ids=[1, 2, 3], frequency=3, impact_score=5.0,
            evidence_sql="SELECT * FROM test",
        )
        d = sig.to_dict()
        assert d["signature_type"] == "timeout"
        assert d["mission_ids"] == [1, 2, 3]
        assert d["frequency"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
