"""Tests for Proposal Gate — 提案→战役播种供给链机器门。

零领域语义：所有测试夹具使用合成域名（foo/bar/widget），
不含任何真实领域词条。域词黑名单由配置文件注入。
"""
import json
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure backend is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.proposal_gate_service import (
    SchemaValidationError,
    _char_ngrams,
    _jaccard_similarity,
    _normalize_text,
    compute_proposal_signature,
    validate_proposal_schema,
    find_duplicate_proposal,
    process_proposal,
    ensure_ledger_table,
    get_ledger_stats,
    record_proposal_verdict,
    _load_config,
)


# ───────────────────────── Test Fixtures (Synthetic Domain) ─────────────────────────

VALID_PROPOSAL_V2 = {
    "title": "Widget optimization for foo-bar pipeline",
    "repo": "TensorPlatform",
    "description": "Improve widget processing efficiency in the foo-bar pipeline",
    "goals": [
        {
            "name": "widget throughput",
            "metric": "widgets_per_second",
            "threshold": 100.0,
            "unit": "wps",
            "comparison": ">="
        },
        {
            "name": "foo processing latency",
            "metric": "latency_ms",
            "threshold": 50.0,
            "unit": "ms",
            "comparison": "<="
        },
        {
            "name": "bar memory usage",
            "metric": "memory_mb",
            "threshold": 256.0,
            "unit": "MB",
            "comparison": "<="
        }
    ],
    "dependencies_hint": ["widget-core", "foo-lib"],
    "is_maturity": True,
    "priority": "high",
    "metadata": {"campaign": "synthetic-campaign-1"}
}

VALID_PROPOSAL_V1 = {
    "title": "Widget enhancement",
    "repo": "TensorLBM",
    "description": "Enhance widget capabilities",
    "goals": [
        {"name": "widget speed", "metric": "speed", "threshold": 10.0},
        {"name": "foo accuracy", "metric": "accuracy", "threshold": 0.95},
        {"name": "bar efficiency", "metric": "efficiency", "threshold": 0.90},
    ],
    "dependencies": ["widget-base"],
    "priority": "medium"
}

INVALID_PROPOSAL_EMPTY_TITLE = {
    "title": "",
    "repo": "TensorPlatform",
    "goals": [
        {"name": "g1", "metric": "m1", "threshold": 1.0},
        {"name": "g2", "metric": "m2", "threshold": 2.0},
        {"name": "g3", "metric": "m3", "threshold": 3.0},
    ],
    "is_maturity": True
}

INVALID_PROPOSAL_BAD_REPO = {
    "title": "Test proposal",
    "repo": "NonExistentRepo",
    "goals": [
        {"name": "g1", "metric": "m1", "threshold": 1.0},
        {"name": "g2", "metric": "m2", "threshold": 2.0},
        {"name": "g3", "metric": "m3", "threshold": 3.0},
    ],
    "is_maturity": True
}

INVALID_PROPOSAL_TOO_FEW_GOALS = {
    "title": "Test proposal",
    "repo": "TensorPlatform",
    "goals": [
        {"name": "g1", "metric": "m1", "threshold": 1.0},
    ],
    "is_maturity": True
}

INVALID_PROPOSAL_MISSING_THRESHOLD = {
    "title": "Test proposal",
    "repo": "TensorPlatform",
    "goals": [
        {"name": "g1", "metric": "m1", "threshold": 1.0},
        {"name": "g2", "metric": "m2"},  # Missing threshold
        {"name": "g3", "metric": "m3", "threshold": 3.0},
    ],
    "is_maturity": True
}

INVALID_PROPOSAL_NON_BOOL_MATURITY = {
    "title": "Test proposal",
    "repo": "TensorPlatform",
    "goals": [
        {"name": "g1", "metric": "m1", "threshold": 1.0},
        {"name": "g2", "metric": "m2", "threshold": 2.0},
        {"name": "g3", "metric": "m3", "threshold": 3.0},
    ],
    "is_maturity": "yes"  # Should be boolean
}


# ───────────────────────── Schema Validation Tests ─────────────────────────

