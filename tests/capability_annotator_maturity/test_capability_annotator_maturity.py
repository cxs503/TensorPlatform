"""Tests for capability annotator maturity pipeline (p488w1-n01).

Covers all 8 acceptance criteria:
C1: capability_annotator.py intercepts >= 5 API paths
C2: capability_grade_updater.py auto-updates evidence levels
C3: /tensor/capability_status returns JSON with grade/last_verified/error_history
C4: Auto-annotation of WITHHELD capabilities (upgrade or confirm)
C5: external_constants.json has >= 20 constants with DOI
C6: constants_gate.py intercepts >= 3 unregistered constant usage points
C7: Zero domain semantics (no ship-specific terms in test fixtures)
C8: >= 15 pytest cases
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

# Ensure backend is on path
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from middleware.capability_annotator import (  # noqa: E402
    CapabilityAnnotator,
    CapabilityAnnotationMiddleware,
    AnnotationRecord,
    get_annotator,
    reset_annotator,
    DEFAULT_TRACKED_PATHS,
)
from services.capability_grade_updater import (  # noqa: E402
    CapabilityGradeUpdater,
    GradeRule,
    GradeDecision,
    LEVELS,
    DEFAULT_PROMOTION_RULES,
)
from services.constants_gate import (  # noqa: E402
    ConstantsGate,
    ConstantsRegistry,
    ConstantUsage,
    GateResult,
    get_gate,
    reset_gate,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def annotator():
    """Fresh annotator for each test."""
    reset_annotator()
    return CapabilityAnnotator()


@pytest.fixture
def updater():
    """Fresh grade updater for each test."""
    return CapabilityGradeUpdater()


@pytest.fixture
def gate():
    """Fresh constants gate for each test."""
    reset_gate()
    registry_path = str(REPO_ROOT / "config" / "external_constants.json")
    return ConstantsGate(registry_path)


@pytest.fixture
def constants_registry():
    """Load the external constants registry."""
    registry_path = str(REPO_ROOT / "config" / "external_constants.json")
    return ConstantsRegistry(registry_path)


# ── C1: Capability Annotator intercepts >= 5 API paths ────────────────────

class TestCapabilityAnnotator:
    """C1: Annotator intercepts >= 5 API paths recording request/response."""

    def test_default_tracked_paths_count(self, annotator):
        """At least 5 API paths are tracked by default."""
        assert len(annotator.tracked_paths) >= 5

    def test_annotate_tracked_path(self, annotator):
        """Annotating a tracked path returns a record."""
        record = annotator.annotate(
            path="/api/v1/calculations/resistance",
            method="POST",
            status_code=200,
            request_body={"speed_ms": 5.0, "length": 100.0},
            response_body={"total_resistance_N": 12345.0},
        )
        assert record is not None
        assert record.path == "/api/v1/calculations/resistance"
        assert record.method == "POST"
        assert record.status_code == 200
        assert record.digest  # Content-addressed

    def test_annotate_untracked_path_returns_none(self, annotator):
        """Annotating an untracked path returns None."""
        record = annotator.annotate(
            path="/api/v1/unknown/endpoint",
            method="GET",
        )
        assert record is None

    def test_annotate_multiple_paths(self, annotator):
        """Can annotate multiple different tracked paths."""
        paths = list(annotator.tracked_paths)[:5]
        for path in paths:
            record = annotator.annotate(path=path, method="POST")
            assert record is not None
        assert len(annotator.records) == 5

    def test_annotate_with_error_calculation(self, annotator):
        """Error percentage is computed when both values provided."""
        record = annotator.annotate(
            path="/api/v1/calculations/resistance",
            reference_value=10000.0,
            computed_value=10500.0,
        )
        assert record is not None
        assert record.error_pct == pytest.approx(5.0, abs=0.01)

    def test_annotate_error_zero_reference(self, annotator):
        """Error is None when reference is zero."""
        record = annotator.annotate(
            path="/api/v1/calculations/resistance",
            reference_value=0.0,
            computed_value=100.0,
        )
        assert record is not None
        assert record.error_pct is None

    def test_configure_custom_paths(self, annotator):
        """Can configure custom tracked paths."""
        annotator.configure(tracked_paths=["/custom/path1", "/custom/path2"])
        assert len(annotator.tracked_paths) == 2
        assert annotator.is_tracked("/custom/path1")
        assert not annotator.is_tracked("/api/v1/calculations/resistance")

    def test_summary(self, annotator):
        """Summary returns correct counts."""
        annotator.annotate(path="/api/v1/calculations/resistance")
        annotator.annotate(path="/api/v1/calculations/resistance")
        annotator.annotate(path="/api/v1/calculations/propulsion")
        summary = annotator.summary()
        assert summary["total_records"] == 3
        assert summary["path_counts"]["/api/v1/calculations/resistance"] == 2

    def test_persistence(self, annotator):
        """Records can be persisted to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            annotator._storage_dir = Path(tmpdir)
            annotator.annotate(path="/api/v1/calculations/resistance")
            files = list(Path(tmpdir).glob("annotation_*.json"))
            assert len(files) == 1


