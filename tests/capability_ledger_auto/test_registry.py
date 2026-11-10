"""Tests for capability_ledger_auto registry module."""
import json
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capability_ledger_auto.registry import (
    CapabilityRegistry, CapabilityDef, EVIDENCE_TYPES, LEVELS, LEVEL_RANK
)

WORK_DIR = Path(__file__).resolve().parents[2]
REGISTRY_PATH = WORK_DIR / "config" / "capabilities.json"
SNAPSHOT_PATH = WORK_DIR / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"


class TestCapabilityDef:
    def test_valid_def(self):
        cap = CapabilityDef(
            capability_id="test.cap",
            label="Test",
            description="Test capability",
            expected_evidence_types={"code", "report"},
            code_patterns=["test"],
            test_patterns=["test_test"],
            report_patterns=["test"],
        )
        assert cap.capability_id == "test.cap"
        assert cap.expected_evidence_types == {"code", "report"}

    def test_invalid_evidence_type(self):
        with pytest.raises(ValueError, match="无效证据类型"):
            CapabilityDef(
                capability_id="test.cap",
                label="Test",
                description="Test",
                expected_evidence_types={"invalid_type"},
            )

    def test_invalid_level(self):
        with pytest.raises(ValueError, match="无效初始等级"):
            CapabilityDef(
                capability_id="test.cap",
                label="Test",
                description="Test",
                initial_level="INVALID",
            )


class TestCapabilityRegistry:
    def test_from_json(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        assert registry.count() == 21
        assert registry.schema_version == "1.0"
        assert "calc.resistance" in registry.capabilities

    def test_from_json_missing_file(self):
        with pytest.raises(FileNotFoundError):
            CapabilityRegistry.from_json("/nonexistent/path.json")

    def test_from_snapshot(self):
        registry = CapabilityRegistry.from_snapshot(SNAPSHOT_PATH)
        assert registry.count() == 21
        assert "calc.resistance" in registry.capabilities
        assert registry.capabilities["calc.resistance"].initial_level == "REAL"

    def test_from_snapshot_missing_file(self):
        with pytest.raises(FileNotFoundError):
            CapabilityRegistry.from_snapshot("/nonexistent/path.json")

    def test_get(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        cap = registry.get("calc.resistance")
        assert cap is not None
        assert cap.capability_id == "calc.resistance"
        assert cap.label == "阻力计算"

    def test_get_missing(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        assert registry.get("nonexistent") is None

    def test_list_all(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        caps = registry.list_all()
        assert len(caps) == 21

    def test_to_dict(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        d = registry.to_dict()
        assert d["schema_version"] == "1.0"
        assert d["capability_count"] == 21
        assert "calc.resistance" in d["capabilities"]

    def test_all_levels_valid(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        for cap in registry.list_all():
            assert cap.initial_level in LEVELS, f"{cap.capability_id} has invalid level {cap.initial_level}"

    def test_all_evidence_types_valid(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        for cap in registry.list_all():
            invalid = cap.expected_evidence_types - EVIDENCE_TYPES
            assert not invalid, f"{cap.capability_id} has invalid evidence types: {invalid}"

    def test_patterns_are_lists(self):
        registry = CapabilityRegistry.from_json(REGISTRY_PATH)
        for cap in registry.list_all():
            assert isinstance(cap.code_patterns, list)
            assert isinstance(cap.test_patterns, list)
            assert isinstance(cap.report_patterns, list)