class TestSchemaValidation:
    """Test schema contract gate (fail-closed)."""
    
    def test_valid_v2_proposal(self):
        """Valid v2 proposal should pass validation."""
        result = validate_proposal_schema(VALID_PROPOSAL_V2, "v2")
        assert result["title"] == VALID_PROPOSAL_V2["title"]
        assert result["repo"] == VALID_PROPOSAL_V2["repo"]
        assert len(result["goals"]) >= 3
    
    def test_valid_v1_proposal_migration(self):
        """Valid v1 proposal should be migrated to v2."""
        result = validate_proposal_schema(VALID_PROPOSAL_V1, "v1")
        assert result["schema_version"] == "v2"
        assert result["migrated_from"] == "v1"
        assert len(result["goals"]) >= 3  # Padded to minimum if needed
    
    def test_empty_title_rejected(self):
        """Empty title should be rejected."""
        with pytest.raises(SchemaValidationError) as exc_info:
            validate_proposal_schema(INVALID_PROPOSAL_EMPTY_TITLE, "v2")
        # JSON Schema catches it as "too short"
        assert any("short" in e.lower() or "non-empty" in e.lower() or "title" in e.lower() 
                   for e in exc_info.value.errors)
    
    def test_invalid_repo_rejected(self):
        """Repo not in whitelist should be rejected."""
        with pytest.raises(SchemaValidationError) as exc_info:
            validate_proposal_schema(INVALID_PROPOSAL_BAD_REPO, "v2")
        assert any("whitelist" in e.lower() for e in exc_info.value.errors)
    
    def test_too_few_goals_rejected(self):
        """Less than 3 goals should be rejected."""
        with pytest.raises(SchemaValidationError) as exc_info:
            validate_proposal_schema(INVALID_PROPOSAL_TOO_FEW_GOALS, "v2")
        assert any("3" in e or "goals" in e.lower() for e in exc_info.value.errors)
    
    def test_missing_threshold_rejected(self):
        """Goal without threshold should be rejected."""
        with pytest.raises(SchemaValidationError) as exc_info:
            validate_proposal_schema(INVALID_PROPOSAL_MISSING_THRESHOLD, "v2")
        assert any("threshold" in e.lower() for e in exc_info.value.errors)
    
    def test_non_bool_maturity_rejected(self):
        """Non-boolean is_maturity should be rejected."""
        with pytest.raises(SchemaValidationError) as exc_info:
            validate_proposal_schema(INVALID_PROPOSAL_NON_BOOL_MATURITY, "v2")
        assert any("boolean" in e.lower() or "is_maturity" in e.lower() 
                   for e in exc_info.value.errors)
    
    def test_unsupported_schema_version(self):
        """Unsupported schema version should be rejected."""
        with pytest.raises(SchemaValidationError) as exc_info:
            validate_proposal_schema(VALID_PROPOSAL_V2, "v99")
        assert any("unsupported" in e.lower() for e in exc_info.value.errors)


# ───────────────────────── Text Normalization Tests ─────────────────────────

class TestTextNormalization:
    """Test NFKC normalization and n-gram extraction."""
    
    def test_nfkc_normalization(self):
        """NFKC normalization should handle unicode variants."""
        # Full-width characters → half-width
        text = "Ｗｉｄｇｅｔ"  # Full-width "Widget"
        normalized = _normalize_text(text)
        assert "widget" in normalized
    
    def test_whitespace_collapse(self):
        """Multiple whitespace should be collapsed."""
        text = "foo   bar\t\twidget\n\nbaz"
        normalized = _normalize_text(text)
        assert normalized == "foo bar widget baz"
    
    def test_lowercase(self):
        """Text should be lowercased."""
        text = "WIDGET FooBar"
        normalized = _normalize_text(text)
        assert normalized == "widget foobar"
    
    def test_char_ngrams(self):
        """Character n-gram extraction."""
        text = "widget"
        ngrams = _char_ngrams(text, 3)
        assert "wid" in ngrams
        assert "idg" in ngrams
        assert "dge" in ngrams
        assert "get" in ngrams
        assert len(ngrams) == 4
    
    def test_char_ngrams_short_text(self):
        """Short text should return single n-gram."""
        text = "ab"
        ngrams = _char_ngrams(text, 3)
        assert ngrams == {"ab"}
    
    def test_jaccard_identical(self):
        """Identical sets should have Jaccard = 1.0."""
        set1 = {"a", "b", "c"}
        set2 = {"a", "b", "c"}
        assert _jaccard_similarity(set1, set2) == 1.0
    
    def test_jaccard_disjoint(self):
        """Disjoint sets should have Jaccard = 0.0."""
        set1 = {"a", "b"}
        set2 = {"c", "d"}
        assert _jaccard_similarity(set1, set2) == 0.0
    
    def test_jaccard_partial_overlap(self):
        """Partial overlap should give intermediate Jaccard."""
        set1 = {"a", "b", "c"}
        set2 = {"b", "c", "d"}
        # Intersection = {b, c}, Union = {a, b, c, d}
        # Jaccard = 2/4 = 0.5
        assert _jaccard_similarity(set1, set2) == 0.5
    
    def test_jaccard_empty_sets(self):
        """Empty sets should be handled."""
        assert _jaccard_similarity(set(), set()) == 1.0
        assert _jaccard_similarity({"a"}, set()) == 0.0
        assert _jaccard_similarity(set(), {"a"}) == 0.0


# ───────────────────────── Signature Tests ─────────────────────────