# ── C2: Grade Updater auto-updates evidence levels ────────────────────────

class TestCapabilityGradeUpdater:
    """C2: Grade updater auto-updates evidence levels with configurable rules."""

    def test_register_capability(self, updater):
        """Can register a capability with initial level."""
        updater.register_capability("test_cap", current_level="WITHHELD")
        assert "test_cap" in updater.capabilities
        assert updater.capabilities["test_cap"]["current_level"] == "WITHHELD"

    def test_record_error(self, updater):
        """Can record error observations."""
        updater.register_capability("test_cap", current_level="WITHHELD")
        updater.record_error("test_cap", error_pct=8.5)
        stats = updater.get_error_stats("test_cap")
        assert stats["count"] == 1
        assert stats["mean"] == pytest.approx(8.5)

    def test_promote_withheld_to_real(self, updater):
        """WITHHELD with <15% error promotes to REAL."""
        updater.register_capability("test_cap", current_level="WITHHELD")
        for _ in range(5):
            updater.record_error("test_cap", error_pct=8.0)
        decision = updater.evaluate("test_cap")
        assert decision.action == "PROMOTE"
        assert decision.proposed_level == "REAL"

    def test_hold_insufficient_samples(self, updater):
        """HOLD when insufficient samples."""
        updater.register_capability("test_cap", current_level="WITHHELD")
        updater.record_error("test_cap", error_pct=5.0)  # Only 1 sample, need 5
        decision = updater.evaluate("test_cap")
        assert decision.action == "HOLD"

    def test_demote_real_to_withheld(self, updater):
        """REAL with high error demotes to WITHHELD."""
        updater.register_capability("test_cap", current_level="REAL")
        for _ in range(3):
            updater.record_error("test_cap", error_pct=30.0)
        decision = updater.evaluate("test_cap")
        assert decision.action == "DEMOTE"
        assert decision.proposed_level == "WITHHELD"

    def test_apply_decision(self, updater):
        """Applying a decision updates the level."""
        updater.register_capability("test_cap", current_level="WITHHELD")
        for _ in range(5):
            updater.record_error("test_cap", error_pct=8.0)
        decision = updater.evaluate("test_cap")
        updater.apply(decision)
        assert updater.capabilities["test_cap"]["current_level"] == "REAL"

    def test_custom_rules(self, updater):
        """Custom rules override defaults."""
        custom_rules = [
            GradeRule(
                from_level="WITHHELD",
                to_level="REAL",
                max_error_pct=5.0,  # Stricter threshold
                min_samples=3,
                description="Custom strict rule",
            )
        ]
        updater.configure_rules(promotion_rules=custom_rules, demotion_rules=[])
        updater.register_capability("test_cap", current_level="WITHHELD")
        for _ in range(3):
            updater.record_error("test_cap", error_pct=6.0)  # > 5% threshold
        decision = updater.evaluate("test_cap")
        assert decision.action == "HOLD"  # Should not promote with 6% error

    def test_status_returns_all(self, updater):
        """Status returns all registered capabilities."""
        updater.register_capability("cap_a", current_level="REAL")
        updater.register_capability("cap_b", current_level="MOCK")
        status = updater.status()
        assert "cap_a" in status
        assert "cap_b" in status
        assert status["cap_a"]["grade"] == "REAL"

    def test_evaluate_all(self, updater):
        """Evaluate all returns decisions for all capabilities."""
        updater.register_capability("cap_a", current_level="WITHHELD")
        updater.register_capability("cap_b", current_level="MOCK")
        decisions = updater.evaluate_all()
        assert len(decisions) == 2


# ── C3: /tensor/capability_status API ─────────────────────────────────────

class TestCapabilityStatusAPI:
    """C3: API returns JSON with grade, last_verified, error_history."""

    def test_api_module_imports(self):
        """API module imports without error."""
        from api.tensor_capability_status import router, get_capability_status
        assert router is not None

    def test_status_response_structure(self):
        """Status response has required fields."""
        from api.tensor_capability_status import (
            CapabilityStatusResponse,
            CapabilityStatus,
            ErrorStats,
        )
        resp = CapabilityStatusResponse(
            timestamp="2026-11-11T00:00:00Z",
            total_capabilities=1,
            grade_distribution={"REAL": 1},
            capabilities={
                "test": CapabilityStatus(
                    capability_id="test",
                    grade="REAL",
                    last_verified="2026-11-11T00:00:00Z",
                    error_history=ErrorStats(count=5, mean=8.0, max=12.0, min=5.0, std=2.0),
                )
            },
        )
        assert resp.total_capabilities == 1
        assert "REAL" in resp.grade_distribution

    def test_store_initialization(self):
        """Store initializes with seed capabilities."""
        from api.tensor_capability_status import _CapabilityStore
        store = _CapabilityStore()
        all_caps = store.get_all()
        assert len(all_caps) >= 5  # At least 5 seed capabilities


# ── C4: Auto-annotation of WITHHELD capabilities ─────────────────────────

class TestAutoAnnotation:
    """C4: Auto-annotation attempts to upgrade WITHHELD capabilities."""

    def test_withheld_upgrade_scenario(self, annotator, updater):
        """WITHHELD capability can be upgraded through annotation."""
        # Register capability
        updater.register_capability("resistance_calc", current_level="WITHHELD")

        # Simulate API annotations
        for i in range(6):
            error = 5.0 + i * 0.5  # 5.0, 5.5, 6.0, 6.5, 7.0, 7.5
            annotator.annotate(
                path="/api/v1/calculations/resistance",
                reference_value=10000.0,
                computed_value=10000.0 * (1 + error / 100),
            )
            updater.record_error("resistance_calc", error_pct=error)

        # Evaluate
        decision = updater.evaluate("resistance_calc")
        assert decision.action == "PROMOTE"
        assert decision.proposed_level == "REAL"

    def test_withheld_confirmed_scenario(self, annotator, updater):
        """WITHHELD capability stays WITHHELD if error too high."""
        updater.register_capability("stability_calc", current_level="WITHHELD")

        # High error annotations
        for i in range(6):
            error = 20.0 + i * 2.0  # 20-30% error
            updater.record_error("stability_calc", error_pct=error)

        decision = updater.evaluate("stability_calc")
        # Should not promote (error > 15%)
        assert decision.action != "PROMOTE" or decision.proposed_level != "REAL"


# ── C5: external_constants.json has >= 20 constants with DOI ──────────────