class TestSignature:
    """Test proposal signature computation."""
    
    def test_deterministic_signature(self):
        """Same proposal should produce same signature."""
        sig1 = compute_proposal_signature(VALID_PROPOSAL_V2)
        sig2 = compute_proposal_signature(VALID_PROPOSAL_V2)
        assert sig1 == sig2
    
    def test_different_proposals_different_signatures(self):
        """Different proposals should produce different signatures."""
        sig1 = compute_proposal_signature(VALID_PROPOSAL_V2)
        modified = VALID_PROPOSAL_V2.copy()
        modified["title"] = "Different title"
        sig2 = compute_proposal_signature(modified)
        assert sig1 != sig2
    
    def test_signature_is_sha256(self):
        """Signature should be 64-char hex string."""
        sig = compute_proposal_signature(VALID_PROPOSAL_V2)
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)


# ───────────────────────── Deduplication Tests (Mock DB) ─────────────────────────

class TestDeduplication:
    """Test deduplication logic with mock database."""
    
    def test_find_duplicate_exact_match(self):
        """Exact signature match should be detected."""
        mock_db = MagicMock()
        
        # Simulate existing record with same signature
        existing_record = {
            "mission_id": 100,
            "plan_id": 1,
            "sha256": compute_proposal_signature(VALID_PROPOSAL_V2),
            "schema_version": "v2",
            "verdict": "accepted",
            "dup_of": None,
            "score": None,
            "at": "2024-01-01T00:00:00",
        }
        
        result_mock = MagicMock()
        result_mock.mappings.return_value.first.return_value = existing_record
        mock_db.execute.return_value = result_mock
        
        duplicate = find_duplicate_proposal(VALID_PROPOSAL_V2, mock_db, 1)
        assert duplicate is not None
        assert duplicate["mission_id"] == 100
    
    def test_no_duplicate_for_unique_proposal(self):
        """Unique proposal should not be flagged as duplicate."""
        mock_db = MagicMock()
        
        # First call: exact match check returns None
        exact_match_result = MagicMock()
        exact_match_result.mappings.return_value.first.return_value = None
        
        # Second call: candidates list is empty
        candidates_result = MagicMock()
        candidates_result.mappings.return_value.all.return_value = []
        
        mock_db.execute.side_effect = [exact_match_result, candidates_result]
        
        duplicate = find_duplicate_proposal(VALID_PROPOSAL_V2, mock_db, 1)
        assert duplicate is None


# ───────────────────────── Configuration Tests ─────────────────────────

class TestConfiguration:
    """Test configuration loading."""
    
    def test_config_loads(self):
        """Configuration should load successfully."""
        config = _load_config()
        assert "schema_contract" in config
        assert "repo_whitelist" in config
        assert "deduplicator" in config
        assert "ledger" in config
    
    def test_repo_whitelist_not_empty(self):
        """Repo whitelist should not be empty."""
        config = _load_config()
        assert len(config["repo_whitelist"]) > 0
    
    def test_deduplicator_thresholds_configured(self):
        """Deduplicator thresholds should be in config."""
        config = _load_config()
        dedup = config["deduplicator"]
        assert "jaccard_threshold" in dedup
        assert "dependency_overlap_threshold" in dedup
        assert 0 <= dedup["jaccard_threshold"] <= 1
        assert 0 <= dedup["dependency_overlap_threshold"] <= 1


# ───────────────────────── Zero Domain Semantics Tests ─────────────────────────

class TestZeroDomainSemantics:
    """Verify zero domain semantics in test fixtures."""
    
    def test_no_domain_words_in_fixtures(self):
        """Test fixtures should not contain real domain words."""
        # Load domain denylist
        denylist_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "domain_word_denylist.json"
        )
        if not os.path.exists(denylist_path):
            pytest.skip("Domain denylist not found")
        
        with open(denylist_path, "r") as f:
            denylist = json.load(f)
        
        ascii_words = [w.lower() for w in denylist.get("ascii_words", [])]
        
        # Check all fixture values
        fixtures = [VALID_PROPOSAL_V2, VALID_PROPOSAL_V1]
        for fixture in fixtures:
            fixture_str = json.dumps(fixture).lower()
            for word in ascii_words:
                # Check word boundary (not substring of another word)
                import re
                pattern = r'\b' + re.escape(word) + r'\b'
                matches = re.findall(pattern, fixture_str)
                assert len(matches) == 0, f"Domain word '{word}' found in fixture"
    
    def test_synthetic_domain_names_used(self):
        """Test fixtures should use synthetic domain names."""
        # Check that we use foo/bar/widget instead of real domain terms
        fixture_str = json.dumps(VALID_PROPOSAL_V2).lower()
        assert "widget" in fixture_str or "foo" in fixture_str or "bar" in fixture_str


# ───────────────────────── Integration Tests (would need real DB) ─────────────────────────

class TestIntegration:
    """Integration tests (require database)."""
    
    @pytest.mark.skip(reason="Requires database connection")
    def test_full_proposal_processing(self):
        """Full proposal processing through all gates."""
        # This would test the complete flow with a real database
        pass
    
    @pytest.mark.skip(reason="Requires database connection")
    def test_ledger_stats(self):
        """Ledger statistics computation."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