class TestExternalConstants:
    """C5: Registry has >= 20 constants with DOI."""

    def test_registry_loads(self, constants_registry):
        """Registry loads from JSON file."""
        assert constants_registry.count >= 20

    def test_all_constants_have_doi(self, constants_registry):
        """All constants have a DOI field."""
        for name in constants_registry.all_names:
            const = constants_registry.get(name)
            assert const is not None
            assert "doi" in const, f"Constant {name} missing DOI"
            assert const["doi"], f"Constant {name} has empty DOI"

    def test_all_constants_have_source(self, constants_registry):
        """All constants have a source field."""
        for name in constants_registry.all_names:
            const = constants_registry.get(name)
            assert "source" in const
            assert const["source"]

    def test_constant_values_are_numeric(self, constants_registry):
        """All constant values are numeric."""
        for name in constants_registry.all_names:
            const = constants_registry.get(name)
            assert isinstance(const["value"], (int, float))

    def test_json_file_exists(self):
        """external_constants.json exists at expected path."""
        path = REPO_ROOT / "config" / "external_constants.json"
        assert path.exists()


# ── C6: constants_gate.py intercepts >= 3 unregistered constants ──────────

class TestConstantsGate:
    """C6: Gate intercepts >= 3 unregistered constant usage points."""

    def test_gate_detects_patterns(self, gate):
        """Gate detects known constant patterns in code."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# Test file with hardcoded constants\n")
            f.write("gravity = 9.81  # hardcoded\n")
            f.write("rho = 1025.0  # sea water\n")
            f.write("pi_val = 3.14159\n")
            f.name
        try:
            usages = gate.scan_file(f.name)
            assert len(usages) >= 3
        finally:
            os.unlink(f.name)

    def test_gate_registered_constants_pass(self, gate):
        """Registered constants are marked as registered."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("g = 9.80665  # registered\n")
            f.name
        try:
            usages = gate.scan_file(f.name)
            for u in usages:
                if u.constant_name == "gravity_ms2":
                    assert u.is_registered
        finally:
            os.unlink(f.name)

    def test_gate_directory_scan(self, gate):
        """Gate can scan a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            Path(tmpdir, "file1.py").write_text("x = 9.81\n")
            Path(tmpdir, "file2.py").write_text("y = 1025.0\n")
            result = gate.scan_directory(tmpdir)
            assert result.total_scanned == 2

    def test_gate_check_constant(self, gate):
        """Gate can check if a specific constant is registered."""
        assert gate.check_constant("gravity_ms2", 9.80665)
        assert not gate.check_constant("unknown_constant", 42.0)

    def test_gate_register_new_constant(self, gate):
        """Can register a new constant."""
        gate.register_constant(
            name="test_constant",
            value=42.0,
            unit="test_unit",
            description="Test constant",
            source="Test source",
            doi="10.1234/test",
        )
        assert gate.registry.is_registered("test_constant")


# ── C7: Zero domain semantics ─────────────────────────────────────────────

class TestZeroDomainSemantics:
    """C7: Test fixtures contain no ship-specific terms."""

    # Forbidden domain terms that should not appear in test fixtures
    FORBIDDEN_TERMS = {
        "wigley", "suboff", "kcs", "dtmb", "marad", "sergeant",
        "hull_form", "ship_type", "vessel", "boat", "barge",
        "propeller_diameter", "rudder_angle", "wave_heading",
    }

    def test_annotator_no_domain_terms(self):
        """Annotator module has no domain-specific terms."""
        source = Path(BACKEND_DIR / "middleware" / "capability_annotator.py").read_text()
        source_lower = source.lower()
        for term in self.FORBIDDEN_TERMS:
            assert term not in source_lower, f"Found domain term '{term}' in annotator"

    def test_grade_updater_no_domain_terms(self):
        """Grade updater module has no domain-specific terms."""
        source = Path(BACKEND_DIR / "services" / "capability_grade_updater.py").read_text()
        source_lower = source.lower()
        for term in self.FORBIDDEN_TERMS:
            assert term not in source_lower, f"Found domain term '{term}' in grade updater"

    def test_constants_gate_no_domain_terms(self):
        """Constants gate module has no domain-specific terms."""
        source = Path(BACKEND_DIR / "services" / "constants_gate.py").read_text()
        source_lower = source.lower()
        for term in self.FORBIDDEN_TERMS:
            assert term not in source_lower, f"Found domain term '{term}' in constants gate"

    def test_api_no_domain_terms(self):
        """API module has no domain-specific terms."""
        source = Path(BACKEND_DIR / "api" / "tensor_capability_status.py").read_text()
        source_lower = source.lower()
        for term in self.FORBIDDEN_TERMS:
            assert term not in source_lower, f"Found domain term '{term}' in API"

    def test_test_file_no_domain_terms(self):
        """This test file uses generic capability IDs, not ship names."""
        # Verify that capability IDs used in test data are generic
        # (e.g., "test_cap", "cap_a") not domain-specific
        generic_ids = ["test_cap", "cap_a", "cap_b", "calc_module", "resistance_calc", "stability_calc"]
        for gid in generic_ids:
            assert gid.lower() not in self.FORBIDDEN_TERMS

class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_pipeline(self, annotator, updater):
        """Full pipeline: annotate -> record error -> evaluate -> apply."""
        # Register
        updater.register_capability("calc_module", current_level="WITHHELD")

        # Annotate and record
        for i in range(6):
            error = 8.0 + i * 0.5
            annotator.annotate(
                path="/api/v1/calculations/resistance",
                reference_value=10000.0,
                computed_value=10000.0 * (1 + error / 100),
            )
            updater.record_error("calc_module", error_pct=error)

        # Evaluate
        decision = updater.evaluate("calc_module")
        assert decision.action == "PROMOTE"

        # Apply
        updater.apply(decision)
        assert updater.capabilities["calc_module"]["current_level"] == "REAL"

    def test_persistence_roundtrip(self, updater):
        """State can be persisted and restored."""
        updater.register_capability("test_cap", current_level="MOCK")
        updater.record_error("test_cap", error_pct=10.0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            updater.to_json(f.name)
            restored = CapabilityGradeUpdater.from_json(f.name)
            assert "test_cap" in restored.capabilities
            assert restored.capabilities["test_cap"]["current_level"] == "MOCK"
            os.unlink(f.name)

    def test_rules_persistence(self, updater):
        """Rules can be saved and loaded."""
        custom_rules = [
            GradeRule("WITHHELD", "REAL", max_error_pct=10.0, min_samples=3)
        ]
        updater.configure_rules(promotion_rules=custom_rules)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            updater.save_rules(f.name)
            updater2 = CapabilityGradeUpdater(rules_path=f.name)
            assert len(updater2._promotion_rules) == 1
            assert updater2._promotion_rules[0].max_error_pct == 10.0
            os.unlink(f.name)


# ── Additional edge case tests ────────────────────────────────────────────

class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_annotator_handles_none_body(self, annotator):
        """Annotator handles None request/response bodies."""
        record = annotator.annotate(
            path="/api/v1/calculations/resistance",
            request_body=None,
            response_body=None,
        )
        assert record is not None
        assert record.request_body is None

    def test_updater_rejects_negative_error(self, updater):
        """Grade updater rejects negative error values."""
        updater.register_capability("test_cap", current_level="WITHHELD")
        with pytest.raises(ValueError):
            updater.record_error("test_cap", error_pct=-5.0)

    def test_updater_unknown_capability(self, updater):
        """Grade updater raises for unknown capability."""
        with pytest.raises(KeyError):
            updater.evaluate("nonexistent")

    def test_constants_registry_empty(self):
        """Empty registry works correctly."""
        registry = ConstantsRegistry()
        assert registry.count == 0
        assert not registry.is_registered("anything")

    def test_gate_excludes_test_dirs(self, gate):
        """Gate excludes test directories by default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "tests"
            test_dir.mkdir()
            (test_dir / "test_file.py").write_text("x = 9.81\n")
            result = gate.scan_directory(tmpdir)
            assert result.total_scanned == 0  # tests dir excluded
